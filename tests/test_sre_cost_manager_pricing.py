"""未知模型的价格缺口：不能以 0 元混进成本统计。

背景：``_DEFAULT_PRICING`` 是一张小而诚实的价格表，老键保留没问题；但
"查不到就返回 0.0"会让用户把"没花钱"当成事实。这里把未知价格建模为
``None``（可区分状态），并验证它不会被加总、不会让预算状态误报、不会让
调用方拿不到可展示的文案。
"""

import pytest

from src.sre.cost.cost_manager import (
    UNKNOWN_COST_MESSAGE,
    BudgetPolicy,
    CostManager,
    Provider,
    UsageRecord,
)


def test_known_model_cost_is_summed():
    manager = CostManager()

    cost = manager.track(UsageRecord(Provider.OPENAI, "gpt-4", 1000, 500))

    assert cost == pytest.approx(0.06)
    assert manager.total_cost == pytest.approx(0.06)
    assert manager.unknown_cost_records == []
    summary = manager.cost_summary()
    assert summary["total_cost"] == pytest.approx(0.06)
    assert summary["total_cost_complete"] is True
    assert summary["message"] == ""


def test_unknown_model_is_none_not_zero():
    manager = CostManager()
    record = UsageRecord(Provider.OPENAI, "gpt-4o-mini", 1000, 500)

    assert manager.estimate_cost(record) is None
    assert manager.track(record) is None

    # 已知成本合计保持 0，但它是"没有已知成本"，不是"这次请求花了 0 元"。
    assert manager.total_cost == 0.0
    # 用量事实仍然记账。
    assert manager.total_input_tokens == 1000
    assert manager.total_output_tokens == 500
    assert manager.unknown_cost_records == [record]

    summary = manager.cost_summary()
    assert summary["total_cost_complete"] is False
    assert summary["unknown_cost_records"] == 1
    assert summary["message"] == UNKNOWN_COST_MESSAGE


def test_budget_status_reports_unknown_price_instead_of_zero():
    manager = CostManager()
    manager.budget_policies["google"] = BudgetPolicy("google", 10.0, Provider.GOOGLE)
    manager.track(UsageRecord(Provider.GOOGLE, "gemini-2.5-flash", 1000, 1000))

    status = manager.budget_status("google")

    assert status["status"] == "unknown_price"
    assert status["current_cost"] is None
    assert status["remaining"] is None
    assert status["unknown_models"] == ["gemini-2.5-flash"]
    assert status["message"] == UNKNOWN_COST_MESSAGE
    # 未知价格不能当成"在预算内"放行，且不抛异常。
    assert manager.apply_budget_policy("google") is False


def test_mixed_known_and_unknown_only_sums_known_cost():
    manager = CostManager()

    known = manager.track(UsageRecord(Provider.OPENAI, "gpt-4", 1000, 0))
    unknown = manager.track(UsageRecord(Provider.OPENAI, "gpt-4o-mini", 1000, 0))

    assert known == pytest.approx(0.03)
    assert unknown is None
    # 未知的那条没有被当成 0 参与统计，也没有被"补"进合计。
    assert manager.total_cost == pytest.approx(0.03)
    assert len(manager.unknown_cost_records) == 1
    assert manager.cost_summary()["total_cost_complete"] is False


def test_unknown_model_does_not_taint_another_provider_budget():
    manager = CostManager()
    manager.budget_policies["openai"] = BudgetPolicy("openai", 10.0, Provider.OPENAI)
    manager.track(UsageRecord(Provider.OPENAI, "gpt-4", 1000, 0))
    manager.track(UsageRecord(Provider.GOOGLE, "gemini-2.5-flash", 1000, 0))

    status = manager.budget_status("openai")

    assert status["status"] == "ok"
    assert status["current_cost"] == pytest.approx(0.03)
    assert manager.cost_summary()["total_cost_complete"] is False


def test_self_hosted_provider_is_known_free_not_unknown():
    manager = CostManager()

    cost = manager.track(UsageRecord(Provider.OPENSOURCE, "qwen2.5-72b", 5000, 5000))

    assert cost == 0.0
    assert manager.unknown_cost_records == []
    assert manager.cost_summary()["total_cost_complete"] is True


def test_usage_by_model_breakdown():
    """B组第二轮：用量按模型明细（SRE CostManager）。"""
    manager = CostManager()
    manager.track(UsageRecord(Provider.OPENAI, "gpt-4", 1000, 500))
    manager.track(UsageRecord(Provider.OPENAI, "gpt-4", 1000, 0))
    manager.track(UsageRecord(Provider.OPENAI, "gpt-3.5-turbo", 2000, 0))

    by_model = manager.usage_by_model()
    assert by_model["openai/gpt-4"]["input_tokens"] == 2000
    assert by_model["openai/gpt-4"]["output_tokens"] == 500
    assert by_model["openai/gpt-4"]["total_tokens"] == 2500
    # gpt-4: (2000/1000*0.03) + (500/1000*0.06) = 0.09
    assert by_model["openai/gpt-4"]["cost_usd"] == pytest.approx(0.09)
    assert by_model["openai/gpt-4"]["cost_complete"] is True
    assert by_model["openai/gpt-3.5-turbo"]["cost_usd"] == pytest.approx(0.003)


def test_usage_by_model_unpriced_model_has_null_cost():
    """B组第二轮：无价格数据的模型 cost_usd 为 None，绝不按 0 计入。"""
    manager = CostManager()
    manager.track(UsageRecord(Provider.GOOGLE, "gemini-2.5-flash", 1000, 0))

    by_model = manager.usage_by_model()
    entry = by_model["google/gemini-2.5-flash"]
    assert entry["input_tokens"] == 1000
    assert entry["cost_usd"] is None
    assert entry["cost_complete"] is False
