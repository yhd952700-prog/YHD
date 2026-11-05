"""SQLite-backed taskiq broker: 单机多进程可用的持久化任务队列。

职责边界（与 `src/distribution/msg_bus.py` 严格区分）：

* **本模块 / taskiq = 任务队列与执行**。一条消息对应「跑这个任务一次」，
  有任务名、参数、重试/结果语义，消费者是 worker。
* **msg_bus = 事件总线**。一条消息对应「这件事发生了」的广播，
  有 topic、订阅者、扇出，消费者可以是任意观察者（UI、指标、审计旁路）。

不要把 goal 执行塞进 msg_bus 当队列用，也不要把事件广播塞进 taskiq 当 pub/sub
用。两者共享 Redis 时也只是共享基础设施，语义不共享。

为什么需要这个 broker：`config/supervisor.conf` 以独立进程跑 worker，而
`InMemoryBroker` 的 `kick()` 在**发送方进程**内直接执行任务 —— 跨进程等于
没有队列。所以默认走 SQLite（无需 Redis / Postgres），Redis 仅在显式配置且
安装了 `taskiq-redis` 时启用，默认关闭。

耐久语义：

* `kick` 落盘 → worker 以 `BEGIN IMMEDIATE` 原子领取 → 执行完成后 ack 删除。
* worker 崩溃时行停留在 `running`；重启 `startup()` 会把超过
  `stale_after_seconds` 的 `running` 行改回 `pending`（队列级恢复扫描）。
* 队列**不保证恰好一次**：重新投递可能发生，所以任务侧必须用
  `ExecutionJournal.claim_idempotency()` 兜底（见 `src/tasks/handlers.py`）。
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import threading
from datetime import timedelta
from functools import partial
from typing import Any, AsyncGenerator, Dict, Optional, Union

from taskiq.abc.broker import AsyncBroker
from taskiq.acks import AckableMessage
from taskiq.message import BrokerMessage

from src._time import utc_now

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS task_queue (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     TEXT NOT NULL,
    task_name   TEXT NOT NULL,
    queue       TEXT NOT NULL DEFAULT 'default',
    payload     BLOB NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    created_at  TEXT NOT NULL,
    claimed_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_task_queue_ready
    ON task_queue(queue, status, id);
"""


class SqliteTaskBroker(AsyncBroker):
    """A tiny durable task queue that speaks the taskiq broker protocol."""

    def __init__(
        self,
        db_path: str,
        queue: str = "default",
        poll_interval: float = 0.2,
        stale_after_seconds: float = 300.0,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.db_path = db_path
        self.queue = queue
        self.poll_interval = poll_interval
        self.stale_after_seconds = stale_after_seconds
        os.makedirs(os.path.dirname(os.path.abspath(db_path)) or ".", exist_ok=True)
        # isolation_level=None => 自动提交，便于使用显式 BEGIN IMMEDIATE。
        self._conn = sqlite3.connect(
            db_path,
            timeout=15.0,
            check_same_thread=False,
            isolation_level=None,
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=15000")
        self._conn.executescript(_SCHEMA)
        self._lock = threading.Lock()

    # -- producer side ----------------------------------------------------

    async def kick(self, message: BrokerMessage) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO task_queue"
                " (task_id, task_name, queue, payload, status, created_at, claimed_at)"
                " VALUES (?, ?, ?, ?, 'pending', ?, NULL)",
                (
                    message.task_id,
                    message.task_name,
                    self.queue,
                    message.message,
                    utc_now().isoformat(),
                ),
            )

    # -- consumer side ----------------------------------------------------

    def listen(self) -> AsyncGenerator[Union[bytes, AckableMessage], None]:
        return self._listen()

    async def _listen(self) -> AsyncGenerator[Union[bytes, AckableMessage], None]:
        while True:
            row = self._claim_next()
            if row is None:
                await asyncio.sleep(self.poll_interval)
                continue
            row_id, payload = row
            yield AckableMessage(data=payload, ack=partial(self._ack, row_id))

    def _claim_next(self) -> Optional[tuple[int, bytes]]:
        """Atomically move one pending row to ``running``.

        ``BEGIN IMMEDIATE`` takes the write lock up front, so two worker
        processes racing on the same DB cannot both claim the same row.
        """
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    "SELECT id, payload FROM task_queue"
                    " WHERE queue = ? AND status = 'pending'"
                    " ORDER BY id LIMIT 1",
                    (self.queue,),
                ).fetchone()
                if row is None:
                    self._conn.execute("COMMIT")
                    return None
                self._conn.execute(
                    "UPDATE task_queue SET status = 'running', claimed_at = ?"
                    " WHERE id = ?",
                    (utc_now().isoformat(), row[0]),
                )
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
        return int(row[0]), bytes(row[1])

    async def _ack(self, row_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM task_queue WHERE id = ?", (row_id,))

    # -- recovery ---------------------------------------------------------

    def requeue_stale(self, stale_after_seconds: Optional[float] = None) -> int:
        """Return crashed workers' in-flight rows to ``pending``.

        A row in ``running`` whose claim is older than the threshold can only
        come from a worker that died mid-task; a healthy worker acks within
        the task's own runtime. This is the queue-level recovery scan.
        """
        threshold = self.stale_after_seconds if stale_after_seconds is None else stale_after_seconds
        cutoff = (utc_now() - timedelta(seconds=threshold)).isoformat()
        with self._lock:
            cur = self._conn.execute(
                "UPDATE task_queue SET status = 'pending', claimed_at = NULL"
                " WHERE status = 'running' AND claimed_at IS NOT NULL AND claimed_at < ?",
                (cutoff,),
            )
            count = cur.rowcount
        return int(count)

    def pending_count(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM task_queue WHERE queue = ? AND status = 'pending'",
                (self.queue,),
            ).fetchone()
        return int(row[0])

    def running_count(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM task_queue WHERE queue = ? AND status = 'running'",
                (self.queue,),
            ).fetchone()
        return int(row[0])

    # -- lifecycle --------------------------------------------------------

    async def startup(self) -> None:
        # 只有 worker 进程才做队列级恢复。调度器启动时也调 broker.startup()，
        # 此时它并不是消费者，若也回填 running 行，会把仍在执行的长任务
        # 重新投递（虽然后续有幂等键兜底，但没必要制造重复）。
        if getattr(self, "is_worker_process", False):
            reclaimed = self.requeue_stale()
            if reclaimed:
                logger.warning(
                    "requeued %d task(s) left running by a previous worker (queue=%s)",
                    reclaimed,
                    self.queue,
                )
        await super().startup()

    async def shutdown(self) -> None:
        await super().shutdown()
        with self._lock:
            self._conn.close()

    def describe(self) -> Dict[str, Any]:
        return {
            "mode": "sqlite",
            "db_path": self.db_path,
            "queue": self.queue,
            "pending": self.pending_count(),
            "running": self.running_count(),
        }
