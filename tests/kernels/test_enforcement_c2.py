"""Policy C-2 — enforcement gate for ``@kernel_action`` (HIGH/CRITICAL only).

C-2 adds the *mechanism* (``enforce`` flag + :class:`PolicyDeniedError` +
fail-closed) but defaults to ``enforce=False`` on every production call site,
so the kernel layer stays additive (record-only) until a specific action is
deliberately flipped. These tests prove the gate behaves correctly and that
the default-off production path is untouched.
"""

from __future__ import annotations

import pytest

from src.kernels._crosscutting import PolicyDeniedError, kernel_action
from src.kernels._risk_classification import RiskTier


def _audit_details(action_name: str):
    from src.kernels.audit import audit_query

    for ev in audit_query(principal_id="kernel", limit=200, reverse=True):
        det = ev.get("details") or {}
        if det.get("action") == action_name:
            return det
    return None


class TestDefaultOffIsAdditive:
    """Regression: with enforce left at its default, nothing is blocked."""

    @kernel_action("identity.grant_permission")  # HIGH, denied for service
    def high_denied_record_only(self):
        return "ran"

    @kernel_action("memory.store")  # LOW, allowed for service
    def low_allowed_record_only(self):
        return "ran"

    def test_high_denied_still_executes_when_not_enforced(self):
        # The verdict is "deny" but without enforce the call must succeed.
        assert self.high_denied_record_only() == "ran"

    def test_low_allowed_executes(self):
        assert self.low_allowed_record_only() == "ran"

    def test_audit_marks_not_enforced(self):
        self.high_denied_record_only()
        det = _audit_details("identity.grant_permission")
        assert det is not None
        assert det.get("policy_enforced") is False


class TestEnforcedHighDenyBlocks:
    """The real C-2 cut line: HIGH/CRITICAL + deny => PolicyDeniedError."""

    @kernel_action("identity.grant_permission", enforce=True)  # HIGH, service=deny
    def high_enforced(self):
        return "ran-should-not-happen"

    def test_raises_policy_denied(self):
        with pytest.raises(PolicyDeniedError) as exc:
            self.high_enforced()
        err = exc.value
        assert err.action == "identity.grant_permission"
        assert err.verdict == "deny"
        # Verdict traced to the default_deny rule that fired for the service.
        assert err.rule_id == "default_deny"

    def test_is_a_permission_error(self):
        with pytest.raises(PermissionError):
            self.high_enforced()

    def test_blocked_audit_is_enforced(self):
        with pytest.raises(PolicyDeniedError):
            self.high_enforced()
        det = _audit_details("identity.grant_permission")
        assert det is not None
        assert det.get("policy_decision") == "deny"
        assert det.get("policy_enforced") is True
        assert det.get("action") == "identity.grant_permission"

    def test_wrapped_body_never_runs(self):
        called = []

        @kernel_action("capability.retire", enforce=True)  # CRITICAL, service=deny
        def critical_enforced():
            called.append(True)
            return "ran"

        with pytest.raises(PolicyDeniedError):
            critical_enforced()
        assert called == [], "被拦截时装饰的函数体不得执行"


class TestEnforcedAllowDoesNotBlock:
    """Enforcement only blocks a non-allow verdict; allow must pass through."""

    @kernel_action("memory.store", enforce=True)  # LOW, service=allow
    def low_enforced_allow(self):
        return "ran"

    def test_low_allow_not_blocked(self):
        assert self.low_enforced_allow() == "ran"

    def test_simulated_allow_high_not_blocked(self, monkeypatch):
        # If adjudication returns allow for a HIGH action, enforce must NOT fire.
        import src.kernels._crosscutting as xc

        monkeypatch.setattr(xc, "_adjudicate", lambda a, r: ("allow", "sim"))

        @kernel_action("identity.grant_permission", enforce=True)
        def high_simulated_allow():
            return "ran"

        assert high_simulated_allow() == "ran"


class TestFailClosed:
    """Under enforce, an unavailable adjudication must block (never fail open)."""

    def test_adjudication_failure_blocks(self, monkeypatch):
        import src.kernels._crosscutting as xc

        monkeypatch.setattr(xc, "_adjudicate", lambda a, r: (None, None))

        @kernel_action("security.set_abac_rule", enforce=True)  # CRITICAL
        def critical_enforced():
            return "ran"

        with pytest.raises(PolicyDeniedError) as exc:
            critical_enforced()
        assert exc.value.verdict == "error"
        assert exc.value.rule_id is None

    def test_adjudication_failure_not_enforced_is_safe(self, monkeypatch):
        import src.kernels._crosscutting as xc

        monkeypatch.setattr(xc, "_adjudicate", lambda a, r: (None, None))

        @kernel_action("security.set_abac_rule")  # enforce defaults False
        def critical_record_only():
            return "ran"

        assert critical_record_only() == "ran"


class TestGateOnlyEscalatesEnforcedTiers:
    """LOW/MEDIUM are never enforcement-gated, even with enforce=True."""

    @kernel_action("context.set_scope", enforce=True)  # MEDIUM, service=deny
    def medium_enforced_denied(self):
        return "ran-medium"

    def test_medium_denied_not_blocked_under_enforce(self):
        assert self.medium_enforced_denied() == "ran-medium"

    def test_tier_enum_cut_line(self):
        from src.kernels._risk_classification import ENFORCED_TIERS, is_enforced_tier

        assert RiskTier.HIGH in ENFORCED_TIERS
        assert RiskTier.CRITICAL in ENFORCED_TIERS
        assert RiskTier.LOW not in ENFORCED_TIERS
        assert RiskTier.MEDIUM not in ENFORCED_TIERS
        assert is_enforced_tier(RiskTier.CRITICAL) is True
        assert is_enforced_tier(RiskTier.MEDIUM) is False
