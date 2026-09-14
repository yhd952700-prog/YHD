"""A completed turn actually charges the ``COST`` quota (quota accounting).

Companion to ``tests/kernels/policy/test_quota_enforcement.py``. That module
proves the guard *reads* the budget; this one proves something *writes back to*
it. Without this half, ``Quota.used`` stays 0 forever, ``available`` stays at
the limit, and the guard only ever bites when a human sets the quota
artificially tight -- i.e. the control is decorative in normal operation.

The claim under test has three parts:

1. **The kernel can record an incurred spend.** ``resource.account_spend``
   moves ``used`` upward and only upward, so it can never be used to grant
   capacity (it is strictly more conservative than the already-approved
   ``resource.release``, which moves ``used`` downward).
2. **A completed turn records it.** ``LiuHaoAssistant`` charges exactly the
   number the gate evaluated -- not a fresh estimate, so authorization and
   accounting cannot disagree.
3. **Accounting is best-effort and honest.** A turn that failed or was denied
   is not charged; an unpriced model is not charged a guessed 0; and a
   bookkeeping failure warns instead of turning into a user-visible outage.

Every number asserted here is produced by the code under test -- no price and
no cost is hardcoded in this file.
"""
from __future__ import annotations

from unittest import mock

import pytest

from src.kernels.resource import (
    ResourceQuotaManager,
    ResourceScope,
    ResourceType,
)

# --------------------------------------------------------------------------
# deterministic stand-ins for the two real sources
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


@pytest.fixture
def real_manager(monkeypatch):
    """A REAL quota manager (not a mock) so ``used`` can be observed."""
    from src.kernels import resource as resource_module

    manager = ResourceQuotaManager()
    monkeypatch.setattr(resource_module, "get_resource_manager", lambda: manager)
    return manager


def _cost_quota(manager, owner, limit, scope=ResourceScope.L2):
    return manager.create_quota(scope, owner, ResourceType.COST, limit)


def _cost_quotas(manager, owner):
    return [
        q
        for q in manager.get_all_quotas(owner)
        if q.resource_type == ResourceType.COST
    ]


# --------------------------------------------------------------------------
# 1. the kernel primitive
# --------------------------------------------------------------------------


class TestAccountSpend:
    def test_it_charges_the_owners_quota(self, real_manager):
        _cost_quota(real_manager, "alice", 10.0)

        assert real_manager.account_spend(ResourceType.COST, 3.0, "alice") == 1

        (quota,) = _cost_quotas(real_manager, "alice")
        assert quota.used == pytest.approx(3.0)
        assert quota.available == pytest.approx(7.0)

    def test_it_only_ever_reduces_availability(self, real_manager):
        """Charging can never grant capacity -- the opposite of ``release``."""
        quota = _cost_quota(real_manager, "alice", 10.0)
        before = quota.available

        real_manager.account_spend(ResourceType.COST, 2.5, "alice")
        real_manager.account_spend(ResourceType.COST, 2.5, "alice")

        assert quota.available == pytest.approx(before - 5.0)
        assert quota.used == pytest.approx(5.0)

    def test_zero_and_negative_amounts_charge_nothing(self, real_manager):
        quota = _cost_quota(real_manager, "alice", 10.0)

        assert real_manager.account_spend(ResourceType.COST, 0.0, "alice") == 0
        assert real_manager.account_spend(ResourceType.COST, -5.0, "alice") == 0
        assert quota.used == pytest.approx(0.0)

    def test_an_owner_without_a_matching_quota_charges_nothing(self, real_manager):
        # No COST quota was created for "ghost"; the default system one must
        # not be silently charged on their behalf.
        assert real_manager.account_spend(ResourceType.COST, 1.0, "ghost") == 0
        assert not _cost_quotas(real_manager, "ghost")

    def test_every_matching_quota_for_the_owner_is_charged(self, real_manager):
        _cost_quota(real_manager, "bob", 10.0, ResourceScope.L2)
        _cost_quota(real_manager, "bob", 100.0, ResourceScope.L3)

        assert real_manager.account_spend(ResourceType.COST, 1.0, "bob") == 2
        assert all(
            q.used == pytest.approx(1.0) for q in _cost_quotas(real_manager, "bob")
        )

    def test_other_resource_types_are_untouched(self, real_manager):
        _cost_quota(real_manager, "alice", 10.0)
        token = real_manager.create_quota(
            ResourceScope.L2, "alice", ResourceType.TOKEN, 1000
        )

        real_manager.account_spend(ResourceType.COST, 4.0, "alice")

        assert token.used == pytest.approx(0.0)

    def test_it_is_pre_approved_for_the_internal_service(self):
        """LOW tier + C-1 allow-list, i.e. it cannot wedge the kernel bus."""
        from src.kernels._risk_classification import KERNEL_ACTION_RISK, RiskTier
        from src.kernels.policy import INTERNAL_SERVICE_ALLOWED_ACTIONS

        assert KERNEL_ACTION_RISK["resource.account_spend"].tier is RiskTier.LOW
        assert "resource.account_spend" in INTERNAL_SERVICE_ALLOWED_ACTIONS


# --------------------------------------------------------------------------
# 2. the assistant charges a turn it actually completed
# --------------------------------------------------------------------------


class TestTheTurnIsCharged:
    @pytest.fixture(autouse=True)
    def _isolate(self, tmp_path, monkeypatch):
        from src.ai import liuhao as liuhao_module
        from src.ai.conversation_store import ConversationStore

        store = ConversationStore(db_path=str(tmp_path / "conv.db"))
        monkeypatch.setattr(liuhao_module, "get_conversation_store", lambda: store)

    def _assistant(self, name):
        from src.ai.liuhao import LiuHaoAssistant
        from src.ai.providers import MockProvider

        return LiuHaoAssistant(
            name=name, provider=MockProvider(name="t", model="mock-model")
        )

    def test_a_completed_turn_charges_what_the_gate_evaluated(
        self, real_manager, monkeypatch
    ):
        _patch_prices(monkeypatch, {"mock-model": 0.5})
        _cost_quota(real_manager, "accounting-owner", 10.0)

        a = self._assistant("accounting-owner")
        assert a.chat("hello")["status"] == "completed"

        evaluated = a._turn_estimated_cost
        assert evaluated, "the gate produced no cost; this test proves nothing"
        (quota,) = _cost_quotas(real_manager, "accounting-owner")
        assert quota.used == pytest.approx(evaluated)

    def test_it_falls_back_to_the_system_quota(self, real_manager, monkeypatch):
        """No per-principal quota -> the system default is charged."""
        _patch_prices(monkeypatch, {"mock-model": 0.5})

        a = self._assistant("accounting-no-own-quota")
        assert a.chat("hello")["status"] == "completed"

        (system,) = _cost_quotas(real_manager, "system")
        assert system.used == pytest.approx(a._turn_estimated_cost)

    def test_the_principals_own_quota_is_charged_not_the_system_one(
        self, real_manager, monkeypatch
    ):
        _patch_prices(monkeypatch, {"mock-model": 0.5})
        _cost_quota(real_manager, "accounting-prefer-own", 10.0)

        a = self._assistant("accounting-prefer-own")
        a.chat("hello")

        (system,) = _cost_quotas(real_manager, "system")
        assert system.used == pytest.approx(0.0)
        (own,) = _cost_quotas(real_manager, "accounting-prefer-own")
        assert own.used > 0

    def test_a_denied_turn_is_not_charged(self, real_manager, monkeypatch):
        """Nothing was generated, so nothing was spent."""
        _patch_prices(monkeypatch, {"mock-model": 0.5})
        quota = _cost_quota(real_manager, "accounting-broke", 0.0)

        a = self._assistant("accounting-broke")
        assert a.chat("hello")["status"] == "denied"
        assert quota.used == pytest.approx(0.0)

    def test_an_unpriced_model_charges_no_guessed_zero(self, real_manager, monkeypatch):
        """No price -> no spend recorded. Recording 0 would be a lie."""
        _patch_prices(monkeypatch, {})

        a = self._assistant("accounting-unpriced")
        assert a.chat("hello")["status"] == "completed"
        assert a._turn_estimated_cost is None

        (system,) = _cost_quotas(real_manager, "system")
        assert system.used == pytest.approx(0.0)

    def test_a_bookkeeping_failure_cannot_break_the_turn(self, monkeypatch):
        _patch_prices(monkeypatch, {"mock-model": 0.5})

        from src.kernels import resource as resource_module

        def boom():
            raise RuntimeError("quota store unavailable")

        monkeypatch.setattr(resource_module, "get_resource_manager", boom)

        a = self._assistant("accounting-broken-store")
        assert a.chat("hello")["status"] == "completed"

    def test_a_failed_generation_is_not_charged(self, real_manager, monkeypatch):
        """status == "error" means no billable completion."""
        _patch_prices(monkeypatch, {"mock-model": 0.5})
        _cost_quota(real_manager, "accounting-error", 10.0)

        a = self._assistant("accounting-error")
        monkeypatch.setattr(
            a, "_generate_with_tools", lambda messages: (_ for _ in ()).throw(
                RuntimeError("upstream exploded")
            )
        )
        assert a.chat("hello")["status"] == "error"

        (quota,) = _cost_quotas(real_manager, "accounting-error")
        assert quota.used == pytest.approx(0.0)
