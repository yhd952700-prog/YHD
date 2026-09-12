"""Security Kernel unit tests.

Covers: RBAC role-based checks, ABAC condition evaluation, combined
access decisions, audit trail filtering, and statistics.

Defect-evidence tests (names prefixed with ``test_defect_``) assert the
behavior REQUIRED BY SPEC (Definition Lock section 112: RBAC+ABAC with
complete audit trail and human sovereignty override). They are expected
to fail until the kernel is fixed; each failure is a recorded defect.
"""
import pytest

from src.kernels.security import (
    ABATCondition,
    AccessDecision,
    AuditLogEntry,
    RBACRole,
    SecurityEngine,
    get_security_engine,
)
from src._time import utc_now


@pytest.fixture
def engine() -> SecurityEngine:
    """Fresh engine per test for full isolation."""
    return SecurityEngine()


# =====================================================================
# Seeded rules
# =====================================================================

class TestSeededRules:
    def test_engine_seeds_twelve_rbac_rules(self, engine):
        stats = engine.stats()
        assert stats["rbac_rules_total"] == 12
        assert stats["rbac_rules_enabled"] == 12

    def test_engine_starts_with_no_abac_rules(self, engine):
        stats = engine.stats()
        assert stats["abac_rules_total"] == 0
        assert stats["abac_rules_enabled"] == 0

    def test_unique_permissions_counts_both_rule_sets(self, engine):
        # 12 RBAC + 0 ABAC
        assert engine.stats()["unique_permissions"] == 12
        engine.set_abac_rule("custom:perm", "dept", ABATCondition.EQUALS, "eng")
        assert engine.stats()["unique_permissions"] == 13


# =====================================================================
# RBAC
# =====================================================================

class TestRBAC:
    def test_allows_principal_with_matching_role(self, engine):
        engine.grant_rbac_role("p1", RBACRole.VIEWER)
        assert engine.check_rbac("p1", "context:read") is AccessDecision.ALLOW

    def test_denies_principal_without_role(self, engine):
        assert engine.check_rbac("p1", "context:read") is AccessDecision.DENY

    def test_denies_principal_with_wrong_role(self, engine):
        # context:read requires VIEWER; OPERATOR alone must not pass
        engine.grant_rbac_role("p1", RBACRole.OPERATOR)
        assert engine.check_rbac("p1", "context:read") is AccessDecision.DENY

    def test_denies_unknown_permission(self, engine):
        engine.grant_rbac_role("p1", RBACRole.ADMIN)
        assert engine.check_rbac("p1", "no:such:permission") is AccessDecision.DENY

    def test_admin_role_covers_admin_permissions(self, engine):
        engine.grant_rbac_role("boss", RBACRole.ADMIN)
        for perm in ("context:write", "capability:manage", "execution:trigger",
                     "policy:manage", "audit:log"):
            assert engine.check_rbac("boss", perm) is AccessDecision.ALLOW, perm

    def test_auditor_role_can_query_but_not_log(self, engine):
        engine.grant_rbac_role("aud", RBACRole.AUDITOR)
        assert engine.check_rbac("aud", "audit:query") is AccessDecision.ALLOW
        assert engine.check_rbac("aud", "audit:log") is AccessDecision.DENY

    def test_set_principal_roles_replaces_roles(self, engine):
        engine.set_principal_roles("p1", {RBACRole.VIEWER})
        assert engine.check_rbac("p1", "context:read") is AccessDecision.ALLOW
        engine.set_principal_roles("p1", {RBACRole.OPERATOR})
        assert engine.check_rbac("p1", "context:read") is AccessDecision.DENY

    def test_grant_and_revoke_roundtrip(self, engine):
        assert engine.grant_rbac_role("p1", RBACRole.ADMIN) is True
        assert engine.check_rbac("p1", "context:write") is AccessDecision.ALLOW
        assert engine.revoke_rbac_role("p1", RBACRole.ADMIN) is True
        assert engine.check_rbac("p1", "context:write") is AccessDecision.DENY
        # revoking a role the principal does not hold returns False
        assert engine.revoke_rbac_role("p1", RBACRole.ADMIN) is False

    def test_multiple_roles_accumulate(self, engine):
        engine.grant_rbac_role("p1", RBACRole.VIEWER)
        engine.grant_rbac_role("p1", RBACRole.OPERATOR)
        assert engine.check_rbac("p1", "context:read") is AccessDecision.ALLOW
        assert engine.check_rbac("p1", "execution:plan") is AccessDecision.ALLOW

    def test_check_writes_audit_entries_for_allow_and_deny(self, engine):
        engine.grant_rbac_role("p1", RBACRole.VIEWER)
        engine.check_rbac("p1", "context:read", scope="L2")
        engine.check_rbac("p2", "context:read", scope="L2")
        trail = engine.audit_trail(operation="rbac_check")
        results = {e.principal_id: e.result for e in trail}
        assert results["p1"] == "allowed"
        assert results["p2"] == "denied"
        # scope must be recorded in the audit entry
        assert all(e.scope == "L2" for e in trail)

    def test_grant_and_revoke_are_audited(self, engine):
        engine.grant_rbac_role("p1", RBACRole.ADMIN, reason="onboarding")
        engine.revoke_rbac_role("p1", RBACRole.ADMIN, reason="offboarding")
        grants = engine.audit_trail(principal_id="p1", operation="grant_role")
        assert len(grants) == 1
        assert grants[0].reason == "onboarding"
        revokes = engine.audit_trail(principal_id="p1", operation="revoke_role")
        assert len(revokes) == 1
        assert revokes[0].reason == "offboarding"


# =====================================================================
# ABAC
# =====================================================================

class TestABAC:
    def test_eq_allows_on_match(self, engine):
        engine.set_abac_rule("doc:read", "department", ABATCondition.EQUALS, "eng")
        engine.set_principal_attributes("p1", {"department": "eng"})
        assert engine.check_abac("p1", "doc:read") is AccessDecision.ALLOW

    def test_eq_denies_on_mismatch(self, engine):
        engine.set_abac_rule("doc:read", "department", ABATCondition.EQUALS, "eng")
        engine.set_principal_attributes("p1", {"department": "sales"})
        assert engine.check_abac("p1", "doc:read") is AccessDecision.DENY

    def test_ne_operator(self, engine):
        engine.set_abac_rule("doc:read", "department", ABATCondition.NOT_EQUALS, "sales")
        engine.set_principal_attributes("p1", {"department": "eng"})
        assert engine.check_abac("p1", "doc:read") is AccessDecision.ALLOW
        engine.set_principal_attributes("p2", {"department": "sales"})
        assert engine.check_abac("p2", "doc:read") is AccessDecision.DENY

    def test_gt_numeric(self, engine):
        engine.set_abac_rule("doc:read", "trust_score", ABATCondition.GREATER_THAN, 0.5)
        engine.set_principal_attributes("p1", {"trust_score": 0.9})
        assert engine.check_abac("p1", "doc:read") is AccessDecision.ALLOW
        engine.set_principal_attributes("p2", {"trust_score": 0.5})
        assert engine.check_abac("p2", "doc:read") is AccessDecision.DENY

    def test_lt_numeric(self, engine):
        engine.set_abac_rule("doc:read", "risk", ABATCondition.LESS_THAN, 10)
        engine.set_principal_attributes("p1", {"risk": 5})
        assert engine.check_abac("p1", "doc:read") is AccessDecision.ALLOW
        engine.set_principal_attributes("p2", {"risk": 10})
        assert engine.check_abac("p2", "doc:read") is AccessDecision.DENY

    def test_gt_string_fallback(self, engine):
        engine.set_abac_rule("doc:read", "tier", ABATCondition.GREATER_THAN, "B")
        engine.set_principal_attributes("p1", {"tier": "C"})
        assert engine.check_abac("p1", "doc:read") is AccessDecision.ALLOW

    def test_in_set_with_list(self, engine):
        engine.set_abac_rule("doc:read", "department",
                             ABATCondition.IN_SET, ["eng", "ops"])
        engine.set_principal_attributes("p1", {"department": "ops"})
        assert engine.check_abac("p1", "doc:read") is AccessDecision.ALLOW
        engine.set_principal_attributes("p2", {"department": "sales"})
        assert engine.check_abac("p2", "doc:read") is AccessDecision.DENY

    def test_in_set_with_scalar_falls_back_to_equality(self, engine):
        engine.set_abac_rule("doc:read", "department", ABATCondition.IN_SET, "eng")
        engine.set_principal_attributes("p1", {"department": "eng"})
        assert engine.check_abac("p1", "doc:read") is AccessDecision.ALLOW

    def test_not_in_set(self, engine):
        engine.set_abac_rule("doc:read", "department",
                             ABATCondition.NOT_IN_SET, ["blocked", "revoked"])
        engine.set_principal_attributes("p1", {"department": "eng"})
        assert engine.check_abac("p1", "doc:read") is AccessDecision.ALLOW
        engine.set_principal_attributes("p2", {"department": "blocked"})
        assert engine.check_abac("p2", "doc:read") is AccessDecision.DENY

    def test_inline_attributes_do_not_escalate_stored_attributes(self, engine):
        """Request-supplied attributes must not escalate stored ones.

        Revised in batch 2 per the S3 design ruling (see
        tests/kernels/security/test_defect_design_gaps.py): the original
        version of this test asserted the escalation as acceptable
        behavior; the ruling makes it a defect. Expected: a stored
        trust_score of 0.1 denies even when the request supplies 0.9.
        """
        engine.set_abac_rule("doc:read", "trust_score", ABATCondition.GREATER_THAN, 0.5)
        engine.set_principal_attributes("p1", {"trust_score": 0.1})
        # request-supplied high score must NOT override the stored low one
        decision = engine.check_abac("p1", "doc:read", attributes={"trust_score": 0.9})
        assert decision is AccessDecision.DENY, (
            "inline attribute escalated stored trust_score (design gap S3)"
        )
        # without inline attributes the stored score fails as well
        assert engine.check_abac("p1", "doc:read") is AccessDecision.DENY

    def test_disabled_abac_rule_never_allows(self, engine):
        engine.set_abac_rule("doc:read", "department", ABATCondition.EQUALS, "eng",
                             enabled=False)
        engine.set_principal_attributes("p1", {"department": "eng"})
        assert engine.check_abac("p1", "doc:read") is AccessDecision.DENY

    def test_no_abac_rule_denies_fail_closed(self, engine):
        engine.set_principal_attributes("p1", {"department": "eng"})
        assert engine.check_abac("p1", "doc:read") is AccessDecision.DENY


# =====================================================================
# Combined access decision
# =====================================================================

class TestCheckAccess:
    @pytest.fixture
    def combined_engine(self, engine):
        engine.set_abac_rule("context:read", "trust_score",
                             ABATCondition.GREATER_THAN, 0.5)
        return engine

    def test_rbac_and_abac_both_pass(self, combined_engine):
        combined_engine.grant_rbac_role("p1", RBACRole.VIEWER)
        combined_engine.set_principal_attributes("p1", {"trust_score": 0.9})
        decision = combined_engine.check_access("p1", "context:read")
        assert decision is AccessDecision.ALLOW

    def test_rbac_pass_abac_fail_is_conditional(self, combined_engine):
        combined_engine.grant_rbac_role("p1", RBACRole.VIEWER)
        combined_engine.set_principal_attributes("p1", {"trust_score": 0.1})
        decision = combined_engine.check_access("p1", "context:read")
        assert decision is AccessDecision.CONDITIONAL

    def test_rbac_fail_abac_pass_is_defer(self, combined_engine):
        combined_engine.set_principal_attributes("p1", {"trust_score": 0.9})
        decision = combined_engine.check_access("p1", "context:read")
        assert decision is AccessDecision.DEFER

    def test_both_fail_is_deny(self, combined_engine):
        combined_engine.set_principal_attributes("p1", {"trust_score": 0.1})
        decision = combined_engine.check_access("p1", "context:read")
        assert decision is AccessDecision.DENY


# =====================================================================
# Audit trail
# =====================================================================

class TestAuditTrail:
    def test_filter_by_principal(self, engine):
        engine.grant_rbac_role("p1", RBACRole.VIEWER)
        engine.check_rbac("p1", "context:read")
        engine.check_rbac("p2", "context:read")
        trail = engine.audit_trail(principal_id="p1")
        assert trail, "expected entries for p1"
        assert all(e.principal_id == "p1" for e in trail)

    def test_filter_by_operation(self, engine):
        engine.grant_rbac_role("p1", RBACRole.VIEWER)
        engine.check_rbac("p1", "context:read")
        trail = engine.audit_trail(operation="grant_role")
        assert len(trail) == 1
        assert trail[0].operation == "grant_role"

    def test_filter_by_since(self, engine):
        from datetime import timedelta
        engine.grant_rbac_role("p1", RBACRole.VIEWER)
        engine.check_rbac("p1", "context:read")
        # far-future cutoff excludes everything
        future = utc_now() + timedelta(hours=1)
        assert engine.audit_trail(since=future) == []
        # past cutoff includes everything
        past = utc_now() - timedelta(hours=1)
        assert len(engine.audit_trail(since=past)) >= 2

    def test_trail_sorted_newest_first(self, engine):
        engine.grant_rbac_role("p1", RBACRole.VIEWER)
        engine.check_rbac("p1", "context:read")
        engine.check_rbac("p1", "context:read")
        trail = engine.audit_trail()
        timestamps = [e.timestamp for e in trail]
        assert timestamps == sorted(timestamps, reverse=True)

    def test_entries_have_unique_ids_and_correlation_ids(self, engine):
        engine.grant_rbac_role("p1", RBACRole.VIEWER)
        engine.check_rbac("p1", "context:read")
        trail = engine.audit_trail()
        ids = [e.id for e in trail]
        corr = [e.correlation_id for e in trail]
        assert len(set(ids)) == len(ids)
        assert all(c for c in corr)


# =====================================================================
# Global singleton
# =====================================================================

class TestGlobalEngine:
    def test_get_security_engine_returns_same_instance(self):
        assert get_security_engine() is get_security_engine()


# =====================================================================
# Defect-evidence tests (expected to FAIL until kernel fixed)
# Spec basis: Definition Lock section 112.
# =====================================================================

class TestDefects:
    def test_defect_check_rbac_accepts_rule_permission_field(self, engine):
        """DEFECT SEC-1: rule keying inconsistency.

        Every seeded RBACRule carries a public ``permission`` field
        (e.g. "context_read"), but check_rbac() only accepts the dict
        key form (e.g. "context:read"). A principal granted exactly the
        rule's role is denied when passing the rule's own permission
        value. Expected: the rule's permission field is a valid input.
        """
        failures = []
        for rule in engine._rbac_rules.values():
            engine.set_principal_roles("p1", {rule.role})
            decision = engine.check_rbac("p1", rule.permission)
            if decision is not AccessDecision.ALLOW:
                failures.append(rule.permission)
        assert failures == [], f"permissions rejected despite matching role: {failures}"

    def test_defect_pure_rbac_allow_is_full_allow(self, engine):
        """DEFECT SEC-5: no ABAC rule is treated as a failed constraint.

        With zero ABAC rules configured (the default state), a principal
        whose RBAC check passes can never receive a full ALLOW; it always
        degrades to CONDITIONAL. Expected: without any ABAC constraint
        the decision is ALLOW.
        """
        engine.grant_rbac_role("p1", RBACRole.VIEWER)
        result = engine.decide_access("p1", "context:read")
        assert result["decision"] == "allow", (
            f"expected 'allow' for pure RBAC pass, got {result['decision']!r}"
        )

    def test_defect_decide_access_returns_reasoning(self, engine):
        """DEFECT SEC-2: decide_access promises reasoning but returns none.

        The result dict documents "rbac_check", "abac_check" and "reason"
        fields, but they are never populated (always None / empty string).
        """
        engine.grant_rbac_role("p1", RBACRole.VIEWER)
        result = engine.decide_access("p1", "context:read")
        assert result["reason"] != "", "decision reason is empty"
        assert result["rbac_check"] is not None, "rbac_check never populated"

    def test_defect_human_override_changes_decision(self, engine):
        """DEFECT SEC-3: human_override parameter is accepted but ignored.

        Definition Lock section 112 requires human sovereignty override
        capability. A human override on a denied decision must not stay
        a plain DENY.
        """
        result = engine.decide_access(
            "human-1", "context:write", human_override=True
        )
        assert result["decision"] != "deny", (
            "human_override=True did not change the denied decision"
        )

    def test_defect_stats_counts_allow_and_deny_results(self, engine):
        """DEFECT SEC-4: stats().by_result always counts zero.

        RBAC check audit entries record result strings "allowed"/"denied"
        but by_result only counts "allow"/"deny", so no check is ever
        counted. Expected: performed checks appear in by_result.
        """
        engine.grant_rbac_role("p1", RBACRole.VIEWER)
        engine.check_rbac("p1", "context:read")   # allowed
        engine.check_rbac("p2", "context:read")   # denied
        stats = engine.stats()
        assert stats["by_result"]["allow"] >= 1, "allowed check not counted"
        assert stats["by_result"]["deny"] >= 1, "denied check not counted"

    def test_defect_gt_condition_with_missing_attribute_denies(self, engine):
        """DEFECT SEC-7: GREATER_THAN with a missing attribute allows.

        _eval_condition falls back to string comparison when float()
        fails; str(None) == "None" > "0.5" evaluates True, so a principal
        WITHOUT the required attribute satisfies a > 0.5 condition.
        Expected: a missing attribute must not satisfy a comparison
        condition (deny).
        """
        engine.set_abac_rule("doc:read", "trust_score",
                             ABATCondition.GREATER_THAN, 0.5)
        engine.set_principal_attributes("p1", {})  # no trust_score at all
        decision = engine.check_abac("p1", "doc:read")
        assert decision is AccessDecision.DENY, (
            "principal without the attribute passed a > comparison"
        )
