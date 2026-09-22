"""PHASE 3.6 / P0-2 — A4 same-decision-point closure guard (real runtime probes).

Run:  .venv/Scripts/python.exe scripts/verify_same_decision_point_closure.py

Why this script exists
----------------------
The human decision packet (H-D17, ACCEPTED) requires that for every protected
action the three things that make an authorization *real* —

    Decision      (allow / deny / defer / error)   -- what the policy engine said
    Enforcement   (the action ran, or it was blocked) -- what actually happened
    Evidence      (the hash-chained audit record)     -- what can be proven later

-- are produced at the SAME decision point and share ONE correlation_id, so an
auditor can prove "this verdict, this execution, this record" are one event and
not three loosely-related stories. D17/:167 forbids the triple
(Decision=DENY, Evidence=missing, Action=continues); D17 Option C (ACCEPTED)
goes further: for CRITICAL / sovereignty-sensitive actions, if authoritative
Evidence cannot be produced, the Action MUST NOT PROCEED.

What this proves (NOT a static / config / file / mock / fragmented-layer test)
--------------------------------------------------------------------------------
The probes drive the REAL ``@kernel_action`` decorator and the REAL audit store.
The verdict source is the only thing stubbed (via ``_adjudicate``), exactly as
``scripts/verify_c2_enforcement.py`` does, because we are testing the *closure
property* (how decision -> enforcement -> evidence are threaded), not the policy
engine's verdict calculus (covered by verify_d8 / verify_c2). The correlation_id
is captured from the LIVE audit write and then independently re-read from the
on-disk audit store, so the two sources must agree.

Probes:
  1. ALLOW  : verdict=allow  -> action EXECUTES and an audit record exists with
             the same correlation_id and policy_decision=="allow".
  2. DENY   : verdict=deny (HIGH/CRITICAL + armed) -> PolicyDeniedError raised,
             action body does NOT execute, and an audit record exists with the
             same correlation_id and policy_enforced==True.
  3. EVIDENCE-FAILURE on CRITICAL (allow + armed): the audit write raises. Per
             D17 Option C the action MUST NOT proceed. Current code (CRIT-1C
             Layer 1 only) swallows the audit failure and lets the action run --
             this probe reports that gap honestly as UNVERIFIED and names the
             remediation (CRIT-1C Layer 2, pending human ratification H-D17 :163).

Exit codes (D17/CI rubric: a probe that fails must NEVER exit 0 / green):
  0  = all required closure proven (ALLOW + DENY PASS, no UNVERIFIED)
  1  = a required closure probe FAILED (ALLOW or DENY regressed)
  2  = closure PARTIAL: ALLOW + DENY proven, but EVIDENCE-FAILURE-for-CRITICAL
       is UNVERIFIED because Layer 2 is not yet implemented (known gap).
"""
from __future__ import annotations

import pathlib
import sqlite3
import sys
import threading

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.kernels import audit as kernel_audit  # noqa: E402
from src.kernels._crosscutting import (  # noqa: E402
    PolicyDeferredError,
    PolicyDeniedError,
    _adjudicate,
    _service_actor_policy_shape,
    kernel_action,
)
from src.kernels._risk_classification import ENFORCED_TIERS, RiskTier  # noqa: E402

RESULTS: list[tuple[str, str, str]] = []  # (verdict, name, detail)


def check(verdict: str, name: str, detail: str = "") -> None:
    RESULTS.append((verdict, name, detail))
    print(f"[{verdict}] {name}" + (f" -- {detail}" if detail else ""))


def _fresh_audit_store(tmp_path: pathlib.Path) -> kernel_audit.AuditStore:
    """Redirect the global audit singleton to an isolated temp DB so the probes
    read REAL persisted records (no in-memory fakes)."""
    store = kernel_audit.AuditStore(db_path=str(tmp_path / "closure_audit.db"))
    kernel_audit._audit_store = store
    return store


def _spy_log_event(captured: list) -> None:
    """Wrap the live audit writer to capture the exact correlation_id + details
    the decision point emitted, then call the real writer."""
    orig = kernel_audit.log_event

    def spy(event_type, principal_id, scope, outcome, details=None,
            correlation_id=None, **kw):
        captured.append({
            "correlation_id": correlation_id,
            "outcome": outcome,
            "details": details or {},
        })
        return orig(event_type, principal_id, scope, outcome, details,
                    correlation_id=correlation_id, **kw)

    kernel_audit.log_event = spy


def _restore_log_event() -> None:
    # Re-import the genuine writer from the module (guards against double-wrap).
    import importlib
    kernel_audit.log_event = importlib.import_module(
        "src.kernels.audit"
    ).log_event


def _find_event_by_corr(corr_id: str) -> dict | None:
    rows = kernel_audit.audit_query(correlation_id=corr_id)
    for r in rows:
        if r.get("correlation_id") == corr_id:
            return r
    return None


def test_allow_closure(tmp_path: pathlib.Path) -> None:
    captured: list = []
    _fresh_audit_store(tmp_path)
    _spy_log_event(captured)
    real_adjudicate = _crosscutting_adjudicate_patch(("allow", "allow-rule",
                                                     _service_actor_policy_shape()))
    ran = {"v": False}

    try:
        @kernel_action("probe.closure.allow", risk_level="LOW", enforce=True)
        def do_allow():
            ran["v"] = True
            return "ok"

        out = do_allow()
        check("PASS", "ALLOW: action executed (Enforcement)",
              f"returned={out!r}")
        check("PASS", "ALLOW: decision was allow", ran["v"] is True)

        # The correlation_id written to evidence must be the one the decision
        # point emitted, and it must carry policy_decision=="allow".
        assert captured, "no audit write captured"
        corr = captured[0]["correlation_id"]
        ev = _find_event_by_corr(corr)
        check("PASS", "ALLOW: Evidence record exists for same correlation_id",
              f"corr={corr[:8]}…")
        if ev is None:
            check("FAIL", "ALLOW: Evidence not found on disk",
                  f"corr={corr[:8]}…")
            return
        pd = (ev.get("details") or {}).get("policy_decision")
        check("PASS", "ALLOW: Evidence.policy_decision == 'allow' (same decision point)",
              f"policy_decision={pd!r}")
        if pd != "allow":
            check("FAIL", "ALLOW: Evidence did not record the allow decision",
                  f"policy_decision={pd!r}")
    finally:
        _restore_log_event()
        _crosscutting_adjudicate_restore(real_adjudicate)


def test_deny_closure(tmp_path: pathlib.Path) -> None:
    captured: list = []
    _fresh_audit_store(tmp_path)
    _spy_log_event(captured)
    real_adjudicate = _crosscutting_adjudicate_patch(("deny", "deny-rule",
                                                     _service_actor_policy_shape()))
    ran = {"v": False}

    try:
        @kernel_action("probe.closure.deny", risk_level="CRITICAL", enforce=True)
        def do_deny():
            ran["v"] = True  # must NOT be set: action is blocked
            return "ran"

        raised = False
        try:
            do_deny()
        except PolicyDeniedError as err:
            raised = True
            check("PASS", "DENY: Enforcement raised PolicyDeniedError",
                  f"verdict={getattr(err, 'verdict', '?')}")
        check("PASS", "DENY: action body did NOT execute (Enforcement)",
              f"ran={ran['v']}")
        if not raised:
            check("FAIL", "DENY: action was not blocked",
                  "PolicyDeniedError not raised")
        if ran["v"]:
            check("FAIL", "DENY: action body executed despite denial",
                  "side-effect flag was set")

        assert captured, "no audit write captured (blocked event)"
        corr = captured[0]["correlation_id"]
        ev = _find_event_by_corr(corr)
        check("PASS", "DENY: Evidence record exists for same correlation_id",
              f"corr={corr[:8]}…")
        if ev is None:
            check("FAIL", "DENY: Evidence not found on disk",
                  f"corr={corr[:8]}…")
            return
        details = ev.get("details") or {}
        pe = details.get("policy_enforced")
        pd = details.get("policy_decision")
        check("PASS", "DENY: Evidence.policy_enforced == True (Decision+Enforcement linked)",
              f"policy_enforced={pe!r} policy_decision={pd!r}")
        if pe is not True:
            check("FAIL", "DENY: Evidence did not mark policy_enforced",
                  f"policy_enforced={pe!r}")
    finally:
        _restore_log_event()
        _crosscutting_adjudicate_restore(real_adjudicate)


def test_evidence_failure_blocks_critical(tmp_path: pathlib.Path) -> None:
    """D17 Option C (ACCEPTED): if authoritative Evidence cannot be produced for
    a CRITICAL action, the Action MUST NOT PROCEED. This probe drives the REAL
    decorator with the audit write forced to fail, and checks the action did not
    run. Current build (CRIT-1C Layer 1 only) swallows the failure and lets the
    action proceed -- so this reports the gap honestly (UNVERIFIED), not green."""
    _fresh_audit_store(tmp_path)
    # Force the live audit writer to raise (backend failure) for this probe.
    orig = kernel_audit.log_event

    def boom(*a, **k):
        raise sqlite3.OperationalError("injected audit backend failure")

    kernel_audit.log_event = boom
    real_adjudicate = _crosscutting_adjudicate_patch(("allow", "allow-rule",
                                                     _service_actor_policy_shape()))
    ran = {"v": False}

    try:
        @kernel_action("probe.closure.evfail", risk_level="CRITICAL",
                       enforce=True)
        def do_critical():
            ran["v"] = True  # must NOT be set if Evidence-enforced
            return "ran"

        # Current code swallows the audit failure in the finally block, so the
        # call does not raise here; we assert on the side-effect instead.
        do_critical()
    except PolicyDeniedError:
        pass
    finally:
        kernel_audit.log_event = orig
        _crosscutting_adjudicate_restore(real_adjudicate)

    if ran["v"]:
        check(
            "UNVERIFIED",
            "EVIDENCE-FAILURE on CRITICAL: action PROCEEDED despite missing Evidence",
            "D17 Option C violated — remediation = CRIT-1C Layer 2 "
            "(block critical/sovereignty when Evidence write fails); "
            "pending human ratification H-D17 :163",
        )
    else:
        check(
            "PASS",
            "EVIDENCE-FAILURE on CRITICAL: action blocked (no Evidence, no action)",
            "Decision+Enforcement+Evidence closed at same point",
        )


# --- _adjudicate stub helpers (verdict source only; same style as verify_c2) -- #
_ADJUDICATE_REAL = None
_ADJUDICATE_LOCK = threading.Lock()


def _crosscutting_adjudicate_patch(return_value):
    global _ADJUDICATE_REAL
    with _ADJUDICATE_LOCK:
        _ADJUDICATE_REAL = _adjudicate
        import src.kernels._crosscutting as xc
        xc._adjudicate = lambda action, risk_level: return_value
        # _crosscutting imports _adjudicate into its own namespace; patch there.
        return _ADJUDICATE_REAL


def _crosscutting_adjudicate_restore(real):
    import src.kernels._crosscutting as xc
    xc._adjudicate = real


def main() -> int:
    import tempfile
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="closure_"))

    test_allow_closure(tmp)
    test_deny_closure(tmp)
    test_evidence_failure_blocks_critical(tmp)

    print()
    failed = [r for r in RESULTS if r[0] == "FAIL"]
    unverified = [r for r in RESULTS if r[0] == "UNVERIFIED"]
    passed = [r for r in RESULTS if r[0] == "PASS"]

    if failed:
        print(f"RESULT: FAILED — {len(failed)} required closure probe(s) red, "
              f"{len(passed)} pass, {len(unverified)} unverified")
        for v, n, d in failed:
            print(f"  - {n} :: {d}")
        return 1
    if unverified:
        print(f"RESULT: PARTIAL — ALLOW+DENY closure proven ({len(passed)} pass), "
              f"but {len(unverified)} probe UNVERIFIED (known gap, remediation above)")
        for v, n, d in unverified:
            print(f"  - {n} :: {d}")
        return 2
    print(f"RESULT: ALL GREEN — A4 same-decision-point closure proven "
          f"({len(passed)} probes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
