"""会话历史持久化 — 跨进程重启恢复对话上下文。

为什么需要它：memory kernel 是进程内内存态（重启即失），audit kernel 虽 SQLite
落盘但 details 里只存元数据（chars_in/chars_out）不存对话文本。因此"鎏灏重启后
还记得上一段对话"这件事此前不成立。

本模块提供一个最小、诚实的会话存储：把「谁(principal)在第几轮(turn)说了什么
(role/content)」落盘，让 ``LiuHaoAssistant`` 重启后能恢复多轮上下文。

设计对齐 audit kernel 的 ``AuditStore``：SQLite + WAL + 全局单例 + D 盘默认路径，
不另起一套存储约定。principal 即隔离维度，天然支持后续多用户/session 隔离。
"""

from __future__ import annotations

import os
import sqlite3
import threading
from typing import Any, Dict, List, Optional

from .observability import observe

DEFAULT_DB_PATH = os.environ.get(
    "CONVERSATION_DB_PATH",
    "D:/LiuHao-AI-OS/conversation_store.db",
)


class ConversationStore:
    """持久化多轮对话历史（按 principal 隔离）。

    每条记录一行（自增 ``seq`` 保证插入顺序即恢复顺序），同一条消息只追加
    不改写，历史不可变、可完整重放。
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        self._db_path = db_path or DEFAULT_DB_PATH
        self._lock = threading.RLock()
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _init_db(self) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self._db_path)), exist_ok=True)
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS conversation_turns (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                principal_id TEXT NOT NULL,
                turn INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))
            );
            CREATE INDEX IF NOT EXISTS idx_conv_principal
                ON conversation_turns(principal_id, turn, seq);
            """
        )
        self._conn.commit()

    # ------------------------------------------------------------------ #
    # 写入 / 读取
    # ------------------------------------------------------------------ #
    @observe("conversation.append")
    def append(self, principal_id: str, turn: int, role: str, content: str) -> None:
        """追加一条对话消息（user 或 assistant）。"""
        with self._lock:
            self._conn.execute(
                "INSERT INTO conversation_turns (principal_id, turn, role, content) "
                "VALUES (?, ?, ?, ?)",
                (principal_id, turn, role, content),
            )
            self._conn.commit()

    @observe("conversation.load")
    def load(self, principal_id: str, limit: Optional[int] = None) -> List[Dict[str, str]]:
        """按插入顺序返回该主体的历史消息 ``[{role, content}, ...]``。

        返回的列表可直接作为多轮上下文的历史片段。``limit`` 为 None 时全量。
        """
        query = (
            "SELECT role, content FROM conversation_turns "
            "WHERE principal_id = ? ORDER BY seq ASC"
        )
        params: List[Any] = [principal_id]
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [{"role": role, "content": content} for role, content in rows]

    def load_recent(self, principal_id: str, limit: int) -> List[Dict[str, str]]:
        """返回最近 ``limit`` 条历史消息（保持顺序）。"""
        query = (
            "SELECT role, content FROM conversation_turns "
            "WHERE principal_id = ? ORDER BY seq DESC LIMIT ?"
        )
        with self._lock:
            rows = self._conn.execute(query, (principal_id, limit)).fetchall()
        # 倒序取回后正序返回，保证时间顺序。
        rows = list(reversed(rows))
        return [{"role": role, "content": content} for role, content in rows]

    def get_last_turn(self, principal_id: str) -> int:
        """返回该主体已进行到的轮数（无记录返回 0）。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT MAX(turn) FROM conversation_turns WHERE principal_id = ?",
                (principal_id,),
            ).fetchone()
        return int(row[0]) if row and row[0] is not None else 0

    def clear(self, principal_id: str) -> None:
        """清空该主体的对话历史（对应 ``reset`` 语义）。"""
        with self._lock:
            self._conn.execute(
                "DELETE FROM conversation_turns WHERE principal_id = ?", (principal_id,)
            )
            self._conn.commit()

    # ------------------------------------------------------------------ #
    # 观测
    # ------------------------------------------------------------------ #
    def stats(self) -> Dict[str, Any]:
        """返回存储统计（含各主体的轮数分布）。"""
        with self._lock:
            total = self._conn.execute("SELECT COUNT(*) FROM conversation_turns").fetchone()[0]
            principals = self._conn.execute(
                "SELECT principal_id, COUNT(DISTINCT turn) FROM conversation_turns "
                "GROUP BY principal_id ORDER BY principal_id"
            ).fetchall()
        return {
            "total_turns": total,
            "principals": {p: t for p, t in principals},
        }


# ---------------------------------------------------------------------- #
# 全局单例（对齐 audit kernel 的 get_audit_store）
# ---------------------------------------------------------------------- #
_conversation_store: Optional[ConversationStore] = None


@observe("conversation.get_conversation_store")
def get_conversation_store() -> ConversationStore:
    """获取或创建全局会话存储实例。"""
    global _conversation_store
    if _conversation_store is None:
        _conversation_store = ConversationStore()
    return _conversation_store
