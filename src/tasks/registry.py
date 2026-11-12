"""把 worker 任务注册到任意 taskiq broker（runner / scheduler / 测试共用）。

之所以不是模块级 `@broker.task` 装饰器：任务需要能注册到**不同实例**上
（测试用 `InMemoryBroker`，生产用 `SqliteTaskBroker`）。taskiq 的任务对象
绑定 broker，所以注册函数按 broker 实例构造。
"""

from __future__ import annotations

import os
from typing import Dict, Optional

from taskiq.abc.broker import AsyncBroker
from taskiq.decor import AsyncTaskiqDecoratedTask

from src.tasks import handlers

PROBE_TASK = "liuhao.system.worker_probe"
RECOVERY_SCAN_TASK = "liuhao.system.recovery_scan"

DEFAULT_RECOVERY_INTERVAL_SECONDS = 300
MIN_RECOVERY_INTERVAL_SECONDS = 5


def recovery_interval_seconds() -> int:
    raw = os.environ.get("LIUHAO_RECOVERY_SCAN_INTERVAL")
    if not raw:
        return DEFAULT_RECOVERY_INTERVAL_SECONDS
    try:
        interval = int(raw)
    except ValueError as exc:
        raise ValueError(
            f"LIUHAO_RECOVERY_SCAN_INTERVAL must be an integer, got {raw!r}"
        ) from exc
    if interval < MIN_RECOVERY_INTERVAL_SECONDS:
        raise ValueError(
            f"LIUHAO_RECOVERY_SCAN_INTERVAL must be >= "
            f"{MIN_RECOVERY_INTERVAL_SECONDS}s, got {interval}"
        )
    return interval


def registered_tasks(broker: AsyncBroker) -> Dict[str, AsyncTaskiqDecoratedTask]:
    return {
        name: task
        for name, task in broker.get_all_tasks().items()
        if task.broker is broker
    }


def register_tasks(
    broker: AsyncBroker, recovery_interval: Optional[int] = None
) -> Dict[str, AsyncTaskiqDecoratedTask]:
    """Register the worker's tasks on ``broker`` (idempotent per broker)."""
    existing = registered_tasks(broker)
    if PROBE_TASK in existing and RECOVERY_SCAN_TASK in existing:
        return existing

    tasks: Dict[str, AsyncTaskiqDecoratedTask] = dict(existing)

    if PROBE_TASK not in tasks:
        tasks[PROBE_TASK] = broker.task(task_name=PROBE_TASK)(handlers.worker_probe)

    if RECOVERY_SCAN_TASK not in tasks:
        interval = recovery_interval_seconds() if recovery_interval is None else recovery_interval
        if interval < MIN_RECOVERY_INTERVAL_SECONDS:
            raise ValueError(
                f"recovery scan interval must be >= {MIN_RECOVERY_INTERVAL_SECONDS}s"
            )
        # taskiq 自带调度：LabelScheduleSource 读取这个 label，
        # 由 src/workers/scheduler.py 用 TaskiqScheduler.on_ready 投递。
        tasks[RECOVERY_SCAN_TASK] = broker.task(
            task_name=RECOVERY_SCAN_TASK,
            schedule=[
                {
                    "interval": interval,
                    "kwargs": {"older_than_seconds": 0.0},
                }
            ],
        )(handlers.recovery_scan)

    return tasks
