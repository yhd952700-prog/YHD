"""`liuhao-scheduler` 入口：`python -m src.workers.scheduler`。

调度**用 taskiq 自带的 `TaskiqScheduler` + `LabelScheduleSource`**（任务在注册时
通过 `schedule=[{"interval": N}]` label 声明周期）。taskiq 0.13 的核心包只提供
调度数据结构和 `on_ready()` 投递原语，不提供独立常驻循环（循环在 CLI 里），
所以这里补一个最小 tick 循环，而不是引入第二套调度器（不引 APScheduler）。

限制（诚实标注）：只支持 `interval` 调度。`cron` 需要 `croniter`（当前未安装），
遇到 cron 配置直接报错，不静默跳过。需要 cron 时再评估 `pip install croniter`。

调度器只负责**投递**：把到期任务写进 broker 队列；真正执行在 worker 进程。

同一个进程里还跑**本地排程器**（`src/tasks/local_scheduler.py`，默认启用，可用
`LIUHAO_LOCAL_SCHEDULER=0` / `--no-local-scheduler` 关闭）：它负责单机的时间点
执行、失败指数退避重试与死信。supervisor 里 worker 组是 `numprocs=2`，而本地
排程器假设**单进程独占同一个 SQLite 库**，所以只在单实例的 liuhao-scheduler
进程里启用。
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import sys
from typing import Dict, Optional

from taskiq.schedule_sources import LabelScheduleSource
from taskiq.scheduler.scheduler import TaskiqScheduler

from src.tasks.broker import broker_mode, build_broker
from src.tasks.local_handlers import build_local_scheduler
from src.tasks.local_scheduler import LocalScheduler, local_scheduler_enabled
from src.tasks.registry import register_tasks
from src.workers._support import audit, install_stop_handlers, require_audit_env

logger = logging.getLogger("liuhao.worker")


async def _run(poll_interval: float, *, with_local_scheduler: bool = True) -> int:
    require_audit_env()
    broker = build_broker()
    register_tasks(broker)
    source = LabelScheduleSource(broker)
    scheduler = TaskiqScheduler(broker, [source])

    await scheduler.startup()
    await source.startup()
    schedules = await source.get_schedules()
    for task in schedules:
        if task.interval is None:
            await scheduler.shutdown()
            raise RuntimeError(
                f"schedule {task.schedule_id} for {task.task_name} has no interval; "
                "cron/time schedules need croniter support which is not installed"
            )

    local: Optional[LocalScheduler] = None
    local_task: Optional[asyncio.Task] = None
    if with_local_scheduler:
        local = build_local_scheduler(
            audit_sink=lambda action, outcome, details: audit(action, outcome, details)
        )
        local_task = asyncio.create_task(local.run())

    audit(
        "scheduler.start",
        "started",
        {
            "broker": broker_mode(),
            "schedules": [
                {"task": t.task_name, "interval": float(t.interval)} for t in schedules
            ],
            "local_scheduler": with_local_scheduler,
            "local_scheduler_db": local.db_path if local else None,
        },
    )
    logger.info(
        "scheduler started with %d interval schedule(s) on %s",
        len(schedules),
        broker_mode(),
    )

    loop = asyncio.get_running_loop()
    stop = install_stop_handlers(loop)
    next_run: Dict[str, float] = {}
    error: Optional[BaseException] = None
    try:
        while not stop.is_set():
            for task in await source.get_schedules():
                now = loop.time()
                due = next_run.get(task.schedule_id)
                if due is None:
                    due = now  # first tick fires immediately (startup recovery scan)
                if now >= due:
                    try:
                        await scheduler.on_ready(source, task)
                    except Exception as exc:  # noqa: BLE001 - 不能因一次投递失败退出
                        logger.exception("failed to enqueue %s", task.task_name)
                        audit(
                            "scheduler.enqueue_failed",
                            "failure",
                            {"task": task.task_name, "error": str(exc)},
                            task.schedule_id,
                        )
                    next_run[task.schedule_id] = now + float(task.interval)
            if local_task is not None and local_task.done():
                exc = local_task.exception()
                if exc is not None:
                    raise exc
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=poll_interval)
    except BaseException as exc:  # noqa: BLE001
        error = exc
    finally:
        if local is not None:
            local.stop()
        if local_task is not None:
            try:
                await asyncio.gather(local_task, return_exceptions=True)
            except Exception:  # noqa: BLE001
                logger.exception("local scheduler shutdown failed")
        if local is not None:
            audit(
                "local_scheduler.stop",
                "attention" if (local.dead_letter_count() or error) else "success",
                {
                    "stats": local.stats(),
                    "dead_letters": local.dead_letter_count(),
                    "error": str(error) if error else None,
                },
            )
            local.close()
        try:
            await scheduler.shutdown()
        except Exception:  # noqa: BLE001
            logger.exception("scheduler shutdown failed")

    audit(
        "scheduler.stop",
        "failure" if error else "success",
        {"error": str(error) if error else None},
    )
    if error is not None:
        logger.error("scheduler stopped with error: %s", error)
        return 1
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="liuhao-scheduler", description=__doc__)
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=1.0,
        help="seconds between schedule checks (default: 1.0)",
    )
    parser.add_argument(
        "--no-local-scheduler",
        action="store_true",
        help="do not start the in-process local scheduler (default: start it)",
    )
    args = parser.parse_args(argv)
    if args.poll_interval <= 0:
        parser.error("--poll-interval must be > 0")
    logging.basicConfig(level=logging.INFO)
    with_local_scheduler = local_scheduler_enabled() and not args.no_local_scheduler
    return asyncio.run(_run(args.poll_interval, with_local_scheduler=with_local_scheduler))


if __name__ == "__main__":
    sys.exit(main())
