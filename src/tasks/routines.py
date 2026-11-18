"""例程（任务 5）：定时 / webhook 触发的自动任务。

定时（cron）与 webhook 是第一批触发器；新邮件 / 新消息 / 日历事件分别是
第二批、第三批——没接的触发器不在创建接口里出现，不画假选项。

持久化：全部状态落 SQLite（WAL），不是内存态——7-3 §12 的疑问已核实：
本地排程器（``src/tasks/local_scheduler.py``）与提醒库都是 SQLite 落盘，
重启不丢任务。例程同样落盘，重启后接着跑。

动作（第一批，只做能真实执行的三种）：
- ``chat_message``：把一句话发到 Joseph 的默认聊天会话里；
- ``create_plan``：按给定步骤建一份草稿计划，等 Joseph 批了再执行，
  例程批了不等于步骤批了，含危险动作的步骤执行到时仍弹卡。
- ``web_research``（C-额外补齐）：定时联网查询并把结果推送到聊天；
  复用 ``src.ai.web_research`` 的只读检索路径，联网检索开关没开时
  老实报错、查不到时老实说查不到，绝不编结果。
需要模型干活的动作（例如“汇总邮件”）是 C 组后续的 ``agent_goal``，
本轮不画假选项。

失败：每次运行都记一条运行记录（触发原因/耗时/结果/产物/Token 用量/
关联 plan_id），与审计用同一个 ``correlation_id``（即 run_id）。
连续失败 3 次自动暂停，并往聊天里说一句人话；全局暂停期间到点的例程
记“已跳过（全局暂停）”，不补跑。

错过规则（2026-11-14，A2a 第 8 项；实现见 ``decide_missed_run``）：
错过一次、且还没过下一次到点，就补做一次，运行记录里注明“补做”；
过期了（已过下一次到点）或连续错过多次的，跳过并记一条“已跳过，
不补做”，绝不悄悄攒一堆补做。全局暂停期间到点的同样记跳过、不补跑。

动作（新增 ``daily_digest``，第 19 项）：每天早上 8 点推今天待办，
走现有例程机制（cron ``0 8 * * *``，时区 America/Tijuana），
执行时现算汇总（今天到点的例程＋最近失败的运行记录），只报结果，
不推过程；设置里关掉“每天待办汇总”就是把这条例程停用。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

ROUTINE_ENV_VAR = "LIUHAO_ROUTINES_DB"
TRIGGER_SCHEDULE = "schedule"
TRIGGER_WEBHOOK = "webhook"
# 第一批只开放这两种触发器；其它类型创建时直接 422，不画假选项。
ALLOWED_TRIGGERS = (TRIGGER_SCHEDULE, TRIGGER_WEBHOOK)
ACTION_CHAT_MESSAGE = "chat_message"
ACTION_CREATE_PLAN = "create_plan"
ACTION_WEB_RESEARCH = "web_research"
#: 每天早上 8 点推今天待办（第 19 项）：执行时现算汇总，只报结果。
ACTION_DAILY_DIGEST = "daily_digest"
#: 每天晚上 9 点推今日小结（A2b 第 14 项）：执行时现算汇总，只报结果。
ACTION_EVENING_DIGEST = "evening_digest"
#: 到点提醒休息（A2b 第 12 项）：午饭 12:00、熬夜 23:00，轻声提醒。
ACTION_REST_REMINDER = "rest_reminder"
ALLOWED_ACTIONS = (ACTION_CHAT_MESSAGE, ACTION_CREATE_PLAN, ACTION_WEB_RESEARCH,
                   ACTION_DAILY_DIGEST, ACTION_EVENING_DIGEST, ACTION_REST_REMINDER)
AUTO_PAUSE_AFTER_FAILURES = 3

PLAN_ROLES = ("captain", "researcher", "organizer", "reminder", "coder")


class RoutineError(RuntimeError):
    """带给用户的例程错误（status_code / code / message 三件套）。"""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


# ── cron（5 段）──
# 2026-11 完善第二轮 D-R4 起引入 croniter（MIT）计算下一次到点：
# 差分探针实测手搓实现三处真分歧——`5/15` 这类单值带步长语义错
# （只在 :05 触发，标准语义是 :05/:20/:35/:50）、闰日表达式在下一次
# 到点超过 366 天扫描上限时被误拒、稀疏表达式 next_after 分钟级扫描
# 约 0.76s（croniter 约 0.5ms）。表达式合法性校验与报错口径仍走自家
# 解析器（422 + 中文文案不变），croniter 只做被校验过的表达式的
# 到点计算；不可能的日期（2 月 30 日）由 CroniterBadDateError 映射回
# 同一个 bad_cron 报错。

_MONTH_NAMES = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_DOW_NAMES = {
    "sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6,
}


def _parse_cron_field(raw: str, *, minimum: int, maximum: int, names: Optional[Dict[str, int]] = None) -> frozenset:
    values: set = set()
    for part in raw.split(","):
        part = part.strip().lower()
        if not part:
            raise RoutineError(422, "bad_cron", f"cron 表达式里有空段：{raw!r}")
        step = 1
        if "/" in part:
            base, step_raw = part.split("/", 1)
            try:
                step = int(step_raw)
            except ValueError:
                raise RoutineError(422, "bad_cron", f"cron 步长不是数字：{part!r}") from None
            if step < 1:
                raise RoutineError(422, "bad_cron", f"cron 步长必须 >= 1：{part!r}")
            part = base
        if part in ("*", ""):
            lo, hi = minimum, maximum
        elif "-" in part:
            start_raw, end_raw = part.split("-", 1)
            lo = _cron_number(start_raw, minimum, maximum, names)
            hi = _cron_number(end_raw, minimum, maximum, names)
            if lo > hi:
                raise RoutineError(422, "bad_cron", f"cron 范围写反了：{part!r}")
        else:
            lo = _cron_number(part, minimum, maximum, names)
            # 单值带步长的标准语义是「从该值到字段上限、按步长取」
            # （`5/15` = 5,20,35,50），不是只取该值本身——手搓初版
            # 在这里取错了，D-R4 差分探针实测后修正。
            hi = maximum if step > 1 else lo
        values.update(range(lo, hi + 1, step))
    return frozenset(values)


def _cron_number(raw: str, minimum: int, maximum: int, names: Optional[Dict[str, int]]) -> int:
    if names and raw in names:
        return names[raw]
    try:
        value = int(raw)
    except ValueError:
        raise RoutineError(422, "bad_cron", f"cron 里的数看不懂：{raw!r}") from None
    if value < minimum or value > maximum:
        raise RoutineError(422, "bad_cron", f"cron 里的数超出范围（{minimum}-{maximum}）：{raw!r}")
    return value


class CronSchedule:
    """5 段 cron：分 时 日 月 周。周日 0 和 7 都算周日。"""

    def __init__(self, expr: str) -> None:
        fields = expr.strip().split()
        if len(fields) != 5:
            raise RoutineError(422, "bad_cron", "cron 表达式必须是 5 段：分 时 日 月 周（例如 0 8 * * *）")
        self.expr = " ".join(fields)
        self.minute = _parse_cron_field(fields[0], minimum=0, maximum=59)
        self.hour = _parse_cron_field(fields[1], minimum=0, maximum=23)
        self.dom = _parse_cron_field(fields[2], minimum=1, maximum=31)
        self.month = _parse_cron_field(fields[3], minimum=1, maximum=12, names=_MONTH_NAMES)
        dow = _parse_cron_field(fields[4], minimum=0, maximum=7, names=_DOW_NAMES)
        self.dow = frozenset(0 if value == 7 else value for value in dow)
        self.dom_star = fields[2] == "*"
        self.dow_star = fields[4] == "*"

    def matches(self, moment: datetime) -> bool:
        if moment.minute not in self.minute:
            return False
        if moment.hour not in self.hour:
            return False
        if moment.month not in self.month:
            return False
        dom_match = moment.day in self.dom
        # cron 的老规矩：日与周都写了时任一满足即触发；只写一个时只看那个。
        dow_value = (moment.weekday() + 1) % 7
        dow_match = dow_value in self.dow
        if self.dom_star and self.dow_star:
            day_match = True
        elif self.dom_star:
            day_match = dow_match
        elif self.dow_star:
            day_match = dom_match
        else:
            day_match = dom_match or dow_match
        return day_match

    def next_after(self, after: datetime, tz: ZoneInfo) -> datetime:
        """after 之后下一个到点时刻（UTC 返回）。

        D-R4 起由 croniter 计算（见本节开头注释的差分证据）：没有
        366 天扫描上限（闰日表达式不再被误拒），稀疏表达式从约
        0.76s 降到毫秒级。不可能的日期（2 月 30 日）映射回同一个
        bad_cron 报错，调用方契约不变。
        """
        from croniter import CroniterBadDateError, croniter as _croniter

        start_local = after.astimezone(tz)
        try:
            nxt = _croniter(self.expr, start_local).get_next(datetime)
        except CroniterBadDateError:
            raise RoutineError(
                422, "bad_cron", "cron 表达式没有下一个到点时刻（例如 2 月 30 日）"
            ) from None
        except Exception:
            raise RoutineError(
                422, "bad_cron", f"cron 表达式算不出到点时刻：{self.expr!r}"
            ) from None
        return nxt.astimezone(timezone.utc)


def resolve_timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except Exception:
        raise RoutineError(422, "bad_timezone", f"时区看不懂：{name!r}（例如 Asia/Shanghai）") from None


# ── 存储 ─────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS routines (
    routine_id           TEXT PRIMARY KEY,
    name                 TEXT NOT NULL DEFAULT '',
    owner_account_id     TEXT NOT NULL DEFAULT '',
    created_by           TEXT NOT NULL DEFAULT '',
    trigger_type         TEXT NOT NULL DEFAULT 'schedule',
    schedule             TEXT NOT NULL DEFAULT '',
    timezone             TEXT NOT NULL DEFAULT 'Asia/Shanghai',
    webhook_secret_hash  TEXT NOT NULL DEFAULT '',
    action_kind          TEXT NOT NULL DEFAULT 'chat_message',
    action_text          TEXT NOT NULL DEFAULT '',
    plan_title           TEXT NOT NULL DEFAULT '',
    plan_goal            TEXT NOT NULL DEFAULT '',
    plan_steps_json      TEXT NOT NULL DEFAULT '[]',
    enabled              INTEGER NOT NULL DEFAULT 1,
    auto_paused          INTEGER NOT NULL DEFAULT 0,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    last_run_at          REAL,
    next_run_at          REAL,
    created_at           REAL NOT NULL,
    updated_at           REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_routines_owner ON routines(owner_account_id);
CREATE TABLE IF NOT EXISTS routine_runs (
    run_id       TEXT PRIMARY KEY,
    routine_id   TEXT NOT NULL,
    trigger      TEXT NOT NULL DEFAULT '',
    reason       TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'success',
    result_text  TEXT NOT NULL DEFAULT '',
    error        TEXT NOT NULL DEFAULT '',
    artifacts_json TEXT NOT NULL DEFAULT '[]',
    tokens_used  INTEGER NOT NULL DEFAULT 0,
    plan_id      TEXT NOT NULL DEFAULT '',
    started_at   REAL NOT NULL,
    ended_at     REAL,
    duration_ms  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_routine_runs_routine ON routine_runs(routine_id, started_at);
CREATE TABLE IF NOT EXISTS routine_settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _loads(text: Any, fallback: Any) -> Any:
    if text is None or text == "":
        return fallback
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return fallback


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def default_routines_db() -> str:
    from src.tasks._queue_paths import resolve_queue_file

    return resolve_queue_file("routines.db", env_var=ROUTINE_ENV_VAR)


class RoutineStore:
    def __init__(self, db_path: Optional[str] = None) -> None:
        self._db_path = str(db_path or default_routines_db())
        directory = os.path.dirname(os.path.abspath(self._db_path))
        if directory:
            os.makedirs(directory, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self._db_path, timeout=10.0, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA busy_timeout=10000")
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                finally:
                    self._conn = None

    # ── 例程 ──

    def create_routine(self, *, name: str, owner_account_id: str, created_by: str,
                       trigger_type: str, schedule: str, timezone_name: str,
                       action_kind: str, action_text: str, plan_title: str,
                       plan_goal: str, plan_steps: Sequence[Dict[str, Any]],
                       webhook_secret: str = "") -> Dict[str, Any]:
        routine_id = _new_id("routine")
        now = time.time()
        next_run: Optional[float] = None
        if trigger_type == TRIGGER_SCHEDULE:
            cron = CronSchedule(schedule)
            tz = resolve_timezone(timezone_name)
            next_run = cron.next_after(
                datetime.fromtimestamp(now, tz=timezone.utc), tz
            ).timestamp()
        secret_hash = (
            hashlib.sha256(webhook_secret.encode("utf-8")).hexdigest()
            if webhook_secret
            else ""
        )
        with self._lock:
            self._conn.execute(
                "INSERT INTO routines(routine_id, name, owner_account_id, created_by,"
                " trigger_type, schedule, timezone, webhook_secret_hash, action_kind,"
                " action_text, plan_title, plan_goal, plan_steps_json, enabled,"
                " auto_paused, consecutive_failures, last_run_at, next_run_at,"
                " created_at, updated_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,1,0,0,NULL,?,?,?)",
                (
                    routine_id, name, owner_account_id, created_by, trigger_type,
                    schedule, timezone_name, secret_hash, action_kind, action_text,
                    plan_title, plan_goal, _dumps(list(plan_steps)), next_run, now, now,
                ),
            )
            self._conn.commit()
        return self.get_routine(routine_id) or {}

    def get_routine(self, routine_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM routines WHERE routine_id=?", (routine_id,)
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["plan_steps"] = _loads(item.pop("plan_steps_json"), [])
        item["enabled"] = bool(item["enabled"])
        item["auto_paused"] = bool(item["auto_paused"])
        # 密钥只存哈希，永不外泄。
        item.pop("webhook_secret_hash", None)
        return item

    def list_routines(self, *, owners: Optional[Sequence[str]] = None,
                      limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
        clauses: List[str] = []
        params: List[Any] = []
        if owners is not None:
            if not owners:
                return []
            placeholders = ", ".join("?" for _ in owners)
            clauses.append(f"owner_account_id IN ({placeholders})")
            params.extend(owners)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        params.extend([limit, offset])
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM routines" + where
                + " ORDER BY created_at DESC LIMIT ? OFFSET ?",
                params,
            ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["plan_steps"] = _loads(item.pop("plan_steps_json"), [])
            item["enabled"] = bool(item["enabled"])
            item["auto_paused"] = bool(item["auto_paused"])
            item.pop("webhook_secret_hash", None)
            items.append(item)
        return items

    def update_routine(self, routine_id: str, **fields: Any) -> Dict[str, Any]:
        allowed = {
            "name", "trigger_type", "schedule", "timezone", "action_kind",
            "action_text", "plan_title", "plan_goal", "plan_steps_json",
            "enabled", "auto_paused", "consecutive_failures",
            "last_run_at", "next_run_at",
        }
        sets: List[str] = ["updated_at=?"]
        params: List[Any] = [time.time()]
        for key, value in fields.items():
            if key not in allowed:
                continue
            sets.append(f"{key}=?")
            params.append(value)
        params.append(routine_id)
        with self._lock:
            cursor = self._conn.execute(
                f"UPDATE routines SET {', '.join(sets)} WHERE routine_id=?", params
            )
            if cursor.rowcount == 0:
                raise RoutineError(404, "routine_not_found", f"例程不存在：{routine_id}")
            self._conn.commit()
        return self.get_routine(routine_id) or {}

    def delete_routine(self, routine_id: str) -> None:
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM routines WHERE routine_id=?", (routine_id,)
            )
            self._conn.execute(
                "DELETE FROM routine_runs WHERE routine_id=?", (routine_id,)
            )
            if cursor.rowcount == 0:
                raise RoutineError(404, "routine_not_found", f"例程不存在：{routine_id}")
            self._conn.commit()

    def update_routine_details(self, routine_id: str, *, name: Optional[str] = None,
                               schedule: Optional[str] = None,
                               timezone_name: Optional[str] = None) -> Dict[str, Any]:
        """改名 / 改执行时间（例程改名、改时间两项）。

        改名只改名字，别的都不动；改时间只对定时例程有效（webhook 例程
        没有定时，改不了时间），新时间要过 cron 校验，下次到点按新时间
        从现在重算。开关、失败计数、自动暂停、运行记录原样不动；错过规则
        （decide_missed_run）照旧按新的 cron 与新的下次到点走。
        """
        routine = self.get_routine(routine_id)
        if routine is None:
            raise RoutineError(404, "routine_not_found", f"例程不存在：{routine_id}")
        fields: Dict[str, Any] = {}
        if name is not None:
            new_name = str(name).strip()
            if not new_name:
                raise RoutineError(422, "empty_name", "例程名字不能为空")
            if len(new_name) > 200:
                raise RoutineError(422, "name_too_long", "例程名字最多 200 个字")
            fields["name"] = new_name
        if schedule is not None or timezone_name is not None:
            if routine.get("trigger_type") != TRIGGER_SCHEDULE:
                raise RoutineError(
                    422, "not_schedule",
                    "webhook 例程没有定时，改不了时间；要改时间先删了重建一个定时例程",
                )
            new_schedule = (schedule if schedule is not None
                            else str(routine.get("schedule") or "")).strip()
            new_timezone = (timezone_name if timezone_name is not None
                            else str(routine.get("timezone") or "Asia/Shanghai")).strip()
            cron = CronSchedule(new_schedule)
            tz = resolve_timezone(new_timezone)
            fields["schedule"] = new_schedule
            fields["timezone"] = new_timezone
            fields["next_run_at"] = cron.next_after(
                datetime.fromtimestamp(time.time(), tz=timezone.utc), tz
            ).timestamp()
        if not fields:
            return routine
        return self.update_routine(routine_id, **fields)

    def verify_webhook_secret(self, routine_id: str, secret: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT webhook_secret_hash FROM routines WHERE routine_id=?",
                (routine_id,),
            ).fetchone()
        if row is None or not row["webhook_secret_hash"]:
            return False
        digest = hashlib.sha256(secret.encode("utf-8")).hexdigest()
        return hmac.compare_digest(digest, str(row["webhook_secret_hash"]))

    def revoke_webhook_secret(self, routine_id: str) -> Dict[str, Any]:
        """吊销 webhook 密钥（P3）：清掉哈希，此后任何 webhook 调用都 401。

        旧密钥立即失效；要再接 webhook 须轮换出一个新密钥。
        """
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE routines SET webhook_secret_hash='', updated_at=?"
                " WHERE routine_id=?",
                (time.time(), routine_id),
            )
            if cursor.rowcount == 0:
                raise RoutineError(404, "routine_not_found", f"例程不存在：{routine_id}")
            self._conn.commit()
        return self.get_routine(routine_id) or {}

    def rotate_webhook_secret(self, routine_id: str) -> Dict[str, Any]:
        """轮换 webhook 密钥（P3）：生成新密钥，只返回一次明文（不落盘）。

        返回 {"routine": ..., "webhook_secret": "<新密钥，只显示这一次>"}；
        旧密钥立即失效。调用方须把新密钥交给调用方保存，丢了只能再轮换。
        """
        secret = generate_webhook_secret()
        digest = hashlib.sha256(secret.encode("utf-8")).hexdigest()
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE routines SET webhook_secret_hash=?, updated_at=?"
                " WHERE routine_id=?",
                (digest, time.time(), routine_id),
            )
            if cursor.rowcount == 0:
                raise RoutineError(404, "routine_not_found", f"例程不存在：{routine_id}")
            self._conn.commit()
        return {"routine": self.get_routine(routine_id) or {}, "webhook_secret": secret}

    # ── 运行记录 ──

    def record_run(self, *, routine_id: str, trigger: str, reason: str,
                   status: str, result_text: str = "", error: str = "",
                   artifacts: Optional[Sequence[str]] = None,
                   tokens_used: int = 0, plan_id: str = "",
                   started_at: float, ended_at: float) -> Dict[str, Any]:
        run_id = _new_id("rrun")
        with self._lock:
            self._conn.execute(
                "INSERT INTO routine_runs(run_id, routine_id, trigger, reason, status,"
                " result_text, error, artifacts_json, tokens_used, plan_id,"
                " started_at, ended_at, duration_ms)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    run_id, routine_id, trigger, reason, status, result_text, error,
                    _dumps(list(artifacts or [])), int(tokens_used), plan_id,
                    started_at, ended_at, int((ended_at - started_at) * 1000),
                ),
            )
            self._conn.commit()
        return self.get_run(run_id) or {}

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM routine_runs WHERE run_id=?", (run_id,)
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["artifacts"] = _loads(item.pop("artifacts_json"), [])
        return item

    def list_runs(self, routine_id: str, *, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM routine_runs WHERE routine_id=?"
                " ORDER BY started_at DESC LIMIT ? OFFSET ?",
                (routine_id, limit, offset),
            ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["artifacts"] = _loads(item.pop("artifacts_json"), [])
            items.append(item)
        return items

    def recent_failures(self, *, owners: Optional[Sequence[str]] = None,
                        since: Optional[float] = None, limit: int = 100) -> List[Dict[str, Any]]:
        """最近失败的运行（角标与通知用）。"""
        moment = float(since if since is not None else time.time() - 7 * 24 * 3600)
        with self._lock:
            rows = self._conn.execute(
                "SELECT r.* FROM routine_runs r JOIN routines t ON t.routine_id = r.routine_id"
                " WHERE r.status='failed' AND r.started_at >= ?"
                " ORDER BY r.started_at DESC LIMIT ?",
                (moment, limit),
            ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["artifacts"] = _loads(item.pop("artifacts_json"), [])
            if owners is not None and item.get("routine_id"):
                routine = self.get_routine(str(item["routine_id"]))
                if routine is None or routine.get("owner_account_id") not in owners:
                    continue
            items.append(item)
        return items

    # ── 全局暂停 ──

    def get_paused_all(self) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM routine_settings WHERE key='paused_all'"
            ).fetchone()
        return row is not None and str(row["value"]) == "1"

    def set_paused_all(self, paused: bool) -> bool:
        with self._lock:
            self._conn.execute(
                "INSERT INTO routine_settings(key, value) VALUES('paused_all', ?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                ("1" if paused else "0",),
            )
            self._conn.commit()
        return paused


_routines_lock = threading.Lock()
_routine_store: Optional[RoutineStore] = None


def get_routine_store(path: Optional[str] = None) -> RoutineStore:
    global _routine_store
    if path is not None:
        return RoutineStore(path)
    with _routines_lock:
        if _routine_store is None:
            _routine_store = RoutineStore()
        return _routine_store


def reset_routine_store() -> None:
    global _routine_store
    with _routines_lock:
        if _routine_store is not None:
            try:
                _routine_store.close()
            except Exception:  # noqa: BLE001
                pass
            _routine_store = None


# ── 聊天通知 ─────────────────────────────────────────────────

def _default_session_principal() -> str:
    """Joseph 的默认聊天会话键（与 src/gateway/chat.py 的取法一致：
    有旧数据沿用旧键，否则用新键）。"""
    try:
        from src.kernels.identity import get_identity_manager

        node = get_identity_manager().account_root()
        root = str(node.principal) if node is not None else None
    except Exception:  # noqa: BLE001 - 身份未就绪时按无根处理
        root = None
    from src.ai.conversation_store import get_conversation_store

    store = get_conversation_store()
    candidates = []
    if root:
        candidates.append(f"liuhao-{root}-default")
    candidates.append("liuhao-default")
    for candidate in candidates:
        try:
            if store.has_rows(candidate):
                return candidate
        except Exception:  # noqa: BLE001 - 查不到就当没有
            continue
    return candidates[0]


def notify_chat(text: str) -> bool:
    """往默认聊天会话里追加一句贾维斯的话。失败返回 False（不抛错、不阻断运行记录）。"""
    try:
        from src.ai.conversation_store import get_conversation_store

        store = get_conversation_store()
        principal = _default_session_principal()
        turn = store.get_last_turn(principal) + 1
        store.append(principal, turn, "assistant", text)
        return True
    except Exception:  # noqa: BLE001 - 通知失败不能拖垮例程本身
        logger.warning("routine chat notify failed", exc_info=True)
        return False


def _audit(action: str, *, principal_id: str, outcome: str,
           details: Dict[str, Any], correlation_id: str) -> None:
    try:
        from src.kernels.audit import AuditEventType, AuditScope, log_event

        log_event(
            AuditEventType.STATE_CHANGE,
            principal_id or "system",
            AuditScope.L1,
            outcome,
            {"action": action, **details},
            correlation_id=correlation_id,
        )
    except Exception:  # noqa: BLE001 - 审计写不进去不能拖垮例程
        logger.warning("routine audit failed: %s", action)


def validate_plan_steps(raw: Any) -> List[Dict[str, Any]]:
    """计划步骤校验（与 src/gateway/plans.py 同一套规则：队长 + 4 个固定角色）。"""
    if not isinstance(raw, list) or not raw:
        raise RoutineError(422, "empty_steps", "计划至少要有一个步骤")
    if len(raw) > 50:
        raise RoutineError(422, "too_many_steps", "计划最多 50 步，先拆小再派")
    steps: List[Dict[str, Any]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise RoutineError(422, "bad_step", f"第 {index + 1} 步不是一个步骤对象")
        title = str(item.get("title") or "").strip()
        what = str(item.get("what") or "").strip()
        assignee = str(item.get("assignee") or "").strip()
        risk = str(item.get("risk") or "low").strip()
        if not title:
            raise RoutineError(422, "bad_step", f"第 {index + 1} 步没有标题")
        if not what:
            raise RoutineError(422, "bad_step", f"第 {index + 1} 步没有写要干什么")
        if assignee not in PLAN_ROLES:
            raise RoutineError(422, "bad_step", f"第 {index + 1} 步的执行人只能是队长或 4 个固定角色")
        if risk not in ("low", "medium", "high"):
            raise RoutineError(422, "bad_step", f"第 {index + 1} 步的风险只能是 low/medium/high")
        targets = item.get("targets") or []
        if not isinstance(targets, list) or any(not isinstance(t, str) for t in targets):
            raise RoutineError(422, "bad_step", f"第 {index + 1} 步的目标清单必须是字符串列表")
        steps.append({
            "title": title,
            "what": what,
            "assignee": assignee,
            "tool": str(item.get("tool") or "").strip(),
            "risk": risk,
            "expected_impact": str(item.get("expected_impact") or "").strip(),
            "targets": [str(t) for t in targets],
            "reversible": bool(item.get("reversible", True)),
            "rollback_note": str(item.get("rollback_note") or "").strip(),
            "approval_required": bool(item.get("approval_required", False)),
        })
    return steps


# ── 执行 ─────────────────────────────────────────────────────

def _cron_interval_seconds(routine: Dict[str, Any]) -> Optional[float]:
    """两次到点之间的秒数（由 cron 表达式算出）；算不出来返回 None。"""
    try:
        cron = CronSchedule(str(routine.get("schedule") or ""))
        tz = resolve_timezone(str(routine.get("timezone") or "Asia/Shanghai"))
        nxt = float(routine.get("next_run_at") or 0)
        start = datetime.fromtimestamp(nxt, tz=timezone.utc)
        following = cron.next_after(start, tz)
        return max(0.0, (following - start).total_seconds())
    except (RoutineError, ValueError, OverflowError, OSError):
        return None


def decide_missed_run(routine: Dict[str, Any], now: float) -> str:
    """定时任务错过规则（第 8 项）：

    * ``"due"``：没错过，到点正常跑；
    * ``"catch_up_once"``：只错过一次（错过时长没超过 1.5 个周期），
      补做一次，运行记录里注明“补做”；
    * ``"skip"``：过期了（已过下一次到点）或连续错过多次，跳过并记一条，
      绝不悄悄攒一堆补做。
    """
    next_run = routine.get("next_run_at")
    if next_run is None:
        return "due"
    missed = float(now) - float(next_run)
    if missed <= 0:
        return "due"
    interval = _cron_interval_seconds(routine)
    if interval is None or interval <= 0:
        return "catch_up_once"
    if missed <= interval * 1.5:
        return "catch_up_once"
    return "skip"


#: 每天早上 8 点待办汇总例程的名字（按名字＋账号去重，只建一条）。
DIGEST_ROUTINE_NAME = "每天早上 8 点待办汇总"
DIGEST_ROUTINE_SCHEDULE = "0 8 * * *"
DIGEST_ROUTINE_TIMEZONE = "America/Tijuana"


def build_daily_digest_text(store: "RoutineStore", owner_account_id: str) -> str:
    """今天待办的结果汇总（只报结果，不推过程）。

    内容来自真实记录：今天到点的例程＋最近失败/跳过的运行；没有就是
    老实的“今天没有待办”，不编。
    """
    lines = ["今天的待办汇总："]
    today = datetime.now(timezone.utc).date()
    try:
        routines = store.list_routines(owners=[str(owner_account_id)], limit=100)
    except Exception:  # noqa: BLE001 - 例程库读不到时汇总里如实说
        return "今天的待办汇总：例程列表读不出来，汇总没算成，请稍后再看。"
    due = []
    for routine in routines:
        if str(routine.get("action_kind") or "") == ACTION_DAILY_DIGEST:
            continue
        nxt = routine.get("next_run_at")
        if nxt and datetime.fromtimestamp(float(nxt), tz=timezone.utc).date() == today:
            due.append(str(routine.get("name") or routine.get("routine_id")))
    if due:
        lines.append("今天要跑的例程：" + "、".join(due))
    failed: List[str] = []
    for routine in routines:
        try:
            runs = store.list_runs(str(routine.get("routine_id")), limit=5)
        except Exception:  # noqa: BLE001
            continue
        for run in runs:
            if run.get("status") in ("failed", "skipped"):
                failed.append(
                    f"· {routine.get('name') or routine.get('routine_id')}："
                    f"{run.get('result_text') or run.get('error') or run.get('status')}"
                )
                break
    if failed:
        lines.append("最近没跑成的：")
        lines.extend(failed[:5])
    if len(lines) == 1:
        lines.append("今天没有待办。")
    return "\n".join(lines)


def ensure_morning_digest_routine(
    store: Optional["RoutineStore"] = None,
    *,
    owner_account_id: str,
    created_by: str,
) -> Dict[str, Any]:
    """确保“每天早上 8 点待办汇总”例程存在（第 19 项）。

    走现有例程机制，不新造定时系统；已存在就直接返回那条，不重复建。
    默认开；设置里关就是把这条例程停用（``enabled=0``）。
    """
    store = store or get_routine_store()
    for routine in store.list_routines(owners=[str(owner_account_id)], limit=500):
        if (
            str(routine.get("action_kind") or "") == ACTION_DAILY_DIGEST
            and routine.get("trigger_type") == TRIGGER_SCHEDULE
        ):
            return routine
    return store.create_routine(
        name=DIGEST_ROUTINE_NAME,
        owner_account_id=str(owner_account_id),
        created_by=str(created_by),
        trigger_type=TRIGGER_SCHEDULE,
        schedule=DIGEST_ROUTINE_SCHEDULE,
        timezone_name=DIGEST_ROUTINE_TIMEZONE,
        action_kind=ACTION_DAILY_DIGEST,
        action_text="",
        plan_title="",
        plan_goal="",
        plan_steps=[],
    )


#: 每天晚上 9 点今日小结例程的名字（按名字＋账号去重，只建一条）。
EVENING_DIGEST_ROUTINE_NAME = "每天晚上 9 点今日小结"
EVENING_DIGEST_ROUTINE_SCHEDULE = "0 21 * * *"
EVENING_DIGEST_ROUTINE_TIMEZONE = "America/Tijuana"

#: 到点提醒休息的两条例程（按名字＋账号去重，各只建一条；默认开）。
REST_REMINDER_ROUTINES = (
    ("午饭提醒", "0 12 * * *"),
    ("熬夜提醒", "0 23 * * *"),
)
REST_REMINDER_TIMEZONE = "America/Tijuana"


def build_evening_digest_text(store: "RoutineStore", owner_account_id: str) -> str:
    """晚上今日小结：办了几件、卡住哪件、明天有什么。

    数字只从真实台账出：今天完成的任务数、卡住（失败/等人）的任务、
    明天到点的例程。没办的就是 0，不编。
    """
    lines = ["今天的小结："]
    done_today = 0
    stuck: List[str] = []
    try:
        from src.gateway.task_ledger import TaskLedger

        page = TaskLedger().list_tasks(owners=[str(owner_account_id)], limit=500)
        items = page.get("items", []) if isinstance(page, dict) else []
        today = datetime.now(timezone.utc).date()
        for task in items:
            if not isinstance(task, dict):
                continue
            completed = task.get("completed_at") or ""
            if completed:
                try:
                    if datetime.fromisoformat(str(completed).replace("Z", "+00:00")).date() == today:
                        done_today += 1
                except ValueError:
                    pass
            if str(task.get("status") or "") == "failed" or task.get("human_hold"):
                stuck.append(str(task.get("title") or task.get("task_id") or ""))
    except Exception:  # noqa: BLE001 - 台账读不到时如实说，不编数字
        return "今天的小结：任务台账读不出来，小结没算成，请稍后再看。"
    lines.append(f"今天办完了 {done_today} 件。")
    if stuck:
        lines.append("卡住的：")
        lines.extend(f"· {title}" for title in stuck[:5])
    else:
        lines.append("没有卡住的。")
    try:
        routines = store.list_routines(owners=[str(owner_account_id)], limit=100)
        tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).date()
        due = []
        for routine in routines:
            if str(routine.get("action_kind") or "") in (ACTION_DAILY_DIGEST, ACTION_EVENING_DIGEST):
                continue
            nxt = routine.get("next_run_at")
            if nxt and datetime.fromtimestamp(float(nxt), tz=timezone.utc).date() == tomorrow:
                due.append(str(routine.get("name") or routine.get("routine_id")))
        if due:
            lines.append("明天要跑的：" + "、".join(due))
        else:
            lines.append("明天暂时没有到点的例程。")
    except Exception:  # noqa: BLE001 - 例程读不到不影响前面的小结
        lines.append("明天的例程没读出来，请明天早上看待办汇总。")
    return "\n".join(lines)


def ensure_evening_digest_routine(
    store: Optional["RoutineStore"] = None,
    *,
    owner_account_id: str,
    created_by: str,
) -> Dict[str, Any]:
    """确保“每天晚上 9 点今日小结”例程存在（A2b 第 14 项）。

    走现有例程机制，不新造定时系统；已存在就直接返回那条，不重复建。
    默认开；设置里关就是把这条例程停用（``enabled=0``）。
    """
    store = store or get_routine_store()
    for routine in store.list_routines(owners=[str(owner_account_id)], limit=500):
        if (
            str(routine.get("action_kind") or "") == ACTION_EVENING_DIGEST
            and routine.get("trigger_type") == TRIGGER_SCHEDULE
        ):
            return routine
    return store.create_routine(
        name=EVENING_DIGEST_ROUTINE_NAME,
        owner_account_id=str(owner_account_id),
        created_by=str(created_by),
        trigger_type=TRIGGER_SCHEDULE,
        schedule=EVENING_DIGEST_ROUTINE_SCHEDULE,
        timezone_name=EVENING_DIGEST_ROUTINE_TIMEZONE,
        action_kind=ACTION_EVENING_DIGEST,
        action_text="",
        plan_title="",
        plan_goal="",
        plan_steps=[],
    )


def ensure_rest_reminder_routines(
    store: Optional["RoutineStore"] = None,
    *,
    owner_account_id: str,
    created_by: str,
) -> List[Dict[str, Any]]:
    """确保“到点提醒休息”两条例程存在（A2b 第 12 项）：午饭 12:00、
    熬夜 23:00。已存在就直接返回，不重复建；默认开，设置里能关。
    """
    store = store or get_routine_store()
    rest = [
        r
        for r in store.list_routines(owners=[str(owner_account_id)], limit=500)
        if str(r.get("action_kind") or "") == ACTION_REST_REMINDER
    ]
    routines: List[Dict[str, Any]] = []
    for name, schedule in REST_REMINDER_ROUTINES:
        # 名字或时间任一对上就算同一条：改名、改时间都不再造重复。
        found = next(
            (
                r
                for r in rest
                if str(r.get("name") or "") == name
                or str(r.get("schedule") or "") == schedule
            ),
            None,
        )
        if found is not None:
            routines.append(found)
            continue
        routines.append(store.create_routine(
            name=name,
            owner_account_id=str(owner_account_id),
            created_by=str(created_by),
            trigger_type=TRIGGER_SCHEDULE,
            schedule=schedule,
            timezone_name=REST_REMINDER_TIMEZONE,
            action_kind=ACTION_REST_REMINDER,
            action_text="",
            plan_title="",
            plan_goal="",
            plan_steps=[],
        ))
    return routines


class RoutineRunner:
    """到点/触发时执行例程。一次 tick 里每个例程最多跑一次，不补跑一堆。"""

    def __init__(self, store: Optional[RoutineStore] = None) -> None:
        self.store = store or get_routine_store()

    def run_due(self, *, now: Optional[float] = None) -> List[Dict[str, Any]]:
        """跑所有到点的定时例程，返回本次的运行记录。

        错过规则（见模块头）：只错过一次的补做一次并注明“补做”；
        过期或连续错过多次的记跳过、不补做。
        """
        moment = float(now if now is not None else time.time())
        paused_all = self.store.get_paused_all()
        results: List[Dict[str, Any]] = []
        for routine in self.store.list_routines(limit=500):
            if routine["trigger_type"] != TRIGGER_SCHEDULE:
                continue
            next_run = routine.get("next_run_at")
            if next_run is None or float(next_run) > moment:
                continue
            if not routine["enabled"]:
                continue
            if paused_all:
                results.append(
                    self._record_skipped(routine, moment, "全局暂停")
                )
                self._advance_next_run(routine, moment)
                continue
            decision = decide_missed_run(routine, moment)
            if decision == "skip":
                results.append(
                    self._record_skipped(routine, moment, "连续错过多次，已跳过，不补做")
                )
                self._advance_next_run(routine, moment)
                continue
            reason = "补做（上次到点错过了）" if decision == "catch_up_once" else ""
            results.append(self.run_once(routine, trigger="schedule", now=moment, reason=reason))
            self._advance_next_run(routine, moment)
        return results

    def _advance_next_run(self, routine: Dict[str, Any], moment: float) -> None:
        """把下一次到点推到未来。停机久了不补跑：直接跳到下一次。"""
        try:
            cron = CronSchedule(str(routine["schedule"]))
            tz = resolve_timezone(str(routine["timezone"] or "Asia/Shanghai"))
            nxt = cron.next_after(
                datetime.fromtimestamp(moment, tz=timezone.utc), tz
            ).timestamp()
            self.store.update_routine(str(routine["routine_id"]), next_run_at=nxt)
        except RoutineError:
            logger.warning("routine next_run advance failed: %s", routine["routine_id"])

    def _record_skipped(self, routine: Dict[str, Any], moment: float, why: str) -> Dict[str, Any]:
        return self.store.record_run(
            routine_id=str(routine["routine_id"]),
            trigger="schedule",
            reason=f"定时到点（已跳过：{why}）",
            status="skipped",
            result_text=f"到点但{why}，没执行，不补跑。",
            started_at=moment,
            ended_at=moment,
        )

    def run_once(self, routine: Dict[str, Any], *, trigger: str,
                 now: Optional[float] = None, reason: str = "") -> Dict[str, Any]:
        """执行一次例程（定时到点 / webhook / 手动），记运行记录并处理失败计数。"""
        moment = float(now if now is not None else time.time())
        routine_id = str(routine["routine_id"])
        started = time.time()
        default_reason = {
            "schedule": f"定时到点（{routine.get('schedule', '')}）",
            "webhook": "webhook 调用",
            "manual": "手动运行",
        }.get(trigger, trigger)
        run_reason = reason or default_reason
        try:
            result_text, plan_id, artifacts = self._execute(routine)
            record = self.store.record_run(
                routine_id=routine_id,
                trigger=trigger,
                reason=run_reason,
                status="success",
                result_text=result_text,
                artifacts=artifacts,
                tokens_used=0,
                plan_id=plan_id,
                started_at=started,
                ended_at=time.time(),
            )
            self.store.update_routine(
                routine_id, consecutive_failures=0, last_run_at=time.time()
            )
            _audit(
                "routine.run",
                principal_id="system",
                outcome="success",
                details={"routine_id": routine_id, "trigger": trigger},
                correlation_id=str(record["run_id"]),
            )
            return record
        except Exception as exc:  # noqa: BLE001 - 失败也要记记录、记数、报信
            ended = time.time()
            error_text = str(exc) or type(exc).__name__
            record = self.store.record_run(
                routine_id=routine_id,
                trigger=trigger,
                reason=run_reason,
                status="failed",
                error=error_text,
                started_at=started,
                ended_at=ended,
            )
            failures = int(routine.get("consecutive_failures") or 0) + 1
            updates: Dict[str, Any] = {
                "consecutive_failures": failures,
                "last_run_at": ended,
            }
            auto_paused = False
            if failures >= AUTO_PAUSE_AFTER_FAILURES:
                updates["enabled"] = 0
                updates["auto_paused"] = 1
                auto_paused = True
            self.store.update_routine(routine_id, **updates)
            name = routine.get("name") or routine_id
            if auto_paused:
                notify_chat(
                    f"例程「{name}」连着失败 {AUTO_PAUSE_AFTER_FAILURES} 次，我先帮你停了，"
                    "你看一下再开。失败原因写在例程页的运行记录里。"
                )
            else:
                notify_chat(
                    f"例程「{name}」没跑成，原因是：{error_text}。"
                    f"这是连续第 {failures} 次失败，连续失败 {AUTO_PAUSE_AFTER_FAILURES} 次我会先帮你停了。"
                )
            _audit(
                "routine.run",
                principal_id="system",
                outcome="deny",
                details={
                    "routine_id": routine_id,
                    "trigger": trigger,
                    "error": error_text,
                    "consecutive_failures": failures,
                    "auto_paused": auto_paused,
                },
                correlation_id=str(record["run_id"]),
            )
            return record

    def _execute(self, routine: Dict[str, Any]) -> tuple:
        """执行动作。返回 (result_text, plan_id, artifacts)。"""
        kind = str(routine.get("action_kind") or "")
        if kind == ACTION_DAILY_DIGEST:
            # 每天早上 8 点推今天待办：执行时现算汇总，只报结果，不推过程。
            text = build_daily_digest_text(self.store, str(routine.get("owner_account_id") or ""))
            if not notify_chat(text):
                raise RoutineError(500, "chat_notify_failed", "消息没能写进聊天（聊天记录不可用）")
            try:
                from src.gateway.a2b import emit_digest_event

                emit_digest_event("morning", text)
            except Exception:  # noqa: BLE001 - 推送失败不影响汇总已发
                pass
            return f"已发到聊天：{text}", "", []
        if kind == ACTION_EVENING_DIGEST:
            # 每天晚上 9 点推今日小结：数字只从真实台账出，没办的就是 0。
            text = build_evening_digest_text(self.store, str(routine.get("owner_account_id") or ""))
            if not notify_chat(text):
                raise RoutineError(500, "chat_notify_failed", "消息没能写进聊天（聊天记录不可用）")
            try:
                from src.gateway.a2b import emit_digest_event

                emit_digest_event("evening", text)
            except Exception:  # noqa: BLE001 - 推送失败不影响汇总已发
                pass
            return f"已发到聊天：{text}", "", []
        if kind == ACTION_REST_REMINDER:
            # 到点提醒休息：轻声提醒一句，不推过程、不重复催。
            name = str(routine.get("name") or "")
            if "熬夜" in name:
                text = "很晚了，该休息了——剩下的事明天再干，我帮你记着，不会丢。"
                reminder_kind = "late_night"
            else:
                text = "到饭点了，先吃口饭、歇一会儿再干，身体要紧。"
                reminder_kind = "lunch"
            if not notify_chat(text):
                raise RoutineError(500, "chat_notify_failed", "消息没能写进聊天（聊天记录不可用）")
            try:
                from src.gateway.a2b import emit_reminder_event

                emit_reminder_event(reminder_kind, text)
            except Exception:  # noqa: BLE001 - 推送失败不影响提醒已发
                pass
            return f"已发到聊天：{text}", "", []
        if kind == ACTION_CHAT_MESSAGE:
            text = str(routine.get("action_text") or "").strip()
            if not text:
                raise RoutineError(422, "empty_action", "例程的消息内容是空的")
            if not notify_chat(text):
                raise RoutineError(500, "chat_notify_failed", "消息没能写进聊天（聊天记录不可用）")
            return f"已发到聊天：{text}", "", []
        if kind == ACTION_CREATE_PLAN:
            from src.workflow.store import get_workflow_store

            steps = validate_plan_steps(routine.get("plan_steps") or [])
            plan = get_workflow_store().create_plan(
                title=str(routine.get("plan_title") or routine.get("name") or ""),
                goal=str(routine.get("plan_goal") or ""),
                created_by="routine:" + str(routine["routine_id"]),
                owner_account_id=str(routine.get("owner_account_id") or ""),
                steps=steps,
            )
            return (
                f"已建草稿计划「{plan['title']}」，等你批了再执行。",
                str(plan["plan_id"]),
                [f"plan:{plan['plan_id']}"],
            )
        if kind == ACTION_WEB_RESEARCH:
            # 定时联网查询（C-额外）：查询词复用 action_text，不另加字段。
            # 开关没开 / 查不到时都老实说，不编结果；结果只推送真实来源。
            from src.ai import web_research

            query = str(routine.get("action_text") or "").strip()
            if not query:
                raise RoutineError(422, "empty_action", "定时查询的例程要先写查询词")
            if not web_research.enabled():
                raise RoutineError(
                    422,
                    "research_disabled",
                    "联网检索没开：先在设置里打开「联网检索」，再开这条例程",
                )
            report = web_research.search(query, limit=6)
            if not report.ok or not report.sources:
                reason = (report.error or "检索没有返回任何来源").strip()
                text = f"定时查「{query}」：没查到结果（{reason}）。没编内容，等下次再查。"
                if not notify_chat(text):
                    raise RoutineError(500, "chat_notify_failed", "消息没能写进聊天（聊天记录不可用）")
                return text, "", []
            lines = [f"定时查「{query}」的结果（{len(report.sources)} 条，都有出处）："]
            artifacts: List[str] = []
            for item in report.sources[:6]:
                title = (item.title or item.url).strip()
                lines.append(f"· {title}（{item.site}）：{item.url}")
                artifacts.append(item.url)
            text = "\n".join(lines)
            if not notify_chat(text):
                raise RoutineError(500, "chat_notify_failed", "消息没能写进聊天（聊天记录不可用）")
            return text, "", artifacts
        raise RoutineError(422, "bad_action", f"不认识的例程动作：{kind!r}")


def generate_webhook_secret() -> str:
    return secrets.token_urlsafe(32)
