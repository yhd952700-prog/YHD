"""小队任务的 SQLite 存储（数据目录 ``team_tasks.db``，不落仓库）。"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS team_tasks (
    task_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS team_subtasks (
    subtask_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    progress INTEGER NOT NULL DEFAULT 0,
    detail TEXT NOT NULL DEFAULT '',
    approval_id TEXT,
    run_id TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS team_events (
    event_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS team_members (
    member_id TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    model_id TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_team_subtasks_task ON team_subtasks(task_id);
CREATE INDEX IF NOT EXISTS idx_team_events_task ON team_events(task_id);
CREATE INDEX IF NOT EXISTS idx_team_members_owner ON team_members(owner);
CREATE INDEX IF NOT EXISTS idx_team_members_kind ON team_members(owner, kind);
"""

#: 子任务状态：queued → running → done；需要拍板时 needs_decision；
#: 批了但对应能力还没接线时 approved_waiting；失败 failed。
SUBTASK_STATUSES = (
    "queued",
    "running",
    "done",
    "needs_decision",
    "approved_waiting",
    "failed",
)


def _default_db_path() -> str:
    from src.common.data_paths import resolve_store_path

    repo_root = Path(__file__).resolve().parents[2]
    return resolve_store_path(
        "team_tasks.db",
        repo_root / "data" / "team_tasks.db",
    )


class TeamStore:
    """小队任务库（线程安全、单文件 SQLite）。"""

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path or _default_db_path()
        directory = str(Path(self.path).parent)
        Path(directory).mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        return conn

    # ------------------------------------------------------------------ 任务
    def create_task(self, title: str, created_by: str) -> Dict[str, Any]:
        task_id = f"team-{uuid.uuid4().hex[:12]}"
        now = time.time()
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO team_tasks(task_id, title, status, created_by,"
                " created_at, updated_at) VALUES(?,?,?,?,?,?)",
                (task_id, title, "running", created_by, now, now),
            )
        return self.get_task(task_id) or {"task_id": task_id, "title": title}

    def add_subtask(
        self, task_id: str, kind: str, title: str, status: str = "queued"
    ) -> Dict[str, Any]:
        subtask_id = f"sub-{uuid.uuid4().hex[:12]}"
        now = time.time()
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO team_subtasks(subtask_id, task_id, kind, title,"
                " status, progress, detail, created_at, updated_at)"
                " VALUES(?,?,?,?,?,0,'',?,?)",
                (subtask_id, task_id, kind, title, status, now, now),
            )
        return self.get_subtask(subtask_id) or {"subtask_id": subtask_id}

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM team_tasks WHERE task_id=?", (task_id,)
            ).fetchone()
            if row is None:
                return None
            task = dict(row)
            subs = conn.execute(
                "SELECT * FROM team_subtasks WHERE task_id=? ORDER BY created_at",
                (task_id,),
            ).fetchall()
            task["subtasks"] = [dict(item) for item in subs]
            return task

    def list_tasks(self, *, created_by: Optional[str] = None, limit: int = 20) -> List[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            if created_by:
                rows = conn.execute(
                    "SELECT * FROM team_tasks WHERE created_by=? ORDER BY created_at DESC LIMIT ?",
                    (created_by, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM team_tasks ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            tasks = []
            for row in rows:
                task = dict(row)
                subs = conn.execute(
                    "SELECT * FROM team_subtasks WHERE task_id=? ORDER BY created_at",
                    (task["task_id"],),
                ).fetchall()
                task["subtasks"] = [dict(item) for item in subs]
                tasks.append(task)
            return tasks

    def update_subtask(self, subtask_id: str, **fields: Any) -> None:
        allowed = {"status", "progress", "detail", "approval_id", "run_id"}
        updates = {key: value for key, value in fields.items() if key in allowed}
        if not updates:
            return
        updates["updated_at"] = time.time()
        assignments = ", ".join(f"{key}=?" for key in updates)
        with self._lock, self._connect() as conn:
            conn.execute(
                f"UPDATE team_subtasks SET {assignments} WHERE subtask_id=?",
                (*updates.values(), subtask_id),
            )

    def update_task_status(self, task_id: str, status: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE team_tasks SET status=?, updated_at=? WHERE task_id=?",
                (status, time.time(), task_id),
            )

    def get_subtask(self, subtask_id: str) -> Optional[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM team_subtasks WHERE subtask_id=?", (subtask_id,)
            ).fetchone()
            return dict(row) if row else None

    # ------------------------------------------------------------------ 成员
    #: 4 种小队角色（查资料/整理文件/管提醒/写代码）：只是“派活时按需建记录”
    #: 用的名字表，不是预置成员。空白开局（2026-11-14 Joseph 定）：首次打开
    #: ``GET /v1/team/members`` 不再播种这 4 人，返回空列表；派活时分发器
    #: 按 kind 需要哪种才建哪一种成员记录（``ensure_member_for_kind``），
    #: 没派过活的 kind 不在前台出现。成员表每人（owner）一份，之后可新增、
    #: 停用/启用、换模型。``model_id`` 为空 = 未指定模型，用默认模型
    #: （老实空状态，不编一个"默认是 DeepSeek"）。
    DEFAULT_MEMBERS: tuple[tuple[str, str], ...] = (
        ("research", "查资料"),
        ("files", "整理文件"),
        ("reminder", "管提醒"),
        ("code", "写代码"),
    )

    def ensure_member_for_kind(self, owner: str, kind: str) -> Optional[Dict[str, Any]]:
        """派活时按需为该 kind 建一条成员记录（只建这一种）；已有就直接返回。

        不知道的 kind 返回 None（不建记录），调用方按原有分支处理。
        """
        owner = str(owner or "").strip()
        kind = str(kind or "").strip()
        if not owner or not kind:
            return None
        existing = self.get_member_by_kind(owner, kind)
        if existing is not None:
            return existing
        labels = dict(self.DEFAULT_MEMBERS)
        name = labels.get(kind)
        if name is None:
            return None
        return self.create_member(owner, kind, name)

    def list_members(self, owner: str) -> List[Dict[str, Any]]:
        """只读：返回该 owner 已有的成员；全新状态下是空列表，不再播种。"""
        owner = str(owner or "").strip()
        if not owner:
            return []
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM team_members WHERE owner=? ORDER BY created_at",
                (owner,),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_member(self, member_id: str, owner: Optional[str] = None) -> Optional[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            if owner is not None:
                row = conn.execute(
                    "SELECT * FROM team_members WHERE member_id=? AND owner=?",
                    (member_id, owner),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM team_members WHERE member_id=?", (member_id,)
                ).fetchone()
            return dict(row) if row else None

    def get_member_by_kind(self, owner: str, kind: str) -> Optional[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM team_members WHERE owner=? AND kind=?",
                (owner, kind),
            ).fetchone()
            return dict(row) if row else None

    def create_member(
        self, owner: str, kind: str, name: str, model_id: Optional[str] = None
    ) -> Dict[str, Any]:
        kind = str(kind or "").strip()
        name = str(name or "").strip()
        if not kind or not name:
            raise ValueError("成员类型和名字都不能为空")
        now = time.time()
        member_id = f"member-{uuid.uuid4().hex[:12]}"
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO team_members(member_id, owner, kind, name,"
                " model_id, active, created_at, updated_at)"
                " VALUES(?,?,?,?,?,1,?,?)",
                (
                    member_id, owner, kind, name,
                    (str(model_id).strip() or None) if model_id is not None else None,
                    now, now,
                ),
            )
        return self.get_member(member_id) or {"member_id": member_id}

    def update_member(self, member_id: str, owner: str, **fields: Any) -> Optional[Dict[str, Any]]:
        """改成员：名字 / 模型 / 停用启用（active 0/1）。不在自己名下返回 None。"""
        allowed: Dict[str, Any] = {}
        if "name" in fields:
            name = str(fields["name"] or "").strip()
            if not name:
                raise ValueError("成员名字不能为空")
            allowed["name"] = name
        if "model_id" in fields:
            raw = fields["model_id"]
            allowed["model_id"] = (str(raw).strip() or None) if raw is not None else None
        if "active" in fields:
            allowed["active"] = 1 if fields["active"] else 0
        if "kind" in fields:
            kind = str(fields["kind"] or "").strip()
            if not kind:
                raise ValueError("成员类型不能为空")
            allowed["kind"] = kind
        if not allowed:
            return self.get_member(member_id, owner)
        allowed["updated_at"] = time.time()
        assignments = ", ".join(f"{key}=?" for key in allowed)
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                f"UPDATE team_members SET {assignments} WHERE member_id=? AND owner=?",
                (*allowed.values(), member_id, owner),
            )
            if cursor.rowcount == 0:
                return None
        return self.get_member(member_id, owner)

    # ------------------------------------------------------------------ 事件
    def add_event(self, task_id: str, kind: str, message: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO team_events(event_id, task_id, kind, message, created_at)"
                " VALUES(?,?,?,?,?)",
                (f"ev-{uuid.uuid4().hex[:12]}", task_id, kind, message, time.time()),
            )

    def list_events(self, task_id: str, *, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM team_events WHERE task_id=? ORDER BY created_at DESC LIMIT ?",
                (task_id, limit),
            ).fetchall()
            return [dict(row) for row in rows]


_team_store: Optional[TeamStore] = None
_team_lock = threading.Lock()


def get_team_store() -> TeamStore:
    """进程级单例（测试可直接构造 ``TeamStore(path)`` 绕开它）。"""
    global _team_store
    if _team_store is None:
        with _team_lock:
            if _team_store is None:
                _team_store = TeamStore()
    return _team_store


__all__ = ["SUBTASK_STATUSES", "TeamStore", "get_team_store"]
