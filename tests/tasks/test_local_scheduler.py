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
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.tasks.local_handlers import (
    HEARTBEAT_KEY_PREFIX,
    LOCAL_HEARTBEAT_JOB,
    local_heartbeat,
    register_local_jobs,
    seed_local_jobs,
)
from src.tasks.local_scheduler import (
    COMPLETED,
    DEAD,
    PENDING,
    RETRY_WAIT,
    LocalScheduler,
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
