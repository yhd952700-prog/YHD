"""SovereigntyGrant target binding — governance ruling (governance-legal 2026-10-05).

The gap this closes
-------------------
A grant recorded *who* authorised *which actions* for *how long* -- but not
*which resource*. So one approval "to retire a capability" could be spent on any
capability: the grant looked entirely normal on the chain (real issuer, real
reason, real TTL) while quietly covering more than was ever reviewed. The unit
of authorisation is now the triple **(principal, action, target)**.

Governance decisions being pinned here (each is a test below):

1. ``target`` is MANDATORY, no default -- a missing target must never be read as
   "all targets". The only explicit all-targets form is ``WILDCARD_TARGET``.
2. The sovereignty namespace is INDEPENDENT of and STRICTER than RBAC/ABAC; it
   does not inherit RBAC's ``Optional``/``None``=wildcard semantics.
3. Cross-target use is REFUSED **and AUDITED** -- the attempt itself is the
   security-relevant event (enumeration probing must not be free).
4. The refusal must read as "denied", never as a 500/internal fault.
5. Wildcard issuance requires explicit secondary confirmation, and both its
   issuance and use are distinguishable on the audit chain.
6. Revoke-fidelity: every rebuild path preserves ``target``. A rebuild that
   dropped it would silently widen a scoped approval to all targets.

Controls are paired throughout: a positive case proves the normal path still
works, so a passing negative test is not just "everything is denied".
"""

from __future__ import annotations

import pytest

from src.kernels import _sovereignty as sov
from src.kernels._sovereignty import (
    WILDCARD_TARGET,
    SovereigntyDeniedError,
    SovereigntyTargetMismatchError,
)
from src.kernels.audit import AuditEventType, audit_query
from src.kernels.identity import IdentityManager, IdentityScope

ACTION = "capability.retire"
TARGET_A = "capability:alpha"
TARGET_B = "capability:beta"


@pytest.fixture
def humans(monkeypatch):
    """Isolated IdentityManager with one ACTIVE human approver."""
    mgr = IdentityManager()
    human = mgr.create_identity(
        "human-target-approver", scope=IdentityScope.L0, metadata={"kind": "human"}
    )
    monkeypatch.setattr("src.kernels.identity.get_identity_manager", lambda: mgr)
    sov.clear_grants()
    yield mgr, human
    sov.clear_grants()


def _events(outcome: str, limit: int = 100):
    """HUMAN_SOVEREIGNTY_OVERRIDE audit rows with the given outcome."""
    rows = audit_query(
        event_type=AuditEventType.HUMAN_SOVEREIGNTY_OVERRIDE, limit=limit, reverse=True
    )
    return [e for e in rows if e.get("outcome") == outcome]


# --------------------------------------------------------------------------- #
# 1. target is mandatory; absence is refused, never inferred
# --------------------------------------------------------------------------- #
class TestTargetIsMandatory:
    def test_omitting_target_is_refused_at_the_call(self, humans):
        """No default existed that could quietly mean 'every target'."""
        _mgr, human = humans
        with pytest.raises(TypeError):
            sov.issue_grant(human.id, [ACTION])

    @pytest.mark.parametrize(
        "bad", [None, "", "   ", 123, ["a"]], ids=["none", "empty", "blank", "int", "list"]
    )
    def test_none_and_junk_targets_are_refused(self, humans, bad):
        _mgr, human = humans
        with pytest.raises(ValueError):
            sov.issue_grant(human.id, [ACTION], target=bad)

    def test_a_real_target_is_accepted(self, humans):
        """Positive control: the normal path is not broken by the guard."""
        _mgr, human = humans
        grant = sov.issue_grant(human.id, [ACTION], target=TARGET_A)
        assert grant.target == TARGET_A
        assert grant.is_wildcard is False


# --------------------------------------------------------------------------- #
# 2. Wildcard needs explicit secondary confirmation and is visible on-chain
# --------------------------------------------------------------------------- #
class TestWildcardRequiresConfirmation:
    def test_wildcard_without_confirmation_is_refused(self, humans):
        _mgr, human = humans
        with pytest.raises(ValueError) as exc:
            sov.issue_grant(human.id, [ACTION], target=WILDCARD_TARGET)
        assert "confirm" in str(exc.value).lower() or "wildcard" in str(exc.value).lower()

    def test_wildcard_with_confirmation_is_accepted(self, humans):
        _mgr, human = humans
        grant = sov.issue_grant(
            human.id, [ACTION], target=WILDCARD_TARGET, allow_wildcard=True
        )
        assert grant.is_wildcard is True

    def test_wildcard_issuance_is_a_visible_fact_on_the_chain(self, humans):
        """'This is an all-targets grant' must not be inferred from a blank field."""
        _mgr, human = humans
        grant = sov.issue_grant(
            human.id, [ACTION], target=WILDCARD_TARGET, allow_wildcard=True
        )
        match = [
            e for e in _events("granted")
            if (e.get("details") or {}).get("grant_id") == grant.grant_id
        ]
        assert match, "wildcard issuance wrote no audited 'granted' event"
        details = match[0]["details"]
        assert details.get("target") == WILDCARD_TARGET
        assert details.get("is_wildcard") is True

    def test_concrete_grant_is_recorded_as_non_wildcard(self, humans):
        _mgr, human = humans
        grant = sov.issue_grant(human.id, [ACTION], target=TARGET_A)
        match = [
            e for e in _events("granted")
            if (e.get("details") or {}).get("grant_id") == grant.grant_id
        ]
        assert match and match[0]["details"].get("is_wildcard") is False


# --------------------------------------------------------------------------- #
# 3 & 4. Cross-target use is refused AND audited, as a denial not a fault
# --------------------------------------------------------------------------- #
class TestCrossTargetUseIsRefused:
    def test_same_target_opens_the_window(self, humans):
        """Positive control -- the fix must not block legitimate use."""
        _mgr, human = humans
        grant = sov.issue_grant(human.id, [ACTION], target=TARGET_A)
        with sov.grant_window(grant, target=TARGET_A):
            active = sov.get_active_sovereignty()
            assert active is not None
            assert active.target == TARGET_A, "the window must carry the target"
            assert ACTION in active.actions

    def test_cross_target_is_refused(self, humans):
        _mgr, human = humans
        grant = sov.issue_grant(human.id, [ACTION], target=TARGET_A)
        with pytest.raises(SovereigntyTargetMismatchError):
            sov.grant_window(grant, target=TARGET_B)

    def test_the_attempt_itself_is_audited(self, humans):
        """Negative control: enumeration probing must leave evidence."""
        _mgr, human = humans
        grant = sov.issue_grant(human.id, [ACTION], target=TARGET_A)
        with pytest.raises(SovereigntyTargetMismatchError):
            sov.grant_window(grant, target=TARGET_B)

        match = [
            e for e in _events("denied")
            if (e.get("details") or {}).get("grant_id") == grant.grant_id
        ]
        assert match, "a cross-target attempt wrote no audited denial"
        details = match[0]["details"]
        assert details.get("grant_target") == TARGET_A
        assert details.get("requested_target") == TARGET_B

    def test_no_denial_is_written_for_a_legitimate_use(self, humans):
        """Guard against the denial being written unconditionally."""
        _mgr, human = humans
        grant = sov.issue_grant(human.id, [ACTION], target=TARGET_A)
        with sov.grant_window(grant, target=TARGET_A):
            pass
        assert not [
            e for e in _events("denied")
            if (e.get("details") or {}).get("grant_id") == grant.grant_id
        ]

    def test_refusal_reads_as_denied_not_a_fault(self, humans):
        """A refusal must be catchable as a denial, so no handler turns it into
        a 500 and hides 'not authorised' behind 'the service is broken'."""
        _mgr, human = humans
        grant = sov.issue_grant(human.id, [ACTION], target=TARGET_A)
        with pytest.raises(SovereigntyDeniedError) as exc:
            sov.grant_window(grant, target=TARGET_B)
        assert isinstance(exc.value, PermissionError)
        assert not isinstance(exc.value, ValueError), (
            "a 400-style ValueError would read as 'bad request', not 'denied'"
        )

    def test_use_side_never_accepts_a_wildcard_target(self, humans):
        """You act on a concrete resource; a use must name it."""
        _mgr, human = humans
        grant = sov.issue_grant(
            human.id, [ACTION], target=WILDCARD_TARGET, allow_wildcard=True
        )
        with pytest.raises(ValueError):
            sov.grant_window(grant, target=WILDCARD_TARGET)

    def test_wildcard_grant_covers_any_concrete_target(self, humans):
        _mgr, human = humans
        grant = sov.issue_grant(
            human.id, [ACTION], target=WILDCARD_TARGET, allow_wildcard=True
        )
        with sov.grant_window(grant, target=TARGET_B):
            assert sov.get_active_sovereignty().target == TARGET_B


# --------------------------------------------------------------------------- #
# 5. Revoke-fidelity: no rebuild may silently widen scope
# --------------------------------------------------------------------------- #
class TestRevokeFidelityPreservesTarget:
    def test_revoke_grant_preserves_target(self, humans):
        _mgr, human = humans
        grant = sov.issue_grant(human.id, [ACTION], target=TARGET_A)
        revoked = sov.revoke_grant(grant.grant_id, reason="done")
        assert revoked.target == TARGET_A
        assert revoked.covers_target(TARGET_B) is False, (
            "revocation widened the scope to all targets -- a rebuild dropped "
            "the target"
        )

    def test_revoke_grant_preserves_wildcard(self, humans):
        _mgr, human = humans
        grant = sov.issue_grant(
            human.id, [ACTION], target=WILDCARD_TARGET, allow_wildcard=True
        )
        revoked = sov.revoke_grant(grant.grant_id)
        assert revoked.is_wildcard is True

    def test_revoke_grants_for_principal_preserves_target(self, humans):
        _mgr, human = humans
        grant = sov.issue_grant(human.id, [ACTION], target=TARGET_A)
        assert sov.revoke_grants_for_principal(human.id, reason="suspended") == 1
        stored = sov.get_grant(grant.grant_id)
        assert stored.target == TARGET_A
        assert stored.covers_target(TARGET_B) is False

    def test_scope_is_not_widened_by_any_rebuild_path(self, humans):
        """The invariant behind the three cases above, stated once.

        Before any mutation the grant covers A and not B; it must still cover A
        and not B after revocation. Equality on the target is the precise
        statement; coverage is the property that actually matters.
        """
        _mgr, human = humans
        grant = sov.issue_grant(human.id, [ACTION], target=TARGET_A)
        assert grant.covers_target(TARGET_A) and not grant.covers_target(TARGET_B)

        sov.revoke_grants_for_principal(human.id)
        after = sov.get_grant(grant.grant_id)
        assert after.covers_target(TARGET_A) and not after.covers_target(TARGET_B)


# --------------------------------------------------------------------------- #
# 6. The API projection must expose the authorised scope
# --------------------------------------------------------------------------- #
class TestToDictExposesTarget:
    def test_to_dict_carries_target_and_wildcard_flag(self, humans):
        _mgr, human = humans
        payload = sov.issue_grant(human.id, [ACTION], target=TARGET_A).to_dict()
        assert payload["target"] == TARGET_A
        assert payload["is_wildcard"] is False

    def test_to_dict_shows_wildcard_when_present(self, humans):
        _mgr, human = humans
        payload = sov.issue_grant(
            human.id, [ACTION], target=WILDCARD_TARGET, allow_wildcard=True
        ).to_dict()
        assert payload["target"] == WILDCARD_TARGET
        assert payload["is_wildcard"] is True
