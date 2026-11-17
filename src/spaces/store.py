"""协作空间的 SQLite 存储（数据目录 ``spaces.db``，重启/刷新还在）。

空间 = 名字 + 一句话介绍 + 成员 + 任务墙（任务墙条目是空间自己的
待办项，有 todo/doing/done 三态，不是台账任务的投影 —— 台账任务
仍由 goals 同步，空间任务墙只记"这个空间里要做的事"）。

可见范围（与账号树同口径）：
* 根账号（Joseph）见子树内所有空间；
* 普通用户只见自己创建的、或自己是成员的空间。
"""

from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Collection, Dict, List, Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS spaces (
    space_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    intro TEXT NOT NULL DEFAULT '',
    owner TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS space_members (
    space_id TEXT NOT NULL,
    principal TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'member',
    joined_at REAL NOT NULL,
    PRIMARY KEY (space_id, principal)
);
CREATE TABLE IF NOT EXISTS space_tasks (
    item_id TEXT PRIMARY KEY,
    space_id TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'todo',
    assignee TEXT NOT NULL DEFAULT '',
    created_by TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_space_members_principal ON space_members(principal);
CREATE INDEX IF NOT EXISTS idx_space_tasks_space ON space_tasks(space_id, created_at);
"""

#: 任务墙三态（空间自己的待办，不复用台账七态，避免把"空间里记一笔"
#: 说成"执行内核已接单"）。
SPACE_TASK_STATUSES = ("todo", "doing", "done")


def _default_db_path() -> str:
    from src.common.data_paths import resolve_store_path

    repo_root = Path(__file__).resolve().parents[2]
    return resolve_store_path(
        "spaces.db",
        repo_root / "data" / "spaces.db",
        env_var="LIUHAO_SPACES_DB",
    )


class SpaceStore:
    """协作空间库（线程安全、单文件 SQLite）。"""

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

    # ------------------------------------------------------------------ 空间
    def create_space(self, owner: str, name: str, intro: str = "") -> Dict[str, Any]:
        name = str(name or "").strip()
        if not name:
            raise ValueError("空间名字不能为空")
        space_id = f"space-{uuid.uuid4().hex[:12]}"
        now = time.time()
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO spaces(space_id, name, intro, owner, created_at, updated_at)"
                " VALUES(?,?,?,?,?,?)",
                (space_id, name, str(intro or "").strip(), owner, now, now),
            )
            conn.execute(
                "INSERT INTO space_members(space_id, principal, role, joined_at)"
                " VALUES(?,?, 'owner', ?)",
                (space_id, owner, now),
            )
        return self.get_space(space_id, principals={owner}) or {"space_id": space_id}

    def _visible_where(self, principals: Collection[str]) -> tuple[str, List[Any]]:
        owners = sorted({str(item) for item in principals if item})
        if not owners:
            return "1=0", []
        placeholders = ",".join("?" for _ in owners)
        return (
            f"(s.owner IN ({placeholders}) OR EXISTS ("
            f"SELECT 1 FROM space_members m WHERE m.space_id = s.space_id "
            f"AND m.principal IN ({placeholders})))",
            owners + owners,
        )

    def list_spaces(self, principals: Collection[str]) -> List[Dict[str, Any]]:
        clause, params = self._visible_where(principals)
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT s.* FROM spaces s WHERE "
                + clause
                + " ORDER BY s.created_at DESC",
                params,
            ).fetchall()
            items = []
            for row in rows:
                item = dict(row)
                item["member_count"] = int(
                    conn.execute(
                        "SELECT COUNT(*) FROM space_members WHERE space_id=?",
                        (item["space_id"],),
                    ).fetchone()[0]
                )
                item["task_count"] = int(
                    conn.execute(
                        "SELECT COUNT(*) FROM space_tasks WHERE space_id=?",
                        (item["space_id"],),
                    ).fetchone()[0]
                )
                items.append(item)
            return items

    def get_space(
        self, space_id: str, *, principals: Optional[Collection[str]] = None
    ) -> Optional[Dict[str, Any]]:
        """空间详情（含任务墙与成员）；不在可见范围返回 None（不区分不存在）。"""
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM spaces WHERE space_id=?", (space_id,)
            ).fetchone()
            if row is None:
                return None
            space = dict(row)
            members = [
                dict(item)
                for item in conn.execute(
                    "SELECT * FROM space_members WHERE space_id=? ORDER BY joined_at",
                    (space_id,),
                ).fetchall()
            ]
            tasks = [
                dict(item)
                for item in conn.execute(
                    "SELECT * FROM space_tasks WHERE space_id=? ORDER BY created_at",
                    (space_id,),
                ).fetchall()
            ]
        if principals is not None:
            allowed = {str(item) for item in principals if item}
            if space["owner"] not in allowed and not any(
                str(member.get("principal")) in allowed for member in members
            ):
                return None
        space["members"] = members
        space["tasks"] = tasks
        return space

    def delete_space(self, space_id: str) -> bool:
        with self._lock, self._connect() as conn:
            cursor = conn.execute("DELETE FROM spaces WHERE space_id=?", (space_id,))
            if cursor.rowcount == 0:
                return False
            conn.execute("DELETE FROM space_members WHERE space_id=?", (space_id,))
            conn.execute("DELETE FROM space_tasks WHERE space_id=?", (space_id,))
            return True

    # ------------------------------------------------------------------ 成员
    def add_member(
        self, space_id: str, principal: str, role: str = "member"
    ) -> Optional[Dict[str, Any]]:
        principal = str(principal or "").strip()
        if not principal:
            raise ValueError("成员不能为空")
        if role not in ("member", "admin"):
            raise ValueError("role 只能是 member 或 admin")
        with self._lock, self._connect() as conn:
            if (
                conn.execute(
                    "SELECT 1 FROM spaces WHERE space_id=?", (space_id,)
                ).fetchone()
                is None
            ):
                return None
            conn.execute(
                "INSERT OR REPLACE INTO space_members(space_id, principal, role, joined_at)"
                " VALUES(?,?,?,?)",
                (space_id, principal, role, time.time()),
            )
            row = conn.execute(
                "SELECT * FROM space_members WHERE space_id=? AND principal=?",
                (space_id, principal),
            ).fetchone()
            return dict(row) if row else None

    def remove_member(self, space_id: str, principal: str) -> bool:
        """移除成员；空间创建者（owner）不能被移出，返回 False。"""
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT owner FROM spaces WHERE space_id=?", (space_id,)
            ).fetchone()
            if row is None:
                return False
            if str(row["owner"]) == principal:
                return False
            cursor = conn.execute(
                "DELETE FROM space_members WHERE space_id=? AND principal=?",
                (space_id, principal),
            )
            return cursor.rowcount > 0

    # ------------------------------------------------------------------ 任务墙
    def add_task(
        self, space_id: str, title: str, created_by: str, assignee: str = ""
    ) -> Optional[Dict[str, Any]]:
        title = str(title or "").strip()
        if not title:
            raise ValueError("任务标题不能为空")
        item_id = f"stask-{uuid.uuid4().hex[:12]}"
        now = time.time()
        with self._lock, self._connect() as conn:
            if (
                conn.execute(
                    "SELECT 1 FROM spaces WHERE space_id=?", (space_id,)
                ).fetchone()
                is None
            ):
                return None
            conn.execute(
                "INSERT INTO space_tasks(item_id, space_id, title, status,"
                " assignee, created_by, created_at, updated_at)"
                " VALUES(?,?,?,?, 'todo', ?, ?, ?)",
                (item_id, space_id, title, str(assignee or "").strip(), created_by, now, now),
            )
            row = conn.execute(
                "SELECT * FROM space_tasks WHERE item_id=?", (item_id,)
            ).fetchone()
            return dict(row) if row else None

    def update_task(
        self, item_id: str, space_id: str, **fields: Any
    ) -> Optional[Dict[str, Any]]:
        allowed: Dict[str, Any] = {}
        if "title" in fields:
            title = str(fields["title"] or "").strip()
            if not title:
                raise ValueError("任务标题不能为空")
            allowed["title"] = title
        if "status" in fields:
            status = str(fields["status"] or "").strip()
            if status not in SPACE_TASK_STATUSES:
                raise ValueError(f"任务状态只能是 {', '.join(SPACE_TASK_STATUSES)}")
            allowed["status"] = status
        if "assignee" in fields:
            allowed["assignee"] = str(fields["assignee"] or "").strip()
        if not allowed:
            return self.get_task(item_id, space_id)
        allowed["updated_at"] = time.time()
        assignments = ", ".join(f"{key}=?" for key in allowed)
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                f"UPDATE space_tasks SET {assignments} WHERE item_id=? AND space_id=?",
                (*allowed.values(), item_id, space_id),
            )
            if cursor.rowcount == 0:
                return None
        return self.get_task(item_id, space_id)

    def get_task(self, item_id: str, space_id: str) -> Optional[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM space_tasks WHERE item_id=? AND space_id=?",
                (item_id, space_id),
            ).fetchone()
            return dict(row) if row else None

    def delete_task(self, item_id: str, space_id: str) -> bool:
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM space_tasks WHERE item_id=? AND space_id=?",
                (item_id, space_id),
            )
            return cursor.rowcount > 0


_space_store: Optional[SpaceStore] = None
_space_lock = threading.Lock()


def get_space_store() -> SpaceStore:
    """进程级单例（测试可直接构造 ``SpaceStore(path)`` 绕开它）。"""
    global _space_store
    if _space_store is None:
        with _space_lock:
            if _space_store is None:
                _space_store = SpaceStore()
    return _space_store


__all__ = ["SPACE_TASK_STATUSES", "SpaceStore", "get_space_store"]
