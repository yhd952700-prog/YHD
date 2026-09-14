"""Policy Kernel — DENY-rule observability tests (P0-3b).

Why these tests exist
---------------------
A built-in DENY rule whose compared operand is *absent* from the context
evaluates to ``False`` and therefore does not apply. Before this change the
decision carried no trace of that, so two very different situations were
indistinguishable:

* "quota was checked, and the action is within budget", and
* "quota was never checked, because nobody supplied the numbers".

That is a silent failure mode for a *security* control: a control that does not
run looks exactly like a control that ran and passed.

What is asserted here
---------------------
1. **Zero behaviour change.** Every decision is exactly what it was before.
   The existing ``tests/kernels/policy/test_policy.py`` (109 tests) is the
   broad proof; the targeted cases are repeated here for locality.
2. **The gap is now observable** via ``PolicyDecision.unapplied_deny_rules``
   and ``PolicyDecision.unresolved_operands``.
3. **Anti-rubber-stamp.** The pre-fix rule shape is reconstructed verbatim and
   asserted to produce *no* signal -- proving the signal is new, not incidental.
4. **The rule is now reachable and fed from real sources.** Its scope was
   lowered to L1 to match the gate, and ``liuhao.chat`` supplies a cost and a
   budget sourced from the billing engine and the resource kernel (see
   ``tests/kernels/policy/test_quota_enforcement.py``). The tests in the last
   class here were *inverted* when that happened -- they used to assert the
   rule was unreachable.

Scope note: this module asserts the gap is *visible*. Whether the rule applies
on a given call still depends on the caller supplying real inputs; an omission
is reported through the decision, never papered over with a zero.
"""
from __future__ import annotations

import ast
import pathlib
import re

import pytest

from src.kernels.policy import (
    PolicyAction,
    PolicyCondition,
    PolicyEngine,
    PolicyEffect,
    PolicyOperator,
    PolicyRule,
    PolicyScope,
)

# Built-in rule ids this change touches.
QUOTA = "quota_enforcement"
SCOPE = "scope_enforcement"


@pytest.fixture
def engine() -> PolicyEngine:
    """A fresh engine with the built-in rules registered."""
    return PolicyEngine()


def _agent(scope="L7", capabilities=("x",)):
    return {"type": "agent", "scope": scope, "capabilities": list(capabilities)}


def _decide(engine, action, resource, scope=None):
    """Evaluate through the same shape a real caller uses."""
    return engine.evaluate(
        {"agent": _agent(), "action": action, "resource": resource or {}},
        scope=scope,
    )


# =====================================================================
# 1. missing_operands — the read-only primitive
# =====================================================================


class TestMissingOperands:
    def test_reports_both_operands_when_context_is_empty(self):
        cond = PolicyCondition("resource.available", PolicyOperator.LT, "$action.estimated_cost")
        assert cond.missing_operands({}) == ["resource.available", "action.estimated_cost"]

    def test_reports_nothing_when_all_operands_present(self):
        cond = PolicyCondition("resource.available", PolicyOperator.LT, "$action.estimated_cost")
        ctx = {"resource": {"available": 5}, "action": {"estimated_cost": 1}}
        assert cond.missing_operands(ctx) == []

    def test_reports_only_the_absent_operand(self):
        cond = PolicyCondition("resource.available", PolicyOperator.LT, "$action.estimated_cost")
        assert cond.missing_operands({"resource": {"available": 5}, "action": {}}) == [
            "action.estimated_cost"
        ]
        assert cond.missing_operands({"resource": {}, "action": {"estimated_cost": 1}}) == [
            "resource.available"
        ]

    def test_exists_is_a_presence_assertion_and_is_not_reported(self):
        """An absent attribute is the *normal* input to EXISTS, not a gap."""
        for op in (PolicyOperator.EXISTS, PolicyOperator.NOT_EXISTS):
            cond = PolicyCondition("action.required_capability", op, True)
            assert cond.missing_operands({}) == []

    def test_nested_path_through_a_non_dict_is_reported(self):
        cond = PolicyCondition("resource.available", PolicyOperator.LT, 1)
        assert cond.missing_operands({"resource": 7}) == ["resource.available"]

    def test_it_never_raises_on_a_hostile_context(self):
        cond = PolicyCondition("a.b.c", PolicyOperator.LT, "$x.y")
        assert cond.missing_operands({"a": None, "x": 1}) == ["a.b.c", "x.y"]

    def test_it_does_not_change_evaluate(self):
        """The primitive is read-only: evaluate() must be unaffected."""
        cond = PolicyCondition("resource.available", PolicyOperator.LT, 10)
        for ctx in ({}, {"resource": {"available": 5}}, {"resource": {"available": 50}}):
            # evaluating must be pure and repeatable
            assert cond.evaluate(ctx) == cond.evaluate(ctx)
            cond.missing_operands(ctx)


# =====================================================================
# 2. Zero behaviour change
# =====================================================================


class TestDecisionsAreUnchanged:
    def test_over_budget_is_denied_by_quota(self, engine):
        d = _decide(engine, {"name": "a", "estimated_cost": 10}, {"available": 1})
        assert d.decision == PolicyEffect.DENY
        assert [r.id for r in d.denied_rules] == [QUOTA]

    def test_within_budget_is_not_denied_by_quota(self, engine):
        d = _decide(engine, {"name": "a", "estimated_cost": 10}, {"available": 100})
        assert QUOTA not in [r.id for r in d.denied_rules]

    def test_missing_available_is_not_denied_by_quota(self, engine):
        d = _decide(engine, {"name": "a", "estimated_cost": 10}, {})
        assert QUOTA not in [r.id for r in d.denied_rules]

    def test_missing_cost_is_not_denied_by_quota(self, engine):
        d = _decide(engine, {"name": "a"}, {"available": 1})
        assert QUOTA not in [r.id for r in d.denied_rules]

    def test_the_six_builtin_rules_are_still_registered(self, engine):
        ids = {r.id for r in engine.list_rules(enabled_only=False)}
        assert {
            "human_sovereignty",
            "internal_service_allow",
            "capability_required",
            QUOTA,
            SCOPE,
            "default_deny",
        } <= ids


# =====================================================================
# 3. The gap is now observable
# =====================================================================


class TestUnappliedDenyRules:
    def test_quota_is_listed_as_unapplied_when_available_is_absent(self, engine):
        d = _decide(engine, {"name": "a", "estimated_cost": 10}, {})
        assert QUOTA in [r.id for r in d.unapplied_deny_rules]
        assert "resource.available" in d.unresolved_operands

    def test_quota_is_listed_as_unapplied_when_cost_is_absent(self, engine):
        d = _decide(engine, {"name": "a"}, {"available": 1})
        assert QUOTA in [r.id for r in d.unapplied_deny_rules]
        assert "action.estimated_cost" in d.unresolved_operands

    def test_quota_is_not_listed_when_it_actually_applied(self, engine):
        """A rule that ran must never be reported as having not run."""
        d = _decide(engine, {"name": "a", "estimated_cost": 10}, {"available": 1})
        assert QUOTA not in [r.id for r in d.unapplied_deny_rules]
        assert "resource.available" not in d.unresolved_operands
        assert "action.estimated_cost" not in d.unresolved_operands

    def test_scope_rule_gets_the_same_treatment(self, engine):
        d = engine.evaluate({"agent": {}, "action": {"name": "a"}, "resource": {}}, scope=None)
        assert SCOPE in [r.id for r in d.unapplied_deny_rules]
        assert "agent.scope" in d.unresolved_operands
        assert "action.required_scope" in d.unresolved_operands

    def test_unresolved_operands_are_deduplicated(self, engine):
        d = _decide(engine, {"name": "a"}, {})
        assert len(d.unresolved_operands) == len(set(d.unresolved_operands))

    def test_the_two_ambiguous_cases_are_now_distinguishable(self, engine):
        """This is the whole point of the change.

        Same decision, different explanation. Before, callers could see
        ``denied_rules`` only, and both of these looked identical.
        """
        checked = _decide(engine, {"name": "a", "estimated_cost": 10}, {"available": 100})
        never_checked = _decide(engine, {"name": "a", "estimated_cost": 10}, {})

        assert checked.decision == never_checked.decision
        assert QUOTA not in [r.id for r in checked.unapplied_deny_rules]
        assert QUOTA in [r.id for r in never_checked.unapplied_deny_rules]

    def test_observability_reaches_the_real_entry_point(self, engine):
        """evaluate_simple is what both production callers use."""
        d = engine.evaluate_simple(
            actor={"type": "agent", "scope": "L7", "capabilities": ["x"]},
            action={"name": "a", "estimated_cost": 10},
            resource={},
            scope=None,
        )
        assert QUOTA in [r.id for r in d.unapplied_deny_rules]
        assert "resource.available" in d.unresolved_operands


# =====================================================================
# 4. The rules now declare their input requirements
# =====================================================================


class TestRulesDeclareTheirInputs:
    def _operators(self, engine, rule_id):
        rule = engine.get_rule(rule_id)
        assert rule is not None
        return [c.operator for c in rule.conditions]

    def test_quota_declares_both_inputs(self, engine):
        ops = self._operators(engine, QUOTA)
        assert ops.count(PolicyOperator.EXISTS) == 2
        assert PolicyOperator.LT in ops

    def test_scope_declares_both_inputs(self, engine):
        ops = self._operators(engine, SCOPE)
        assert ops.count(PolicyOperator.EXISTS) == 2
        assert PolicyOperator.LT in ops

    def test_declaring_the_inputs_did_not_change_the_verdict(self, engine):
        """EXISTS gate + comparison ≡ comparison alone, for every input shape."""
        for available, cost in [(1, 10), (100, 10), (None, 10), (1, None), (None, None)]:
            resource = {} if available is None else {"available": available}
            action = {"name": "a"}
            if cost is not None:
                action["estimated_cost"] = cost
            d = _decide(engine, action, resource)
            expected_deny = available is not None and cost is not None and available < cost
            assert (QUOTA in [r.id for r in d.denied_rules]) is expected_deny


# =====================================================================
# 5. Anti-rubber-stamp: prove the signal is new
# =====================================================================


class TestTheGapWasReal:
    def test_prefix_rule_shape_produces_no_signal_at_all(self):
        """Reconstruct the pre-fix quota rule verbatim: LT only, no EXISTS gate.

        It must (a) fail to match when an operand is missing, and (b) leave the
        decision with no way to say so -- which is exactly the silent behaviour
        this change removes.
        """
        old_rule = PolicyRule(
            id="prefix_quota",
            name="prefix",
            description="pre-fix shape",
            conditions=[
                PolicyCondition("resource.available", PolicyOperator.LT, "$action.estimated_cost"),
            ],
            action=PolicyAction.DENY,
            scope=PolicyScope.L2,
            precedence=150,
        )
        engine = PolicyEngine()
        engine.register_rule(old_rule)

        ctx = {"agent": _agent(), "action": {"name": "a", "estimated_cost": 10}, "resource": {}}
        assert old_rule.matches(ctx) is False  # silent skip
        d = engine.evaluate(ctx, scope=None)
        assert "prefix_quota" not in [r.id for r in d.denied_rules]  # no verdict
        # ...and the old shape has no field capable of reporting the skip.
        assert not hasattr(old_rule, "unresolved_operands")

    def test_fixed_rule_reports_what_the_prefix_shape_hid(self, engine):
        """Same input, new shape: the skip is now named."""
        d = _decide(engine, {"name": "a", "estimated_cost": 10}, {})
        assert QUOTA in [r.id for r in d.unapplied_deny_rules]
        assert "resource.available" in d.unresolved_operands

    def test_a_rule_with_no_comparison_operand_is_not_noise(self, engine):
        """EXISTS-only rules must not be reported -- they have no absent input."""
        rule = PolicyRule(
            id="exists_only",
            name="exists_only",
            description="presence assertion only",
            conditions=[PolicyCondition("action.thing", PolicyOperator.EXISTS, True)],
            action=PolicyAction.DENY,
            scope=PolicyScope.L0,
            precedence=5,
        )
        engine.register_rule(rule)
        d = engine.evaluate({"action": {"name": "a"}}, scope=None)
        assert "exists_only" not in [r.id for r in d.unapplied_deny_rules]


# =====================================================================
# 6. Backwards compatibility of the decision object
# =====================================================================


class TestDecisionCompatibility:
    def test_new_fields_default_to_empty(self):
        from src.kernels.policy import PolicyDecision

        d = PolicyDecision(
            decision=PolicyEffect.ALLOW,
            matched_rules=[],
            denied_rules=[],
            abstained_rules=[],
            traceability=[],
            context={},
        )
        assert d.unapplied_deny_rules == []
        assert d.unresolved_operands == []
        assert d.is_allowed is True

    def test_every_decision_path_populates_the_new_fields(self, engine):
        """All four return sites must carry the fields, not just some."""
        decisions = [
            _decide(engine, {"name": "a", "estimated_cost": 10}, {"available": 1}),   # DENY in loop
            engine.evaluate(                                                          # ALLOW
                {"actor": {"type": "human", "verified": True},
                 "action": {"name": "a", "risk_level": "HIGH"}, "resource": {}},
                scope=None,
            ),
            engine.evaluate({"action": {}}, scope=None),                              # NOT_APPLICABLE
        ]
        for d in decisions:
            assert isinstance(d.unapplied_deny_rules, list)
            assert isinstance(d.unresolved_operands, list)


# =====================================================================
# 7. Documented finding: quota cannot fire from production call sites
# =====================================================================


class TestQuotaIsNowReachable:
    """The scope filter used to drop this rule on the only path that could
    apply it. These tests pin the *fixed* state -- this class previously
    asserted the opposite, so a silent regression to unreachability is what
    they are here to catch.
    """

    def test_authorize_applies_the_quota_rule_when_over_budget(self):
        from src.ai.agent_factory import AgentPolicy

        policy = AgentPolicy(principal="probe", scope="L7", capabilities=["x"])
        d = policy.authorize("some.action", resource={"available": 1}, estimated_cost=10)
        # Over budget: the rule is in the candidate set and decides.
        assert QUOTA in [r.id for r in d.denied_rules]
        assert d.is_denied
        assert d.decision == PolicyEffect.DENY

    def test_authorize_leaves_a_within_budget_action_alone(self):
        from src.ai.agent_factory import AgentPolicy

        policy = AgentPolicy(principal="probe", scope="L7", capabilities=["x"])
        d = policy.authorize("some.action", resource={"available": 100}, estimated_cost=10)
        assert QUOTA not in [r.id for r in d.denied_rules]
        assert not d.is_denied

    def test_the_authorization_gate_supplies_economy_inputs(self):
        """AST tripwire: the gate must hand the rule real cost/budget inputs.

        The previous version of this test asserted that *nobody* supplied a
        cost. That stopped being true once the chat path was wired up -- and it
        was a poor tripwire anyway, because ``liuhao.chat`` splats a mapping,
        so a scan for a literal ``estimated_cost=`` keyword would have kept
        passing even if the wiring were deleted. Assert the wiring structurally
        instead: the gate splats a helper, and that helper calls the real
        sourcing function.
        """
        root = pathlib.Path(__file__).resolve().parents[3]
        tree = ast.parse((root / "src" / "ai" / "liuhao.py").read_text(encoding="utf-8"))

        splats = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            callee = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
            if callee != "authorize":
                continue
            for keyword in node.keywords:
                # arg is None => ``**mapping``
                if keyword.arg is None and isinstance(keyword.value, ast.Call):
                    splats.append(
                        getattr(keyword.value.func, "attr", None)
                        or getattr(keyword.value.func, "id", None)
                    )

        assert splats, (
            "neither chat() nor chat_stream() splats economy inputs into "
            "authorize(); the quota rule would be fed nothing"
        )
        assert set(splats) == {"_quota_authorize_kwargs"}, splats

        helper = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "_quota_authorize_kwargs"
        )
        called = {
            getattr(c.func, "attr", None) or getattr(c.func, "id", None)
            for c in ast.walk(helper)
            if isinstance(c, ast.Call)
        }
        assert "economy_inputs" in called, called

    def test_only_the_sourcing_module_hands_a_cost_towards_the_engine(self):
        """Containment: exactly one module bridges a cost into the engine.

        ``economy.py`` builds cost figures; the cost that reaches the policy
        engine must come through the module that sources it from real data
        (``agent_factory.economy_inputs``), not be invented ad hoc by some
        other caller.
        """
        root = pathlib.Path(__file__).resolve().parents[3]
        policy_kernel = root / "src" / "kernels" / "policy" / "__init__.py"
        engine_call = re.compile(r"evaluate_simple\(|evaluate_policy\(")

        offenders = []
        for path in (root / "src").rglob("*.py"):
            if "__pycache__" in path.parts or path == policy_kernel:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if "estimated_cost" in text and engine_call.search(text):
                offenders.append(path.relative_to(root).as_posix())

        # agent_factory is the bridge: it sources the cost from the billing
        # engine's price table and forwards it to AgentPolicy.authorize (which
        # calls the engine). A second module here would be an unreviewed path
        # into the quota rule.
        assert offenders == ["src/ai/agent_factory.py"], (
            "the set of modules that both mention estimated_cost and call the "
            f"policy engine changed -- check who feeds the quota rule: {offenders}"
        )
