"""Policy C-3 — dynamic human-principal channel + DEFER (resolves C-2 self-lock).

These tests prove:
  * the sovereignty context manager scopes a delegation to specific actions;
  * WITHOUT a delegation, a HIGH/CRITICAL action is still denied (service actor);
  * WITH a delegation by a verified human, the action is adjudicated as that
    human and ALLOWED (the C-2 self-lock is resolved);
  * a spoofed principal (service identity masquerading as human) is rejected;
  * an enforced HIGH/CRITICAL action WITHOUT delegation raises
    PolicyDeferredError("defer"); WITH delegation it executes;
  * LOW actions are never escalated (their service allow is preserved).
"""

from __future__ import annotations

import pytest

from src.kernels._crosscutting import (
    PolicyDeferredError,
    PolicyDeniedError,
    _adjudicate,
    kernel_action,
)
from src.kernels._sovereignty import (
    ActiveSovereignty,
    clear_active_sovereignty,
    get_active_sovereignty,
    human_sovereign,
    set_active_sovereignty,
)
from src.kernels.identity import (
    INTERNAL_SERVICE_PRINCIPAL,
    IdentityManager,
    IdentityScope,
    IdentityStatus,
)


def _fresh_manager_with_human():
    """An isolated IdentityManager holding only a real ACTIVE human identity.

    We isolate from the process-wide singleton so this test cannot perturb
    other tests' identity counts. The engine re-verifies against whatever
    ``get_identity_manager`` returns, so patching it here is authoritative.
    """
    mgr = IdentityManager()
    human = mgr.create_identity(
        "human-c3-operator",
        scope=IdentityScope.L0,
        metadata={"kind": "human"},
    )
    assert human.status == IdentityStatus.ACTIVE
    return mgr, human


@pytest.fixture
def human_manager(monkeypatch):
    mgr, human = _fresh_manager_with_human()
    # The policy engine re-verifies the actor against the identity kernel via
    # this function. Patching it isolates the human identity to this test.
    monkeypatch.setattr("src.kernels.identity.get_identity_manager", lambda: mgr)
    return mgr, human


def _audit_details(action_name: str):
    from src.kernels.audit import audit_query

    for ev in audit_query(principal_id="kernel", limit=200, reverse=True):
        det = ev.get("details") or {}
        if det.get("action") == action_name:
            return det
    return None


class TestSovereigntyContext:
    def test_default_none(self):
        clear_active_sovereignty()
        assert get_active_sovereignty() is None

    def test_context_manager_sets_and_clears(self):
        assert get_active_sovereignty() is None
        with human_sovereign("h", ["capability.retire"]):
            active = get_active_sovereignty()
            assert active is not None
            assert active.principal == "h"
            assert "capability.retire" in active.actions
        assert get_active_sovereignty() is None

    def test_set_active_sovereignty(self):
        try:
            sov = ActiveSovereignty(
                principal="h", actions=frozenset(["x"]), granted_at=0.0
            )
            set_active_sovereignty(sov)
            assert get_active_sovereignty() is sov
        finally:
            clear_active_sovereignty()


class TestNoSovereigntyStillDenies:
    def test_high_critical_denied_without_delegation(self, human_manager):
        clear_active_sovereignty()
        verdict, rule = _adjudicate("capability.retire", "CRITICAL")
        assert verdict == "deny"
        assert rule == "default_deny"

    def test_enforced_high_raises_deferred_without_delegation(self, human_manager):
        clear_active_sovereignty()

        @kernel_action("capability.retire", enforce=True)
        def critical():
            return "ran"

        with pytest.raises(PolicyDeferredError) as exc:
            critical()
        assert exc.value.verdict == "defer"
        assert isinstance(exc.value, PolicyDeniedError)


class TestHumanSovereignFlipsAllow:
    def test_delegated_high_allowed(self, human_manager):
        mgr, human = human_manager
        with human_sovereign(human.id, ["capability.retire"]):
            verdict, rule = _adjudicate("capability.retire", "CRITICAL")
        assert verdict == "allow"
        assert rule == "human_sovereignty"

    def test_delegated_high_executes_when_enforced(self, human_manager):
        mgr, human = human_manager

        @kernel_action("capability.retire", enforce=True)
        def critical():
            return "ran-under-human"

        with human_sovereign(human.id, ["capability.retire"]):
            assert critical() == "ran-under-human"

    def test_out_of_scope_action_still_denied(self, human_manager):
        mgr, human = human_manager
        # Delegate ONLY capability.retire; security.set_abac_rule is out of scope.
        with human_sovereign(human.id, ["capability.retire"]):
            verdict, rule = _adjudicate("security.set_abac_rule", "CRITICAL")
        assert verdict == "deny"
        assert rule == "default_deny"

    def test_low_not_escalated_under_delegation(self, human_manager):
        mgr, human = human_manager
        # LOW stays on the service actor (allow-listed there); delegation must
        # NOT change its verdict.
        with human_sovereign(human.id, ["memory.store"]):
            verdict, rule = _adjudicate("memory.store", "LOW")
        assert verdict == "allow"
        assert rule == "internal_service_allow"


class TestSpoofRejected:
    def test_service_principal_as_human_rejected(self, human_manager):
        # Label the internal service principal as a "human" actor -- the kind
        # guard in _is_verified_human must reject it.
        with human_sovereign(INTERNAL_SERVICE_PRINCIPAL, ["capability.retire"]):
            verdict, rule = _adjudicate("capability.retire", "CRITICAL")
        assert verdict == "deny"
        assert rule == "default_deny"

    def test_unknown_principal_rejected(self, human_manager):
        with human_sovereign("does-not-exist", ["capability.retire"]):
            verdict, rule = _adjudicate("capability.retire", "CRITICAL")
        assert verdict == "deny"
        assert rule == "default_deny"


class TestAuditOfDefer:
    def test_deferred_audit_records_enforced(self, human_manager):
        clear_active_sovereignty()

        @kernel_action("capability.retire", enforce=True)
        def critical():
            return "ran"

        with pytest.raises(PolicyDeferredError):
            critical()
        det = _audit_details("capability.retire")
        assert det is not None
        # Engine verdict ("deny") is recorded, plus the enforcement flag so
        # "was this actually blocked?" is separately auditable.
        assert det.get("policy_decision") == "deny"
        assert det.get("policy_enforced") is True
