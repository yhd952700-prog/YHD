"""单进程本地排程器：定时执行 / 失败重试（指数退避）/ 死信。

## 与 taskiq 的边界（不写两套调度逻辑）

| | taskiq（`src/tasks/sqlite_broker.py` + `src/workers/scheduler.py`） | 本模块 `LocalScheduler` |
|---|---|---|
| 范围 | 跨进程 / 多 worker 的任务队列与周期投递 | **单机本地**的时间点 / 延迟 / 重试 |
| 触发 | 外部 `kick`、或 `LabelScheduleSource` 的固定 interval | `run_at` 绝对时间 / `delay_seconds` |
| 重试 | 队列层不重试（重新投递靠 ack 语义） | 本模块内建指数退避 + 上限 + 死信 |
| 状态 | `task_queue`（pending/running，ack 即删） | `local_jobs`（pending/running/retry_wait/completed/dead，**永久可查**） |

一句话：**跨进程投递用 taskiq；需要「几点几分做什么 / 失败后退避重试 / 耗尽进死信」
用本模块。** 不要把本模块的 `run_at` 调度搬到 taskiq 里重写，也不要用本模块
承担跨机分发。

## 可靠性

* 所有状态落 SQLite（WAL + `BEGIN IMMEDIATE` 原子领取），不用内存字典。
* 进程重启：启动时 `recover_expired_leases(force=True)` 立刻把上一个进程留下的
  `running` 行放回 `retry_wait`（本模块假设「单机单进程独占这个库」），到期后
  继续执行 —— 不会因为重启丢任务；运行期间另按租约过期周期性回收。
* **默认不可重入**（TOOL-04）：handler 真正开始前写 `exec_started_at`；崩溃恢复
  时如果该标记已存在且任务没显式声明 `reentrant=True`，直接进死信而**不再重跑**，
  避免同一副作用执行两次。只有明确幂等的 handler 才允许恢复重试。
* 同一 `concurrency_key`（默认 `job_name`）不并发执行：领取时在事务内检查是否
  已有同 key 的 running 行。不同 `concurrency_key` 的同名任务互不阻塞（提醒按
  `reminder:<id>` 分组，一条提醒慢不会拖住其他提醒）。
* 进程内并发上限：`max_concurrency` 在领任务前按空槽计数（默认 4，env
  `LIUHAO_LOCAL_SCHEDULER_CONCURRENCY`）。
* 每条任务硬超时：`asyncio.wait_for`（默认 300s，env
  `LIUHAO_LOCAL_SCHEDULER_JOB_TIMEOUT`，也可按任务 `timeout_seconds` 覆盖），
  超时按失败走重试/死信，不会永远停在 running。
* 队列上限：活动行（pending/running/retry_wait）达到 `max_queue`（默认 5000，
  env `LIUHAO_LOCAL_SCHEDULER_MAX_QUEUE`）时 `enqueue` 抛 `QueueFull`。
* 租约（lease）心跳：**只在 handler 显式调用 `heartbeat()` 时续期**，不再每 tick
  全量刷新；本进程正在执行的 job 不会被自己的周期回收误判为崩溃。
* 幂等入队：同一 `job_key` 在 active 状态（pending/running/retry_wait）内唯一；
  重复 `enqueue` 返回已存在的 job id，不产生第二条（队列满时也保持幂等）。

## 死信可见性

耗尽重试的 job 变成 `status='dead'`，保留 `last_error` / `attempts` /
`dead_lettered_at`，可 `list_dead_letters()` 查询；同时写审计事件
`local_scheduler.dead_letter`（审计控制台可直接看到）。审计写失败只记 ERROR
日志、**不会**丢死信行 —— 死信先落盘，再通知。
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import json
import logging
import os
import socket
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Union

from src._time import utc_now

logger = logging.getLogger(__name__)

# 当前正在执行的 job id（handler 调 ``scheduler.heartbeat()`` 时用来续租）。
_CURRENT_JOB_ID: contextvars.ContextVar[Optional[int]] = contextvars.ContextVar(
    "liuhao_local_job_id", default=None
)

PENDING = "pending"
RUNNING = "running"
RETRY_WAIT = "retry_wait"
COMPLETED = "completed"
DEAD = "dead"
CANCELLED = "cancelled"
ACTIVE_STATUSES = (PENDING, RUNNING, RETRY_WAIT)

DEFAULT_MAX_CONCURRENCY = 4
DEFAULT_MAX_QUEUE = 5000
DEFAULT_JOB_TIMEOUT_SECONDS = 300.0
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF_SECONDS = 2.0
DEFAULT_BACKOFF_MULTIPLIER = 2.0
DEFAULT_MAX_BACKOFF_SECONDS = 300.0
DEFAULT_LEASE_SECONDS = 60.0
DEFAULT_TICK_SECONDS = 0.5

Handler = Callable[..., Awaitable[Any]]
AuditSink = Callable[[str, str, Dict[str, Any]], None]
DeadLetterHook = Callable[[Dict[str, Any]], None]


class QueueFull(RuntimeError):
    """活动队列已达上限。调用方必须显式降级，不能静默无限堆积。"""

    def __init__(self, active: int, limit: int) -> None:
        super().__init__(
            f"local scheduler queue is full ({active}/{limit}); "
            "increase LIUHAO_LOCAL_SCHEDULER_MAX_QUEUE or drain the queue"
        )
        self.active = active
        self.limit = limit


_SCHEMA = """
CREATE TABLE IF NOT EXISTS local_jobs (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    job_key              TEXT NOT NULL,
    job_name             TEXT NOT NULL,
    payload              TEXT NOT NULL,
    status               TEXT NOT NULL,
    run_at               TEXT NOT NULL,
    attempts             INTEGER NOT NULL DEFAULT 0,
    max_attempts         INTEGER NOT NULL DEFAULT 3,
    backoff_seconds      REAL NOT NULL DEFAULT 2.0,
    backoff_multiplier   REAL NOT NULL DEFAULT 2.0,
    max_backoff_seconds  REAL NOT NULL DEFAULT 300.0,
    last_error           TEXT,
    result               TEXT,
    concurrency_key      TEXT NOT NULL DEFAULT '',
    timeout_seconds      REAL,
    reentrant            INTEGER NOT NULL DEFAULT 0,
    exec_started_at      TEXT,
    lease_owner          TEXT,
    lease_expires_at     TEXT,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL,
    completed_at         TEXT,
    dead_lettered_at     TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_local_jobs_active_key
    ON local_jobs(job_key) WHERE status IN ('pending', 'running', 'retry_wait');
CREATE INDEX IF NOT EXISTS idx_local_jobs_due
    ON local_jobs(status, run_at);
CREATE INDEX IF NOT EXISTS idx_local_jobs_name
    ON local_jobs(job_name, status);
"""

#: 建表之后才能建（引用新增列）。**不能**并进 ``_SCHEMA``：旧库上
#: ``executescript`` 会在补列之前撞上 ``no such column``，导致升级用户
#: 的排程器整个起不来。
_SCHEMA_NEW_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_local_jobs_concurrency
    ON local_jobs(concurrency_key, status);
"""

#: 旧库缺失、需要 ``ALTER TABLE`` 补上的列（新库建表时已包含）。
_NEW_COLUMNS: Dict[str, str] = {
    "concurrency_key": "TEXT NOT NULL DEFAULT ''",
    "timeout_seconds": "REAL",
    "reentrant": "INTEGER NOT NULL DEFAULT 0",
    "exec_started_at": "TEXT",
}


def default_scheduler_db() -> str:
    """调度库落点：与 tasks.db / reminders.db / outbox 共用同一套解析。

    优先级 ``LIUHAO_LOCAL_SCHEDULER_DB`` → ``LIUHAO_DATA_DIR`` →
    ``LIUHAO_WORKSPACE_ROOT`` → ``~/.liuhao/queue``；旧位置有数据时由
    ``resolve_queue_file`` 用 SQLite backup API 迁移到数据根并校验行数，
    迁移失败继续用旧库，绝不以空库启动。
    """
    from src.tasks._queue_paths import resolve_queue_file

    return resolve_queue_file(
        "local_scheduler.db", env_var="LIUHAO_LOCAL_SCHEDULER_DB"
    )


def default_max_concurrency() -> int:
    raw = os.environ.get("LIUHAO_LOCAL_SCHEDULER_CONCURRENCY")
    if not raw:
        return DEFAULT_MAX_CONCURRENCY
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(
            f"LIUHAO_LOCAL_SCHEDULER_CONCURRENCY must be an integer, got {raw!r}"
        ) from exc
    if value < 1:
        raise ValueError("LIUHAO_LOCAL_SCHEDULER_CONCURRENCY must be >= 1")
    return value


def default_max_queue() -> int:
    """``LIUHAO_LOCAL_SCHEDULER_MAX_QUEUE`` (default 5000, min 1)."""
    raw = os.environ.get("LIUHAO_LOCAL_SCHEDULER_MAX_QUEUE")
    if not raw:
        return DEFAULT_MAX_QUEUE
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(
            f"LIUHAO_LOCAL_SCHEDULER_MAX_QUEUE must be an integer, got {raw!r}"
        ) from exc
    if value < 1:
        raise ValueError("LIUHAO_LOCAL_SCHEDULER_MAX_QUEUE must be >= 1")
    return value


def default_job_timeout_seconds() -> float:
    """``LIUHAO_LOCAL_SCHEDULER_JOB_TIMEOUT`` (seconds, default 300, min 0.1)."""
    raw = os.environ.get("LIUHAO_LOCAL_SCHEDULER_JOB_TIMEOUT")
    if not raw:
        return DEFAULT_JOB_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(
            f"LIUHAO_LOCAL_SCHEDULER_JOB_TIMEOUT must be a number, got {raw!r}"
        ) from exc
    if value <= 0:
        raise ValueError("LIUHAO_LOCAL_SCHEDULER_JOB_TIMEOUT must be > 0")
    return value


_TRUTHY = {"1", "true", "yes", "on", "enabled"}
_FALSY = {"0", "false", "no", "off", "disabled"}


def local_scheduler_enabled() -> bool:
    """``LIUHAO_LOCAL_SCHEDULER`` (default on). Unknown values fail loudly."""
    raw = (os.environ.get("LIUHAO_LOCAL_SCHEDULER") or "1").strip().lower()
    if raw in _TRUTHY:
        return True
    if raw in _FALSY:
        return False
    raise ValueError(
        f"LIUHAO_LOCAL_SCHEDULER must be one of {sorted(_TRUTHY | _FALSY)}, got {raw!r}"
    )


def _naive_utc(value: datetime) -> datetime:
    """Normalise to the naive-UTC form used everywhere else (see ``src/_time.py``).

    Aware inputs are converted instead of stored verbatim: mixing ``+00:00``
    strings with naive ones makes the ``run_at <= now`` comparison wrong.
    """
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _as_iso(value: Union[None, str, datetime]) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _naive_utc(value).isoformat()
    try:
        return _naive_utc(datetime.fromisoformat(value)).isoformat()
    except ValueError as exc:
        raise ValueError(f"run_at must be an ISO datetime or datetime, got {value!r}") from exc


def _default_audit(action: str, outcome: str, details: Dict[str, Any]) -> None:
    try:
        from src.kernels.audit import AuditEventType, AuditScope, log_event

        log_event(
            AuditEventType.STATE_CHANGE,
            "liuhao-scheduler",
            AuditScope.L1,
            outcome,
            details={"action": action, **details},
        )
    except Exception as exc:  # noqa: BLE001 - 死信已落盘，通知失败只记录，不回滚状态
        from src.common.atomic_write import is_storage_failure

        if is_storage_failure(exc):
            # 磁盘满/只读：中文可行动提示，不刷英文堆栈（后台心跳会周期性重试）。
            logger.error(
                "本地调度器审计写入失败（action=%s）：磁盘已满或数据目录只读/被占用；"
                "调度状态已保存在死信表，本次审计链缺一条。请清理磁盘空间或解除只读后重试。",
                action,
            )
        else:
            logger.exception("local scheduler audit write failed (action=%s)", action)


class LocalScheduler:
    """SQLite-backed, single-machine scheduler with retry and dead letters."""

    def __init__(
        self,
        db_path: Optional[str] = None,
        *,
        max_concurrency: Optional[int] = None,
        max_queue: Optional[int] = None,
        job_timeout_seconds: Optional[float] = None,
        lease_seconds: float = DEFAULT_LEASE_SECONDS,
        tick_seconds: float = DEFAULT_TICK_SECONDS,
        owner: Optional[str] = None,
        now_fn: Callable[[], datetime] = utc_now,
        audit_sink: Optional[AuditSink] = None,
    ) -> None:
        self.db_path = db_path or default_scheduler_db()
        self.max_concurrency = (
            default_max_concurrency() if max_concurrency is None else max_concurrency
        )
        if self.max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        self.max_queue = default_max_queue() if max_queue is None else max_queue
        if self.max_queue < 1:
            raise ValueError("max_queue must be >= 1")
        self.job_timeout_seconds = (
            default_job_timeout_seconds()
            if job_timeout_seconds is None
            else job_timeout_seconds
        )
        if self.job_timeout_seconds <= 0:
            raise ValueError("job_timeout_seconds must be > 0")
        self.lease_seconds = lease_seconds
        self.tick_seconds = tick_seconds
        self.owner = owner or f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        self._now = now_fn
        self._audit_sink = audit_sink or _default_audit
        self._handlers: Dict[str, Handler] = {}
        self._reentrant: Dict[str, bool] = {}
        self._dead_letter_hooks: List[DeadLetterHook] = []
        self._active: set[asyncio.Task] = set()
        self._active_ids: set[int] = set()
        self._stop = asyncio.Event()
        self._running = False
        self._ticks = 0

        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)) or ".", exist_ok=True)
        self._conn = sqlite3.connect(
            self.db_path,
            timeout=15.0,
            check_same_thread=False,
            isolation_level=None,
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=15000")
        # 顺序必须是「建表 → 补列 → 建引用新列的索引」：旧库上先把
        # idx_local_jobs_concurrency 建在还不存在的列上，会让补列永远跑不到。
        try:
            self._conn.executescript(_SCHEMA)
            self._ensure_columns()
            self._conn.executescript(_SCHEMA_NEW_INDEXES)
        except sqlite3.Error as exc:
            raise RuntimeError(
                f"本地排程器未启动：无法初始化/升级调度库 {self.db_path}（{exc}）。"
                "提醒与定时任务不会触发；请先停止进程并备份该库，"
                "检查数据目录是否只读或磁盘已满后重试。"
            ) from exc
        self._lock = threading.Lock()

    def _ensure_columns(self) -> None:
        """老库升级：``CREATE TABLE IF NOT EXISTS`` 不会补列，这里显式 ALTER。

        补列失败必须**明确报错并说明排程器未启动**，不能只记一行日志后
        带病继续（提醒会静默不触发）。ALTER 不报错也要复核列真的出现了。
        """
        existing = {row[1] for row in self._conn.execute("PRAGMA table_info(local_jobs)")}
        missing = [name for name in _NEW_COLUMNS if name not in existing]
        if not missing:
            return
        failures: List[str] = []
        for name in missing:
            try:
                self._conn.execute(
                    f"ALTER TABLE local_jobs ADD COLUMN {name} {_NEW_COLUMNS[name]}"
                )
            except sqlite3.Error as exc:
                failures.append(f"{name}（{exc}）")
        current = {
            row[1] for row in self._conn.execute("PRAGMA table_info(local_jobs)")
        }
        still_missing = [name for name in missing if name not in current]
        if failures or still_missing:
            detail = "、".join(failures) if failures else "、".join(still_missing)
            raise RuntimeError(
                f"本地排程器未启动：旧数据库 {self.db_path} 无法补齐 local_jobs 列 "
                f"（{detail}）。提醒与定时任务不会触发；请停止进程并备份该库，"
                "解除只读/清理磁盘空间后重试，或手动执行 ALTER TABLE 补列。"
            )

    # -- registration -----------------------------------------------------

    def register(
        self, name: str, handler: Handler, *, reentrant: bool = False
    ) -> None:
        """注册 handler。

        ``reentrant=False``（默认）表示**同一个副作用不允许执行两次**：进程
        在 handler 开始后崩溃/重启，该任务会直接进死信，不会被恢复重跑。
        只有明确幂等的 handler 才标 ``reentrant=True`` 允许崩溃后重试。
        """
        if not name or not callable(handler):
            raise ValueError("register() needs a non-empty name and an async callable")
        self._handlers[name] = handler
        self._reentrant[name] = bool(reentrant)

    def registered_jobs(self) -> List[str]:
        return sorted(self._handlers)

    def on_dead_letter(self, hook: DeadLetterHook) -> None:
        """注册死信回调（如提醒服务把重试耗尽的提醒标成 missed）。"""
        if not callable(hook):
            raise ValueError("on_dead_letter() needs a callable")
        self._dead_letter_hooks.append(hook)

    # -- producer ---------------------------------------------------------

    def enqueue(
        self,
        job_name: str,
        *,
        run_at: Union[None, str, datetime] = None,
        delay_seconds: float = 0.0,
        job_key: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
        backoff_multiplier: float = DEFAULT_BACKOFF_MULTIPLIER,
        max_backoff_seconds: float = DEFAULT_MAX_BACKOFF_SECONDS,
        concurrency_key: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
        reentrant: Optional[bool] = None,
    ) -> int:
        """Schedule ``job_name`` for an absolute time or a delay.

        ``job_key`` makes enqueue idempotent while the job is active: calling
        twice with the same key returns the existing id instead of creating a
        duplicate. ``max_attempts`` counts the first run as attempt 1.

        ``concurrency_key``（默认 ``job_name``）决定串行组：同一个 key 同时
        最多只有一条 running。``timeout_seconds`` 到点硬超时（失败进重试/死信）。
        活动行数达到 ``max_queue`` 时抛 :class:`QueueFull`，不无限堆积。
        """
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if backoff_seconds < 0 or max_backoff_seconds < 0:
            raise ValueError("backoff delays must be >= 0")
        if backoff_multiplier < 1:
            raise ValueError("backoff_multiplier must be >= 1")
        timeout = (
            self.job_timeout_seconds if timeout_seconds is None else float(timeout_seconds)
        )
        if timeout <= 0:
            raise ValueError("timeout_seconds must be > 0")

        absolute = _as_iso(run_at)
        if absolute is None:
            absolute = (self._now() + timedelta(seconds=max(0.0, delay_seconds))).isoformat()
        elif delay_seconds:
            raise ValueError("pass either run_at or delay_seconds, not both")

        key = job_key or uuid.uuid4().hex
        ckey = (concurrency_key or job_name).strip() or job_name
        flag = bool(self._reentrant.get(job_name, False)) if reentrant is None else bool(reentrant)
        now_iso = self._now().isoformat()
        params = (
            key,
            job_name,
            json.dumps(payload or {}, ensure_ascii=False, default=str),
            PENDING,
            absolute,
            0,
            max_attempts,
            float(backoff_seconds),
            float(backoff_multiplier),
            float(max_backoff_seconds),
            ckey,
            timeout,
            1 if flag else 0,
            now_iso,
            now_iso,
        )
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                # 幂等优先：已存在同 key 的活动任务直接返回，不受队列上限影响。
                existing = self._conn.execute(
                    "SELECT id FROM local_jobs"
                    " WHERE job_key = ? AND status IN ('pending','running','retry_wait')"
                    " ORDER BY id LIMIT 1",
                    (key,),
                ).fetchone()
                if existing is not None:
                    self._conn.execute("COMMIT")
                    return int(existing[0])
                active = int(
                    self._conn.execute(
                        "SELECT COUNT(*) FROM local_jobs"
                        " WHERE status IN ('pending','running','retry_wait')"
                    ).fetchone()[0]
                )
                if active >= self.max_queue:
                    raise QueueFull(active, self.max_queue)
                cur = self._conn.execute(
                    "INSERT INTO local_jobs"
                    " (job_key, job_name, payload, status, run_at, attempts,"
                    "  max_attempts, backoff_seconds, backoff_multiplier,"
                    "  max_backoff_seconds, concurrency_key, timeout_seconds,"
                    "  reentrant, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    params,
                )
                self._conn.execute("COMMIT")
                return int(cur.lastrowid)
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise

    def cancel_jobs_by_prefix(self, prefix: str) -> int:
        """按 ``job_key`` 前缀取消尚未完成的 job，返回取消条数。

        提醒取消/修改时用它把已经排在队列里的后续触发一并撤销：只动
        ``pending/running/retry_wait``（未来工作），已完成/已死信的历史记录
        保留不动。正在执行的 job 即使被标成 cancelled，handler 也会在业务层
        （提醒状态/版本）再次核对并跳过，不会重复响。
        """
        if not prefix:
            raise ValueError("prefix must be non-empty")
        now = self._now().isoformat()
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE local_jobs SET status = ?, updated_at = ?,"
                " lease_owner = NULL, lease_expires_at = NULL"
                " WHERE job_key LIKE ? AND status IN ('pending','running','retry_wait')",
                (CANCELLED, now, f"{prefix}%"),
            )
            return int(cursor.rowcount)

    # -- claim / state transitions ----------------------------------------

    def _claim_due(self, limit: int) -> List[Dict[str, Any]]:
        """Atomically claim up to ``limit`` due jobs, one at a time.

        Re-selecting after each update inside the same ``BEGIN IMMEDIATE``
        transaction is what enforces "one job per job_name at a time": the
        newly running row is visible to the next SELECT's NOT EXISTS check.
        """
        now = self._now().isoformat()
        lease_until = (self._now() + timedelta(seconds=self.lease_seconds)).isoformat()
        claimed: List[Dict[str, Any]] = []
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                for _ in range(max(0, limit)):
                    row = self._conn.execute(
                        "SELECT id, job_key, job_name, payload, attempts, max_attempts,"
                        " timeout_seconds"
                        " FROM local_jobs j"
                        " WHERE j.status IN ('pending','retry_wait')"
                        "   AND j.run_at <= ?"
                        "   AND NOT EXISTS ("
                        "     SELECT 1 FROM local_jobs r"
                        "     WHERE r.status = 'running'"
                        "       AND COALESCE(NULLIF(r.concurrency_key, ''), r.job_name)"
                        "         = COALESCE(NULLIF(j.concurrency_key, ''), j.job_name)"
                        "   )"
                        " ORDER BY j.run_at, j.id LIMIT 1",
                        (now,),
                    ).fetchone()
                    if row is None:
                        break
                    self._conn.execute(
                        "UPDATE local_jobs SET status = 'running',"
                        " attempts = attempts + 1, lease_owner = ?,"
                        " lease_expires_at = ?, exec_started_at = NULL, updated_at = ?"
                        " WHERE id = ?",
                        (self.owner, lease_until, now, row[0]),
                    )
                    claimed.append(
                        {
                            "id": int(row[0]),
                            "job_key": row[1],
                            "job_name": row[2],
                            "payload": json.loads(row[3]),
                            "attempts": int(row[4]) + 1,
                            "max_attempts": int(row[5]),
                            "timeout_seconds": float(row[6]) if row[6] is not None else None,
                        }
                    )
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
        return claimed

    def _complete(self, job_id: int, result: Any) -> None:
        now = self._now().isoformat()
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE local_jobs SET status = 'completed', result = ?,"
                " completed_at = ?, updated_at = ?, lease_owner = NULL,"
                " lease_expires_at = NULL"
                " WHERE id = ? AND status = 'running'",
                (json.dumps(result, ensure_ascii=False, default=str), now, now, job_id),
            )
        if cursor.rowcount == 0:
            # 已被取消（cancel_jobs_by_prefix）或已被租约回收：不能覆盖终态。
            logger.info(
                "local job %s finished after it was no longer running; "
                "result discarded (status preserved)",
                job_id,
            )

    def _fail(self, job: Dict[str, Any], error: str) -> None:
        attempt = int(job["attempts"])
        max_attempts = int(job["max_attempts"])
        with self._lock:
            row = self._conn.execute(
                "SELECT backoff_seconds, backoff_multiplier, max_backoff_seconds"
                " FROM local_jobs WHERE id = ?",
                (job["id"],),
            ).fetchone()
        if row is None:  # pragma: no cover - 行被外部删除
            logger.error("local job %s vanished before failure handling", job["id"])
            return
        if attempt >= max_attempts:
            self._dead_letter(job, error, attempts=attempt)
            return
        delay = min(float(row[2]), float(row[0]) * (float(row[1]) ** (attempt - 1)))
        retry_at = (self._now() + timedelta(seconds=delay)).isoformat()
        now = self._now().isoformat()
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE local_jobs SET status = 'retry_wait', run_at = ?,"
                " last_error = ?, updated_at = ?, lease_owner = NULL,"
                " lease_expires_at = NULL"
                " WHERE id = ? AND status = 'running'",
                (retry_at, error[:1000], now, job["id"]),
            )
        if cursor.rowcount == 0:
            logger.info(
                "local job %s failed after being cancelled; state preserved",
                job["id"],
            )
            return
        logger.warning(
            "local job %s (%s) attempt %d/%d failed; retry in %.1fs: %s",
            job["id"],
            job["job_name"],
            attempt,
            max_attempts,
            delay,
            error[:200],
        )

    def _dead_letter(self, job: Dict[str, Any], error: str, attempts: int) -> None:
        now = self._now().isoformat()
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE local_jobs SET status = 'dead', last_error = ?,"
                " dead_lettered_at = ?, updated_at = ?, lease_owner = NULL,"
                " lease_expires_at = NULL"
                " WHERE id = ? AND status = 'running'",
                (error[:1000], now, now, job["id"]),
            )
        if cursor.rowcount == 0:
            # 已被取消或已由别处结束：死信不能覆盖 cancelled 等终态。
            logger.info(
                "local job %s would be dead-lettered but is no longer running; "
                "state preserved",
                job["id"],
            )
            return
        logger.error(
            "local job %s (%s) exhausted %d attempts -> dead letter: %s",
            job["id"],
            job["job_name"],
            attempts,
            error[:200],
        )
        self.audit_event(
            "local_scheduler.dead_letter",
            "failure",
            {
                "job_id": job["id"],
                "job_key": job.get("job_key"),
                "job_name": job["job_name"],
                "attempts": attempts,
                "max_attempts": job["max_attempts"],
                "error": error[:500],
            },
        )
        info = {
            "id": job["id"],
            "job_key": job.get("job_key"),
            "job_name": job["job_name"],
            "payload": dict(job.get("payload") or {}),
            "attempts": attempts,
            "last_error": error[:1000],
        }
        for hook in list(self._dead_letter_hooks):
            try:
                hook(info)
            except Exception:  # noqa: BLE001 - 死信已落盘，回调失败不能回滚
                logger.exception(
                    "local scheduler dead-letter hook failed (job_id=%s)", job["id"]
                )

    def _release(self, job_id: int) -> None:
        """Graceful shutdown during execution: give the attempt back."""
        now = self._now().isoformat()
        with self._lock:
            self._conn.execute(
                "UPDATE local_jobs SET status = 'pending', run_at = ?,"
                " attempts = CASE WHEN attempts > 0 THEN attempts - 1 ELSE 0 END,"
                " updated_at = ?, lease_owner = NULL, lease_expires_at = NULL"
                " WHERE id = ? AND status = 'running'",
                (now, now, job_id),
            )

    # -- recovery ---------------------------------------------------------

    def recover_expired_leases(self, *, force: bool = False) -> int:
        """Requeue rows whose worker died — survives restart.

        ``force=True`` is used once at :meth:`run` startup: this module assumes
        **one scheduler process per database**, so every row still marked
        ``running`` at startup belongs to a dead predecessor and is reclaimed
        immediately instead of waiting out its lease. While running, the
        periodic sweep stays lease-based (``force=False``) so a live lease is
        never stolen.
        """
        now = self._now()
        now_iso = now.isoformat()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                if force:
                    rows = self._conn.execute(
                        "SELECT id, attempts, max_attempts, reentrant, exec_started_at"
                        " FROM local_jobs WHERE status = 'running'"
                    ).fetchall()
                else:
                    # 回收过期租约；本进程正在执行的 job（在 _active_ids 里）
                    # 跳过，避免"没显式心跳就被自己当崩溃回收"→同任务跑两遍。
                    rows = self._conn.execute(
                        "SELECT id, attempts, max_attempts, reentrant, exec_started_at"
                        " FROM local_jobs WHERE status = 'running'"
                        " AND lease_expires_at IS NOT NULL AND lease_expires_at < ?",
                        (now_iso,),
                    ).fetchall()
                for job_id, attempts, max_attempts, reentrant, exec_started_at in rows:
                    if job_id in self._active_ids:
                        continue
                    if exec_started_at and not reentrant:
                        # 默认口径：不可重入。handler 已开始 = 副作用可能已发生，
                        # 重启后再跑就是第二条副作用 → 直接死信，不重跑。
                        self._conn.execute(
                            "UPDATE local_jobs SET status = 'dead',"
                            " last_error = ?, dead_lettered_at = ?, updated_at = ?,"
                            " lease_owner = NULL, lease_expires_at = NULL"
                            " WHERE id = ?",
                            (
                                "执行已开始后进程退出；任务默认不可重入，"
                                "为避免重复副作用不再重跑",
                                now_iso,
                                now_iso,
                                job_id,
                            ),
                        )
                        continue
                    if attempts >= max_attempts:
                        self._conn.execute(
                            "UPDATE local_jobs SET status = 'dead',"
                            " last_error = 'lease expired (worker crashed)',"
                            " dead_lettered_at = ?, updated_at = ?,"
                            " lease_owner = NULL, lease_expires_at = NULL"
                            " WHERE id = ?",
                            (now_iso, now_iso, job_id),
                        )
                    else:
                        self._conn.execute(
                            "UPDATE local_jobs SET status = 'retry_wait',"
                            " run_at = ?, last_error = 'lease expired (worker crashed)',"
                            " updated_at = ?, lease_owner = NULL,"
                            " lease_expires_at = NULL WHERE id = ?",
                            (now_iso, now_iso, job_id),
                        )
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
        if rows:
            logger.warning(
                "local scheduler recovered %d crashed job lease(s): %s",
                len(rows),
                ", ".join(str(r[0]) for r in rows[:20]),
            )
            self.audit_event(
                "local_scheduler.recovered",
                "attention",
                {"count": len(rows), "job_ids": [int(r[0]) for r in rows]},
            )
        return len(rows)

    def heartbeat(self, job_id: Optional[int] = None) -> None:
        """handler 显式上报进度：只续**自己**的租约。

        本进程不再每个 tick 全量续租，避免挂死的 handler 永远显示 running。
        需要长时间执行的任务应在循环里调用 ``scheduler.heartbeat()``；不调用
        也不会被本进程误回收（回收只针对别的 owner），但会被超时兜底。
        """
        target = job_id if job_id is not None else _CURRENT_JOB_ID.get()
        if target is None:
            return
        lease_until = (self._now() + timedelta(seconds=self.lease_seconds)).isoformat()
        with self._lock:
            self._conn.execute(
                "UPDATE local_jobs SET lease_expires_at = ?, updated_at = ?"
                " WHERE id = ? AND status = 'running' AND lease_owner = ?",
                (lease_until, self._now().isoformat(), target, self.owner),
            )

    def _mark_executing(self, job_id: int) -> None:
        """记下「handler 真正开始」的时刻；恢复时据此判断副作用是否可能已发生。"""
        now = self._now().isoformat()
        with self._lock:
            self._conn.execute(
                "UPDATE local_jobs SET exec_started_at = ?, updated_at = ?"
                " WHERE id = ? AND status = 'running' AND exec_started_at IS NULL",
                (now, now, job_id),
            )

    # -- consumer loop ----------------------------------------------------

    async def _execute(self, job: Dict[str, Any]) -> None:
        self._active_ids.add(job["id"])
        token = _CURRENT_JOB_ID.set(job["id"])
        try:
            handler = self._handlers.get(job["job_name"])
            if handler is None:
                # 未知 handler 是配置错误：直接死信，不静默卡在 pending。
                self._dead_letter(
                    job,
                    f"handler not registered: {job['job_name']}",
                    attempts=job["attempts"],
                )
                return
            # handler 真正开始前先落「执行已开始」标记：崩溃恢复据此判断
            # 副作用是否可能已经发生（默认不可重入的任务不会被重跑）。
            self._mark_executing(job["id"])
            timeout = job.get("timeout_seconds") or self.job_timeout_seconds
            try:
                result = await asyncio.wait_for(
                    handler(**job["payload"]), timeout=float(timeout)
                )
            except asyncio.TimeoutError:
                self._fail(
                    job,
                    f"TimeoutError: handler exceeded {float(timeout):g}s "
                    "and was cancelled",
                )
            except asyncio.CancelledError:
                self._release(job["id"])
                raise
            except Exception as exc:  # noqa: BLE001 - 失败必须落 retry/dead，不能吞
                self._fail(job, f"{type(exc).__name__}: {exc}")
            else:
                self._complete(job["id"], result)
        finally:
            _CURRENT_JOB_ID.reset(token)
            self._active_ids.discard(job["id"])

    async def _tick(self) -> None:
        self._ticks += 1
        # 不再每 tick 全量续租：租约只由 handler 显式 heartbeat() 续。
        # 每 30 个 tick 扫一次**别人**的过期租约（本进程的行不会被回收）。
        if self._ticks % 30 == 0:
            self.recover_expired_leases()
        slots = self.max_concurrency - len(self._active)
        if slots <= 0:
            return
        for job in self._claim_due(slots):
            task = asyncio.create_task(self._execute(job))
            self._active.add(task)
            task.add_done_callback(self._active.discard)

    async def run(self) -> None:
        """Run until :meth:`stop` is called. Call after registering handlers."""
        self._running = True
        self._stop.clear()
        # 启动即回收上一个进程崩溃时留下的 running 行（force：不等租约）。
        self.recover_expired_leases(force=True)
        while self._running:
            await self._tick()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=self.tick_seconds)
        if self._active:
            for task in list(self._active):
                task.cancel()
            await asyncio.gather(*list(self._active), return_exceptions=True)

    @property
    def running(self) -> bool:
        """``run()`` 是否已进入主循环。

        托管者（网关）在**别的线程**里启动它，需要一个可观测信号确认
        "协程真的跑起来了"，而不是"提交了任务就当成功"。
        """
        return self._running

    def stop(self) -> None:
        self._running = False
        self._stop.set()

    # -- inspection / hooks ---------------------------------------------

    def now(self) -> datetime:
        """The scheduler's clock (fake-able in tests)."""
        return self._now()

    def audit_event(self, action: str, outcome: str, details: Dict[str, Any]) -> None:
        """Emit an audit event; failure is logged, never propagates.

        Durable state is written **before** this is called (dead letters and
        lease recovery are already committed), so a broken audit sink must not
        roll the transition back or kill the loop.
        """
        try:
            self._audit_sink(action, outcome, details)
        except Exception:  # noqa: BLE001
            logger.exception("local scheduler audit write failed (action=%s)", action)

    def get_job(self, job_id: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, job_key, job_name, payload, status, run_at, attempts,"
                " max_attempts, last_error, result, created_at, completed_at,"
                " dead_lettered_at, concurrency_key, timeout_seconds, reentrant"
                " FROM local_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "id": int(row[0]),
            "job_key": row[1],
            "job_name": row[2],
            "payload": json.loads(row[3]),
            "status": row[4],
            "run_at": row[5],
            "attempts": int(row[6]),
            "max_attempts": int(row[7]),
            "last_error": row[8],
            "result": json.loads(row[9]) if row[9] else None,
            "created_at": row[10],
            "completed_at": row[11],
            "dead_lettered_at": row[12],
            "concurrency_key": row[13],
            "timeout_seconds": float(row[14]) if row[14] is not None else None,
            "reentrant": bool(row[15]),
        }

    def list_jobs(self, status: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        """Inspect jobs by status (``None`` = any), newest first."""
        with self._lock:
            if status is None:
                rows = self._conn.execute(
                    "SELECT id FROM local_jobs ORDER BY id DESC LIMIT ?",
                    (int(limit),),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT id FROM local_jobs WHERE status = ?"
                    " ORDER BY id DESC LIMIT ?",
                    (status, int(limit)),
                ).fetchall()
        return [job for row in rows if (job := self.get_job(int(row[0])))]  # type: ignore[misc]

    def list_dead_letters(self, limit: int = 100) -> List[Dict[str, Any]]:
        return self.list_jobs(DEAD, limit)

    def dead_letter_count(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM local_jobs WHERE status = 'dead'"
            ).fetchone()
        return int(row[0])

    def stats(self) -> Dict[str, int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT status, COUNT(*) FROM local_jobs GROUP BY status"
            ).fetchall()
        return {str(status): int(count) for status, count in rows}

    def close(self) -> None:
        with self._lock:
            self._conn.close()
