"""`liuhao-scheduler` 入口：`python -m src.workers.scheduler`。

调度**用 taskiq 自带的 `TaskiqScheduler` + `LabelScheduleSource`**（任务在注册时
通过 `schedule=[{"interval": N}]` label 声明周期）。taskiq 0.13 的核心包只提供
调度数据结构和 `on_ready()` 投递原语，不提供独立常驻循环（循环在 CLI 里），
所以这里补一个最小 tick 循环，而不是引入第二套调度器（不引 APScheduler）。

调度类型：`interval` 与 `cron` 都支持——cron 自完善第二轮 D-R4 起由
croniter（MIT）计算到点时刻（此前未装该依赖，遇到 cron 配置直接报错；
现在接上了）。`time` 一次性时间点调度仍不支持，遇到照样明确报错，
不静默跳过。

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
from datetime import datetime, timezone
from typing import Dict, Optional

from taskiq.schedule_sources import LabelScheduleSource
from taskiq.scheduler.scheduler import TaskiqScheduler

from src.tasks.broker import broker_mode, build_broker
from src.tasks.local_handlers import build_local_scheduler
from src.tasks.local_scheduler import LocalScheduler, local_scheduler_enabled
from src.tasks.registry import register_tasks
from src.workers._support import audit, install_stop_handlers, require_audit_env

logger = logging.getLogger("liuhao.worker")


def _cron_delay_seconds(
    expr: str,
    *,
    now: Optional[datetime] = None,
    offset_seconds: float = 0.0,
) -> float:
    """cron 表达式距下一次到点的秒数（UTC 计算，含 taskiq 的 cron_offset）。

    D-R4 接线点：抽成纯函数单测覆盖，tick 循环只管按它排期。
    表达式非法/无下一次到点时 croniter 自己抛错，调用方不要吞。
    """
    from croniter import croniter as _croniter

    base = now or datetime.now(timezone.utc)
    nxt = _croniter(expr, base).get_next(datetime)
    return max(0.0, (nxt - base).total_seconds() + offset_seconds)


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
        if task.interval is None and not task.cron:
            await scheduler.shutdown()
            raise RuntimeError(
                f"schedule {task.schedule_id} for {task.task_name} has neither "
                "interval nor cron; one-shot time schedules are not supported"
            )

    local: Optional[LocalScheduler] = None
    local_task: Optional[asyncio.Task] = None
    scheduler_guard: Optional[object] = None
    if with_local_scheduler:
        # 与 worker/网关共用一把锁：任何部署形态下同一队列只有一个运行者，
        # 避免启动时互相回收租约造成重复通知。
        from src.tasks.scheduler_host import claim_local_scheduler_owner

        scheduler_guard = claim_local_scheduler_owner()
        if scheduler_guard is not None:
            local = build_local_scheduler(
                audit_sink=lambda action, outcome, details: audit(action, outcome, details)
            )
            local_task = asyncio.create_task(local.run())
        else:
            with_local_scheduler = False

    audit(
        "scheduler.start",
        "started",
        {
            "broker": broker_mode(),
            "schedules": [
                {
                    "task": t.task_name,
                    "interval": float(t.interval) if t.interval is not None else None,
                    "cron": t.cron,
                }
                for t in schedules
            ],
            "local_scheduler": with_local_scheduler,
            "local_scheduler_db": local.db_path if local else None,
        },
    )
    logger.info(
        "scheduler started with %d schedule(s) on %s",
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
                    # interval 任务首 tick 立即投递（启动恢复扫描，既有语义）；
                    # cron 任务按表达式排到下一次到点——重启不等于到点，
                    # 立即投递会让每次重启都多跑一次。
                    if task.interval is not None:
                        due = now
                    else:
                        offset = (
                            task.cron_offset.total_seconds() if task.cron_offset else 0.0
                        )
                        due = now + _cron_delay_seconds(task.cron, offset_seconds=offset)
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
                    if task.interval is not None:
                        next_run[task.schedule_id] = now + float(task.interval)
                    else:
                        offset = (
                            task.cron_offset.total_seconds() if task.cron_offset else 0.0
                        )
                        next_run[task.schedule_id] = now + _cron_delay_seconds(
                            task.cron, offset_seconds=offset
                        )
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
        if scheduler_guard is not None:
            try:
                scheduler_guard.release()
            except Exception:  # noqa: BLE001
                logger.exception("local scheduler lock release failed")
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
