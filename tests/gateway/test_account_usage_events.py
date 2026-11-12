"""逐笔用量明细（``/usage/events``）与成本展示回归。

覆盖三件事：
* 成员只能看到 ``actor=自己`` 的行；根可以按子树 + actor/key_owner 过滤；
* ``correlation_id`` / 时间范围 / 分页都能过滤到同一批真实账本行；
* 成本口径与 ``BillingEngine`` 一致：未定价模型 ``cost=None``（不是 0 元），
  合计只含已定价模型并明确标记"未计入成本"。
"""

from __future__ import annotations

import pytest

from src.gateway import account_usage


@pytest.fixture()
def usage_db(tmp_path, monkeypatch):
    monkeypatch.setenv("LIUHAO_ACCOUNT_USAGE_DB", str(tmp_path / "usage.sqlite3"))
    account_usage._initialised.clear()
    yield
    account_usage._initialised.clear()


def _seed() -> None:
    rows = [
        ("root1", "child_a", "unpriced-model", 100, 20, "corr-root-a1"),
        ("root1", "child_a", "priced-model", 200, 40, "corr-root-a2"),
        ("child_a", "child_a", "priced-model", 50, 10, "corr-child-a"),
        ("child_b", "child_b", "unpriced-model", 30, 6, "corr-child-b"),
    ]
    for actor, owner, model, tokens_in, tokens_out, correlation in rows:
        assert account_usage.record_usage(
            actor=actor,
            key_owner=owner,
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            session_id="s1",
            correlation_id=correlation,
            estimated=False,
        )


@pytest.fixture()
def priced(monkeypatch):
    """把"已定价模型"固定为 10 元 / 1K token；未定价模型仍不在表里。"""
    monkeypatch.setattr(
        account_usage,
        "_price_table",
        lambda models: {"priced-model": 10.0}
        if "priced-model" in list(models)
        else {},
    )


def test_member_events_only_actor_self(usage_db):
    _seed()
    result = account_usage.usage_events(
        "child_a", include_callers=False, owners=("child_a",), actor="root1"
    )
    assert result["total"] == 1
    assert result["scope"] == "self"
    assert result["events"][0]["actor"] == "child_a"
    assert result["events"][0]["key_owner"] == "child_a"


def test_root_events_filter_and_pagination(usage_db):
    _seed()
    owners = ("root1", "child_a", "child_b")

    by_actor = account_usage.usage_events(
        "root1", include_callers=True, owners=owners, actor="root1"
    )
    assert by_actor["total"] == 2, "根借用子账号 Key 的两次调用都属于 root1"

    by_owner = account_usage.usage_events(
        "root1", include_callers=True, owners=owners, key_owner="child_a"
    )
    assert by_owner["total"] == 3

    by_corr = account_usage.usage_events(
        "root1", include_callers=True, owners=owners, correlation_id="corr-child-b"
    )
    assert by_corr["total"] == 1
    assert by_corr["events"][0]["model"] == "unpriced-model"

    page = account_usage.usage_events(
        "root1", include_callers=True, owners=owners, limit=2, offset=0
    )
    assert page["total"] == 4
    assert page["count"] == 2
    assert page["has_more"] is True
    second = account_usage.usage_events(
        "root1", include_callers=True, owners=owners, limit=2, offset=2
    )
    assert second["count"] == 2
    assert second["has_more"] is False


def test_events_time_filter_uses_normalised_iso(usage_db):
    _seed()
    with account_usage._lock, account_usage._connect() as connection:
        connection.execute(
            "UPDATE account_usage_events SET created_at=? WHERE correlation_id=?",
            ("2020-01-01T00:00:00+00:00", "corr-root-a1"),
        )
        connection.commit()

    result = account_usage.usage_events(
        "root1",
        include_callers=True,
        owners=("root1", "child_a", "child_b"),
        since="2021-01-01T00:00:00",
    )
    assert result["total"] == 3
    assert "corr-root-a1" not in [event["correlation_id"] for event in result["events"]]


def test_duplicate_correlation_is_written_once(usage_db):
    """同一 correlation_id 只能有一行用量：重复记账 = 重复扣费。"""
    kwargs = dict(
        actor="root1",
        key_owner="root1",
        model="priced-model",
        tokens_in=100,
        tokens_out=20,
        session_id="s1",
        correlation_id="corr-dup",
        estimated=False,
    )
    assert account_usage.record_usage(**kwargs) is True
    assert account_usage.record_usage(**kwargs) is True  # 幂等：不报错、不再写行

    result = account_usage.usage_events(
        "root1", include_callers=True, owners=("root1",), correlation_id="corr-dup"
    )
    assert result["total"] == 1
    assert result["events"][0]["tokens_in"] == 100


def test_empty_correlation_keeps_legacy_rows(usage_db):
    """没有 correlation_id 的旧调用保持原语义：允许写多行。"""
    for _ in range(2):
        assert account_usage.record_usage(
            actor="root1", key_owner="root1", model="m", tokens_in=1, tokens_out=1
        )
    result = account_usage.usage_events(
        "root1", include_callers=True, owners=("root1",)
    )
    assert result["total"] == 2


def test_cost_fields_mark_unknown_models_as_not_counted(usage_db, priced):
    _seed()
    result = account_usage.usage_events(
        "root1", include_callers=True, owners=("root1", "child_a", "child_b")
    )
    # 已定价：240 + 60 token = 300/1000*10 = 3.0 元。
    assert result["cost"] == 3.0
    assert result["cost_complete"] is False
    assert result["unknown_models"] == ["unpriced-model"]
    assert "未计入成本" in result["cost_message"]
    assert "不参与预算拦截" in result["budget_note"]

    unknown_events = [
        event for event in result["events"] if event["model"] == "unpriced-model"
    ]
    assert unknown_events
    for event in unknown_events:
        assert event["cost"] is None, "未定价模型不能按 0 元展示"
        assert event["pricing_status"] == "unknown"
        assert "未计入成本" in event["message"]

    priced_events = [
        event for event in result["events"] if event["model"] == "priced-model"
    ]
    assert {event["cost"] for event in priced_events} == {2.4, 0.6}


def test_summary_cost_by_model(usage_db, priced):
    _seed()
    summary = account_usage.usage_summary(
        "root1", include_callers=True, owners=("root1", "child_a", "child_b")
    )
    assert summary["cost"] == 3.0
    assert summary["cost_complete"] is False
    assert summary["unknown_models"] == ["unpriced-model"]
    assert "未计入成本" in summary["cost_message"]
    models = {entry["model"]: entry for entry in summary["by_model"]}
    assert models["priced-model"]["cost"] == 3.0
    assert models["unpriced-model"]["cost"] is None
    assert models["unpriced-model"]["pricing_status"] == "unknown"
