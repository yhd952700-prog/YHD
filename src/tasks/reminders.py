"""定时提醒：状态存储 + 业务服务（复用 ``LocalScheduler``，不另起调度器）。

可靠性完全沿用 ``src/tasks/local_scheduler.py``：

* **幂等入队** —— 每次提醒用 ``reminder:{id}:v{version}:{occurrence}`` 作为
  ``job_key``，重复投递不会产生第二条；
* **失败重试 / 指数退避 / 死信** —— 通知失败就让 handler 抛错，由排程器重试，
  耗尽后进死信（`local_scheduler.dead_letter` 审计 + ``list_dead_letters``）；
* **重启续跑** —— 状态全在 SQLite，排程器启动时会接着跑到期任务。

本模块只做"提醒是什么、什么时候响、响了怎么记账"，不写第二套调度循环。

## 可见性

提醒触发时通过 ``reminder_notify`` 的通道（Windows 托盘气泡 / 控制台横幅 +
落地 JSONL）让用户看到；**不是只写日志**。每次投递都写
``reminder_deliveries``，失败原因可在 ``failures()`` 里查到。

## 取消 / 修改

取消会把提醒标成 ``cancelled`` 并按前缀取消排程器里尚未执行的 job；
修改会 ``version + 1``、取消旧 job、按新时间重新入队。触发时 handler 会再次
核对 ``status``/``version``，因此旧版本即使已被领取也会被跳过 —— 取消后不会响。
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from src._time import utc_now
from src.tasks.local_scheduler import LocalScheduler
from src.tasks.reminder_notify import (
    Notifier,
    NotifyResult,
    build_default_notifier,
)
from src.tasks.reminder_time import (
    WEEKDAY_NAMES,
    NeedsClarification,
    ReminderSpec,
    local_timezone,
    parse_reminder,
)

REMINDER_FIRE_JOB = "liuhao.reminder.fire"

# 提醒的"下一次/这一次"全部用这个前缀做 job_key，取消时按前缀批量取消。
REMINDER_KEY_PREFIX = "reminder"

DEFAULT_FIRE_MAX_ATTEMPTS = 5
DEFAULT_FIRE_BACKOFF_SECONDS = 5.0
DEFAULT_FIRE_BACKOFF_MULTIPLIER = 2.0
DEFAULT_FIRE_MAX_BACKOFF_SECONDS = 60.0

_SCHEMA = """
CREATE TABLE IF NOT EXISTS reminders (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    title       TEXT NOT NULL,
    source_text TEXT NOT NULL DEFAULT '',
    time_text   TEXT NOT NULL DEFAULT '',
    repeat_kind TEXT NOT NULL,
    weekday     INTEGER,
    hour        INTEGER,
    minute      INTEGER,
    run_at      TEXT NOT NULL,
    version     INTEGER NOT NULL DEFAULT 1,
    status      TEXT NOT NULL DEFAULT 'active',
    last_fired_at TEXT,
    last_error  TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reminders_user
    ON reminders(user_id, status);
CREATE INDEX IF NOT EXISTS idx_reminders_due
    ON reminders(status, run_at);

CREATE TABLE IF NOT EXISTS reminder_deliveries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    reminder_id TEXT NOT NULL,
    user_id     TEXT NOT NULL,
    occurrence  TEXT NOT NULL,
    status      TEXT NOT NULL,
    channels    TEXT,
    error       TEXT,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reminder_deliveries_user
    ON reminder_deliveries(user_id, id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_reminder_deliveries_delivered
    ON reminder_deliveries(reminder_id, occurrence) WHERE status = 'delivered';
"""


class ReminderDeliveryError(RuntimeError):
    """通知没有任何通道成功；抛给排程器以触发重试/死信。"""


def default_reminders_db() -> str:
    explicit = os.environ.get("LIUHAO_REMINDERS_DB")
    if explicit:
        return explicit
    workspace = os.environ.get("LIUHAO_WORKSPACE_ROOT")
    base = (
        os.path.join(workspace, "queue")
        if workspace
        else os.path.join(os.path.expanduser("~"), ".liuhao", "queue")
    )
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "reminders.db")


def reminder_job_key(reminder_id: str, version: int, occurrence: str) -> str:
    return f"{REMINDER_KEY_PREFIX}:{reminder_id}:v{int(version)}:{occurrence}"


def reminder_key_prefix(reminder_id: str) -> str:
    return f"{REMINDER_KEY_PREFIX}:{reminder_id}:"


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


class ReminderStore:
    """提醒与投递记录的 SQLite 存储（WAL，跨进程可读）。"""

    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = db_path or default_reminders_db()
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

    # -- reminders --------------------------------------------------------

    def create(self, *, reminder_id: str, user_id: str, spec: ReminderSpec, now_iso: str) -> Dict[str, Any]:
        with self._lock:
            self._conn.execute(
                "INSERT INTO reminders (id, user_id, title, source_text, time_text,"
                " repeat_kind, weekday, hour, minute, run_at, version, status,"
                " created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'active', ?, ?)",
                (
                    reminder_id,
                    user_id,
                    spec.title,
                    spec.source_text,
                    spec.time_text,
                    spec.repeat_kind,
                    spec.weekday,
                    spec.hour,
                    spec.minute,
                    _iso(spec.run_at),
                    now_iso,
                    now_iso,
                ),
            )
        return self.get(reminder_id, user_id=user_id)  # type: ignore[return-value]

    def get(self, reminder_id: str, user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        with self._lock:
            if user_id is None:
                row = self._conn.execute(
                    "SELECT * FROM reminders WHERE id = ?", (reminder_id,)
                ).fetchone()
            else:
                row = self._conn.execute(
                    "SELECT * FROM reminders WHERE id = ? AND user_id = ?",
                    (reminder_id, user_id),
                ).fetchone()
            columns = [item[0] for item in self._conn.execute(
                "SELECT * FROM reminders LIMIT 0"
            ).description]
        return dict(zip(columns, row)) if row else None

    def list(
        self,
        user_id: str,
        *,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        with self._lock:
            if status is None:
                rows = self._conn.execute(
                    "SELECT id FROM reminders WHERE user_id = ?"
                    " ORDER BY created_at DESC, rowid DESC LIMIT ?",
                    (user_id, int(limit)),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT id FROM reminders WHERE user_id = ? AND status = ?"
                    " ORDER BY created_at DESC, rowid DESC LIMIT ?",
                    (user_id, status, int(limit)),
                ).fetchall()
        return [item for row in rows if (item := self.get(str(row[0])))]  # type: ignore[misc]

    def cancel(self, reminder_id: str, user_id: str, now_iso: str) -> bool:
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE reminders SET status = 'cancelled', updated_at = ?"
                " WHERE id = ? AND user_id = ? AND status = 'active'",
                (now_iso, reminder_id, user_id),
            )
            return int(cursor.rowcount) > 0

    def rearm(
        self,
        reminder_id: str,
        user_id: str,
        *,
        spec: ReminderSpec,
        version: int,
        now_iso: str,
    ) -> Optional[Dict[str, Any]]:
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE reminders SET title = ?, source_text = ?, time_text = ?,"
                " repeat_kind = ?, weekday = ?, hour = ?, minute = ?, run_at = ?,"
                " version = ?, status = 'active', last_error = NULL, updated_at = ?"
                " WHERE id = ? AND user_id = ? AND status = 'active'",
                (
                    spec.title,
                    spec.source_text,
                    spec.time_text,
                    spec.repeat_kind,
                    spec.weekday,
                    spec.hour,
                    spec.minute,
                    _iso(spec.run_at),
                    int(version),
                    now_iso,
                    reminder_id,
                    user_id,
                ),
            )
            changed = int(cursor.rowcount) > 0
        return self.get(reminder_id, user_id=user_id) if changed else None

    def mark_fired(
        self,
        reminder_id: str,
        fired_at: str,
        *,
        next_run_at: Optional[str] = None,
        now_iso: str,
    ) -> None:
        with self._lock:
            if next_run_at is None:
                self._conn.execute(
                    "UPDATE reminders SET status = 'completed', last_fired_at = ?,"
                    " last_error = NULL, updated_at = ? WHERE id = ?",
                    (fired_at, now_iso, reminder_id),
                )
            else:
                self._conn.execute(
                    "UPDATE reminders SET run_at = ?, last_fired_at = ?,"
                    " last_error = NULL, updated_at = ? WHERE id = ?",
                    (next_run_at, fired_at, now_iso, reminder_id),
                )

    def set_run_at(self, reminder_id: str, run_at: str, now_iso: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE reminders SET run_at = ?, updated_at = ? WHERE id = ?",
                (run_at, now_iso, reminder_id),
            )

    def set_error(self, reminder_id: str, error: str, now_iso: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE reminders SET last_error = ?, updated_at = ? WHERE id = ?",
                (error[:1000], now_iso, reminder_id),
            )

    def clear_error(self, reminder_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE reminders SET last_error = NULL WHERE id = ?",
                (reminder_id,),
            )

    # -- deliveries -------------------------------------------------------

    def has_delivered(self, reminder_id: str, occurrence: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM reminder_deliveries"
                " WHERE reminder_id = ? AND occurrence = ? AND status = 'delivered'"
                " LIMIT 1",
                (reminder_id, occurrence),
            ).fetchone()
        return row is not None

    def record_delivery(
        self,
        *,
        reminder_id: str,
        user_id: str,
        occurrence: str,
        status: str,
        channels: List[Dict[str, Any]],
        error: Optional[str],
        now_iso: str,
    ) -> bool:
        """写入投递记录；已存在同一 occurrence 的成功记录时返回 False。"""
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO reminder_deliveries"
                    " (reminder_id, user_id, occurrence, status, channels, error, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        reminder_id,
                        user_id,
                        occurrence,
                        status,
                        json.dumps(channels, ensure_ascii=False, default=str),
                        error,
                        now_iso,
                    ),
                )
            except sqlite3.IntegrityError:
                return False
        return True

    def list_deliveries(
        self,
        user_id: str,
        *,
        reminder_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        query = "SELECT * FROM reminder_deliveries WHERE user_id = ?"
        params: List[Any] = [user_id]
        if reminder_id:
            query += " AND reminder_id = ?"
            params.append(reminder_id)
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(int(limit))
        with self._lock:
            rows = self._conn.execute(query, tuple(params)).fetchall()
            columns = [item[0] for item in self._conn.execute(
                "SELECT * FROM reminder_deliveries LIMIT 0"
            ).description]
        items = []
        for row in rows:
            item = dict(zip(columns, row))
            try:
                item["channels"] = json.loads(item["channels"]) if item["channels"] else []
            except (json.JSONDecodeError, TypeError):
                item["channels"] = []
            items.append(item)
        return items

    def close(self) -> None:
        with self._lock:
            self._conn.close()


class ReminderService:
    """创建 / 查看 / 取消 / 修改提醒，并在排程器触发时投递 + 记账。"""

    def __init__(
        self,
        scheduler: LocalScheduler,
        *,
        store: Optional[ReminderStore] = None,
        notifier: Optional[Notifier] = None,
        tz=None,
        max_attempts: int = DEFAULT_FIRE_MAX_ATTEMPTS,
        backoff_seconds: float = DEFAULT_FIRE_BACKOFF_SECONDS,
        backoff_multiplier: float = DEFAULT_FIRE_BACKOFF_MULTIPLIER,
        max_backoff_seconds: float = DEFAULT_FIRE_MAX_BACKOFF_SECONDS,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        self.scheduler = scheduler
        self.store = store or ReminderStore()
        self.notifier = notifier or build_default_notifier()
        self.tz = tz or local_timezone()
        self.max_attempts = max_attempts
        self.backoff_seconds = backoff_seconds
        self.backoff_multiplier = backoff_multiplier
        self.max_backoff_seconds = max_backoff_seconds

    # -- CRUD -------------------------------------------------------------

    def create(self, user_id: str, text: str, *, now: Optional[datetime] = None) -> Dict[str, Any]:
        now_dt = now if now is not None else self.scheduler.now()
        spec = parse_reminder(text, now_dt, tz=self.tz)
        reminder_id = uuid.uuid4().hex[:12]
        record = self.store.create(
            reminder_id=reminder_id,
            user_id=user_id,
            spec=spec,
            now_iso=_iso(now_dt),
        )
        self._enqueue(record, _iso(spec.run_at))
        self._audit(
            "reminder.created",
            "success",
            {
                "reminder_id": reminder_id,
                "repeat": spec.repeat_kind,
                "run_at": _iso(spec.run_at),
                "title": spec.title,
            },
        )
        return self.describe(record)

    def list(self, user_id: str, *, include_cancelled: bool = True) -> List[Dict[str, Any]]:
        records = self.store.list(user_id)
        if not include_cancelled:
            records = [item for item in records if item["status"] == "active"]
        return [self.describe(item) for item in records]

    def get(self, user_id: str, reminder_id: str) -> Optional[Dict[str, Any]]:
        record = self.store.get(reminder_id, user_id=user_id)
        return self.describe(record) if record else None

    def cancel(self, user_id: str, reminder_id: str) -> Dict[str, Any]:
        record = self.store.get(reminder_id, user_id=user_id)
        if record is None:
            return {"status": "not_found", "message": "没有这个提醒。"}
        if record["status"] != "active":
            return {
                "status": "noop",
                "message": f"提醒已经是「{record['status']}」，无需取消。",
                "reminder": self.describe(record),
            }
        now_iso = _iso(self.scheduler.now())
        if not self.store.cancel(reminder_id, user_id, now_iso):
            current = self.store.get(reminder_id, user_id=user_id)
            return {
                "status": "noop",
                "message": "提醒状态已变化，没有取消。",
                "reminder": self.describe(current) if current else None,
            }
        cancelled_jobs = self.scheduler.cancel_jobs_by_prefix(
            reminder_key_prefix(reminder_id)
        )
        self._audit(
            "reminder.cancelled",
            "success",
            {"reminder_id": reminder_id, "cancelled_jobs": cancelled_jobs},
        )
        current = self.store.get(reminder_id, user_id=user_id)
        return {
            "status": "cancelled",
            "message": "已取消，之后不会再响。",
            "cancelled_jobs": cancelled_jobs,
            "reminder": self.describe(current) if current else None,
        }

    def update(self, user_id: str, reminder_id: str, text: str) -> Dict[str, Any]:
        record = self.store.get(reminder_id, user_id=user_id)
        if record is None:
            return {"status": "not_found", "message": "没有这个提醒。"}
        if record["status"] != "active":
            return {
                "status": "noop",
                "message": f"提醒已经是「{record['status']}」，不能修改。",
                "reminder": self.describe(record),
            }
        now_dt = self.scheduler.now()
        spec = parse_reminder(text, now_dt, tz=self.tz)
        new_version = int(record["version"]) + 1

        rearmed = self.store.rearm(
            reminder_id,
            user_id,
            spec=spec,
            version=new_version,
            now_iso=_iso(now_dt),
        )
        if rearmed is None:
            return {"status": "noop", "message": "提醒状态已变化，没有修改。"}

        # 先取消旧版本（含已经到期但还没执行的 job），再排新版本；
        # 顺序反了会把刚入队的新 job 一起取消。
        cancelled_jobs = self.scheduler.cancel_jobs_by_prefix(
            reminder_key_prefix(reminder_id)
        )
        self._enqueue(rearmed, _iso(spec.run_at))
        self._audit(
            "reminder.updated",
            "success",
            {
                "reminder_id": reminder_id,
                "version": new_version,
                "repeat": spec.repeat_kind,
                "run_at": _iso(spec.run_at),
                "cancelled_jobs": cancelled_jobs,
            },
        )
        return {"status": "updated", "reminder": self.describe(rearmed)}

    def list_deliveries(
        self,
        user_id: str,
        *,
        reminder_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        return self.store.list_deliveries(
            user_id, reminder_id=reminder_id, status=status, limit=limit
        )

    def failures(self, user_id: str, *, limit: int = 50) -> Dict[str, Any]:
        """失败的提醒：投递失败原因 + 排程器死信，两者都能说明为什么没响。"""
        deliveries = self.store.list_deliveries(
            user_id, status="failed", limit=limit
        )
        dead_jobs = []
        for job in self.scheduler.list_dead_letters(limit=max(limit, 100)):
            if job.get("job_name") != REMINDER_FIRE_JOB:
                continue
            payload = job.get("payload") or {}
            if payload.get("user_id") != user_id:
                continue
            dead_jobs.append(
                {
                    "job_id": job.get("id"),
                    "reminder_id": payload.get("reminder_id"),
                    "title": payload.get("title"),
                    "attempts": job.get("attempts"),
                    "max_attempts": job.get("max_attempts"),
                    "error": job.get("last_error"),
                    "dead_lettered_at": job.get("dead_lettered_at"),
                }
            )
        return {
            "failed_deliveries": deliveries,
            "dead_letter_jobs": dead_jobs,
            "count": len(deliveries) + len(dead_jobs),
        }

    # -- firing -----------------------------------------------------------

    def handle_fire(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """排程器调用：核对状态/版本 → 通知 → 记账 → 排下一次。"""
        reminder_id = str(payload.get("reminder_id") or "")
        try:
            version = int(payload.get("version") or 0)
        except (TypeError, ValueError):
            version = 0
        occurrence = str(payload.get("occurrence") or "")
        user_id = str(payload.get("user_id") or "")

        record = self.store.get(reminder_id) if reminder_id else None
        if record is None:
            return {"status": "skipped", "reason": "reminder not found"}
        if record["user_id"] != user_id:
            return {"status": "skipped", "reason": "user mismatch"}
        if record["status"] != "active":
            return {"status": "skipped", "reason": f"reminder is {record['status']}"}
        if int(record["version"]) != version:
            return {"status": "skipped", "reason": "stale version"}
        if self.store.has_delivered(reminder_id, occurrence):
            return {"status": "skipped", "reason": "occurrence already delivered"}

        now_dt = self.scheduler.now()
        now_iso = _iso(now_dt)
        title = "鎏灏提醒"
        body = f"{record['title']}（{self._human_time(record)}）"

        try:
            results: List[NotifyResult] = list(self.notifier.notify(title, body))
        except Exception as exc:  # noqa: BLE001 - 通知器自身异常也算投递失败
            results = [
                NotifyResult("notifier", False, f"{type(exc).__name__}: {exc}")
            ]
        channels = [item.as_dict() for item in results]

        if not any(item.ok for item in results):
            error = "; ".join(
                f"{item.channel}: {item.detail}" for item in results if not item.ok
            ) or "no notification channel"
            self.store.record_delivery(
                reminder_id=reminder_id,
                user_id=user_id,
                occurrence=occurrence,
                status="failed",
                channels=channels,
                error=error,
                now_iso=now_iso,
            )
            self.store.set_error(reminder_id, error, now_iso)
            self._audit(
                "reminder.delivery_failed",
                "failure",
                {"reminder_id": reminder_id, "occurrence": occurrence, "error": error},
            )
            # 重复提醒：这一次失败不能把整条序列断掉，下一次仍然要排上。
            if record["repeat_kind"] != "once":
                self._schedule_next(record, after=now_dt)
            raise ReminderDeliveryError(error)

        recorded = self.store.record_delivery(
            reminder_id=reminder_id,
            user_id=user_id,
            occurrence=occurrence,
            status="delivered",
            channels=channels,
            error=None,
            now_iso=now_iso,
        )
        if not recorded:
            return {"status": "skipped", "reason": "duplicate delivery"}

        self.store.clear_error(reminder_id)
        if record["repeat_kind"] == "once":
            self.store.mark_fired(
                reminder_id, now_iso, next_run_at=None, now_iso=now_iso
            )
        else:
            next_at = self._next_run_at(record, now_dt)
            self.store.mark_fired(
                reminder_id, now_iso, next_run_at=next_at, now_iso=now_iso
            )
            self._schedule_next(record, next_run_at=next_at)
        self._audit(
            "reminder.delivered",
            "success",
            {
                "reminder_id": reminder_id,
                "occurrence": occurrence,
                "channels": [item.channel for item in results if item.ok],
            },
        )
        return {
            "status": "delivered",
            "reminder_id": reminder_id,
            "occurrence": occurrence,
            "channels": channels,
        }

    # -- internals --------------------------------------------------------

    def _enqueue(self, record: Dict[str, Any], occurrence: str) -> int:
        version = int(record["version"])
        return self.scheduler.enqueue(
            REMINDER_FIRE_JOB,
            run_at=occurrence,
            job_key=reminder_job_key(record["id"], version, occurrence),
            payload={
                "reminder_id": record["id"],
                "version": version,
                "occurrence": occurrence,
                "user_id": record["user_id"],
                "title": record["title"],
            },
            max_attempts=self.max_attempts,
            backoff_seconds=self.backoff_seconds,
            backoff_multiplier=self.backoff_multiplier,
            max_backoff_seconds=self.max_backoff_seconds,
        )

    def _schedule_next(
        self,
        record: Dict[str, Any],
        *,
        after: Optional[datetime] = None,
        next_run_at: Optional[str] = None,
    ) -> Optional[str]:
        if record["repeat_kind"] == "once":
            return None
        occurrence = next_run_at or self._next_run_at(record, after or self.scheduler.now())
        updated = dict(record)
        updated["run_at"] = occurrence
        self.store.set_run_at(record["id"], occurrence, _iso(self.scheduler.now()))
        self._enqueue(updated, occurrence)
        return occurrence

    def _next_run_at(self, record: Dict[str, Any], after_utc: datetime) -> Optional[str]:
        kind = record["repeat_kind"]
        if kind not in ("daily", "weekly"):
            return None
        local_after = self._to_local(after_utc)
        hour = int(record["hour"])
        minute = int(record["minute"])
        candidate = local_after.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if kind == "weekly":
            days = (int(record["weekday"]) - local_after.weekday()) % 7
            candidate = candidate + timedelta(days=days)
            if candidate <= local_after:
                candidate = candidate + timedelta(days=7)
        elif candidate <= local_after:
            candidate = candidate + timedelta(days=1)
        if candidate.tzinfo is None:
            candidate = candidate.replace(tzinfo=self.tz)
        return self._to_utc_naive(candidate).isoformat()

    def _to_local(self, utc_naive: datetime) -> datetime:
        return utc_naive.replace(tzinfo=timezone.utc).astimezone(self.tz)

    def _to_utc_naive(self, local_dt: datetime) -> datetime:
        return local_dt.astimezone(timezone.utc).replace(tzinfo=None)

    def _human_time(self, record: Dict[str, Any]) -> str:
        kind = record["repeat_kind"]
        if kind == "daily":
            return f"每天 {int(record['hour']):02d}:{int(record['minute']):02d}"
        if kind == "weekly":
            name = WEEKDAY_NAMES[int(record["weekday"])]
            return f"每周{name} {int(record['hour']):02d}:{int(record['minute']):02d}"
        run_at = datetime.fromisoformat(record["run_at"])
        return self._to_local(run_at).strftime("%Y-%m-%d %H:%M")

    def describe(self, record: Dict[str, Any]) -> Dict[str, Any]:
        run_at = datetime.fromisoformat(record["run_at"])
        return {
            "id": record["id"],
            "title": record["title"],
            "status": record["status"],
            "repeat": record["repeat_kind"],
            "run_at": record["run_at"],
            "run_at_local": self._to_local(run_at).isoformat(),
            "human_time": self._human_time(record),
            "time_text": record.get("time_text") or "",
            "version": int(record["version"]),
            "last_fired_at": record.get("last_fired_at"),
            "last_error": record.get("last_error"),
        }

    def _audit(self, action: str, outcome: str, details: Dict[str, Any]) -> None:
        self.scheduler.audit_event(action, outcome, details)


# -- process-wide service (scheduler handler / AI tool share one instance) ---

_default_service: Optional[ReminderService] = None
_default_service_lock = threading.Lock()


def get_reminder_service(
    *,
    scheduler: Optional[LocalScheduler] = None,
    store: Optional[ReminderStore] = None,
    notifier: Optional[Notifier] = None,
) -> ReminderService:
    """进程内单例。

    * AI 进程调工具时用默认的 producer 排程器（只入队，不 ``run()``）；
    * 排程器进程触发提醒时，把**正在运行的** ``scheduler`` 传进来，后续重排
      直接复用同一个连接。两者读写同一个 SQLite 库，不会丢任务。
    """
    global _default_service
    with _default_service_lock:
        if _default_service is None:
            _default_service = ReminderService(
                scheduler or LocalScheduler(),
                store=store,
                notifier=notifier,
            )
        elif scheduler is not None:
            _default_service.scheduler = scheduler
        return _default_service


__all__ = [
    "REMINDER_FIRE_JOB",
    "ReminderDeliveryError",
    "ReminderService",
    "ReminderStore",
    "default_reminders_db",
    "get_reminder_service",
    "reminder_job_key",
    "reminder_key_prefix",
]
