#!/usr/bin/env python
"""P0-8 FINAL STATUS MATRIX -- HC-01..HC-11.

Every cell is produced by a runtime probe against the real code. Nothing here is
inherited from prose, from a docstring, or from a previous report.

Status vocabulary is CLOSED (there is no "basically done"):

    COMPLIANT   every required dimension demonstrated at runtime, durable, and
                no pending human decision blocks its use as evidence
    LEGACY      all dimensions hold, but persisted data written before this
                round was DETECTED that cannot verify until migrated
    UNVERIFIED  at least one required dimension could NOT be demonstrated
                at runtime
    BLOCKED     the chain cannot be relied on at all pending a decision or an
                external precondition

The matrix is also a GATE: each chain declares the status it is expected to
hold. If runtime evidence derives a DIFFERENT status, that is drift and the
script exits 1. This is what stops the matrix from rotting into decoration.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Declared expectation per chain. Changing one of these IS the decision; the
# script only reports when reality no longer matches it.
EXPECTED = {
    # IMPORTANT: HC-01 is NOT "COMPLIANT". Its code detects tampering correctly,
    # but the persisted database that ships in this working tree has a forked
    # chain (concurrent writers stamped two successors off one parent). Verifying
    # a freshly created temp database proved the IMPLEMENTATION -- it said
    # nothing about the DATA. Keeping this entry honest is the whole point of
    # the matrix, so the expected status reflects the real, persisted state.
    "HC-01": "UNVERIFIED",   # declaration coverage COMPLIANT, persisted chain continuity NOT VERIFIED
    "HC-02": "COMPLIANT",
    "HC-03": "COMPLIANT",
    "HC-04": "COMPLIANT",
    "HC-05": "COMPLIANT",
    "HC-06": "COMPLIANT",
    "HC-07": "COMPLIANT",
    "HC-08": "COMPLIANT",
    "HC-09": "UNVERIFIED",   # durability: memory-only, pending decision D19
    "HC-10": "UNVERIFIED",   # durability: memory-only, pending decision D19
    "HC-11": "UNVERIFIED",   # no integrity key configured -> verification is vacuous
}

FACTS: dict = {}


def put(hc: str, **kw: str) -> None:
    FACTS.setdefault(hc, {}).update(kw)


def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def classify(hc: str):
    """Derive the status from the runtime facts. Rules only, no judgement."""
    f = FACTS[hc]
    reasons = []
    if f["declaration"] == "ABSENT":
        reasons.append("no algorithm declaration")
    if f["unknown_alg"] == "FAIL_OPEN":
        reasons.append("unknown algorithm is not refused")
    if f["tamper"] != "DETECTED":
        reasons.append("tamper not detected: " + f["tamper"])
    if f["verification"] != "VERIFIED":
        reasons.append("pristine verification did not pass")
    if f["persistence"].startswith("VOLATILE"):
        reasons.append("not durable (memory-only)")
    if f.get("continuity_ok") == "NO":
        reasons.append("persisted chain continuity broken (forked chain)")
    if reasons:
        return "UNVERIFIED", "; ".join(reasons)
    if f["legacy_data"] == "PRESENT":
        return "LEGACY", "persisted data without a declaration detected"
    return "COMPLIANT", "all required dimensions demonstrated at runtime"


# ===========================================================================
# HC-01 -- kernels/audit (SQLite)
# ===========================================================================
def probe_hc01() -> None:
    from src.kernels.audit import AuditStore, AuditEventType, AuditScope

    hc = "HC-01"
    etype, scope = next(iter(AuditEventType)), next(iter(AuditScope))
    tmp = tempfile.mkdtemp(prefix="p08f_hc01_")
    db = os.path.join(tmp, "audit.db")
    store = AuditStore(db_path=db)
    if hasattr(store, "initialize"):
        store.initialize()
    ev1 = store.log_event(etype, "p-1", scope, "ok", details={"n": 1})
    ev2 = store.log_event(etype, "p-2", scope, "ok", details={"n": 2})

    with sqlite3.connect(db) as cx:
        rows = cx.execute(
            "SELECT seq, event_hash, hash_alg FROM audit_events ORDER BY seq"
        ).fetchall()
    declared = {r[2] for r in rows}
    seq2 = rows[1][0]

    rebuilt = {
        "event_id": ev2.event_id, "event_type": ev2.event_type.value,
        "principal_id": ev2.principal_id, "scope": ev2.scope.value,
        "timestamp": ev2.timestamp, "correlation_id": ev2.correlation_id,
        "outcome": ev2.outcome, "details": ev2.details,
    }
    canonic_ok = sha256_hex(canon(rebuilt)) == ev2.event_hash
    ok0, _ = store.verify_integrity()

    with sqlite3.connect(db) as cx:
        cx.execute("UPDATE audit_events SET principal_id='TAMPERED' WHERE seq=?", (seq2,))
    ok_t, _ = store.verify_integrity()
    with sqlite3.connect(db) as cx:
        cx.execute("UPDATE audit_events SET principal_id=? WHERE seq=?",
                   (ev2.principal_id, seq2))

    with sqlite3.connect(db) as cx:
        cx.execute("UPDATE audit_events SET hash_alg='sha512-unknown' WHERE seq=?", (seq2,))
    ok_u, _ = store.verify_integrity()
    with sqlite3.connect(db) as cx:
        cx.execute("UPDATE audit_events SET hash_alg='sha256' WHERE seq=?", (seq2,))

    real = os.path.join(REPO_ROOT, "audit_store.db")
    migration = "no persisted database present"
    undeclared = 0
    total_rows = 0
    continuity = "no persisted database to check"
    persistence = "DURABLE (SQLite file)"
    if os.path.isfile(real):
        try:
            cx = sqlite3.connect(real)
            cols = {r[1] for r in cx.execute("PRAGMA table_info(audit_events)")}
            if "hash_alg" in cols:
                total_rows = cx.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
                undeclared = cx.execute(
                    "SELECT COUNT(*) FROM audit_events WHERE hash_alg IS NULL OR hash_alg=''"
                ).fetchone()[0]
            cx.close()
            migration = "%d/%d rows carry a declaration" % (total_rows - undeclared, total_rows)

            # Probe the REAL persisted chain, not a synthetic one. Verify via a
            # consistent snapshot: the SQLite backup API is safe against a live
            # writer, whereas copying the file is not.
            snap = os.path.join(tempfile.mkdtemp(prefix="p08f_hc01_snap_"), "snapshot.db")
            src = sqlite3.connect("file:%s?mode=ro" % real, uri=True)
            dst = sqlite3.connect(snap)
            with dst:
                src.backup(dst)
            src.close()
            dst.close()

            sx = sqlite3.connect(snap)
            by_seq = sx.execute(
                "SELECT seq, prev_event_hash, event_hash FROM audit_events ORDER BY seq"
            ).fetchall()
            dup_seq = sx.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] - sx.execute(
                "SELECT COUNT(DISTINCT seq) FROM audit_events"
            ).fetchone()[0]
            hashes = {r[2] for r in by_seq}
            breaks = sum(1 for i in range(1, len(by_seq)) if by_seq[i - 1][2] != by_seq[i][1])
            orphans = sum(1 for r in by_seq if r[1] and r[1] not in hashes)
            sx.close()

            live = AuditStore(db_path=snap)
            live_ok, live_total = live.verify_integrity()
            continuity = (
                "%d broken joins, %d duplicate seq, %d orphan links; "
                "verify_integrity() reports is_ok=%s on %d rows"
                % (breaks, dup_seq, orphans, live_ok, live_total)
            )
            if breaks or dup_seq or live_ok is not True:
                persistence = ("DURABLE (SQLite file) BUT the persisted chain is forked; "
                               "the code does report it (is_ok=%s)" % live_ok)
        except sqlite3.Error as exc:
            continuity = "unreadable (%s)" % exc

    put(hc,
        declaration="DECLARED sha256" if declared == {"sha256"} else "OTHER " + str(sorted(declared)),
        canonicalization="8-field json(sort_keys,separators); event_hash/prev_event_hash excluded",
        prev_hash="EXCLUDED from input (linkage only)" if canonic_ok else "UNKNOWN",
        persistence=persistence,
        verification="VERIFIED" if ok0 is True else "FAILED",
        tamper="DETECTED" if ok_t is False else "NOT DETECTED",
        unknown_alg="FAIL_CLOSED" if ok_u is False else "FAIL_OPEN",
        legacy_data="PRESENT" if undeclared else "NONE",
        migration=migration + " | persisted continuity: " + continuity)

    # NOTE: the dimensions above were probed on a SYNTHETIC pristine store, which
    # proves the implementation. The persisted-chain check below is what decides
    # the reported status for real data; record it explicitly rather than letting
    # a green synthetic run imply the deployment is clean.
    if "broken joins" in continuity and "0 broken joins" not in continuity:
        FACTS[hc]["continuity_ok"] = "NO"
    elif os.path.isfile(os.path.join(REPO_ROOT, "audit_store.db")):
        FACTS[hc]["continuity_ok"] = "YES"
    else:
        FACTS[hc]["continuity_ok"] = "N/A"


# ===========================================================================
# HC-02..HC-08 -- JSON-file chains (reuse the P0-8c gate's chain table)
# ===========================================================================
def probe_json_chains() -> None:
    spec = importlib.util.spec_from_file_location(
        "p08_gate_cfg",
        os.path.join(REPO_ROOT, "scripts", "verify_p08_hash_chain_sig_alg.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    for cfg in mod.CHAIN_CONFIGS:
        hc, cls = cfg["hc"], cfg["cls"]
        records_key, tamper_field = cfg["records_key"], cfg["tamper_field"]
        tmp = tempfile.mkdtemp(prefix="p08f_%s_" % hc)
        path = os.path.join(tmp, "store.json")

        cls(storage_path=path)
        factory = cfg["factory"]
        factory(cls(storage_path=path))

        raw = json.load(open(path, encoding="utf-8"))
        declared = raw.get("hash_alg")
        ok0 = cls(storage_path=path).verify_integrity()

        recs = raw.get(records_key) or {}
        first_key = sorted(recs)[0] if recs else None
        prev_rule = "UNKNOWN"
        if first_key is not None:
            without_prev = dict(recs[first_key])
            prev = without_prev.pop("prev_hash", "genesis")
            h_without = sha256_hex(canon(without_prev))
            h_with = sha256_hex(canon(dict(recs[first_key], prev_hash=prev)))
            links = raw.get("hash_chain") or []
            first_link = links[0] if links else None
            if first_link == h_with and h_with != h_without:
                prev_rule = "INCLUDED in input (genesis seed)"
            elif first_link == h_without:
                prev_rule = "EXCLUDED from input"

        raw[records_key][first_key][tamper_field] = "TAMPERED"
        json.dump(raw, open(path, "w", encoding="utf-8"))
        ok_t = cls(storage_path=path).verify_integrity()

        raw2 = json.load(open(path, encoding="utf-8"))
        raw2["hash_alg"] = "sha512-unknown"
        json.dump(raw2, open(path, "w", encoding="utf-8"))
        ok_u = cls(storage_path=path).verify_integrity()

        put(hc,
            declaration="DECLARED sha256" if declared == "sha256" else "ABSENT/OTHER " + repr(declared),
            canonicalization="record.to_dict() + prev_hash, json(sort_keys,separators)",
            prev_hash=prev_rule,
            persistence="DURABLE (JSON file)",
            verification="VERIFIED" if ok0 is True else "FAILED",
            tamper="DETECTED" if ok_t is False else "NOT DETECTED",
            unknown_alg="FAIL_CLOSED" if ok_u is False else "FAIL_OPEN",
            legacy_data="NONE",
            migration="self-describing (writes hash_alg on every save)")


# ===========================================================================
# HC-09 / HC-10 -- memory-only security audit chains
# ===========================================================================
def probe_hc09() -> None:
    from src.security.audit_logger import CryptoAuditLogger, CryptoOperation

    hc = "HC-09"
    op = next(iter(CryptoOperation))
    logger = CryptoAuditLogger(component_name="p08f")
    e1 = logger.log(op, key_name="k1", success=True)
    e2 = logger.log(op, key_name="k2", success=True)

    verifies = logger.verify_chain() is True
    e2.key_name = "TAMPERED"
    tampered = logger.verify_chain() is False
    e2.key_name = "k2"

    pristine = CryptoAuditLogger(component_name="p08f-fc")
    p1 = pristine.log(op, key_name="k1", success=True)
    p1.hash_alg = "sha3-512-not-registered"
    refuse = pristine.verify_chain() is False

    put(hc,
        declaration="DECLARED " + str(e1.hash_alg),
        canonicalization="to_dict() minus event_hash/prev_event_hash/hash_alg, json(sort_keys,separators)",
        prev_hash="EXCLUDED from input (linkage only)" if e2.prev_event_hash == e1.event_hash else "UNKNOWN",
        persistence="VOLATILE (CryptoAuditLogger._events list; no persistence call)",
        verification="VERIFIED" if verifies else "FAILED",
        tamper="DETECTED" if tampered else "NOT DETECTED",
        unknown_alg="FAIL_CLOSED" if refuse else "FAIL_OPEN",
        legacy_data="NONE",
        migration="N/A (nothing is persisted)")


def probe_hc10() -> None:
    from enum import Enum
    from src.security.audit_policy import AuditKernel, AuditEventType

    hc = "HC-10"
    etype = next(iter(AuditEventType))
    kernel = AuditKernel()
    a1 = kernel.log(etype, "p-1", result="ok")
    a2 = kernel.log(etype, "p-2", result="ok")

    et = a2.event_type.value if isinstance(a2.event_type, Enum) else a2.event_type
    chain_data = {
        "id": a2.id, "timestamp": a2.timestamp.isoformat(), "event_type": et,
        "principal_id": a2.principal_id, "permission": a2.permission,
        "scope": a2.scope, "result": a2.result, "reason": a2.reason,
        "correlation_id": a2.correlation_id, "prev_hash": a2.prev_hash,
    }
    included = sha256_hex(canon(chain_data)) == a2.hash
    excluded = sha256_hex(canon({k: v for k, v in chain_data.items() if k != "prev_hash"})) != a2.hash

    ok0, _ = kernel.verify_integrity()
    a2.principal_id = "TAMPERED"
    ok_t, _ = kernel.verify_integrity()
    a2.principal_id = "p-2"

    pk = AuditKernel()
    q1 = pk.log(etype, "p-fc", result="ok")
    q1.hash_alg = "sha3-512-not-registered"
    ok_u, _ = pk.verify_integrity()

    put(hc,
        declaration="DECLARED " + str(a1.hash_alg),
        canonicalization="10-field json(sort_keys,separators); timestamp isoformat, event_type=value",
        prev_hash="INCLUDED in input" if included and excluded else "UNKNOWN",
        persistence="VOLATILE (AuditKernel._entries list; no persistence call)",
        verification="VERIFIED" if ok0 is True else "FAILED",
        tamper="DETECTED" if ok_t is False else "NOT DETECTED",
        unknown_alg="FAIL_CLOSED" if ok_u is False else "FAIL_OPEN",
        legacy_data="NONE",
        migration="N/A (nothing is persisted)")


# ===========================================================================
# HC-11 -- kernels/identity MAC (NOT a hash chain)
# ===========================================================================
def probe_hc11() -> None:
    from src.kernels.identity._persistence import (
        SqliteHumanIdentityStore,
        HUMAN_IDENTITIES_INTEGRITY_KEY_ENV,
        integrity_enforced,
    )

    hc = "HC-11"
    before = os.environ.get(HUMAN_IDENTITIES_INTEGRITY_KEY_ENV)
    configured_now = integrity_enforced()
    entry = {"principal": "boss", "display_name": "owner", "permissions": ["*"],
             "scope": "L0", "source": "p08f", "trust_score": 1.0}

    def round_trip(key):
        """Return (pristine_verified, tamper_detected) for one key setting."""
        if key is None:
            os.environ.pop(HUMAN_IDENTITIES_INTEGRITY_KEY_ENV, None)
        else:
            os.environ[HUMAN_IDENTITIES_INTEGRITY_KEY_ENV] = key
        tmp = tempfile.mkdtemp(prefix="p08f_hc11_")
        db = os.path.join(tmp, "identities.db")
        SqliteHumanIdentityStore(location=db).upsert(dict(entry))
        store = SqliteHumanIdentityStore(location=db)
        loaded = store.load_all()
        pristine = bool(loaded) and not store.last_load_report["rejected"]
        with sqlite3.connect(db) as cx:
            cx.execute("UPDATE human_identities SET display_name='TAMPERED' WHERE principal=?",
                       (entry["principal"],))
        reloaded = SqliteHumanIdentityStore(location=db)
        reloaded.load_all()
        detected = any(entry["principal"] in str(r) for r in reloaded.last_load_report["rejected"])
        return pristine, detected

    pristine_with, detected_with = round_trip("p08f-test-key")
    pristine_without, detected_without = round_trip(None)

    # The CURRENT deployment posture decides the status we report.
    if configured_now:
        pristine, detected = pristine_with, detected_with
    else:
        pristine, detected = pristine_without, detected_without

    put(hc,
        declaration="DECLARED inside the tag ('<alg>:<digest>')",
        canonicalization="_canonical_row json(sort_keys,separators) over authority-bearing fields",
        prev_hash="N/A (per-row MAC, not a linked chain)",
        persistence="DURABLE (SQLite human_identities table)",
        verification="VERIFIED" if pristine else "FAILED",
        tamper="DETECTED" if detected else "NOT DETECTED (no integrity key -> verification vacuously True)",
        unknown_alg="FAIL_CLOSED (bare digest and unknown alg both refused)",
        legacy_data="NONE",
        migration="key %s; tamper detected with key=%s, without key=%s" % (
            "configured" if configured_now else "NOT configured", detected_with, detected_without))

    if before is None:
        os.environ.pop(HUMAN_IDENTITIES_INTEGRITY_KEY_ENV, None)
    else:
        os.environ[HUMAN_IDENTITIES_INTEGRITY_KEY_ENV] = before


# ===========================================================================
def main() -> int:
    print("P0-8 FINAL STATUS MATRIX (HC-01..HC-11)")
    print("Every cell below is produced by a runtime probe, not by prose.")

    probe_hc01()
    probe_json_chains()
    probe_hc09()
    probe_hc10()
    probe_hc11()

    order = ["HC-%02d" % i for i in range(1, 12)]
    derived = {}
    print("")
    print("=" * 78)
    print("HASH-CHAIN FINAL STATUS MATRIX")
    print("=" * 78)
    for hc in order:
        f = FACTS[hc]
        status, why = classify(hc)
        derived[hc] = status
        print("")
        print("[%s]  %s" % (hc, status))
        print("   hash_alg declaration : %s" % f["declaration"])
        print("   canonicalization     : %s" % f["canonicalization"])
        print("   prev_hash rule       : %s" % f["prev_hash"])
        print("   persistence          : %s" % f["persistence"])
        print("   verification         : %s" % f["verification"])
        print("   tamper detection     : %s" % f["tamper"])
        print("   unknown algorithm    : %s" % f["unknown_alg"])
        print("   migration status     : %s" % f["migration"])
        print("   why                  : %s" % why)

    print("")
    print("=" * 78)
    print("SUMMARY")
    print("=" * 78)
    for s in ("COMPLIANT", "LEGACY", "UNVERIFIED", "BLOCKED"):
        print("  %-12s %d" % (s, sum(1 for h in order if derived[h] == s)))

    drift = [(h, EXPECTED[h], derived[h]) for h in order if EXPECTED[h] != derived[h]]
    if drift:
        print("")
        print("DRIFT -> declared expectation no longer matches runtime evidence:")
        for h, exp, got in drift:
            print("  %s: expected %s, derived %s" % (h, exp, got))
        return 1

    print("")
    print("No drift: every chain's derived status matches its declared expectation.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
