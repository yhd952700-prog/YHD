"""Memory Kernel — SQLite 持久化后端。

让 memory kernel 跨进程重启保留记忆。风格对齐 audit kernel 的
``AuditStore``：SQLite + WAL + 线程安全 + 环境变量可配路径。

本模块只负责「字段字典 <-> SQLite 行」之间的序列化与读写，**不 import
``MemoryEntry`` 类型**（延迟到调用方传入字段字典），从而避免与包
``__init__.py`` 形成循环 import。序列化约定由本模块与 ``MemoryKernel``
共同遵守：

    key_hash  = f"{tier.value}:{key}"   （主键，覆盖式 upsert）
    value     = json.dumps(value)       （value 可为任意 JSON-able 结构）
    tags      = json.dumps(sorted(tags))
    provenance= json.dumps(provenance)  （可为 None）
    时间字段   = isoformat() 字符串
"""
from __future__ import annotations

import os
import sqlite3
import threading
from typing import Any, Dict, List, Optional

from ...ai.observability import observe

# 默认落盘位置（遵循用户约束：交付物/数据一律 D 盘）。
DEFAULT_DB_PATH = "D:/LiuHao-AI-OS/memory_store.db"


class MemoryStore:
    """SQLite-backed persistence for MemoryKernel entries."""

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            db_path = os.environ.get("MEMORY_DB_PATH", DEFAULT_DB_PATH)
        self._db_path = db_path
        self._lock = threading.RLock()
        self._conn: Optional[sqlite3.Connection] = None
        if db_path != ":memory:":
            parent = os.path.dirname(os.path.abspath(db_path))
            os.makedirs(parent, exist_ok=True)
        self._init_db()

    # ------------------------------------------------------------------ #
    # 建表
    # ------------------------------------------------------------------ #
    def _init_db(self) -> None:
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS memory_entries (
                key_hash        TEXT PRIMARY KEY,
                entry_id        TEXT NOT NULL,
                key             TEXT NOT NULL,
                value           TEXT NOT NULL,
                tier            TEXT NOT NULL,
                scope           TEXT NOT NULL,
                created_at      TEXT NOT NULL,
                expires_at      TEXT,
                tags            TEXT,
                correlation_id  TEXT NOT NULL,
                access_count    INTEGER NOT NULL DEFAULT 0,
                last_accessed   TEXT,
                provenance      TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_memory_tier  ON memory_entries(tier);
            CREATE INDEX IF NOT EXISTS idx_memory_scope ON memory_entries(scope);
            CREATE INDEX IF NOT EXISTS idx_memory_key   ON memory_entries(key);
            """
        )
        conn.commit()
        self._conn = conn

    # ------------------------------------------------------------------ #
    # 写
    # ------------------------------------------------------------------ #
    @observe("kernels.memory.store.persist")
    def persist(self, entry: Dict[str, Any]) -> None:
        """写穿落盘一条条目（按 ``key_hash`` 主键 upsert）。

        ``entry`` 是 ``MemoryEntry`` 的字段字典，字段名约定见模块 docstring。
        """
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO memory_entries (
                    key_hash, entry_id, key, value, tier, scope, created_at,
                    expires_at, tags, correlation_id, access_count,
                    last_accessed, provenance
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(key_hash) DO UPDATE SET
                    entry_id = excluded.entry_id,
                    key = excluded.key,
                    value = excluded.value,
                    tier = excluded.tier,
                    scope = excluded.scope,
                    created_at = excluded.created_at,
                    expires_at = excluded.expires_at,
                    tags = excluded.tags,
                    correlation_id = excluded.correlation_id,
                    access_count = excluded.access_count,
                    last_accessed = excluded.last_accessed,
                    provenance = excluded.provenance
                """,
                (
                    entry["key_hash"],
                    entry["entry_id"],
                    entry["key"],
                    entry["value"],
                    entry["tier"],
                    entry["scope"],
                    entry["created_at"],
                    entry["expires_at"],
                    entry["tags"],
                    entry["correlation_id"],
                    entry["access_count"],
                    entry["last_accessed"],
                    entry["provenance"],
                ),
            )
            self._conn.commit()

    @observe("kernels.memory.store.delete")
    def delete(self, key_hash: str) -> None:
        """按 ``key_hash`` 删除一条条目（compress 合并后移除源条目时用）。"""
        with self._lock:
            self._conn.execute(
                "DELETE FROM memory_entries WHERE key_hash = ?", (key_hash,)
            )
            self._conn.commit()

    def clear(self) -> None:
        """清空持久化存储（重置会话 / 测试隔离时用）。"""
        with self._lock:
            self._conn.execute("DELETE FROM memory_entries")
            self._conn.commit()

    # ------------------------------------------------------------------ #
    # 读
    # ------------------------------------------------------------------ #
    @observe("kernels.memory.store.load_all")
    def load_all(self) -> List[Dict[str, Any]]:
        """返回所有持久化条目的字段字典（供初始化时重建 ``_entries``）。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT key_hash, entry_id, key, value, tier, scope, created_at, "
                "expires_at, tags, correlation_id, access_count, last_accessed, "
                "provenance FROM memory_entries"
            ).fetchall()
        return [
            {
                "key_hash": r[0],
                "entry_id": r[1],
                "key": r[2],
                "value": r[3],
                "tier": r[4],
                "scope": r[5],
                "created_at": r[6],
                "expires_at": r[7],
                "tags": r[8],
                "correlation_id": r[9],
                "access_count": r[10],
                "last_accessed": r[11],
                "provenance": r[12],
            }
            for r in rows
        ]

    def count(self) -> int:
        with self._lock:
            return self._conn.execute(
                "SELECT COUNT(*) FROM memory_entries"
            ).fetchone()[0]

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
