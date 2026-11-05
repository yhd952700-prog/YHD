"""定时提醒的真实行为验证（复用进程内排程器，不是另起一套）。

覆盖用户要求：
* 真到点触发（2 秒后）
* 取消后不触发
* 修改后旧版本不触发、新版本按时触发
* 触发失败可查（投递失败原因 + 死信）
* 重复规则失败一次不中断整条序列
* 含糊时间被反问（工具层原样返回问题）
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.tasks.local_handlers import register_local_jobs
from src.tasks.local_scheduler import (
    CANCELLED,
    PENDING,
    RETRY_WAIT,
    LocalScheduler,
)
from src.tasks.reminder_notify import (
    ConsoleOutboxNotifier,
    NotifyResult,
    WindowsBalloonNotifier,
    build_default_notifier,
)
from src.tasks.reminder_tool import build_reminder_tool
from src.tasks.reminders import ReminderService, ReminderStore

TZ = timezone(timedelta(hours=8))


class FakeClock:
    def __init__(self, start: datetime) -> None:
        self.current = start

    def __call__(self) -> datetime:
        return self.current

    def advance(self, seconds: float) -> None:
        self.current = self.current + timedelta(seconds=seconds)


class RecordingNotifier:
    channel = "test"

    def __init__(self, *, fail: bool = False, detail: str = "boom") -> None:
        self.calls: list[tuple[str, str]] = []
        self.fail = fail
        self.detail = detail

    def notify(self, title: str, body: str) -> list[NotifyResult]:
        self.calls.append((title, body))
        return [NotifyResult(self.channel, not self.fail, "" if not self.fail else self.detail)]


def _store(tmp_path: Path) -> ReminderStore:
    return ReminderStore(str(tmp_path / "reminders.db"))


def _scheduler(tmp_path: Path, clock, *, tick_seconds: float = 0.01) -> LocalScheduler:
    return LocalScheduler(
        str(tmp_path / "sched.db"),
        max_concurrency=1,
        tick_seconds=tick_seconds,
        now_fn=clock,
        audit_sink=lambda action, outcome, details: None,
    )


async def _drive(scheduler: LocalScheduler, predicate, *, timeout: float = 1.0) -> bool:
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


async def test_reminder_really_fires_after_two_seconds(tmp_path):
    """真到点触发：容器内跑真实排程循环，2 秒后才响。"""
    scheduler = LocalScheduler(
        str(tmp_path / "sched.db"),
        max_concurrency=1,
        tick_seconds=0.05,
        audit_sink=lambda action, outcome, details: None,
    )
    store = _store(tmp_path)
    notifier = RecordingNotifier()
    service = ReminderService(scheduler, store=store, notifier=notifier, tz=TZ)
    register_local_jobs(scheduler, reminder_service=service)

    created = service.create("u1", "2秒后提醒我喝水")
    start = time.monotonic()
    runner = asyncio.create_task(scheduler.run())
    try:
        while not (
            notifier.calls
            and (service.get("u1", created["id"]) or {}).get("status") == "completed"
        ):
            assert time.monotonic() - start < 10, "reminder never fired"
            await asyncio.sleep(0.05)
    finally:
        scheduler.stop()
        await asyncio.wait_for(runner, timeout=5.0)
    elapsed = time.monotonic() - start

    assert elapsed >= 1.5, f"fired too early ({elapsed:.2f}s)"
    assert len(notifier.calls) == 1
    assert "喝水" in notifier.calls[0][1]

    deliveries = service.list_deliveries("u1", reminder_id=created["id"])
    assert len(deliveries) == 1
    assert deliveries[0]["status"] == "delivered"
    assert deliveries[0]["channels"][0]["ok"] is True
    assert service.get("u1", created["id"])["status"] == "completed"
    scheduler.close()
    store.close()


async def test_cancelled_reminder_does_not_fire(tmp_path):
    clock = FakeClock(datetime(2026, 1, 1, 0, 0))
    scheduler = _scheduler(tmp_path, clock)
    store = _store(tmp_path)
    notifier = RecordingNotifier()
    service = ReminderService(scheduler, store=store, notifier=notifier, tz=TZ)
    register_local_jobs(scheduler, reminder_service=service)

    created = service.create("u1", "5秒后提醒我喝水")
    result = service.cancel("u1", created["id"])
    assert result["status"] == "cancelled"
    assert result["cancelled_jobs"] >= 1
    assert service.list("u1", include_cancelled=False) == []
    assert service.get("u1", created["id"])["status"] == "cancelled"

    clock.advance(6)
    fired = await _drive(scheduler, lambda: bool(notifier.calls), timeout=0.5)
    assert fired is False
    assert notifier.calls == []
    assert scheduler.list_jobs(CANCELLED, limit=10)
    scheduler.close()
    store.close()


async def test_update_rearms_and_stale_version_is_skipped(tmp_path):
    clock = FakeClock(datetime(2026, 1, 1, 0, 0))
    scheduler = _scheduler(tmp_path, clock)
    store = _store(tmp_path)
    notifier = RecordingNotifier()
    service = ReminderService(scheduler, store=store, notifier=notifier, tz=TZ)
    register_local_jobs(scheduler, reminder_service=service)

    created = service.create("u1", "5秒后提醒我旧内容")
    updated = service.update("u1", created["id"], "10秒后提醒我新内容")
    assert updated["status"] == "updated"
    assert updated["reminder"]["version"] == 2

    # 旧版本（5 秒）已被取消：到点也不响
    clock.advance(6)
    assert await _drive(scheduler, lambda: bool(notifier.calls), timeout=0.5) is False
    assert notifier.calls == []

    # 旧版本 job 即使被误触发，也因版本不匹配而跳过
    stale = service.handle_fire(
        {
            "reminder_id": created["id"],
            "version": 1,
            "occurrence": created["run_at"],
            "user_id": "u1",
            "title": "旧内容",
        }
    )
    assert stale["status"] == "skipped"
    assert "stale" in stale["reason"]

    # 新版本（10 秒）到点触发
    clock.advance(4.1)
    assert await _drive(
        scheduler,
        lambda: len(notifier.calls) == 1
        and (service.get("u1", created["id"]) or {}).get("status") == "completed",
        timeout=1.0,
    )
    assert len(notifier.calls) == 1
    assert "新内容" in notifier.calls[0][1]
    scheduler.close()
    store.close()


async def test_delivery_failure_is_recorded_and_dead_lettered(tmp_path):
    clock = FakeClock(datetime(2026, 1, 1, 0, 0))
    scheduler = _scheduler(tmp_path, clock)
    store = _store(tmp_path)
    notifier = RecordingNotifier(fail=True, detail="boom")
    service = ReminderService(
        scheduler,
        store=store,
        notifier=notifier,
        tz=TZ,
        max_attempts=2,
        backoff_seconds=0.2,
        backoff_multiplier=1.0,
        max_backoff_seconds=0.2,
    )
    register_local_jobs(scheduler, reminder_service=service)

    created = service.create("u1", "1秒后提醒我喝水")
    clock.advance(1)
    assert await _drive(
        scheduler,
        lambda: len(notifier.calls) == 1
        and bool(scheduler.list_jobs(RETRY_WAIT, limit=10)),
        timeout=0.6,
    )

    clock.advance(0.21)
    assert await _drive(scheduler, lambda: scheduler.dead_letter_count() == 1, timeout=0.6)

    failures = service.failures("u1")
    assert len(failures["failed_deliveries"]) == 2
    assert all("boom" in (item["error"] or "") for item in failures["failed_deliveries"])
    assert len(failures["dead_letter_jobs"]) == 1
    assert failures["dead_letter_jobs"][0]["reminder_id"] == created["id"]
    assert "boom" in failures["dead_letter_jobs"][0]["error"]

    reminder = service.get("u1", created["id"])
    assert reminder["last_error"] and "boom" in reminder["last_error"]
    dead = scheduler.list_dead_letters()
    assert dead and dead[0]["payload"]["reminder_id"] == created["id"]
    scheduler.close()
    store.close()


async def test_repeat_series_survives_one_failed_occurrence(tmp_path):
    clock = FakeClock(datetime(2026, 1, 1, 0, 0))
    scheduler = _scheduler(tmp_path, clock)
    store = _store(tmp_path)
    notifier = RecordingNotifier(fail=True, detail="boom")
    service = ReminderService(
        scheduler,
        store=store,
        notifier=notifier,
        tz=TZ,
        max_attempts=2,
        backoff_seconds=0.2,
        max_backoff_seconds=0.2,
    )
    register_local_jobs(scheduler, reminder_service=service)

    # 本地 10:00 = UTC 02:00
    created = service.create("u1", "每天十点提醒我喝水")
    clock.advance(2 * 3600)
    assert await _drive(
        scheduler,
        lambda: len(notifier.calls) == 1
        and any(
            job["payload"].get("reminder_id") == created["id"]
            and job["run_at"] == "2026-01-02T02:00:00"
            for job in scheduler.list_jobs(PENDING, limit=20)
        ),
        timeout=0.6,
    )

    reminder = service.get("u1", created["id"])
    assert reminder["status"] == "active"
    assert reminder["run_at"] == "2026-01-02T02:00:00"
    pending = scheduler.list_jobs(PENDING, limit=20)
    assert any(
        job["payload"].get("reminder_id") == created["id"]
        and job["run_at"] == "2026-01-02T02:00:00"
        for job in pending
    ), pending
    scheduler.close()
    store.close()


def test_ambiguous_time_returns_question_from_tool(tmp_path):
    clock = FakeClock(datetime(2026, 1, 1, 0, 0))
    scheduler = _scheduler(tmp_path, clock)
    store = _store(tmp_path)
    notifier = RecordingNotifier()
    service = ReminderService(scheduler, store=store, notifier=notifier, tz=TZ)
    tool = build_reminder_tool("u1", service=service)

    out = json.loads(tool.fn(action="create", text="下周那个会"))
    assert out["status"] == "needs_clarification"
    assert "question" in out and ("具体" in out["question"] or "几点" in out["question"])

    created = json.loads(tool.fn(action="create", text="每天十点提醒我喝水"))
    assert created["status"] == "ok"
    assert "每天 10:00" in created["message"]

    listed = json.loads(tool.fn(action="list"))
    assert listed["count"] == 1
    cancelled = json.loads(
        tool.fn(action="cancel", reminder_id=created["reminder"]["id"])
    )
    assert cancelled["status"] == "cancelled"
    assert json.loads(tool.fn(action="list"))["count"] == 0
    scheduler.close()
    store.close()


def test_needs_clarification_is_a_value_error():
    # 工具层靠捕获它来把问题问回用户；保持 ValueError 继承关系。
    from src.tasks.reminders import NeedsClarification as RemindersClarification

    assert issubclass(RemindersClarification, ValueError)


def test_console_outbox_is_durable_and_visible(tmp_path):
    outbox = tmp_path / "outbox.jsonl"
    notifier = ConsoleOutboxNotifier(str(outbox))
    results = notifier.notify("鎏灏提醒", "喝水")
    assert results[0].ok is True
    record = json.loads(outbox.read_text(encoding="utf-8").splitlines()[0])
    assert record["title"] == "鎏灏提醒"
    assert record["body"] == "喝水"


def test_default_notifier_console_only_when_os_channel_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("LIUHAO_REMINDER_OS_NOTIFY", "0")
    notifier = build_default_notifier(str(tmp_path / "outbox.jsonl"))
    assert [type(item).__name__ for item in notifier.notifiers] == [
        "ConsoleOutboxNotifier"
    ]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows 托盘气泡只在 Windows 可用")
def test_windows_balloon_is_a_real_os_channel():
    """真实弹一次托盘气泡（不是 mock）：返回 BALLOON_OK 才算用户看得到。"""
    results = WindowsBalloonNotifier(timeout_seconds=30.0).notify(
        "鎏灏提醒测试", "这是一条真实的托盘气泡通知"
    )
    assert any(item.ok for item in results), results
