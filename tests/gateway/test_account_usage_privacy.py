"""成员侧用量不可从计数里看出"我的 Key 被主账号借用过"。

计费归属不变：借用仍记在 Key 主人名下；根视角看到真实总量与代用记录，
成员侧的 ``calls`` 只统计本人发起的调用。
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def usage_db(monkeypatch, tmp_path):
    path = tmp_path / "account_usage.sqlite3"
    monkeypatch.setenv("LIUHAO_ACCOUNT_USAGE_DB", str(path))
    return path


def test_member_count_hides_superior_borrowing(usage_db):
    from src.gateway.account_usage import record_usage, usage_summary

    # 成员自己调用 1 次。
    assert record_usage(
        actor="member1", key_owner="member1", model="m", tokens_in=10, tokens_out=5
    )
    # 根借用成员 Key 3 次：计费仍归 member1，调用者是 root1。
    for _ in range(3):
        assert record_usage(
            actor="root1", key_owner="member1", model="m", tokens_in=20, tokens_out=8
        )

    member_view = usage_summary(
        "member1", include_callers=False, owners=("member1",)
    )
    assert member_view["calls"] == 1
    assert member_view["tokens_in"] == 10
    assert member_view["tokens_out"] == 5
    assert member_view["callers_visible"] is False
    assert "by_actor" not in member_view

    root_view = usage_summary(
        "root1", include_callers=True, owners=("root1", "member1")
    )
    assert root_view["calls"] == 4
    assert root_view["tokens_in"] == 70
    assert root_view["callers_visible"] is True
    actors = {item["actor"]: item["calls"] for item in root_view["by_actor"]}
    assert actors == {"member1": 1, "root1": 3}
    owners = {item["key_owner"]: item["calls"] for item in root_view["by_key_owner"]}
    assert owners == {"member1": 4}


def test_member_count_unchanged_after_more_borrowing(usage_db):
    from src.gateway.account_usage import record_usage, usage_summary

    record_usage(actor="member1", key_owner="member1", model="m", tokens_in=1, tokens_out=1)
    baseline = usage_summary("member1", include_callers=False, owners=("member1",))

    for _ in range(5):
        record_usage(actor="root1", key_owner="member1", model="m", tokens_in=9, tokens_out=9)

    after = usage_summary("member1", include_callers=False, owners=("member1",))
    assert after["calls"] == baseline["calls"] == 1
    assert after["total_tokens"] == baseline["total_tokens"] == 2

    root_view = usage_summary("root1", include_callers=True, owners=("root1", "member1"))
    assert root_view["calls"] == 6
