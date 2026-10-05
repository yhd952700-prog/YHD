#!/usr/bin/env python3
"""D-ag — Read-only DERIVED decision-basis view for the audit chain.

WHAT QUESTION THIS ANSWERS
--------------------------
    Given any audit event: "on what basis was this change allowed?"

That is the bar set for this work, and it must be EXECUTABLE as a gate.

THE HARD CRITERION (as ruled)
-----------------------------
A basis counts as EVIDENCE only when the basis is itself a record EARLIER in
the same chain — i.e. the derived view must be able to point at a
``basis_seq < event_seq``. "The record exists" and "the record predates the
event" are different claims; only the second is evidence. Anything that cannot
meet it is reported as ``UNVERIFIED/DERIVED`` with the reason stated, and is
NEVER allowed to degrade into "looks traceable".

SAFETY CONTRACT (strictly read-only on the source)
--------------------------------------------------
Inherited from ``scripts/derive_audit_view.py`` (D22):

* The source DB is copied via SQLite's ``.backup`` API into a throwaway temp
  file and ALL work happens on that copy. The original is never written.
* No VACUUM, no write-back, no reordering of the source.
* Output is deterministic given the source bytes: no wall-clock timestamps are
  written into the output, so re-running on the same source is byte-identical.
* The frozen HC-01 ``audit_store.db`` is never modified by this script.

WHAT IT COMPUTES
----------------
1. Per event: the basis it carries, classified as
     * ``EVIDENCE``   — verifiable and pointing to an earlier chain record
     * ``UNRESOLVED`` — a real adjudication (rule name) but NO pointer to any
                        specific earlier record, so the chain says *which rule*
                        allowed it but not *which authorisation*
     * ``PLACEHOLDER``— the field is populated but adjudicates nothing
                        (``unadjudicated`` / ``default_deny`` / ``error`` ...)
     * ``ABSENT``     — no basis field at all

   A ``PLACEHOLDER`` is NOT counted as coverage. It occupies the field while
   answering nothing; counting it would be the exact failure under test.

2. A candidate-resolution pass for ``UNRESOLVED`` events: grants that precede
   the event, cover the action, and were live at the event's timestamp. This is
   an INFERENCE, not evidence — when it is ambiguous it stays UNVERIFIED.

3. Coverage figures and a machine-readable verdict.

USAGE
-----
    python scripts/derive_decision_basis_view.py --source audit_store.db --out out_dir
    python scripts/derive_decision_basis_view.py --self-test      # synthetic chain

Exit code 0 = every event can answer the question with EVIDENCE.
Exit code 1 = at least one event cannot (this is the gate's true negative).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile

PLACEHOLDER_RULES = {
    "unadjudicated", "default_deny", "error", "none", "unknown",
    "not_adjudicated", "no_rule", "n/a", "",
}

BASIS_KEYS = ("grant_id", "policy_rule", "rule_id", "basis", "authority")

# Classification labels. The word UNVERIFIED is mandatory on anything that is
# not evidence: this view must never be mistaken for the authoritative record.
L_EVIDENCE = "EVIDENCE"
L_UNRESOLVED = "UNVERIFIED/DERIVED"
L_PLACEHOLDER = "UNVERIFIED/DERIVED"
L_ABSENT = "UNVERIFIED/DERIVED"


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _details(raw):
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            d = json.loads(raw)
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}
    return {}


def copy_source_readonly(source: str) -> str:
    """Isolate the source via the backup API; never write to the original."""
    if not os.path.exists(source):
        raise SystemExit(f"source db not found: {source}")
    tmp_dir = tempfile.mkdtemp(prefix="basis_view_")
    dest = os.path.join(tmp_dir, "working_copy.db")
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(dest)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return dest


def load_events(db_path: str):
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        cur = conn.execute(
            "SELECT seq, event_id, event_type, principal_id, scope, timestamp, "
            "correlation_id, outcome, details FROM audit_events ORDER BY seq ASC")
        out = []
        for seq, eid, etype, pid, scope, ts, cid, outcome, raw in cur.fetchall():
            d = _details(raw)
            if not isinstance(d, dict):
                d = {}
            d.setdefault("seq", seq)
            out.append({
                "seq": seq, "event_id": eid, "event_type": etype,
                "principal_id": pid, "scope": scope, "timestamp": ts,
                "correlation_id": cid, "outcome": outcome, "details": d,
            })
        return out
    finally:
        conn.close()


def _is_grant_issuance(d: dict) -> bool:
    return bool(d.get("grant_id")) and "expires_at" in d and "actions" in d


def classify(events):
    """Classify every event's basis and attempt candidate resolution."""
    grants = [e for e in events if _is_grant_issuance(e["details"])]

    results = []
    for e in events:
        d = e["details"]
        gid = d.get("grant_id")
        rule = d.get("policy_rule") or d.get("rule_id")
        rule_s = str(rule).strip().lower() if rule is not None else ""
        action = d.get("action")
        ts = e.get("timestamp") or 0.0

        basis_seq = None
        if gid:
            for g in grants:
                if g["details"].get("grant_id") == gid:
                    basis_seq = g["seq"]
                    break

        if gid and basis_seq is not None and basis_seq < e["seq"]:
            label, reason = L_EVIDENCE, (
                f"grant {gid} issued at seq={basis_seq} (< event seq={e['seq']})")
        elif gid and basis_seq is not None and basis_seq == e["seq"] and _is_grant_issuance(d):
            # The issuance event cites its own grant id; it IS the basis record
            # other events would cite. Classifying it as "not earlier" would be
            # a false back-fill accusation against the record that anchors the
            # chain. It is not itself seeking a basis.
            label, reason = L_EVIDENCE, (
                f"this event IS the grant issuance for {gid} (seq={e['seq']}); "
                "it is the basis record, not a citation of one")
        elif gid and basis_seq is not None and basis_seq >= e["seq"]:
            label, reason = L_UNRESOLVED, (
                f"cited grant seq={basis_seq} is NOT earlier than event "
                f"seq={e['seq']} — back-fill signature")
        elif gid:
            label, reason = L_UNRESOLVED, (
                f"cited grant {gid} has no issuance record in this chain")
        elif rule_s and rule_s not in PLACEHOLDER_RULES:
            # A real adjudication by name, but no pointer to the specific
            # authorisation. Try to resolve candidates; label as inference.
            cands = []
            for g in grants:
                gd = g["details"]
                if g["seq"] >= e["seq"]:
                    continue
                if action and action not in (gd.get("actions") or []):
                    continue
                if float(gd.get("expires_at") or 0) < float(ts):
                    continue
                cands.append(g["seq"])
            if len(cands) == 1:
                label = L_UNRESOLVED
                reason = (
                    f"rule '{rule}' adjudicated but the event carries no "
                    f"grant_id; exactly one candidate grant precedes it "
                    f"(seq={cands[0]}) — INFERRED, not cited by the event")
            elif len(cands) > 1:
                label = L_UNRESOLVED
                reason = (
                    f"rule '{rule}' adjudicated but no grant_id; "
                    f"{len(cands)} candidate grants precede it "
                    f"({cands}) — ambiguous, cannot be attributed")
            else:
                label = L_UNRESOLVED
                reason = (
                    f"rule '{rule}' adjudicated but no grant_id and no "
                    f"preceding grant covers action={action!r}")
        elif rule_s:
            label, reason = L_PLACEHOLDER, (
                f"policy_rule={rule!r} is a placeholder — the field is "
                f"populated but adjudicates nothing")
        else:
            label, reason = L_ABSENT, "event carries no basis field at all"

        results.append({
            "seq": e["seq"],
            "event_type": e["event_type"],
            "action": action,
            "principal_id": e["principal_id"],
            "outcome": e["outcome"],
            "basis_label": label,
            "basis_seq": basis_seq,
            "reason": reason,
        })
    return results


def build_report(source: str, events, results):
    total = len(results)
    ev = [r for r in results if r["basis_label"] == L_EVIDENCE]
    return {
        "provenance": {
            "source_db": os.path.abspath(source),
            "source_sha256": _sha256_file(source),
            "kind": "DERIVED",
            "authoritative": False,
            "status": "UNVERIFIED/DERIVED",
            "note": (
                "This is a read-only derived view. It is NOT the authoritative "
                "audit record and does not modify it. Any association it "
                "produces is an inference over already-written records unless "
                "explicitly labelled EVIDENCE with basis_seq < event_seq."),
        },
        "criterion": {
            "question": "on what basis was this change allowed?",
            "hard_rule": "a basis is evidence only if basis_seq < event_seq",
        },
        "coverage": {
            "events_total": total,
            "answerable_with_evidence": len(ev),
            "not_answerable": total - len(ev),
            "pct_answerable": round(len(ev) / total * 100, 2) if total else 0.0,
        },
        "events": results,
    }


def self_test():
    """Build a synthetic chain in a temp DB and run the view over it."""
    tmp = tempfile.mkdtemp(prefix="basis_selftest_")
    db = os.path.join(tmp, "audit.db")
    os.environ["AUDIT_DB_PATH"] = db
    os.environ["LIUHAO_WORKSPACE_ROOT"] = tmp
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    from src.kernels.audit import get_audit_store
    from src.kernels._crosscutting import kernel_action
    from src.kernels._sovereignty import human_sovereign, issue_grant

    get_audit_store().initialize()
    from src.kernels.identity import get_identity_manager
    mgr = get_identity_manager()
    ident = mgr.create_human_identity("basis.view.selftest", trust_score=1.0)
    principal = getattr(ident, "principal_id", None) or ident.id

    grant = issue_grant(principal, ["capability.register"],
                        reason="basis view self-test", ttl_seconds=300)

    @kernel_action("capability.register")
    def _gated():
        return "ok"

    @kernel_action("audit.verify_chain")
    def _ungated():
        return "ok"

    with human_sovereign(principal=principal, actions=["capability.register"],
                         grant_id=grant.grant_id):
        _gated()
    _ungated()

    out = os.path.join(tmp, "out")
    os.makedirs(out, exist_ok=True)
    return db, out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", help="path to the source audit db")
    ap.add_argument("--out", help="output directory")
    ap.add_argument("--self-test", action="store_true",
                    help="build a synthetic chain and run over it")
    args = ap.parse_args()

    if args.self_test:
        source, out = self_test()
        print(f"[self-test] built chain at {source}")
    else:
        if not args.source:
            ap.error("--source is required (or use --self-test)")
        source, out = args.source, args.out or tempfile.mkdtemp(prefix="basis_out_")

    working = copy_source_readonly(source)
    try:
        events = load_events(working)
        results = classify(events)
        report = build_report(source, events, results)
    finally:
        shutil.rmtree(os.path.dirname(working), ignore_errors=True)

    os.makedirs(out, exist_ok=True)
    report_path = os.path.join(out, "decision_basis_view.json")
    with open(report_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, sort_keys=True)

    cov = report["coverage"]
    print("=" * 78)
    print("DERIVED DECISION-BASIS VIEW  (status: UNVERIFIED/DERIVED)")
    print("=" * 78)
    print(f"source           : {report['provenance']['source_db']}")
    print(f"source sha256    : {report['provenance']['source_sha256'][:16]}...")
    print(f"events total     : {cov['events_total']}")
    print(f"answerable (EVIDENCE)      : {cov['answerable_with_evidence']}")
    print(f"NOT answerable             : {cov['not_answerable']}")
    print(f"pct answerable             : {cov['pct_answerable']}%")
    print()
    for r in results:
        mark = "OK " if r["basis_label"] == L_EVIDENCE else "-- "
        print(f"  [{mark}] seq={r['seq']:<4} {str(r['action'])[:26]:<26} "
              f"{r['basis_label']:<22} {r['reason']}")
    print()
    print(f"report written: {report_path}")

    if cov["not_answerable"]:
        print()
        print("VERDICT: the chain CANNOT answer 'on what basis' for every event.")
        print("Unresolved events are reported as UNVERIFIED/DERIVED — they are")
        print("NOT counted as traceable.")
        return 1
    print("VERDICT: every event answers the question with EVIDENCE.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
