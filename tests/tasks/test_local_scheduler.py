"""进程内本地排程器的真实行为验证。

要求（逐条对应用户需求）：
* 到什么时间做什么 —— `delay_seconds` / `run_at` 到点才执行；
* 失败重试，指数退避且有上限；
* 重试耗尽落地成死信，可查询、有审计事件，不静默消失；
* 进程重启后到期任务续上（同库新实例 + 真实子进程重启）；
* 状态在 SQLite，不在内存字典；
* 并发上限、同一 job_name 不并发；
* 同 job_key 幂等入队；
* handler 未注册 → 死信，不静默卡住。

用假时钟驱动，避免测试依赖真实等待；执行并发用真实 asyncio.sleep 观测峰值。
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.tasks import _queue_paths
from src.tasks import local_scheduler as local_scheduler_module
from src.tasks.local_handlers import (
    HEARTBEAT_KEY_PREFIX,
    LOCAL_HEARTBEAT_JOB,
    local_heartbeat,
    register_local_jobs,
    seed_local_jobs,
)
from src.tasks.local_scheduler import (
    CANCELLED,
    COMPLETED,
    DEAD,
    PENDING,
    RETRY_WAIT,
    RUNNING,
    LocalScheduler,
    QueueFull,
    default_max_concurrency,
    default_scheduler_db,
    local_scheduler_enabled,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


class FakeClock:
    """Deterministic naive-UTC clock, same format as ``src._time.utc_now``."""

    def __init__(self, start: datetime | None = None) -> None:
        self.current = start or datetime(2026, 1, 1, 0, 0, 0)

    def __call__(self) -> datetime:
        return self.current

    def advance(self, seconds: float) -> None:
        self.current = self.current + timedelta(seconds=seconds)


def _scheduler(tmp_path: Path, clock: FakeClock, **kwargs) -> LocalScheduler:
    kwargs.setdefault("max_concurrency", 2)
    kwargs.setdefault("tick_seconds", 0.01)
    kwargs.setdefault("audit_sink", lambda action, outcome, details: None)
    return LocalScheduler(str(tmp_path / "sched.db"), now_fn=clock, **kwargs)


async def _drive(scheduler: LocalScheduler, predicate=None, *, timeout: float = 3.0) -> bool:
    """Run the scheduler loop until ``predicate`` is true (or timeout)."""
    runner = asyncio.create_task(scheduler.run())
    try:
        if predicate is None:
            await asyncio.sleep(timeout)
            return True
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            await asyncio.sleep(0.01)
        return False
    finally:
        scheduler.stop()
        await asyncio.wait_for(runner, timeout=2.0)


# ----------------------------------------------------------------- scheduling


async def test_job_runs_only_after_scheduled_time(tmp_path):
    clock = FakeClock()
    sched = _scheduler(tmp_path, clock)
    calls: list[str] = []

    async def handler(tag: str = "") -> str:
        calls.append(tag)
        return tag

    sched.register("tell_time", handler)
    job_id = sched.enqueue("tell_time", delay_seconds=30, payload={"tag": "early"})

    assert await _drive(sched, lambda: bool(calls), timeout=0.5) is False
    assert sched.get_job(job_id)["status"] == PENDING

    clock.advance(31)
    assert await _drive(sched, lambda: bool(calls), timeout=3.0)
    assert calls == ["early"]
    job = sched.get_job(job_id)
    assert job["status"] == COMPLETED
    assert job["attempts"] == 1
    assert job["result"] == "early"
    sched.close()


async def test_run_at_accepts_explicit_time_and_aware_datetime(tmp_path):
    clock = FakeClock()
    sched = _scheduler(tmp_path, clock)
    ran: list[str] = []

    async def handler() -> None:
        ran.append("x")

    sched.register("point_in_time", handler)
    aware = (clock() + timedelta(seconds=10)).replace(tzinfo=timezone.utc)
    job_id = sched.enqueue("point_in_time", run_at=aware)

    # 带时区的输入被归一化成 naive UTC，否则字符串比较会错。
    assert sched.get_job(job_id)["run_at"] == (clock() + timedelta(seconds=10)).isoformat()
    clock.advance(11)
    assert await _drive(sched, lambda: bool(ran), timeout=3.0)
    sched.close()


# ------------------------------------------------------------ retry / backoff


async def test_retry_exponential_backoff_with_cap_then_dead_letter(tmp_path):
    clock = FakeClock()
    events: list[tuple[str, str, dict]] = []
    sched = _scheduler(
        tmp_path,
        clock,
        audit_sink=lambda action, outcome, details: events.append((action, outcome, details)),
    )
    attempts: list[str] = []

    async def always_fails() -> None:
        attempts.append("fail")
        raise RuntimeError("boom")

    sched.register("flaky", always_fails)
    job_id = sched.enqueue(
        "flaky",
        job_key="flaky-1",
        max_attempts=3,
        backoff_seconds=1.0,
        backoff_multiplier=2.0,
        max_backoff_seconds=3.0,
    )

    # 第 1 次失败 → retry_wait，退避 1s
    assert await _drive(sched, lambda: len(attempts) == 1, timeout=3.0)
    job = sched.get_job(job_id)
    assert job["status"] == RETRY_WAIT and job["attempts"] == 1
    assert job["run_at"] == (clock() + timedelta(seconds=1)).isoformat()

    # 第 2 次失败 → 退避 2s
    clock.advance(1)
    assert await _drive(sched, lambda: len(attempts) == 2, timeout=3.0)
    job = sched.get_job(job_id)
    assert job["status"] == RETRY_WAIT and job["attempts"] == 2
    assert job["run_at"] == (clock() + timedelta(seconds=2)).isoformat()

    # 第 3 次失败 → 耗尽，落地死信
    clock.advance(2)
    assert await _drive(sched, lambda: sched.dead_letter_count() == 1, timeout=3.0)
    assert len(attempts) == 3
    dead = sched.list_dead_letters()
    assert [item["id"] for item in dead] == [job_id]
    assert dead[0]["status"] == DEAD
    assert dead[0]["attempts"] == 3
    assert "boom" in dead[0]["last_error"]
    assert dead[0]["dead_lettered_at"] is not None
    assert events == [
        (
            "local_scheduler.dead_letter",
            "failure",
            {
                "job_id": job_id,
                "job_key": "flaky-1",
                "job_name": "flaky",
                "attempts": 3,
                "max_attempts": 3,
                "error": "RuntimeError: boom",
            },
        )
    ]
    sched.close()


async def test_backoff_is_capped(tmp_path):
    clock = FakeClock()
    sched = _scheduler(tmp_path, clock)
    failures: list[str] = []

    async def always_fails() -> None:
        failures.append("x")
        raise RuntimeError("nope")

    sched.register("capped", always_fails)
    job_id = sched.enqueue(
        "capped",
        max_attempts=3,
        backoff_seconds=100.0,
        backoff_multiplier=10.0,
        max_backoff_seconds=150.0,
    )

    assert await _drive(sched, lambda: len(failures) == 1, timeout=3.0)
    assert sched.get_job(job_id)["run_at"] == (clock() + timedelta(seconds=100)).isoformat()

    clock.advance(100)
    assert await _drive(sched, lambda: len(failures) == 2, timeout=3.0)
    # 100 * 10 = 1000 被上限 150 截断
    assert sched.get_job(job_id)["run_at"] == (clock() + timedelta(seconds=150)).isoformat()
    assert sched.get_job(job_id)["status"] == RETRY_WAIT
    sched.close()


# --------------------------------------------------------------- dead letters


async def test_unregistered_handler_goes_straight_to_dead_letter(tmp_path):
    clock = FakeClock()
    sched = _scheduler(tmp_path, clock)
    job_id = sched.enqueue("not_registered", max_attempts=5)

    assert await _drive(sched, lambda: sched.dead_letter_count() == 1, timeout=3.0)
    job = sched.get_job(job_id)
    assert job["status"] == DEAD
    assert "handler not registered: not_registered" in job["last_error"]
    assert job["attempts"] == 1  # 配置错误不该白烧重试次数
    sched.close()


# ------------------------------------------------------------ restart / lease


async def test_due_job_survives_instance_restart(tmp_path):
    clock = FakeClock()
    first = _scheduler(tmp_path, clock)
    job_id = first.enqueue("persisted", delay_seconds=10, payload={"tag": "after-restart"})
    first.close()

    ran: list[str] = []

    async def handler(tag: str = "") -> None:
        ran.append(tag)

    second = _scheduler(tmp_path, clock)
    second.register("persisted", handler)
    clock.advance(11)
    assert await _drive(second, lambda: second.get_job(job_id)["status"] == COMPLETED, timeout=3.0)
    assert ran == ["after-restart"]
    second.close()


async def test_startup_reclaims_job_left_running_by_crashed_process(tmp_path):
    clock = FakeClock()
    crashed = _scheduler(tmp_path, clock, lease_seconds=60)
    job_id = crashed.enqueue("crash_recovery", payload={"tag": "resumed"})
    claimed = crashed._claim_due(1)  # 模拟：领取后进程硬死，行停在 running
    assert [item["id"] for item in claimed] == [job_id]
    assert crashed.get_job(job_id)["status"] == "running"
    crashed.close()

    ran: list[str] = []

    async def handler(tag: str = "") -> None:
        ran.append(tag)

    restarted = _scheduler(tmp_path, clock, lease_seconds=60)
    restarted.register("crash_recovery", handler)
    # 启动即回收（force），不用等 60s 租约到期
    assert await _drive(restarted, lambda: bool(ran), timeout=3.0)
    assert ran == ["resumed"]
    assert restarted.get_job(job_id)["attempts"] == 2
    restarted.close()


async def test_expired_lease_is_requeued_and_exhausted_lease_is_dead(tmp_path):
    clock = FakeClock()
    sched = _scheduler(tmp_path, clock, lease_seconds=5)

    async def handler(tag: str = "") -> None:
        return None

    sched.register("lease_job", handler)
    job_id = sched.enqueue("lease_job", payload={"tag": "a"})
    sched._claim_due(1)
    assert sched.get_job(job_id)["status"] == "running"

    clock.advance(6)
    assert sched.recover_expired_leases() == 1
    job = sched.get_job(job_id)
    assert job["status"] == RETRY_WAIT
    assert "lease expired" in job["last_error"]

    assert await _drive(sched, lambda: sched.get_job(job_id)["status"] == COMPLETED, timeout=3.0)
    sched.close()

    # 最后一次尝试期间崩溃 → 租约过期直接死信，不会无限重来
    clock2 = FakeClock()
    sched2 = _scheduler(tmp_path / "dead", clock2, lease_seconds=5)
    sched2.register("one_shot", handler)
    job_id2 = sched2.enqueue("one_shot", max_attempts=1)
    sched2._claim_due(1)
    clock2.advance(6)
    assert sched2.recover_expired_leases() == 1
    dead = sched2.get_job(job_id2)
    assert dead["status"] == DEAD
    assert "lease expired" in dead["last_error"]
    sched2.close()


async def test_due_job_survives_real_process_restart(tmp_path):
    db = str(tmp_path / "restart.db")
    marker = tmp_path / "marker.txt"

    enqueue_script = (
        "import sys\n"
        "from src.tasks.local_scheduler import LocalScheduler\n"
        "s = LocalScheduler(sys.argv[1], max_concurrency=1,"
        " audit_sink=lambda *a, **k: None)\n"
        "s.enqueue('restart_probe', delay_seconds=1.0, payload={'tag': 'restart'})\n"
        "s.close()\n"
    )
    run_script = (
        "import asyncio, os, sys, time\n"
        "from src.tasks.local_scheduler import LocalScheduler\n"
        "db, marker = sys.argv[1], sys.argv[2]\n"
        "async def handler(tag=''):\n"
        "    open(marker, 'w', encoding='utf-8').write(tag)\n"
        "async def main():\n"
        "    s = LocalScheduler(db, max_concurrency=1, tick_seconds=0.05,"
        " audit_sink=lambda *a, **k: None)\n"
        "    s.register('restart_probe', handler)\n"
        "    task = asyncio.create_task(s.run())\n"
        "    deadline = time.monotonic() + 10\n"
        "    while time.monotonic() < deadline and not os.path.exists(marker):\n"
        "        await asyncio.sleep(0.05)\n"
        "    s.stop()\n"
        "    await asyncio.wait_for(task, timeout=2)\n"
        "    return 0 if os.path.exists(marker) else 3\n"
        "sys.exit(asyncio.run(main()))\n"
    )

    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT)

    def _run(script: str, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-c", script, *args],
            cwd=str(REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )

    produced = _run(enqueue_script, db)
    assert produced.returncode == 0, produced.stderr
    assert not marker.exists(), "job must not run before it is due"

    consumed = _run(run_script, db, str(marker))
    assert consumed.returncode == 0, consumed.stderr
    assert marker.read_text(encoding="utf-8") == "restart"


# ------------------------------------------------- concurrency / uniqueness


async def test_concurrency_limit_is_enforced(tmp_path):
    clock = FakeClock()
    sched = _scheduler(tmp_path, clock, max_concurrency=2)
    active = 0
    peak = 0
    completed = 0

    async def slow(tag: str = "") -> None:
        nonlocal active, peak, completed
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.1)
        active -= 1
        completed += 1

    sched.register("slow", slow)
    for index in range(5):
        sched.enqueue("slow", payload={"tag": f"j{index}"}, job_key=f"slow-{index}")

    assert await _drive(sched, lambda: completed == 5, timeout=5.0)
    assert peak <= 2, f"concurrency limit violated, peak={peak}"
    assert completed == 5
    sched.close()


async def test_same_job_name_never_runs_concurrently(tmp_path):
    clock = FakeClock()
    sched = _scheduler(tmp_path, clock, max_concurrency=4)
    active = 0
    peak = 0
    done = 0

    async def serialised(tag: str = "") -> None:
        nonlocal active, peak, done
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.1)
        active -= 1
        done += 1

    sched.register("serialised", serialised)
    sched.enqueue("serialised", payload={"tag": "first"})
    sched.enqueue("serialised", payload={"tag": "second"}, job_key="second")

    assert await _drive(sched, lambda: done == 2, timeout=5.0)
    assert peak == 1, f"same job_name ran concurrently, peak={peak}"
    sched.close()


async def test_enqueue_is_idempotent_while_job_is_active(tmp_path):
    clock = FakeClock()
    sched = _scheduler(tmp_path, clock)

    async def handler() -> None:
        return None

    sched.register("once", handler)
    first = sched.enqueue("once", job_key="dedupe-me", delay_seconds=10)
    second = sched.enqueue("once", job_key="dedupe-me", delay_seconds=10)
    assert first == second
    assert sched.stats() == {PENDING: 1}

    clock.advance(11)
    assert await _drive(sched, lambda: sched.get_job(first)["status"] == COMPLETED, timeout=3.0)
    third = sched.enqueue("once", job_key="dedupe-me")
    assert third != first  # 终态后可再次入队
    sched.close()


# ------------------------------------------------------------- config surface


async def test_heartbeat_job_chains_and_is_dedupe_safe(tmp_path):
    clock = FakeClock()
    events: list[tuple[str, str, dict]] = []
    sched = _scheduler(
        tmp_path,
        clock,
        audit_sink=lambda action, outcome, details: events.append((action, outcome, details)),
    )

    registered = register_local_jobs(sched)
    assert LOCAL_HEARTBEAT_JOB in registered
    # 提醒作业也由同一个排程器执行（不另起调度器）。
    assert "liuhao.reminder.fire" in registered
    seed_local_jobs(sched)

    def heartbeats() -> int:
        return sum(1 for event in events if event[0] == "local_scheduler.heartbeat")

    def heartbeat_pending() -> list[dict]:
        return [
            job
            for job in sched.list_jobs(PENDING, 50)
            if job["job_name"] == LOCAL_HEARTBEAT_JOB
        ]

    assert await _drive(sched, lambda: heartbeats() == 1, timeout=3.0)
    # 心跳完成后把下一次按「下一次运行时刻」入队 —— 且只有一条 pending
    pending = heartbeat_pending()
    assert len(pending) == 1
    assert pending[0]["job_key"].startswith(HEARTBEAT_KEY_PREFIX)
    assert pending[0]["run_at"] == (clock() + timedelta(seconds=300)).isoformat()

    # 同一时刻重复调用不会产生第二条（重试安全）
    first = await local_heartbeat(sched, 300.0)
    second = await local_heartbeat(sched, 300.0)
    assert first == second
    assert len(heartbeat_pending()) == 1

    before = heartbeats()
    clock.advance(300)
    assert await _drive(sched, lambda: heartbeats() == before + 1, timeout=3.0)
    assert len(heartbeat_pending()) == 1  # 第二次心跳又续上了下一条
    sched.close()


def test_default_paths_and_concurrency_env(monkeypatch, tmp_path):
    monkeypatch.setenv("LIUHAO_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.delenv("LIUHAO_DATA_DIR", raising=False)
    monkeypatch.delenv("LIUHAO_LOCAL_SCHEDULER_DB", raising=False)
    monkeypatch.delenv("LIUHAO_LOCAL_SCHEDULER_CONCURRENCY", raising=False)
    monkeypatch.delenv("LIUHAO_LOCAL_SCHEDULER", raising=False)

    assert default_scheduler_db() == os.path.join(str(tmp_path), "queue", "local_scheduler.db")
    assert default_max_concurrency() == 4
    assert local_scheduler_enabled() is True

    monkeypatch.setenv("LIUHAO_LOCAL_SCHEDULER_CONCURRENCY", "7")
    assert default_max_concurrency() == 7
    monkeypatch.setenv("LIUHAO_LOCAL_SCHEDULER_CONCURRENCY", "0")
    with pytest.raises(ValueError):
        default_max_concurrency()
    monkeypatch.setenv("LIUHAO_LOCAL_SCHEDULER_CONCURRENCY", "abc")
    with pytest.raises(ValueError):
        default_max_concurrency()

    monkeypatch.setenv("LIUHAO_LOCAL_SCHEDULER", "0")
    assert local_scheduler_enabled() is False
    monkeypatch.setenv("LIUHAO_LOCAL_SCHEDULER", "off")
    assert local_scheduler_enabled() is False
    monkeypatch.setenv("LIUHAO_LOCAL_SCHEDULER", "maybe")
    with pytest.raises(ValueError):
        local_scheduler_enabled()


# ------------------------------------------------- 超时 / 取消终态 / 可重入


async def test_hung_handler_times_out_and_goes_dead_not_permanently_running(tmp_path):
    """证据③：挂住的 handler 超时后判失败（死信），不能永远停在 running。"""
    clock = FakeClock()
    sched = _scheduler(tmp_path, clock)

    async def hang():
        await asyncio.sleep(0.6)

    sched.register("hang", hang)
    job_id = sched.enqueue(
        "hang", job_key="hang-1", timeout_seconds=0.05, max_attempts=1
    )

    assert await _drive(
        sched, lambda: sched.get_job(job_id)["status"] == DEAD, timeout=3.0
    )
    row = sched.get_job(job_id)
    assert row["status"] == DEAD
    assert row["attempts"] == 1
    assert "TimeoutError" in (row["last_error"] or "")
    sched.close()


async def test_cancelled_running_job_stays_cancelled(tmp_path):
    """证据④：被取消的在途任务，handler 结束后终态仍是 cancelled。"""
    clock = FakeClock()
    sched = _scheduler(tmp_path, clock, max_concurrency=1)
    started = asyncio.Event()
    release = asyncio.Event()

    async def block():
        started.set()
        await release.wait()

    sched.register("block", block, reentrant=True)
    job_id = sched.enqueue("block", job_key="cancel:1")
    runner = asyncio.create_task(sched.run())
    try:
        await asyncio.wait_for(started.wait(), timeout=2.0)
        assert sched.get_job(job_id)["status"] == RUNNING
        assert sched.cancel_jobs_by_prefix("cancel") == 1
        release.set()
        await asyncio.sleep(0.2)
        assert sched.get_job(job_id)["status"] == CANCELLED
    finally:
        sched.stop()
        await asyncio.wait_for(runner, timeout=3.0)
    # _complete 带 AND status='running'：handler 正常返回也不能覆盖 cancelled
    assert sched.get_job(job_id)["status"] == CANCELLED
    sched.close()


async def test_crash_after_side_effect_is_not_replayed_for_non_reentrant(tmp_path):
    """TOOL-04：默认不可重入；崩溃恢复后同一副作用只能出现一次。"""
    clock = FakeClock()
    sched = _scheduler(tmp_path, clock, lease_seconds=5.0)
    trace: list[str] = ["pre-crash"]

    async def side():
        trace.append("side")

    sched.register("side", side)  # 默认 reentrant=False
    await side()  # 崩溃前已经执行过一次副作用
    job_id = sched.enqueue("side", job_key="side-1")

    claimed = sched._claim_due(1)
    assert [job["id"] for job in claimed] == [job_id]
    sched._mark_executing(job_id)  # handler 已开始 → 副作用可能已发生

    clock.advance(6)
    assert sched.recover_expired_leases() == 1

    row = sched.get_job(job_id)
    assert row["status"] == DEAD
    assert row["attempts"] == 1  # attempts 与实际副作用次数对得上
    assert "不可重入" in (row["last_error"] or "")
    assert trace == ["pre-crash", "side"], trace  # 没有第二次 "side"
    sched.close()


async def test_reentrant_job_is_replayed_after_crash(tmp_path):
    """显式标 reentrant=True 的任务才允许崩溃后重试。"""
    clock = FakeClock()
    sched = _scheduler(tmp_path, clock, lease_seconds=5.0)

    async def side():
        return None

    sched.register("side", side, reentrant=True)
    job_id = sched.enqueue("side", job_key="side-r")
    sched._claim_due(1)
    sched._mark_executing(job_id)
    clock.advance(6)

    assert sched.recover_expired_leases() == 1
    assert sched.get_job(job_id)["status"] == RETRY_WAIT
    sched.close()


def test_queue_limit_rejects_new_jobs_but_keeps_idempotent_retry(tmp_path):
    clock = FakeClock()
    sched = _scheduler(tmp_path, clock, max_queue=1)
    first = sched.enqueue("q", job_key="first", delay_seconds=10)
    with pytest.raises(QueueFull):
        sched.enqueue("q", job_key="second", delay_seconds=10)
    # 队列满时，重复入队同一个 key 仍返回原 id（幂等不能被上限打断）
    assert sched.enqueue("q", job_key="first", delay_seconds=10) == first
    assert [job["id"] for job in sched.list_jobs(None, 20)] == [first]
    sched.close()


def test_different_concurrency_keys_do_not_serialize_same_job_name(tmp_path):
    clock = FakeClock()
    sched = _scheduler(tmp_path, clock, max_concurrency=2)
    a = sched.enqueue("reminder", job_key="r:1", concurrency_key="reminder:1")
    b = sched.enqueue("reminder", job_key="r:2", concurrency_key="reminder:2")
    claimed = sched._claim_due(2)
    assert {job["id"] for job in claimed} == {a, b}
    sched.close()


# ------------------------------------------------------- 旧库自动补列（升级路径）

#: 迁移前（新增 concurrency_key 等列之前）的建表语句。
_LEGACY_SCHEMA = """
CREATE TABLE IF NOT EXISTS local_jobs (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    job_key              TEXT NOT NULL,
    job_name             TEXT NOT NULL,
    payload              TEXT NOT NULL,
    status               TEXT NOT NULL,
    run_at               TEXT NOT NULL,
    attempts             INTEGER NOT NULL DEFAULT 0,
    max_attempts         INTEGER NOT NULL DEFAULT 3,
    backoff_seconds      REAL NOT NULL DEFAULT 2.0,
    backoff_multiplier   REAL NOT NULL DEFAULT 2.0,
    max_backoff_seconds  REAL NOT NULL DEFAULT 300.0,
    last_error           TEXT,
    result               TEXT,
    lease_owner          TEXT,
    lease_expires_at     TEXT,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL,
    completed_at         TEXT,
    dead_lettered_at     TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_local_jobs_active_key
    ON local_jobs(job_key) WHERE status IN ('pending', 'running', 'retry_wait');
CREATE INDEX IF NOT EXISTS idx_local_jobs_due
    ON local_jobs(status, run_at);
CREATE INDEX IF NOT EXISTS idx_local_jobs_name
    ON local_jobs(job_name, status);
"""


def _make_legacy_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(_LEGACY_SCHEMA)
        now = "2026-11-01T00:00:00"
        conn.execute(
            "INSERT INTO local_jobs (job_key, job_name, payload, status, run_at,"
            " attempts, max_attempts, backoff_seconds, backoff_multiplier,"
            " max_backoff_seconds, last_error, result, lease_owner, lease_expires_at,"
            " created_at, updated_at, completed_at, dead_lettered_at)"
            " VALUES ('legacy:1', 'liuhao.history.echo', '{}', 'completed', ?,"
            " 1, 3, 2.0, 2.0, 300.0, NULL, '\"legacy\"', NULL, NULL, ?, ?, ?, NULL)",
            (now, now, now, now),
        )
        conn.commit()
    finally:
        conn.close()


def test_legacy_db_is_auto_migrated_and_scheduler_starts(tmp_path):
    """升级用户真实路径：旧库缺 concurrency_key 等列，启动时必须自动补齐。"""
    db = tmp_path / "legacy.db"
    _make_legacy_db(db)
    before = {row[1] for row in sqlite3.connect(str(db)).execute("PRAGMA table_info(local_jobs)")}
    assert "concurrency_key" not in before

    sched = LocalScheduler(
        str(db), now_fn=FakeClock(datetime(2026, 11, 8)),
        audit_sink=lambda action, outcome, details: None,
    )
    try:
        cols = {row[1] for row in sched._conn.execute("PRAGMA table_info(local_jobs)")}
        assert set(local_scheduler_module._NEW_COLUMNS) <= cols
        indexes = {row[1] for row in sched._conn.execute("PRAGMA index_list(local_jobs)")}
        assert "idx_local_jobs_concurrency" in indexes

        # 历史任务与数据都还在
        legacy = sched.get_job(1)
        assert legacy["status"] == COMPLETED
        assert legacy["result"] == "legacy"

        # 新列真的可用：带 concurrency_key 的新任务能入队并读回
        job_id = sched.enqueue("new_job", delay_seconds=0, concurrency_key="reminder:1")
        assert sched.get_job(job_id)["concurrency_key"] == "reminder:1"
    finally:
        sched.close()


def test_migration_failure_says_scheduler_not_started(tmp_path, monkeypatch):
    """补列失败必须明确报「排程器未启动」，不能静默带病启动。"""
    db = tmp_path / "legacy.db"
    _make_legacy_db(db)
    broken = dict(local_scheduler_module._NEW_COLUMNS)
    broken["concurrency_key"] = "TEXT NOT NULL DEFAULT ("
    monkeypatch.setattr(local_scheduler_module, "_NEW_COLUMNS", broken)

    with pytest.raises(RuntimeError, match="本地排程器未启动"):
        LocalScheduler(
            str(db), now_fn=FakeClock(datetime(2026, 11, 8)),
            audit_sink=lambda action, outcome, details: None,
        )


# --------------------------------------------- 调度库跟随数据根（隔离缺口）


def _patch_data_root(monkeypatch, tmp_path: Path, legacy_dir: Path) -> Path:
    """``LIUHAO_DATA_DIR`` 生效 + 旧位置指向可控目录，返回新目标路径。"""
    data = tmp_path / "data"
    monkeypatch.setenv("LIUHAO_DATA_DIR", str(data))
    monkeypatch.delenv("LIUHAO_WORKSPACE_ROOT", raising=False)
    monkeypatch.delenv("LIUHAO_LOCAL_SCHEDULER_DB", raising=False)
    monkeypatch.setattr(_queue_paths, "legacy_queue_dir", lambda: legacy_dir)
    return data / "queue" / "local_scheduler.db"


def _new_scheduler(path: Path, clock: FakeClock | None = None) -> LocalScheduler:
    return LocalScheduler(
        str(path),
        now_fn=clock or FakeClock(datetime(2026, 11, 8)),
        audit_sink=lambda action, outcome, details: None,
    )


def _schema(path: Path) -> tuple[list, list]:
    conn = sqlite3.connect(str(path))
    try:
        cols = [row[1] for row in conn.execute("PRAGMA table_info(local_jobs)")]
        idx = [row[1] for row in conn.execute("PRAGMA index_list(local_jobs)")]
    finally:
        conn.close()
    return cols, idx


def _job_keys(path: Path) -> list[str]:
    conn = sqlite3.connect(str(path))
    try:
        return sorted(row[0] for row in conn.execute("SELECT job_key FROM local_jobs"))
    finally:
        conn.close()


def test_queue_db_follows_data_root_when_only_legacy_exists(tmp_path, monkeypatch):
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir()
    target = _patch_data_root(monkeypatch, tmp_path, legacy_dir)
    legacy_db = legacy_dir / "local_scheduler.db"
    sched = _new_scheduler(legacy_db)
    sched.enqueue("old_job", job_key="legacy:1", delay_seconds=0)
    sched.close()
    legacy_before = _schema(legacy_db)

    resolved = Path(default_scheduler_db())

    assert resolved == target, "旧位置有库时必须判定为目标位置"
    assert target.exists(), "旧库必须被迁移到数据根"
    assert legacy_db.exists(), "旧文件必须保留，不能删"
    assert _job_keys(target) == _job_keys(legacy_db) == ["legacy:1"]
    assert _schema(target) == legacy_before, "迁移副本的列与索引必须和旧库一致"


def test_queue_db_follows_data_root_when_only_new_exists(tmp_path, monkeypatch):
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir()
    target = _patch_data_root(monkeypatch, tmp_path, legacy_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    sched = _new_scheduler(target)
    sched.enqueue("new_job", job_key="new:1", delay_seconds=0)
    sched.close()

    assert Path(default_scheduler_db()) == target
    assert not (legacy_dir / "local_scheduler.db").exists()
    assert _job_keys(target) == ["new:1"]


def test_queue_db_keeps_new_when_both_locations_exist(tmp_path, monkeypatch):
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir()
    target = _patch_data_root(monkeypatch, tmp_path, legacy_dir)
    legacy_db = legacy_dir / "local_scheduler.db"
    for path, key in ((target, "new:1"), (legacy_db, "old:1")):
        path.parent.mkdir(parents=True, exist_ok=True)
        sched = _new_scheduler(path)
        sched.enqueue("job", job_key=key, delay_seconds=0)
        sched.close()
    legacy_digest = _queue_paths.file_digest(legacy_db)

    assert Path(default_scheduler_db()) == target
    assert _job_keys(target) == ["new:1"], "两边都有时必须以新位置为准"
    assert _queue_paths.file_digest(legacy_db) == legacy_digest, "旧文件不能被改写/删除"
    assert _job_keys(legacy_db) == ["old:1"]


def test_queue_db_initializes_when_neither_exists(tmp_path, monkeypatch):
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir()
    target = _patch_data_root(monkeypatch, tmp_path, legacy_dir)

    resolved = Path(default_scheduler_db())

    assert resolved == target
    assert not target.exists(), "两边都没有时不要凭空建库，交给 LocalScheduler 初始化"
    sched = _new_scheduler(target)
    sched.close()
    assert target.exists()


def test_queue_db_migration_failure_falls_back_to_legacy(tmp_path, monkeypatch):
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir()
    target = _patch_data_root(monkeypatch, tmp_path, legacy_dir)
    legacy_db = legacy_dir / "local_scheduler.db"
    sched = _new_scheduler(legacy_db)
    sched.enqueue("old_job", job_key="legacy:1", delay_seconds=0)
    sched.close()

    def _boom(source, target_path, *, kind):
        raise OSError("simulated copy failure")

    monkeypatch.setattr(_queue_paths, "_copy_and_verify", _boom)

    resolved = Path(default_scheduler_db())

    assert resolved == legacy_db, "迁移失败必须退回旧库，不能以空库启动"
    assert not target.exists(), "失败不能留下半截新文件"
    reopened = _new_scheduler(resolved)
    try:
        assert _job_keys(resolved) == ["legacy:1"]
    finally:
        reopened.close()


async def test_migrated_old_schema_db_adds_columns_then_index(tmp_path, monkeypatch):
    """迁移到旧结构库：补列必须在建新索引之前，不能在迁移路径上重演启动失败。"""
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir()
    target = _patch_data_root(monkeypatch, tmp_path, legacy_dir)
    legacy_db = legacy_dir / "local_scheduler.db"
    _make_legacy_db(legacy_db)
    conn = sqlite3.connect(str(legacy_db))
    try:
        conn.execute(
            "INSERT INTO local_jobs (job_key, job_name, payload, status, run_at,"
            " attempts, max_attempts, backoff_seconds, backoff_multiplier,"
            " max_backoff_seconds, created_at, updated_at)"
            " VALUES ('legacy:pending', 'after_upgrade', '{}', 'pending',"
            " '2026-11-08T08:59:00', 0, 3, 2.0, 2.0, 300.0,"
            " '2026-11-07T00:00:00', '2026-11-07T00:00:00')"
        )
        conn.commit()
    finally:
        conn.close()

    migrated = Path(default_scheduler_db())
    assert migrated == target
    sched = _new_scheduler(migrated, FakeClock(datetime(2026, 11, 8, 9, 0, 0)))
    calls: list[str] = []

    async def after_upgrade() -> None:
        calls.append("ran")

    sched.register("after_upgrade", after_upgrade)
    try:
        cols = {row[1] for row in sched._conn.execute("PRAGMA table_info(local_jobs)")}
        indexes = {row[1] for row in sched._conn.execute("PRAGMA index_list(local_jobs)")}
        assert set(local_scheduler_module._NEW_COLUMNS) <= cols
        assert "idx_local_jobs_concurrency" in indexes
        assert await _drive(sched, lambda: bool(calls), timeout=3.0)
        jobs = {job["job_key"]: job for job in sched.list_jobs(limit=100)}
        assert jobs["legacy:pending"]["status"] == COMPLETED
        assert jobs["legacy:1"]["status"] == COMPLETED
    finally:
        sched.close()


async def test_migrated_queue_db_keeps_schema_and_runs_pending_job(tmp_path, monkeypatch):
    """真实证据：旧位置 pending + 已完成 legacy → 迁移 → 用真实排程器跑一次 tick。"""
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir()
    target = _patch_data_root(monkeypatch, tmp_path, legacy_dir)
    legacy_db = legacy_dir / "local_scheduler.db"
    clock = FakeClock(datetime(2026, 11, 8, 9, 0, 0))

    source = _new_scheduler(legacy_db, clock)
    done_calls: list[str] = []

    async def legacy_done() -> None:
        done_calls.append("legacy")

    source.register("legacy_done", legacy_done)
    source.enqueue("legacy_done", job_key="legacy:done", delay_seconds=0)
    assert await _drive(source, lambda: bool(done_calls), timeout=3.0)
    source.enqueue(
        "after_upgrade",
        job_key="legacy:pending",
        delay_seconds=0,
        concurrency_key="reminder:1",
    )
    source.close()
    source_schema = _schema(legacy_db)

    migrated = Path(default_scheduler_db())
    assert migrated == target
    assert legacy_db.exists(), "旧文件保留"
    assert _schema(migrated) == source_schema, "迁移副本列/索引必须与旧库一致"
    assert set(local_scheduler_module._NEW_COLUMNS) <= set(_schema(migrated)[0])
    assert "idx_local_jobs_concurrency" in _schema(migrated)[1]

    dest = _new_scheduler(migrated, clock)
    pending_calls: list[str] = []

    async def after_upgrade() -> None:
        pending_calls.append("ran")

    dest.register("after_upgrade", after_upgrade)
    try:
        assert await _drive(dest, lambda: bool(pending_calls), timeout=3.0)
        jobs = {job["job_key"]: job for job in dest.list_jobs(limit=100)}
        assert jobs["legacy:pending"]["status"] == COMPLETED
        assert jobs["legacy:pending"]["attempts"] == 1
        assert jobs["legacy:done"]["status"] == COMPLETED, "已完成的历史行必须还在"
    finally:
        dest.close()
