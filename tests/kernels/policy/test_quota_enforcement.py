"""``quota_enforcement`` is reachable, and fed only by real data (P0-3b, part 2).

Companion to ``test_policy_rule_observability.py``. That module makes the
"a DENY rule silently did not run" gap *visible*; this one proves the rule now
actually *runs* when -- and only when -- real inputs exist.

The three claims under test:

1. **Reachable.** ``AgentPolicy.authorize`` evaluates at L1 and the engine keeps
   only rules at or below the requested scope. The rule now declares L1, so it
   reaches the decision instead of being filtered away.
2. **Fed from real sources, never invented.** The cost comes from the billing
   engine's price table (priced per model by the model registry); the budget
   comes from the resource kernel's ``COST`` quota. When either is genuinely
   unknown the input is *omitted* -- passing 0 would be a lie that silences the
   rule, since 0 is always within budget.
3. **Wired at the production gate.** ``LiuHaoAssistant.chat`` supplies the
   inputs, and an unaffordable turn really is refused.
"""
from __future__ import annotations

from unittest import mock

import pytest

from src.ai.agent_factory import AgentPolicy, economy_inputs
from src.kernels.policy import PolicyEffect, PolicyScope, get_policy_engine
from src.kernels.resource import ResourceType

QUOTA = "quota_enforcement"

# The app-level ALLOW that lets the assistant chat at all.
LOW_RISK_ALLOW_PRECEDENCE = 50


# --------------------------------------------------------------------------
# deterministic stand-ins for the real sources
# --------------------------------------------------------------------------


def _patch_prices(monkeypatch, prices):
    """Patch the model registry so exactly ``prices`` are priced."""
    registry = mock.Mock()
    registry.get_model = lambda name: (
        mock.Mock(estimated_cost_per_1k=prices[name]) if name in prices else None
    )
    import src.models.registry as registry_module

    monkeypatch.setattr(registry_module, "get_model_registry", lambda: registry)
    return registry


def _patch_quotas(monkeypatch, by_owner):
    """Patch the resource kernel so ``by_owner`` maps owner -> [available]."""
    manager = mock.Mock()
    manager.get_all_quotas = lambda owner=None: [
        mock.Mock(resource_type=ResourceType.COST, available=value)
        for value in by_owner.get(owner, [])
    ]
    import src.kernels.resource as resource_module

    monkeypatch.setattr(resource_module, "get_resource_manager", lambda: manager)
    return manager


@pytest.fixture
def predictable_sources(monkeypatch):
    """One priced model (``alpha``) and one COST quota (100.0)."""
    _patch_prices(monkeypatch, {"alpha": 0.5})
    _patch_quotas(monkeypatch, {"system": [100.0]})


# --------------------------------------------------------------------------
# 1. reachability
# --------------------------------------------------------------------------


class TestReachability:
    def test_rule_scope_matches_the_gate(self):
        rule = get_policy_engine().get_rule(QUOTA)
        # The gate evaluates at L1; a higher scope would filter the rule out.
        assert rule.scope == PolicyScope.L1

    def test_precedence_outranks_the_low_risk_allow(self):
        rule = get_policy_engine().get_rule(QUOTA)
        # Over budget must win over "this is a low-risk action".
        assert rule.precedence > LOW_RISK_ALLOW_PRECEDENCE

    def test_it_decides_when_the_caller_supplies_inputs(self):
        decision = AgentPolicy(principal="probe").authorize(
            "some.action", resource={"available": 1}, estimated_cost=10
        )
        assert decision.decision == PolicyEffect.DENY
        assert QUOTA in [r.id for r in decision.denied_rules]
        assert decision.is_denied

    def test_it_stays_out_of_the_way_within_budget(self):
        decision = AgentPolicy(principal="probe").authorize(
            "some.action", resource={"available": 100}, estimated_cost=10
        )
        assert QUOTA not in [r.id for r in decision.denied_rules]
        assert not decision.is_denied

    def test_a_denied_quota_is_not_reported_as_merely_unapplied(self):
        decision = AgentPolicy(principal="probe").authorize(
            "some.action", resource={"available": 1}, estimated_cost=10
        )
        # It applied. "Unapplied" must mean "did not run", not "ran and said no".
        assert QUOTA not in [r.id for r in decision.unapplied_deny_rules]


# --------------------------------------------------------------------------
# 2. real sourcing, and the deliberate omissions
# --------------------------------------------------------------------------


class TestOmissionIsNotZero:
    def test_priced_model_supplies_a_cost(self, predictable_sources):
        kwargs = economy_inputs("probe", "alpha", 1000, 1000)
        # 0.5 per 1K over 2000 tokens = 1.0
        assert kwargs["estimated_cost"] == pytest.approx(1.0)

    def test_unpriced_model_omits_the_cost_rather_than_calling_it_free(self, predictable_sources):
        kwargs = economy_inputs("probe", "no-such-model", 1000, 1000)
        # A 0.0 here would be indistinguishable from "free" and would defeat
        # the guard. The key must be absent instead.
        assert "estimated_cost" not in kwargs

    def test_no_model_at_all_omits_the_cost(self, predictable_sources):
        assert "estimated_cost" not in economy_inputs("probe", None, 100, 100)

    def test_a_cost_quota_supplies_the_budget(self, predictable_sources):
        kwargs = economy_inputs("probe", "alpha", 1, 1)
        assert kwargs["resource"]["available"] == pytest.approx(100.0)

    def test_no_cost_quota_omits_the_budget(self, monkeypatch):
        _patch_prices(monkeypatch, {"alpha": 0.5})
        _patch_quotas(monkeypatch, {})  # nobody has a quota
        kwargs = economy_inputs("probe", "alpha", 1, 1)
        assert "resource" not in kwargs
        assert "estimated_cost" in kwargs  # the cost is still known

    def test_the_principals_own_quota_wins_over_the_system_default(self, monkeypatch):
        _patch_prices(monkeypatch, {"alpha": 0.5})
        _patch_quotas(monkeypatch, {"probe": [2.0], "system": [100.0]})
        kwargs = economy_inputs("probe", "alpha", 1, 1)
        assert kwargs["resource"]["available"] == pytest.approx(2.0)

    def test_the_tightest_quota_in_force_binds(self, monkeypatch):
        _patch_prices(monkeypatch, {"alpha": 0.5})
        _patch_quotas(monkeypatch, {"system": [100.0, 3.0]})
        kwargs = economy_inputs("probe", "alpha", 1, 1)
        assert kwargs["resource"]["available"] == pytest.approx(3.0)


class TestDegradationIsLoud:
    """A broken source must not raise on the auth path -- but must not lie."""

    def test_pricing_failure_degrades_to_omission(self, monkeypatch):
        def _boom():
            raise RuntimeError("registry on fire")

        import src.models.registry as registry_module

        monkeypatch.setattr(registry_module, "get_model_registry", _boom)
        _patch_quotas(monkeypatch, {"system": [100.0]})

        kwargs = economy_inputs("probe", "alpha", 10, 10)  # must not raise
        assert "estimated_cost" not in kwargs
        assert kwargs["resource"]["available"] == pytest.approx(100.0)

    def test_quota_failure_degrades_to_omission(self, monkeypatch):
        def _boom():
            raise RuntimeError("resource kernel on fire")

        import src.kernels.resource as resource_module

        _patch_prices(monkeypatch, {"alpha": 0.5})
        monkeypatch.setattr(resource_module, "get_resource_manager", _boom)

        kwargs = economy_inputs("probe", "alpha", 10, 10)  # must not raise
        assert "resource" not in kwargs
        assert "estimated_cost" in kwargs


# --------------------------------------------------------------------------
# 3. the production gate
# --------------------------------------------------------------------------


class TestChatGate:
    @pytest.fixture(autouse=True)
    def _isolate(self, tmp_path, monkeypatch):
        from src.ai import liuhao as liuhao_module
        from src.ai.conversation_store import ConversationStore

        store = ConversationStore(db_path=str(tmp_path / "conv.db"))
        monkeypatch.setattr(liuhao_module, "get_conversation_store", lambda: store)

    def _assistant(self, name):
        from src.ai.providers import MockProvider

        from src.ai.liuhao import LiuHaoAssistant

        return LiuHaoAssistant(name=name, provider=MockProvider(name="t", model="mock-model"))

    def test_the_gate_collects_cost_and_budget(self, monkeypatch):
        _patch_prices(monkeypatch, {"mock-model": 0.5})
        _patch_quotas(monkeypatch, {"system": [100.0]})

        a = self._assistant("quota-gate-inputs")
        kwargs = a._quota_authorize_kwargs("hello")
        assert set(kwargs) == {"estimated_cost", "resource"}
        assert kwargs["resource"]["available"] == pytest.approx(100.0)
        assert kwargs["estimated_cost"] > 0

    def test_chat_completes_when_the_budget_covers_it(self, monkeypatch):
        _patch_prices(monkeypatch, {"mock-model": 0.5})
        _patch_quotas(monkeypatch, {"system": [100.0]})

        a = self._assistant("quota-gate-affordable")
        assert a.chat("hello")["status"] == "completed"

    def test_chat_is_refused_when_the_budget_cannot_cover_it(self, monkeypatch):
        _patch_prices(monkeypatch, {"mock-model": 0.5})
        _patch_quotas(monkeypatch, {"system": [0.0]})  # nothing left to spend

        a = self._assistant("quota-gate-broke")
        result = a.chat("hello")
        assert result["status"] == "denied"
        assert "权限不足" in result["reply"]

    def test_chat_is_not_refused_when_the_cost_is_unknown(self, monkeypatch):
        """An unpriced model must not be blocked on a guess."""
        _patch_prices(monkeypatch, {})  # model unpriced
        _patch_quotas(monkeypatch, {"system": [0.0]})

        a = self._assistant("quota-gate-unpriced")
        assert a.chat("hello")["status"] == "completed"
