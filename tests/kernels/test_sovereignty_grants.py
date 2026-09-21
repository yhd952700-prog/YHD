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
from src.kernels._crosscutting import _adjudicate, kernel_action
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
    """Newest audit event written for ``action_name`` -- attribution verified.

    Does **not** filter by ``principal_id``: before A2 the decorator recorded the
    literal subject ``"kernel"``, so a test could find its own row by that
    string. A2 replaced the constant with the *actual* acting principal (here:
    the delegated human), so pinning the literal finds either nothing or a stale
    row left in the shared ``audit_store.db`` by an earlier run.

    Attribution is asserted as a **value** (F26): ``actor_fingerprint`` must be
    32 hex chars, and the event's ``principal_id`` must agree with the
    ``actor_identity_id`` in ``details`` (the A2 keyspace convergence).
    """
    for ev in audit_query(limit=200, reverse=True):
        details = ev.get("details") or {}
        if details.get("action") != action_name:
            continue
        fp = details.get("actor_fingerprint")
        assert isinstance(fp, str) and len(fp) == 32 and all(
            c in "0123456789abcdef" for c in fp
        ), (
            f"audit row for {action_name!r} carries no usable attribution: "
            f"actor_fingerprint={fp!r} (F26: a non-empty column is not evidence)"
        )
        assert ev.get("principal_id") == details.get("actor_identity_id"), (
            f"audit principal keyspace divergence for {action_name!r}: "
            f"principal_id={ev.get('principal_id')!r} vs "
            f"actor_identity_id={details.get('actor_identity_id')!r}"
        )
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


def _action_audit_row(action_name: str):
    """The newest audit *event* (not just its details) for ``action_name``."""
    for ev in audit_query(limit=200, reverse=True):
        if (ev.get("details") or {}).get("action") == action_name:
            return ev
    return None


class TestAdjudicationRechecksGrantAuthority:
    """F33 — a grant valid at window-open time is not authority at decision time.

    ``grant_window`` validates the grant object it is handed, once, when the
    window opens. The adjudication point used to trust that snapshot, so a grant
    revoked in between still produced ``verdict=allow`` as a *human* actor. Each
    test below fails (allow) on that behaviour and passes (deny) once the grant
    is re-resolved from the registry at decision time.
    """

    def test_revoked_grant_is_denied_at_adjudication(self, humans):
        _mgr, human = humans
        grant = sov.issue_grant(human.id, ["capability.retire"])
        window = sov.grant_window(grant)
        sov.revoke_grant(grant.grant_id)

        with window:
            verdict, rule, actor = _adjudicate("capability.retire", "CRITICAL")

        assert verdict == "deny", (
            f"a revoked grant adjudicated as {verdict!r} (rule={rule!r}, "
            f"actor={actor!r}) — revocation is not enforced at the decision point"
        )
        assert actor["type"] != "human", actor

    def test_expired_grant_is_denied_at_adjudication(self, humans):
        _mgr, human = humans
        grant = sov.issue_grant(human.id, ["capability.retire"], ttl_seconds=0.05)
        window = sov.grant_window(grant)
        time.sleep(0.1)
        assert grant.is_expired() is True

        with window:
            verdict, rule, actor = _adjudicate("capability.retire", "CRITICAL")

        assert verdict == "deny", (
            f"an expired grant adjudicated as {verdict!r} (rule={rule!r}, "
            f"actor={actor!r}) — expiry is not enforced at the decision point"
        )
        assert actor["type"] != "human", actor

    def test_valid_grant_still_allows_at_adjudication(self, humans):
        """Non-regression: the re-check must not break a genuinely live grant."""
        _mgr, human = humans
        grant = sov.issue_grant(human.id, ["capability.retire"])
        with sov.grant_window(grant):
            verdict, rule, actor = _adjudicate("capability.retire", "CRITICAL")
        assert verdict == "allow"
        assert rule == "human_sovereignty"
        assert actor["type"] == "human"

    def test_revoked_grant_audit_is_not_a_human_approval(self, humans):
        """F26 — the authoritative chain must not record a refused claim as a human.

        On the pre-fix code this row reads ``actor_kind='human'`` +
        ``actor_source='sovereignty-window'`` + ``policy_decision='allow'``: a
        REVOKED grant written into the hash chain as a human-authored approval.
        """
        _mgr, human = humans
        grant = sov.issue_grant(human.id, ["capability.retire"])
        window = sov.grant_window(grant)
        sov.revoke_grant(grant.grant_id)

        @kernel_action("capability.retire")
        def retire():
            return "ran-under-revoked-grant"

        with window:
            retire()

        details = _action_audit("capability.retire")
        assert details is not None
        assert details["actor_kind"] != "human", (
            f"a revoked grant was recorded as a human author: {details!r}"
        )
        assert details["actor_source"] != "sovereignty-window", (
            f"a revoked grant was recorded as a live sovereignty window: {details!r}"
        )
        assert details["actor_source"] == "sovereignty-window-rejected"
        assert details["sovereignty_claim"] == "grant-revoked"
        assert details["policy_decision"] == "deny", details
        assert details["sovereignty_grant"] is None, (
            "a rejected window must not record a grant id that reads as authorisation"
        )


class TestAuditKeyspaceConvergence:
    """F26 — audit and authorization must name an actor in ONE key space."""

    def test_convergence_holds_for_plain_principal_form(self, humans):
        """A window opened with the plain principal name must still converge.

        ``human_sovereign`` accepts either the identity id (``human:bob``) or the
        principal name (``bob``); the audit ``principal_id`` used to echo whichever
        was passed, so it agreed with ``actor_identity_id`` only for callers that
        happened to pass the id. The property must hold by construction, not by
        lucky spelling.
        """
        _mgr, human = humans
        assert human.principal != human.id, "test premise: the two forms differ"
        grant = sov.issue_grant(human.id, ["capability.retire"])

        @kernel_action("capability.retire")
        def retire():
            return "ran-under-plain-principal-window"

        with sov.human_sovereign(
            human.principal, grant.actions, grant_id=grant.grant_id
        ):
            retire()

        row = _action_audit_row("capability.retire")
        assert row is not None, "no audit row written"
        details = row["details"]
        assert row.get("principal_id") == details.get("actor_identity_id"), (
            f"audit keyspace divergence for the same actor: "
            f"principal_id={row.get('principal_id')!r} vs "
            f"actor_identity_id={details.get('actor_identity_id')!r}"
        )
        assert row.get("principal_id") == human.id, (
            "the canonical principal form (identity.id) must be what is recorded"
        )

    def test_convergence_holds_for_grant_issued_by_plain_name(self, humans):
        """The same property must hold when the *grant* was issued by the plain name.

        ``issue_grant`` accepts either spelling, so a grant issued as ``bob``
        opens a window whose principal is ``bob`` while the identity's id is
        ``human:bob``. Convergence must be a property of the audit writer, not of
        which spelling the deployer happened to use.
        """
        _mgr, human = humans
        assert human.principal != human.id, "test premise: the two forms differ"
        grant = sov.issue_grant(human.principal, ["capability.retire"])

        @kernel_action("capability.retire")
        def retire():
            return "ran-under-plain-named-grant"

        with sov.grant_window(grant):
            assert retire() == "ran-under-plain-named-grant"

        row = _action_audit_row("capability.retire")
        assert row is not None, "no audit row written"
        details = row["details"]
        assert row.get("principal_id") == details.get("actor_identity_id"), (
            f"audit keyspace divergence for the same actor: "
            f"principal_id={row.get('principal_id')!r} vs "
            f"actor_identity_id={details.get('actor_identity_id')!r}"
        )
        assert row.get("principal_id") == human.id, (
            "the canonical principal form (identity.id) must be what is recorded"
        )
