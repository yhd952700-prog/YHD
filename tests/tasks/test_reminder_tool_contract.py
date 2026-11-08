"""R2 / R3：提醒工具入参契约。

R2 覆盖模型实际会传的字段组合（``time + instruction`` / ``time + reminder_text``）：
必须写库成功，拼接文本保留原文，且不补默认时间。
R3 覆盖两种拒绝路径，且都必须 0 行：
* 只有时间、没有内容 → ``status=error``；
* 有内容、没有时间 → ``needs_clarification``（不许默认「1 分钟后」）。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.tasks.local_scheduler import LocalScheduler
from src.tasks.reminder_tool import build_reminder_tool
from src.tasks.reminders import ReminderService, ReminderStore

TZ = timezone(timedelta(hours=8))


class FakeClock:
    def __init__(self, start: datetime) -> None:
        self.current = start

    def __call__(self) -> datetime:
        return self.current


@pytest.fixture
def service(tmp_path: Path):
    clock = FakeClock(datetime(2026, 1, 1, 0, 0, tzinfo=TZ))
    scheduler = LocalScheduler(
        str(tmp_path / "sched.db"),
        max_concurrency=1,
        tick_seconds=0.01,
        now_fn=clock,
        audit_sink=lambda action, outcome, details: None,
    )
    store = ReminderStore(str(tmp_path / "reminders.db"))
    svc = ReminderService(scheduler, store=store, notifier=None, tz=TZ)
    svc.update_settings({"quiet_enabled": False})
    yield svc
    scheduler.stop()


def _tool(service: ReminderService):
    return build_reminder_tool("u1", service=service)


def _create(tool, **args) -> dict:
    return json.loads(tool.fn(action="create", **args))


@pytest.mark.parametrize(
    "args",
    [
        {"time": "1分钟后", "instruction": "提醒我喝水"},
        {"time": "1分钟后", "reminder_text": "提醒我喝水"},
        {"time": "1分钟后", "title": "提醒我喝水"},
    ],
)
def test_time_plus_content_creates_reminder_preserving_original(service, args):
    tool = _tool(service)
    result = _create(tool, **args)

    assert result["status"] == "ok", result
    rows = service.list("u1")
    assert len(rows) == 1

    reminder = rows[0]
    assert "喝水" in reminder["title"]
    assert "分钟后" in reminder["time_text"], "原文里的时间表达必须保留"


def test_text_with_full_sentence_still_works(service):
    tool = _tool(service)
    result = _create(tool, text="1分钟后提醒我喝水")
    assert result["status"] == "ok", result
    assert len(service.list("u1")) == 1


def test_time_only_is_rejected_without_rows(service):
    tool = _tool(service)
    result = _create(tool, time="1分钟后")

    assert result["status"] == "error", result
    assert "内容" in result["error"]
    assert service.list("u1") == [], "拒绝路径不得写库"


def test_content_without_time_asks_instead_of_defaulting(service):
    tool = _tool(service)
    result = _create(tool, instruction="提醒我喝水")

    assert result["status"] == "needs_clarification", result
    assert result.get("question"), "缺时间必须反问，不许猜"
    assert service.list("u1") == [], "缺时间不得写库"
