"""`liuhao-worker` 入口：`python -m src.workers.runner`。

`config/supervisor.conf` 之前指向这个不存在的模块（空指配置）。现在它是
真实入口，职责：

1. 选 broker（默认 SQLite，见 `src/tasks/broker.py`），注册任务。
2. **启动即扫描**：读执行 journal 的半截执行，写审计 + 日志。
3. 跑 taskiq `Receiver` 消费队列；任务处理函数全部强制审计
   （`src/tasks/handlers.py`）。
4. SIGTERM/SIGINT 优雅停机；退出码 0。

另：进程内本地排程器（`src/tasks/local_scheduler.py`，默认启用，可用
`LIUHAO_LOCAL_SCHEDULER=0` 或 `--no-local-scheduler` 关闭）在这里启动，
负责单机的时间点执行 / 失败退避重试 / 死信；跨进程任务仍走上面的 taskiq 队列。
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from typing import Optional

from taskiq.receiver import Receiver

from src.tasks.broker import broker_mode, build_broker
from src.tasks.journal import get_execution_journal
from src.tasks.local_handlers import build_local_scheduler
from src.tasks.local_scheduler import LocalScheduler, local_scheduler_enabled
from src.tasks.registry import register_tasks
from src.workers._support import audit, install_stop_handlers, require_audit_env

logger = logging.getLogger("liuhao.worker")


def _startup_execution_scan() -> int:
    """Read-only journal scan at worker start (the "what did we crash on?" query)."""
    try:
        scanned = get_execution_journal().incomplete_executions()
    except Exception as exc:
        logger.exception("startup execution scan failed")
        audit("worker.startup_scan", "failure", {"error": str(exc)})
        return -1
    audit(
        "worker.startup_scan",
        "success" if not scanned else "attention",
        {
            "count": len(scanned),
            "executions": [item.execution_id for item in scanned][:20],
        },
    )
    if scanned:
        logger.warning(
            "startup scan found %d unfinished execution(s): %s",
            len(scanned),
            ", ".join(item.execution_id for item in scanned[:20]),
        )
    return len(scanned)


async def _run(
    concurrency: int, max_tasks: Optional[int], *, with_local_scheduler: bool = True
) -> int:
    require_audit_env()
    broker = build_broker()
    broker.is_worker_process = True  # enables the SQLite queue's stale reclaim
    register_tasks(broker)
    found = _startup_execution_scan()

    local: Optional[LocalScheduler] = None
    local_task: Optional[asyncio.Task] = None
    scheduler_guard: Optional[object] = None
    if with_local_scheduler:
        # supervisor 下 worker 是 numprocs=2；两个进程同时 run() 同一个库会在
        # 启动时互相强制回收租约，导致同一提醒重复触发。抢不到锁就只做 worker。
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
        "worker.start",
        "started",
        {
            "broker": broker_mode(),
            "concurrency": concurrency,
            "max_tasks": max_tasks,
            "unfinished_at_start": found,
            "local_scheduler": with_local_scheduler,
            "local_scheduler_db": local.db_path if local else None,
        },
    )

    loop = asyncio.get_running_loop()
    stop = install_stop_handlers(loop)
    finish = asyncio.Event()
    receiver = Receiver(
        broker,
        max_async_tasks=concurrency,
        propagate_exceptions=False,
        max_tasks_to_execute=max_tasks,
    )

    listener = asyncio.create_task(receiver.listen(finish))
    stopper = asyncio.create_task(stop.wait())
    waitables = {listener, stopper}
    if local_task is not None:
        waitables.add(local_task)
    error: Optional[BaseException] = None
    try:
        done, _pending = await asyncio.wait(
            waitables, return_when=asyncio.FIRST_COMPLETED
        )
        if local_task is not None and local_task in done:
            # 本地排程器是核心组件，崩溃不能静默：让 supervisor 重启进程。
            error = local_task.exception()
        finish.set()
        await listener
    except BaseException as exc:  # noqa: BLE001 - 需要原样上报给 supervisor
        error = exc
    finally:
        finish.set()
        stopper.cancel()
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
            except Exception:  # noqa: BLE001 - 收尾阶段不让锁拖垮退出
                logger.exception("local scheduler lock release failed")
        try:
            await broker.shutdown()
        except Exception:  # noqa: BLE001
            logger.exception("broker shutdown failed")

    audit(
        "worker.stop",
        "failure" if error else "success",
        {"error": str(error) if error else None},
    )
    if error is not None:
        logger.error("worker stopped with error: %s", error)
        return 1
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="liuhao-worker", description=__doc__)
    parser.add_argument(
        "--concurrency",
        type=int,
        default=2,
        help="max concurrently executing tasks (default: 2, matches supervisor)",
    )
    parser.add_argument(
        "--max-tasks",
        type=int,
        default=0,
        help="exit after N tasks (0 = run forever; useful for smoke tests)",
    )
    parser.add_argument(
        "--no-local-scheduler",
        action="store_true",
        help="do not start the in-process local scheduler (default: start it)",
    )
    args = parser.parse_args(argv)
    if args.concurrency < 1:
        parser.error("--concurrency must be >= 1")
    logging.basicConfig(level=logging.INFO)
    max_tasks = args.max_tasks or None
    with_local_scheduler = local_scheduler_enabled() and not args.no_local_scheduler
    return asyncio.run(_run(args.concurrency, max_tasks, with_local_scheduler=with_local_scheduler))


if __name__ == "__main__":
    sys.exit(main())
