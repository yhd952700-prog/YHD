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
    RUNNING,
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

    def notify(
        self, title: str, body: str, *, idempotency_key: str | None = None
    ) -> list[NotifyResult]:
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


def _settled(scheduler: LocalScheduler, predicate):
    """谓词成立且 handler 已退出（没有 running job）才算完成。

    否则 ``_drive`` 可能在 handler 写库后、入队下一次之前就停掉调度器，
    把"已完成一半"的任务误当成最终状态（测试会随机挂）。
    """

    def check() -> bool:
        if not predicate():
            return False
        return not any(
            job["status"] == RUNNING for job in scheduler.list_jobs(None, 50)
        )

    return check


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
    # 这条用真实时钟，必须关掉勿扰，否则在夜里跑测试会被延后到早上。
    service.update_settings({"quiet_enabled": False})
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


async def test_missed_reminders_coalesce_into_one_digest(tmp_path):
    """关机三天：三条错过的提醒只弹一条摘要，不一次性全炸出来。"""
    clock = FakeClock(datetime(2026, 1, 1, 0, 0))  # 本地 08:00，不在勿扰
    scheduler = _scheduler(tmp_path, clock)
    store = _store(tmp_path)
    notifier = RecordingNotifier()
    service = ReminderService(scheduler, store=store, notifier=notifier, tz=TZ)
    register_local_jobs(scheduler, reminder_service=service)

    texts = ["2秒后提醒我喝水", "4秒后提醒我交材料", "6秒后提醒我开会"]
    created = [service.create("u1", text) for text in texts]

    # 关机三天，期间三条都到点了。
    clock.advance(3 * 24 * 3600)
    missed = lambda: service.missed("u1")["missed"]  # noqa: E731
    assert await _drive(
        scheduler, _settled(scheduler, lambda: len(missed()) == 3), timeout=2.0
    )
    assert notifier.calls == [], "错过的提醒不许单独弹"

    rows = missed()
    assert {row["status"] for row in rows} == {"missed"}
    assert [row["channels"] for row in rows] == [[], [], []]
    assert all(row["lateness_seconds"] > 6 * 3600 for row in rows)
    assert all(row["digested_at"] is None for row in rows)

    # 摘要稍微延迟（3 秒）后只弹一次。
    clock.advance(3.1)
    digested = lambda: all(row["digested_at"] for row in missed())  # noqa: E731
    assert await _drive(scheduler, _settled(scheduler, digested), timeout=2.0)

    assert len(notifier.calls) == 1, notifier.calls
    body = notifier.calls[0][1]
    assert "3 条" in body
    assert "开会" in body, "摘要里要带最近一条，方便用户知道最要紧的是什么"
    assert service.missed("u1")["pending_digest"] == 0
    for item in created:
        assert service.get("u1", item["id"])["status"] == "completed"

    # 错过的提醒也要能"做完"，否则清单只增不减。
    assert service.done("u1")["status"] == "done"
    acked = [
        row
        for row in service.list_deliveries("u1")
        if row["status"] == "missed" and row["ack_status"] == "done"
    ]
    assert len(acked) == 1
    scheduler.close()
    store.close()


async def test_digest_is_not_fired_in_the_middle_of_the_night(tmp_path):
    """关机后开机正好在半夜：错过的提醒也不能半夜弹，摘要同样延后到早上。"""
    clock = FakeClock(datetime(2026, 1, 1, 10, 0))  # 本地 18:00
    scheduler = _scheduler(tmp_path, clock)
    store = _store(tmp_path)
    notifier = RecordingNotifier()
    service = ReminderService(scheduler, store=store, notifier=notifier, tz=TZ)
    register_local_jobs(scheduler, reminder_service=service)

    service.create("u1", "2秒后提醒我喝水")
    # 6.5 小时后（本地 00:30）才开机：已算错过，且此刻在静默期。
    clock.advance(6 * 3600 + 1810)
    missed = lambda: service.missed("u1")["missed"]  # noqa: E731
    assert await _drive(
        scheduler, _settled(scheduler, lambda: len(missed()) == 1), timeout=1.0
    )
    assert notifier.calls == []

    # 摘要 job 3 秒后执行，仍处于静默期 → 自己延后到 07:00（UTC 23:00）。
    clock.advance(3.1)
    digest_deferred = lambda: any(  # noqa: E731
        job["job_name"] == "liuhao.reminder.digest"
        and job["payload"].get("user_id") == "u1"
        and job["run_at"].startswith("2026-01-01T23:00")
        for job in scheduler.list_jobs(PENDING, 20)
    )
    assert await _drive(scheduler, _settled(scheduler, digest_deferred), timeout=1.0)
    assert notifier.calls == [], "错过的摘要不能在半夜炸"

    clock.advance((datetime(2026, 1, 1, 23, 0) - clock.current).total_seconds())
    assert await _drive(
        scheduler,
        _settled(
            scheduler,
            lambda: bool(notifier.calls) and missed()[0]["digested_at"] is not None,
        ),
        timeout=1.0,
    )
    assert len(notifier.calls) == 1
    assert "1 条" in notifier.calls[0][1]
    scheduler.close()
    store.close()


async def test_quiet_period_defers_then_delivers(tmp_path):
    """静默期内注册 → 静默结束投递（不丢弃、不变成从没响过）。"""
    clock = FakeClock(datetime(2026, 1, 1, 15, 0))  # 本地 23:00，默认勿扰开始
    scheduler = _scheduler(tmp_path, clock)
    store = _store(tmp_path)
    notifier = RecordingNotifier()
    service = ReminderService(scheduler, store=store, notifier=notifier, tz=TZ)
    register_local_jobs(scheduler, reminder_service=service)

    created = service.create("u1", "2分钟后提醒我喝水")
    clock.advance(121)  # 本地 23:02，仍在静默期

    deferred = lambda: any(  # noqa: E731
        job["payload"].get("quiet_deferred") for job in scheduler.list_jobs(PENDING, 20)
    )
    assert await _drive(scheduler, _settled(scheduler, deferred), timeout=1.0)
    assert notifier.calls == [], "静默期不能弹"
    assert service.get("u1", created["id"])["status"] == "active"
    pending = service.list_deliveries("u1")
    assert [row["status"] for row in pending] == ["deferred"], "延后不是丢弃，也不是提前送达"

    # 跨到本地次日 07:00（UTC 23:00）静默结束，延后的那条这时才响。
    quiet_end = datetime(2026, 1, 1, 23, 0)
    clock.advance((quiet_end - clock.current).total_seconds())
    assert await _drive(
        scheduler,
        _settled(
            scheduler,
            lambda: len(notifier.calls) == 1
            and service.get("u1", created["id"])["status"] == "completed",
        ),
        timeout=1.0,
    )
    assert "喝水" in notifier.calls[0][1]
    delivery = service.list_deliveries("u1")[0]
    assert delivery["status"] == "delivered"
    assert delivery["lateness_seconds"] < 6 * 3600
    scheduler.close()
    store.close()


async def test_cancel_during_quiet_prevents_deferred_delivery(tmp_path):
    """静默期内取消 → 延后的那条也不再投递。"""
    clock = FakeClock(datetime(2026, 1, 1, 15, 0))
    scheduler = _scheduler(tmp_path, clock)
    store = _store(tmp_path)
    notifier = RecordingNotifier()
    service = ReminderService(scheduler, store=store, notifier=notifier, tz=TZ)
    register_local_jobs(scheduler, reminder_service=service)

    created = service.create("u1", "2分钟后提醒我喝水")
    clock.advance(121)
    deferred = lambda: any(  # noqa: E731
        job["payload"].get("quiet_deferred") for job in scheduler.list_jobs(PENDING, 20)
    )
    assert await _drive(scheduler, _settled(scheduler, deferred), timeout=1.0)

    cancelled = service.cancel("u1", created["id"])
    assert cancelled["status"] == "cancelled"
    assert cancelled["cancelled_jobs"] >= 1, "延后的 job 必须一并撤掉"

    clock.advance((datetime(2026, 1, 1, 23, 0) - clock.current).total_seconds())
    assert await _drive(scheduler, lambda: bool(notifier.calls), timeout=1.0) is False
    assert notifier.calls == []
    fire_jobs = [
        job
        for job in scheduler.list_jobs(PENDING, 20)
        if job["job_name"] == "liuhao.reminder.fire"
    ]
    assert fire_jobs == [], "取消后不应再有待发的提醒"
    scheduler.close()
    store.close()


async def test_done_marks_fired_occurrence_and_shrinks_backlog(tmp_path):
    clock = FakeClock(datetime(2026, 1, 1, 0, 0))
    scheduler = _scheduler(tmp_path, clock)
    store = _store(tmp_path)
    notifier = RecordingNotifier()
    service = ReminderService(scheduler, store=store, notifier=notifier, tz=TZ)
    register_local_jobs(scheduler, reminder_service=service)

    created = service.create("u1", "2秒后提醒我喝水")
    clock.advance(3)
    assert await _drive(
        scheduler,
        _settled(
            scheduler,
            lambda: len(notifier.calls) == 1
            and service.get("u1", created["id"])["status"] == "completed",
        ),
        timeout=0.6,
    )

    assert service.done("u1")["status"] == "done"
    delivery = service.list_deliveries("u1")[0]
    assert delivery["ack_status"] == "done"
    assert delivery["acked_at"]
    assert service.done("u1")["status"] == "not_found", "没有待处理的了，不能再重复标记"
    scheduler.close()
    store.close()


async def test_snooze_refires_then_done(tmp_path):
    clock = FakeClock(datetime(2026, 1, 1, 0, 0))
    scheduler = _scheduler(tmp_path, clock)
    store = _store(tmp_path)
    notifier = RecordingNotifier()
    service = ReminderService(scheduler, store=store, notifier=notifier, tz=TZ)
    register_local_jobs(scheduler, reminder_service=service)
    service.update_settings({"snooze_minutes": 10})

    created = service.create("u1", "2秒后提醒我交材料")
    clock.advance(3)
    assert await _drive(
        scheduler, _settled(scheduler, lambda: len(notifier.calls) == 1), timeout=0.6
    )

    snoozed = service.snooze("u1", reminder_id=created["id"])
    assert snoozed["status"] == "snoozed"
    assert snoozed["minutes"] == 10
    assert snoozed["reminder"]["status"] == "completed"
    first = service.list_deliveries("u1")[0]
    assert first["ack_status"] == "snoozed"

    clock.advance(10 * 60 + 1)
    assert await _drive(
        scheduler, _settled(scheduler, lambda: len(notifier.calls) == 2), timeout=0.6
    )
    assert "交材料" in notifier.calls[1][1]
    deliveries = service.list_deliveries("u1")
    assert len(deliveries) == 2
    latest = deliveries[0]
    assert latest["status"] == "delivered"
    assert latest["occurrence"].startswith("snooze:")
    assert latest["ack_status"] is None, "补响的这一次仍可被标记完成"

    assert service.done("u1", delivery_id=latest["id"])["status"] == "done"
    assert service.list_deliveries("u1")[0]["ack_status"] == "done"
    scheduler.close()
    store.close()


async def test_snoozed_once_reminder_can_still_be_cancelled(tmp_path):
    """一次性提醒响过并"稍后再提醒"后，取消也要能撤掉那条补响。"""
    clock = FakeClock(datetime(2026, 1, 1, 0, 0))
    scheduler = _scheduler(tmp_path, clock)
    store = _store(tmp_path)
    notifier = RecordingNotifier()
    service = ReminderService(scheduler, store=store, notifier=notifier, tz=TZ)
    register_local_jobs(scheduler, reminder_service=service)

    created = service.create("u1", "2秒后提醒我喝水")
    clock.advance(3)
    assert await _drive(
        scheduler, _settled(scheduler, lambda: len(notifier.calls) == 1), timeout=0.6
    )
    assert service.snooze("u1", reminder_id=created["id"], minutes=5)["status"] == "snoozed"

    cancelled = service.cancel("u1", created["id"])
    assert cancelled["status"] == "cancelled"
    assert cancelled["cancelled_jobs"] >= 1

    clock.advance(5 * 60 + 1)
    assert await _drive(scheduler, lambda: len(notifier.calls) == 2, timeout=0.6) is False
    assert len(notifier.calls) == 1
    scheduler.close()
    store.close()


async def test_settings_and_ack_survive_store_restart(tmp_path):
    """设置有落 SQLite；重开库后仍生效，已完成的记账也还在。"""
    clock = FakeClock(datetime(2026, 1, 1, 0, 0))
    scheduler = _scheduler(tmp_path, clock)
    store = _store(tmp_path)
    notifier = RecordingNotifier()
    service = ReminderService(scheduler, store=store, notifier=notifier, tz=TZ)
    register_local_jobs(scheduler, reminder_service=service)

    settings = service.update_settings(
        {"quiet_start": "22:30", "quiet_end": "06:30", "snooze_minutes": 7}
    )
    assert settings["status"] == "updated"
    assert settings["settings"]["quiet_start"] == "22:30"
    assert settings["settings"]["snooze_minutes"] == 7

    created = service.create("u1", "2秒后提醒我喝水")
    clock.advance(3)
    assert await _drive(
        scheduler,
        _settled(
            scheduler,
            lambda: len(notifier.calls) == 1
            and service.get("u1", created["id"])["status"] == "completed",
        ),
        timeout=0.6,
    )
    assert service.done("u1")["status"] == "done"

    scheduler.close()
    store.close()
    store2 = ReminderStore(str(tmp_path / "reminders.db"))
    raw = store2.get_settings()
    assert raw["quiet_start_minutes"] == str(22 * 60 + 30)
    assert raw["quiet_end_minutes"] == str(6 * 60 + 30)
    assert raw["snooze_minutes"] == "7"
    assert store2.list_deliveries("u1")[0]["ack_status"] == "done"
    assert store2.latest_pending_ack("u1") is None
    store2.close()


def test_tool_exposes_settings_done_and_snooze(tmp_path):
    clock = FakeClock(datetime(2026, 1, 1, 0, 0))
    scheduler = _scheduler(tmp_path, clock)
    store = _store(tmp_path)
    notifier = RecordingNotifier()
    service = ReminderService(scheduler, store=store, notifier=notifier, tz=TZ)
    tool = build_reminder_tool("u1", service=service)

    current = json.loads(tool.fn(action="settings"))
    assert current["status"] == "ok"
    assert current["settings"]["quiet_text"].startswith("勿扰 23:00–07:00")

    updated = json.loads(
        tool.fn(action="settings", quiet_enabled=False, missed_threshold_hours=3)
    )
    assert updated["status"] == "updated"
    assert updated["settings"]["quiet_enabled"] is False
    assert updated["settings"]["missed_threshold_minutes"] == 180

    assert json.loads(tool.fn(action="done"))["status"] == "not_found"
    assert json.loads(tool.fn(action="snooze"))["status"] == "not_found"
    assert json.loads(tool.fn(action="missed"))["count"] == 0
    scheduler.close()
    store.close()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows 托盘气泡只在 Windows 可用")
def test_windows_balloon_is_a_real_os_channel():
    """真实弹一次托盘气泡（不是 mock）：返回 BALLOON_OK 才算用户看得到。"""
    results = WindowsBalloonNotifier(timeout_seconds=30.0).notify(
        "鎏灏提醒测试", "这是一条真实的托盘气泡通知"
    )
    assert any(item.ok for item in results), results


# -- 崩溃 / 幂等（at-most-once）------------------------------------------------
#
# 进程硬死（SIGKILL / 断电）是 BaseException 级别的消失，不会被
# ``except Exception`` 兜住，所以测试里用 ``KeyboardInterrupt`` 模拟。
# 每个测试都会"重启"一遍（新 store + 新 service 指向同一个库），模拟真实重启。


def _dispatch_row(store: ReminderStore, reminder_id: str, occurrence: str):
    with store._lock:
        row = store._conn.execute(
            "SELECT status, attempts FROM reminder_dispatch"
            " WHERE reminder_id = ? AND occurrence = ?",
            (reminder_id, occurrence),
        ).fetchone()
    if row is None:
        return None
    return {"status": str(row[0]), "attempts": int(row[1])}


def _payload(created, user_id: str = "u1"):
    return {
        "reminder_id": created["id"],
        "version": int(created["version"]),
        "occurrence": created["run_at"],
        "user_id": user_id,
    }


async def test_crash_before_notify_never_repeats_and_is_queryable(tmp_path, monkeypatch):
    """通知前硬死：重启后最多响一次（这里是 0 次），并可在 failures 里查到。"""
    clock = FakeClock(datetime(2026, 1, 1, 0, 0))  # 本地 08:00，不在勿扰
    scheduler = _scheduler(tmp_path, clock)
    store = _store(tmp_path)
    notifier = RecordingNotifier()
    service = ReminderService(scheduler, store=store, notifier=notifier, tz=TZ)

    created = service.create("u1", "2秒后提醒我喝水")
    payload = _payload(created)
    clock.advance(3)

    import src.tasks.reminders as reminders_module

    original = reminders_module.maybe_test_crash

    def crash(point: str) -> None:
        if point == "before_notify":
            raise KeyboardInterrupt("simulated hard kill before notify")
        original(point)

    monkeypatch.setattr(reminders_module, "maybe_test_crash", crash)
    with pytest.raises(KeyboardInterrupt):
        service.handle_fire(payload)
    assert notifier.calls == [], "通知前就死了，不该响"
    assert _dispatch_row(store, created["id"], created["run_at"]) == {
        "status": "intent",
        "attempts": 1,
    }

    # 进程重启：同一个库、新的 store/service/notifier。
    store2 = ReminderStore(str(tmp_path / "reminders.db"))
    scheduler2 = _scheduler(tmp_path, clock)
    notifier2 = RecordingNotifier()
    service2 = ReminderService(
        scheduler2, store=store2, notifier=notifier2, tz=TZ
    )
    result = service2.handle_fire(payload)

    assert result["status"] == "unknown", result
    assert notifier2.calls == [], "宁可少响一次，也不能重响"
    assert _dispatch_row(store2, created["id"], created["run_at"])["status"] == "unknown"
    assert service2.get("u1", created["id"])["status"] == "completed"
    deliveries = service2.list_deliveries("u1")
    assert [row["status"] for row in deliveries] == ["unknown"]
    failures = service2.failures("u1")
    assert [row["status"] for row in failures["failed_deliveries"]] == ["unknown"]
    assert failures["failed_deliveries"][0]["error"]

    scheduler.close()
    scheduler2.close()
    store.close()
    store2.close()


async def test_crash_during_notify_never_repeats(tmp_path):
    """通知中硬死（已经弹了一次）：重启后封口 unknown，绝不再弹第二次。"""

    class CrashingNotifier(RecordingNotifier):
        def notify(self, title, body, *, idempotency_key=None):
            self.calls.append((title, body))
            raise KeyboardInterrupt("simulated hard kill during notify")

    clock = FakeClock(datetime(2026, 1, 1, 0, 0))
    scheduler = _scheduler(tmp_path, clock)
    store = _store(tmp_path)
    notifier = CrashingNotifier()
    service = ReminderService(scheduler, store=store, notifier=notifier, tz=TZ)

    created = service.create("u1", "2秒后提醒我喝水")
    payload = _payload(created)
    clock.advance(3)

    with pytest.raises(KeyboardInterrupt):
        service.handle_fire(payload)
    assert len(notifier.calls) == 1, "这一次真的响了"
    assert _dispatch_row(store, created["id"], created["run_at"])["status"] == "intent"

    store2 = ReminderStore(str(tmp_path / "reminders.db"))
    scheduler2 = _scheduler(tmp_path, clock)
    notifier2 = RecordingNotifier()
    service2 = ReminderService(scheduler2, store=store2, notifier=notifier2, tz=TZ)
    result = service2.handle_fire(payload)

    assert result["status"] == "unknown", result
    assert notifier2.calls == [], "已经响过一次，不能补响第二次"
    assert _dispatch_row(store2, created["id"], created["run_at"])["status"] == "unknown"
    failures = service2.failures("u1")
    assert failures["failed_deliveries"][0]["status"] == "unknown"
    assert "不再重响" in failures["failed_deliveries"][0]["error"]

    scheduler.close()
    scheduler2.close()
    store.close()
    store2.close()


async def test_crash_after_notify_before_terminal_record_never_repeats(
    tmp_path, monkeypatch
):
    """通知成功后、写终态前硬死：重启同样封口 unknown，不再重响。"""
    clock = FakeClock(datetime(2026, 1, 1, 0, 0))
    scheduler = _scheduler(tmp_path, clock)
    store = _store(tmp_path)
    notifier = RecordingNotifier()
    service = ReminderService(scheduler, store=store, notifier=notifier, tz=TZ)

    created = service.create("u1", "2秒后提醒我喝水")
    payload = _payload(created)
    clock.advance(3)

    def crash(**kwargs):
        raise KeyboardInterrupt("simulated hard kill after notify")

    monkeypatch.setattr(store, "record_outcome_and_advance", crash)
    with pytest.raises(KeyboardInterrupt):
        service.handle_fire(payload)
    assert len(notifier.calls) == 1, "通知已经发出去了"
    assert _dispatch_row(store, created["id"], created["run_at"])["status"] == "intent"

    store2 = ReminderStore(str(tmp_path / "reminders.db"))
    scheduler2 = _scheduler(tmp_path, clock)
    notifier2 = RecordingNotifier()
    service2 = ReminderService(scheduler2, store=store2, notifier=notifier2, tz=TZ)
    result = service2.handle_fire(payload)

    assert result["status"] == "unknown", result
    assert notifier2.calls == []
    assert service2.get("u1", created["id"])["status"] == "completed"
    assert service2.failures("u1")["failed_deliveries"][0]["status"] == "unknown"

    scheduler.close()
    scheduler2.close()
    store.close()
    store2.close()


async def test_all_channels_failed_can_retry_with_same_idempotency_key(tmp_path):
    """所有通道都明确失败（没响）= 允许重试；重试用同一个幂等键。"""
    from src.tasks.reminders import (
        ReminderDeliveryError,
        reminder_dispatch_key,
    )

    clock = FakeClock(datetime(2026, 1, 1, 0, 0))
    scheduler = _scheduler(tmp_path, clock)
    store = _store(tmp_path)
    notifier = RecordingNotifier(fail=True, detail="boom")
    service = ReminderService(scheduler, store=store, notifier=notifier, tz=TZ)

    created = service.create("u1", "2秒后提醒我喝水")
    payload = _payload(created)
    clock.advance(3)

    with pytest.raises(ReminderDeliveryError):
        service.handle_fire(payload)
    assert _dispatch_row(store, created["id"], created["run_at"])["status"] == "failed"
    assert [row["status"] for row in service.list_deliveries("u1")] == ["failed"]

    notifier.fail = False
    result = service.handle_fire(payload)
    assert result["status"] == "delivered", result
    assert len(notifier.calls) == 2, "第一次没响，第二次才真的响"
    assert _dispatch_row(store, created["id"], created["run_at"])["status"] == "delivered"
    assert service.get("u1", created["id"])["status"] == "completed"
    assert service.list_deliveries("u1")[0]["status"] == "delivered"

    # 幂等键只由 id/版本/时刻决定，跨进程/时钟变化都不变。
    key = reminder_dispatch_key(created["id"], int(created["version"]), created["run_at"])
    other = ReminderStore(str(tmp_path / "reminders.db"))
    assert key == reminder_dispatch_key(
        created["id"], 1, created["run_at"]
    ), "换个进程/时钟重算也必须是同一个键"
    other.close()

    scheduler.close()
    store.close()


async def test_repeat_schedule_is_repaired_when_delivered_but_not_advanced(tmp_path):
    """已响并记 delivered，但死在"排下一次"之前：重启后补齐下一次。"""
    clock = FakeClock(datetime(2026, 1, 1, 0, 0))  # 本地 08:00
    scheduler = _scheduler(tmp_path, clock)
    store = _store(tmp_path)
    notifier = RecordingNotifier()
    service = ReminderService(scheduler, store=store, notifier=notifier, tz=TZ)

    created = service.create("u1", "每天十点提醒我喝水")
    payload = _payload(created)
    clock.advance(2 * 3600)  # 本地 10:00，到点

    # 模拟"响过并写了 delivered，但进程死在推进提醒状态之前"。
    store.record_delivery(
        reminder_id=created["id"],
        user_id="u1",
        occurrence=payload["occurrence"],
        status="delivered",
        channels=[{"channel": "test", "ok": True}],
        error=None,
        now_iso=clock.current.isoformat(),
    )

    result = service.handle_fire(payload)
    assert result["status"] == "skipped", result
    assert result["outcome"] == "delivered"
    assert notifier.calls == [], "已经响过，不能重响"
    reminder = service.get("u1", created["id"])
    assert reminder["status"] == "active"
    assert reminder["run_at"] == "2026-01-02T02:00:00"
    pending = scheduler.list_jobs(PENDING, 20)
    assert any(
        job["payload"].get("reminder_id") == created["id"]
        and job["run_at"] == "2026-01-02T02:00:00"
        for job in pending
    ), pending
    scheduler.close()
    store.close()

