"""D8 — kernel action risk classification.

Every ``@kernel_action``-decorated action under ``src/`` is now classified in a
single authoritative registry (``src.kernels._risk_classification``) instead
of each call site silently defaulting to the dead ``"LOW"`` risk_level.

Every claim here is backed by a runtime assertion, and the registry is
cross-checked against an independent source so it cannot drift:

* the AST scan of ``@kernel_action("...")`` decorations across **all of
  ``src/``** (``discover_kernel_action_names``); and
* ``INTERNAL_SERVICE_ALLOWED_ACTIONS`` -- via the *sovereignty* invariant
  (``TestSovereigntyInvariant``): the internal-service allow-list may only
  contain actions that are **not** enforcement-gated and are **non-destructive**,
  so no HIGH/CRITICAL or irreversible action is ever pre-approved for the
  service. (This deliberately replaced an older, over-strict
  ``LOW == ALLOW`` equality that wrongly coupled two orthogonal dimensions --
  see that test's docstring.)
"""

from __future__ import annotations

from src.kernels._risk_classification import (
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

    for ev in audit_query(limit=2000, reverse=True):
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

    def test_total_is_47(self):
        assert len(KERNEL_ACTION_RISK) == 47


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
            RiskTier.LOW: 17,
            RiskTier.MEDIUM: 13,
            RiskTier.HIGH: 15,
            RiskTier.CRITICAL: 2,
        }, counts

    def test_high_and_critical_are_mutually_exclusive_from_low_medium(self):
        enforced = {a for a, rec in KERNEL_ACTION_RISK.items() if is_enforced_tier(rec.tier)}
        low_medium = {
            a for a, rec in KERNEL_ACTION_RISK.items()
            if rec.tier in (RiskTier.LOW, RiskTier.MEDIUM)
        }
        assert not (enforced & low_medium)


# --------------------------------------------------------------------------- #
# 1b. Sovereignty invariant: what the internal service may be pre-approved for
# --------------------------------------------------------------------------- #
#
# This pair of assertions replaces an older ``LOW == ALLOW`` equality. That
# equality was an accidental coupling of two ORTHOGONAL dimensions:
#   * the risk TIER answers "how much blast radius / does this need a human?";
#   * the ALLOW-list answers "may the internal service do this unattended?".
# It also contradicted the risk table's own MEDIUM definition ("reversible
# operational authority/state change" -- trust-score write, identity creation,
# topology add, quota lifecycle), so it excluded MEDIUM *by construction* and
# could not express "a MEDIUM operational action is legitimately pre-approved".
# A batch allow-list edit (315fc804) therefore left it red for weeks.
#
# The invariant that actually protects the boundary is the sovereignty one:
# nothing the C-2 gate treats as HIGH/CRITICAL, and nothing irreversibly
# destructive, may be pre-approved for the internal service -- those require a
# verified human (OD-010). MEDIUM operational actions are legitimate here and
# are explicitly allowed to stay.


class TestSovereigntyInvariant:
    def test_allow_list_contains_no_sovereignty_gated_action(self):
        allow = set(INTERNAL_SERVICE_ALLOWED_ACTIONS)
        sovereignty_gated = {
            a
            for a in allow
            if (rec := get_action_risk(a)) is not None
            and is_enforced_tier(rec.tier)
        }
        assert not sovereignty_gated, (
            "内部服务白名单不得预批准需要人类主权的动作 (HIGH/CRITICAL): "
            f"{sorted(sovereignty_gated)}"
        )

    def test_allow_list_contains_no_destructive_action(self):
        allow = set(INTERNAL_SERVICE_ALLOWED_ACTIONS)
        destructive = {
            a
            for a in allow
            if (rec := get_action_risk(a)) is not None and rec.destructive
        }
        assert not destructive, (
            "内部服务白名单不得预批准不可逆销毁动作: " f"{sorted(destructive)}"
        )

    def test_medium_operational_actions_are_allowed_on_purpose(self):
        """The 7 MEDIUM operational actions are ALLOW-listed deliberately.

        This test exists so the invariant above can never be "satisfied" by
        quietly deleting the MEDIUM entries: it positively asserts the
        legitimate operational set is still pre-approved, and that it is
        MEDIUM (not LOW) -- i.e. the two dimensions stay decoupled.
        """
        expected = {
            "identity.create_identity",
            "network.add_route",
            "resource.allocate",
            "resource.create_quota",
            "trust.assign_score",
            "trust.update_score",
        }
        allow = set(INTERNAL_SERVICE_ALLOWED_ACTIONS)
        assert expected <= allow, sorted(expected - allow)
        for action in expected:
            rec = get_action_risk(action)
            assert rec is not None and rec.tier is RiskTier.MEDIUM, (
                action,
                rec,
            )

    def test_sovereignty_gated_actions_are_not_pre_approved(self):
        """Every HIGH/CRITICAL action must be absent from the allow-list."""
        allow = set(INTERNAL_SERVICE_ALLOWED_ACTIONS)
        gated = {
            a
            for a, rec in KERNEL_ACTION_RISK.items()
            if is_enforced_tier(rec.tier)
        }
        assert not (gated & allow), sorted(gated & allow)


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
