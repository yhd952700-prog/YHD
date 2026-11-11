"""工作流与审批的本地持久化（SQLite WAL，单连接 + RLock）。

为什么单独一个库
================
流程定义、运行实例、步骤记录、审批待办是"编排层"自己的真相，和任务台账
（``liuhao_tasks.db``，由 ``AIStateManager`` 从 goal 投影）职责不同：台账是
执行层结果的只读投影，这里记录的是"谁在什么时候让哪条流程走到了哪一步"。
两者通过 goal_id 关联，不互相写。

时间统一存 epoch 秒（REAL），对外展示由 API 层转 ISO。
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, Dict, Iterator, List, Optional, Sequence

from src.common.data_paths import resolve_store_path

logger = logging.getLogger(__name__)

WORKFLOW_FILENAME = "liuhao_workflows.db"
WORKFLOW_ENV_VAR = "LIUHAO_WORKFLOW_DB"
SCHEMA_VERSION = 1

RUN_STATUSES = (
    "pending",
    "running",
    "waiting_approval",
    "completed",
    "failed",
    "cancelled",
)
TERMINAL_RUN_STATUSES = frozenset({"completed", "failed", "cancelled"})

STEP_STATUSES = (
    "pending",
    "running",
    "waiting_approval",
    "completed",
    "failed",
    "rejected",
    "cancelled",
)

APPROVAL_STATUSES = ("pending", "approved", "rejected", "expired")
TERMINAL_APPROVAL_STATUSES = frozenset({"approved", "rejected", "expired"})


class StoreError(Exception):
    """存储层稳定错误（API 层翻译成 HTTP）。"""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message

    def detail(self) -> Dict[str, str]:
        return {"code": self.code, "message": self.message}


def default_workflow_path() -> str:
    repo_root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    return resolve_store_path(
        WORKFLOW_FILENAME,
        os.path.join(repo_root, f".{WORKFLOW_FILENAME}"),
        env_var=WORKFLOW_ENV_VAR,
        workspace=True,
    )


def _new_id(prefix: str, length: int = 12) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:length]}"


def _dumps(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return json.dumps(str(value), ensure_ascii=False)


def _loads(text: Any, fallback: Any) -> Any:
    if text is None or text == "":
        return fallback
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return fallback


def _row(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
    return dict(row) if row is not None else None


def _bool(value: Any) -> bool:
    return bool(int(value or 0))


_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS workflow_defs (
    def_id            TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    description       TEXT NOT NULL DEFAULT '',
    enabled           INTEGER NOT NULL DEFAULT 1,
    version           INTEGER NOT NULL DEFAULT 1,
    definition_json   TEXT NOT NULL,
    owner_account_id  TEXT NOT NULL DEFAULT '',
    created_by        TEXT NOT NULL DEFAULT '',
    created_at        REAL NOT NULL,
    updated_at        REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_wf_defs_owner ON workflow_defs(owner_account_id);

CREATE TABLE IF NOT EXISTS workflow_runs (
    run_id             TEXT PRIMARY KEY,
    def_id             TEXT NOT NULL,
    def_version        INTEGER NOT NULL DEFAULT 1,
    definition_json    TEXT NOT NULL,
    name               TEXT NOT NULL DEFAULT '',
    status             TEXT NOT NULL,
    trigger_type       TEXT NOT NULL DEFAULT 'manual',
    trigger_payload_json TEXT NOT NULL DEFAULT '{}',
    inputs_json        TEXT NOT NULL DEFAULT '{}',
    variables_json     TEXT NOT NULL DEFAULT '{}',
    owner_account_id   TEXT NOT NULL DEFAULT '',
    submitted_by       TEXT NOT NULL DEFAULT '',
    current_node_id    TEXT,
    outcome            TEXT NOT NULL DEFAULT '',
    error              TEXT,
    error_code         TEXT,
    created_at         REAL NOT NULL,
    started_at         REAL,
    finished_at        REAL,
    updated_at         REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_wf_runs_def ON workflow_runs(def_id, created_at);
CREATE INDEX IF NOT EXISTS idx_wf_runs_status ON workflow_runs(status);
CREATE INDEX IF NOT EXISTS idx_wf_runs_owner ON workflow_runs(owner_account_id);

CREATE TABLE IF NOT EXISTS workflow_steps (
    step_id      TEXT PRIMARY KEY,
    run_id       TEXT NOT NULL,
    node_id      TEXT NOT NULL,
    node_type    TEXT NOT NULL,
    name         TEXT NOT NULL DEFAULT '',
    seq          INTEGER NOT NULL DEFAULT 0,
    status       TEXT NOT NULL,
    attempt      INTEGER NOT NULL DEFAULT 1,
    inputs_json  TEXT NOT NULL DEFAULT '{}',
    outputs_json TEXT NOT NULL DEFAULT '{}',
    error        TEXT,
    error_code   TEXT,
    duration_ms  REAL,
    started_at   REAL,
    finished_at  REAL,
    updated_at   REAL NOT NULL,
    UNIQUE(run_id, node_id)
);
CREATE INDEX IF NOT EXISTS idx_wf_steps_run ON workflow_steps(run_id, seq);

CREATE TABLE IF NOT EXISTS workflow_approvals (
    approval_id      TEXT PRIMARY KEY,
    run_id           TEXT NOT NULL,
    step_id          TEXT NOT NULL,
    node_id          TEXT NOT NULL,
    def_id           TEXT NOT NULL,
    title            TEXT NOT NULL DEFAULT '',
    operation        TEXT NOT NULL DEFAULT '',
    reason           TEXT NOT NULL DEFAULT '',
    approvers_json   TEXT NOT NULL DEFAULT '[]',
    status           TEXT NOT NULL DEFAULT 'pending',
    submitted_by     TEXT NOT NULL DEFAULT '',
    owner_account_id TEXT NOT NULL DEFAULT '',
    decided_by       TEXT NOT NULL DEFAULT '',
    comment          TEXT NOT NULL DEFAULT '',
    created_at       REAL NOT NULL,
    expires_at       REAL,
    decided_at       REAL,
    updated_at       REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_wf_approvals_status ON workflow_approvals(status, created_at);
CREATE INDEX IF NOT EXISTS idx_wf_approvals_run ON workflow_approvals(run_id);

CREATE TABLE IF NOT EXISTS workflow_triggers (
    trigger_key   TEXT PRIMARY KEY,
    def_id        TEXT NOT NULL,
    trigger_type  TEXT NOT NULL,
    config_json   TEXT NOT NULL DEFAULT '{}',
    enabled       INTEGER NOT NULL DEFAULT 1,
    last_fired_at REAL,
    next_due_at   REAL,
    updated_at    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_wf_triggers_due ON workflow_triggers(enabled, next_due_at);
"""


def _next_daily_due(at: str, now: float) -> float:
    """把 ``HH:MM`` 换算成"下一次"的 epoch（按本机时区）。"""
    try:
        hour, minute = (int(part) for part in str(at).split(":", 1))
    except (TypeError, ValueError):
        return now + 86400
    current = datetime.fromtimestamp(now)
    candidate = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate.timestamp() <= now:
        candidate = candidate + timedelta(days=1)
    return candidate.timestamp()


class WorkflowStore:
    """流程定义 / 运行实例 / 步骤 / 审批 / 触发器 的唯一存储。"""

    def __init__(self, db_path: Optional[str] = None) -> None:
        self._db_path = str(db_path or default_workflow_path())
        self._lock = threading.RLock()
        self._conn: Optional[sqlite3.Connection] = None
        self._ensure_schema()

    # ── 连接 ────────────────────────────────────────────────

    @property
    def path(self) -> str:
        return self._db_path

    def _connection(self) -> sqlite3.Connection:
        if self._conn is None:
            directory = os.path.dirname(os.path.abspath(self._db_path))
            if directory:
                os.makedirs(directory, exist_ok=True)
            conn = sqlite3.connect(self._db_path, timeout=10.0, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
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
                except sqlite3.Error:  # pragma: no cover - 连接已坏
                    pass
                raise

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                finally:
                    self._conn = None

    # ── 定义 ────────────────────────────────────────────────

    def create_definition(
        self,
        definition: Dict[str, Any],
        *,
        owner_account_id: str = "",
        created_by: str = "",
    ) -> Dict[str, Any]:
        def_id = _new_id("wf", 10)
        now = time.time()
        payload = dict(definition)
        payload["owner_account_id"] = owner_account_id
        payload["created_by"] = created_by
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO workflow_defs(def_id, name, description, enabled, version,"
                " definition_json, owner_account_id, created_by, created_at, updated_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    def_id,
                    payload.get("name", ""),
                    payload.get("description", ""),
                    1 if payload.get("enabled", True) else 0,
                    1,
                    _dumps(payload),
                    owner_account_id,
                    created_by,
                    now,
                    now,
                ),
            )
        self.sync_triggers(def_id, payload)
        return self.get_definition(def_id) or {}

    def update_definition(
        self, def_id: str, definition: Dict[str, Any]
    ) -> Dict[str, Any]:
        now = time.time()
        with self._tx() as conn:
            current = conn.execute(
                "SELECT * FROM workflow_defs WHERE def_id = ?", (def_id,)
            ).fetchone()
            if current is None:
                raise StoreError(404, "workflow_not_found", f"流程不存在：{def_id}")
            payload = dict(definition)
            payload["owner_account_id"] = current["owner_account_id"]
            payload["created_by"] = current["created_by"]
            version = int(current["version"]) + 1
            conn.execute(
                "UPDATE workflow_defs SET name=?, description=?, enabled=?, version=?,"
                " definition_json=?, updated_at=? WHERE def_id=?",
                (
                    payload.get("name", ""),
                    payload.get("description", ""),
                    1 if payload.get("enabled", True) else 0,
                    version,
                    _dumps(payload),
                    now,
                    def_id,
                ),
            )
        self.sync_triggers(def_id, payload)
        return self.get_definition(def_id) or {}

    def get_definition(self, def_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._connection().execute(
                "SELECT * FROM workflow_defs WHERE def_id = ?", (def_id,)
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["enabled"] = _bool(item["enabled"])
        item["definition"] = _loads(item.pop("definition_json"), {})
        return item

    def list_definitions(
        self,
        *,
        owners: Optional[Sequence[str]] = None,
        include_disabled: bool = True,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        clauses: List[str] = []
        params: List[Any] = []
        if owners is not None:
            if not owners:
                return []
            placeholders = ",".join("?" for _ in owners)
            clauses.append(f"owner_account_id IN ({placeholders})")
            params.extend(owners)
        if not include_disabled:
            clauses.append("enabled = 1")
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        params.extend([limit, offset])
        with self._lock:
            rows = self._connection().execute(
                "SELECT * FROM workflow_defs" + where + " ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                params,
            ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["enabled"] = _bool(item["enabled"])
            item["definition"] = _loads(item.pop("definition_json"), {})
            items.append(item)
        return items

    def set_definition_enabled(self, def_id: str, enabled: bool) -> Dict[str, Any]:
        now = time.time()
        with self._tx() as conn:
            row = conn.execute(
                "SELECT definition_json FROM workflow_defs WHERE def_id = ?", (def_id,)
            ).fetchone()
            if row is None:
                raise StoreError(404, "workflow_not_found", f"流程不存在：{def_id}")
            payload = _loads(row["definition_json"], {})
            payload["enabled"] = bool(enabled)
            conn.execute(
                "UPDATE workflow_defs SET enabled=?, definition_json=?, updated_at=? WHERE def_id=?",
                (1 if enabled else 0, _dumps(payload), now, def_id),
            )
        return self.get_definition(def_id) or {}

    # ── 运行实例 ────────────────────────────────────────────

    def create_run(
        self,
        definition: Dict[str, Any],
        *,
        def_id: str,
        def_version: int,
        trigger_type: str,
        trigger_payload: Dict[str, Any],
        inputs: Dict[str, Any],
        owner_account_id: str,
        submitted_by: str,
    ) -> Dict[str, Any]:
        run_id = _new_id("run", 12)
        now = time.time()
        variables = {"input": dict(inputs or {}), "steps": {}, "definition": {"name": definition.get("name", "")}}
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO workflow_runs(run_id, def_id, def_version, definition_json, name,"
                " status, trigger_type, trigger_payload_json, inputs_json, variables_json,"
                " owner_account_id, submitted_by, current_node_id, outcome, error, error_code,"
                " created_at, started_at, finished_at, updated_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    def_id,
                    int(def_version),
                    _dumps(definition),
                    definition.get("name", ""),
                    "pending",
                    trigger_type,
                    _dumps(trigger_payload or {}),
                    _dumps(inputs or {}),
                    _dumps(variables),
                    owner_account_id,
                    submitted_by,
                    None,
                    "",
                    None,
                    None,
                    now,
                    None,
                    None,
                    now,
                ),
            )
        return self.get_run(run_id) or {}

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._connection().execute(
                "SELECT * FROM workflow_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["definition"] = _loads(item.pop("definition_json"), {})
        item["inputs"] = _loads(item.pop("inputs_json"), {})
        item["variables"] = _loads(item.pop("variables_json"), {})
        item["trigger_payload"] = _loads(item.pop("trigger_payload_json"), {})
        return item

    def list_runs(
        self,
        *,
        owners: Optional[Sequence[str]] = None,
        def_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        clauses: List[str] = []
        params: List[Any] = []
        if owners is not None:
            if not owners:
                return []
            placeholders = ",".join("?" for _ in owners)
            clauses.append(f"owner_account_id IN ({placeholders})")
            params.extend(owners)
        if def_id:
            clauses.append("def_id = ?")
            params.append(def_id)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        params.extend([limit, offset])
        with self._lock:
            rows = self._connection().execute(
                "SELECT * FROM workflow_runs" + where + " ORDER BY created_at DESC LIMIT ? OFFSET ?",
                params,
            ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["definition"] = _loads(item.pop("definition_json"), {})
            item["inputs"] = _loads(item.pop("inputs_json"), {})
            item["variables"] = _loads(item.pop("variables_json"), {})
            item["trigger_payload"] = _loads(item.pop("trigger_payload_json"), {})
            items.append(item)
        return items

    def mark_run_running(self, run_id: str, *, current_node_id: Optional[str] = None) -> None:
        now = time.time()
        with self._tx() as conn:
            conn.execute(
                "UPDATE workflow_runs SET status='running', started_at=COALESCE(started_at, ?),"
                " current_node_id=?, updated_at=? WHERE run_id=?",
                (now, current_node_id, now, run_id),
            )

    def set_run_node(self, run_id: str, node_id: Optional[str]) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE workflow_runs SET current_node_id=?, updated_at=? WHERE run_id=?",
                (node_id, time.time(), run_id),
            )

    def update_run_variables(self, run_id: str, variables: Dict[str, Any]) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE workflow_runs SET variables_json=?, updated_at=? WHERE run_id=?",
                (_dumps(variables), time.time(), run_id),
            )

    def set_run_waiting_approval(self, run_id: str, node_id: str) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE workflow_runs SET status='waiting_approval', current_node_id=?,"
                " updated_at=? WHERE run_id=?",
                (node_id, time.time(), run_id),
            )

    def finish_run(
        self,
        run_id: str,
        *,
        status: str,
        outcome: str = "",
        error: Optional[str] = None,
        error_code: Optional[str] = None,
    ) -> Dict[str, Any]:
        if status not in RUN_STATUSES:
            raise StoreError(400, "invalid_status", f"未知运行状态：{status}")
        now = time.time()
        with self._tx() as conn:
            conn.execute(
                "UPDATE workflow_runs SET status=?, outcome=?, error=?, error_code=?,"
                " finished_at=?, updated_at=? WHERE run_id=?",
                (status, outcome, error, error_code, now, now, run_id),
            )
        return self.get_run(run_id) or {}

    # ── 步骤 ────────────────────────────────────────────────

    def begin_step(
        self,
        run_id: str,
        *,
        node: Dict[str, Any],
        seq: int,
        inputs: Dict[str, Any],
        attempt: int = 1,
    ) -> Dict[str, Any]:
        now = time.time()
        step_id = _new_id("st", 12)
        with self._tx() as conn:
            existing = conn.execute(
                "SELECT step_id FROM workflow_steps WHERE run_id=? AND node_id=?",
                (run_id, node["id"]),
            ).fetchone()
            if existing is not None:
                step_id = str(existing["step_id"])
                conn.execute(
                    "UPDATE workflow_steps SET status='running', attempt=?, inputs_json=?,"
                    " outputs_json='{}', error=NULL, error_code=NULL, started_at=?,"
                    " finished_at=NULL, duration_ms=NULL, updated_at=?, name=?, node_type=?"
                    " WHERE step_id=?",
                    (
                        attempt,
                        _dumps(inputs),
                        now,
                        now,
                        node.get("name", ""),
                        node.get("type", ""),
                        step_id,
                    ),
                )
            else:
                conn.execute(
                    "INSERT INTO workflow_steps(step_id, run_id, node_id, node_type, name,"
                    " seq, status, attempt, inputs_json, outputs_json, started_at, updated_at)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        step_id,
                        run_id,
                        node["id"],
                        node.get("type", ""),
                        node.get("name", ""),
                        seq,
                        "running",
                        attempt,
                        _dumps(inputs),
                        "{}",
                        now,
                        now,
                    ),
                )
        return self.get_step(step_id) or {}

    def finish_step(
        self,
        step_id: str,
        *,
        status: str,
        outputs: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
        error_code: Optional[str] = None,
    ) -> Dict[str, Any]:
        if status not in STEP_STATUSES:
            raise StoreError(400, "invalid_status", f"未知步骤状态：{status}")
        now = time.time()
        with self._tx() as conn:
            row = conn.execute(
                "SELECT started_at FROM workflow_steps WHERE step_id=?", (step_id,)
            ).fetchone()
            if row is None:
                raise StoreError(404, "step_not_found", f"步骤不存在：{step_id}")
            started_at = row["started_at"]
            duration_ms = (
                max(0.0, (now - float(started_at)) * 1000.0)
                if started_at is not None
                else None
            )
            conn.execute(
                "UPDATE workflow_steps SET status=?, outputs_json=?, error=?, error_code=?,"
                " finished_at=?, duration_ms=?, updated_at=? WHERE step_id=?",
                (
                    status,
                    _dumps(outputs or {}),
                    error,
                    error_code,
                    now,
                    duration_ms,
                    now,
                    step_id,
                ),
            )
        return self.get_step(step_id) or {}

    def mark_step_waiting(self, step_id: str) -> Dict[str, Any]:
        with self._tx() as conn:
            conn.execute(
                "UPDATE workflow_steps SET status='waiting_approval', updated_at=? WHERE step_id=?",
                (time.time(), step_id),
            )
        return self.get_step(step_id) or {}

    def get_step(self, step_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._connection().execute(
                "SELECT * FROM workflow_steps WHERE step_id=?", (step_id,)
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["inputs"] = _loads(item.pop("inputs_json"), {})
        item["outputs"] = _loads(item.pop("outputs_json"), {})
        return item

    def get_step_by_node(self, run_id: str, node_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._connection().execute(
                "SELECT * FROM workflow_steps WHERE run_id=? AND node_id=?",
                (run_id, node_id),
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["inputs"] = _loads(item.pop("inputs_json"), {})
        item["outputs"] = _loads(item.pop("outputs_json"), {})
        return item

    def list_steps(self, run_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._connection().execute(
                "SELECT * FROM workflow_steps WHERE run_id=? ORDER BY seq ASC, started_at ASC",
                (run_id,),
            ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["inputs"] = _loads(item.pop("inputs_json"), {})
            item["outputs"] = _loads(item.pop("outputs_json"), {})
            items.append(item)
        return items

    # ── 审批 ────────────────────────────────────────────────

    def create_approval(
        self,
        *,
        run_id: str,
        step_id: str,
        node_id: str,
        def_id: str,
        title: str,
        operation: str,
        reason: str,
        approvers: Sequence[str],
        submitted_by: str,
        owner_account_id: str,
        timeout_seconds: int,
    ) -> Dict[str, Any]:
        approval_id = _new_id("ap", 12)
        now = time.time()
        expires_at = now + int(timeout_seconds) if timeout_seconds else None
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO workflow_approvals(approval_id, run_id, step_id, node_id, def_id,"
                " title, operation, reason, approvers_json, status, submitted_by,"
                " owner_account_id, created_at, expires_at, updated_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,'pending',?,?,?,?,?)",
                (
                    approval_id,
                    run_id,
                    step_id,
                    node_id,
                    def_id,
                    title,
                    operation,
                    reason,
                    _dumps(list(approvers)),
                    submitted_by,
                    owner_account_id,
                    now,
                    expires_at,
                    now,
                ),
            )
        return self.get_approval(approval_id) or {}

    def get_approval(self, approval_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._connection().execute(
                "SELECT * FROM workflow_approvals WHERE approval_id=?", (approval_id,)
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["approvers"] = _loads(item.pop("approvers_json"), [])
        return item

    def list_approvals(
        self,
        *,
        status: Optional[str] = None,
        owners: Optional[Sequence[str]] = None,
        approver: Optional[str] = None,
        run_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        clauses: List[str] = []
        params: List[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if owners is not None:
            if not owners:
                return []
            placeholders = ",".join("?" for _ in owners)
            clauses.append(f"owner_account_id IN ({placeholders})")
            params.extend(owners)
        if run_id:
            clauses.append("run_id = ?")
            params.append(run_id)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        params.extend([limit, offset])
        with self._lock:
            rows = self._connection().execute(
                "SELECT * FROM workflow_approvals" + where
                + " ORDER BY created_at DESC LIMIT ? OFFSET ?",
                params,
            ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["approvers"] = _loads(item.pop("approvers_json"), [])
            if approver and item["approvers"] and approver not in item["approvers"]:
                continue
            items.append(item)
        return items

    def decide_approval(
        self,
        approval_id: str,
        *,
        decision: str,
        decided_by: str,
        comment: str = "",
    ) -> Dict[str, Any]:
        """原子地把待办置为通过/拒绝；已决定或已过期一律拒绝（幂等安全）。"""
        if decision not in ("approved", "rejected"):
            raise StoreError(400, "invalid_decision", "审批结论只能是 approved 或 rejected")
        now = time.time()
        with self._tx() as conn:
            row = conn.execute(
                "SELECT * FROM workflow_approvals WHERE approval_id=?", (approval_id,)
            ).fetchone()
            if row is None:
                raise StoreError(404, "approval_not_found", f"审批不存在：{approval_id}")
            if row["status"] != "pending":
                raise StoreError(
                    409,
                    "approval_already_decided",
                    f"该审批已是 {row['status']}，不能重复处理",
                )
            expires_at = row["expires_at"]
            if expires_at is not None and float(expires_at) <= now:
                conn.execute(
                    "UPDATE workflow_approvals SET status='expired', updated_at=?"
                    " WHERE approval_id=?",
                    (now, approval_id),
                )
                raise StoreError(
                    409, "approval_expired", "该审批已超时自动失效，不能再处理"
                )
            conn.execute(
                "UPDATE workflow_approvals SET status=?, decided_by=?, comment=?,"
                " decided_at=?, updated_at=? WHERE approval_id=?",
                (decision, decided_by, comment, now, now, approval_id),
            )
        return self.get_approval(approval_id) or {}

    def cancel_pending_approvals(self, run_id: str, *, comment: str = "") -> int:
        """运行被取消时，把它的待办一并失效，避免待办里残留永远点不动的条目。"""
        now = time.time()
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT approval_id FROM workflow_approvals WHERE run_id=? AND status='pending'",
                (run_id,),
            ).fetchall()
            for row in rows:
                conn.execute(
                    "UPDATE workflow_approvals SET status='expired', comment=?, updated_at=?"
                    " WHERE approval_id=?",
                    (comment, now, row["approval_id"]),
                )
        return len(rows)

    def expire_overdue_approvals(self, now: Optional[float] = None) -> List[Dict[str, Any]]:
        """把超时未处理的待办置为 expired，返回被过期的记录（供引擎终止运行）。"""
        moment = float(now if now is not None else time.time())
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT * FROM workflow_approvals WHERE status='pending'"
                " AND expires_at IS NOT NULL AND expires_at <= ?",
                (moment,),
            ).fetchall()
            ids = [str(row["approval_id"]) for row in rows]
            for approval_id in ids:
                conn.execute(
                    "UPDATE workflow_approvals SET status='expired', updated_at=?"
                    " WHERE approval_id=?",
                    (moment, approval_id),
                )
        return [self.get_approval(approval_id) for approval_id in ids if approval_id]

    # ── 触发器 ──────────────────────────────────────────────

    def sync_triggers(self, def_id: str, definition: Dict[str, Any]) -> None:
        """按定义重建触发器行；manual 不落行（它由 API 直接触发）。"""
        now = time.time()
        triggers = definition.get("triggers") or []
        with self._tx() as conn:
            conn.execute("DELETE FROM workflow_triggers WHERE def_id=?", (def_id,))
            for index, trigger in enumerate(triggers):
                if not isinstance(trigger, dict):
                    continue
                kind = str(trigger.get("type") or "manual")
                if kind == "manual":
                    continue
                next_due: Optional[float] = None
                if kind == "interval":
                    seconds = int(trigger.get("every_seconds") or 0)
                    next_due = now + seconds if seconds > 0 else None
                elif kind == "daily":
                    next_due = _next_daily_due(str(trigger.get("at") or ""), now)
                conn.execute(
                    "INSERT INTO workflow_triggers(trigger_key, def_id, trigger_type,"
                    " config_json, enabled, last_fired_at, next_due_at, updated_at)"
                    " VALUES(?,?,?,?,1,NULL,?,?)",
                    (
                        f"{def_id}:{index}",
                        def_id,
                        kind,
                        _dumps(trigger),
                        next_due,
                        now,
                    ),
                )

    def due_triggers(self, now: Optional[float] = None) -> List[Dict[str, Any]]:
        moment = float(now if now is not None else time.time())
        with self._lock:
            rows = self._connection().execute(
                "SELECT t.*, d.enabled AS def_enabled, d.definition_json"
                " FROM workflow_triggers t JOIN workflow_defs d ON d.def_id = t.def_id"
                " WHERE t.enabled=1 AND d.enabled=1 AND t.next_due_at IS NOT NULL"
                " AND t.next_due_at <= ? ORDER BY t.next_due_at ASC LIMIT 20",
                (moment,),
            ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["config"] = _loads(item.pop("config_json"), {})
            item["definition"] = _loads(item.pop("definition_json"), {})
            item["def_enabled"] = _bool(item.pop("def_enabled"))
            items.append(item)
        return items

    def event_triggers(self, event_name: str) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._connection().execute(
                "SELECT t.*, d.definition_json FROM workflow_triggers t"
                " JOIN workflow_defs d ON d.def_id = t.def_id"
                " WHERE t.enabled=1 AND d.enabled=1 AND t.trigger_type='event'",
            ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            config = _loads(item.pop("config_json"), {})
            if str(config.get("event") or "") != event_name:
                continue
            item["config"] = config
            item["definition"] = _loads(item.pop("definition_json"), {})
            items.append(item)
        return items

    def list_triggers(self, *, kind: Optional[str] = None) -> List[Dict[str, Any]]:
        sql = (
            "SELECT t.*, d.enabled AS def_enabled, d.definition_json FROM workflow_triggers t"
            " JOIN workflow_defs d ON d.def_id = t.def_id WHERE 1=1"
        )
        params: List[Any] = []
        if kind:
            sql += " AND t.trigger_type = ?"
            params.append(kind)
        with self._lock:
            rows = self._connection().execute(sql, params).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["config"] = _loads(item.pop("config_json"), {})
            item["definition"] = _loads(item.pop("definition_json"), {})
            item["def_enabled"] = _bool(item.pop("def_enabled"))
            items.append(item)
        return items

    def mark_trigger_fired(
        self, trigger_key: str, *, retire: bool = False
    ) -> None:
        now = time.time()
        with self._tx() as conn:
            row = conn.execute(
                "SELECT config_json FROM workflow_triggers WHERE trigger_key=?",
                (trigger_key,),
            ).fetchone()
            if row is None:
                return
            config = _loads(row["config_json"], {})
            next_due: Optional[float] = None
            if not retire:
                if config.get("type") == "interval":
                    seconds = int(config.get("every_seconds") or 0)
                    next_due = now + seconds if seconds > 0 else None
                elif config.get("type") == "daily":
                    next_due = _next_daily_due(str(config.get("at") or ""), now)
            conn.execute(
                "UPDATE workflow_triggers SET last_fired_at=?, next_due_at=?, updated_at=?"
                " WHERE trigger_key=?",
                (now, next_due, now, trigger_key),
            )

    # ── 概览 ────────────────────────────────────────────────

    def summary(self, *, owners: Optional[Sequence[str]] = None) -> Dict[str, Any]:
        run_clauses: List[str] = []
        approval_clauses: List[str] = []
        params_runs: List[Any] = []
        params_approvals: List[Any] = []
        if owners is not None:
            if not owners:
                return {
                    "definitions": 0,
                    "runs": {status: 0 for status in RUN_STATUSES},
                    "pending_approvals": 0,
                }
            placeholders = ",".join("?" for _ in owners)
            run_clauses.append(f"owner_account_id IN ({placeholders})")
            approval_clauses.append(f"owner_account_id IN ({placeholders})")
            params_runs.extend(owners)
            params_approvals.extend(owners)
        run_where = f" WHERE {' AND '.join(run_clauses)}" if run_clauses else ""
        approval_where = f" WHERE {' AND '.join(approval_clauses)}" if approval_clauses else ""
        with self._lock:
            conn = self._connection()
            counts = {status: 0 for status in RUN_STATUSES}
            for row in conn.execute(
                "SELECT status, COUNT(*) AS n FROM workflow_runs" + run_where + " GROUP BY status",
                params_runs,
            ).fetchall():
                counts[str(row["status"])] = int(row["n"])
            pending_row = conn.execute(
                "SELECT COUNT(*) AS n FROM workflow_approvals"
                + (approval_where + " AND " if approval_where else " WHERE ")
                + "status='pending'",
                params_approvals,
            ).fetchone()
            defs_row = conn.execute("SELECT COUNT(*) AS n FROM workflow_defs").fetchone()
        return {
            "definitions": int(defs_row["n"]) if defs_row is not None else 0,
            "runs": counts,
            "pending_approvals": int(pending_row["n"]) if pending_row is not None else 0,
        }


_stores: Dict[str, WorkflowStore] = {}
_stores_lock = threading.Lock()


def get_workflow_store(path: Optional[str] = None) -> WorkflowStore:
    key = str(path or default_workflow_path())
    with _stores_lock:
        store = _stores.get(key)
        if store is None:
            store = WorkflowStore(key)
            _stores[key] = store
        return store


def reset_workflow_stores() -> None:
    """测试用：关闭并清空单例缓存。"""
    with _stores_lock:
        for store in _stores.values():
            try:
                store.close()
            except Exception:  # noqa: BLE001 - 关闭失败不影响测试
                pass
        _stores.clear()
