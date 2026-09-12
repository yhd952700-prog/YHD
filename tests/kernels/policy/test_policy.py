"""Policy Kernel unit tests.

Covers: PolicyCondition operators, rule registration/precedence,
policy evaluation (ALLOW/DENY/ABSTAIN), scope filtering, policy sets,
and the built-in policies (human sovereignty, default deny).

Defect-evidence tests (names prefixed with ``test_defect_``) assert the
behavior REQUIRED BY SPEC (Definition Lock section 112: ABAC policy
engine with scope enforcement L0-L7). They are expected to fail until
the kernel is fixed; each failure is a recorded defect.
"""
import pytest

from src.kernels.policy import (
    PolicyAction,
    PolicyCondition,
    PolicyEngine,
    PolicyEffect,
    PolicyOperator,
    PolicyRule,
    PolicyScope,
    get_policy_engine,
)


def make_rule(rule_id, action, conditions, precedence=0, scope=PolicyScope.L0,
              enabled=True):
    return PolicyRule(
        id=rule_id,
        name=rule_id,
        description=rule_id,
        conditions=conditions,
        action=action,
        scope=scope,
        precedence=precedence,
        enabled=enabled,
    )


@pytest.fixture
def engine() -> PolicyEngine:
    return PolicyEngine()


# =====================================================================
# Built-in policies
# =====================================================================

class TestBuiltins:
    def test_six_builtin_rules_registered(self, engine):
        stats = engine.stats()
        assert stats["total_rules"] == 6
        for rid in ("human_sovereignty", "internal_service_allow",
                    "default_deny", "scope_enforcement",
                    "capability_required", "quota_enforcement"):
            assert engine.get_rule(rid) is not None, rid

    def test_human_sovereignty_allows_human_high_risk(self, engine):
        # P10: a *verified* human (registered + ACTIVE identity) may
        # trigger the sovereignty override for high-risk actions.
        #
        # Since Policy C-7 "verified human" is a POSITIVE allowlist: the
        # identity must be registered through ``create_human_identity``,
        # which stamps ``metadata["kind"] == "human"``. Registering it with
        # a bare ``create_identity`` is no longer sufficient -- see the
        # companion test below.
        import uuid
        from src.kernels.identity import get_identity_manager
        ident = get_identity_manager().create_human_identity(
            principal=f"human:{uuid.uuid4().hex}",
            trust_score=1.0,
        )
        decision = engine.evaluate_simple(
            actor={"type": "human", "id": ident.id},
            action={"name": "deploy", "risk_level": "CRITICAL"},
        )
        assert decision.is_allowed
        assert decision.decision is PolicyEffect.ALLOW
        assert any(r.id == "human_sovereignty" for r in decision.matched_rules)

    def test_an_unmarked_identity_is_not_a_verified_human(self, engine):
        # Policy C-7. The previous test was ``kind != "service"``, a reverse
        # exclusion that fails OPEN: the built-in ``system`` identity has no
        # kind at all and therefore counted as a verified human.
        #
        # This is the regression test. If the verifier ever goes back to a
        # reverse exclusion, this fails.
        import uuid
        from src.kernels.identity import IdentityScope, create_identity
        ident = create_identity(
            principal=f"unmarked:{uuid.uuid4().hex}",
            scope=IdentityScope.L0,
            trust_score=1.0,
        )
        decision = engine.evaluate_simple(
            actor={"type": "human", "id": ident.id},
            action={"name": "deploy", "risk_level": "CRITICAL"},
        )
        assert not any(r.id == "human_sovereignty" for r in decision.matched_rules)
        assert decision.is_denied

    def test_the_builtin_system_identity_is_not_a_verified_human(self, engine):
        # The concrete C-7 finding: ``system`` is auto-created with
        # ``metadata={}``. It must never be able to hold human sovereignty,
        # or a machine ends up recorded as the approver of a CRITICAL action.
        decision = engine.evaluate_simple(
            actor={"type": "human", "id": "system"},
            action={"name": "capability.retire", "risk_level": "CRITICAL"},
        )
        assert not any(r.id == "human_sovereignty" for r in decision.matched_rules)
        assert decision.is_denied

    def test_human_sovereignty_does_not_match_agent(self, engine):
        decision = engine.evaluate_simple(
            actor={"type": "agent"},
            action={"name": "deploy", "risk_level": "CRITICAL"},
        )
        assert not any(r.id == "human_sovereignty" for r in decision.matched_rules)
        # falls through to default deny
        assert decision.is_denied

    def test_default_deny_denies_named_actions(self, engine):
        decision = engine.evaluate_simple(
            actor={"type": "agent"}, action={"name": "anything"}
        )
        assert decision.is_denied
        assert any(r.id == "default_deny" for r in decision.denied_rules)

    def test_action_without_name_is_not_applicable_at_low_scope(self, engine):
        # default_deny requires action.name to exist; without it nothing
        # matches at scope L0 (default_deny lives at L7).
        decision = engine.evaluate({"action": {}}, scope=PolicyScope.L0)
        assert decision.decision is PolicyEffect.NOT_APPLICABLE

    def test_action_without_name_is_denied_without_scope_filter(self, engine):
        # Once an action is present, default_deny applies regardless of
        # whether it carries a name (POL-5 fix: no fail-open for unnamed
        # actions). Scope filtering still governs reachability (see the
        # L0/L1 variants above).
        decision = engine.evaluate({"action": {"other": 1}})
        assert decision.is_denied
        assert any(r.id == "default_deny" for r in decision.denied_rules)


# =====================================================================
# PolicyCondition operators
# =====================================================================

class TestPolicyCondition:
    def test_eq(self):
        assert PolicyCondition("a", PolicyOperator.EQ, 1).evaluate({"a": 1})
        assert not PolicyCondition("a", PolicyOperator.EQ, 1).evaluate({"a": 2})

    def test_neq(self):
        assert PolicyCondition("a", PolicyOperator.NEQ, 1).evaluate({"a": 2})
        assert not PolicyCondition("a", PolicyOperator.NEQ, 1).evaluate({"a": 1})

    def test_gt_gte_lt_lte(self):
        assert PolicyCondition("a", PolicyOperator.GT, 5).evaluate({"a": 6})
        assert not PolicyCondition("a", PolicyOperator.GT, 5).evaluate({"a": 5})
        assert PolicyCondition("a", PolicyOperator.GTE, 5).evaluate({"a": 5})
        assert PolicyCondition("a", PolicyOperator.LT, 5).evaluate({"a": 4})
        assert not PolicyCondition("a", PolicyOperator.LT, 5).evaluate({"a": 5})
        assert PolicyCondition("a", PolicyOperator.LTE, 5).evaluate({"a": 5})

    def test_in_not_in(self):
        cond = PolicyCondition("a", PolicyOperator.IN, ["x", "y"])
        assert cond.evaluate({"a": "x"})
        assert not cond.evaluate({"a": "z"})
        cond = PolicyCondition("a", PolicyOperator.NOT_IN, ["x", "y"])
        assert cond.evaluate({"a": "z"})
        assert not cond.evaluate({"a": "x"})

    def test_in_with_non_collection_value_is_false(self):
        cond = PolicyCondition("a", PolicyOperator.IN, "x")
        assert not cond.evaluate({"a": "x"})

    def test_contains(self):
        cond = PolicyCondition("tags", PolicyOperator.CONTAINS, "core")
        assert cond.evaluate({"tags": ["core", "api"]})
        assert not cond.evaluate({"tags": ["web"]})
        assert not cond.evaluate({"tags": "not-a-container"})

    def test_regex(self):
        cond = PolicyCondition("name", PolicyOperator.REGEX, r"user-\d+")
        assert cond.evaluate({"name": "user-42"})
        assert not cond.evaluate({"name": "admin-1"})

    def test_exists_not_exists(self):
        assert PolicyCondition("a", PolicyOperator.EXISTS, True).evaluate({"a": 1})
        assert not PolicyCondition("a", PolicyOperator.EXISTS, True).evaluate({})
        assert PolicyCondition("a", PolicyOperator.NOT_EXISTS, True).evaluate({})
        assert not PolicyCondition("a", PolicyOperator.NOT_EXISTS, True).evaluate({"a": 1})

    def test_eq_with_missing_attribute_is_false(self):
        assert not PolicyCondition("missing", PolicyOperator.EQ, 1).evaluate({"a": 1})

    def test_negate_flips_result(self):
        cond = PolicyCondition("a", PolicyOperator.EQ, 1, negate=True)
        assert cond.evaluate({"a": 2})
        assert not cond.evaluate({"a": 1})

    def test_nested_dot_notation(self):
        cond = PolicyCondition("agent.profile.tier", PolicyOperator.EQ, "gold")
        assert cond.evaluate({"agent": {"profile": {"tier": "gold"}}})
        assert not cond.evaluate({"agent": {"profile": {"tier": "silver"}}})
        assert not cond.evaluate({"agent": {}})

    def test_nested_path_through_non_dict_returns_none(self):
        cond = PolicyCondition("agent.profile.tier", PolicyOperator.EXISTS, True)
        assert not cond.evaluate({"agent": "not-a-dict"})


# =====================================================================
# Rule registration and listing
# =====================================================================

class TestRuleManagement:
    def test_register_returns_true_for_new_rule(self, engine):
        rule = make_rule("r1", PolicyAction.ALLOW,
                         [PolicyCondition("a", PolicyOperator.EQ, 1)])
        assert engine.register_rule(rule) is True

    def test_register_duplicate_id_returns_false(self, engine):
        rule = make_rule("r1", PolicyAction.ALLOW,
                         [PolicyCondition("a", PolicyOperator.EQ, 1)])
        engine.register_rule(rule)
        assert engine.register_rule(rule) is False

    def test_unregister(self, engine):
        rule = make_rule("r1", PolicyAction.ALLOW, [])
        engine.register_rule(rule)
        assert engine.unregister_rule("r1") is True
        assert engine.unregister_rule("r1") is False
        assert engine.get_rule("r1") is None

    def test_list_rules_sorted_by_precedence_desc(self, engine):
        engine.register_rule(make_rule("low", PolicyAction.ALLOW, [], precedence=1))
        engine.register_rule(make_rule("high", PolicyAction.ALLOW, [], precedence=999))
        rules = engine.list_rules()
        precedences = [r.precedence for r in rules]
        assert precedences == sorted(precedences, reverse=True)
        # builtin human_sovereignty has precedence 1000 and stays on top
        assert rules[0].id == "human_sovereignty"
        ids = [r.id for r in rules]
        assert ids.index("high") < ids.index("low")

    def test_list_rules_enabled_only(self, engine):
        engine.register_rule(make_rule("off", PolicyAction.ALLOW, [], enabled=False))
        ids = [r.id for r in engine.list_rules(enabled_only=True)]
        assert "off" not in ids
        ids_all = [r.id for r in engine.list_rules(enabled_only=False)]
        assert "off" in ids_all

    def test_disabled_rule_never_matches(self):
        rule = make_rule("off", PolicyAction.ALLOW, [], enabled=False)
        assert rule.matches({"action": {"name": "x"}}) is False


# =====================================================================
# Evaluation semantics
# =====================================================================

class TestEvaluation:
    def test_matching_allow_rule_wins(self, engine):
        engine.register_rule(make_rule(
            "allow_ops", PolicyAction.ALLOW,
            [PolicyCondition("actor.type", PolicyOperator.EQ, "agent")],
            precedence=10,
        ))
        decision = engine.evaluate_simple(
            actor={"type": "agent"}, action={"name": "run"}
        )
        assert decision.is_allowed

    def test_higher_precedence_deny_beats_lower_allow(self, engine):
        engine.register_rule(make_rule(
            "allow_low", PolicyAction.ALLOW,
            [PolicyCondition("action.name", PolicyOperator.EQ, "run")],
            precedence=10,
        ))
        engine.register_rule(make_rule(
            "deny_high", PolicyAction.DENY,
            [PolicyCondition("action.name", PolicyOperator.EQ, "run")],
            precedence=20,
        ))
        decision = engine.evaluate_simple(
            actor={"type": "agent"}, action={"name": "run"}
        )
        assert decision.is_denied

    def test_higher_precedence_allow_beats_lower_deny(self, engine):
        engine.register_rule(make_rule(
            "deny_low", PolicyAction.DENY,
            [PolicyCondition("action.name", PolicyOperator.EQ, "run")],
            precedence=10,
        ))
        engine.register_rule(make_rule(
            "allow_high", PolicyAction.ALLOW,
            [PolicyCondition("action.name", PolicyOperator.EQ, "run")],
            precedence=20,
        ))
        decision = engine.evaluate_simple(
            actor={"type": "agent"}, action={"name": "run"}
        )
        assert decision.is_allowed

    def test_all_conditions_must_match(self, engine):
        engine.register_rule(make_rule(
            "strict", PolicyAction.ALLOW,
            [
                PolicyCondition("actor.type", PolicyOperator.EQ, "agent"),
                PolicyCondition("actor.tier", PolicyOperator.EQ, "gold"),
            ],
            precedence=10,
        ))
        good = engine.evaluate_simple(
            actor={"type": "agent", "tier": "gold"}, action={"name": "run"}
        )
        assert good.is_allowed
        bad = engine.evaluate_simple(
            actor={"type": "agent", "tier": "silver"}, action={"name": "run"}
        )
        assert bad.is_denied  # falls to default_deny

    def test_abstain_rule_does_not_decide(self, engine):
        engine.register_rule(make_rule(
            "no_opinion", PolicyAction.ABSTAIN,
            [PolicyCondition("actor.type", PolicyOperator.EQ, "agent")],
            precedence=50,
        ))
        # no action present, so default_deny does not fire
        decision = engine.evaluate({"actor": {"type": "agent"}})
        assert decision.decision is PolicyEffect.NOT_APPLICABLE
        assert any(r.id == "no_opinion" for r in decision.abstained_rules)

    def test_traceability_records_evaluated_rules(self, engine):
        decision = engine.evaluate_simple(
            actor={"type": "agent"}, action={"name": "run"}
        )
        assert "RULE:default_deny:deny" in decision.traceability

    def test_scope_filter_excludes_higher_scope_rules(self, engine):
        # default_deny is L7; evaluating at L0 excludes it
        decision = engine.evaluate({"action": {"name": "x"}}, scope=PolicyScope.L0)
        assert decision.decision is PolicyEffect.NOT_APPLICABLE
        # without scope filter default_deny applies
        decision = engine.evaluate({"action": {"name": "x"}})
        assert decision.is_denied

    def test_scope_filter_keeps_rules_at_or_below_scope(self, engine):
        engine.register_rule(make_rule(
            "l2_allow", PolicyAction.ALLOW,
            [PolicyCondition("action.name", PolicyOperator.EQ, "x")],
            precedence=10, scope=PolicyScope.L2,
        ))
        decision = engine.evaluate(
            {"action": {"name": "x"}}, scope=PolicyScope.L3
        )
        assert decision.is_allowed
        # at L1 the L2 rule is filtered out again
        decision = engine.evaluate(
            {"action": {"name": "x"}}, scope=PolicyScope.L1
        )
        assert decision.decision is PolicyEffect.NOT_APPLICABLE


# =====================================================================
# Policy sets
# =====================================================================

class TestPolicySets:
    def test_create_and_get_policy_set(self, engine):
        rule = make_rule("set_allow", PolicyAction.ALLOW,
                         [PolicyCondition("action.name", PolicyOperator.EQ, "x")])
        engine.register_rule(rule)
        ps = engine.create_policy_set(
            "ps1", "PS1", "test set", ["set_allow", "does_not_exist"]
        )
        assert ps.id == "ps1"
        assert [r.id for r in ps.rules] == ["set_allow"]  # unknown ids skipped
        assert engine.get_policy_set("ps1") is ps
        assert engine.get_policy_set("nope") is None

    def test_evaluate_with_policy_set_uses_only_set_rules(self, engine):
        rule = make_rule("set_allow", PolicyAction.ALLOW, [], precedence=0)
        engine.register_rule(rule)
        engine.create_policy_set("ps1", "PS1", "test set", ["set_allow"])
        # default_deny is NOT in the set, so an unnamed action is allowed
        decision = engine.evaluate({"action": {}}, policy_set_id="ps1")
        assert decision.is_allowed


# =====================================================================
# Stats / singleton
# =====================================================================

class TestStatsAndSingleton:
    def test_stats_shape(self, engine):
        stats = engine.stats()
        assert stats["total_rules"] == 6
        assert stats["enabled_rules"] == 6
        assert stats["policy_sets"] == 0
        # allow: human_sovereignty + internal_service_allow
        assert stats["by_action"]["allow"] == 2
        assert stats["by_action"]["deny"] == 4

    def test_get_policy_engine_returns_same_instance(self):
        assert get_policy_engine() is get_policy_engine()


# =====================================================================
# Defect-evidence tests (expected to FAIL until kernel fixed)
# Spec basis: Definition Lock section 112 (ABAC engine, capability
# requirement, scope enforcement L0-L7).
# =====================================================================

class TestDefects:
    def test_defect_capability_required_allows_capable_agent(self, engine):
        """DEFECT POL-1: capability_required denies agents that HAVE the capability.

        The builtin rule uses NOT_IN with the literal string
        "action.required_capability" instead of the resolved attribute
        value, so the condition is always True and every action with a
        required_capability is denied -- even when the agent's
        capability list contains it. Expected: a capable agent is not
        denied by this rule.
        """
        ctx = {
            "agent": {"id": "a1", "scope": "L1", "capabilities": ["cap_a"]},
            "action": {"name": "run_tool", "required_capability": "cap_a"},
        }
        decision = engine.evaluate(ctx)
        assert "RULE:capability_required:deny" not in decision.traceability, (
            "agent holding the required capability was denied by "
            "capability_required"
        )

    def test_defect_scope_enforcement_compares_against_required_scope(self, engine):
        """DEFECT POL-2: scope_enforcement compares against a literal string.

        The rule condition is agent.scope < "action.required_scope"
        (the literal string), not the resolved value. Since every Lx
        string sorts before "action...", ANY agent with a scope is
        denied. Expected: an agent whose scope satisfies the required
        scope is not denied by this rule.
        """
        ctx = {
            "agent": {"scope": "L1"},
            "action": {"name": "x", "required_scope": "L1"},
        }
        decision = engine.evaluate(ctx)
        assert "RULE:scope_enforcement:deny" not in decision.traceability, (
            "agent at exactly the required scope was denied by "
            "scope_enforcement"
        )

    def test_defect_evaluate_simple_enforces_agent_scope(self, engine):
        """DEFECT POL-3: builtin rules read "agent.*" but evaluate_simple
        supplies "actor.*".

        The scope/capability/quota builtin policies can never fire on
        the evaluate_simple path because the context keys do not match.
        Expected: an under-scoped agent (scope L0, action requires L7)
        triggers scope_enforcement.
        """
        decision = engine.evaluate_simple(
            actor={"type": "agent", "scope": "L0"},
            action={"name": "x", "required_scope": "L7"},
        )
        assert "RULE:scope_enforcement:deny" in decision.traceability, (
            "scope_enforcement never fires on the evaluate_simple path"
        )

    def test_defect_comparison_with_missing_attribute_is_false(self):
        """DEFECT POL-4: comparison operators crash on missing attributes.

        PolicyCondition.evaluate raises an unhandled TypeError when a
        GT/GTE/LT/LTE condition references a missing attribute
        (None > 5). Expected: a missing attribute simply does not
        satisfy the condition (evaluate returns False).
        """
        cond = PolicyCondition("missing_attr", PolicyOperator.GT, 5)
        assert cond.evaluate({}) is False

    def test_defect_default_deny_covers_unnamed_actions(self, engine):
        """DEFECT POL-5: default deny fails open for unnamed actions.

        The builtin rule is described as "Deny by default if no explicit
        allow", but its condition requires action.name to exist. An
        action without a name matches nothing and evaluates to
        NOT_APPLICABLE instead of DENY -- a fail-open gap. Expected: an
        unnamed action is denied by default.
        """
        decision = engine.evaluate({"action": {"other": 1}})
        assert decision.is_denied, (
            "action without a name escaped the default deny policy"
        )
