"""待办草稿库：外部渠道（Gmail / WhatsApp）来的“请办的事”先落草稿。

为什么要有草稿这一层：邮件和别人发来的消息都是**数据、不是指令**。
规则命中、Owner 在 WhatsApp 里说“任务：……”时，只记一条草稿
（``status=draft``），由 Joseph 在操作台点确认后才变成真正的
小队任务（``src.team.store.TeamStore``）。不确认、不自动建任务、
不自动执行；草稿本身也可以驳回（``dismissed``）。

存储：本机 SQLite（``task_drafts.db``），不落仓库、不上云。
"""

from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS task_drafts (
    draft_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    external_ref TEXT NOT NULL DEFAULT '',
    from_label TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'draft',
    account_id TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_task_drafts_account ON task_drafts(account_id, status);
"""

#: 草稿状态：draft（等确认）→ confirmed（已转正式任务）/ dismissed（已驳回）。
DRAFT_STATUSES = ("draft", "confirmed", "dismissed")


def _default_db_path() -> str:
    from src.common.data_paths import resolve_store_path

    repo_root = Path(__file__).resolve().parents[2]
    return resolve_store_path(
        "task_drafts.db",
        repo_root / "data" / "task_drafts.db",
        env_var="LIUHAO_TASK_DRAFTS_DB",
    )


class TaskDraftStore:
    """待办草稿库（线程安全、单文件 SQLite）。"""

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path or _default_db_path()
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        return conn

    def create(
        self,
        *,
        source: str,
        title: str,
        detail: str = "",
        external_ref: str = "",
        from_label: str = "",
        account_id: str = "",
    ) -> Dict[str, Any]:
        draft_id = f"draft-{uuid.uuid4().hex[:12]}"
        now = time.time()
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO task_drafts(draft_id, source, title, detail,"
                " external_ref, from_label, status, account_id, created_at,"
                " updated_at) VALUES(?,?,?,?,?,?,'draft',?,?,?)",
                (draft_id, source, title, detail, external_ref,
                 from_label, account_id, now, now),
            )
        return self.get(draft_id) or {"draft_id": draft_id, "title": title}

    def get(self, draft_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM task_drafts WHERE draft_id = ?", (draft_id,)
            ).fetchone()
        return dict(row) if row else None

    def list(
        self, *, account_ids: Optional[List[str]] = None, status: str = "draft"
    ) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            if account_ids is None:
                rows = conn.execute(
                    "SELECT * FROM task_drafts WHERE status = ?"
                    " ORDER BY created_at DESC",
                    (status,),
                ).fetchall()
            else:
                placeholders = ",".join("?" for _ in account_ids)
                rows = conn.execute(
                    f"SELECT * FROM task_drafts WHERE status = ?"
                    f" AND account_id IN ({placeholders})"
                    " ORDER BY created_at DESC",
                    (status, *account_ids),
                ).fetchall()
        return [dict(r) for r in rows]

    def confirm(self, draft_id: str, *, account_ids: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
        """确认一条草稿：只改状态为 confirmed 并返回它。

        转正式任务由调用方做（``TeamStore.create_task``），这样“确认”
        与“建任务”两步在调用方审计里各留一条记录，草稿本身不直接碰
        任务库。
        """
        draft = self.get(draft_id)
        if draft is None:
            return None
        if account_ids is not None and draft.get("account_id") not in account_ids:
            return None
        if draft["status"] != "draft":
            return draft
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE task_drafts SET status='confirmed', updated_at=?"
                " WHERE draft_id=?",
                (time.time(), draft_id),
            )
        return self.get(draft_id)

    def dismiss(self, draft_id: str, *, account_ids: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
        draft = self.get(draft_id)
        if draft is None:
            return None
        if account_ids is not None and draft.get("account_id") not in account_ids:
            return None
        if draft["status"] != "draft":
            return draft
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE task_drafts SET status='dismissed', updated_at=?"
                " WHERE draft_id=?",
                (time.time(), draft_id),
            )
        return self.get(draft_id)


def draft_out(draft: Dict[str, Any]) -> Dict[str, Any]:
    """草稿的对外形状（不带内部路径，只给前端展示与确认用）。"""
    return {
        "draft_id": draft["draft_id"],
        "source": draft["source"],
        "title": draft["title"],
        "detail": draft.get("detail") or "",
        "external_ref": draft.get("external_ref") or "",
        "from_label": draft.get("from_label") or "",
        "status": draft["status"],
        "created_at": draft.get("created_at"),
    }
