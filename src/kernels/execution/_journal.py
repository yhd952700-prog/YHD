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

## 没做的事（诚实标注）

- 不做**副作用去重**（同一任务的外部副作用去重需要幂等键，属另一件事）
- 不做 plan 结构的版本化重建（恢复时重新跑一遍分解/计划，只跳过已完成的任务）
- 没有压缩/归档策略，长期运行会线性增长 —— 这是已知局限
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Set

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
        self._conn = sqlite3.connect(db_path, timeout=10.0)
        # 注意：必须先 connect 再设 WAL（Windows 上顺序反了会报错，
        # 见 identity 内核持久化里的同一条坑）。
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

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
        rows = self._conn.execute(
            "SELECT DISTINCT execution_id FROM journal_events ORDER BY execution_id"
        ).fetchall()
        return [r[0] for r in rows]

    def completed_task_names(self, execution_id: str) -> Set[str]:
        """该 execution 中**已经跑完**的任务名字集合。

        这是断点续跑的唯一依据：后续 `execute_goal` 会把计划里同名任务直接
        标成 COMPLETED 而不重新执行，从而跳过已完成的副作用。
        """
        rows = self._conn.execute(
            "SELECT DISTINCT task_name FROM journal_events"
            " WHERE execution_id = ? AND event_type = 'task_completed'"
            "   AND task_name IS NOT NULL",
            (execution_id,),
        ).fetchall()
        return {r[0] for r in rows if r[0]}

    def failed_task_names(self, execution_id: str) -> Set[str]:
        rows = self._conn.execute(
            "SELECT DISTINCT task_name FROM journal_events"
            " WHERE execution_id = ? AND event_type = 'task_failed'"
            "   AND task_name IS NOT NULL",
            (execution_id,),
        ).fetchall()
        # 一条任务失败后又被重试成功时，以最后一次成功为准 —— 即不再算作失败。
        return {r[0] for r in rows if r[0]} - self.completed_task_names(execution_id)

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
