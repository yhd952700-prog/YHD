"""本地排程器注册的作业（单机、按时间点执行）。

边界：这里的作业只跑在**本进程**内，走 `src/tasks/local_scheduler.py`
（SQLite + 指数退避 + 死信）。跨进程 / 多 worker 的任务投递仍走
`src/tasks/handlers.py` + taskiq 队列，两套不重复写调度逻辑。

当前注册三个真实作业：

* `liuhao.local.heartbeat` —— 周期性心跳，写审计、并把下一次心跳按
  「下一次运行时刻」为 `job_key` 重新入队（重试不会产生重复心跳）。
* `liuhao.reminder.fire` —— 定时提醒到点触发（`src/tasks/reminders.py`），
  通知走托盘气泡/控制台，失败由排程器重试并最终进死信。
* `liuhao.backup.daily` —— 每日自动备份（`src/backup/auto_backup.py`），
  每天本地时间 03:00 跑一次，留最近 7 天；跑完按日期重新入队下一天。
* `liuhao.email.poll` —— 邮件规则轮询（`src/ai/email_rules.py`），
  周期拉 Gmail 未读按规则建草稿计划；开关默认关、Google 没接通时
  如实跳过，只按间隔重新入队自己。
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from src.backup.auto_backup import AUTO_BACKUP_HOUR, run_daily_auto_backup
from src.common.engines import data_dir
from src.tasks.local_scheduler import LocalScheduler
from src.tasks.reminder_time import local_timezone
from src.tasks.reminders import (
    REMINDER_DIGEST_JOB,
    REMINDER_FIRE_JOB,
    get_reminder_service,
)
from src.upgrade.update_check import register_update_check_job, seed_update_check_job

AuditSink = Callable[[str, str, Dict[str, Any]], None]

logger = logging.getLogger(__name__)

LOCAL_HEARTBEAT_JOB = "liuhao.local.heartbeat"
HEARTBEAT_KEY_PREFIX = "liuhao.local.heartbeat"
DEFAULT_HEARTBEAT_SECONDS = 300.0
MIN_HEARTBEAT_SECONDS = 10.0

DAILY_BACKUP_JOB = "liuhao.backup.daily"
BACKUP_JOB_KEY_PREFIX = "liuhao.backup.daily"

EMAIL_POLL_JOB = "liuhao.email.poll"
EMAIL_POLL_KEY_PREFIX = "liuhao.email.poll"
DEFAULT_EMAIL_POLL_SECONDS = 900.0
MIN_EMAIL_POLL_SECONDS = 60.0


def heartbeat_interval_seconds() -> float:
    """``LIUHAO_LOCAL_HEARTBEAT_INTERVAL`` in seconds (default 300, min 10)."""
    raw = os.environ.get("LIUHAO_LOCAL_HEARTBEAT_INTERVAL")
    if not raw:
        return DEFAULT_HEARTBEAT_SECONDS
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(
            f"LIUHAO_LOCAL_HEARTBEAT_INTERVAL must be a number, got {raw!r}"
        ) from exc
    if value < MIN_HEARTBEAT_SECONDS:
        raise ValueError(
            f"LIUHAO_LOCAL_HEARTBEAT_INTERVAL must be >= {MIN_HEARTBEAT_SECONDS:g}s"
        )
    return value


async def local_heartbeat(
    scheduler: LocalScheduler,
    interval_seconds: float | None = None,
    **_: Any,
) -> Dict[str, Any]:
    """Audit a heartbeat and chain the next one deterministically."""
    interval = heartbeat_interval_seconds() if interval_seconds is None else float(interval_seconds)
    if interval < MIN_HEARTBEAT_SECONDS:
        raise ValueError(f"interval_seconds must be >= {MIN_HEARTBEAT_SECONDS:g}")
    scheduler.audit_event(
        "local_scheduler.heartbeat",
        "success",
        {"interval_seconds": interval, "scheduler_owner": scheduler.owner},
    )
    next_run_at = scheduler.now() + timedelta(seconds=interval)
    scheduler.enqueue(
        LOCAL_HEARTBEAT_JOB,
        run_at=next_run_at,
        job_key=f"{HEARTBEAT_KEY_PREFIX}:{next_run_at.isoformat()}",
        payload={"interval_seconds": interval},
    )
    return {"interval_seconds": interval, "next_run_at": next_run_at.isoformat()}


def next_daily_backup_run(now: Optional[datetime] = None) -> datetime:
    """下一个本地时间 03:00（aware；入队时排程器转成 naive UTC 存库）。

    ``now`` 只给测试用；生产永远用当前时间算。naive 的 ``now`` 按 UTC
    理解（排程器的时钟就是 naive UTC），不按系统本地时区猜。
    """
    tz = local_timezone()
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(tz)
    target = current.replace(
        hour=AUTO_BACKUP_HOUR, minute=0, second=0, microsecond=0
    )
    if target <= current:
        target += timedelta(days=1)
    return target


def _daily_backup_job_key(run_at: datetime) -> str:
    """按目标日期派生的 job_key：种子与自调度用同一个 key，重启后重复
    入队会返回已有的 job id，不会同一天备两次。"""
    return f"{BACKUP_JOB_KEY_PREFIX}:{run_at.date().isoformat()}"


async def daily_backup(
    scheduler: LocalScheduler,
    **_: Any,
) -> Dict[str, Any]:
    """跑一次每日自动备份，并按心跳的自调度模式排下一天 03:00。"""
    meta = await asyncio.to_thread(run_daily_auto_backup, data_dir())
    scheduler.audit_event(
        "local_scheduler.daily_backup",
        "success",
        {"backup_id": meta.backup_id, "scheduler_owner": scheduler.owner},
    )
    # 下一次按排程器的时钟算（不是墙钟）：排程器的 now() 在测试里可 fake，
    # 且正在跑的作业行还是 running 状态 —— 用墙钟会在测试里算回当天
    # 03:00，与当前作业的 job_key 撞车、入队被去重掉。
    next_run_at = next_daily_backup_run(scheduler.now())
    scheduler.enqueue(
        DAILY_BACKUP_JOB,
        run_at=next_run_at,
        job_key=_daily_backup_job_key(next_run_at),
        payload={},
    )
    return {"backup_id": meta.backup_id, "next_run_at": next_run_at.isoformat()}


def email_poll_interval_seconds() -> float:
    """``LIUHAO_EMAIL_POLL_INTERVAL`` in seconds (default 900, min 60)."""
    raw = os.environ.get("LIUHAO_EMAIL_POLL_INTERVAL")
    if not raw:
        return DEFAULT_EMAIL_POLL_SECONDS
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(
            f"LIUHAO_EMAIL_POLL_INTERVAL must be a number, got {raw!r}"
        ) from exc
    if value < MIN_EMAIL_POLL_SECONDS:
        raise ValueError(
            f"LIUHAO_EMAIL_POLL_INTERVAL must be >= {MIN_EMAIL_POLL_SECONDS:g}s"
        )
    return value


async def email_poll(
    scheduler: LocalScheduler,
    interval_seconds: float | None = None,
    **_: Any,
) -> Dict[str, Any]:
    """跑一次邮件规则轮询，并按心跳的自调度模式排下一轮。

    开关没开 / Google 没接通时 poll_gmail_once 如实回报原因、本轮不拉
    邮件；无论跑没跑，都按间隔重新入队自己（翻开关不用重启排程器）。
    """
    from src.ai.email_rules import poll_gmail_once

    interval = (
        email_poll_interval_seconds() if interval_seconds is None else float(interval_seconds)
    )
    if interval < MIN_EMAIL_POLL_SECONDS:
        raise ValueError(f"interval_seconds must be >= {MIN_EMAIL_POLL_SECONDS:g}s")
    result = await asyncio.to_thread(poll_gmail_once)
    scheduler.audit_event(
        "local_scheduler.email_poll",
        "success",
        {
            "ran": bool(result.get("ran")),
            "reason": result.get("reason", ""),
            "matched": result.get("matched", 0),
            "scheduler_owner": scheduler.owner,
        },
    )
    next_run_at = scheduler.now() + timedelta(seconds=interval)
    scheduler.enqueue(
        EMAIL_POLL_JOB,
        run_at=next_run_at,
        job_key=f"{EMAIL_POLL_KEY_PREFIX}:{next_run_at.isoformat()}",
        payload={"interval_seconds": interval},
    )
    return {**result, "next_run_at": next_run_at.isoformat()}


def register_local_jobs(
    scheduler: LocalScheduler, *, reminder_service=None
) -> List[str]:
    """Register every local job handler; idempotent.

    ``reminder_service`` is only for tests: production resolves the process-wide
    service lazily so the scheduler process and the AI tool share one store
    (same SQLite database).
    """
    scheduler.register(
        LOCAL_HEARTBEAT_JOB, _make_heartbeat_handler(scheduler), reentrant=True
    )
    # 提醒/摘要自身有 dispatch intent 幂等标记（重启后按 unknown 封口而不是
    # 重弹），所以允许崩溃后重试；业务层保证最多响一次。
    scheduler.register(
        REMINDER_FIRE_JOB,
        _make_reminder_handler(scheduler, reminder_service),
        reentrant=True,
    )
    scheduler.register(
        REMINDER_DIGEST_JOB,
        _make_reminder_digest_handler(scheduler, reminder_service),
        reentrant=True,
    )
    # 每日自动备份：备份本身是幂等的（每次新归档），崩溃后可重跑；
    # 同名作业默认串行（concurrency_key=job_name），不会自己跟自己重叠。
    scheduler.register(
        DAILY_BACKUP_JOB,
        _make_daily_backup_handler(scheduler),
        reentrant=True,
    )
    # 邮件规则轮询：处理本身有 seen 落盘去重，崩溃后重跑不会重复建计划；
    # 开关默认关，没开时 handler 只如实跳过并续排下一轮。
    scheduler.register(
        EMAIL_POLL_JOB,
        _make_email_poll_handler(scheduler),
        reentrant=True,
    )

    # 重试耗尽的提醒不能静默消失：排程器死信时回调提醒服务记 missed + 摘要。
    def _on_dead_letter(info: Dict[str, Any]) -> None:
        if info.get("job_name") not in (REMINDER_FIRE_JOB, REMINDER_DIGEST_JOB):
            return
        try:
            svc = reminder_service or get_reminder_service(scheduler=scheduler)
            svc.handle_dead_letter(info)
        except Exception:  # noqa: BLE001 - 死信已落盘，回调失败只记录
            logger.exception(
                "reminder dead-letter hook failed (job_id=%s)", info.get("id")
            )

    scheduler.on_dead_letter(_on_dead_letter)
    # 每周自动检查更新：只提示新版本，绝不自动安装（src/upgrade/update_check.py）。
    register_update_check_job(scheduler)
    _register_intel_weekly_jobs(scheduler)
    return scheduler.registered_jobs()


def _register_intel_weekly_jobs(scheduler: LocalScheduler) -> bool:
    """注册并预约情报周报；情报模块缺失/导入失败不能拖垮 worker 启动。

    失败只记一条 WARNING 并继续（不循环重试，避免启动时刷日志）。
    """
    try:
        from src.intel.scheduling import register_weekly_jobs, seed_weekly_jobs
    except Exception as exc:  # noqa: BLE001 - 可选模块，任何导入失败都不该阻断启动
        logger.warning(
            "情报周报未注册：无法导入 src.intel.scheduling（%s: %s）。"
            "本次启动继续，不做重试。",
            type(exc).__name__,
            exc,
        )
        return False
    try:
        register_weekly_jobs(scheduler)
        seed_weekly_jobs(scheduler)
    except Exception as exc:  # noqa: BLE001 - 注册失败同样不能阻断启动
        logger.warning(
            "情报周报未注册：注册/预约失败（%s: %s）。本次启动继续，不做重试。",
            type(exc).__name__,
            exc,
        )
        return False
    return True


def build_local_scheduler(audit_sink: Optional[AuditSink] = None) -> LocalScheduler:
    """Configured local scheduler: handlers registered, first heartbeat queued.

    Used by `liuhao-worker` and `liuhao-scheduler` entrypoints. Only **one**
    process may run it against a given database (see the module docstring of
    `src/tasks/local_scheduler.py`); supervisor disables it on the 2-process
    worker group and enables it on the single-instance scheduler.
    """
    scheduler = LocalScheduler(audit_sink=audit_sink)
    register_local_jobs(scheduler)
    seed_local_jobs(scheduler)
    seed_update_check_job(scheduler)
    return scheduler


def seed_local_jobs(scheduler: LocalScheduler) -> List[int]:
    """Schedule the first heartbeat immediately (idempotent across restarts)."""
    job_key = f"{HEARTBEAT_KEY_PREFIX}:start:{scheduler.now().isoformat()}"
    job_id = scheduler.enqueue(
        LOCAL_HEARTBEAT_JOB,
        delay_seconds=0.0,
        job_key=job_key,
        payload={"interval_seconds": heartbeat_interval_seconds()},
    )
    # 每日自动备份：首启时排到下一个本地时间 03:00。job_key 按目标日期
    # 派生，重启后重复入队会返回已有 job id，不会同一天备两次。
    # 时间按排程器的时钟算（与 handler 的自调度同一口径）。
    backup_run_at = next_daily_backup_run(scheduler.now())
    backup_job_id = scheduler.enqueue(
        DAILY_BACKUP_JOB,
        run_at=backup_run_at,
        job_key=_daily_backup_job_key(backup_run_at),
        payload={},
    )
    # 邮件规则轮询：首启排在第一个间隔之后；job_key 按目标时刻派生，
    # 重启重复入队返回已有 job id。开关没开时它只空转续排，不拉邮件。
    email_run_at = scheduler.now() + timedelta(seconds=email_poll_interval_seconds())
    email_job_id = scheduler.enqueue(
        EMAIL_POLL_JOB,
        run_at=email_run_at,
        job_key=f"{EMAIL_POLL_KEY_PREFIX}:{email_run_at.isoformat()}",
        payload={"interval_seconds": email_poll_interval_seconds()},
    )
    return [job_id, backup_job_id, email_job_id]


def _make_heartbeat_handler(scheduler: LocalScheduler):
    async def handler(interval_seconds: float | None = None, **_: Any) -> Dict[str, Any]:
        return await local_heartbeat(scheduler, interval_seconds)

    return handler


def _make_daily_backup_handler(scheduler: LocalScheduler):
    async def handler(**payload: Any) -> Dict[str, Any]:
        return await daily_backup(scheduler, **payload)

    return handler


def _make_email_poll_handler(scheduler: LocalScheduler):
    async def handler(interval_seconds: float | None = None, **_: Any) -> Dict[str, Any]:
        return await email_poll(scheduler, interval_seconds)

    return handler


def _make_reminder_handler(scheduler: LocalScheduler, service=None):
    async def handler(**payload: Any) -> Dict[str, Any]:
        svc = service or get_reminder_service(scheduler=scheduler)
        # 通知会阻塞（PowerShell 气泡/文件写入），放线程池执行，别卡住 tick 循环。
        return await asyncio.to_thread(svc.handle_fire, payload)

    return handler


def _make_reminder_digest_handler(scheduler: LocalScheduler, service=None):
    async def handler(**payload: Any) -> Dict[str, Any]:
        svc = service or get_reminder_service(scheduler=scheduler)
        return await asyncio.to_thread(svc.handle_digest, payload)

    return handler
