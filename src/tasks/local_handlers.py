"""本地排程器注册的作业（单机、按时间点执行）。

边界：这里的作业只跑在**本进程**内，走 `src/tasks/local_scheduler.py`
（SQLite + 指数退避 + 死信）。跨进程 / 多 worker 的任务投递仍走
`src/tasks/handlers.py` + taskiq 队列，两套不重复写调度逻辑。

当前只注册一个真实作业：

* `liuhao.local.heartbeat` —— 周期性心跳，写审计、并把下一次心跳按
  「下一次运行时刻」为 `job_key` 重新入队（重试不会产生重复心跳）。
"""

from __future__ import annotations

import os
from datetime import timedelta
from typing import Any, Callable, Dict, List, Optional

from src.tasks.local_scheduler import LocalScheduler

AuditSink = Callable[[str, str, Dict[str, Any]], None]

LOCAL_HEARTBEAT_JOB = "liuhao.local.heartbeat"
HEARTBEAT_KEY_PREFIX = "liuhao.local.heartbeat"
DEFAULT_HEARTBEAT_SECONDS = 300.0
MIN_HEARTBEAT_SECONDS = 10.0


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


def register_local_jobs(scheduler: LocalScheduler) -> List[str]:
    """Register every local job handler; idempotent."""
    scheduler.register(LOCAL_HEARTBEAT_JOB, _make_heartbeat_handler(scheduler))
    return scheduler.registered_jobs()


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
    return [job_id]


def _make_heartbeat_handler(scheduler: LocalScheduler):
    async def handler(interval_seconds: float | None = None, **_: Any) -> Dict[str, Any]:
        return await local_heartbeat(scheduler, interval_seconds)

    return handler
