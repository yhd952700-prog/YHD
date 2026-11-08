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
* 同一 `job_name` 不并发执行：领取时在事务内检查「是否已有同名 running 行」。
* 进程内并发上限：`max_concurrency` 在领任务前按空槽计数（默认 4，env
  `LIUHAO_LOCAL_SCHEDULER_CONCURRENCY`）。
* 租约（lease）心跳：长任务执行期间每次 tick 刷新租约，避免别的进程误判为崩溃
  而重复领取；进程硬死后的租约过期才会被回收。
* 幂等入队：同一 `job_key` 在 active 状态（pending/running/retry_wait）内唯一；
  重复 `enqueue` 返回已存在的 job id，不产生第二条。

## 死信可见性

耗尽重试的 job 变成 `status='dead'`，保留 `last_error` / `attempts` /
`dead_lettered_at`，可 `list_dead_letters()` 查询；同时写审计事件
`local_scheduler.dead_letter`（审计控制台可直接看到）。审计写失败只记 ERROR
日志、**不会**丢死信行 —— 死信先落盘，再通知。
"""

from __future__ import annotations

import asyncio
import contextlib
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

PENDING = "pending"
RUNNING = "running"
RETRY_WAIT = "retry_wait"
COMPLETED = "completed"
DEAD = "dead"
CANCELLED = "cancelled"
ACTIVE_STATUSES = (PENDING, RUNNING, RETRY_WAIT)

DEFAULT_MAX_CONCURRENCY = 4
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF_SECONDS = 2.0
DEFAULT_BACKOFF_MULTIPLIER = 2.0
DEFAULT_MAX_BACKOFF_SECONDS = 300.0
DEFAULT_LEASE_SECONDS = 60.0
DEFAULT_TICK_SECONDS = 0.5

Handler = Callable[..., Awaitable[Any]]
AuditSink = Callable[[str, str, Dict[str, Any]], None]

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


def default_scheduler_db() -> str:
    explicit = os.environ.get("LIUHAO_LOCAL_SCHEDULER_DB")
    if explicit:
        return explicit
    workspace = os.environ.get("LIUHAO_WORKSPACE_ROOT")
    base = (
        os.path.join(workspace, "queue")
        if workspace
        else os.path.join(os.path.expanduser("~"), ".liuhao", "queue")
    )
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "local_scheduler.db")


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
        self.lease_seconds = lease_seconds
        self.tick_seconds = tick_seconds
        self.owner = owner or f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        self._now = now_fn
        self._audit_sink = audit_sink or _default_audit
        self._handlers: Dict[str, Handler] = {}
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
        self._conn.executescript(_SCHEMA)
        self._lock = threading.Lock()

    # -- registration -----------------------------------------------------

    def register(self, name: str, handler: Handler) -> None:
        if not name or not callable(handler):
            raise ValueError("register() needs a non-empty name and an async callable")
        self._handlers[name] = handler

    def registered_jobs(self) -> List[str]:
        return sorted(self._handlers)

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
    ) -> int:
        """Schedule ``job_name`` for an absolute time or a delay.

        ``job_key`` makes enqueue idempotent while the job is active: calling
        twice with the same key returns the existing id instead of creating a
        duplicate. ``max_attempts`` counts the first run as attempt 1.
        """
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if backoff_seconds < 0 or max_backoff_seconds < 0:
            raise ValueError("backoff delays must be >= 0")
        if backoff_multiplier < 1:
            raise ValueError("backoff_multiplier must be >= 1")

        absolute = _as_iso(run_at)
        if absolute is None:
            absolute = (self._now() + timedelta(seconds=max(0.0, delay_seconds))).isoformat()
        elif delay_seconds:
            raise ValueError("pass either run_at or delay_seconds, not both")

        key = job_key or uuid.uuid4().hex
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
            now_iso,
            now_iso,
        )
        with self._lock:
            try:
                cur = self._conn.execute(
                    "INSERT INTO local_jobs"
                    " (job_key, job_name, payload, status, run_at, attempts,"
                    "  max_attempts, backoff_seconds, backoff_multiplier,"
                    "  max_backoff_seconds, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    params,
                )
            except sqlite3.IntegrityError:
                row = self._conn.execute(
                    "SELECT id FROM local_jobs"
                    " WHERE job_key = ? AND status IN ('pending','running','retry_wait')"
                    " ORDER BY id LIMIT 1",
                    (key,),
                ).fetchone()
                if row is None:  # pragma: no cover - 索引冲突理论上必有 active 行
                    raise
                return int(row[0])
            return int(cur.lastrowid)

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
                        "SELECT id, job_key, job_name, payload, attempts, max_attempts"
                        " FROM local_jobs j"
                        " WHERE j.status IN ('pending','retry_wait')"
                        "   AND j.run_at <= ?"
                        "   AND NOT EXISTS ("
                        "     SELECT 1 FROM local_jobs r"
                        "     WHERE r.status = 'running' AND r.job_name = j.job_name"
                        "   )"
                        " ORDER BY j.run_at, j.id LIMIT 1",
                        (now,),
                    ).fetchone()
                    if row is None:
                        break
                    self._conn.execute(
                        "UPDATE local_jobs SET status = 'running',"
                        " attempts = attempts + 1, lease_owner = ?,"
                        " lease_expires_at = ?, updated_at = ?"
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
            self._conn.execute(
                "UPDATE local_jobs SET status = 'completed', result = ?,"
                " completed_at = ?, updated_at = ?, lease_owner = NULL,"
                " lease_expires_at = NULL WHERE id = ?",
                (json.dumps(result, ensure_ascii=False, default=str), now, now, job_id),
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
            self._conn.execute(
                "UPDATE local_jobs SET status = 'retry_wait', run_at = ?,"
                " last_error = ?, updated_at = ?, lease_owner = NULL,"
                " lease_expires_at = NULL WHERE id = ?",
                (retry_at, error[:1000], now, job["id"]),
            )
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
            self._conn.execute(
                "UPDATE local_jobs SET status = 'dead', last_error = ?,"
                " dead_lettered_at = ?, updated_at = ?, lease_owner = NULL,"
                " lease_expires_at = NULL WHERE id = ?",
                (error[:1000], now, now, job["id"]),
            )
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
                        "SELECT id, attempts, max_attempts"
                        " FROM local_jobs WHERE status = 'running'"
                    ).fetchall()
                else:
                    rows = self._conn.execute(
                        "SELECT id, attempts, max_attempts"
                        " FROM local_jobs WHERE status = 'running'"
                        " AND lease_expires_at IS NOT NULL AND lease_expires_at < ?",
                        (now_iso,),
                    ).fetchall()
                for job_id, attempts, max_attempts in rows:
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

    def _refresh_leases(self) -> None:
        if not self._active_ids:
            return
        lease_until = (self._now() + timedelta(seconds=self.lease_seconds)).isoformat()
        ids = tuple(self._active_ids)
        placeholders = ",".join("?" for _ in ids)
        with self._lock:
            self._conn.execute(
                f"UPDATE local_jobs SET lease_expires_at = ?"
                f" WHERE status = 'running' AND lease_owner = ?"
                f" AND id IN ({placeholders})",
                (lease_until, self.owner, *ids),
            )

    # -- consumer loop ----------------------------------------------------

    async def _execute(self, job: Dict[str, Any]) -> None:
        self._active_ids.add(job["id"])
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
            try:
                result = await handler(**job["payload"])
            except asyncio.CancelledError:
                self._release(job["id"])
                raise
            except Exception as exc:  # noqa: BLE001 - 失败必须落 retry/dead，不能吞
                self._fail(job, f"{type(exc).__name__}: {exc}")
            else:
                self._complete(job["id"], result)
        finally:
            self._active_ids.discard(job["id"])

    async def _tick(self) -> None:
        self._ticks += 1
        self._refresh_leases()
        # 每 30 个 tick 扫一次别人的过期租约（崩溃恢复）。
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
                " dead_lettered_at FROM local_jobs WHERE id = ?",
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
