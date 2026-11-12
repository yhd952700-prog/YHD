"""Policy C-2 — enforcement gate for ``@kernel_action`` (HIGH/CRITICAL only).

C-2 adds the *mechanism* (``enforce`` flag + :class:`PolicyDeniedError` +
fail-closed) but defaults to ``enforce=False`` on every production call site,
so the kernel layer stays additive (record-only) until a specific action is
deliberately flipped. These tests prove the gate behaves correctly and that
the default-off production path is untouched.
"""

from __future__ import annotations

import pytest

from src.kernels._crosscutting import PolicyDeniedError, PolicyDeferredError, kernel_action
from src.kernels._risk_classification import RiskTier


def _audit_details(action_name: str):
    """Newest audit event written for ``action_name`` -- attribution verified.

    Does **not** filter by ``principal_id``: before A2 the decorator recorded the
    literal subject ``"kernel"``, so a test could find its own row by that
    string. A2 replaced the constant with the *actual* acting principal, so
    pinning the literal finds either nothing or a stale row left in the shared
    ``audit_store.db`` by an earlier run -- a green that proves nothing.

    Attribution is asserted as a **value** (F26): ``actor_fingerprint`` must be
    32 hex chars, and the event's ``principal_id`` must agree with the
    ``actor_identity_id`` in ``details`` (the A2 keyspace convergence).
    """
    from src.kernels.audit import audit_query

    for ev in audit_query(limit=200, reverse=True):
        det = ev.get("details") or {}
        if det.get("action") != action_name:
            continue
        fp = det.get("actor_fingerprint")
        assert isinstance(fp, str) and len(fp) == 32 and all(
            c in "0123456789abcdef" for c in fp
        ), (
            f"audit row for {action_name!r} carries no usable attribution: "
            f"actor_fingerprint={fp!r} (F26: a non-empty column is not evidence)"
        )
        assert ev.get("principal_id") == det.get("actor_identity_id"), (
            f"audit principal keyspace divergence for {action_name!r}: "
            f"principal_id={ev.get('principal_id')!r} vs "
            f"actor_identity_id={det.get('actor_identity_id')!r}"
        )
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
    """The real C-2/C-3 cut line: HIGH/CRITICAL + deny => PolicyDeferredError
    (deferred pending human sovereignty, Policy C-3)."""

    @kernel_action("identity.grant_permission", enforce=True)  # HIGH, service=deny
    def high_enforced(self):
        return "ran-should-not-happen"

    def test_raises_policy_deferred(self):
        with pytest.raises(PolicyDeferredError) as exc:
            self.high_enforced()
        err = exc.value
        # A defer IS a PolicyDeniedError (and PermissionError) -- existing
        # guards keep catching it.
        assert isinstance(err, PolicyDeniedError)
        assert err.action == "identity.grant_permission"
        # C-3 reclassifies the enforced HIGH/CRITICAL block as "defer"
        # (pending human sovereignty) rather than a permanent "deny".
        assert err.verdict == "defer"
        # Verdict still traced to the rule that denied it for the service.
        assert err.rule_id == "default_deny"

    def test_is_a_permission_error(self):
        with pytest.raises(PermissionError):
            self.high_enforced()

    def test_blocked_audit_is_enforced(self):
        with pytest.raises(PolicyDeferredError):
            self.high_enforced()
        det = _audit_details("identity.grant_permission")
        assert det is not None
        # Audit records the engine's verdict ("deny") plus the enforcement flag
        # (so "was this actually blocked?" is separately auditable).
        assert det.get("policy_decision") == "deny"
        assert det.get("policy_enforced") is True
        assert det.get("action") == "identity.grant_permission"

    def test_wrapped_body_never_runs(self):
        called = []

        @kernel_action("capability.retire", enforce=True)  # CRITICAL, service=deny
        def critical_enforced():
            called.append(True)
            return "ran"

        with pytest.raises(PolicyDeferredError):
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

        monkeypatch.setattr(
            xc, "_adjudicate",
            lambda a, r: ("allow", "sim", xc._service_actor_policy_shape()),
        )

        @kernel_action("identity.grant_permission", enforce=True)
        def high_simulated_allow():
            return "ran"

        assert high_simulated_allow() == "ran"


class TestFailClosed:
    """Under enforce, an unavailable adjudication must block (never fail open)."""

    def test_adjudication_failure_blocks(self, monkeypatch):
        import src.kernels._crosscutting as xc

        monkeypatch.setattr(
            xc, "_adjudicate",
            lambda a, r: (None, None, xc._service_actor_policy_shape()),
        )

        @kernel_action("security.set_abac_rule", enforce=True)  # CRITICAL
        def critical_enforced():
            return "ran"

        with pytest.raises(PolicyDeniedError) as exc:
            critical_enforced()
        assert exc.value.verdict == "error"
        assert exc.value.rule_id is None

    def test_adjudication_failure_not_enforced_is_safe(self, monkeypatch):
        import src.kernels._crosscutting as xc

        monkeypatch.setattr(
            xc, "_adjudicate",
            lambda a, r: (None, None, xc._service_actor_policy_shape()),
        )

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


class TestCriticalBlockVsRecord:
    """D24: CRITICAL actions are deferred/blocked when enforced, recorded when not."""

    @kernel_action("capability.retire", enforce=True)  # CRITICAL, service=deny
    def critical_enforced(self):
        return "ran-should-not"

    def test_enforced_critical_blocks_pending_human(self):
        with pytest.raises(PolicyDeferredError) as exc:
            self.critical_enforced()
        assert exc.value.action == "capability.retire"
        assert exc.value.verdict == "defer"

    def test_record_only_critical_executes(self):
        @kernel_action("capability.retire")  # enforce defaults False
        def critical_record_only():
            return "ran"

        # Library default (no enforce + env unset): additive record-only, the
        # body runs and nothing is blocked even though the verdict is deny.
        assert critical_record_only() == "ran"
