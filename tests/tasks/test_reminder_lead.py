"""V2(a)：提醒提前量（lead_minutes）全链。

口径：事件时刻 run_at 不变（标题/展示/记账 occurrence 都按它），只有
排程 job 提前 lead 分钟触发；迟到判定从实际响铃时刻（payload 的
scheduled_at）算。解析器认「提前10分钟 / 提前半小时 / 提前2小时 /
提前十分钟」，病句与超限不猜。
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.tasks.local_handlers import register_local_jobs
from src.tasks.local_scheduler import RUNNING, LocalScheduler
from src.tasks.reminder_notify import NotifyResult
from src.tasks.reminder_time import parse_reminder
from src.tasks.reminders import ReminderService, ReminderStore

TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 11, 18, 8, 0)  # naive UTC, parser treats as UTC


# ---------------------------------------------------------------- parser


@pytest.mark.parametrize(
    "text,lead",
    [
        ("明天下午3点提前10分钟提醒我开会", 10),
        ("提前半小时提醒我吃药，明天早上8点", 30),
        ("每天早上7点提前2小时提醒我看书", 120),
        ("明天下午3点提前十分钟提醒我开会", 10),
        ("明天下午3点提前1天提醒我交报告", 1440),
        ("明天上午九点提醒我开会", 0),
        ("明天下午3点提醒我提前准备材料", 0),  # 「提前」无量词单位，不猜
    ],
)
def test_parser_lead_extraction(text, lead):
    spec = parse_reminder(text, NOW)
    assert spec.lead_minutes == lead


def test_parser_lead_keeps_event_time_and_clean_title():
    spec = parse_reminder("明天下午3点提前10分钟提醒我开会", NOW, tz=TZ)
    # 明天 15:00（+08）= 07:00 UTC；提前量不挪事件时刻
    assert spec.run_at == datetime(2026, 11, 19, 7, 0)
    assert spec.title == "开会"


# ----------------------------------------------------------------- store


def test_store_migrates_old_schema(tmp_path: Path):
    db = tmp_path / "old.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE reminders ("
        " id TEXT PRIMARY KEY, user_id TEXT NOT NULL, title TEXT NOT NULL,"
        " source_text TEXT NOT NULL DEFAULT '', time_text TEXT NOT NULL DEFAULT '',"
        " repeat_kind TEXT NOT NULL, weekday INTEGER, hour INTEGER, minute INTEGER,"
        " run_at TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,"
        " status TEXT NOT NULL DEFAULT 'active', last_fired_at TEXT,"
        " last_error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    conn.commit()
    conn.close()
    store = ReminderStore(str(db))
    cols = {
        row[1]
        for row in store._conn.execute("PRAGMA table_info(reminders)").fetchall()
    }
    assert "lead_minutes" in cols


# --------------------------------------------------------------- service


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

    def notify(self, title, body, *, idempotency_key=None):
        self.calls.append((title, body))
        return [NotifyResult(self.channel, True, "")]


def _make_service(tmp_path: Path, clock: FakeClock):
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
    return scheduler, service, notifier


def test_explicit_create_schedules_job_at_fire_time(tmp_path: Path):
    clock = FakeClock(datetime(2026, 11, 18, 0, 0))
    scheduler, service, _notifier = _make_service(tmp_path, clock)
    created = service.create_explicit(
        "u1",
        title="开会",
        run_at_local="2026-11-19T15:00",
        lead_minutes=30,
    )
    assert created["lead_minutes"] == 30
    # 事件 15:00（+08）= 07:00 UTC；响铃应提前到 06:30 UTC
    assert created["fire_at_local"].startswith("2026-11-19T14:30")
    jobs = [
        j for j in scheduler.list_jobs(None, 50)
        if (j.get("payload") or {}).get("reminder_id") == created["id"]
    ]
    assert len(jobs) == 1
    job = jobs[0]
    assert job["run_at"].startswith("2026-11-19T06:30")
    assert job["payload"]["occurrence"].startswith("2026-11-19T07:00")
    assert job["payload"]["scheduled_at"].startswith("2026-11-19T06:30")
    assert job["payload"]["lead_minutes"] == 30


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


@pytest.mark.asyncio
async def test_lead_reminder_fires_early_with_lead_wording(tmp_path: Path):
    clock = FakeClock(datetime(2026, 11, 18, 0, 0))
    scheduler, service, notifier = _make_service(tmp_path, clock)
    created = service.create("u1", "3分钟后提前1分钟提醒我喝水")
    assert created["lead_minutes"] == 1

    def settled(predicate):
        def check() -> bool:
            if not predicate():
                return False
            return not any(
                job["status"] == RUNNING for job in scheduler.list_jobs(None, 50)
            )

        return check

    # 推进 121 秒：刚过响铃时刻（事件在 3 分钟后、提前 1 分钟 = 2 分钟处）
    clock.advance(121)
    fired = await _drive(
        scheduler,
        settled(
            lambda: bool(notifier.calls)
            and (service.get("u1", created["id"]) or {}).get("status") == "completed"
        ),
    )
    assert fired, "提前量提醒没有在响铃时刻触发"
    assert len(notifier.calls) == 1
    assert "喝水" in notifier.calls[0][1]
    assert "提前 1 分钟" in notifier.calls[0][1]


@pytest.mark.asyncio
async def test_no_lead_still_fires_at_event_time(tmp_path: Path):
    clock = FakeClock(datetime(2026, 11, 18, 0, 0))
    scheduler, service, notifier = _make_service(tmp_path, clock)
    created = service.create("u1", "1分钟后提醒我喝水")
    assert created["lead_minutes"] == 0
    clock.advance(61)
    fired = await _drive(
        scheduler,
        lambda: bool(notifier.calls)
        and (service.get("u1", created["id"]) or {}).get("status") == "completed",
    )
    assert fired
    assert "提前" not in notifier.calls[0][1]
