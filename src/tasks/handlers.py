"""worker 侧任务处理函数：审计先行、幂等兜底。

规则：

1. **不许绕过审计**。每个任务开始/结束/去重/失败都写 `AuditStore`
   （`src.kernels.audit.log_event`）。审计不可用时任务直接失败 —— 失败关闭
   优于「悄悄跑完不留痕」。
2. **不许重复副作用**。队列是「至少一次」语义（worker 崩溃后行会被重新投递），
   所以副作用任务必须用 `ExecutionJournal` 的幂等账本占坑。
3. 本模块只依赖 journal 与审计，不直接碰 gateway/knowledge/automation。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from taskiq import TaskiqDepends
from taskiq.context import Context

from src.kernels.audit import AuditEventType, AuditScope, log_event
from src.tasks.journal import get_execution_journal, get_task_journal

_PRINCIPAL = "liuhao-worker"


def _audit(action: str, outcome: str, details: Dict[str, Any], correlation_id: Optional[str] = None) -> None:
    # 审计失败 = 任务失败，绝不吞异常。
    log_event(
        AuditEventType.STATE_CHANGE,
        _PRINCIPAL,
        AuditScope.L1,
        outcome,
        details={"action": action, **details},
        correlation_id=correlation_id,
    )


async def worker_probe(
    marker_path: Optional[str] = None,
    journal_path: Optional[str] = None,
    ctx: Context = TaskiqDepends(),
) -> Dict[str, Any]:
    """Health probe that also proves queue-redelivery dedupe.

    ``marker_path`` is appended once per *executed* delivery. If the same
    task id is delivered twice (reclaimed after a crash), the second delivery
    returns the remembered result without touching the marker again.
    """
    task_id = ctx.message.task_id
    task_name = ctx.message.task_name
    key = f"taskiq::{task_id}"
    journal = get_task_journal(journal_path)

    _audit("worker_probe.start", "started", {"task_id": task_id, "task_name": task_name}, task_id)

    if journal.idempotency_claimed(key):
        previous = journal.idempotent_result(key)
        _audit(
            "worker_probe.deduplicated",
            "success",
            {"task_id": task_id, "task_name": task_name, "previous": previous},
            task_id,
        )
        return {
            "executed": False,
            "deduplicated": True,
            "task_id": task_id,
            "previous": previous,
        }

    if not journal.claim_idempotency(
        key, execution_id=f"taskiq:{task_name}", task_name=task_name
    ):
        # 并发重复投递：另一个消费者已经占坑。
        previous = journal.idempotent_result(key)
        _audit(
            "worker_probe.deduplicated",
            "success",
            {"task_id": task_id, "task_name": task_name, "previous": previous},
            task_id,
        )
        return {
            "executed": False,
            "deduplicated": True,
            "task_id": task_id,
            "previous": previous,
        }

    try:
        if marker_path:
            with open(marker_path, "a", encoding="utf-8") as fh:
                fh.write(f"{task_id}\n")
    except Exception as exc:
        # 副作用没有发生，释放占坑让重试可以真正执行。
        journal.release_idempotency(key)
        _audit(
            "worker_probe.failed",
            "failure",
            {"task_id": task_id, "task_name": task_name, "error": str(exc)},
            task_id,
        )
        raise

    result: Dict[str, Any] = {
        "executed": True,
        "deduplicated": False,
        "task_id": task_id,
        "task_name": task_name,
        "marker_path": marker_path,
    }
    # 先落结果再审计：即使后面的审计写失败，重投递也会走 dedupe 分支，
    # 不会把副作用再执行一次。
    journal.remember_idempotent_result(key, **result)
    _audit("worker_probe.completed", "success", result, task_id)
    return result


async def recovery_scan(
    older_than_seconds: float = 0.0,
    journal_path: Optional[str] = None,
    ctx: Context = TaskiqDepends(),
) -> Dict[str, Any]:
    """Scan the execution journal for crashed, unfinished executions.

    Reports the execution ids plus the tasks that were in flight when the
    process died — those are precisely the side effects that need the
    idempotency ledger's protection.
    """
    task_id = ctx.message.task_id
    journal = get_execution_journal(journal_path)
    try:
        scanned = journal.incomplete_executions(
            older_than_seconds=older_than_seconds or None
        )
    except Exception as exc:
        _audit(
            "recovery_scan.failed",
            "failure",
            {"task_id": task_id, "error": str(exc)},
            task_id,
        )
        raise

    payload = {
        "task_id": task_id,
        "journal_path": journal.db_path,
        "count": len(scanned),
        "executions": [
            {
                "execution_id": item.execution_id,
                "started_at": item.started_at,
                "last_event_at": item.last_event_at,
                "last_event_type": item.last_event_type,
                "age_seconds": item.age_seconds,
                "in_flight_tasks": list(item.in_flight_tasks),
                "completed_tasks": list(item.completed_tasks),
                "failed_tasks": list(item.failed_tasks),
            }
            for item in scanned
        ],
    }
    _audit(
        "recovery_scan.completed",
        "success" if not scanned else "attention",
        {"task_id": task_id, "count": len(scanned)},
        task_id,
    )
    return payload
