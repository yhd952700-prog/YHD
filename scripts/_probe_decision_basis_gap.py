"""FORENSIC PROBE — can you answer "on what basis was this allowed?"

This measures the ledger's real answerability BEFORE any derived view is
written, so the view is built against a MEASURED gap instead of an assumed one.

The bar set for this work:

    Given ANY audit event, can you answer "what allowed this change?"
    If not, it is not done -- and that must be EXECUTABLE as a gate.

Three measurable sub-questions:

  Q1  COVERAGE    — what fraction of chain events carry a basis at all?
  Q2  SUBSTANCE   — of the events that carry a `policy_rule`, how many carry a
                    REAL adjudication versus a PLACEHOLDER? A placeholder such
                    as `unadjudicated` / `default_deny` / `error` occupies the
                    field and therefore *looks* like an answer while answering
                    nothing. Counting it as coverage would be the exact
                    "looks like it works" failure this probe exists to catch,
                    so it is measured separately.
  Q3  LINKABILITY — for events citing a grant_id, is the cited grant itself an
                    EARLIER record in the same chain (basis_seq < event_seq)?
                    "the record exists" and "the record predates the event" are
                    different claims; only the second is evidence rather than
                    back-fill.

The chain is produced by the real production write paths into a TEMP database.
The frozen HC-01 ``audit_store.db`` is never opened, read, or written here.

Run: .venv/Scripts/python.exe scripts/_probe_decision_basis_gap.py
"""
import json
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_TMP = tempfile.mkdtemp(prefix="probe_basis_gap_")
os.environ["AUDIT_DB_PATH"] = os.path.join(_TMP, "audit.db")
os.environ["LIUHAO_WORKSPACE_ROOT"] = _TMP
os.environ["LIUHAO_MEMORY_DB_PATH"] = os.path.join(_TMP, "memory.db")

from src.kernels.audit import get_audit_store  # noqa: E402

# Values that occupy a basis field while conveying no actual adjudication.
PLACEHOLDER_RULES = {
    "unadjudicated", "default_deny", "error", "none", "unknown",
    "not_adjudicated", "no_rule", "n/a", "",
}


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


def build_chain():
    """Write a real chain via the production paths: grant, then actions."""
    from src.kernels._sovereignty import human_sovereign, issue_grant

    store = get_audit_store()
    store.initialize()

    principal = None
    try:
        from src.kernels.identity import get_identity_manager
        mgr = get_identity_manager()
        # Use the SANCTIONED constructor: create_human_identity stamps
        # metadata["kind"] == "human", which is the only marker
        # is_human_identity accepts. Reusing an arbitrary existing identity is
        # what failed on the first run (kind was None -> 'system' refused).
        try:
            ident = mgr.create_human_identity("probe.human.basis", trust_score=1.0)
            principal = getattr(ident, "principal_id", None) or getattr(ident, "id", None)
            print(f"  created human identity: {principal}")
        except Exception as exc:
            print(f"  create_human_identity failed: {type(exc).__name__}: {exc}")
    except Exception as exc:
        print(f"  identity lookup failed: {type(exc).__name__}: {exc}")

    grant = None
    if principal:
        try:
            # The grant must govern an enforcement-gated action (HIGH/CRITICAL);
            # a MEDIUM action such as ai.execute_planner_task is refused by
            # design, which is why the first run produced no grant record.
            grant = issue_grant(principal, ["capability.register"],
                                reason="probe: basis chain", ttl_seconds=180)
            print(f"  issued grant {grant.grant_id[:12]}... for principal={principal}")
        except Exception as exc:
            print(f"  issue_grant refused: {type(exc).__name__}: {exc}")
    else:
        print("  no usable human identity found; grant path skipped")

    from src.kernels._crosscutting import kernel_action

    @kernel_action("capability.register")
    def _gated():
        return "ok"

    @kernel_action("audit.verify_chain")
    def _ungated():
        return "ok"

    try:
        if grant is not None:
            with human_sovereign(principal=principal,
                                 actions=["capability.register"],
                                 grant_id=grant.grant_id):
                _gated()
        else:
            _gated()
    except Exception as exc:
        print(f"  _gated raised: {type(exc).__name__}: {exc}")
    try:
        _ungated()
    except Exception as exc:
        print(f"  _ungated raised: {type(exc).__name__}: {exc}")
    return grant


def load_rows():
    """Read rows WITH `seq` — query_events omits it, and seq is the whole
    point of the linkability criterion."""
    db = os.environ["AUDIT_DB_PATH"]
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        cur = conn.execute(
            "SELECT seq, event_type, principal_id, outcome, details "
            "FROM audit_events ORDER BY seq ASC")
        rows = []
        for seq, etype, pid, outcome, raw in cur.fetchall():
            rows.append({"seq": seq, "type": etype, "principal": pid,
                         "outcome": outcome, "details": _details(raw)})
        return rows
    finally:
        conn.close()


def main():
    print("=" * 78)
    print("PROBE: the decision-basis gap in the REAL audit chain")
    print("=" * 78)
    build_chain()
    rows = load_rows()
    print(f"\nchain length: {len(rows)}")

    # index grant issuances by grant_id -> seq
    grant_seq = {}
    for r in rows:
        gid = r["details"].get("grant_id")
        if gid and "expires_at" in r["details"] and "actions" in r["details"]:
            grant_seq.setdefault(gid, r["seq"])

    print()
    print("=" * 78)
    print("Q1 COVERAGE — does each event carry ANY basis field?")
    print("=" * 78)
    basis_fields = ("policy_rule", "rule_id", "grant_id", "basis", "authority")
    with_any = [r for r in rows
                if any(r["details"].get(k) not in (None, "") for k in basis_fields)]
    print(f"   events total             : {len(rows)}")
    print(f"   with at least one basis  : {len(with_any)}")
    print(f"   with NO basis field      : {len(rows) - len(with_any)}")

    print()
    print("=" * 78)
    print("Q2 SUBSTANCE — of those, how many are REAL vs PLACEHOLDER?")
    print("=" * 78)
    real, placeholder = [], []
    for r in rows:
        rule = r["details"].get("policy_rule") or r["details"].get("rule_id")
        gid = r["details"].get("grant_id")
        has_real = bool(gid) or (
            rule is not None and str(rule).strip().lower() not in PLACEHOLDER_RULES)
        (real if has_real else placeholder).append(r)
    print(f"   events with a SUBSTANTIVE basis : {len(real)}")
    print(f"   events with a PLACEHOLDER only  : {len(placeholder)}")
    print("   -- per event --")
    for r in rows:
        rule = r["details"].get("policy_rule")
        gid = r["details"].get("grant_id")
        verdict = "REAL     " if r in real else "PLACEHOLD"
        print(f"   [{verdict}] seq={r['seq']:<4} {r['type']:<14} "
              f"action={str(r['details'].get('action'))[:26]:<26} "
              f"rule={str(rule)[:20]!r:<22} grant={'yes' if gid else '-'}")

    print()
    print("=" * 78)
    print("Q3 LINKABILITY — is the cited basis an EARLIER chain record?")
    print("=" * 78)
    cited = [r for r in rows if r["details"].get("grant_id")]
    linkable, dangling, not_earlier = [], [], []
    for r in cited:
        gid = r["details"]["grant_id"]
        gseq = grant_seq.get(gid)
        if gseq is None:
            dangling.append((r["seq"], gid))
        elif gseq < r["seq"]:
            linkable.append((r["seq"], gid, gseq))
        else:
            not_earlier.append((r["seq"], gid, gseq))
    print(f"   events citing a grant_id                     : {len(cited)}")
    print(f"   grant issuances found in chain               : {grant_seq}")
    print(f"   citations VERIFIABLE (grant seq < event seq) : {len(linkable)} {linkable}")
    print(f"   citations DANGLING (no grant record at all)  : {len(dangling)} {dangling}")
    print(f"   citations NOT EARLIER (back-fill signature)  : {len(not_earlier)} {not_earlier}")

    print()
    print("=" * 78)
    print("THE GAP TO BUILD AGAINST")
    print("=" * 78)
    print(f"   events that CAN answer 'what allowed this'   : {len(real)}")
    print(f"   events that CANNOT                           : {len(rows) - len(real)}")
    print("\n   NOTE: a placeholder policy_rule is NOT an answer -- it occupies")
    print("   the field while adjudicating nothing. Counting it as covered would")
    print("   be the failure mode under investigation.")
    print(f"\n   tmp = {_TMP}")


if __name__ == "__main__":
    main()
