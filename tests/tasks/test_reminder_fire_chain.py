"""R4：经工具创建的提醒必须真的写库、到点触发并投递（原始 P0 的闭环）。

用户当时看到的是「30 秒后只显示裸 JSON」，``reminders`` 里 0 条、一分钟
后也没有任何弹出。这里从工具入口建一条 ``time + instruction``，推进假时钟，
跑真实调度循环，断言：通知发生了、提醒状态 completed、delivery 有一条。
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.tasks.local_handlers import register_local_jobs
from src.tasks.local_scheduler import RUNNING, LocalScheduler
from src.tasks.reminder_tool import build_reminder_tool
from src.tasks.reminder_notify import NotifyResult
from src.tasks.reminders import ReminderService, ReminderStore

TZ = timezone(timedelta(hours=8))


class FakeClock:
    def __init__(self, start: datetime) -> None:
        self.current = start

    def __call__(self) -> datetime:
        return self.current

    def advance(self, seconds: float) -> None:
        self.current += timedelta(seconds=seconds)


class RecordingNotifier:
    channel = "test"

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def notify(
        self, title: str, body: str, *, idempotency_key: str | None = None
    ) -> list[NotifyResult]:
        self.calls.append((title, body))
        return [NotifyResult(self.channel, True, "")]


async def _drive(scheduler: LocalScheduler, predicate, *, timeout: float = 2.0) -> bool:
    runner = asyncio.create_task(scheduler.run())
    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            await asyncio.sleep(0.01)
        return bool(predicate())
    finally:
        scheduler.stop()
        await asyncio.wait_for(runner, timeout=3.0)


def _settled(scheduler: LocalScheduler, predicate):
    def check() -> bool:
        if not predicate():
            return False
        return not any(
            job["status"] == RUNNING for job in scheduler.list_jobs(None, 50)
        )

    return check


@pytest.mark.asyncio
async def test_tool_created_reminder_really_fires(tmp_path: Path):
    clock = FakeClock(datetime(2026, 1, 1, 0, 0))
    scheduler = LocalScheduler(
        str(tmp_path / "sched.db"),
        max_concurrency=1,
        tick_seconds=0.01,
        now_fn=clock,
        audit_sink=lambda action, outcome, details: None,
    )
    store = ReminderStore(str(tmp_path / "reminders.db"))
    notifier = RecordingNotifier()
    service = ReminderService(scheduler, store=store, notifier=notifier, tz=TZ)
    service.update_settings({"quiet_enabled": False})
    register_local_jobs(scheduler, reminder_service=service)

    tool = build_reminder_tool("u1", service=service)
    result = json.loads(tool.fn(action="create", time="1分钟后", instruction="提醒我喝水"))
    assert result["status"] == "ok", result
    reminder_id = result["reminder"]["id"]

    clock.advance(61)
    fired = await _drive(
        scheduler,
        _settled(
            scheduler,
            lambda: bool(notifier.calls)
            and (service.get("u1", reminder_id) or {}).get("status") == "completed",
        ),
    )
    assert fired, "提醒没有触发（又回到原始 P0）"
    assert len(notifier.calls) == 1
    assert "喝水" in notifier.calls[0][1]
    assert len(service.list_deliveries("u1", reminder_id=reminder_id)) == 1
