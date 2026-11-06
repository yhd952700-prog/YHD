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

## 真实使用里的三种兜底

* **错过合并** —— 到点后超过阈值（默认 6 小时）仍未响的，不单独弹；记成
  ``missed`` 并触发一条 ``liuhao.reminder.digest`` 摘要（"你有 N 条错过的提醒
  最近一条：…"），明细在 ``missed()`` 里逐条可查。关机几天后开机不会轰炸。
* **勿扰时段** —— 默认 23:00–07:00（本地时间，可关闭/可改）。静默期内的提醒
  不丢弃，而是延后到静默结束投递；"延后"本身也记账（``status='deferred'``），
  进程中途崩溃重启不会误判成错过。
* **完成 / 稍后** —— 每次投递可被标记 ``done`` 或 ``snoozed``（默认 10 分钟）。
  标记落在投递记录上（``ack_status``），跨重启保留；清单不会只增不减。
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from src.tasks.local_scheduler import LocalScheduler
from src.tasks.reminder_notify import (
    Notifier,
    NotifyResult,
    build_default_notifier,
    maybe_test_crash,
)
# 由本模块转出：tests/tasks/test_reminders.py 从这里导入它，删掉会断测试收集。
from src.tasks.reminder_time import NeedsClarification  # noqa: F401
from src.tasks.reminder_time import (
    WEEKDAY_NAMES,
    ReminderSpec,
    local_timezone,
    parse_reminder,
)

REMINDER_FIRE_JOB = "liuhao.reminder.fire"
# 错过提醒合并成一条摘要（关机几天后开机不轰炸）。
REMINDER_DIGEST_JOB = "liuhao.reminder.digest"

# 提醒的"下一次/这一次"全部用这个前缀做 job_key，取消时按前缀批量取消。
REMINDER_KEY_PREFIX = "reminder"

DEFAULT_FIRE_MAX_ATTEMPTS = 5
DEFAULT_FIRE_BACKOFF_SECONDS = 5.0
DEFAULT_FIRE_BACKOFF_MULTIPLIER = 2.0
DEFAULT_FIRE_MAX_BACKOFF_SECONDS = 60.0

# 错过判定：到点后超过这么久还没响，就不再单独弹，合并成摘要。
DEFAULT_MISSED_THRESHOLD_HOURS = 6.0
# 勿扰时段（本地时间，默认 23:00–07:00）。
DEFAULT_QUIET_START_MINUTES = 23 * 60
DEFAULT_QUIET_END_MINUTES = 7 * 60
# 响过之后默认"稍后再提醒"的间隔。
DEFAULT_SNOOZE_MINUTES = 10
# 合并摘要稍微延迟一下，让同一批错过的提醒全部到齐，只弹一条。
DIGEST_DELAY_SECONDS = 3.0
DIGEST_JOB_KEY = f"{REMINDER_KEY_PREFIX}:digest"
# 摘要不是某一条提醒，"完成/稍后"不能作用在它身上，避免误标后明细还在。
DIGEST_REMINDER_ID = "__digest__"

# 投递前的幂等标记（"最多响一次"的结构性保证）：
#   intent    —— 已领取、正在通知；进程在此时硬死 → 重启后不再重响，记 unknown
#   delivered —— 已确认通知成功并落账
#   failed    —— 所有通道都明确失败（没有响），允许下一次重试
#   unknown   —— 进程死在投递中途，无法确认是否已响；按"宁可少响"封口，且可在
#                `failures` 里查到
DISPATCH_INTENT = "intent"
DISPATCH_DELIVERED = "delivered"
DISPATCH_FAILED = "failed"
DISPATCH_UNKNOWN = "unknown"
DISPATCH_UNKNOWN_REASON = (
    "进程在投递阶段退出，无法确认是否已经响过；按「最多一次」不再重响"
    "（可能已响，也可能漏响一次）"
)

_SETTINGS_DEFAULTS = {
    "quiet_enabled": "1",
    "quiet_start_minutes": str(DEFAULT_QUIET_START_MINUTES),
    "quiet_end_minutes": str(DEFAULT_QUIET_END_MINUTES),
    "missed_threshold_minutes": str(int(DEFAULT_MISSED_THRESHOLD_HOURS * 60)),
    "snooze_minutes": str(DEFAULT_SNOOZE_MINUTES),
}

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
CREATE UNIQUE INDEX IF NOT EXISTS idx_reminder_deliveries_unknown
    ON reminder_deliveries(reminder_id, occurrence) WHERE status = 'unknown';

CREATE TABLE IF NOT EXISTS reminder_dispatch (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    idempotency_key TEXT NOT NULL,
    reminder_id     TEXT NOT NULL,
    user_id         TEXT NOT NULL,
    occurrence      TEXT NOT NULL,
    status          TEXT NOT NULL,
    owner           TEXT,
    attempts        INTEGER NOT NULL DEFAULT 1,
    channels        TEXT,
    error           TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_reminder_dispatch_key
    ON reminder_dispatch(idempotency_key);
CREATE UNIQUE INDEX IF NOT EXISTS idx_reminder_dispatch_occurrence
    ON reminder_dispatch(reminder_id, occurrence);

CREATE TABLE IF NOT EXISTS reminder_settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
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


def reminder_dispatch_key(reminder_id: str, version: int, occurrence: str) -> str:
    """通知幂等键：只由提醒 id / 版本 / 触发时刻决定。

    不含时钟、进程 id、主机名 —— 换进程、改系统时间、重启机器之后都还是同一个
    键，因此可以跨进程复用。
    """

    return f"{reminder_job_key(reminder_id, version, occurrence)}:dispatch"


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _hhmm(minutes: int) -> str:
    return f"{int(minutes) // 60:02d}:{int(minutes) % 60:02d}"


def _setting_minutes(
    raw: Any, key: str, *, minimum: int = 0, maximum: int = 24 * 60 - 1
) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} 必须是整数分钟，收到 {raw!r}") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{key} 必须在 {minimum}–{maximum} 之间，收到 {value}")
    return value


def _parse_hhmm(raw: Any, key: str) -> int:
    text = str(raw).strip()
    parts = text.replace("：", ":").split(":")
    if len(parts) != 2:
        raise ValueError(f"{key} 需要 HH:MM 格式，收到 {raw!r}")
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError as exc:
        raise ValueError(f"{key} 需要 HH:MM 格式，收到 {raw!r}") from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"{key} 不是有效时刻：{raw!r}")
    return hour * 60 + minute


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
        self._lock = threading.Lock()
        self._conn.executescript(_SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        """给老库补列（SQLite 的 CREATE TABLE IF NOT EXISTS 不会加新列）。"""
        with self._lock:
            columns = {
                row[1]
                for row in self._conn.execute(
                    "PRAGMA table_info(reminder_deliveries)"
                ).fetchall()
            }
            additions = (
                ("ack_status", "TEXT"),
                ("acked_at", "TEXT"),
                ("digested_at", "TEXT"),
                ("lateness_seconds", "REAL"),
            )
            for name, kind in additions:
                if name not in columns:
                    self._conn.execute(
                        f"ALTER TABLE reminder_deliveries ADD COLUMN {name} {kind}"
                    )

    # -- settings ---------------------------------------------------------

    def get_settings(self) -> Dict[str, str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT key, value FROM reminder_settings"
            ).fetchall()
        values = dict(_SETTINGS_DEFAULTS)
        values.update({str(key): str(value) for key, value in rows})
        return values

    def set_settings(self, values: Dict[str, str], now_iso: str) -> Dict[str, str]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                for key, value in values.items():
                    self._conn.execute(
                        "INSERT INTO reminder_settings (key, value, updated_at)"
                        " VALUES (?, ?, ?)"
                        " ON CONFLICT(key) DO UPDATE SET value = excluded.value,"
                        " updated_at = excluded.updated_at",
                        (key, str(value), now_iso),
                    )
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
        return self.get_settings()

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
                " WHERE id = ? AND user_id = ? AND status IN ('active', 'completed')",
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

    def has_recorded(self, reminder_id: str, occurrence: str) -> bool:
        """该 occurrence 是否已经记过账（送达/错过/已延后都算，避免重复处理）。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM reminder_deliveries"
                " WHERE reminder_id = ? AND occurrence = ?"
                " AND status IN ('delivered', 'missed', 'deferred') LIMIT 1",
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
        lateness_seconds: Optional[float] = None,
    ) -> bool:
        """写入投递记录；已存在同一 occurrence 的成功记录时返回 False。"""
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO reminder_deliveries"
                    " (reminder_id, user_id, occurrence, status, channels, error,"
                    "  lateness_seconds, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        reminder_id,
                        user_id,
                        occurrence,
                        status,
                        json.dumps(channels, ensure_ascii=False, default=str),
                        error,
                        lateness_seconds,
                        now_iso,
                    ),
                )
            except sqlite3.IntegrityError:
                return False
        return True

    def occurrence_status(self, reminder_id: str, occurrence: str) -> Optional[str]:
        """该 occurrence 最近一次投递记录的状态（没有记录时返回 None）。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT status FROM reminder_deliveries"
                " WHERE reminder_id = ? AND occurrence = ?"
                " ORDER BY id DESC LIMIT 1",
                (reminder_id, occurrence),
            ).fetchone()
        return str(row[0]) if row else None

    # -- dispatch markers (at-most-once) ---------------------------------

    def claim_dispatch(
        self,
        *,
        reminder_id: str,
        user_id: str,
        occurrence: str,
        idempotency_key: str,
        owner: str,
        now_iso: str,
    ) -> Dict[str, Any]:
        """通知前原子地领取该 occurrence 的投递权。

        返回值：
        * ``claimed``    —— 首次领取（或上一轮"所有通道明确失败"后的重试）；
        * ``interrupted``—— 上一个进程死在投递中途，调用方必须按 unknown 封口；
        * ``duplicate``  —— 已经投递/已判不明，不能再响。
        """
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    "SELECT status, attempts FROM reminder_dispatch"
                    " WHERE reminder_id = ? AND occurrence = ?",
                    (reminder_id, occurrence),
                ).fetchone()
                if row is None:
                    self._conn.execute(
                        "INSERT INTO reminder_dispatch"
                        " (idempotency_key, reminder_id, user_id, occurrence, status,"
                        "  owner, attempts, created_at, updated_at)"
                        " VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)",
                        (
                            idempotency_key,
                            reminder_id,
                            user_id,
                            occurrence,
                            DISPATCH_INTENT,
                            owner,
                            now_iso,
                            now_iso,
                        ),
                    )
                    self._conn.execute("COMMIT")
                    return {"status": "claimed", "attempts": 1, "retry": False}
                status, attempts = str(row[0]), int(row[1])
                if status == DISPATCH_FAILED:
                    # 上一轮所有通道都明确失败（没有响），允许再试一次。
                    self._conn.execute(
                        "UPDATE reminder_dispatch SET status = ?, owner = ?,"
                        " attempts = attempts + 1, channels = NULL, error = NULL,"
                        " updated_at = ?"
                        " WHERE reminder_id = ? AND occurrence = ?",
                        (
                            DISPATCH_INTENT,
                            owner,
                            now_iso,
                            reminder_id,
                            occurrence,
                        ),
                    )
                    self._conn.execute("COMMIT")
                    return {
                        "status": "claimed",
                        "attempts": attempts + 1,
                        "retry": True,
                    }
                self._conn.execute("COMMIT")
                if status == DISPATCH_INTENT:
                    return {"status": "interrupted", "attempts": attempts}
                return {"status": "duplicate", "existing": status}
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise

    def resolve_interrupted_dispatch(
        self,
        *,
        reminder_id: str,
        user_id: str,
        occurrence: str,
        idempotency_key: str,
        now_iso: str,
        lateness_seconds: Optional[float],
        next_run_at: Optional[str],
        complete: bool,
    ) -> Dict[str, Any]:
        """把上一个进程留下的 intent 标记一次性封口为 unknown。

        投递记录、提醒状态（完成/排下一次）与 dispatch 状态在**同一个事务**里
        写完，避免"已经封口但没记账"再被下一次重启漏掉。
        """
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    "SELECT status FROM reminder_dispatch"
                    " WHERE idempotency_key = ?",
                    (idempotency_key,),
                ).fetchone()
                if row is None:
                    self._conn.execute("COMMIT")
                    return {"status": "missing"}
                if str(row[0]) != DISPATCH_INTENT:
                    self._conn.execute("COMMIT")
                    return {"status": "already_handled", "existing": str(row[0])}
                self._conn.execute(
                    "UPDATE reminder_dispatch SET status = ?, channels = '[]',"
                    " error = ?, updated_at = ? WHERE idempotency_key = ?",
                    (DISPATCH_UNKNOWN, DISPATCH_UNKNOWN_REASON, now_iso,
                     idempotency_key),
                )
                try:
                    self._conn.execute(
                        "INSERT INTO reminder_deliveries"
                        " (reminder_id, user_id, occurrence, status, channels, error,"
                        "  lateness_seconds, created_at)"
                        " VALUES (?, ?, ?, ?, '[]', ?, ?, ?)",
                        (
                            reminder_id,
                            user_id,
                            occurrence,
                            DISPATCH_UNKNOWN,
                            DISPATCH_UNKNOWN_REASON,
                            lateness_seconds,
                            now_iso,
                        ),
                    )
                except sqlite3.IntegrityError:
                    # 同一 occurrence 已经有一条 unknown 记录（极端重复重启），
                    # 不挡状态推进。
                    pass
                self._advance_reminder_locked(
                    reminder_id=reminder_id,
                    fired_at=now_iso,
                    next_run_at=next_run_at,
                    complete=complete,
                    now_iso=now_iso,
                )
                self._conn.execute("COMMIT")
                return {"status": "unknown"}
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise

    def close_dispatch(
        self,
        idempotency_key: str,
        *,
        status: str,
        channels: List[Dict[str, Any]],
        error: Optional[str],
        now_iso: str,
    ) -> bool:
        """把 intent 标记推进到终态（delivered / failed）。只能改 intent 行。"""
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE reminder_dispatch SET status = ?, channels = ?, error = ?,"
                " updated_at = ? WHERE idempotency_key = ? AND status = ?",
                (
                    status,
                    json.dumps(channels, ensure_ascii=False, default=str),
                    error,
                    now_iso,
                    idempotency_key,
                    DISPATCH_INTENT,
                ),
            )
            return int(cursor.rowcount) > 0

    def record_outcome_and_advance(
        self,
        *,
        reminder_id: str,
        user_id: str,
        occurrence: str,
        status: str,
        channels: List[Dict[str, Any]],
        error: Optional[str],
        now_iso: str,
        lateness_seconds: Optional[float],
        next_run_at: Optional[str] = None,
        complete: bool = False,
    ) -> bool:
        """投递记录与提醒状态在同一个事务里落盘。

        进程死在两者之间会变成"已响但没排下一次"，这种静默流失比重复响更难查，
        所以这里不拆成两次写。
        """
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                try:
                    self._conn.execute(
                        "INSERT INTO reminder_deliveries"
                        " (reminder_id, user_id, occurrence, status, channels, error,"
                        "  lateness_seconds, created_at)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            reminder_id,
                            user_id,
                            occurrence,
                            status,
                            json.dumps(channels, ensure_ascii=False, default=str),
                            error,
                            lateness_seconds,
                            now_iso,
                        ),
                    )
                except sqlite3.IntegrityError:
                    self._conn.execute("ROLLBACK")
                    return False
                self._advance_reminder_locked(
                    reminder_id=reminder_id,
                    fired_at=now_iso,
                    next_run_at=next_run_at,
                    complete=complete,
                    now_iso=now_iso,
                )
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
        return True

    def _advance_reminder_locked(
        self,
        *,
        reminder_id: str,
        fired_at: str,
        next_run_at: Optional[str],
        complete: bool,
        now_iso: str,
    ) -> None:
        """推进提醒状态（调用方必须已持有写事务）。"""
        if complete:
            self._conn.execute(
                "UPDATE reminders SET status = 'completed', last_fired_at = ?,"
                " last_error = NULL, updated_at = ? WHERE id = ?",
                (fired_at, now_iso, reminder_id),
            )
        elif next_run_at is not None:
            self._conn.execute(
                "UPDATE reminders SET run_at = ?, last_fired_at = ?,"
                " last_error = NULL, updated_at = ? WHERE id = ?",
                (next_run_at, fired_at, now_iso, reminder_id),
            )
        else:
            self._conn.execute(
                "UPDATE reminders SET last_fired_at = ?, last_error = NULL,"
                " updated_at = ? WHERE id = ?",
                (fired_at, now_iso, reminder_id),
            )

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
        items = self._query_deliveries(query, params)
        return items

    def get_delivery(self, delivery_id: int, user_id: str) -> Optional[Dict[str, Any]]:
        items = self._query_deliveries(
            "SELECT * FROM reminder_deliveries WHERE id = ? AND user_id = ?",
            [int(delivery_id), user_id],
        )
        return items[0] if items else None

    def latest_pending_ack(
        self, user_id: str, *, reminder_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        query = (
            "SELECT * FROM reminder_deliveries WHERE user_id = ?"
            " AND ack_status IS NULL AND status IN ('delivered', 'missed')"
            " AND reminder_id != ?"
        )
        params: List[Any] = [user_id, DIGEST_REMINDER_ID]
        if reminder_id:
            query += " AND reminder_id = ?"
            params.append(reminder_id)
        query += " ORDER BY id DESC LIMIT 1"
        items = self._query_deliveries(query, params)
        return items[0] if items else None

    def ack_delivery(
        self, delivery_id: int, user_id: str, ack_status: str, now_iso: str
    ) -> bool:
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE reminder_deliveries SET ack_status = ?, acked_at = ?"
                " WHERE id = ? AND user_id = ? AND ack_status IS NULL",
                (ack_status, now_iso, int(delivery_id), user_id),
            )
            return int(cursor.rowcount) > 0

    def list_undigested_missed(self, user_id: str) -> List[Dict[str, Any]]:
        return self._query_deliveries(
            "SELECT * FROM reminder_deliveries WHERE user_id = ?"
            " AND status = 'missed' AND digested_at IS NULL"
            " ORDER BY occurrence DESC, id DESC",
            [user_id],
        )

    def list_missed(self, user_id: str, *, limit: int = 100) -> List[Dict[str, Any]]:
        return self._query_deliveries(
            "SELECT * FROM reminder_deliveries WHERE user_id = ? AND status = 'missed'"
            " ORDER BY id DESC LIMIT ?",
            [user_id, int(limit)],
        )

    def list_history(self, user_id: str, *, limit: int = 100) -> List[Dict[str, Any]]:
        """最近真正触发过的提醒（送达/错过），不含摘要、延后、失败。"""
        return self._query_deliveries(
            "SELECT * FROM reminder_deliveries WHERE user_id = ?"
            " AND status IN ('delivered', 'missed') AND reminder_id != ?"
            " ORDER BY id DESC LIMIT ?",
            [user_id, DIGEST_REMINDER_ID, int(limit)],
        )

    def distinct_users(self) -> List[str]:
        """所有出现过提醒/投递记录的用户（命令行默认看全部）。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT user_id FROM reminders"
                " UNION SELECT user_id FROM reminder_deliveries"
                " ORDER BY user_id"
            ).fetchall()
        return [str(row[0]) for row in rows if row[0]]

    def delivery_status_counts(
        self, user_id: Optional[str] = None
    ) -> Dict[str, Dict[str, int]]:
        """按提醒聚合投递状态（只读；命令行判断「已错过/已完成」用）。"""
        query = (
            "SELECT reminder_id, status, COUNT(*) FROM reminder_deliveries"
            " WHERE 1 = 1"
        )
        params: List[Any] = []
        if user_id:
            query += " AND user_id = ?"
            params.append(user_id)
        query += " GROUP BY reminder_id, status"
        with self._lock:
            rows = self._conn.execute(query, tuple(params)).fetchall()
        summary: Dict[str, Dict[str, int]] = {}
        for reminder_id, status, count in rows:
            summary.setdefault(str(reminder_id), {})[str(status)] = int(count)
        return summary

    def mark_digested(self, delivery_ids: List[int], now_iso: str) -> None:
        if not delivery_ids:
            return
        placeholders = ",".join("?" for _ in delivery_ids)
        with self._lock:
            self._conn.execute(
                f"UPDATE reminder_deliveries SET digested_at = ?"
                f" WHERE id IN ({placeholders}) AND digested_at IS NULL",
                (now_iso, *[int(item) for item in delivery_ids]),
            )

    def _query_deliveries(
        self, query: str, params: List[Any]
    ) -> List[Dict[str, Any]]:
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
        # 一次性提醒响过（completed）仍可能有"稍后提醒"在排队，取消要能把它撤掉。
        if record["status"] not in ("active", "completed"):
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

    # -- settings ---------------------------------------------------------

    def settings(self) -> Dict[str, Any]:
        """当前配置（全部落 SQLite，重启后保持）。"""
        raw = self.store.get_settings()
        quiet_enabled = _truthy(raw.get("quiet_enabled"))
        start = _setting_minutes(
            raw.get("quiet_start_minutes"), "quiet_start_minutes"
        )
        end = _setting_minutes(raw.get("quiet_end_minutes"), "quiet_end_minutes")
        threshold = _setting_minutes(
            raw.get("missed_threshold_minutes"),
            "missed_threshold_minutes",
            minimum=1,
            maximum=7 * 24 * 60,
        )
        snooze = _setting_minutes(
            raw.get("snooze_minutes"), "snooze_minutes", minimum=1, maximum=24 * 60
        )
        return {
            "quiet_enabled": quiet_enabled,
            "quiet_start": _hhmm(start),
            "quiet_end": _hhmm(end),
            "quiet_start_minutes": start,
            "quiet_end_minutes": end,
            "missed_threshold_minutes": threshold,
            "missed_threshold_hours": round(threshold / 60, 2),
            "snooze_minutes": snooze,
            "quiet_text": (
                f"勿扰 {_hhmm(start)}–{_hhmm(end)}" if quiet_enabled else "勿扰已关闭"
            ),
        }

    def update_settings(self, changes: Dict[str, Any]) -> Dict[str, Any]:
        """更新配置；非法值直接报错，不静默改成"差不多"的值。"""
        updates: Dict[str, str] = {}
        allowed = {
            "quiet_enabled",
            "quiet_start",
            "quiet_end",
            "quiet_start_minutes",
            "quiet_end_minutes",
            "missed_threshold_minutes",
            "missed_threshold_hours",
            "snooze_minutes",
        }
        unknown = [key for key in changes if key not in allowed]
        if unknown:
            return {
                "status": "error",
                "error": f"不支持的设置项：{', '.join(unknown)}；可用：{', '.join(sorted(allowed))}。",
            }

        if changes.get("quiet_enabled") is not None:
            value = changes["quiet_enabled"]
            if isinstance(value, bool):
                updates["quiet_enabled"] = "1" if value else "0"
            elif str(value).strip().lower() in {"0", "false", "no", "off", "disabled"}:
                updates["quiet_enabled"] = "0"
            elif _truthy(value):
                updates["quiet_enabled"] = "1"
            else:
                return {
                    "status": "error",
                    "error": f"quiet_enabled 应是开启/关闭，收到 {value!r}。",
                }

        for key in ("quiet_start", "quiet_end"):
            if changes.get(key) is not None:
                updates[f"{key}_minutes"] = str(_parse_hhmm(changes[key], key))
        for source in ("quiet_start_minutes", "quiet_end_minutes"):
            if changes.get(source) is not None:
                updates[source] = str(
                    _setting_minutes(changes[source], source)
                )

        if changes.get("missed_threshold_minutes") is not None:
            updates["missed_threshold_minutes"] = str(
                _setting_minutes(
                    changes["missed_threshold_minutes"],
                    "missed_threshold_minutes",
                    minimum=1,
                    maximum=7 * 24 * 60,
                )
            )
        if changes.get("missed_threshold_hours") is not None:
            try:
                hours = float(changes["missed_threshold_hours"])
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    "missed_threshold_hours 必须是数字，"
                    f"收到 {changes['missed_threshold_hours']!r}"
                ) from exc
            if not 0 < hours <= 7 * 24:
                raise ValueError("missed_threshold_hours 必须在 0–168 之间（不含 0）")
            updates["missed_threshold_minutes"] = str(max(1, int(round(hours * 60))))

        if changes.get("snooze_minutes") is not None:
            updates["snooze_minutes"] = str(
                _setting_minutes(
                    changes["snooze_minutes"],
                    "snooze_minutes",
                    minimum=1,
                    maximum=24 * 60,
                )
            )

        if not updates:
            return {"status": "noop", "settings": self.settings()}

        self.store.set_settings(updates, _iso(self.scheduler.now()))
        self._audit("reminder.settings_updated", "success", updates)
        return {"status": "updated", "settings": self.settings()}

    # -- acknowledge ------------------------------------------------------

    def done(
        self,
        user_id: str,
        *,
        reminder_id: Optional[str] = None,
        delivery_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """标记"已完成"（响过的那一次），清单不会无限堆积。"""
        now_iso = _iso(self.scheduler.now())
        target, error = self._ack_target(user_id, reminder_id, delivery_id)
        if error is not None:
            return error
        assert target is not None
        if not self.store.ack_delivery(target["id"], user_id, "done", now_iso):
            return {"status": "noop", "message": "这条提醒已经处理过了。"}
        self._audit(
            "reminder.done",
            "success",
            {"reminder_id": target["reminder_id"], "delivery_id": target["id"]},
        )
        return {
            "status": "done",
            "message": "已标记完成。",
            "delivery": self.store.get_delivery(target["id"], user_id),
        }

    def snooze(
        self,
        user_id: str,
        *,
        reminder_id: Optional[str] = None,
        delivery_id: Optional[int] = None,
        minutes: Optional[int] = None,
    ) -> Dict[str, Any]:
        """稍后再提醒（默认 10 分钟）；只对"真的响过"的那一次生效。"""
        target, error = self._ack_target(user_id, reminder_id, delivery_id)
        if error is not None:
            return error
        assert target is not None
        if target["status"] != "delivered":
            return {
                "status": "noop",
                "message": "错过的提醒不能稍后提醒；请标记完成，或重新创建一条。",
                "delivery": target,
            }
        record = self.store.get(target["reminder_id"], user_id=user_id)
        if record is None:
            return {"status": "not_found", "message": "没有这个提醒。"}
        if record["status"] == "cancelled":
            return {"status": "noop", "message": "提醒已取消，不会再响。"}
        if record["status"] not in ("active", "completed"):
            return {
                "status": "noop",
                "message": f"提醒已经是「{record['status']}」，不能稍后提醒。",
            }
        default_minutes = self.settings()["snooze_minutes"]
        delay = default_minutes if minutes is None else int(minutes)
        if delay < 1:
            return {"status": "error", "error": "稍后提醒的分钟数必须 >= 1。"}

        now_dt = self.scheduler.now()
        if not self.store.ack_delivery(target["id"], user_id, "snoozed", _iso(now_dt)):
            return {"status": "noop", "message": "这条提醒已经处理过了。"}

        run_at = now_dt + timedelta(minutes=delay)
        token = uuid.uuid4().hex[:10]
        occurrence = f"snooze:{token}"
        version = int(record["version"])
        self.scheduler.enqueue(
            REMINDER_FIRE_JOB,
            run_at=run_at,
            job_key=f"{reminder_key_prefix(record['id'])}v{version}:{occurrence}",
            payload={
                "reminder_id": record["id"],
                "version": version,
                "occurrence": occurrence,
                "scheduled_at": run_at.isoformat(),
                "snooze": True,
                "user_id": record["user_id"],
                "title": record["title"],
            },
            max_attempts=self.max_attempts,
            backoff_seconds=self.backoff_seconds,
            backoff_multiplier=self.backoff_multiplier,
            max_backoff_seconds=self.max_backoff_seconds,
        )
        self._audit(
            "reminder.snoozed",
            "success",
            {
                "reminder_id": record["id"],
                "delivery_id": target["id"],
                "run_at": run_at.isoformat(),
                "minutes": delay,
            },
        )
        return {
            "status": "snoozed",
            "message": f"{delay} 分钟后再次提醒：{record['title']}",
            "run_at": run_at.isoformat(),
            "minutes": delay,
            "reminder": self.describe(record),
        }

    def missed(self, user_id: str, *, limit: int = 100) -> Dict[str, Any]:
        """错过的提醒明细（摘要里提示的那批，逐条可查）。"""
        rows = self.store.list_missed(user_id, limit=limit)
        pending = [item for item in rows if not item.get("digested_at")]
        return {
            "status": "ok",
            "count": len(rows),
            "pending_digest": len(pending),
            "missed": rows,
        }

    def _ack_target(
        self,
        user_id: str,
        reminder_id: Optional[str],
        delivery_id: Optional[int],
    ) -> tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        if delivery_id:
            try:
                delivery_id = int(delivery_id)
            except (TypeError, ValueError):
                return None, {"status": "error", "error": "delivery_id 必须是整数。"}
            row = self.store.get_delivery(delivery_id, user_id)
            if row is None:
                return None, {"status": "not_found", "message": "没有这条提醒记录。"}
            if row.get("reminder_id") == DIGEST_REMINDER_ID:
                return None, {
                    "status": "noop",
                    "message": "这条是错过提醒的汇总，不能单独标记；请用 missed 查看明细后逐条处理。",
                    "delivery": row,
                }
            if row.get("ack_status"):
                return None, {
                    "status": "noop",
                    "message": f"这条提醒已经标记为「{row['ack_status']}」。",
                    "delivery": row,
                }
            if row.get("status") not in ("delivered", "missed"):
                return None, {
                    "status": "noop",
                    "message": "这条记录不是响过的提醒，不能标记。",
                    "delivery": row,
                }
            return row, None
        if reminder_id:
            record = self.store.get(reminder_id, user_id=user_id)
            if record is None:
                return None, {"status": "not_found", "message": "没有这个提醒。"}
        row = self.store.latest_pending_ack(user_id, reminder_id=reminder_id or None)
        if row is None:
            return None, {
                "status": "not_found",
                "message": "没有待处理的提醒；可以用 action=list 重新创建。",
            }
        return row, None

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
        # unknown = 进程死在投递中途、无法确认是否响过；同样属于"可能没响"，
        # 必须让用户在 failures 里看得到。
        deliveries = self.store.list_deliveries(
            user_id, status="failed", limit=limit
        ) + self.store.list_deliveries(user_id, status="unknown", limit=limit)
        deliveries.sort(key=lambda item: int(item.get("id") or 0), reverse=True)
        deliveries = deliveries[:limit]
        dead_jobs = []
        for job in self.scheduler.list_dead_letters(limit=max(limit, 100)):
            if job.get("job_name") not in (REMINDER_FIRE_JOB, REMINDER_DIGEST_JOB):
                continue
            payload = job.get("payload") or {}
            if payload.get("user_id") != user_id:
                continue
            dead_jobs.append(
                {
                    "job_id": job.get("id"),
                    "job_name": job.get("job_name"),
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
        """排程器调用：核对状态/版本 → 错过/勿扰判定 → 通知 → 记账 → 排下一次。"""
        reminder_id = str(payload.get("reminder_id") or "")
        try:
            version = int(payload.get("version") or 0)
        except (TypeError, ValueError):
            version = 0
        occurrence = str(payload.get("occurrence") or "")
        user_id = str(payload.get("user_id") or "")
        is_snooze = bool(payload.get("snooze"))
        quiet_deferred = bool(payload.get("quiet_deferred"))

        record = self.store.get(reminder_id) if reminder_id else None
        if record is None:
            return {"status": "skipped", "reason": "reminder not found"}
        if record["user_id"] != user_id:
            return {"status": "skipped", "reason": "user mismatch"}
        # 一次性提醒响过之后是 completed；此时只允许"稍后提醒"这一次补响。
        if record["status"] != "active" and not (
            is_snooze
            and record["status"] == "completed"
            and record["repeat_kind"] == "once"
        ):
            return {"status": "skipped", "reason": f"reminder is {record['status']}"}
        if int(record["version"]) != version:
            return {"status": "skipped", "reason": "stale version"}
        # 静默延后本身也要记账：万一进程在延后中途崩溃，重启后原 job 被回收重跑，
        # 不能因为"又晚了 8 小时"就误判成错过（延后是用户可预期的行为）。
        already_recorded = (
            self.store.has_delivered(reminder_id, occurrence)
            if quiet_deferred
            else self.store.has_recorded(reminder_id, occurrence)
        )
        if already_recorded:
            outcome = self.store.occurrence_status(reminder_id, occurrence)
            if outcome in ("delivered", "missed"):
                # 上一次可能死在"记账成功 → 排下一次/标记完成"之间：这里幂等补齐，
                # 避免重复提醒序列因为一次崩溃静默断掉。
                self._reconcile_after_recorded(record, occurrence)
                if outcome == "delivered":
                    self.store.close_dispatch(
                        reminder_dispatch_key(reminder_id, version, occurrence),
                        status=DISPATCH_DELIVERED,
                        channels=[],
                        error=None,
                        now_iso=_iso(self.scheduler.now()),
                    )
            return {
                "status": "skipped",
                "reason": "occurrence already handled",
                "outcome": outcome,
            }

        now_dt = self.scheduler.now()
        now_iso = _iso(now_dt)
        scheduled_at = self._scheduled_at(payload, occurrence)
        lateness = max(0.0, (now_dt - scheduled_at).total_seconds())

        # 1) 错过：到点太久没响，不单独弹，合并成一条摘要（关机几天也不会轰炸）。
        threshold_seconds = self.settings()["missed_threshold_minutes"] * 60
        if not quiet_deferred and lateness > threshold_seconds:
            return self._handle_missed(
                record, occurrence, lateness, now_dt, now_iso, is_snooze
            )

        # 2) 勿扰：延后到静默结束投递，不丢弃、不变成"从来没响过"。
        # 用户主动要的"稍后再提醒"不因勿扰再往后拖（他此刻明确醒着）。
        quiet_until = self._quiet_defer_until(now_dt) if not is_snooze else None
        if quiet_until is not None:
            return self._defer_for_quiet(
                record, payload, occurrence, quiet_until, now_iso
            )

        title = "鎏灏提醒"
        body = f"{record['title']}（{self._human_time(record)}）"

        # 先落 intent 标记再通知：进程死在通知的任何阶段，重启后看到 intent
        # 就按"最多响一次"封口 —— 宁可少响一次，也不在重启后补响第二次。
        idempotency_key = reminder_dispatch_key(reminder_id, version, occurrence)
        claim = self.store.claim_dispatch(
            reminder_id=reminder_id,
            user_id=user_id,
            occurrence=occurrence,
            idempotency_key=idempotency_key,
            owner=self.scheduler.owner,
            now_iso=now_iso,
        )
        if claim["status"] == "duplicate":
            self._reconcile_after_recorded(record, occurrence)
            return {
                "status": "skipped",
                "reason": f"dispatch already {claim.get('existing', 'handled')}",
                "outcome": claim.get("existing"),
            }
        if claim["status"] == "interrupted":
            # 上一个进程死在投递中途，无法确认是否已经响过。不再重响，记 unknown
            # 并推进提醒状态；unknown 会出现在 `failures` 里，漏响可查。
            next_at = (
                None
                if (is_snooze or record["repeat_kind"] == "once")
                else self._next_run_at(record, now_dt)
            )
            self.store.resolve_interrupted_dispatch(
                reminder_id=reminder_id,
                user_id=user_id,
                occurrence=occurrence,
                idempotency_key=idempotency_key,
                now_iso=now_iso,
                lateness_seconds=lateness,
                next_run_at=next_at,
                complete=(
                    record["status"] == "active"
                    and record["repeat_kind"] == "once"
                    and not is_snooze
                ),
            )
            if next_at:
                self._enqueue({**record, "run_at": next_at}, next_at)
            self._audit(
                "reminder.dispatch_unknown",
                "attention",
                {
                    "reminder_id": reminder_id,
                    "occurrence": occurrence,
                    "attempts": claim.get("attempts"),
                },
            )
            return {
                "status": "unknown",
                "reminder_id": reminder_id,
                "occurrence": occurrence,
                "reason": DISPATCH_UNKNOWN_REASON,
            }

        maybe_test_crash("before_notify")
        try:
            results: List[NotifyResult] = list(
                self.notifier.notify(
                    title, body, idempotency_key=idempotency_key
                )
            )
        except Exception as exc:  # noqa: BLE001 - 通知器自身异常也算投递失败
            results = [
                NotifyResult("notifier", False, f"{type(exc).__name__}: {exc}")
            ]
        maybe_test_crash("after_notify")
        channels = [item.as_dict() for item in results]

        if not any(item.ok for item in results):
            error = "; ".join(
                f"{item.channel}: {item.detail}" for item in results if not item.ok
            ) or "no notification channel"
            # 所有通道都明确失败 = 这一轮没有响；先封口 failed（允许同一 job 重试），
            # 再写失败明细。死在两者之间最多少一条明细，不会重响。
            self.store.close_dispatch(
                idempotency_key,
                status=DISPATCH_FAILED,
                channels=channels,
                error=error,
                now_iso=now_iso,
            )
            self.store.record_delivery(
                reminder_id=reminder_id,
                user_id=user_id,
                occurrence=occurrence,
                status="failed",
                channels=channels,
                error=error,
                now_iso=now_iso,
                lateness_seconds=lateness,
            )
            self.store.set_error(reminder_id, error, now_iso)
            self._audit(
                "reminder.delivery_failed",
                "failure",
                {"reminder_id": reminder_id, "occurrence": occurrence, "error": error},
            )
            # 重复提醒：这一次失败不能把整条序列断掉，下一次仍然要排上。
            # 稍后提醒（snooze）不改变原序列，失败只让这一次重试/进死信。
            if record["repeat_kind"] != "once" and not is_snooze:
                self._schedule_next(record, after=now_dt)
            raise ReminderDeliveryError(error)

        if is_snooze:
            recorded = self.store.record_delivery(
                reminder_id=reminder_id,
                user_id=user_id,
                occurrence=occurrence,
                status="delivered",
                channels=channels,
                error=None,
                now_iso=now_iso,
                lateness_seconds=lateness,
            )
            if not recorded:
                # 已有一条 delivered（极端竞态）：标记封口，不再响。
                self.store.close_dispatch(
                    idempotency_key,
                    status=DISPATCH_DELIVERED,
                    channels=channels,
                    error=None,
                    now_iso=now_iso,
                )
                return {"status": "skipped", "reason": "duplicate delivery"}
            self.store.clear_error(reminder_id)
            self.store.close_dispatch(
                idempotency_key,
                status=DISPATCH_DELIVERED,
                channels=channels,
                error=None,
                now_iso=now_iso,
            )
            # 用户主动要的那一次已响完，原提醒的序列/状态都不动。
            self._audit(
                "reminder.snooze_delivered",
                "success",
                {"reminder_id": reminder_id, "occurrence": occurrence},
            )
            return {
                "status": "delivered",
                "reminder_id": reminder_id,
                "occurrence": occurrence,
                "channels": channels,
            }

        next_at = (
            None
            if record["repeat_kind"] == "once"
            else self._next_run_at(record, now_dt)
        )
        # 投递记录 + 提醒状态（完成/排下一次）在同一个事务里写：不会出现
        # "已经响过，但下一次没排上"的静默断档。
        recorded = self.store.record_outcome_and_advance(
            reminder_id=reminder_id,
            user_id=user_id,
            occurrence=occurrence,
            status="delivered",
            channels=channels,
            error=None,
            now_iso=now_iso,
            lateness_seconds=lateness,
            next_run_at=next_at,
            complete=record["repeat_kind"] == "once",
        )
        if not recorded:
            self.store.close_dispatch(
                idempotency_key,
                status=DISPATCH_DELIVERED,
                channels=channels,
                error=None,
                now_iso=now_iso,
            )
            return {"status": "skipped", "reason": "duplicate delivery"}
        self.store.close_dispatch(
            idempotency_key,
            status=DISPATCH_DELIVERED,
            channels=channels,
            error=None,
            now_iso=now_iso,
        )
        if next_at:
            # enqueue 按 job_key 幂等；即使上一次死在这里，重跑时也会补齐。
            self._enqueue({**record, "run_at": next_at}, next_at)
        self._audit(
            "reminder.delivered",
            "success",
            {
                "reminder_id": reminder_id,
                "occurrence": occurrence,
                "channels": [item.channel for item in results if item.ok],
                "lateness_seconds": round(lateness, 1),
            },
        )
        return {
            "status": "delivered",
            "reminder_id": reminder_id,
            "occurrence": occurrence,
            "channels": channels,
        }

    def handle_digest(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """把一段时间内错过的提醒合并成一条摘要（只弹一次，明细仍可查）。"""
        user_id = str(payload.get("user_id") or "")
        if not user_id:
            return {"status": "skipped", "reason": "missing user_id"}
        now_dt = self.scheduler.now()
        now_iso = _iso(now_dt)

        quiet_until = self._quiet_defer_until(now_dt)
        if quiet_until is not None:
            self._enqueue_digest(user_id, run_at=quiet_until)
            self._audit(
                "reminder.digest_deferred",
                "success",
                {"user_id": user_id, "until": quiet_until.isoformat()},
            )
            return {"status": "deferred", "until": quiet_until.isoformat()}

        rows = self.store.list_undigested_missed(user_id)
        if not rows:
            return {"status": "skipped", "reason": "nothing to digest"}

        count = len(rows)
        latest = rows[0]
        latest_title = self._delivery_title(latest)
        latest_time = self._delivery_time_text(latest)
        title = "鎏灏提醒：错过的提醒"
        body = (
            f"你有 {count} 条错过的提醒。最近一条：{latest_title}"
            f"（{latest_time}）。明细可在「错过的提醒」里查看。"
        )

        try:
            results: List[NotifyResult] = list(self.notifier.notify(title, body))
        except Exception as exc:  # noqa: BLE001
            results = [NotifyResult("notifier", False, f"{type(exc).__name__}: {exc}")]
        channels = [item.as_dict() for item in results]

        occurrence = f"digest:{min(item['id'] for item in rows)}-{max(item['id'] for item in rows)}"
        if not any(item.ok for item in results):
            error = "; ".join(
                f"{item.channel}: {item.detail}" for item in results if not item.ok
            ) or "no notification channel"
            self.store.record_delivery(
                reminder_id=DIGEST_REMINDER_ID,
                user_id=user_id,
                occurrence=occurrence,
                status="failed",
                channels=channels,
                error=error,
                now_iso=now_iso,
            )
            self._audit(
                "reminder.digest_failed",
                "failure",
                {"user_id": user_id, "count": count, "error": error},
            )
            # 抛给排程器：重试成功后这批错过的提醒才会被标记已合并。
            raise ReminderDeliveryError(error)

        recorded = self.store.record_delivery(
            reminder_id=DIGEST_REMINDER_ID,
            user_id=user_id,
            occurrence=occurrence,
            status="delivered",
            channels=channels,
            error=None,
            now_iso=now_iso,
        )
        if not recorded:
            return {"status": "skipped", "reason": "duplicate digest"}
        self.store.mark_digested([item["id"] for item in rows], now_iso)
        # 并发下可能有新的 missed 在"取数→标记"之间落库；补一条后续摘要，
        # 否则它会永远停在未合并状态（用户看不到、也没人再触发摘要）。
        remaining = self.store.list_undigested_missed(user_id)
        if remaining:
            self._enqueue_digest(
                user_id,
                key_suffix=f"followup:{max(item['id'] for item in remaining)}",
            )
        self._audit(
            "reminder.digest_delivered",
            "success",
            {
                "user_id": user_id,
                "count": count,
                "channels": [item.channel for item in results if item.ok],
            },
        )
        return {
            "status": "digested",
            "count": count,
            "occurrence": occurrence,
            "channels": channels,
        }

    # -- internals --------------------------------------------------------

    def _handle_missed(
        self,
        record: Dict[str, Any],
        occurrence: str,
        lateness: float,
        now_dt: datetime,
        now_iso: str,
        is_snooze: bool,
    ) -> Dict[str, Any]:
        """补记一次"错过"：不单独弹，交给摘要；重复提醒继续排下一次。"""
        next_at = (
            None
            if (is_snooze or record["repeat_kind"] == "once")
            else self._next_run_at(record, now_dt)
        )
        recorded = self.store.record_outcome_and_advance(
            reminder_id=record["id"],
            user_id=record["user_id"],
            occurrence=occurrence,
            status="missed",
            channels=[],
            error=None,
            now_iso=now_iso,
            lateness_seconds=lateness,
            next_run_at=next_at,
            complete=(
                record["status"] == "active"
                and record["repeat_kind"] == "once"
                and not is_snooze
            ),
        )
        if not recorded:
            return {"status": "skipped", "reason": "duplicate missed"}
        if next_at:
            self._enqueue({**record, "run_at": next_at}, next_at)
        self._enqueue_digest(record["user_id"])
        self._audit(
            "reminder.missed",
            "attention",
            {
                "reminder_id": record["id"],
                "occurrence": occurrence,
                "lateness_seconds": round(lateness, 1),
            },
        )
        return {
            "status": "missed",
            "reminder_id": record["id"],
            "occurrence": occurrence,
            "lateness_seconds": round(lateness, 1),
        }

    def _defer_for_quiet(
        self,
        record: Dict[str, Any],
        payload: Dict[str, Any],
        occurrence: str,
        quiet_until: datetime,
        now_iso: str,
    ) -> Dict[str, Any]:
        """静默期内的提醒延后到静默结束再投递（原 occurrence 不变）。"""
        quiet_iso = quiet_until.isoformat()
        version = int(record["version"])
        new_payload = {
            "reminder_id": record["id"],
            "version": version,
            "occurrence": occurrence,
            # 延后后的"到点时刻"就是静默结束，迟到分钟数从这一刻算，不误导后续查询。
            "scheduled_at": quiet_iso,
            "quiet_deferred": True,
            "user_id": record["user_id"],
            "title": record["title"],
        }
        if payload.get("snooze"):
            new_payload["snooze"] = True
        self.scheduler.enqueue(
            REMINDER_FIRE_JOB,
            run_at=quiet_iso,
            job_key=f"{reminder_job_key(record['id'], version, occurrence)}:quiet:{quiet_iso}",
            payload=new_payload,
            max_attempts=self.max_attempts,
            backoff_seconds=self.backoff_seconds,
            backoff_multiplier=self.backoff_multiplier,
            max_backoff_seconds=self.max_backoff_seconds,
        )
        # 先入队（job_key 幂等），再记"已延后"：即使中途崩溃，重启后也不会误判成错过。
        self.store.record_delivery(
            reminder_id=record["id"],
            user_id=record["user_id"],
            occurrence=occurrence,
            status="deferred",
            channels=[],
            error=None,
            now_iso=now_iso,
        )
        self._audit(
            "reminder.deferred_for_quiet",
            "success",
            {
                "reminder_id": record["id"],
                "occurrence": occurrence,
                "until": quiet_iso,
                "at": now_iso,
            },
        )
        return {
            "status": "deferred",
            "reminder_id": record["id"],
            "occurrence": occurrence,
            "until": quiet_iso,
        }

    def _quiet_defer_until(self, now_dt: datetime) -> Optional[datetime]:
        """当前是否在静默期；是则返回静默结束的 naive UTC 时刻。"""
        cfg = self.settings()
        if not cfg["quiet_enabled"]:
            return None
        start = int(cfg["quiet_start_minutes"])
        end = int(cfg["quiet_end_minutes"])
        if start == end:
            return None
        local_now = self._to_local(now_dt)
        minute_of_day = local_now.hour * 60 + local_now.minute
        if start < end:
            in_quiet = start <= minute_of_day < end
        else:  # 跨零点，例如 23:00–07:00
            in_quiet = minute_of_day >= start or minute_of_day < end
        if not in_quiet:
            return None
        end_local = local_now.replace(
            hour=end // 60, minute=end % 60, second=0, microsecond=0
        )
        if end_local <= local_now:
            end_local = end_local + timedelta(days=1)
        return self._to_utc_naive(end_local)

    def _scheduled_at(self, payload: Dict[str, Any], occurrence: str) -> datetime:
        raw = payload.get("scheduled_at") or occurrence
        try:
            value = datetime.fromisoformat(str(raw))
        except ValueError:
            return self.scheduler.now()
        if value.tzinfo is not None:
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value

    def _enqueue_digest(
        self,
        user_id: str,
        *,
        run_at: Optional[datetime] = None,
        key_suffix: str = "",
    ) -> int:
        """摘要 job 幂等：同一用户同一时刻最多一条待发（重复登记不会多弹）。

        静默延后必须用带结束时刻的 key，否则会和**正在运行**的那条摘要撞 key，
        幂等逻辑会把它当成"已入队"而悄悄吞掉，摘要就永远发不出去。
        """
        kwargs: Dict[str, Any] = {}
        job_key = f"{DIGEST_JOB_KEY}:{user_id}"
        if run_at is None:
            kwargs["delay_seconds"] = DIGEST_DELAY_SECONDS
        else:
            kwargs["run_at"] = run_at
            job_key = f"{job_key}:quiet:{run_at.isoformat()}"
        if key_suffix:
            job_key = f"{job_key}:{key_suffix}"
        return self.scheduler.enqueue(
            REMINDER_DIGEST_JOB,
            job_key=job_key,
            payload={"user_id": user_id},
            max_attempts=self.max_attempts,
            backoff_seconds=self.backoff_seconds,
            backoff_multiplier=self.backoff_multiplier,
            max_backoff_seconds=self.max_backoff_seconds,
            **kwargs,
        )

    def _delivery_title(self, delivery: Dict[str, Any]) -> str:
        record = self.store.get(str(delivery.get("reminder_id") or ""))
        return str(record["title"]) if record else "（已删除的提醒）"

    def _delivery_time_text(self, delivery: Dict[str, Any]) -> str:
        occurrence = str(delivery.get("occurrence") or "")
        if occurrence.startswith("snooze:"):
            return "稍后提醒"
        try:
            scheduled = datetime.fromisoformat(occurrence)
        except ValueError:
            return occurrence or "未知时间"
        return self._to_local(scheduled).strftime("%Y-%m-%d %H:%M")

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

    def _reconcile_after_recorded(
        self, record: Dict[str, Any], occurrence: str
    ) -> None:
        """幂等跳过时补齐崩溃留下的收尾：标记完成 / 排下一次。

        只处理"当前 occurrence 已经记账"的情况；重复提醒若 run_at 还停在这个
        occurrence，说明上一次死在推进之前，先算下一次再排；若 run_at 已经前进，
        则按 job_key 幂等补一次 enqueue（补回"记账成功后、enqueue 前"崩溃丢的 job）。
        """

        if record["status"] != "active":
            return
        now_iso = _iso(self.scheduler.now())
        if record["repeat_kind"] == "once":
            self.store.mark_fired(
                record["id"], now_iso, next_run_at=None, now_iso=now_iso
            )
            return
        try:
            run_at = datetime.fromisoformat(str(record["run_at"]))
            current = datetime.fromisoformat(occurrence)
        except (TypeError, ValueError):
            return
        if run_at <= current:
            next_at = self._next_run_at(record, self.scheduler.now())
            if next_at:
                self.store.mark_fired(
                    record["id"], now_iso, next_run_at=next_at, now_iso=now_iso
                )
                self._enqueue({**record, "run_at": next_at}, next_at)
        else:
            self._enqueue(record, record["run_at"])

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
    "DEFAULT_MISSED_THRESHOLD_HOURS",
    "DEFAULT_QUIET_END_MINUTES",
    "DEFAULT_QUIET_START_MINUTES",
    "DEFAULT_SNOOZE_MINUTES",
    "REMINDER_DIGEST_JOB",
    "REMINDER_FIRE_JOB",
    "ReminderDeliveryError",
    "ReminderService",
    "ReminderStore",
    "default_reminders_db",
    "get_reminder_service",
    "reminder_job_key",
    "reminder_key_prefix",
]
