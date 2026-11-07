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
from typing import Any, Dict, List, Optional, Sequence

from .observability import observe
from .audit import audited

# 默认落在项目根目录，而不是写死某一台机器的绝对路径：保留"数据不入库内"
# 的约定，同时保证在任何机器上 checkout 后都能直接运行（写死路径会在 CI 的
# 工作区里造出名为 "D:" 的目录，upload-artifact 因路径含冒号而失败）。
_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
DEFAULT_DB_PATH = os.environ.get(
    "CONVERSATION_DB_PATH",
    os.path.join(_PROJECT_ROOT, "conversation_store.db"),
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

    @property
    def db_path(self) -> str:
        """实际落盘的库路径（账号备份按此路径取一致快照）。"""
        return self._db_path

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
    @audited("p5.conversation.append", module="src.ai.conversation_store")
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

    def load_turns(self, principal_id: str) -> List[Dict[str, Any]]:
        """按顺序返回带时间戳的完整消息（导出「人可读对话」用）。

        ``load`` 只给 role/content，是为了直接喂给模型；导出需要让人看出
        每句话是什么时候说的，所以单独给一个带 ``turn`` / ``created_at``
        的读取口，不改动既有调用方契约。
        """
        query = (
            "SELECT turn, role, content, created_at FROM conversation_turns "
            "WHERE principal_id = ? ORDER BY seq ASC"
        )
        with self._lock:
            rows = self._conn.execute(query, (principal_id,)).fetchall()
        return [
            {
                "turn": int(turn),
                "role": str(role),
                "content": str(content),
                "created_at": float(created_at or 0),
            }
            for turn, role, content, created_at in rows
        ]

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

    def has_rows(self, principal_id: str) -> bool:
        """该主体是否有任何已落盘的对话消息（用于新旧键择优）。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM conversation_turns WHERE principal_id = ? LIMIT 1",
                (principal_id,),
            ).fetchone()
        return row is not None

    def list_session_ids(
        self,
        principal: Optional[str],
        known_principals: Sequence[str] = (),
        allow_legacy: bool = False,
    ) -> List[str]:
        """从持久库解析出会话名（重启后仍有列表，不再依赖进程内存）。

        键口径兼容两代：

        * 新键 ``liuhao-{principal}-{session}``（当前版本）；
        * 旧键 ``liuhao-{session}``（0.1.0；``session`` 往往自带 ``liuhao-``
          前缀，故旧行形如 ``liuhao-liuhao-session-xxx``）。

        ``allow_legacy`` 只对根主体/全局视图开放：旧键没有主体段，无法区分
        归属；把它开放给普通成员会暴露别人的会话名。``known_principals`` 用于
        排除"其实是别人新键"的行（例如 ``liuhao-alice-chat``）。
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT principal_id FROM conversation_turns"
            ).fetchall()
        ids: set[str] = set()
        others = [
            f"{item}-" for item in known_principals
            if item and item != principal
        ]
        for (raw,) in rows:
            principal_id = str(raw or "")
            if not principal_id.startswith("liuhao-"):
                continue
            rest = principal_id[len("liuhao-"):]
            if not rest:
                continue
            if principal is None:
                ids.add(rest)
                continue
            composite = f"{principal}-"
            if rest.startswith(composite):
                session_id = rest[len(composite):]
                if session_id:
                    ids.add(session_id)
                continue
            if not allow_legacy:
                continue
            if any(rest.startswith(prefix) for prefix in others):
                continue
            ids.add(rest)
        return sorted(ids)

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
