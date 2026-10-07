"""Revoking a human must take effect in the RUNNING process, not after restart.

The defect this guards: removing a row from the human-identity store removed
the *persisted* registration but left the in-memory identity ACTIVE, so an
approval the operator had just revoked kept authorising a CRITICAL action until
the next restart. ``IdentityStatus.SUSPENDED`` existed but was read by no code,
and ``IdentityManager`` exposed no way to suspend anything -- only
``revoke_permission``, which revokes a permission, not an identity.

Every assertion here runs in ONE process and never restarts anything: that is
the whole point. A test that only checked the store would have passed while the
defect was present.
"""
from __future__ import annotations


import pytest

from src.kernels import _sovereignty
from src.kernels.identity import (
    IdentityStatus,
    get_identity_manager,
    is_human_identity,
)
from src.kernels.policy import get_policy_engine
from src.kernels._risk_classification import KERNEL_ACTION_RISK, RiskTier


# Mandatory target for sovereignty grants (governance ruling
# 2026-10-05). Target-binding semantics themselves are covered in
# tests/kernels/test_sovereignty_target_binding.py; here a single
# concrete target is enough to exercise the other properties.
_TARGET = "kernel-target:demo"


def _a_critical_action() -> str:
    return sorted(
        a for a, r in KERNEL_ACTION_RISK.items() if r.tier == RiskTier.CRITICAL
    )[0]


def _decide(action: str, principal: str, grant_id: str | None):
    """Ask the policy engine, under a sovereignty window if one is given."""
    engine = get_policy_engine()
    context = {
        "actor": {"type": "human", "principal": principal},
        "action": {"name": action, "risk_level": "CRITICAL"},
        "resource": {"type": "capability"},
    }
    if grant_id is None:
        return engine.evaluate(context).decision
    with _sovereignty.human_sovereign(principal, [action], grant_id=grant_id):
        return engine.evaluate(context).decision


@pytest.fixture(autouse=True)
def _clean_grants():
    _sovereignty.clear_grants()
    yield
    _sovereignty.clear_grants()


def test_suspended_human_loses_sovereignty_without_restart():
    """The core assertion: ALLOW -> DENY in the same process, no restart."""
    action = _a_critical_action()
    manager = get_identity_manager()
    manager.create_human_identity("revoked-leaver")
    grant = _sovereignty.issue_grant(
        principal="revoked-leaver", actions=[action], reason="fixture", target=_TARGET
    )

    assert _decide(action, "revoked-leaver", grant.grant_id).name == "ALLOW"

    assert manager.suspend_identity("revoked-leaver", reason="left the company")

    assert is_human_identity(manager.get_identity_by_principal("revoked-leaver")) is False
    assert _decide(action, "revoked-leaver", grant.grant_id).name == "DENY"


def test_suspending_recalls_outstanding_grants():
    """A suspended human must not keep live grants; those are what authorise."""
    action = _a_critical_action()
    manager = get_identity_manager()
    manager.create_human_identity("grant-holder")
    grants = [
        _sovereignty.issue_grant(
            principal="grant-holder", actions=[action], reason="one", target=_TARGET
        ),
        _sovereignty.issue_grant(
            principal="grant-holder", actions=[action], reason="two", target=_TARGET
        ),
    ]
    assert all(g.is_active() for g in (g.grant_id and g or g for g in grants))

    manager.suspend_identity("grant-holder", reason="compromised")

    for grant in grants:
        assert _sovereignty.get_grant(grant.grant_id).is_revoked, (
            "a suspended human still held a live sovereignty grant"
        )


def test_revoke_grants_for_principal_matches_both_id_spellings():
    """'human:bob' and 'bob' are the same person; a miss would leave authority."""
    action = _a_critical_action()
    manager = get_identity_manager()
    manager.create_human_identity("spelling")
    identity = manager.get_identity_by_principal("spelling")
    grant = _sovereignty.issue_grant(
        principal=identity.id, actions=[action], reason="fixture", target=_TARGET
    )

    recalled = _sovereignty.revoke_grants_for_principal("spelling", reason="test")

    assert recalled == 1
    assert _sovereignty.get_grant(grant.grant_id).is_revoked


def test_suspend_reports_failure_instead_of_claiming_success():
    """Refusing must be visible: no silent no-op reported as success."""
    manager = get_identity_manager()
    assert manager.suspend_identity("no-such-person-at-all") is False


def test_suspend_is_idempotent():
    manager = get_identity_manager()
    manager.create_human_identity("twice")
    assert manager.suspend_identity("twice") is True
    assert manager.suspend_identity("twice") is True
    assert (
        manager.get_identity_by_principal("twice").status
        == IdentityStatus.SUSPENDED
    )


def test_suspension_is_reversible_and_keeps_the_audit_record():
    """SUSPENDED, not deleted: revocation must not destroy history."""
    manager = get_identity_manager()
    manager.create_human_identity("reversible")
    manager.suspend_identity("reversible")

    identity = manager.get_identity_by_principal("reversible")
    assert identity is not None, "revocation must not delete the identity"
    identity.status = IdentityStatus.ACTIVE
    assert is_human_identity(identity)
