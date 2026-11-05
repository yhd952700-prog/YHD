"""提醒命令行入口（``python -m src.reminders``）的只读行为验证。

覆盖：
* list 能分清 正常 / 已错过 / 已完成 / 已取消；
* failures 同时给出失败投递原因与死信；
* history 给出触发时间与处理结果（完成/稍后/未处理）；
* --json 结构稳定；
* 三条命令跑完数据库一行都不变（只读）；
* 用户要求的确切命令形式 ``python -m src.reminders`` 能直接跑。
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.tasks.local_scheduler import RUNNING, LocalScheduler
from src.tasks.reminder_time import ReminderSpec
from src.tasks.reminders import (
    DIGEST_REMINDER_ID,
    REMINDER_FIRE_JOB,
    ReminderStore,
)
from src.tasks.reminders_cli import main

REPO_ROOT = Path(__file__).resolve().parents[2]
TZ = timezone(timedelta(hours=8))


def _use_temp_db(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LIUHAO_REMINDERS_DB", str(tmp_path / "reminders.db"))
    monkeypatch.setenv(
        "LIUHAO_LOCAL_SCHEDULER_DB", str(tmp_path / "local_scheduler.db")
    )
    monkeypatch.setenv("LIUHAO_TIMEZONE_OFFSET_HOURS", "8")


def _store(tmp_path: Path) -> ReminderStore:
    return ReminderStore(str(tmp_path / "reminders.db"))


def _spec(
    title: str,
    run_at: datetime,
    *,
    kind: str = "once",
    hour: int | None = None,
    minute: int | None = None,
    weekday: int | None = None,
) -> ReminderSpec:
    return ReminderSpec(
        title=title,
        run_at=run_at,
        repeat_kind=kind,
        weekday=weekday,
        hour=hour,
        minute=minute,
        time_text="",
        source_text=title,
    )


def _create(
    store: ReminderStore,
    *,
    reminder_id: str,
    user_id: str,
    spec: ReminderSpec,
    now: datetime,
) -> dict:
    return store.create(
        reminder_id=reminder_id,
        user_id=user_id,
        spec=spec,
        now_iso=now.isoformat(),
    )


def _record(
    store: ReminderStore,
    *,
    reminder_id: str,
    user_id: str,
    occurrence: str,
    status: str,
    now: datetime,
    error: str | None = None,
) -> int:
    assert store.record_delivery(
        reminder_id=reminder_id,
        user_id=user_id,
        occurrence=occurrence,
        status=status,
        channels=[],
        error=error,
        now_iso=now.isoformat(),
    )
    return int(store.list_deliveries(user_id, limit=1)[0]["id"])


def _seed(tmp_path: Path) -> ReminderStore:
    store = _store(tmp_path)
    now = datetime(2026, 1, 1, 2, 0)  # UTC 02:00 = 本地 10:00

    _create(
        store,
        reminder_id="active1",
        user_id="u1",
        spec=_spec("喝水", datetime(2026, 1, 1, 3, 0)),
        now=now,
    )
    _create(
        store,
        reminder_id="daily1",
        user_id="u1",
        spec=_spec(
            "每天十点开会", datetime(2026, 1, 1, 2, 0), kind="daily",
            hour=10, minute=0,
        ),
        now=now,
    )
    _create(
        store,
        reminder_id="cancel1",
        user_id="u1",
        spec=_spec("旧提醒", datetime(2026, 1, 1, 4, 0)),
        now=now,
    )
    assert store.cancel("cancel1", "u1", now.isoformat())

    _create(
        store,
        reminder_id="missed1",
        user_id="u1",
        spec=_spec("交周报", datetime(2026, 1, 1, 1, 0)),
        now=now,
    )
    _record(
        store,
        reminder_id="missed1",
        user_id="u1",
        occurrence="2026-01-01T01:00:00",
        status="missed",
        now=now,
    )
    store.mark_fired("missed1", now.isoformat(), next_run_at=None, now_iso=now.isoformat())

    _create(
        store,
        reminder_id="done1",
        user_id="u1",
        spec=_spec("倒垃圾", datetime(2026, 1, 1, 0, 30)),
        now=now,
    )
    _record(
        store,
        reminder_id="done1",
        user_id="u1",
        occurrence="2026-01-01T00:30:00",
        status="delivered",
        now=now,
    )
    store.mark_fired("done1", now.isoformat(), next_run_at=None, now_iso=now.isoformat())

    # 每天十点这条在 10:00 错过过一次，但仍然是 active（明天还会响）。
    _record(
        store,
        reminder_id="daily1",
        user_id="u1",
        occurrence="2026-01-01T02:00:00",
        status="missed",
        now=now,
    )
    return store


def test_list_distinguishes_all_statuses(tmp_path, monkeypatch, capsys):
    _use_temp_db(tmp_path, monkeypatch)
    store = _seed(tmp_path)
    store.close()

    assert main(["list"]) == 0
    out = capsys.readouterr().out
    assert "提醒共 5 条" in out
    assert "正常 2" in out and "已错过 1" in out
    assert "已完成 1" in out and "已取消 1" in out
    assert "喝水" in out and "每天 10:00" in out
    assert "交周报" in out and "旧提醒" in out and "倒垃圾" in out
    assert "[已错过]" in out and "[已取消]" in out and "[已完成]" in out


def test_list_json_is_machine_readable(tmp_path, monkeypatch, capsys):
    _use_temp_db(tmp_path, monkeypatch)
    store = _seed(tmp_path)
    store.close()

    assert main(["list", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "list"
    assert payload["count"] == 5
    assert payload["counts"] == {
        "total": 5, "normal": 2, "missed": 1, "completed": 1, "cancelled": 1,
    }
    by_id = {item["id"]: item for item in payload["reminders"]}
    assert by_id["missed1"]["status_label"] == "已错过"
    assert by_id["missed1"]["next_run_at_local"] is None
    assert by_id["daily1"]["missed_count"] == 1
    assert by_id["active1"]["next_run_at_local"].startswith("2026-01-01 11:00")
    assert by_id["active1"]["repeat_label"] == "一次"


def test_list_can_filter_one_user(tmp_path, monkeypatch, capsys):
    _use_temp_db(tmp_path, monkeypatch)
    store = _seed(tmp_path)
    _create(
        store,
        reminder_id="other1",
        user_id="u2",
        spec=_spec("别人的提醒", datetime(2026, 1, 1, 3, 0)),
        now=datetime(2026, 1, 1, 2, 0),
    )
    store.close()

    assert main(["list", "--user", "u2"]) == 0
    out = capsys.readouterr().out
    assert "别人的提醒" in out
    assert "喝水" not in out


@pytest.mark.asyncio
async def test_failures_shows_reason_and_dead_letters(tmp_path, monkeypatch, capsys):
    _use_temp_db(tmp_path, monkeypatch)
    store = _seed(tmp_path)
    now = datetime(2026, 1, 1, 2, 0)
    _record(
        store,
        reminder_id="active1",
        user_id="u1",
        occurrence="2026-01-01T03:00:00",
        status="failed",
        now=now,
        error="PowerShell: 托盘气泡不可用",
    )
    store.close()

    scheduler = LocalScheduler(
        str(tmp_path / "local_scheduler.db"),
        max_concurrency=1,
        tick_seconds=0.01,
        now_fn=lambda: now,
        audit_sink=lambda action, outcome, details: None,
    )

    async def boom(**payload):
        raise RuntimeError("通知通道全部失败")

    scheduler.register(REMINDER_FIRE_JOB, boom)
    scheduler.enqueue(
        REMINDER_FIRE_JOB,
        run_at=now,
        job_key="dead-1",
        payload={"user_id": "u1", "reminder_id": "active1", "title": "喝水"},
        max_attempts=1,
        backoff_seconds=0.0,
    )
    runner = asyncio.create_task(scheduler.run())
    try:
        deadline = time.monotonic() + 2.0
        while scheduler.dead_letter_count() == 0 and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        assert scheduler.dead_letter_count() == 1
        while any(job["status"] == RUNNING for job in scheduler.list_jobs(RUNNING, 50)):
            await asyncio.sleep(0.01)
    finally:
        scheduler.stop()
        await asyncio.wait_for(runner, timeout=3.0)
    scheduler.close()

    assert main(["failures"]) == 0
    out = capsys.readouterr().out
    assert "失败/不明投递 1 条，死信 1 个" in out
    assert "托盘气泡不可用" in out
    assert "通知通道全部失败" in out
    assert "重试 1/1 后放弃" in out

    assert main(["failures", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["count"] == 2
    assert payload["failed_deliveries"][0]["reason"].startswith("PowerShell")
    assert payload["dead_letter_jobs"][0]["reason"] == "RuntimeError: 通知通道全部失败"


def test_history_shows_ack_results_and_skips_non_events(tmp_path, monkeypatch, capsys):
    _use_temp_db(tmp_path, monkeypatch)
    store = _store(tmp_path)
    now = datetime(2026, 1, 1, 2, 0)
    for index, title in enumerate(("已做", "稍后", "没管", "错过", "失败", "延后")):
        _create(
            store,
            reminder_id=f"h{index}",
            user_id="u1",
            spec=_spec(title, now + timedelta(minutes=index)),
            now=now,
        )

    done_id = _record(
        store, reminder_id="h0", user_id="u1",
        occurrence="2026-01-01T02:00:00", status="delivered", now=now,
    )
    assert store.ack_delivery(done_id, "u1", "done", now.isoformat())
    snooze_id = _record(
        store, reminder_id="h1", user_id="u1",
        occurrence="2026-01-01T02:01:00", status="delivered", now=now,
    )
    assert store.ack_delivery(snooze_id, "u1", "snoozed", now.isoformat())
    _record(
        store, reminder_id="h2", user_id="u1",
        occurrence="2026-01-01T02:02:00", status="delivered", now=now,
    )
    _record(
        store, reminder_id="h3", user_id="u1",
        occurrence="2026-01-01T02:03:00", status="missed", now=now,
    )
    _record(
        store, reminder_id="h4", user_id="u1",
        occurrence="2026-01-01T02:04:00", status="failed", now=now, error="boom",
    )
    _record(
        store, reminder_id="h5", user_id="u1",
        occurrence="2026-01-01T02:05:00", status="deferred", now=now,
    )
    _record(
        store, reminder_id=DIGEST_REMINDER_ID, user_id="u1",
        occurrence="digest:1-2", status="delivered", now=now,
    )
    store.close()

    assert main(["history", "--limit", "10"]) == 0
    out = capsys.readouterr().out
    assert "最近 4 条触发记录" in out
    assert "[已完成] 2026-01-01 10:00  已做" in out
    assert "[稍后提醒]" in out and "稍后" in out
    assert "[未处理]" in out and "没管" in out
    assert "错过" in out and "（已错过）" in out
    assert "失败" not in out and "延后" not in out and "错过提醒汇总" not in out

    assert main(["history", "--json", "--limit", "2"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "history"
    assert payload["count"] == 2
    assert [item["title"] for item in payload["records"]] == ["错过", "没管"]
    assert payload["records"][0]["ack_label"] == "未处理"


def test_commands_are_read_only(tmp_path, monkeypatch, capsys):
    _use_temp_db(tmp_path, monkeypatch)
    store = _seed(tmp_path)
    before_reminders = store.list("u1", limit=100)
    before_deliveries = store.list_deliveries("u1", limit=100)
    before_settings = store.get_settings()
    before_counts = store.delivery_status_counts()
    store.close()

    for argv in (["list"], ["failures"], ["history", "--limit", "10"]):
        assert main(argv) == 0
    capsys.readouterr()

    store = ReminderStore(str(tmp_path / "reminders.db"))
    try:
        assert store.list("u1", limit=100) == before_reminders
        assert store.list_deliveries("u1", limit=100) == before_deliveries
        assert store.get_settings() == before_settings
        assert store.delivery_status_counts() == before_counts
        assert store.distinct_users() == ["u1"]
    finally:
        store.close()


def test_exact_module_command_works(tmp_path, monkeypatch):
    _use_temp_db(tmp_path, monkeypatch)
    store = _seed(tmp_path)
    store.close()

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        [sys.executable, "-m", "src.reminders", "list", "--json"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["command"] == "list"
    assert payload["count"] == 5
    assert any(item["title"] == "喝水" for item in payload["reminders"])


def test_missing_database_is_reported_without_creating_it(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("LIUHAO_REMINDERS_DB", str(tmp_path / "nope.db"))
    monkeypatch.setenv(
        "LIUHAO_LOCAL_SCHEDULER_DB", str(tmp_path / "nope_sched.db")
    )
    monkeypatch.setenv("LIUHAO_TIMEZONE_OFFSET_HOURS", "8")

    assert main(["list"]) == 0
    out = capsys.readouterr().out
    assert "还没有提醒数据" in out
    assert not (tmp_path / "nope.db").exists()
