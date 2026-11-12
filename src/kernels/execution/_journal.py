"""Execution Journal — 让 ExecutionEngine 在进程崩溃后能从断点继续。

## 为什么需要它

在加这一层之前，鎏灏的执行状态**100% 只存在于内存**：

- `Task.status` 是 dataclass 字段，进程没了就没了
- `ExecutionEngine.create_checkpoint()` 构造的是**纯内存 dict**，且
  `execute_goal` / `_execute_plan` / `_execute_task` **从未调用它**
  （docstring 声称 "for rollback"，但全文件没有任何 rollback 实现）
- 没有 `execution_id`：`Task.id` 是 `uuid4()[:8]`，每次gui他都变

后果：**长任务跑到一半进程崩溃 = 全部从头再来**，已完成的副作用（比如已经
发出去的消息、已经写过的文件）会在重跑时被执行第二次。

## 设计取舍（刻意的最小化）

1. **默认关闭**。 `ExecutionEngine(journal=None)` 与历史行为逐字节一致，
   与 `capability_executor` 那条依赖注入接缝同一哲学：不注入就不生效。
2. **恢复按 task 名字匹配，不按 id**。 id 是随机的，跨进程必然对不上；
   `GoalDecomposer` 对同一目标描述是确定性的，因此名字可以跨进程稳定。
3. **每条事件立即 commit**。 崩溃是硬kill（`kill -9` / TerminateProcess），
   没有机会跑 `finally` 或 `atexit`，所以 journal 不能攒批落在死后 flush。
4. **append-only**。 不更新、不删除 —— 事件序列本身就是唯一真源，
   当前状态由 replay 推导出来。

## 幂等键（本模块新增，2026-11-05）

`task_started` 之后、`task_completed` 之前崩溃，会留下一个**语义上无法判定**
的状态：副作用可能已经发生，也可能没有。重跑可能重复副作用，跳过可能丢工作。
仅靠 append-only 事件无法消解这个二义性 —— 必须由**副作用执行方**持有一个
跨进程稳定的幂等键，并在真正的外部系统上去重。

本模块提供那张**持久化的幂等账本**（`journal_idempotency` 表）：

- `claim_idempotency(key, ...)`：`INSERT OR IGNORE` 原子占坑。返回 `True` 表示
  这是**第一次**占坑，调用方可以执行副作用；返回 `False` 表示该键已被占，
  调用方**不得**再次执行副作用（可按需读取 `idempotent_result(key)`）。
- `remember_idempotent_result(key, **payload)`：副作用成功后把结果落盘，
  后续重复投递可直接复用。
- `release_idempotency(key)`：**仅**在确认副作用尚未发生（例如参数校验失败、
  执行前异常）时释放占坑，让重试可以真正执行。副作用已经发生就绝不该释放。

`make_idempotency_key(execution_id, task_name)` 给出跨进程稳定的键（task 名字
由 `GoalDecomposer` 确定性生成，id 则每次 uuid4 都会变，不能用作键）。

## 恢复扫描（本模块新增，2026-11-05）

`incomplete_executions()` 扫描出「有 `execution_started`、但没有
`execution_completed` / `execution_cancelled`」的 execution，并标出每个
execution 里**已开始、却无终态**的任务名。worker/scheduler 启动时据此回答：
上次进程留下了哪些半截执行、哪些任务当时正在跑（这些正是需要幂等键兜底的地方）。

## 没做的事（诚实标注）

- 不做 plan 结构的版本化重建（恢复时重新跑一遍分解/计划，只跳过已完成的任务）
- 没有压缩/归档策略，长期运行会线性增长 —— 这是已知局限
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from src._time import utc_now


_SCHEMA = """
CREATE TABLE IF NOT EXISTS journal_events (
    seq           INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_id  TEXT NOT NULL,
    event_type    TEXT NOT NULL,
    task_name     TEXT,
    ts            TEXT NOT NULL,
    payload       TEXT
);
CREATE INDEX IF NOT EXISTS idx_journal_exec
    ON journal_events(execution_id, seq);
CREATE TABLE IF NOT EXISTS journal_idempotency (
    idempotency_key TEXT PRIMARY KEY,
    execution_id    TEXT NOT NULL,
    task_name       TEXT,
    claimed_at      TEXT NOT NULL,
    result          TEXT
);
CREATE INDEX IF NOT EXISTS idx_journal_idem_exec
    ON journal_idempotency(execution_id, task_name);
"""


@dataclass(frozen=True)
class JournalEvent:
    """一条已落盘的执行事件。"""

    seq: int
    execution_id: str
    event_type: str
    task_name: Optional[str]
    ts: str
    payload: Dict[str, Any]


def new_execution_id() -> str:
    return str(uuid.uuid4())


def make_idempotency_key(execution_id: str, task_name: str) -> str:
    """Build the cross-process-stable key for one task's side effect.

    Task *names* survive restarts (the decomposer is deterministic); task
    *ids* do not (``uuid4()`` per run). So the durable key must be built from
    the execution id + task name, never from a task id.
    """
    return f"{execution_id}::{task_name}"


@dataclass(frozen=True)
class IncompleteExecution:
    """A journaled execution that has no terminal event (crash candidate)."""

    execution_id: str
    started_at: Optional[str]
    last_event_at: str
    last_event_type: str
    last_seq: int
    age_seconds: Optional[float]
    #: Tasks that emitted ``task_started`` but never a terminal event. These are
    #: exactly the side effects a crash left ambiguous.
    in_flight_tasks: Tuple[str, ...]
    completed_tasks: Tuple[str, ...]
    failed_tasks: Tuple[str, ...]


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    """Parse a journal timestamp; unparsable rows degrade to ``None`` age."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


class ExecutionJournal:
    """Append-only 的执行事件日志， backed by SQLite。

    用法::

        journal = ExecutionJournal("./execution-journal.db")
        engine = ExecutionEngine(journal=journal)
        engine.execute_goal(goal)          # 第一次：正常跑
        # ... 进程崩了 ...
        engine = ExecutionJournal(...)     # 重建
        engine.execute_goal(goal)          # 第二次：从断点继续
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        # check_same_thread=False: the journal owns a single connection but may
        # be written from a worker thread (background goal execution) while it
        # was opened on the request thread. Writes are serialized by the caller
        # / journal lock, so disabling the same-thread guard is safe and avoids
        # "SQLite objects created in a thread can only be used in that same
        # thread" when a goal runs in the background.
        self._conn = sqlite3.connect(db_path, timeout=10.0, check_same_thread=False)
        # 注意：必须先 connect 再设 WAL（Windows 上顺序反了会报错，
        # 见 identity 内核持久化里的同一条坑）。
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        # Serialize all connection access: the journal may be written from a
        # background goal thread while read from the request thread, and
        # multiple concurrent background goals would otherwise race on the
        # single shared connection. Reentrant: ``failed_task_names`` composes
        # ``completed_task_names`` and MUST hold the same lock across both
        # SELECTs so the two sets are read from one consistent point.
        self._lock = threading.RLock()

    # -- write -----------------------------------------------------------

    def record(
        self,
        execution_id: str,
        event_type: str,
        task_name: Optional[str] = None,
        **payload: Any,
    ) -> int:
        """追加一条事件并**立即提交**。

        立即提交是为了对抗硬 kill：进程被 `kill -9` 时没有 flush 机会，
        尚未 commit 的事件会随进程一起消失，那样 journal 就形同虚设。
        """
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO journal_events (execution_id, event_type, task_name, ts, payload)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    execution_id,
                    event_type,
                    task_name,
                    utc_now().isoformat(),
                    json.dumps(payload, ensure_ascii=False, default=str),
                ),
            )
            self._conn.commit()
        return int(cur.lastrowid or 0)

    # -- read ------------------------------------------------------------

    def events(self, execution_id: str) -> List[JournalEvent]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT seq, execution_id, event_type, task_name, ts, payload"
                " FROM journal_events WHERE execution_id = ? ORDER BY seq",
                (execution_id,),
            ).fetchall()
        out: List[JournalEvent] = []
        for seq, eid, etype, tname, ts, payload in rows:
            try:
                data = json.loads(payload) if payload else {}
            except (TypeError, ValueError):
                data = {"_raw": payload}
            out.append(JournalEvent(seq, eid, etype, tname, ts, data))
        return out

    def executions(self) -> List[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT execution_id FROM journal_events ORDER BY execution_id"
            ).fetchall()
        return [r[0] for r in rows]

    def completed_task_names(self, execution_id: str) -> Set[str]:
        """该 execution 中**已经跑完**的任务名字集合。

        这是断点续跑的唯一依据：后续 `execute_goal` 会把计划里同名任务直接
        标成 COMPLETED 而不重新执行，从而跳过已完成的副作用。
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT task_name FROM journal_events"
                " WHERE execution_id = ? AND event_type = 'task_completed'"
                "   AND task_name IS NOT NULL",
                (execution_id,),
            ).fetchall()
        return {r[0] for r in rows if r[0]}

    def completed_task_artifacts(self, execution_id: str) -> Dict[str, str]:
        """Map each completed task name to the artifact path it recorded.

        Defect E: a ``task_completed`` row proves the task ran *at some point*,
        not that its side effect is still on disk. The artifact path recorded at
        completion is what lets recovery ask the harder question -- does it still
        exist? Tasks that declared no artifact are simply absent from the map.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT task_name, payload FROM journal_events"
                " WHERE execution_id = ? AND event_type = 'task_completed'"
                "   AND task_name IS NOT NULL",
                (execution_id,),
            ).fetchall()
        out: Dict[str, str] = {}
        for tname, payload in rows:
            if not tname:
                continue
            try:
                data = json.loads(payload) if payload else {}
            except (TypeError, ValueError):
                continue
            path = data.get("artifact_path") if isinstance(data, dict) else None
            if isinstance(path, str) and path:
                out[tname] = path
        return out

    def failed_task_names(self, execution_id: str) -> Set[str]:
        # Both SELECTs (this one and the composed ``completed_task_names``) run
        # under ONE held lock so the "failed minus completed" subtraction is
        # taken from a single consistent point -- otherwise a concurrent
        # ``task_completed`` commit landing between them makes a task look both
        # failed-and-superseded while a recovery decision reads it. RLock makes
        # the composition reentrant without re-acquiring.
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT task_name FROM journal_events"
                " WHERE execution_id = ? AND event_type = 'task_failed'"
                "   AND task_name IS NOT NULL",
                (execution_id,),
            ).fetchall()
            failed = {r[0] for r in rows if r[0]}
            # 一条任务失败后又被重试成功时，以最后一次成功为准 —— 即不再算作失败。
            return failed - self.completed_task_names(execution_id)

    # -- idempotency ledger ---------------------------------------------

    def claim_idempotency(
        self,
        idempotency_key: str,
        execution_id: str,
        task_name: Optional[str] = None,
    ) -> bool:
        """Atomically claim a side-effect slot. ``True`` == caller may proceed.

        The claim is durable *before* the side effect runs, so a second
        delivery (retry, reclaimed queue item, resumed execution) sees it and
        must not repeat the effect. ``INSERT OR IGNORE`` on a PRIMARY KEY is
        the atomic primitive: exactly one concurrent caller wins, including
        across processes sharing the same SQLite file.
        """
        with self._lock:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO journal_idempotency"
                " (idempotency_key, execution_id, task_name, claimed_at, result)"
                " VALUES (?, ?, ?, ?, NULL)",
                (idempotency_key, execution_id, task_name, utc_now().isoformat()),
            )
            self._conn.commit()
            return cur.rowcount == 1

    def idempotency_claimed(self, idempotency_key: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM journal_idempotency WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        return row is not None

    def idempotent_result(self, idempotency_key: str) -> Optional[Dict[str, Any]]:
        """The recorded outcome of a claimed side effect, if it completed."""
        with self._lock:
            row = self._conn.execute(
                "SELECT result FROM journal_idempotency WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        if not row or row[0] is None:
            return None
        try:
            parsed = json.loads(row[0])
        except (TypeError, ValueError):
            return {"_raw": row[0]}
        return parsed if isinstance(parsed, dict) else {"value": parsed}

    def remember_idempotent_result(self, idempotency_key: str, **payload: Any) -> None:
        """Persist the result of an already-claimed side effect.

        Raises ``ValueError`` when the key was never claimed: silently
        remembering a result would let a caller "prove" work it never
        registered, which is exactly the failure mode this ledger prevents.
        """
        with self._lock:
            cur = self._conn.execute(
                "UPDATE journal_idempotency SET result = ? WHERE idempotency_key = ?",
                (
                    json.dumps(payload, ensure_ascii=False, default=str),
                    idempotency_key,
                ),
            )
            self._conn.commit()
        if cur.rowcount != 1:
            raise ValueError(f"idempotency key was never claimed: {idempotency_key!r}")

    def release_idempotency(self, idempotency_key: str) -> bool:
        """Release a claim whose side effect is *known not to have happened*.

        A completed (result-bearing) claim is never released: the effect is
        already out in the world, and letting a retry run it again is the
        duplicate this ledger exists to stop.
        """
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM journal_idempotency"
                " WHERE idempotency_key = ? AND result IS NULL",
                (idempotency_key,),
            )
            self._conn.commit()
            return cur.rowcount == 1

    # -- recovery scan ---------------------------------------------------

    def incomplete_executions(
        self, older_than_seconds: Optional[float] = None
    ) -> List[IncompleteExecution]:
        """Find executions with no terminal event after their latest start.

        A crash leaves ``execution_started`` behind but no
        ``execution_completed`` / ``execution_cancelled``. Re-running the
        same goal appends a *new* ``execution_started``, so the terminal
        comparison is anchored to the **latest** start — otherwise an
        execution that completed in a previous run would look terminal while
        the new run is still in flight.

        ``older_than_seconds`` filters out executions that just started (a
        scan racing a healthy in-flight run should not report it as orphaned).
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT execution_id,"
                " MAX(CASE WHEN event_type = 'execution_started' THEN seq END),"
                " MAX(CASE WHEN event_type = 'execution_completed' THEN seq END),"
                " MAX(CASE WHEN event_type = 'execution_cancelled' THEN seq END),"
                " MAX(seq)"
                " FROM journal_events GROUP BY execution_id",
            ).fetchall()
            candidates = []
            for eid, started_seq, completed_seq, cancelled_seq, last_seq in rows:
                if started_seq is None:
                    continue
                if completed_seq is not None and completed_seq > started_seq:
                    continue
                if cancelled_seq is not None and cancelled_seq > started_seq:
                    continue
                candidates.append((eid, last_seq))

            now = utc_now()
            out: List[IncompleteExecution] = []
            for eid, last_seq in candidates:
                detail = self._conn.execute(
                    "SELECT seq, task_name, event_type, ts"
                    " FROM journal_events WHERE execution_id = ? ORDER BY seq",
                    (eid,),
                ).fetchall()
                if not detail:
                    continue

                completed: Set[str] = set()
                failed: Set[str] = set()
                started_order: List[str] = []
                started_at: Optional[str] = None
                for _seq, tname, etype, ts in detail:
                    if etype == "execution_started" and started_at is None:
                        started_at = ts
                    if not tname:
                        continue
                    if etype == "task_started":
                        if tname not in started_order:
                            started_order.append(tname)
                    elif etype == "task_completed":
                        completed.add(tname)
                        failed.discard(tname)
                    elif etype == "task_failed" and tname not in completed:
                        failed.add(tname)

                in_flight = tuple(
                    name for name in started_order if name not in completed and name not in failed
                )
                dt = _parse_iso(started_at)
                age = (now - dt).total_seconds() if dt is not None else None
                if (
                    older_than_seconds is not None
                    and age is not None
                    and age < older_than_seconds
                ):
                    continue
                last_row = detail[-1]
                out.append(
                    IncompleteExecution(
                        execution_id=eid,
                        started_at=started_at,
                        last_event_at=last_row[3],
                        last_event_type=last_row[2],
                        last_seq=last_seq,
                        age_seconds=age,
                        in_flight_tasks=in_flight,
                        completed_tasks=tuple(sorted(completed)),
                        failed_tasks=tuple(sorted(failed)),
                    )
                )
        return out

    # -- lifecycle -------------------------------------------------------

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "ExecutionJournal":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()


def derive_state(events: Iterable[JournalEvent]) -> Dict[str, Any]:
    """从事件序列推导出当前状态（纯函数，便于单测）。

    append-only 日志 + replay = 状态。此处只恢复 `ExecutionEngine` 续跑真正
    需要的那部分：哪些任务已完成、哪些失败过、这条 execution 到过哪些阶段。
    """
    completed: Set[str] = set()
    failed: Set[str] = set()
    stages: List[str] = []
    for ev in events:
        if ev.event_type == "task_completed" and ev.task_name:
            completed.add(ev.task_name)
            failed.discard(ev.task_name)
        elif ev.event_type == "task_failed" and ev.task_name:
            if ev.task_name not in completed:
                failed.add(ev.task_name)
        elif ev.event_type.endswith("_started"):
            stages.append(ev.event_type)
    return {"completed": completed, "failed": failed, "stages": stages}
