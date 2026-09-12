"""Policy C-4 — audited approval grants for kernel actions.

C-3 gave us a *channel* (``human_sovereign``); C-4 makes the authorization
itself a first-class object. These tests prove the grant layer is:

  * **bounded** -- TTL enforced, hard ceiling, expiry hides from listings;
  * **revocable** -- and revocation is idempotent and audited;
  * **least-privilege** -- only known, enforcement-gated (HIGH/CRITICAL) actions;
  * **human-only** -- service identities and unknown/inactive principals refused;
  * **accountable** -- issue/revoke write audit events, and an action executed
    under a window carries the grant id in its own audit record, closing the
    chain ``kernel action <- grant <- authorising human``.
"""

from __future__ import annotations

import time

import pytest

from src.kernels import _sovereignty as sov
from src.kernels._crosscutting import kernel_action
from src.kernels.audit import AuditEventType, audit_query
from src.kernels.identity import (
    INTERNAL_SERVICE_PRINCIPAL,
    IdentityManager,
    IdentityScope,
    IdentityStatus,
)


@pytest.fixture
def humans(monkeypatch):
    """Isolated IdentityManager with one ACTIVE human + the service identity."""
    mgr = IdentityManager()
    human = mgr.create_identity(
        "human-c4-approver", scope=IdentityScope.L0, metadata={"kind": "human"}
    )
    assert human.status == IdentityStatus.ACTIVE
    monkeypatch.setattr("src.kernels.identity.get_identity_manager", lambda: mgr)
    sov.clear_grants()
    yield mgr, human
    sov.clear_grants()


def _lifecycle_events(outcome: str, limit: int = 50):
    """Audit events of type HUMAN_SOVEREIGNTY_OVERRIDE with the given outcome."""
    events = audit_query(
        event_type=AuditEventType.HUMAN_SOVEREIGNTY_OVERRIDE, limit=limit, reverse=True
    )
    return [e for e in events if e.get("outcome") == outcome]


def _action_audit(action_name: str):
    for ev in audit_query(principal_id="kernel", limit=200, reverse=True):
        details = ev.get("details") or {}
        if details.get("action") == action_name:
            return details
    return None


class TestGrantLifecycle:
    def test_issue_records_and_lists(self, humans):
        _mgr, human = humans
        grant = sov.issue_grant(human.id, ["capability.retire"], reason="quarterly cleanup")

        assert grant.grant_id
        assert grant.principal == human.id
        assert grant.actions == frozenset({"capability.retire"})
        assert grant.reason == "quarterly cleanup"
        assert grant.issued_by == human.id
        assert grant.is_active()
        assert grant.is_expired() is False
        assert grant.is_revoked is False

        assert sov.get_grant(grant.grant_id) is grant
        assert grant.grant_id in [g.grant_id for g in sov.list_grants()]

    def test_to_dict_shape(self, humans):
        _mgr, human = humans
        payload = sov.issue_grant(human.id, ["capability.retire"]).to_dict()
        assert payload["actions"] == ["capability.retire"]
        assert payload["is_active"] is True
        assert payload["revoked_at"] is None
        assert payload["remaining_seconds"] > 0

    def test_revoke_is_idempotent(self, humans):
        _mgr, human = humans
        grant = sov.issue_grant(human.id, ["capability.retire"])
        first = sov.revoke_grant(grant.grant_id, revoked_by=human.id, reason="changed my mind")
        second = sov.revoke_grant(grant.grant_id)
        assert first.is_revoked is True
        assert first.revoke_reason == "changed my mind"
        assert second.revoked_at == first.revoked_at

    def test_revoke_unknown_raises(self, humans):
        with pytest.raises(KeyError):
            sov.revoke_grant("no-such-grant")

    def test_revoked_hidden_from_default_listing(self, humans):
        _mgr, human = humans
        grant = sov.issue_grant(human.id, ["capability.retire"])
        sov.revoke_grant(grant.grant_id)
        assert sov.list_grants() == []
        assert [g.grant_id for g in sov.list_grants(include_revoked=True)] == [grant.grant_id]


class TestGrantIsBounded:
    def test_ttl_must_be_positive(self, humans):
        _mgr, human = humans
        for bad in (0, -1):
            with pytest.raises(ValueError):
                sov.issue_grant(human.id, ["capability.retire"], ttl_seconds=bad)

    def test_ttl_has_a_ceiling(self, humans):
        _mgr, human = humans
        with pytest.raises(ValueError):
            sov.issue_grant(
                human.id, ["capability.retire"], ttl_seconds=sov.MAX_GRANT_TTL_SECONDS + 1
            )

    def test_expired_grant_cannot_open_a_window(self, humans):
        _mgr, human = humans
        grant = sov.issue_grant(human.id, ["capability.retire"], ttl_seconds=0.01)
        time.sleep(0.05)
        assert grant.is_expired() is True
        assert grant.is_active() is False
        with pytest.raises(ValueError):
            sov.grant_window(grant)

    def test_expired_hidden_from_default_listing(self, humans):
        _mgr, human = humans
        grant = sov.issue_grant(human.id, ["capability.retire"], ttl_seconds=0.01)
        time.sleep(0.05)
        assert sov.list_grants() == []
        assert [g.grant_id for g in sov.list_grants(include_expired=True)] == [grant.grant_id]

    def test_revoked_grant_cannot_open_a_window(self, humans):
        _mgr, human = humans
        grant = sov.issue_grant(human.id, ["capability.retire"])
        revoked = sov.revoke_grant(grant.grant_id)
        with pytest.raises(ValueError):
            sov.grant_window(revoked)


class TestGrantIsLeastPrivilege:
    def test_unknown_action_rejected(self, humans):
        _mgr, human = humans
        with pytest.raises(ValueError):
            sov.issue_grant(human.id, ["not.a.kernel.action"])

    def test_low_and_medium_actions_rejected(self, humans):
        _mgr, human = humans
        for action in ("memory.store", "trust.assign_score"):
            with pytest.raises(ValueError):
                sov.issue_grant(human.id, [action])

    def test_empty_action_set_rejected(self, humans):
        _mgr, human = humans
        with pytest.raises(ValueError):
            sov.issue_grant(human.id, [])

    def test_critical_action_accepted(self, humans):
        _mgr, human = humans
        grant = sov.issue_grant(
            human.id, ["capability.retire", "security.set_abac_rule"]
        )
        assert grant.actions == frozenset({"capability.retire", "security.set_abac_rule"})

    def test_grant_does_not_widen_beyond_its_actions(self, humans):
        _mgr, human = humans
        grant = sov.issue_grant(human.id, ["capability.retire"])
        with sov.grant_window(grant):
            active = sov.get_active_sovereignty()
            assert active is not None
            assert "security.set_abac_rule" not in active.actions


class TestGrantRequiresAVerifiedHuman:
    def test_unknown_principal_rejected(self, humans):
        with pytest.raises(ValueError):
            sov.issue_grant("does-not-exist", ["capability.retire"])

    def test_service_identity_cannot_hold_sovereignty(self, humans):
        # The internal service principal exists and is ACTIVE, but is marked
        # kind == "service" -> it must never hold human sovereignty (OD-010).
        with pytest.raises(ValueError):
            sov.issue_grant(INTERNAL_SERVICE_PRINCIPAL, ["capability.retire"])

    def test_empty_principal_rejected(self, humans):
        with pytest.raises(ValueError):
            sov.issue_grant("", ["capability.retire"])

    def test_inactive_identity_rejected(self, humans):
        mgr, _human = humans
        suspended = mgr.create_identity(
            "human-c4-suspended", scope=IdentityScope.L0, metadata={"kind": "human"}
        )
        mgr._identities[suspended.id].status = IdentityStatus.SUSPENDED
        with pytest.raises(ValueError):
            sov.issue_grant(suspended.id, ["capability.retire"])


class TestGrantAudit:
    def test_issue_writes_granted_event(self, humans):
        _mgr, human = humans
        grant = sov.issue_grant(human.id, ["capability.retire"], reason="audit me")
        events = _lifecycle_events("granted")
        assert events, "issue_grant must write a HUMAN_SOVEREIGNTY_OVERRIDE event"
        match = [e for e in events if (e.get("details") or {}).get("grant_id") == grant.grant_id]
        assert match, "the granted audit event must carry the grant id"
        details = match[0]["details"]
        assert details["principal"] == human.id
        assert details["actions"] == ["capability.retire"]
        assert details["reason"] == "audit me"
        assert details["risk_levels"]["capability.retire"] == "CRITICAL"

    def test_revoke_writes_revoked_event(self, humans):
        _mgr, human = humans
        grant = sov.issue_grant(human.id, ["capability.retire"])
        sov.revoke_grant(grant.grant_id, revoked_by=human.id, reason="done")
        events = _lifecycle_events("revoked")
        match = [e for e in events if (e.get("details") or {}).get("grant_id") == grant.grant_id]
        assert match
        assert match[0]["details"]["revoked_by"] == human.id

    def test_action_audit_carries_the_grant_id(self, humans):
        """The causal chain: kernel action <- grant <- authorising human."""
        _mgr, human = humans
        grant = sov.issue_grant(human.id, ["capability.retire"], reason="chain")

        @kernel_action("capability.retire", enforce=True)
        def retire():
            return "ran-under-grant"

        with sov.grant_window(grant):
            assert retire() == "ran-under-grant"

        details = _action_audit("capability.retire")
        assert details is not None
        assert details["policy_decision"] == "allow"
        assert details["policy_rule"] == "human_sovereignty"
        assert details["sovereignty_grant"] == grant.grant_id
        assert details["risk_level"] == "CRITICAL"
