"""情报周报挂到生产启动链：正常注册；情报模块不可用时 worker 仍能起来。"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from src.intel.scheduling import WEEKLY_SCAN_JOB
from src.tasks.local_handlers import (
    LOCAL_HEARTBEAT_JOB,
    register_local_jobs,
)
from src.tasks.local_scheduler import PENDING, LocalScheduler
from src.tasks.reminders import REMINDER_FIRE_JOB


def _scheduler(tmp_path: Path) -> LocalScheduler:
    return LocalScheduler(
        str(tmp_path / "sched.db"),
        tick_seconds=0.05,
        audit_sink=lambda action, outcome, details: None,
    )


def _weekly_pending(scheduler: LocalScheduler) -> list[dict]:
    return [
        job
        for job in scheduler.list_jobs(PENDING, 50)
        if job["job_name"] == WEEKLY_SCAN_JOB
    ]


def test_register_local_jobs_registers_and_seeds_intel_weekly(tmp_path, caplog):
    scheduler = _scheduler(tmp_path)
    with caplog.at_level(logging.WARNING, logger="src.tasks.local_handlers"):
        registered = register_local_jobs(scheduler)

    assert WEEKLY_SCAN_JOB in registered
    assert LOCAL_HEARTBEAT_JOB in registered
    assert REMINDER_FIRE_JOB in registered
    assert len(_weekly_pending(scheduler)) == 1, "启动时应预约下一周的扫描，且只预约一条"
    assert not caplog.records, "正常注册不应报 WARNING"
    scheduler.close()


def test_worker_starts_when_intel_import_fails(tmp_path, monkeypatch, caplog):
    """模拟情报模块不可用：启动链不能崩，只留一条明确的 WARNING。"""
    scheduler = _scheduler(tmp_path)
    # 让 ``from src.intel.scheduling import ...`` 直接失败（等价于模块缺失/损坏）。
    monkeypatch.setitem(sys.modules, "src.intel.scheduling", None)

    with caplog.at_level(logging.WARNING, logger="src.tasks.local_handlers"):
        registered = register_local_jobs(scheduler)

    assert WEEKLY_SCAN_JOB not in registered
    assert LOCAL_HEARTBEAT_JOB in registered
    assert REMINDER_FIRE_JOB in registered
    assert _weekly_pending(scheduler) == []
    warnings = [item for item in caplog.records if item.levelno >= logging.WARNING]
    assert len(warnings) == 1, "失败只记一条，不在启动时刷日志"
    message = warnings[0].getMessage()
    assert "情报周报未注册" in message
    assert "ImportError" in message or "导入" in message
    scheduler.close()


def test_worker_starts_when_intel_registration_fails(tmp_path, monkeypatch, caplog):
    """模块能导入但注册本身抛错：同样只警告、不中断、不反复重试。"""
    scheduler = _scheduler(tmp_path)

    def boom(_scheduler):
        raise RuntimeError("weekly scan unavailable")

    monkeypatch.setattr("src.intel.scheduling.register_weekly_jobs", boom)
    with caplog.at_level(logging.WARNING, logger="src.tasks.local_handlers"):
        registered = register_local_jobs(scheduler)

    assert LOCAL_HEARTBEAT_JOB in registered
    assert REMINDER_FIRE_JOB in registered
    assert WEEKLY_SCAN_JOB not in registered
    warnings = [item for item in caplog.records if item.levelno >= logging.WARNING]
    assert len(warnings) == 1
    assert "情报周报未注册" in warnings[0].getMessage()
    assert "RuntimeError" in warnings[0].getMessage()
    assert "weekly scan unavailable" in warnings[0].getMessage()
    scheduler.close()


def test_intel_failure_does_not_block_real_jobs(tmp_path, monkeypatch):
    """情报不可用时，心跳/提醒的注册链照常完整（不是静默跳过后崩在别处）。"""
    scheduler = _scheduler(tmp_path)
    monkeypatch.setitem(sys.modules, "src.intel.scheduling", None)
    register_local_jobs(scheduler)

    jobs = scheduler.registered_jobs()
    for name in (LOCAL_HEARTBEAT_JOB, REMINDER_FIRE_JOB, "liuhao.reminder.digest"):
        assert name in jobs
    scheduler.close()
