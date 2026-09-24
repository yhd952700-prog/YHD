#!/usr/bin/env python
"""P0-8b — HASH-CHAIN VERIFICATION MATRIX (HC-01..HC-11) + per-chain CI assertions.

What this guard does
--------------------
For EVERY chain in the P0-8 matrix it (a) **declares** the five attributes the
containment requires --

    hash_alg declaration / canonicalization / prev_hash participation /
    verify path / fail-closed behaviour

-- and (b) **proves** each one at **runtime against the real code**, not by
prose and not by reading docstrings.

Discipline (PHASE 3.6)
----------------------
* A fact is only recorded as confirmed when a runtime probe produced it.
* Anything that cannot be confirmed is reported as **UNVERIFIED** and is
  NEVER filled in by assumption.
* Known defects are reported in an explicit ``GAPS`` section (they are the
  P0-8c work items); they are not hidden behind a green exit code, and they
  are not asserted as correct behaviour either.

Exit codes: 0 = every *confirmed* assertion held; 1 = a declared fact failed.
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

RESULTS = []          # (ok, name, detail)
UNVERIFIED = []       # (hc, what, why)      -- cannot be confirmed; never assumed
GAPS = []             # (hc, gap, why)       -- P0-8c work items
FINDINGS = []         # (hc, finding, why)   -- CONFIRMED facts that imply a decision
                      #                         (fixing them changes historical
                      #                         semantics -> human decision, not ours)


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((bool(ok), name, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  -- {detail}" if detail else ""))
    return bool(ok)


def mark_unverified(hc: str, what: str, why: str) -> None:
    UNVERIFIED.append((hc, what, why))
    print(f"  [UNVERIFIED] {hc}: {what}  -- {why}")


def mark_gap(hc: str, gap: str, why: str) -> None:
    GAPS.append((hc, gap, why))
    print(f"  [GAP->P0-8c] {hc}: {gap}  -- {why}")


def mark_finding(hc: str, finding: str, why: str) -> None:
    FINDINGS.append((hc, finding, why))
    print(f"  [FINDING->DECISION] {hc}: {finding}  -- {why}")


def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


# ===========================================================================
# HC-01 — kernels/audit (SQLite, A5 main chain)
# ===========================================================================
def probe_hc01() -> None:
    from src.kernels.audit import (
        AuditStore, AuditEventType, AuditScope, HASH_ALGORITHMS, DEFAULT_HASH_ALG,
    )

    hc = "HC-01"
    print(f"\n== {hc} — src.kernels.audit.AuditStore (SQLite) ==")

    etype = next(iter(AuditEventType))
    scope = next(iter(AuditScope))

    tmp = tempfile.mkdtemp(prefix="p08b_hc01_")
    db = os.path.join(tmp, "audit.db")
    store = AuditStore(db_path=db)
    if hasattr(store, "initialize"):
        store.initialize()

    ev1 = store.log_event(etype, "principal-1", scope, "ok", details={"n": 1})
    ev2 = store.log_event(etype, "principal-2", scope, "ok", details={"n": 2})

    # --- 1) hash_alg declaration -------------------------------------------
    with sqlite3.connect(db) as cx:
        rows = cx.execute(
            "SELECT seq, event_hash, prev_event_hash, hash_alg FROM audit_events ORDER BY seq"
        ).fetchall()
    declared = {r[3] for r in rows}
    check(f"{hc}: hash_alg declared on every stored row", declared == {"sha256"},
          f"declared={sorted(declared)}")
    # `seq` is assigned by the write path and is not an attribute of AuditEvent.
    seq2 = rows[1][0]

    # --- 2) canonicalization: excludes event_hash + prev_event_hash ---------
    # Independently recompute the canonical 7-field form from the live object
    # and compare against the stored hash. Matching proves prev_event_hash is
    # NOT part of the hash input (it is absent from the canonical dict).
    rebuilt = {
        "event_id": ev2.event_id,
        "event_type": ev2.event_type.value,
        "principal_id": ev2.principal_id,
        "scope": ev2.scope.value,
        "timestamp": ev2.timestamp,
        "correlation_id": ev2.correlation_id,
        "outcome": ev2.outcome,
        "details": ev2.details,
    }
    check(f"{hc}: canonicalization = 8-field json(sort_keys, separators), "
          "event_hash/prev_event_hash EXCLUDED",
          sha256_hex(canon(rebuilt)) == ev2.event_hash,
          "independent recompute matches stored event_hash")

    # --- 3) prev_hash participation: linkage only, never in the hash input --
    check(f"{hc}: prev_event_hash chains to the previous event_hash",
          ev2.prev_event_hash == ev1.event_hash,
          "linkage by value")

    with sqlite3.connect(db) as cx:
        cx.execute("UPDATE audit_events SET prev_event_hash='CORRUPT' WHERE seq=?", (seq2,))
    # NOTE: AuditStore.verify_integrity() returns (is_ok, TOTAL_EVENTS) -- the
    # second element is a count of events, NOT a count of broken links. Assert
    # on `is_ok` only; treating the second element as "broken" is wrong.
    ok_after, total_after = store.verify_integrity()
    check(f"{hc}: corrupting prev_event_hash is DETECTED (linkage check)",
          ok_after is False,
          f"is_ok={ok_after} (total={total_after})")
    # ... yet the content hash is unaffected, proving prev was never hashed in.
    check(f"{hc}: prev_event_hash NOT part of the hash input "
          "(content hash unchanged by the corruption)",
          sha256_hex(canon(rebuilt)) == ev2.event_hash,
          "canonical recompute still matches")
    with sqlite3.connect(db) as cx:
        cx.execute("UPDATE audit_events SET prev_event_hash=? WHERE seq=?",
                   (ev1.event_hash, seq2))

    # --- 4) fail-closed on an UNKNOWN declared algorithm --------------------
    with sqlite3.connect(db) as cx:
        cx.execute("UPDATE audit_events SET hash_alg='sha512-unknown' WHERE seq=?", (seq2,))
    ok_u, total_u = store.verify_integrity()
    check(f"{hc}: unknown hash_alg -> fail-closed (counted broken, no default)",
          ok_u is False,
          f"is_ok={ok_u} (total={total_u})")
    with sqlite3.connect(db) as cx:
        cx.execute("UPDATE audit_events SET hash_alg='sha256' WHERE seq=?", (seq2,))

    # --- 5) GAP: a MISSING declaration silently falls back to default -------
    ok0, total0 = store.verify_integrity()
    check(f"{hc}: pristine chain verifies", ok0 is True,
          f"is_ok={ok0} (total={total0})")
    with sqlite3.connect(db) as cx:
        cx.execute("UPDATE audit_events SET hash_alg='' WHERE seq=?", (seq2,))
    ok_f, total_f = store.verify_integrity()
    if ok_f is True:
        mark_gap(hc, "empty/NULL hash_alg silently falls back to "
                     f"{DEFAULT_HASH_ALG!r} (verify_integrity still green)",
                 "violates P0-8c 'no default sha256 fallback'; an undeclared "
                 "row must be UNVERIFIED, not silently verified")
    else:
        check(f"{hc}: missing hash_alg -> fail-closed", True, "already fail-closed")


# ===========================================================================
# HC-02..HC-08 — JSON-file chains (reuse the P0-8c gate's chain table)
# ===========================================================================
def _load_gate_configs():
    path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "verify_p08_hash_chain_sig_alg.py")
    spec = importlib.util.spec_from_file_location("p08_gate_cfg", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.CHAIN_CONFIGS


def probe_json_chain(cfg: dict) -> None:
    hc = cfg["hc"]
    cls = cfg["cls"]
    records_key = cfg["records_key"]
    tamper_field = cfg["tamper_field"]
    print(f"\n== {hc} — {cls.__module__}.{cls.__name__} (JSON file) ==")

    tmp = tempfile.mkdtemp(prefix=f"p08b_{hc}_")
    path = os.path.join(tmp, "store.json")

    store = cls(storage_path=path)
    cfg["factory"](store)

    raw = json.load(open(path, encoding="utf-8"))
    chain = raw.get("hash_chain")

    # --- 1) hash_alg declaration -------------------------------------------
    check(f"{hc}: hash_alg declared in the persisted envelope",
          raw.get("hash_alg") == "sha256", f"got {raw.get('hash_alg')!r}")

    recs = raw.get(records_key) or {}
    if not chain or not recs:
        mark_unverified(hc, "chain/record shape", "no persisted chain or records to probe")
        return
    first_key = sorted(recs)[0]
    rec = recs[first_key]

    # --- 2) canonicalization + prev_hash participation ---------------------
    # The chain value must equal H(record_dict + prev_hash="genesis"), and must
    # NOT equal H(record_dict) alone -- that pair proves prev_hash is folded
    # into the hash input (the opposite of HC-01's rule).
    with_prev = sha256_hex(canon({**rec, "prev_hash": "genesis"}))
    without_prev = sha256_hex(canon(rec))
    check(f"{hc}: canonicalization = record.to_dict() + prev_hash, "
          "json(sort_keys, separators)",
          with_prev == chain[0], "H(rec+prev=genesis) == chain[0]")
    check(f"{hc}: prev_hash IS part of the hash input (genesis seed)",
          without_prev != chain[0], "H(rec) alone != chain[0]")

    # --- 3) verify path ----------------------------------------------------
    reloaded = cls(storage_path=path)
    check(f"{hc}: verify path -> verify_integrity() True on independent reload",
          reloaded.verify_integrity() is True)

    recs2 = json.load(open(path, encoding="utf-8"))
    recs2[records_key][first_key][tamper_field] = "TAMPERED"
    json.dump(recs2, open(path, "w", encoding="utf-8"), indent=2)
    check(f"{hc}: verify path -> verify_integrity() False after tamper",
          cls(storage_path=path).verify_integrity() is False)

    # --- 4) fail-closed on an UNKNOWN declared algorithm --------------------
    raw3 = json.load(open(path, encoding="utf-8"))
    raw3["hash_alg"] = "sha512-unknown"
    json.dump(raw3, open(path, "w", encoding="utf-8"), indent=2)
    check(f"{hc}: unknown hash_alg -> fail-closed (no default fallback)",
          cls(storage_path=path).verify_integrity() is False)

    # --- 5) GAP: legacy file with NO declaration ---------------------------
    # IMPORTANT: this must run against PRISTINE data. Reusing the file above
    # would test already-tampered records and report a false "fail-closed",
    # which would hide the very fallback this probe exists to expose.
    tmp2 = tempfile.mkdtemp(prefix=f"p08b_{hc}_legacy_")
    path2 = os.path.join(tmp2, "store.json")
    store2 = cls(storage_path=path2)
    cfg["factory"](store2)
    legacy = json.load(open(path2, encoding="utf-8"))
    legacy.pop("hash_alg", None)           # simulate pre-P0-8 on-disk data
    json.dump(legacy, open(path2, "w", encoding="utf-8"), indent=2)
    if cls(storage_path=path2).verify_integrity() is True:
        mark_gap(hc, "envelope without hash_alg silently defaults to 'sha256'",
                 "violates P0-8c 'no default sha256 fallback'; legacy data must "
                 "be explicitly migrated, not silently assumed")
    else:
        check(f"{hc}: missing hash_alg -> fail-closed", True, "already fail-closed")


# ===========================================================================
# HC-09 — security/audit_logger (memory-only)
# ===========================================================================
def probe_hc09() -> None:
    from src.security.audit_logger import CryptoAuditLogger, CryptoOperation

    hc = "HC-09"
    print(f"\n== {hc} — src.security.audit_logger.CryptoAuditLogger (memory) ==")
    op = next(iter(CryptoOperation))

    logger = CryptoAuditLogger(component_name="p08b")
    e1 = logger.log(op, key_name="k1", success=True)
    e2 = logger.log(op, key_name="k2", success=True)

    # --- algorithm actually used ------------------------------------------
    # P0-8c: the declared algorithm is EXCLUDED from the canonical form (it is
    # metadata ABOUT the hash, not part of its input), so it is popped before
    # recomputing. Popping it is also why adding the field changed no hash.
    d = e2.to_dict()
    d.pop("event_hash", None)
    d.pop("prev_event_hash", None)
    d.pop("hash_alg", None)
    check(f"{hc}: algorithm in use is sha256 (proved by recompute)",
          sha256_hex(canon(d)) == e2.event_hash, "recompute == event_hash")

    # --- declaration -------------------------------------------------------
    check(f"{hc}: hash_alg declared on the event (P0-8c)",
          getattr(e2, "hash_alg", None) == "sha256",
          f"hash_alg={getattr(e2, 'hash_alg', None)!r}")

    # --- declaration is metadata: it does not enter the hash input ---------
    with_alg = e2.to_dict()
    with_alg.pop("event_hash", None)
    with_alg.pop("prev_event_hash", None)
    check(f"{hc}: hash_alg is EXCLUDED from the canonical input (no silent rehash)",
          sha256_hex(canon(with_alg)) != e2.event_hash,
          "canonical WITH hash_alg hashes differently, proving it is popped")

    # --- prev_hash participation -------------------------------------------
    check(f"{hc}: prev_event_hash EXCLUDED from the hash input",
          e2.prev_event_hash == e1.event_hash and
          sha256_hex(canon(d)) == e2.event_hash,
          "linkage by value; canonical pops prev_event_hash")

    # --- verify path -------------------------------------------------------
    check(f"{hc}: verify path -> verify_chain() True", logger.verify_chain() is True)
    e2.key_name = "TAMPERED"
    check(f"{hc}: verify path -> verify_chain() False after tamper",
          logger.verify_chain() is False)

    # --- fail-closed (P0-8c) -----------------------------------------------
    # IMPORTANT: this must run on PRISTINE data. Reusing the tampered logger
    # above would return False for the wrong reason and fake a pass -- the very
    # methodological trap this probe already hit once for HC-04..HC-08.
    pristine = CryptoAuditLogger(component_name="p08b-failclosed")
    p1 = pristine.log(op, key_name="k1", success=True)
    check(f"{hc}: pristine chain verifies before the fail-closed probe",
          pristine.verify_chain() is True)
    # The data is intact under sha256, so a silent sha256 fallback WOULD verify.
    pd = p1.to_dict()
    pd.pop("event_hash", None)
    pd.pop("prev_event_hash", None)
    pd.pop("hash_alg", None)
    check(f"{hc}: pre-condition -- data is intact under sha256",
          sha256_hex(canon(pd)) == p1.event_hash,
          "sha256 recompute matches, so a fallback would have returned True")
    # Now declare an algorithm this build cannot perform.
    p1.hash_alg = "sha3-512-not-registered"
    check(f"{hc}: unknown declared algorithm -> verify_chain() False (fail-closed)",
          pristine.verify_chain() is False,
          "unknown alg refused; NOT silently recomputed with sha256")

    # --- evidence grade ----------------------------------------------------
    mark_unverified(hc, "durability / evidence grade",
                    "events live only in CryptoAuditLogger._events (a list); no "
                    "persistence call exists despite the docstring claiming "
                    "'Uses the existing AuditStore for persistence'")


# ===========================================================================
# HC-10 — security/audit_policy (memory-only)
# ===========================================================================
def probe_hc10() -> None:
    from enum import Enum
    from src.security.audit_policy import AuditKernel, AuditEventType

    hc = "HC-10"
    print(f"\n== {hc} — src.security.audit_policy.AuditKernel (memory) ==")
    etype = next(iter(AuditEventType))

    kernel = AuditKernel()
    a1 = kernel.log(etype, "principal-1", result="ok")
    a2 = kernel.log(etype, "principal-2", result="ok")

    # --- canonicalization (exactly the 10 fields the code hashes) ----------
    et = a2.event_type.value if isinstance(a2.event_type, Enum) else a2.event_type
    chain_data = {
        "id": a2.id,
        "timestamp": a2.timestamp.isoformat(),
        "event_type": et,
        "principal_id": a2.principal_id,
        "permission": a2.permission,
        "scope": a2.scope,
        "result": a2.result,
        "reason": a2.reason,
        "correlation_id": a2.correlation_id,
        "prev_hash": a2.prev_hash,
    }
    included = sha256_hex(canon(chain_data))
    excluded = sha256_hex(canon({k: v for k, v in chain_data.items() if k != "prev_hash"}))

    check(f"{hc}: canonicalization = 10-field json(sort_keys, separators) "
          "(timestamp isoformat, event_type=value)",
          included == a2.hash, "independent recompute == entry.hash")
    check(f"{hc}: prev_hash IS part of the hash input (genesis='genesis_hash_0')",
          excluded != a2.hash and a1.prev_hash == "genesis_hash_0",
          "H(without prev_hash) != entry.hash")

    # --- metadata is NOT covered by the hash -------------------------------
    a2.metadata["injected"] = "TAMPERED"
    ok_m, _ = kernel.verify_integrity()
    if ok_m:
        mark_finding(hc, "entry.metadata is NOT covered by the entry hash, so "
                         "tampering metadata is undetectable",
                     "canonicalization truth; extending coverage would change "
                     "every historical hash -> human decision, not assumed here")

    # --- declaration -------------------------------------------------------
    check(f"{hc}: hash_alg declared on the entry (P0-8c)",
          getattr(a2, "hash_alg", None) == "sha256",
          f"hash_alg={getattr(a2, 'hash_alg', None)!r}")
    # The declaration is NOT part of ``chain_data`` (the 10 hashed fields), so
    # adding it changed no existing hash -- same discipline as HC-09.
    check(f"{hc}: hash_alg is EXCLUDED from the 10 hashed fields",
          "hash_alg" not in chain_data and
          sha256_hex(canon(chain_data)) == a2.hash,
          "recompute of the 10-field form still matches")

    # --- verify path -------------------------------------------------------
    ok, broken = kernel.verify_integrity()
    check(f"{hc}: verify path -> verify_integrity() True", ok is True, f"broken={broken}")
    a2.principal_id = "TAMPERED"
    ok2, _ = kernel.verify_integrity()
    check(f"{hc}: verify path -> verify_integrity() False after tamper", ok2 is False)

    # --- fail-closed (P0-8c) -----------------------------------------------
    # PRISTINE data again: the kernel above was already tampered, and verifying
    # it would return False for the wrong reason (a false pass).
    pk = AuditKernel()
    q1 = pk.log(etype, "principal-fc", result="ok")
    ok_p, _ = pk.verify_integrity()
    check(f"{hc}: pristine chain verifies before the fail-closed probe", ok_p is True)
    # Pre-condition: the entry is intact under sha256, so a fallback would pass.
    qt = q1.event_type.value if isinstance(q1.event_type, Enum) else q1.event_type
    qd = {
        "id": q1.id, "timestamp": q1.timestamp.isoformat(), "event_type": qt,
        "principal_id": q1.principal_id, "permission": q1.permission,
        "scope": q1.scope, "result": q1.result, "reason": q1.reason,
        "correlation_id": q1.correlation_id, "prev_hash": q1.prev_hash,
    }
    check(f"{hc}: pre-condition -- entry is intact under sha256",
          sha256_hex(canon(qd)) == q1.hash,
          "sha256 recompute matches, so a fallback would have returned True")
    q1.hash_alg = "sha3-512-not-registered"
    ok_u, broken_u = pk.verify_integrity()
    check(f"{hc}: unknown declared algorithm -> verify_integrity() False (fail-closed)",
          ok_u is False, f"broken={broken_u}; NOT recomputed with sha256")

    mark_unverified(hc, "durability / evidence grade",
                    "entries live only in AuditKernel._entries (a list); no persistence")


# ===========================================================================
# HC-11 — kernels/identity MAC (A3 template -- NOT a hash chain)
# ===========================================================================
def probe_hc11() -> None:
    from src.kernels.identity._persistence import (
        compute_row_tag, tag_matches, DEFAULT_MAC_ALG,
    )

    hc = "HC-11"
    print(f"\n== {hc} — src.kernels.identity._persistence MAC (A3, not a hash chain) ==")

    entry = {"principal": "ceo", "kind": "human", "v": 1}
    key = "p08b-integrity-key"

    tag = compute_row_tag(entry, key)
    check(f"{hc}: algorithm name travels INSIDE the tag ('<alg>:<digest>')",
          isinstance(tag, str) and tag.startswith(DEFAULT_MAC_ALG + ":"),
          f"tag={tag[:24]}...")

    check(f"{hc}: correct tag verifies", tag_matches(entry, key, tag) is True)

    check(f"{hc}: unknown algorithm -> fail-closed (rejected, no fallback)",
          tag_matches(entry, key, f"sha512-unknown:{tag.split(':')[-1]}") is False)

    check(f"{hc}: bare digest with no algorithm id -> fail-closed (rejected)",
          tag_matches(entry, key, tag.split(":")[-1]) is False)

    tampered = dict(entry, principal="attacker")
    check(f"{hc}: tampered row rejected", tag_matches(tampered, key, tag) is False)

    # Documented degraded posture (P0-3, id 9a7yq2): no key -> nothing to verify.
    degraded = tag_matches(entry, "", tag)
    print(f"  [NOTE] {hc}: no integrity key configured -> tag_matches returns "
          f"{degraded} (DEGRADED/UNVERIFIED posture, human decision id 9a7yq2, "
          "must stay visible)")


def main() -> int:
    print("P0-8b — HASH-CHAIN VERIFICATION MATRIX (HC-01..HC-11)")
    print("Each chain declares: hash_alg / canonicalization / prev_hash rule / "
          "verify path / fail-closed. Each declaration is proved at runtime.")

    probe_hc01()
    for cfg in _load_gate_configs():
        probe_json_chain(cfg)
    probe_hc09()
    probe_hc10()
    probe_hc11()

    total = len(RESULTS)
    passed = sum(1 for ok, _, _ in RESULTS if ok)
    failed = total - passed

    print("\n" + "=" * 70)
    print(f"P0-8b SUMMARY: {passed}/{total} confirmed assertions passed, {failed} failed")
    print("=" * 70)

    if UNVERIFIED:
        print("\nUNVERIFIED (recorded, NOT assumed — these need a human decision):")
        for hc, what, why in UNVERIFIED:
            print(f"  - {hc}: {what}\n      {why}")
    if GAPS:
        print("\nGAPS -> to be closed by P0-8c:")
        for hc, gap, why in GAPS:
            print(f"  - {hc}: {gap}\n      {why}")
    if FINDINGS:
        print("\nFINDINGS -> confirmed, but fixing them changes historical "
              "semantics: HUMAN DECISION REQUIRED (not decided here):")
        for hc, finding, why in FINDINGS:
            print(f"  - {hc}: {finding}\n      {why}")

    # Two explicit returns, not "return 0 if failed == 0 else 1": the guardrail
    # test that every gate must be able to fail decides that by AST, and it only
    # recognises a Return holding a literal non-zero int.
    if failed:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
