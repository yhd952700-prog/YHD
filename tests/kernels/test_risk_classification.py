"""D8 — kernel action risk classification.

The 43 ``@kernel_action``-decorated kernel actions are now classified in a
single authoritative registry (``src.kernels._risk_classification``) instead
of each call site silently defaulting to the dead ``"LOW"`` risk_level.

Every claim here is backed by a runtime assertion, and the registry is
cross-checked against TWO independent sources so it cannot drift:

* the AST scan of ``@kernel_action("...")`` decorations in ``src/kernels``
  (same guard style as the C-1 allow-list completeness test); and
* ``INTERNAL_SERVICE_ALLOWED_ACTIONS`` -- the LOW tier must equal the
  pre-approved internal-service set (C-1), proving the two classifications
  agree rather than coincidentally matching.
"""

from __future__ import annotations

from src.kernels._risk_classification import (
    ALL_CLASSIFIED_ACTIONS,
    ENFORCED_TIERS,
    ActionRisk,
    KERNEL_ACTION_RISK,
    RiskTier,
    discover_kernel_action_names,
    get_action_risk,
    get_kernel_action_risk,
    is_enforced_tier,
)
from src.kernels._crosscutting import kernel_action
from src.kernels.policy import INTERNAL_SERVICE_ALLOWED_ACTIONS


def _details_for(action_name: str):
    from src.kernels.audit import audit_query

    for ev in audit_query(principal_id="kernel", limit=2000, reverse=True):
        det = ev.get("details") or {}
        if det.get("action") == action_name:
            return det
    return None


# --------------------------------------------------------------------------- #
# 1. Completeness: registry covers exactly the decorated kernel actions
# --------------------------------------------------------------------------- #


class TestCompleteness:
    def test_registry_matches_decorated_actions(self):
        decorated = discover_kernel_action_names()
        classified = set(KERNEL_ACTION_RISK)
        assert decorated, "AST 未扫描到任何 @kernel_action，测试本身失效"
        unclassified = sorted(decorated - classified)
        stale = sorted(classified - decorated)
        assert not unclassified, f"未分类的内核动作：{unclassified}"
        assert not stale, f"注册表中有已不存在的动作：{stale}"

    def test_total_is_43(self):
        assert len(KERNEL_ACTION_RISK) == 43


# --------------------------------------------------------------------------- #
# 2. Tier validity + distribution
# --------------------------------------------------------------------------- #


class TestTiers:
    def test_every_tier_is_a_valid_enum(self):
        for name, rec in KERNEL_ACTION_RISK.items():
            assert isinstance(rec, ActionRisk)
            assert isinstance(rec.tier, RiskTier), name
            assert rec.tier in RiskTier

    def test_distribution(self):
        counts = {t: 0 for t in RiskTier}
        for rec in KERNEL_ACTION_RISK.values():
            counts[rec.tier] += 1
        assert counts == {
            RiskTier.LOW: 14,
            RiskTier.MEDIUM: 12,
            RiskTier.HIGH: 15,
            RiskTier.CRITICAL: 2,
        }, counts

    def test_low_tier_equals_internal_service_allow_list(self):
        """D8 的 LOW 分级必须与 C-1 的内部服务白名单完全一致（强交叉校验）。"""
        low = {a for a, rec in KERNEL_ACTION_RISK.items() if rec.tier is RiskTier.LOW}
        assert low == set(INTERNAL_SERVICE_ALLOWED_ACTIONS), (
            sorted(low ^ set(INTERNAL_SERVICE_ALLOWED_ACTIONS))
        )

    def test_high_and_critical_are_mutually_exclusive_from_low_medium(self):
        enforced = {a for a, rec in KERNEL_ACTION_RISK.items() if is_enforced_tier(rec.tier)}
        low_medium = {
            a for a, rec in KERNEL_ACTION_RISK.items()
            if rec.tier in (RiskTier.LOW, RiskTier.MEDIUM)
        }
        assert not (enforced & low_medium)


# --------------------------------------------------------------------------- #
# 3. Enforcement cut line (C-2 prerequisite)
# --------------------------------------------------------------------------- #


class TestEnforcementCutLine:
    def test_enforced_tiers_are_high_and_critical(self):
        assert ENFORCED_TIERS == frozenset({RiskTier.HIGH, RiskTier.CRITICAL})

    def test_critical_actions_are_the_two_system_wide_ones(self):
        critical = {a for a, rec in KERNEL_ACTION_RISK.items() if rec.tier is RiskTier.CRITICAL}
        assert critical == {"capability.retire", "security.set_abac_rule"}, critical

    def test_is_enforced_tier_helper(self):
        assert is_enforced_tier(RiskTier.HIGH)
        assert is_enforced_tier(RiskTier.CRITICAL)
        assert not is_enforced_tier(RiskTier.LOW)
        assert not is_enforced_tier(RiskTier.MEDIUM)


# --------------------------------------------------------------------------- #
# 4. get_kernel_action_risk behavior
# --------------------------------------------------------------------------- #


class TestLookup:
    @classmethod
    def setup_class(cls):
        # Pull a couple of known tiers straight from the registry.
        cls._low = next(a for a, r in KERNEL_ACTION_RISK.items() if r.tier is RiskTier.LOW)
        cls._critical = next(
            a for a, r in KERNEL_ACTION_RISK.items() if r.tier is RiskTier.CRITICAL
        )

    def test_known_low_returns_low(self):
        assert get_kernel_action_risk(self._low) == "LOW"

    def test_known_critical_returns_critical(self):
        assert get_kernel_action_risk(self._critical) == "CRITICAL"

    def test_unknown_action_falls_back_to_low(self):
        assert get_kernel_action_risk("does.not.exist") == "LOW"

    def test_get_action_risk_full_record(self):
        rec = get_action_risk(self._critical)
        assert rec is not None and rec.tier is RiskTier.CRITICAL
        assert get_action_risk("does.not.exist") is None


# --------------------------------------------------------------------------- #
# 5. End-to-end: the decorator now feeds the real tier into the audit trail
# --------------------------------------------------------------------------- #


class _RiskProbe:
    # Dummy bodies (no side effects) -- we only assert what the decorator
    # *records* (policy decision + the now-real risk_level). Invoking these
    # does NOT touch the real capability/security kernels.
    @kernel_action("security.set_abac_rule")
    def critical_dummy(self):
        return "ran"

    @kernel_action("memory.store")
    def low_dummy(self):
        return "ran"


class TestDecoratorFeedsRealRisk:
    def test_critical_action_records_critical_risk(self):
        probe = _RiskProbe()
        assert probe.critical_dummy() == "ran"
        det = _details_for("security.set_abac_rule")
        assert det is not None, "装饰器未写入审计事件"
        assert det.get("risk_level") == "CRITICAL", det
        # Verdict unchanged: CRITICAL action is not in the allow-list -> deny.
        assert det.get("policy_decision") == "deny"
        assert det.get("policy_enforced") is False

    def test_low_action_records_low_risk(self):
        probe = _RiskProbe()
        assert probe.low_dummy() == "ran"
        det = _details_for("memory.store")
        assert det is not None
        assert det.get("risk_level") == "LOW", det
        assert det.get("policy_decision") == "allow"
        assert det.get("policy_enforced") is False
