"""任务台账（Task Ledger）——SQLite 真持久化的任务/子步骤/执行记录。

背景
====
* ``/v1/goals*``（``src/gateway/ai_management.py``）把目标写成
  ``liuhao_goals.json``（热文件 + 归档 JSONL 分片）。它是**执行真源**：
  AgentRuntime / journal / 多进程文件锁 / 损坏隔离 / 中断收敛都绑在这份
  数据上，但"按状态查、按负责人查、分页、执行记录、人工接管"这些台账能力
  它给不了。
* ``src/api/tasks_api.py`` 只是 LEGACY 内存 scaffold（未被网关挂载）。

本模块提供**独立、可查询的台账真源**（SQLite，WAL）：

* 任务（Task）——一条 = 一个 Goal 的工作单（与 ``AgentRunState`` 的 goal 级
  生命周期一一对应）；
* 子步骤（Step）——执行内核 plan 里的每个 task（真实拆解结果，不造步骤）；
* 执行记录（Run）——状态迁移事件：created / started / finished / failed /
  cancelled / retried / takeover / status_change / migrated。

与 ``liuhao_goals.json`` 的关系（本次定案）
==========================================
**并存 + 写穿透 + 一次性幂等迁移**，本次不替换 goals 的底层存储：

1. goals JSON 仍是执行真源；台账是它的投影，不参与执行调度；
2. ``AIStateManager`` 在创建 / 进度快照 / 终态 / 重试 / 中止处调用
   :meth:`TaskLedger.sync_goal` 写穿透（失败只记日志，不打断执行）；
3. :meth:`TaskLedger.migrate_from_goals` 只读旧 JSON 与归档分片，按
   ``(goal_id, step_key)`` upsert，单事务、可重跑、失败回滚；旧文件一字不动，
   回滚 = 删导入行或删 DB 文件，因此**现网数据不会因为迁移而丢**。

七态与现有状态机的映射
======================
统一七态只做投影，不新增第三套状态机（现有 goal 级状态机是
``src/ai/agent_runtime.py::AgentRunState``）：

    待分配 ← created / unknown（且无负责人）
    待执行 ← planning / waiting / pending / planned（有负责人）
    执行中 ← executing / running / retrying
    待审核 ← verifying / reviewing
    已完成 ← completed / completed_unattributed
    失败   ← failed / interrupted / error
    已取消 ← cancelled / stopped / skipped

子步骤用执行内核的 ``TaskStatus`` 走同一张映射表。
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

from src.common.data_paths import resolve_store_path

logger = logging.getLogger(__name__)

LEDGER_FILENAME = "liuhao_tasks.db"
LEDGER_ENV_VAR = "LIUHAO_TASK_LEDGER_DB"
SCHEMA_VERSION = 1


class LedgerStatus(str, Enum):
    """统一七态（台账唯一状态机）。"""

    PENDING_ASSIGNMENT = "pending_assignment"  # 待分配
    PENDING = "pending"                        # 待执行
    RUNNING = "running"                        # 执行中
    REVIEWING = "reviewing"                    # 待审核
    COMPLETED = "completed"                    # 已完成
    FAILED = "failed"                          # 失败
    CANCELLED = "cancelled"                    # 已取消


ALL_STATUSES = tuple(item.value for item in LedgerStatus)
TERMINAL_STATUSES = frozenset({
    LedgerStatus.COMPLETED.value,
    LedgerStatus.FAILED.value,
    LedgerStatus.CANCELLED.value,
})
RETRYABLE_STATUSES = frozenset({LedgerStatus.FAILED.value})
TAKEOVER_STATUSES = frozenset({
    LedgerStatus.PENDING.value,
    LedgerStatus.PENDING_ASSIGNMENT.value,
    LedgerStatus.REVIEWING.value,
    LedgerStatus.FAILED.value,
})

#: goal 级状态（AgentRunState + 网关落盘态）→ 七态
_GOAL_STATE_MAP: Dict[str, LedgerStatus] = {
    "": LedgerStatus.PENDING,
    "created": LedgerStatus.PENDING_ASSIGNMENT,
    "unknown": LedgerStatus.PENDING_ASSIGNMENT,
    "planning": LedgerStatus.PENDING,
    "waiting": LedgerStatus.PENDING,
    "pending": LedgerStatus.PENDING,
    "planned": LedgerStatus.PENDING,
    "queued": LedgerStatus.PENDING,
    "executing": LedgerStatus.RUNNING,
    "running": LedgerStatus.RUNNING,
    "retrying": LedgerStatus.RUNNING,
    "verifying": LedgerStatus.REVIEWING,
    "reviewing": LedgerStatus.REVIEWING,
    "pending_review": LedgerStatus.REVIEWING,
    "completed": LedgerStatus.COMPLETED,
    "completed_unattributed": LedgerStatus.COMPLETED,
    "failed": LedgerStatus.FAILED,
    "interrupted": LedgerStatus.FAILED,
    "error": LedgerStatus.FAILED,
    "cancelled": LedgerStatus.CANCELLED,
    "stopped": LedgerStatus.CANCELLED,
    "skipped": LedgerStatus.CANCELLED,
}

#: 子步骤状态（内核 TaskStatus）→ 七态，与 goal 级共用同一张映射
_STEP_STATE_MAP: Dict[str, LedgerStatus] = dict(_GOAL_STATE_MAP)
_STEP_STATE_MAP.update({
    "draft": LedgerStatus.PENDING,
    "blocked": LedgerStatus.PENDING,
})


def map_goal_state(raw_state: Any) -> LedgerStatus:
    """goal 级状态 → 七态。未知状态按"待执行"如实降级，不猜测成功。"""
    key = str(raw_state or "").strip().lower()
    return _GOAL_STATE_MAP.get(key, LedgerStatus.PENDING)


def map_step_status(raw_state: Any, *, has_assignee: bool = True) -> LedgerStatus:
    """子步骤状态 → 七态。没有负责人且尚未开始的步骤落"待分配"。"""
    key = str(raw_state or "").strip().lower()
    mapped = _STEP_STATE_MAP.get(key, LedgerStatus.PENDING)
    if mapped is LedgerStatus.PENDING and not has_assignee:
        return LedgerStatus.PENDING_ASSIGNMENT
    return mapped


class LedgerError(Exception):
    """台账操作的稳定错误（API 层直接翻译成 HTTP）。"""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message

    def detail(self) -> Dict[str, str]:
        return {"code": self.code, "message": self.message}


# ── 时间与序列化 ─────────────────────────────────────────────


def _epoch(value: Any) -> Optional[float]:
    """把 epoch / 数字字符串 / ISO 8601 统一成 epoch 秒；认不出就返回 None。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if number > 0 else None
    text = str(value).strip()
    if not text:
        return None
    try:
        number = float(text)
        return number if number > 0 else None
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _iso(value: Any) -> Optional[str]:
    stamp = _epoch(value)
    if stamp is None:
        return None
    return datetime.fromtimestamp(stamp, tz=timezone.utc).isoformat()


def _json_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item.strip()]


def _json_dumps(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError, OverflowError):
        return "null"


def _json_loads(text: Any, fallback: Any) -> Any:
    if not text:
        return fallback
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return fallback


def _resolve_assignee(entry: Dict[str, Any]) -> Tuple[str, str, str]:
    """从 goal / step 记录里取负责人：``(type, id, name)``。

    口径与 ``AIStateManager._attach_employee_attribution`` 一致：
    点名派活优先 agent，其次员工名；没有归属就如实记 ``none``，
    绝不把"不知道"猜成默认管家。

    注意：员工稳定 id 目前没有随 goal 记录落盘，因此员工级负责人用名字
    同时填 ``id`` 与 ``name``（可查询、可展示，不伪造 id）。
    """
    employee = str(entry.get("assigned_employee") or "").strip()
    agent = str(entry.get("assigned_agent") or "").strip()
    if employee:
        return "employee", employee, employee
    if agent:
        return "agent", agent, ""
    names = [
        str(name).strip()
        for name in (entry.get("assigned_employees") or [])
        if str(name).strip()
    ]
    if names:
        unique = sorted(set(names))
        return "employee", unique[0], "、".join(unique)
    return "none", "", ""


def _resolve_task_assignee(
    entry: Dict[str, Any], steps: Sequence[Dict[str, Any]]
) -> Tuple[str, str, str]:
    """任务负责人：goal 级没写就从子步骤反推（真实执行者，不猜默认人）。"""
    resolved = _resolve_assignee(entry)
    if resolved[0] != "none":
        return resolved
    names: List[str] = []
    for step in steps:
        step_type, step_id, step_name = _resolve_assignee(step)
        if step_type == "none":
            continue
        names.append(step_name or step_id)
    unique = sorted({name for name in names if name})
    if not unique:
        return "none", "", ""
    return "employee", unique[0], "、".join(unique)


def default_ledger_path() -> str:
    repo_root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    return resolve_store_path(
        LEDGER_FILENAME,
        os.path.join(repo_root, f".{LEDGER_FILENAME}"),
        env_var=LEDGER_ENV_VAR,
        workspace=True,
    )


_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    task_id           TEXT PRIMARY KEY,
    goal_id           TEXT NOT NULL,
    title             TEXT NOT NULL DEFAULT '',
    description       TEXT NOT NULL DEFAULT '',
    status            TEXT NOT NULL,
    status_source     TEXT NOT NULL DEFAULT '',
    assignee_type     TEXT NOT NULL DEFAULT 'none',
    assignee_id       TEXT NOT NULL DEFAULT '',
    assignee_name     TEXT NOT NULL DEFAULT '',
    owner_account_id  TEXT NOT NULL DEFAULT '',
    project_id        TEXT,
    artifacts_json    TEXT NOT NULL DEFAULT '[]',
    error             TEXT,
    technical_detail  TEXT,
    created_at        REAL,
    started_at        REAL,
    completed_at      REAL,
    updated_at        REAL NOT NULL,
    attempt_count     INTEGER NOT NULL DEFAULT 0,
    archived          INTEGER NOT NULL DEFAULT 0,
    human_hold        INTEGER NOT NULL DEFAULT 0,
    source            TEXT NOT NULL DEFAULT 'goal',
    migration_id      TEXT,
    last_seen_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_assignee ON tasks(assignee_type, assignee_id);
CREATE INDEX IF NOT EXISTS idx_tasks_goal ON tasks(goal_id);
CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at);
CREATE INDEX IF NOT EXISTS idx_tasks_owner ON tasks(owner_account_id);

CREATE TABLE IF NOT EXISTS task_steps (
    step_id           TEXT PRIMARY KEY,
    task_id           TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
    step_key          TEXT NOT NULL,
    seq               INTEGER NOT NULL DEFAULT 0,
    name              TEXT NOT NULL DEFAULT '',
    description       TEXT NOT NULL DEFAULT '',
    status            TEXT NOT NULL,
    status_source     TEXT NOT NULL DEFAULT '',
    capability_id     TEXT,
    assignee_type     TEXT NOT NULL DEFAULT 'none',
    assignee_id       TEXT NOT NULL DEFAULT '',
    assignee_name     TEXT NOT NULL DEFAULT '',
    dependencies_json TEXT NOT NULL DEFAULT '[]',
    inputs_json       TEXT NOT NULL DEFAULT '{}',
    artifacts_json    TEXT NOT NULL DEFAULT '[]',
    error             TEXT,
    technical_detail  TEXT,
    started_at        REAL,
    completed_at      REAL,
    attempt_count     INTEGER NOT NULL DEFAULT 0,
    human_hold        INTEGER NOT NULL DEFAULT 0,
    last_seen_at      REAL NOT NULL,
    UNIQUE(task_id, step_key)
);
CREATE INDEX IF NOT EXISTS idx_steps_task ON task_steps(task_id, seq);
CREATE INDEX IF NOT EXISTS idx_steps_status ON task_steps(status);

CREATE TABLE IF NOT EXISTS task_runs (
    run_id       TEXT PRIMARY KEY,
    task_id      TEXT NOT NULL,
    step_id      TEXT,
    step_key     TEXT,
    event        TEXT NOT NULL,
    status_from  TEXT,
    status_to    TEXT,
    actor_type   TEXT NOT NULL DEFAULT 'system',
    actor_id     TEXT NOT NULL DEFAULT '',
    reason       TEXT,
    error        TEXT,
    attempt      INTEGER NOT NULL DEFAULT 0,
    detail_json  TEXT NOT NULL DEFAULT '{}',
    at           REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runs_task ON task_runs(task_id, at);

CREATE TABLE IF NOT EXISTS migration_runs (
    migration_id     TEXT PRIMARY KEY,
    source_path      TEXT NOT NULL,
    source_sha256    TEXT NOT NULL,
    started_at       REAL NOT NULL,
    finished_at      REAL,
    status           TEXT NOT NULL,
    entries_scanned  INTEGER NOT NULL DEFAULT 0,
    tasks_imported   INTEGER NOT NULL DEFAULT 0,
    steps_imported   INTEGER NOT NULL DEFAULT 0,
    runs_imported    INTEGER NOT NULL DEFAULT 0,
    skipped          INTEGER NOT NULL DEFAULT 0,
    error            TEXT
);
CREATE INDEX IF NOT EXISTS idx_migration_hash ON migration_runs(source_sha256, status);
"""


class TaskLedger:
    """SQLite 任务台账。线程安全（单连接 + RLock + WAL）。"""

    def __init__(self, db_path: Optional[str] = None) -> None:
        self._db_path = str(db_path or default_ledger_path())
        self._lock = threading.RLock()
        self._conn: Optional[sqlite3.Connection] = None
        # 写穿透失败计数（自检接口用）：只统计"台账缺一条"，不影响执行。
        self._write_failures = 0
        self._last_failure_at: Optional[float] = None
        self._last_failure_reason = ""
        self._ensure_schema()

    # ── 连接 / 事务 ─────────────────────────────────────────

    @property
    def path(self) -> str:
        return self._db_path

    def _connection(self) -> sqlite3.Connection:
        if self._conn is None:
            directory = os.path.dirname(os.path.abspath(self._db_path))
            if directory:
                os.makedirs(directory, exist_ok=True)
            conn = sqlite3.connect(
                self._db_path, timeout=10.0, check_same_thread=False
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=10000")
            self._conn = conn
        return self._conn

    def _ensure_schema(self) -> None:
        with self._lock:
            conn = self._connection()
            conn.executescript(_SCHEMA)
            conn.execute(
                "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(SCHEMA_VERSION),),
            )
            conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            conn.commit()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            conn = self._connection()
            try:
                conn.execute("BEGIN IMMEDIATE")
                yield conn
                conn.commit()
            except Exception:
                try:
                    conn.rollback()
                except sqlite3.Error:  # pragma: no cover - 连接已坏时尽力而为
                    pass
                raise

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                finally:
                    self._conn = None

    # ── 写穿透自检 / 补偿 ───────────────────────────────────

    def note_write_failure(self, exc: BaseException) -> None:
        """记录一次写穿透失败（调用方保证不影响热路径）。"""
        with self._lock:
            self._write_failures += 1
            self._last_failure_at = time.time()
            self._last_failure_reason = f"{type(exc).__name__}: {exc}"

    def lag_report(
        self, goals_path: str, *, default_owner: str = ""
    ) -> Dict[str, Any]:
        """台账落后自检：热文件里的目标在台账中缺多少 / 状态偏差多少。

        只读热文件（活跃目标都在这里），不解析归档分片，保证自检接口轻量。
        归档目标是否已导入，看 ``summary().recent_migrations``。
        """
        hot_path, _full, _index = self._source_paths(goals_path)
        hot_entries: List[Dict[str, Any]] = []
        if os.path.exists(hot_path):
            try:
                data = self._read_json_file(hot_path)
            except (OSError, ValueError) as exc:
                raise LedgerError(
                    500, "lag_source_unreadable",
                    f"目标记录文件无法读取：{hot_path}（{exc}）",
                ) from exc
            if isinstance(data, dict):
                for goal_id, entry in data.items():
                    if isinstance(entry, dict):
                        item = dict(entry)
                        item.setdefault("goal_id", str(goal_id))
                        hot_entries.append(item)
        with self._lock:
            rows = self._connection().execute(
                "SELECT task_id, status, human_hold, owner_account_id FROM tasks"
            ).fetchall()
            failures = self._write_failures
            last_failure_at = self._last_failure_at
            last_failure_reason = self._last_failure_reason
        by_id = {str(row["task_id"]): row for row in rows}
        missing: List[str] = []
        stale: List[Dict[str, Any]] = []
        for entry in hot_entries:
            goal_id = str(entry.get("goal_id") or "").strip()
            if not goal_id:
                continue
            row = by_id.get(goal_id)
            if row is None:
                missing.append(goal_id)
                continue
            if row["human_hold"]:
                continue
            expected = map_goal_state(entry.get("state"))
            if str(row["status"]) != expected.value:
                stale.append({
                    "task_id": goal_id,
                    "source_state": str(entry.get("state") or ""),
                    "expected": expected.value,
                    "ledger": str(row["status"]),
                })
        return {
            "hot_goals": len(hot_entries),
            "ledger_tasks": len(by_id),
            "missing_in_ledger": missing,
            "stale": stale,
            "lag_total": len(missing) + len(stale),
            "write_failures": failures,
            "last_failure_at": _iso(last_failure_at),
            "last_failure_reason": last_failure_reason,
            "default_owner": default_owner,
        }

    def repair_from_source(
        self, goals_path: str, *, default_owner: str = ""
    ) -> Dict[str, Any]:
        """用热文件补齐/纠正台账（写穿透失败后的补偿；幂等）。"""
        hot_path, _full, _index = self._source_paths(goals_path)
        if not os.path.exists(hot_path):
            return {"status": "no_source", "goals": 0, "tasks": 0, "steps": 0, "runs": 0}
        try:
            data = self._read_json_file(hot_path)
        except (OSError, ValueError) as exc:
            raise LedgerError(
                500, "repair_source_unreadable",
                f"目标记录文件无法读取：{hot_path}（{exc}）",
            ) from exc
        counts = {"goals": 0, "tasks": 0, "steps": 0, "runs": 0}
        if isinstance(data, dict):
            for goal_id, entry in data.items():
                if not isinstance(entry, dict):
                    continue
                item = dict(entry)
                item.setdefault("goal_id", str(goal_id))
                projected = self.sync_goal(item, default_owner=default_owner)
                counts["goals"] += 1
                counts["tasks"] += projected.get("tasks", 0)
                counts["steps"] += projected.get("steps", 0)
                counts["runs"] += projected.get("runs", 0)
        return {"status": "repaired", **counts}

    # ── 投影（goals → ledger） ──────────────────────────────

    def sync_goal(
        self, entry: Dict[str, Any], *, default_owner: str = ""
    ) -> Dict[str, int]:
        """把一个 goal 记录投影进台账（幂等；按状态迁移补执行记录）。

        幂等口径：同一条记录重复投影不会新增 run；只有状态/负责人/错误/
        产物确实变化时才写一条 ``status_change`` 或对应的开始/结束事件。
        人工接管（``human_hold=1``）的任务不被自动投影覆盖，除非真实执行
        重新开始（running）或已经到达真实终态。
        """
        if not isinstance(entry, dict):
            return {"tasks": 0, "steps": 0, "runs": 0}
        goal_id = str(entry.get("goal_id") or "").strip()
        if not goal_id:
            return {"tasks": 0, "steps": 0, "runs": 0}
        raw_state = str(entry.get("state") or "").strip().lower()
        mapped = map_goal_state(raw_state)
        steps = [task for task in (entry.get("tasks") or []) if isinstance(task, dict)]
        assignee_type, assignee_id, assignee_name = _resolve_task_assignee(entry, steps)
        if mapped is LedgerStatus.PENDING_ASSIGNMENT and assignee_type != "none":
            mapped = LedgerStatus.PENDING
        started_at = _epoch(entry.get("started_at"))
        completed_at = _epoch(entry.get("completed_at"))
        if started_at is None:
            started_at = min(
                (value for value in (_epoch(s.get("started_at")) for s in steps)
                 if value is not None),
                default=None,
            )
        if completed_at is None and mapped.value in TERMINAL_STATUSES:
            completed_at = max(
                (value for value in (_epoch(s.get("completed_at")) for s in steps)
                 if value is not None),
                default=None,
            )
        now = time.time()
        counts = {"tasks": 0, "steps": 0, "runs": 0}
        with self._tx() as conn:
            previous = conn.execute(
                "SELECT * FROM tasks WHERE task_id = ?", (goal_id,)
            ).fetchone()
            hold = bool(previous["human_hold"]) if previous is not None else False
            proj_status = mapped
            proj_assignee = (assignee_type, assignee_id, assignee_name)
            if hold and mapped.value not in (
                LedgerStatus.RUNNING.value,
                LedgerStatus.COMPLETED.value,
                LedgerStatus.CANCELLED.value,
            ):
                proj_status = LedgerStatus.PENDING
                proj_assignee = (
                    str(previous["assignee_type"]),
                    str(previous["assignee_id"]),
                    str(previous["assignee_name"]),
                )
            payload = {
                "goal_id": goal_id,
                "title": str(entry.get("natural_language") or "") or goal_id,
                "description": str(entry.get("description") or ""),
                "status": proj_status.value,
                "status_source": raw_state or "unknown",
                "assignee_type": proj_assignee[0],
                "assignee_id": proj_assignee[1],
                "assignee_name": proj_assignee[2],
                "owner_account_id": str(entry.get("owner_account_id") or "")
                or str(default_owner or ""),
                "project_id": entry.get("project_id"),
                "artifacts_json": _json_dumps(_json_list(entry.get("artifacts"))),
                "error": entry.get("error"),
                "technical_detail": entry.get("technical_detail"),
                "created_at": _epoch(entry.get("created_at")),
                "started_at": started_at,
                "completed_at": completed_at if proj_status.value in TERMINAL_STATUSES else None,
                "updated_at": now,
                "archived": 1 if entry.get("archived") else 0,
                "last_seen_at": now,
            }
            if previous is None:
                conn.execute(
                    "INSERT INTO tasks(task_id, goal_id, title, description, status, "
                    "status_source, assignee_type, assignee_id, assignee_name, "
                    "owner_account_id, project_id, artifacts_json, error, technical_detail, "
                    "created_at, started_at, completed_at, updated_at, attempt_count, "
                    "archived, human_hold, source, migration_id, last_seen_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        goal_id, payload["goal_id"], payload["title"],
                        payload["description"], payload["status"],
                        payload["status_source"], payload["assignee_type"],
                        payload["assignee_id"], payload["assignee_name"],
                        payload["owner_account_id"], payload["project_id"],
                        payload["artifacts_json"], payload["error"],
                        payload["technical_detail"], payload["created_at"],
                        payload["started_at"], payload["completed_at"],
                        payload["updated_at"],
                        1 if proj_status is LedgerStatus.RUNNING else 0,
                        payload["archived"], 0, "goal", None, payload["last_seen_at"],
                    ),
                )
                counts["tasks"] += 1
                counts["runs"] += self._append_run(
                    conn,
                    task_id=goal_id,
                    step_id=None,
                    step_key=None,
                    event="created",
                    status_from=None,
                    status_to=proj_status.value,
                    actor_type="system",
                    actor_id="",
                    attempt=0,
                    detail={"source": "goal", "raw_state": raw_state},
                )
            else:
                changed = self._task_changed(previous, payload)
                if changed:
                    attempt = int(previous["attempt_count"] or 0)
                    if (
                        proj_status is LedgerStatus.RUNNING
                        and str(previous["status"]) != LedgerStatus.RUNNING.value
                    ):
                        attempt += 1
                    conn.execute(
                        "UPDATE tasks SET goal_id=?, title=?, description=?, status=?, "
                        "status_source=?, assignee_type=?, assignee_id=?, assignee_name=?, "
                        "owner_account_id=?, project_id=?, artifacts_json=?, error=?, "
                        "technical_detail=?, created_at=?, started_at=?, completed_at=?, "
                        "updated_at=?, archived=?, attempt_count=?, last_seen_at=? "
                        "WHERE task_id=?",
                        (
                            payload["goal_id"], payload["title"], payload["description"],
                            payload["status"], payload["status_source"],
                            payload["assignee_type"], payload["assignee_id"],
                            payload["assignee_name"], payload["owner_account_id"],
                            payload["project_id"], payload["artifacts_json"],
                            payload["error"], payload["technical_detail"],
                            payload["created_at"], payload["started_at"],
                            payload["completed_at"], payload["updated_at"],
                            payload["archived"], attempt, payload["last_seen_at"],
                            goal_id,
                        ),
                    )
                    counts["tasks"] += 1
                    if str(previous["status"]) != proj_status.value:
                        counts["runs"] += self._transition_runs(
                            conn,
                            task_id=goal_id,
                            step_id=None,
                            step_key=None,
                            status_from=str(previous["status"]),
                            status_to=proj_status.value,
                            error=payload["error"],
                            attempt=attempt,
                        )
            counts["steps"], step_runs = self._sync_steps(
                conn, task_id=goal_id, steps=steps, now=now
            )
            counts["runs"] += step_runs
        return counts

    @staticmethod
    def _task_changed(previous: sqlite3.Row, payload: Dict[str, Any]) -> bool:
        for column in (
            "title", "description", "status", "status_source", "assignee_type",
            "assignee_id", "assignee_name", "owner_account_id", "artifacts_json",
            "error", "technical_detail", "project_id", "archived", "started_at",
            "completed_at",
        ):
            old = previous[column]
            new = payload.get(column)
            if column == "completed_at":
                if old is None and new is None:
                    continue
            if old != new:
                return True
        return False

    def _sync_steps(
        self,
        conn: sqlite3.Connection,
        *,
        task_id: str,
        steps: List[Dict[str, Any]],
        now: float,
    ) -> Tuple[int, int]:
        changed_steps = 0
        runs = 0
        for index, step in enumerate(steps):
            step_key = str(step.get("id") or "").strip()
            if not step_key:
                # 没有稳定 id 的步骤无法幂等投影：记一条告警，不编造 id。
                logger.warning(
                    "task_ledger: goal %s step at index %d has no id; skipped",
                    task_id, index,
                )
                continue
            status_raw = str(step.get("status") or "").strip().lower()
            assignee_type, assignee_id, assignee_name = _resolve_assignee(step)
            mapped = map_step_status(
                status_raw, has_assignee=assignee_type != "none"
            )
            payload = {
                "task_id": task_id,
                "step_key": step_key,
                "seq": index,
                "name": str(step.get("name") or "") or step_key,
                "description": str(step.get("description") or ""),
                "status": mapped.value,
                "status_source": status_raw or "unknown",
                "capability_id": step.get("capability_id"),
                "assignee_type": assignee_type,
                "assignee_id": assignee_id,
                "assignee_name": assignee_name,
                "dependencies_json": _json_dumps(list(step.get("dependencies") or [])),
                "inputs_json": _json_dumps(step.get("inputs") or {}),
                "artifacts_json": _json_dumps(_json_list(step.get("artifacts"))),
                "error": step.get("error"),
                "technical_detail": step.get("technical_detail"),
                "started_at": _epoch(step.get("started_at")),
                "completed_at": _epoch(step.get("completed_at")),
                "last_seen_at": now,
            }
            previous = conn.execute(
                "SELECT * FROM task_steps WHERE task_id = ? AND step_key = ?",
                (task_id, step_key),
            ).fetchone()
            if previous is None:
                step_id = uuid.uuid4().hex
                conn.execute(
                    "INSERT INTO task_steps(step_id, task_id, step_key, seq, name, "
                    "description, status, status_source, capability_id, assignee_type, "
                    "assignee_id, assignee_name, dependencies_json, inputs_json, "
                    "artifacts_json, error, technical_detail, started_at, completed_at, "
                    "attempt_count, human_hold, last_seen_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        step_id, task_id, step_key, index, payload["name"],
                        payload["description"], payload["status"],
                        payload["status_source"], payload["capability_id"],
                        payload["assignee_type"], payload["assignee_id"],
                        payload["assignee_name"], payload["dependencies_json"],
                        payload["inputs_json"], payload["artifacts_json"],
                        payload["error"], payload["technical_detail"],
                        payload["started_at"], payload["completed_at"],
                        1 if mapped is LedgerStatus.RUNNING else 0, 0, now,
                    ),
                )
                changed_steps += 1
                runs += self._append_run(
                    conn, task_id=task_id, step_id=step_id, step_key=step_key,
                    event="created", status_from=None, status_to=mapped.value,
                    actor_type="system", actor_id="", attempt=0,
                    detail={"source": "goal"},
                )
                continue
            hold = bool(previous["human_hold"])
            if hold and mapped.value not in (
                LedgerStatus.RUNNING.value,
                LedgerStatus.COMPLETED.value,
                LedgerStatus.CANCELLED.value,
            ):
                payload["status"] = LedgerStatus.PENDING.value
                payload["assignee_type"] = str(previous["assignee_type"])
                payload["assignee_id"] = str(previous["assignee_id"])
                payload["assignee_name"] = str(previous["assignee_name"])
            if not self._step_changed(previous, payload):
                continue
            attempt = int(previous["attempt_count"] or 0)
            if (
                payload["status"] == LedgerStatus.RUNNING.value
                and str(previous["status"]) != LedgerStatus.RUNNING.value
            ):
                attempt += 1
            conn.execute(
                "UPDATE task_steps SET seq=?, name=?, description=?, status=?, "
                "status_source=?, capability_id=?, assignee_type=?, assignee_id=?, "
                "assignee_name=?, dependencies_json=?, inputs_json=?, artifacts_json=?, "
                "error=?, technical_detail=?, started_at=?, completed_at=?, "
                "attempt_count=?, last_seen_at=? WHERE step_id=?",
                (
                    payload["seq"], payload["name"], payload["description"],
                    payload["status"], payload["status_source"],
                    payload["capability_id"], payload["assignee_type"],
                    payload["assignee_id"], payload["assignee_name"],
                    payload["dependencies_json"], payload["inputs_json"],
                    payload["artifacts_json"], payload["error"],
                    payload["technical_detail"], payload["started_at"],
                    payload["completed_at"], attempt, payload["last_seen_at"],
                    previous["step_id"],
                ),
            )
            changed_steps += 1
            if str(previous["status"]) != payload["status"]:
                runs += self._transition_runs(
                    conn, task_id=task_id, step_id=str(previous["step_id"]),
                    step_key=step_key, status_from=str(previous["status"]),
                    status_to=payload["status"], error=payload["error"],
                    attempt=attempt,
                )
        return changed_steps, runs

    @staticmethod
    def _step_changed(previous: sqlite3.Row, payload: Dict[str, Any]) -> bool:
        for column in (
            "seq", "name", "description", "status", "status_source",
            "capability_id", "assignee_type", "assignee_id", "assignee_name",
            "dependencies_json", "inputs_json", "artifacts_json", "error",
            "technical_detail", "started_at", "completed_at",
        ):
            if previous[column] != payload.get(column):
                return True
        return False

    def _transition_runs(
        self,
        conn: sqlite3.Connection,
        *,
        task_id: str,
        step_id: Optional[str],
        step_key: Optional[str],
        status_from: str,
        status_to: str,
        error: Any,
        attempt: int,
    ) -> int:
        runs = 0
        common = dict(
            task_id=task_id, step_id=step_id, step_key=step_key,
            status_from=status_from, status_to=status_to,
            actor_type="system", actor_id="", error=error, attempt=attempt,
        )
        if status_to == LedgerStatus.RUNNING.value:
            runs += self._append_run(conn, event="started", **common)
        if status_to in (LedgerStatus.COMPLETED.value, LedgerStatus.FAILED.value,
                         LedgerStatus.CANCELLED.value):
            event = {
                LedgerStatus.COMPLETED.value: "finished",
                LedgerStatus.FAILED.value: "failed",
                LedgerStatus.CANCELLED.value: "cancelled",
            }[status_to]
            runs += self._append_run(conn, event=event, **common)
        runs += self._append_run(
            conn, event="status_change",
            **{**common, "detail": {"from": status_from, "to": status_to}},
        )
        return runs

    def _append_run(
        self,
        conn: sqlite3.Connection,
        *,
        task_id: str,
        step_id: Optional[str],
        step_key: Optional[str],
        event: str,
        status_from: Optional[str],
        status_to: Optional[str],
        actor_type: str = "system",
        actor_id: str = "",
        reason: Optional[str] = None,
        error: Any = None,
        attempt: int = 0,
        detail: Optional[Dict[str, Any]] = None,
    ) -> int:
        conn.execute(
            "INSERT INTO task_runs(run_id, task_id, step_id, step_key, event, "
            "status_from, status_to, actor_type, actor_id, reason, error, attempt, "
            "detail_json, at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                uuid.uuid4().hex, task_id, step_id, step_key, event,
                status_from, status_to, actor_type, actor_id, reason,
                None if error is None else str(error), attempt,
                _json_dumps(detail or {}), time.time(),
            ),
        )
        return 1

    # ── 查询 ────────────────────────────────────────────────

    def _task_row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "task_id": row["task_id"],
            "goal_id": row["goal_id"],
            "title": row["title"],
            "description": row["description"],
            "status": row["status"],
            "status_source": row["status_source"],
            "assignee": {
                "type": row["assignee_type"],
                "id": row["assignee_id"],
                "name": row["assignee_name"],
            },
            "owner_account_id": row["owner_account_id"],
            "project_id": row["project_id"],
            "artifacts": _json_loads(row["artifacts_json"], []),
            "error": row["error"],
            "technical_detail": row["technical_detail"],
            "created_at": _iso(row["created_at"]),
            "started_at": _iso(row["started_at"]),
            "completed_at": _iso(row["completed_at"]),
            "updated_at": _iso(row["updated_at"]),
            "created_at_epoch": row["created_at"],
            "completed_at_epoch": row["completed_at"],
            "attempt_count": int(row["attempt_count"] or 0),
            "archived": bool(row["archived"]),
            "human_hold": bool(row["human_hold"]),
            "source": row["source"],
        }

    def _step_row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "step_id": row["step_id"],
            "task_id": row["task_id"],
            "step_key": row["step_key"],
            "seq": int(row["seq"] or 0),
            "name": row["name"],
            "description": row["description"],
            "status": row["status"],
            "status_source": row["status_source"],
            "capability_id": row["capability_id"],
            "assignee": {
                "type": row["assignee_type"],
                "id": row["assignee_id"],
                "name": row["assignee_name"],
            },
            "dependencies": _json_loads(row["dependencies_json"], []),
            "inputs": _json_loads(row["inputs_json"], {}),
            "artifacts": _json_loads(row["artifacts_json"], []),
            "error": row["error"],
            "technical_detail": row["technical_detail"],
            "started_at": _iso(row["started_at"]),
            "completed_at": _iso(row["completed_at"]),
            "attempt_count": int(row["attempt_count"] or 0),
            "human_hold": bool(row["human_hold"]),
        }

    def _run_row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "run_id": row["run_id"],
            "task_id": row["task_id"],
            "step_id": row["step_id"],
            "step_key": row["step_key"],
            "event": row["event"],
            "status_from": row["status_from"],
            "status_to": row["status_to"],
            "actor": {"type": row["actor_type"], "id": row["actor_id"]},
            "reason": row["reason"],
            "error": row["error"],
            "attempt": int(row["attempt"] or 0),
            "detail": _json_loads(row["detail_json"], {}),
            "at": _iso(row["at"]),
            "at_epoch": row["at"],
        }

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._connection().execute(
                "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
        return self._task_row_to_dict(row) if row is not None else None

    def list_tasks(
        self,
        *,
        status: Optional[str] = None,
        assignee_type: Optional[str] = None,
        assignee_id: Optional[str] = None,
        assignee_name: Optional[str] = None,
        goal_id: Optional[str] = None,
        owners: Optional[Sequence[str]] = None,
        limit: int = 20,
        offset: int = 0,
        sort_by: str = "created_at",
        sort_order: str = "desc",
    ) -> Dict[str, Any]:
        where: List[str] = []
        params: List[Any] = []
        if status:
            where.append("status = ?")
            params.append(status)
        if assignee_type:
            where.append("assignee_type = ?")
            params.append(assignee_type)
        if assignee_id:
            where.append("assignee_id = ?")
            params.append(assignee_id)
        if assignee_name:
            where.append("assignee_name = ?")
            params.append(assignee_name)
        if goal_id:
            where.append("goal_id = ?")
            params.append(goal_id)
        if owners is not None:
            owners = list(dict.fromkeys(owners))
            if not owners:
                return {"total": 0, "limit": limit, "offset": offset, "items": []}
            where.append(f"owner_account_id IN ({','.join('?' for _ in owners)})")
            params.extend(owners)
        clause = f" WHERE {' AND '.join(where)}" if where else ""
        sort_column = sort_by if sort_by in {
            "created_at", "updated_at", "completed_at", "status", "title",
        } else "created_at"
        direction = "ASC" if str(sort_order).lower() == "asc" else "DESC"
        # 旧数据没有 completed_at 时读侧退回 created_at，不伪造完成时间。
        order_expr = (
            "COALESCE(completed_at, created_at)"
            if sort_column == "completed_at" else sort_column
        )
        with self._lock:
            conn = self._connection()
            total = int(
                conn.execute(f"SELECT COUNT(*) AS c FROM tasks{clause}", params)
                .fetchone()["c"]
            )
            rows = conn.execute(
                f"SELECT * FROM tasks{clause} ORDER BY {order_expr} {direction}, "
                f"task_id {direction} LIMIT ? OFFSET ?",
                params + [int(limit), int(offset)],
            ).fetchall()
        return {
            "total": total,
            "limit": int(limit),
            "offset": int(offset),
            "items": [self._task_row_to_dict(row) for row in rows],
        }

    def list_steps(self, task_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._connection().execute(
                "SELECT * FROM task_steps WHERE task_id = ? ORDER BY seq ASC",
                (task_id,),
            ).fetchall()
        return [self._step_row_to_dict(row) for row in rows]

    def list_runs(self, task_id: str, *, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._connection().execute(
                "SELECT * FROM task_runs WHERE task_id = ? ORDER BY at DESC LIMIT ?",
                (task_id, int(limit)),
            ).fetchall()
        return [self._run_row_to_dict(row) for row in rows]

    def task_detail(self, task_id: str, *, run_limit: int = 50) -> Optional[Dict[str, Any]]:
        task = self.get_task(task_id)
        if task is None:
            return None
        task["steps"] = self.list_steps(task_id)
        task["runs"] = self.list_runs(task_id, limit=run_limit)
        return task

    # ── 人工动作 ────────────────────────────────────────────

    def set_assignee(
        self, task_id: str, *, assignee_type: str, assignee_id: str,
        assignee_name: str, actor: str = "",
    ) -> Dict[str, Any]:
        with self._tx() as conn:
            row = conn.execute(
                "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            if row is None:
                raise LedgerError(404, "task_not_found", f"任务不存在：{task_id}")
            conn.execute(
                "UPDATE tasks SET assignee_type=?, assignee_id=?, assignee_name=?, "
                "updated_at=? WHERE task_id=?",
                (assignee_type, assignee_id, assignee_name, time.time(), task_id),
            )
            self._append_run(
                conn, task_id=task_id, step_id=None, step_key=None,
                event="assignee_changed", status_from=str(row["status"]),
                status_to=str(row["status"]), actor_type="human",
                actor_id=actor, detail={
                    "from": {
                        "type": row["assignee_type"], "id": row["assignee_id"],
                        "name": row["assignee_name"],
                    },
                    "to": {
                        "type": assignee_type, "id": assignee_id,
                        "name": assignee_name,
                    },
                },
            )
        return self.get_task(task_id) or {}

    def takeover(
        self, task_id: str, *, principal: str, reason: str = ""
    ) -> Dict[str, Any]:
        """人工接管：把任务从自动执行者转给人类操作员（不伪造执行）。

        * 只有非终态/失败/待审核任务可以接管；执行中 409（先中止再接管），
          已完成 409（没有可接管的活），已取消 409；
        * 接管后状态落"待执行"，负责人变成人类，并给每个未完成子步骤打上
          人工保持（``human_hold``），后续 goal 投影不会把接管改回去；
        * 真实执行重新开始（running）或任务到达真实终态时，人工保持解除。
        """
        now = time.time()
        with self._tx() as conn:
            row = conn.execute(
                "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            if row is None:
                raise LedgerError(404, "task_not_found", f"任务不存在：{task_id}")
            current = str(row["status"])
            if current == LedgerStatus.RUNNING.value:
                raise LedgerError(
                    409, "task_running",
                    "任务仍在执行中，请先中止执行再人工接管",
                )
            if current == LedgerStatus.COMPLETED.value:
                raise LedgerError(409, "task_completed", "任务已完成，无需人工接管")
            if current == LedgerStatus.CANCELLED.value:
                raise LedgerError(409, "task_cancelled", "任务已取消，不能人工接管")
            conn.execute(
                "UPDATE tasks SET status=?, assignee_type='human', assignee_id=?, "
                "assignee_name=?, human_hold=1, updated_at=?, last_seen_at=? "
                "WHERE task_id=?",
                (LedgerStatus.PENDING.value, principal, principal, now, now, task_id),
            )
            conn.execute(
                "UPDATE task_steps SET status=?, status_source='human_takeover', "
                "assignee_type='human', assignee_id=?, assignee_name=?, human_hold=1, "
                "last_seen_at=? WHERE task_id=? AND status NOT IN (?,?)",
                (
                    LedgerStatus.PENDING.value, principal, principal, now, task_id,
                    LedgerStatus.COMPLETED.value, LedgerStatus.CANCELLED.value,
                ),
            )
            self._append_run(
                conn, task_id=task_id, step_id=None, step_key=None,
                event="takeover", status_from=current,
                status_to=LedgerStatus.PENDING.value, actor_type="human",
                actor_id=principal, reason=reason or None,
                detail={"previous_assignee": {
                    "type": row["assignee_type"], "id": row["assignee_id"],
                    "name": row["assignee_name"],
                }},
            )
        return self.get_task(task_id) or {}

    def record_retry_request(
        self, task_id: str, *, step_key: Optional[str], principal: str,
        reason: str = "",
    ) -> None:
        with self._tx() as conn:
            row = conn.execute(
                "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            if row is None:
                raise LedgerError(404, "task_not_found", f"任务不存在：{task_id}")
            self._append_run(
                conn, task_id=task_id, step_id=None, step_key=step_key,
                event="retry_requested", status_from=str(row["status"]),
                status_to=None, actor_type="human", actor_id=principal,
                reason=reason or None,
            )

    def clear_human_hold(self, task_id: str) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE tasks SET human_hold=0, updated_at=? WHERE task_id=?",
                (time.time(), task_id),
            )
            conn.execute(
                "UPDATE task_steps SET human_hold=0 WHERE task_id=?", (task_id,)
            )

    # ── 迁移 ────────────────────────────────────────────────

    @staticmethod
    def _read_json_file(path: str) -> Any:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)

    @staticmethod
    def _iter_jsonl(path: str) -> Iterator[Dict[str, Any]]:
        opener = gzip.open if path.endswith(".gz") else open
        try:
            with opener(path, "rt", encoding="utf-8") as handle:
                for line in handle:
                    text = line.strip()
                    if not text:
                        continue
                    try:
                        payload = json.loads(text)
                    except ValueError:
                        logger.warning("task_ledger: unreadable archive line in %s", path)
                        continue
                    if isinstance(payload, dict):
                        yield payload
        except OSError as exc:
            logger.warning("task_ledger: cannot read archive shard %s: %s", path, exc)

    @staticmethod
    def _source_paths(
        goals_path: str,
    ) -> Tuple[str, List[str], List[str]]:
        """把源拆成（热文件, 归档全量分片, 归档索引分片），顺序固定。"""
        directory = os.path.dirname(os.path.abspath(goals_path))
        stem = os.path.basename(goals_path)
        full: List[str] = []
        index: List[str] = []
        if os.path.isdir(directory):
            for name in sorted(os.listdir(directory)):
                if not name.startswith(stem + ".archive-"):
                    continue
                if not (name.endswith(".jsonl") or name.endswith(".jsonl.gz")):
                    continue
                path = os.path.join(directory, name)
                if ".archive-index-" in name:
                    index.append(path)
                else:
                    full.append(path)
        return os.path.abspath(goals_path), full, index

    def _source_fingerprint(self, goals_path: str) -> Tuple[str, Tuple[str, List[str], List[str]]]:
        """源指纹：热文件做内容哈希，归档分片只做 stat 清单哈希。

        归档是只追加的分片，逐字节哈希几十 MB 会让每次启动都读满磁盘；
        用 ``名字:大小:mtime`` 判定"归档有没有变过"足够可靠，且热文件
        （真正会被改写的部分）仍然逐字节哈希。
        """
        paths = self._source_paths(goals_path)
        hot_path, full_files, index_files = paths
        hasher = hashlib.sha256()
        if os.path.exists(hot_path):
            with open(hot_path, "rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 256), b""):
                    hasher.update(chunk)
        for path in [*full_files, *index_files]:
            try:
                stat = os.stat(path)
            except OSError:
                continue
            hasher.update(
                f"{os.path.basename(path)}:{stat.st_size}:{stat.st_mtime_ns}".encode()
            )
        return hasher.hexdigest(), paths

    def _read_entries(
        self, goals_path: str, paths: Optional[Tuple[str, List[str], List[str]]] = None
    ) -> List[Dict[str, Any]]:
        """读热文件 + 归档分片，按"热 > 全量 > 索引"去重。

        索引摘要没有 ``tasks``，只用来补全量分片与热文件里都不存在的旧目标；
        这类目标导入后台账里是一张"没有子步骤"的任务卡（如实，不编造步骤）。
        """
        hot_path, full_files, index_files = paths or self._source_paths(goals_path)
        entries: List[Dict[str, Any]] = []
        seen: set = set()
        if os.path.exists(hot_path):
            try:
                data = self._read_json_file(hot_path)
            except (OSError, ValueError) as exc:
                raise LedgerError(
                    500, "migration_source_unreadable",
                    f"目标记录文件无法读取：{hot_path}（{exc}）",
                ) from exc
            if isinstance(data, dict):
                for goal_id, entry in data.items():
                    if isinstance(entry, dict):
                        item = dict(entry)
                        item.setdefault("goal_id", str(goal_id))
                        if item.get("goal_id"):
                            entries.append(item)
                            seen.add(str(item["goal_id"]))
        for path in [*full_files, *index_files]:
            for entry in self._iter_jsonl(path):
                goal_id = str(entry.get("goal_id") or "").strip()
                if goal_id and goal_id in seen:
                    continue
                # 没有 goal_id 的坏行也保留，交给导入阶段计入 skipped 原因，
                # 否则"跳过了多少条"会变成静默丢失。
                entries.append(entry)
                if goal_id:
                    seen.add(goal_id)
        return entries

    def _source_entries(
        self, goals_path: str
    ) -> Tuple[List[Dict[str, Any]], str]:
        """读全部源条目 + 源指纹（一致性核对与演练用）。"""
        fingerprint, paths = self._source_fingerprint(goals_path)
        return self._read_entries(goals_path, paths), fingerprint

    def _import_entries(
        self,
        conn: sqlite3.Connection,
        entries: Iterable[Dict[str, Any]],
        migration_id: str,
        *,
        default_owner: str = "",
    ) -> Dict[str, Any]:
        counts: Dict[str, Any] = {
            "tasks_imported": 0, "steps_imported": 0,
            "runs_imported": 0, "skipped": 0,
            "skipped_reasons": {},
        }

        def _skip(reason: str) -> None:
            counts["skipped"] += 1
            bucket = counts["skipped_reasons"]
            bucket[reason] = int(bucket.get(reason) or 0) + 1

        now = time.time()
        for entry in entries:
            goal_id = str(entry.get("goal_id") or "").strip()
            if not goal_id:
                _skip("missing_goal_id")
                continue
            existing = conn.execute(
                "SELECT task_id FROM tasks WHERE task_id = ?", (goal_id,)
            ).fetchone()
            if existing is not None:
                _skip("already_in_ledger")
                continue
            raw_state = str(entry.get("state") or "").strip().lower()
            mapped = map_goal_state(raw_state)
            steps = [t for t in (entry.get("tasks") or []) if isinstance(t, dict)]
            assignee_type, assignee_id, assignee_name = _resolve_task_assignee(
                entry, steps
            )
            if mapped is LedgerStatus.PENDING_ASSIGNMENT and assignee_type != "none":
                mapped = LedgerStatus.PENDING
            started = _epoch(entry.get("started_at"))
            if started is None:
                started = min(
                    (v for v in (_epoch(s.get("started_at")) for s in steps)
                     if v is not None),
                    default=None,
                )
            completed = _epoch(entry.get("completed_at"))
            if completed is None and mapped.value in TERMINAL_STATUSES:
                completed = max(
                    (v for v in (_epoch(s.get("completed_at")) for s in steps)
                     if v is not None),
                    default=None,
                )
            conn.execute(
                "INSERT INTO tasks(task_id, goal_id, title, description, status, "
                "status_source, assignee_type, assignee_id, assignee_name, "
                "owner_account_id, project_id, artifacts_json, error, technical_detail, "
                "created_at, started_at, completed_at, updated_at, attempt_count, "
                "archived, human_hold, source, migration_id, last_seen_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    goal_id, goal_id,
                    str(entry.get("natural_language") or "") or goal_id,
                    str(entry.get("description") or ""),
                    mapped.value, raw_state or "unknown", assignee_type,
                    assignee_id, assignee_name,
                    str(entry.get("owner_account_id") or "")
                    or str(default_owner or ""),
                    entry.get("project_id"),
                    _json_dumps(_json_list(entry.get("artifacts"))),
                    entry.get("error"), entry.get("technical_detail"),
                    _epoch(entry.get("created_at")), started, completed, now,
                    0, 1 if entry.get("archived") else 0, 0,
                    "migration", migration_id, now,
                ),
            )
            counts["tasks_imported"] += 1
            counts["runs_imported"] += self._append_run(
                conn, task_id=goal_id, step_id=None, step_key=None,
                event="migrated", status_from=None, status_to=mapped.value,
                actor_type="system", actor_id="",
                detail={"migration_id": migration_id, "raw_state": raw_state},
            )
            for index, step in enumerate(steps):
                step_key = str(step.get("id") or "").strip()
                if not step_key:
                    _skip("step_missing_id")
                    continue
                status_raw = str(step.get("status") or "").strip().lower()
                s_type, s_id, s_name = _resolve_assignee(step)
                s_status = map_step_status(
                    status_raw, has_assignee=s_type != "none"
                )
                step_id = uuid.uuid4().hex
                conn.execute(
                    "INSERT INTO task_steps(step_id, task_id, step_key, seq, name, "
                    "description, status, status_source, capability_id, assignee_type, "
                    "assignee_id, assignee_name, dependencies_json, inputs_json, "
                    "artifacts_json, error, technical_detail, started_at, completed_at, "
                    "attempt_count, human_hold, last_seen_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        step_id, goal_id, step_key, index,
                        str(step.get("name") or "") or step_key,
                        str(step.get("description") or ""),
                        s_status.value, status_raw or "unknown",
                        step.get("capability_id"), s_type, s_id, s_name,
                        _json_dumps(list(step.get("dependencies") or [])),
                        _json_dumps(step.get("inputs") or {}),
                        _json_dumps(_json_list(step.get("artifacts"))),
                        step.get("error"), step.get("technical_detail"),
                        _epoch(step.get("started_at")),
                        _epoch(step.get("completed_at")),
                        1 if s_status is LedgerStatus.RUNNING else 0, 0, now,
                    ),
                )
                counts["steps_imported"] += 1
                counts["runs_imported"] += self._append_run(
                    conn, task_id=goal_id, step_id=step_id, step_key=step_key,
                    event="migrated", status_from=None, status_to=s_status.value,
                    actor_type="system", actor_id="",
                    detail={"migration_id": migration_id},
                )
        return counts

    def migrate_from_goals(
        self, goals_path: str, *, force: bool = False, default_owner: str = "",
    ) -> Dict[str, Any]:
        """把 ``liuhao_goals.json``（含归档分片）导入台账。可重复执行。

        * 同一份源指纹 + 已有成功迁移 → 直接返回 ``skipped``（不重复导入，
          也不重新解析归档分片）；
        * ``force=True`` 时重跑：已有任务卡跳过（不覆盖活数据），只补新目标；
        * 整个导入在单个 ``BEGIN IMMEDIATE`` 事务内完成，失败即回滚；
        * 源文件只读，绝不改写；``skipped`` 会附带原因分布，不只报成功数。
        """
        source_hash, paths = self._source_fingerprint(goals_path)
        source_path = os.path.abspath(goals_path)
        with self._lock:
            previous = self._connection().execute(
                "SELECT * FROM migration_runs m WHERE m.source_path = ? AND "
                "m.source_sha256 = ? AND m.status = 'completed' AND EXISTS ("
                "  SELECT 1 FROM tasks t WHERE t.migration_id = m.migration_id"
                ") ORDER BY m.finished_at DESC LIMIT 1",
                (source_path, source_hash),
            ).fetchone()
        if previous is not None and not force:
            return {
                "status": "skipped",
                "reason": "source unchanged since last completed migration",
                "migration_id": previous["migration_id"],
                "source_path": source_path,
                "source_sha256": source_hash,
                "entries": int(previous["entries_scanned"]),
                "tasks_imported": int(previous["tasks_imported"]),
                "steps_imported": int(previous["steps_imported"]),
                "runs_imported": int(previous["runs_imported"]),
                "skipped": int(previous["skipped"]),
                "skipped_reasons": {},
            }
        entries = self._read_entries(goals_path, paths)
        migration_id = uuid.uuid4().hex
        started_at = time.time()
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO migration_runs(migration_id, source_path, source_sha256, "
                "started_at, entries_scanned, status) VALUES (?,?,?,?,?,'running')",
                (migration_id, source_path, source_hash, started_at, len(entries)),
            )
        try:
            with self._tx() as conn:
                counts = self._import_entries(
                    conn, entries, migration_id, default_owner=default_owner
                )
                conn.execute(
                    "UPDATE migration_runs SET status='completed', finished_at=?, "
                    "entries_scanned=?, tasks_imported=?, steps_imported=?, "
                    "runs_imported=?, skipped=? WHERE migration_id=?",
                    (
                        time.time(), len(entries), counts["tasks_imported"],
                        counts["steps_imported"], counts["runs_imported"],
                        counts["skipped"], migration_id,
                    ),
                )
        except Exception as exc:
            logger.error("task_ledger: migration %s failed: %s", migration_id, exc)
            with self._tx() as conn:
                conn.execute(
                    "UPDATE migration_runs SET status='failed', finished_at=?, "
                    "error=? WHERE migration_id=?",
                    (time.time(), f"{type(exc).__name__}: {exc}", migration_id),
                )
            if isinstance(exc, LedgerError):
                raise
            raise LedgerError(
                500, "migration_failed", f"任务台账迁移失败，已回滚：{exc}"
            ) from exc
        return {
            "status": "completed",
            "migration_id": migration_id,
            "source_path": source_path,
            "source_sha256": source_hash,
            "entries": len(entries),
            **counts,
        }

    def rollback_migration(self, migration_id: str) -> Dict[str, Any]:
        """回滚一次迁移：删掉该次导入的任务/步骤/记录（源文件不受影响）。"""
        with self._tx() as conn:
            row = conn.execute(
                "SELECT * FROM migration_runs WHERE migration_id = ?",
                (migration_id,),
            ).fetchone()
            if row is None:
                raise LedgerError(
                    404, "migration_not_found", f"迁移记录不存在：{migration_id}"
                )
            task_ids = [
                str(item["task_id"])
                for item in conn.execute(
                    "SELECT task_id FROM tasks WHERE migration_id = ?",
                    (migration_id,),
                ).fetchall()
            ]
            for task_id in task_ids:
                conn.execute("DELETE FROM task_runs WHERE task_id = ?", (task_id,))
                conn.execute("DELETE FROM task_steps WHERE task_id = ?", (task_id,))
                conn.execute("DELETE FROM tasks WHERE task_id = ?", (task_id,))
            conn.execute(
                "UPDATE migration_runs SET status='rolled_back', finished_at=? "
                "WHERE migration_id=?",
                (time.time(), migration_id),
            )
        return {"migration_id": migration_id, "tasks_deleted": len(task_ids)}

    # ── 一致性核对 / 概览 ───────────────────────────────────

    def consistency(self, goals_path: str) -> Dict[str, Any]:
        """迁移前后一致性：源里的目标 ↔ 台账里的任务。

        只比对"状态投影"与"目标是否存在"。人工接管（``human_hold``）造成
        的状态差异属于有意的本地覆盖，单独计数，不算丢失/不一致。
        """
        entries, _hash = self._source_entries(goals_path)
        with self._lock:
            rows = self._connection().execute(
                "SELECT task_id, status, human_hold, source FROM tasks"
            ).fetchall()
        by_id = {str(row["task_id"]): row for row in rows}
        missing: List[str] = []
        mismatched: List[Dict[str, Any]] = []
        manual_override = 0
        source_ids = set()
        for entry in entries:
            goal_id = str(entry.get("goal_id") or "").strip()
            if not goal_id:
                continue
            source_ids.add(goal_id)
            row = by_id.get(goal_id)
            if row is None:
                missing.append(goal_id)
                continue
            expected = map_goal_state(entry.get("state"))
            if row["human_hold"]:
                manual_override += 1
                continue
            if str(row["status"]) != expected.value:
                mismatched.append({
                    "goal_id": goal_id,
                    "source_state": str(entry.get("state") or ""),
                    "expected": expected.value,
                    "ledger": str(row["status"]),
                })
        extra = sorted(set(by_id) - source_ids)
        return {
            "source_goals": len(source_ids),
            "ledger_tasks": len(by_id),
            "missing_in_ledger": missing,
            "state_mismatched": mismatched,
            "manual_override": manual_override,
            "ledger_only": extra,
            "consistent": not missing and not mismatched,
        }

    def summary(self) -> Dict[str, Any]:
        with self._lock:
            conn = self._connection()
            status_rows = conn.execute(
                "SELECT status, COUNT(*) AS c FROM tasks GROUP BY status"
            ).fetchall()
            totals = conn.execute(
                "SELECT (SELECT COUNT(*) FROM tasks) AS tasks, "
                "(SELECT COUNT(*) FROM task_steps) AS steps, "
                "(SELECT COUNT(*) FROM task_runs) AS runs"
            ).fetchone()
            migrations = conn.execute(
                "SELECT migration_id, source_path, source_sha256, started_at, "
                "finished_at, status, entries_scanned, tasks_imported, "
                "steps_imported, runs_imported, skipped, error FROM migration_runs "
                "ORDER BY started_at DESC LIMIT 5"
            ).fetchall()
        return {
            "db_path": self._db_path,
            "schema_version": SCHEMA_VERSION,
            "counts": {
                "tasks": int(totals["tasks"]),
                "steps": int(totals["steps"]),
                "runs": int(totals["runs"]),
            },
            "by_status": {str(row["status"]): int(row["c"]) for row in status_rows},
            "recent_migrations": [
                {
                    "migration_id": row["migration_id"],
                    "source_path": row["source_path"],
                    "source_sha256": row["source_sha256"],
                    "started_at": _iso(row["started_at"]),
                    "finished_at": _iso(row["finished_at"]),
                    "status": row["status"],
                    "entries_scanned": int(row["entries_scanned"]),
                    "tasks_imported": int(row["tasks_imported"]),
                    "steps_imported": int(row["steps_imported"]),
                    "runs_imported": int(row["runs_imported"]),
                    "skipped": int(row["skipped"]),
                    "error": row["error"],
                }
                for row in migrations
            ],
        }


_INSTANCES: Dict[str, TaskLedger] = {}
_INSTANCES_LOCK = threading.Lock()


def get_task_ledger(path: Optional[str] = None) -> TaskLedger:
    """按路径复用台账实例（默认路径由环境/数据根决定）。"""
    resolved = os.path.abspath(str(path or default_ledger_path()))
    with _INSTANCES_LOCK:
        ledger = _INSTANCES.get(resolved)
        if ledger is None:
            ledger = TaskLedger(resolved)
            _INSTANCES[resolved] = ledger
        return ledger


def reset_task_ledger_instances() -> None:
    """测试用：关闭并清空所有实例（不删库文件）。"""
    with _INSTANCES_LOCK:
        for ledger in _INSTANCES.values():
            ledger.close()
        _INSTANCES.clear()
