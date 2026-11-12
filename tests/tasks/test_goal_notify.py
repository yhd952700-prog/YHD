"""后台派活结束通知的文案与幂等契约。

用户关掉页面后要能知道"跑完了 / 失败了"，且文案不能出现内部术语或原始异常。
"""

from __future__ import annotations

import json
import os

from src.tasks.goal_notify import notify_goal_outcome, goal_notification_text
from src.tasks.reminder_notify import ConsoleOutboxNotifier


def _entry(state: str, **extra) -> dict:
    base = {
        "goal_id": "g1",
        "state": state,
        "natural_language": "整理本周的项目进展并生成摘要",
    }
    base.update(extra)
    return base


def test_completed_copy_is_neutral_chinese():
    title, body = goal_notification_text(_entry("completed"))
    assert title == "鎏灏任务通知"
    assert "已完成" in body
    assert "failed" not in body.lower()
    assert "目标与任务" in body


def test_failed_copy_has_no_internal_terms_or_raw_error():
    title, body = goal_notification_text(
        _entry("failed", failed_tasks=2, error="RuntimeError: provider timeout")
    )
    assert "没有完成" in body
    assert "2 项" in body
    assert "RuntimeError" not in body
    assert "timeout" not in body


def test_cancelled_copy_says_stopped():
    _, body = goal_notification_text(_entry("cancelled"))
    assert "已停止" in body


def test_partial_failure_is_not_reported_as_all_good():
    """goal 整体 completed 但有子任务失败时，不能说"已完成"糊弄过去。"""
    _, body = goal_notification_text(
        _entry(
            "completed",
            tasks=[
                {"status": "completed"},
                {"status": "failed"},
            ],
        )
    )
    assert "1 项没有成功" in body
    assert "已完成" not in body


def test_notify_writes_outbox_and_suppresses_duplicate(tmp_path):
    outbox = tmp_path / "outbox.jsonl"
    notifier = ConsoleOutboxNotifier(str(outbox))
    first = notify_goal_outcome(_entry("completed"), notifier=notifier)
    second = notify_goal_outcome(_entry("completed"), notifier=notifier)

    lines = outbox.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1, "同一目标同一终态只能落一条通知"
    payload = json.loads(lines[0])
    assert payload["idempotency_key"] == "goal:g1:completed"
    assert any(item.ok for item in first)
    assert any(item.ok for item in second)


def test_notifier_exception_never_breaks_goal(tmp_path):
    class Exploding:
        channel = "boom"

        def notify(self, title, body, *, idempotency_key=None):
            raise RuntimeError("boom")

    assert notify_goal_outcome(_entry("failed"), notifier=Exploding()) == []


def test_entry_without_goal_id_is_skipped(tmp_path):
    outbox = tmp_path / "outbox.jsonl"
    assert notify_goal_outcome(
        {"state": "completed", "natural_language": "x"},
        notifier=ConsoleOutboxNotifier(str(outbox)),
    ) == []
    assert not os.path.exists(outbox)
