#!/usr/bin/env python3
"""Identity refusal -> audit-truthfulness verification (grant/revoke).

Proves the core requirement on the **authoritative** store:

    A refusal returned by ``identity.grant_permission`` /
    ``identity.revoke_permission`` is NEVER recorded as ``outcome="success"``
    on the hash-chained audit chain written by ``@kernel_action``.

Why this script exists (the defect class, once more): both methods report a
refusal by ``return False``. The ``@kernel_action`` decorator only sees "the
call returned", so unless the body declares the refusal with
``mark_action_denied(...)`` the chain event is stamped ``outcome="success"`` --
a refused permission grant reads, in the only durable record, as a successful
one. Same lie as ``resource.allocate`` / ``policy.unregister_rule`` before they
were fixed.

Everything here runs the REAL production path: the real ``IdentityManager``
methods, the real ``@kernel_action`` decorator, the real policy engine and the
real hash-chained audit store -- only redirected to a throwaway temp tree so
the frozen HC-01 production evidence is never touched.

How successful grant/revoke is reachable at all: ``identity.grant_permission``
and ``identity.revoke_permission`` are HIGH risk and are deliberately NOT in
``INTERNAL_SERVICE_ALLOWED_ACTIONS``, so adjudicated as the internal service
principal they are policy-denied and *every* event -- even a successful one --
would read ``outcome="denied"`` (the mirrored lie: the record says "refused"
for an action that actually completed). To isolate exactly what this script is
about (the body's refusal vs. the body's success), the calls run inside a real
C-3 sovereignty window opened by a verified human identity, which is the
sanctioned way a HIGH authority action is authorised. Under that window the
policy verdict is ``allow``, so the ONLY thing that can make an event read
``denied`` is the body itself declaring the refusal.

Exit code 0 = all guarantees hold; 1 = at least one failed.
"""

from __future__ import annotations

import os
import pathlib
import sys
import tempfile

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# --- isolate from the frozen HC-01 live store: redirect audit + workspace ----
_TMP = tempfile.mkdtemp(prefix="liuhao_identity_denial_")
AUDIT_DB = os.path.join(_TMP, "audit_store.db")
os.environ["AUDIT_DB_PATH"] = AUDIT_DB
os.environ["LIUHAO_WORKSPACE_ROOT"] = _TMP
os.environ["LIUHAO_AUDIT_SYNCHRONOUS"] = "FULL"
# Deterministic posture: this script is about *record truthfulness*, so neither
# the C-2 enforcement gate nor the executor fence may block/reclassify here.
os.environ.pop("LIUHAO_KERNEL_POLICY_ENFORCE", None)
os.environ.pop("LIUHAO_EXECUTOR_FENCE", None)

from src.kernels._crosscutting import kernel_action_correlation_id  # noqa: E402
from src.kernels._sovereignty import human_sovereign  # noqa: E402
from src.kernels.audit import audit_query  # noqa: E402
from src.kernels.identity import IdentityManager, IdentityScope  # noqa: E402

GRANT = "identity.grant_permission"
REVOKE = "identity.revoke_permission"
OUR_ACTIONS = (GRANT, REVOKE)

_FAILURES: list = []
#: correlation_id -> label, for every exercised call
_STEPS: dict = {}


def check(label: str, cond: bool, detail: str = "") -> bool:
    mark = "PASS" if cond else "FAIL"
    if not cond:
        _FAILURES.append(label)
    print(f"  [{mark}] {label}{(' -- ' + detail) if detail else ''}")
    return cond


def _event(cid: str, action: str):
    """The terminal hash-chain event written for one action invocation.

    HIGH/CRITICAL actions also get a mandatory-evidence ``intent`` pre-write
    carrying the same correlation id; that pre-write is not the verdict about
    what happened, so it is excluded. What remains is what a reviewer reading
    the chain would take as the answer.
    """
    rows = [
        e for e in audit_query(correlation_id=cid)
        if (e.get("details") or {}).get("action") == action
        and e.get("outcome") != "intent"
    ]
    return rows[0] if rows else None


def _refusal_step(label: str, cid: str, action: str, call, expect_return):
    """Exercise one call and assert its chain event reads ``denied``."""
    with kernel_action_correlation_id(cid):
        returned = call()
    _STEPS[cid] = label

    check(f"{label}: return value unchanged ({expect_return!r})",
          returned is expect_return, f"returned {returned!r}")

    ev = _event(cid, action)
    ok_present = check(f"{label}: hash-chain event exists", ev is not None)
    if not ok_present:
        return
    det = ev.get("details") or {}
    outcome = ev.get("outcome")
    decision = det.get("policy_decision")
    reason = det.get("denial_reason")
    print(f"        outcome={outcome!r} policy_decision={decision!r} "
          f"denial_reason={reason!r}")
    check(f"{label}: recorded outcome == 'denied'", outcome == "denied",
          f"outcome={outcome!r}")
    check(f"{label}: NOT recorded as success", outcome != "success",
          f"outcome={outcome!r}")
    check(f"{label}: carries a refusal reason, not only a policy verdict",
          isinstance(reason, str) and reason and not reason.startswith("policy:"),
          f"denial_reason={reason!r}")
    check(f"{label}: no '(deny AND success)' event for this invocation",
          not [e for e in audit_query(correlation_id=cid)
               if (e.get("details") or {}).get("action") == action
               and (e.get("details") or {}).get("policy_decision") == "deny"
               and e.get("outcome") == "success"],
          "policy_decision=deny with outcome=success would be the lie")


def _success_step(label: str, cid: str, action: str, call):
    """Exercise one call that succeeds and assert it still reads ``success``."""
    with kernel_action_correlation_id(cid):
        returned = call()
    _STEPS[cid] = label

    check(f"{label}: return value True (happy path intact)", returned is True,
          f"returned {returned!r}")

    ev = _event(cid, action)
    ok_present = check(f"{label}: hash-chain event exists", ev is not None)
    if not ok_present:
        return
    det = ev.get("details") or {}
    outcome = ev.get("outcome")
    decision = det.get("policy_decision")
    reason = det.get("denial_reason")
    print(f"        outcome={outcome!r} policy_decision={decision!r} "
          f"denial_reason={reason!r}")
    check(f"{label}: policy_decision == 'allow' (window really authorised it)",
          decision == "allow", f"policy_decision={decision!r}")
    check(f"{label}: recorded outcome == 'success'", outcome == "success",
          f"outcome={outcome!r}")
    check(f"{label}: carries NO denial reason", reason is None,
          f"denial_reason={reason!r}")


def main() -> int:
    # A fresh manager keeps this script's identities out of every other reader;
    # it is still the real IdentityManager. The crosscutting + policy layers
    # resolve the singleton lazily, so pointing it at our instance makes them
    # adjudicate against the same identities (as scripts/verify_c3_sovereignty
    # does).
    import src.kernels.identity as idmod

    mgr = IdentityManager()
    orig_get = idmod.get_identity_manager
    idmod.get_identity_manager = lambda: mgr
    try:
        human = mgr.create_identity(
            "human-denial-audit", scope=IdentityScope.L0,
            metadata={"kind": "human"},
        )
        check("verified human identity available for the sovereignty window",
              human is not None, f"id={getattr(human, 'id', None)!r}")
        # L1 target: any grant above its own scope must be refused by the
        # scope ceiling.
        target = mgr.create_identity(
            "agent-denial-audit", scope=IdentityScope.L1, permissions={"dataset:read"},
        )
        check("L1 target identity available", target is not None,
              f"id={getattr(target, 'id', None)!r}")

        missing_id = "identity-00000000-does-not-exist"

        with human_sovereign(human.id, OUR_ACTIONS):
            print("\n=== 1. grant refused by the scope ceiling (L5 > identity L1) ===")
            _refusal_step(
                "grant/scope-ceiling", "cid-grant-scope-ceiling", GRANT,
                lambda: mgr.grant_permission(
                    target.id, "cluster:admin", scope=IdentityScope.L5,
                ),
                False,
            )

            print("\n=== 2. grant to an unknown identity ===")
            _refusal_step(
                "grant/unknown-identity", "cid-grant-unknown", GRANT,
                lambda: mgr.grant_permission(
                    missing_id, "dataset:read", scope=IdentityScope.L1,
                ),
                False,
            )

            print("\n=== 3. successful grant is still recorded 'success' ===")
            _success_step(
                "grant/success", "cid-grant-success", GRANT,
                lambda: mgr.grant_permission(
                    target.id, "report:read", scope=IdentityScope.L1,
                ),
            )

            print("\n=== 4a. revoke from an unknown identity ===")
            _refusal_step(
                "revoke/unknown-identity", "cid-revoke-unknown", REVOKE,
                lambda: mgr.revoke_permission(missing_id, "report:read"),
                False,
            )

            print("\n=== 4b. revoke of a permission the identity does not hold ===")
            _refusal_step(
                "revoke/not-present", "cid-revoke-absent", REVOKE,
                lambda: mgr.revoke_permission(target.id, "permission:never-granted"),
                False,
            )

            print("\n=== 5. successful revoke is still recorded 'success' ===")
            _success_step(
                "revoke/success", "cid-revoke-success", REVOKE,
                lambda: mgr.revoke_permission(target.id, "dataset:read"),
            )

        print("\n=== 6. regression guard across the whole temp chain ===")
        events = audit_query(limit=1000, reverse=True)
        check("chain has events to inspect", bool(events),
              f"{len(events)} event(s)")
        liars = [
            e for e in events
            if (e.get("details") or {}).get("policy_decision") == "deny"
            and e.get("outcome") == "success"
        ]
        check("NO event anywhere has policy_decision=deny AND outcome=success",
              not liars, f"{len(liars)} offending event(s): "
                         f"{[(e.get('details') or {}).get('action') for e in liars]}")
        silent = [
            e for e in events
            if (e.get("details") or {}).get("action") in OUR_ACTIONS
            and (e.get("details") or {}).get("denial_reason")
            and e.get("outcome") == "success"
        ]
        check("NO grant/revoke event carries a refusal reason yet reads success",
              not silent, f"{len(silent)} offending event(s)")
        # And every exercised invocation is accounted for on the chain.
        missing_trace = [
            cid for cid in _STEPS if _event(cid, GRANT) is None
            and _event(cid, REVOKE) is None
        ]
        check("every exercised invocation left a chain event",
              not missing_trace, f"missing for {missing_trace}")

        print()
        if _FAILURES:
            print(f"RESULT: FAIL ({len(_FAILURES)} check(s) failed)")
            for name in _FAILURES:
                print(f"  - {name}")
            print(f"  (audit DB isolated at {AUDIT_DB})")
            return 1
        print("RESULT: PASS -- a refusal from identity grant/revoke is recorded "
              "as denied, never as success")
        print(f"  (audit DB isolated at {AUDIT_DB}; HC-01 frozen store untouched)")
        return 0
    finally:
        idmod.get_identity_manager = orig_get


if __name__ == "__main__":
    sys.exit(main())
