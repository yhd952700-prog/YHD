"""24 小时等效的排程稳定性验证（时间压缩，假时钟）。

一天里会有：大量一次性提醒到点、一段"关机"导致的错过、用户取消、以及
两次进程重启。目标只有一个：**零漏排、零重复响** —— 每个应该响的 occurrence
在 SQLite 里恰好有一条 delivered/missed 记录，错过的合并成一条摘要。

做法：
* 假时钟从本地 00:00 走到次日 00:00，每 5 分钟一个 tick（共 288 个时间点）；
* 287 条一次性提醒（每 5 分钟一条）+ 2 条每周 + 3 条每天；
* 每 8 条取消 1 条（共 36 条）；
* 本地 07:55–10:00 不跑排程（模拟关机），10:00 重启后这批全部记为错过；
* 08:00、16:00 各重启一次（新 LocalScheduler/ReminderStore，同一个库）；
* 12:30 取消一条已经响过一次的每日提醒。

所有断言都基于 SQLite 投递记录，不用内存计数。
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.tasks.local_scheduler import (
    PENDING,
    RUNNING,
    LocalScheduler,
)
from src.tasks.reminder_notify import NotifyResult
from src.tasks.reminders import (
    DIGEST_REMINDER_ID,
    REMINDER_DIGEST_JOB,
    REMINDER_FIRE_JOB,
    ReminderService,
    ReminderStore,
    reminder_key_prefix,
)

TZ = timezone(timedelta(hours=8))
DAY_START_LOCAL = datetime(2026, 1, 1, 0, 0, tzinfo=TZ)  # 周四
USER = "u1"


class FakeClock:
    def __init__(self, start: datetime) -> None:
        self.current = start

    def __call__(self) -> datetime:
        return self.current

    def set_local(self, minutes: int) -> None:
        self.current = (
            DAY_START_LOCAL + timedelta(minutes=minutes)
        ).astimezone(timezone.utc).replace(tzinfo=None)


class RecordingNotifier:
    channel = "test"

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def notify(
        self, title: str, body: str, *, idempotency_key: str | None = None
    ) -> list[NotifyResult]:
        self.calls.append((title, body))
        return [NotifyResult(self.channel, True, "")]


def _utc(minutes: int) -> datetime:
    return (DAY_START_LOCAL + timedelta(minutes=minutes)).astimezone(
        timezone.utc
    ).replace(tzinfo=None)


def _utc_iso(minutes: int) -> str:
    return _utc(minutes).isoformat()


def _open_worker(tmp_path: Path, clock: FakeClock, notifier: RecordingNotifier):
    scheduler = LocalScheduler(
        str(tmp_path / "local_scheduler.db"),
        max_concurrency=1,
        tick_seconds=0.002,
        now_fn=clock,
        audit_sink=lambda action, outcome, details: None,
    )
    store = ReminderStore(str(tmp_path / "reminders.db"))
    service = ReminderService(scheduler, store=store, notifier=notifier, tz=TZ)
    service.update_settings(
        {"quiet_enabled": False, "missed_threshold_minutes": 1}
    )

    async def fire(**payload):
        return await asyncio.to_thread(service.handle_fire, payload)

    async def digest(**payload):
        return await asyncio.to_thread(service.handle_digest, payload)

    scheduler.register(REMINDER_FIRE_JOB, fire)
    scheduler.register(REMINDER_DIGEST_JOB, digest)
    return scheduler, store, service


async def _drain(scheduler: LocalScheduler, clock: FakeClock) -> None:
    """把当前假时刻所有到期任务跑完，避免下一跳把正常任务算成迟到。"""
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        now_iso = clock().isoformat()
        due = [
            job
            for job in scheduler.list_jobs(PENDING, 500)
            if job["run_at"] <= now_iso
        ]
        running = scheduler.list_jobs(RUNNING, 500)
        if not due and not running:
            return
        await asyncio.sleep(0.002)
    raise AssertionError(
        f"drain timeout: due={len(due)} running={len(running)}"
    )


async def _run_segment(
    scheduler: LocalScheduler,
    clock: FakeClock,
    start_minute: int,
    end_minute: int,
    *,
    step_minutes: int = 5,
    at_minute=None,
) -> None:
    runner = asyncio.create_task(scheduler.run())
    try:
        for minute in range(start_minute, end_minute + 1, step_minutes):
            clock.set_local(minute)
            await _drain(scheduler, clock)
            if at_minute is not None:
                await at_minute(minute)
    finally:
        scheduler.stop()
        await asyncio.wait_for(runner, timeout=5.0)


def _stop_worker(scheduler, store) -> None:
    scheduler.close()
    store.close()


async def test_24h_equivalent_schedule_has_no_missing_or_duplicate_fires(tmp_path):
    clock = FakeClock(_utc(0))
    notifier = RecordingNotifier()
    scheduler, store, service = _open_worker(tmp_path, clock, notifier)

    # ---- 建数据：287 条一次性 + 3 条每天 + 2 条每周 ----------------------
    one_offs: dict[str, int] = {}
    for index, minute in enumerate(range(5, 1440, 5)):
        created = service.create(
            USER, f"{minute}分钟后提醒我R{index:03d}", now=_utc(0)
        )
        one_offs[created["id"]] = minute

    daily = [
        service.create(USER, f"每天{h}点提醒我D{i}", now=_utc(0))["id"]
        for i, h in enumerate(("九", "十二", "二十一"), start=1)
    ]
    weekly = [
        service.create(USER, f"每周四十八点提醒我W{i}", now=_utc(0))["id"]
        for i in (1, 2)
    ]

    # 每 8 条取消 1 条（共 36 条）。
    cancelled: set[str] = set()
    for index, (reminder_id, _minute) in enumerate(one_offs.items()):
        if index % 8 == 0:
            result = service.cancel(USER, reminder_id)
            assert result["status"] == "cancelled"
            cancelled.add(reminder_id)

    # ---- 第一段：00:00–07:55 正常跑 ------------------------------------
    await _run_segment(scheduler, clock, 0, 475)

    # ---- 关机 07:55–10:00：不跑排程，时钟直接跳到 10:00 -----------------
    _stop_worker(scheduler, store)
    clock.set_local(600)

    # ---- 重启 #1：10:00 开机，错过合并成摘要 ---------------------------
    scheduler, store, service = _open_worker(tmp_path, clock, notifier)
    await _run_segment(
        scheduler,
        clock,
        600,
        955,
        at_minute=_cancel_daily_at(service, daily[1], 750),
    )

    # ---- 重启 #2：16:00 换一个进程接着跑到次日 00:00 --------------------
    _stop_worker(scheduler, store)
    scheduler, store, service = _open_worker(tmp_path, clock, notifier)
    await _run_segment(scheduler, clock, 960, 1440)

    # ---- 核对：每个 occurrence 恰好一条记录 ------------------------------
    read_store = ReminderStore(str(tmp_path / "reminders.db"))
    try:
        deliveries = read_store.list_deliveries(USER, limit=1000)
        reminder_rows = {
            row["id"]: row for row in read_store.list(USER, limit=1000)
        }

        triggered = [
            row for row in deliveries if row["status"] in ("delivered", "missed")
            and row["reminder_id"] != DIGEST_REMINDER_ID
        ]
        actual = {
            (row["reminder_id"], row["occurrence"]): row["status"]
            for row in triggered
        }
        assert len(actual) == len(triggered), "同一个 occurrence 被记了两次"

        expected: dict[tuple[str, str], str] = {}
        missed_window = range(480, 600)
        for reminder_id, minute in one_offs.items():
            if reminder_id in cancelled:
                continue
            expected[(reminder_id, _utc_iso(minute))] = (
                "missed" if minute in missed_window else "delivered"
            )
        for reminder_id, hour_minute in zip(daily, (540, 720, 1260)):
            expected[(reminder_id, _utc_iso(hour_minute))] = (
                "missed" if hour_minute in missed_window else "delivered"
            )
        for reminder_id in weekly:
            expected[(reminder_id, _utc_iso(1080))] = "delivered"

        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        assert not missing, f"漏排 {len(missing)} 条：{missing[:5]}"
        assert not extra, f"多响/重复 {len(extra)} 条：{extra[:5]}"
        assert actual == expected, "状态与预期不一致（正常/错过判定错了）"

        # 取消的提醒一条都不应该响。
        for reminder_id in cancelled:
            assert reminder_id not in {row["reminder_id"] for row in triggered}
            assert reminder_rows[reminder_id]["status"] == "cancelled"

        missed = [row for row in triggered if row["status"] == "missed"]
        delivered = [row for row in triggered if row["status"] == "delivered"]

        # 错过合并成一条摘要（不是 21+3 条弹窗）。
        digests = [
            row for row in deliveries
            if row["reminder_id"] == DIGEST_REMINDER_ID
            and row["status"] == "delivered"
        ]
        assert len(digests) == 1, f"摘要应只有 1 条，实际 {len(digests)}"
        digest_calls = [
            body for title, body in notifier.calls if "错过的提醒" in title
        ]
        assert len(digest_calls) == 1
        assert f"{len(missed)} 条错过的提醒" in digest_calls[0]

        # 正常提醒每条恰好一次通知，加上 1 条摘要；没有重复响。
        normal_calls = [
            body for title, body in notifier.calls if "错过的提醒" not in title
        ]
        assert len(normal_calls) == len(delivered)
        assert len(notifier.calls) == len(delivered) + 1

        # 一次性提醒全部走完；重复提醒仍在 active 且只排着下一次。
        for reminder_id in one_offs:
            if reminder_id not in cancelled:
                assert reminder_rows[reminder_id]["status"] == "completed"
        pending = scheduler.list_jobs(PENDING, 500)
        for reminder_id in [daily[0], daily[2], *weekly]:
            assert reminder_rows[reminder_id]["status"] == "active"
            assert len([
                job for job in pending
                if job["job_key"].startswith(reminder_key_prefix(reminder_id))
            ]) == 1
        assert reminder_rows[daily[1]]["status"] == "cancelled"

        print(
            "[stability] 24h-equivalent: "
            f"reminders={len(one_offs) + len(daily) + len(weekly)}, "
            f"cancelled={len(cancelled)}, "
            f"occurrences={len(actual)}, delivered={len(delivered)}, "
            f"missed={len(missed)}, digest=1, missing=0, duplicate=0"
        )
    finally:
        read_store.close()
        scheduler.close()
        store.close()


def _cancel_daily_at(service: ReminderService, reminder_id: str, minute: int):
    async def callback(current_minute: int) -> None:
        if current_minute == minute:
            result = service.cancel(USER, reminder_id)
            assert result["status"] == "cancelled"

    return callback
