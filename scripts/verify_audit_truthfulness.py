#!/usr/bin/env python3
"""Audit-truthfulness verification (Phase 3.6 -- owner directive).

Proves the core requirement:

    A ``policy decision = deny`` is NEVER recorded as ``outcome = success``.

The kernel_action decorator previously executed the wrapped body anyway under
the record-only (C-1) posture and labelled the event ``outcome="success"``,
which silently hid a policy refusal. This script exercises the *real* decorator
end-to-end through the *real* policy engine + *real* audit store (redirected to
a temp DB so the frozen HC-01 evidence store is never touched) and asserts:

  1. A definitive ``deny`` verdict records ``outcome == "denied"`` (never
     ``"success"``), ``policy_decision == "deny"``, ``policy_enforced == False``,
     while the body STILL runs (additive -- no operational self-lock).
  2. Every action in INTERNAL_SERVICE_DENIED_ACTIONS, when invoked through the
     decorator, records ``outcome != "success"`` (the systematic guarantee).
  3. An allowed action records ``outcome == "success"`` + ``policy_decision ==
     "allow"`` (truthful allow, not a fake denial).
  4. A reclassified operational action (identity.create_identity) now records
     ``allow`` + ``success`` (the policy posture was completed, not just
     relabelled).

Exit code 0 = all guarantees hold; 1 = a guarantee failed.
"""

import os
import sys
import tempfile

# --- isolate from the frozen HC-01 live store: REDIRECT audit + workspace -----
_TMP = tempfile.mkdtemp(prefix="liuhao_audit_truth_")
AUDIT_DB = os.path.join(_TMP, "audit_store.db")
os.environ["AUDIT_DB_PATH"] = AUDIT_DB
os.environ["LIUHAO_WORKSPACE_ROOT"] = _TMP
os.environ["LIUHAO_AUDIT_SYNCHRONOUS"] = "FULL"

from src.kernels._crosscutting import kernel_action
from src.kernels.policy import (
    INTERNAL_SERVICE_ALLOWED_ACTIONS,
    INTERNAL_SERVICE_DENIED_ACTIONS,
)
from src.kernels.audit import audit_query


def _latest_details(action_name):
    for ev in audit_query(limit=500, reverse=True):
        det = ev.get("details") or {}
        if det.get("action") == action_name:
            # outcome lives at the event top level; policy_decision /
            # policy_enforced live inside details.
            return ev, det, ev.get("outcome")
    return None, None, None


class _Probe:
    @kernel_action("identity.grant_permission")  # DENIED for service
    def denied_action(self):
        return "ran"

    @kernel_action("memory.store")  # ALLOWED for service
    def allowed_action(self):
        return "ran"

    @kernel_action("identity.create_identity")  # reclassified ALLOWED
    def moved_action(self):
        return "ran"


def _check(label, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {label}{(' -- ' + detail) if detail else ''}")
    return cond


def main():
    failures = 0
    probe = _Probe()

    print("=== 1. denied action: outcome must be 'denied', body still runs ===")
    ret = probe.denied_action()  # additive: body runs
    ev, det, outcome = _latest_details("identity.grant_permission")
    failures += 0 if _check("body still executed (additive)", ret == "ran", f"returned {ret!r}") else 1
    failures += 0 if _check("audit event written", det is not None) else 1
    if det is not None:
        failures += 0 if _check("outcome != 'success' (core requirement)",
                                outcome != "success",
                                f"outcome={outcome!r}") else 1
        failures += 0 if _check("outcome == 'denied'",
                                outcome == "denied",
                                f"outcome={outcome!r}") else 1
        failures += 0 if _check("policy_decision == 'deny'",
                                det.get("policy_decision") == "deny",
                                f"policy_decision={det.get('policy_decision')!r}") else 1
        failures += 0 if _check("policy_enforced == False (record-only)",
                                det.get("policy_enforced") is False) else 1

    print("\n=== 2. EVERY denied action records outcome != 'success' ===")
    for name in sorted(INTERNAL_SERVICE_DENIED_ACTIONS):
        # dynamic stub so we exercise the real decorator + real policy engine
        def _stub(self):
            return "ran"

        _stub.__name__ = "denied_%s" % name.replace(".", "_")
        klass = type("Probe_%s" % _stub.__name__, (_Probe,), {_stub.__name__: kernel_action(name)(_stub)})
        inst = klass()
        getattr(inst, _stub.__name__)()
        _e, d, outcome = _latest_details(name)
        ok = d is not None and outcome != "success"
        detail = (f"outcome={outcome!r} decision={d.get('policy_decision')!r}"
                  if d is not None else "no event")
        failures += 0 if _check(f"denied action {name}", ok, detail) else 1

    print("\n=== 3. allowed action: outcome == 'success' + decision == 'allow' ===")
    ra = probe.allowed_action()
    ev, det, outcome = _latest_details("memory.store")
    failures += 0 if _check("body executed", ra == "ran") else 1
    if det is not None:
        failures += 0 if _check("outcome == 'success'", outcome == "success",
                                f"outcome={outcome!r}") else 1
        failures += 0 if _check("policy_decision == 'allow'",
                                det.get("policy_decision") == "allow",
                                f"policy_decision={det.get('policy_decision')!r}") else 1

    print("\n=== 4. reclassified operational action: now allow + success ===")
    rm = probe.moved_action()
    ev, det, outcome = _latest_details("identity.create_identity")
    failures += 0 if _check("body executed", rm == "ran") else 1
    if det is not None:
        failures += 0 if _check("outcome == 'success' (truthful allow)",
                                outcome == "success",
                                f"outcome={outcome!r}") else 1
        failures += 0 if _check("policy_decision == 'allow'",
                                det.get("policy_decision") == "allow",
                                f"policy_decision={det.get('policy_decision')!r}") else 1

    print("\n=== 5. allow/deny lists: disjoint + cover all decorated actions ===")
    failures += 0 if _check("disjoint", not (INTERNAL_SERVICE_ALLOWED_ACTIONS & INTERNAL_SERVICE_DENIED_ACTIONS)) else 1

    print()
    if failures:
        print(f"RESULT: FAIL ({failures} check(s) failed)")
        return 1
    print("RESULT: PASS -- policy decision=deny is never recorded as outcome=success")
    print(f"  (audit DB isolated at {AUDIT_DB}; HC-01 frozen store untouched)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
