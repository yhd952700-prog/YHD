"""
Tests for Economy layer (MASTER-SPEC 74-76).

Asserts REAL behaviour: two-phase reservation (reserve holds, consume charges),
over-budget/block rejections, exact billing arithmetic, per-model aggregation and
honest unknown-model handling. No fakes, no assert-True tautologies.
"""

import pytest

from src.ai.economy import BillingEngine, BudgetEngine, EconomyEngine


# ===================== BudgetEngine (§75) =====================
class TestBudgetEngine:
    def test_reserve_holds_money_and_consume_charges(self):
        b = BudgetEngine(total=10.0)
        rid = b.reserve(5.0)
        assert rid is not None
        # reserve does NOT change remaining()
        assert b.remaining() == pytest.approx(10.0)
        assert b.consume(rid) is True
        # consume finally charges it
        assert b.remaining() == pytest.approx(5.0)
        assert b.usage_report()["consumed"] == pytest.approx(5.0)

    def test_over_budget_reserve_returns_none(self):
        b = BudgetEngine(total=10.0)
        assert b.reserve(6.0) is not None
        # only 4 free now (10 - 0 - 6)
        assert b.reserve(6.0) is None

    def test_release_frees_reservation(self):
        b = BudgetEngine(total=10.0)
        rid = b.reserve(6.0)
        assert b.reserved_total() == pytest.approx(6.0)
        assert b.release(rid) is True
        assert b.reserved_total() == pytest.approx(0.0)
        # freed money can be reserved again
        assert b.reserve(6.0) is not None

    def test_consume_only_charges_the_reserved_amount(self):
        b = BudgetEngine(total=10.0)
        r1 = b.reserve(4.0)
        r2 = b.reserve(3.0)
        assert b.remaining() == pytest.approx(10.0)  # nothing charged yet
        assert b.consume(r1) is True
        assert b.remaining() == pytest.approx(6.0)  # only the 4 charged

    def test_block_refuses_reserve_and_recovers_on_unblock(self):
        b = BudgetEngine(total=10.0)
        b.block()
        assert b.reserve(1.0) is None
        b.unblock()
        assert b.reserve(1.0) is not None

    def test_block_freezes_existing_reservation_consume(self):
        b = BudgetEngine(total=10.0)
        rid = b.reserve(2.0)
        b.block()
        assert b.consume(rid) is False  # cannot finalize while frozen
        b.unblock()
        assert b.consume(rid) is True

    def test_usage_report_has_all_fields(self):
        b = BudgetEngine(total=10.0)
        b.reserve(3.0)
        report = b.usage_report()
        assert set(report) == {"total", "reserved", "consumed", "remaining", "blocked"}
        assert report["total"] == pytest.approx(10.0)
        assert report["reserved"] == pytest.approx(3.0)
        assert report["remaining"] == pytest.approx(10.0)


# ===================== BillingEngine (§76) =====================
class TestBillingEngine:
    def test_cost_arithmetic_flat_price(self):
        billing = BillingEngine({"gpt-4": 0.03})
        rec = billing.record_usage("gpt-4", 1000, 1000)
        # (1000 + 1000) / 1000 * 0.03 = 0.06
        assert rec["cost"] == pytest.approx(0.06)
        assert rec["unknown_model"] is False

    def test_cost_arithmetic_split_input_output_price(self):
        billing = BillingEngine({"gpt-4": {"input": 0.01, "output": 0.02}})
        rec = billing.record_usage("gpt-4", 1000, 2000)
        # 1*0.01 + 2*0.02 = 0.05
        assert rec["cost"] == pytest.approx(0.05)

    def test_unknown_model_is_honest_not_crashing(self):
        billing = BillingEngine({"gpt-4": 0.03})
        rec = billing.record_usage("mystery-model", 500, 500)
        assert rec["cost"] == pytest.approx(0.0)
        assert rec["unknown_model"] is True

    def test_usage_report_aggregates_per_model(self):
        billing = BillingEngine({"gpt-4": 0.03})
        billing.record_usage("gpt-4", 1000, 1000)  # cost 0.06
        billing.record_usage("gpt-4", 1000, 0)      # cost 0.03
        report = billing.usage_report()
        gpt = report["models"]["gpt-4"]
        assert gpt["tokens_in"] == pytest.approx(2000.0)
        assert gpt["total_tokens"] == pytest.approx(3000.0)
        assert gpt["cost"] == pytest.approx(0.09)
        assert report["total_cost"] == pytest.approx(0.09)


# ===================== EconomyEngine (§74-76) =====================
class TestEconomyEngine:
    def _engine(self, total, price=0.03):
        return EconomyEngine(BudgetEngine(total), BillingEngine({"gpt-4": price}))

    def test_success_path_real_charge(self):
        eng = self._engine(total=1.0)
        res = eng.execute("gpt-4", 1000, 1000)
        assert res["ok"] is True
        assert res["cost"] == pytest.approx(0.06)
        assert eng._budget.remaining() == pytest.approx(0.94)

    def test_over_budget_is_rejected(self):
        eng = self._engine(total=0.05)  # cost of one call is 0.06
        res = eng.execute("gpt-4", 1000, 1000)
        assert res["ok"] is False
        assert res["status"] == "over_budget"
        assert eng._budget.usage_report()["consumed"] == pytest.approx(0.0)

    def test_explicit_block_rejects_execution(self):
        eng = self._engine(total=1.0)
        eng._budget.block()
        res = eng.execute("gpt-4", 1000, 1000)
        assert res["ok"] is False
        assert res["status"] == "blocked"
