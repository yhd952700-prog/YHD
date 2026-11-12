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

from ...common.data_paths import resolve_store_path
from ...ai.observability import observe

# 默认落盘位置：项目根目录（非写死某台机器的绝对路径 —— 写死会在 CI 工作区
# 造出名为 "D:" 的目录，upload-artifact 因路径含冒号失败）。
_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
)
_LEGACY_DB_PATH = os.path.join(_PROJECT_ROOT, "memory_store.db")


def _default_db_path() -> str:
    """记忆库落盘路径：``MEMORY_DB_PATH`` > ``LIUHAO_DATA_DIR`` > 历史默认。"""
    return resolve_store_path(
        "memory_store.db",
        _LEGACY_DB_PATH,
        env_var="MEMORY_DB_PATH",
    )


DEFAULT_DB_PATH = _default_db_path()

# 断电级持久化：WAL+synchronous=NORMAL 只能扛"进程被杀"（已提交事务在 OS
# 页缓存里），扛不住"直接断电/蓝屏"（最后若干已确认事务可能随页缓存丢失）。
# 记忆写入是低频操作，默认 FULL 换取"已返回成功的写入一定还在"；确实要
# 用吞吐换风险时可用环境变量降为 NORMAL/OFF。
_SYNC_LEVELS = {"OFF", "NORMAL", "FULL", "EXTRA"}
_MEMORY_SYNCHRONOUS = (
    os.environ.get("LIUHAO_MEMORY_SYNCHRONOUS") or "FULL"
).strip().upper()
if _MEMORY_SYNCHRONOUS not in _SYNC_LEVELS:
    _MEMORY_SYNCHRONOUS = "FULL"


class MemoryStoreError(RuntimeError):
    """记忆落盘失败：带可读原因，调用方必须据此失败（fail-closed）。

    不吞异常、不假装成功：磁盘满 / 数据目录只读 / 文件被独占时，调用方拿到
    的是一条能看懂的中文原因，并且已成功落盘的旧记忆保持不变。
    """

    def __init__(self, db_path: str, exc: Exception):
        super().__init__(
            f"无法写入记忆库 {db_path}（磁盘可能已满、数据目录只读或文件被占用）："
            f"{exc}。本次记忆未保存，已有记忆保持不变。"
        )
        self.db_path = db_path
        self.original = exc


class MemoryStore:
    """SQLite-backed persistence for MemoryKernel entries."""

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            db_path = _default_db_path()
        self._db_path = db_path
        self._lock = threading.RLock()
        self._conn: Optional[sqlite3.Connection] = None
        try:
            if db_path != ":memory:":
                parent = os.path.dirname(os.path.abspath(db_path))
                os.makedirs(parent, exist_ok=True)
            self._init_db()
        except (sqlite3.Error, OSError) as exc:
            raise MemoryStoreError(db_path, exc) from exc

    def _rollback_quietly(self) -> None:
        """失败路径的回滚：本身再失败也不能掩盖原始异常。"""
        try:
            if self._conn is not None:
                self._conn.rollback()
        except sqlite3.Error:
            pass

    # ------------------------------------------------------------------ #
    # 建表
    # ------------------------------------------------------------------ #
    def _init_db(self) -> None:
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(f"PRAGMA synchronous={_MEMORY_SYNCHRONOUS}")
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
            try:
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
            except sqlite3.Error as exc:
                self._rollback_quietly()
                raise MemoryStoreError(self._db_path, exc) from exc

    @observe("kernels.memory.store.delete")
    def delete(self, key_hash: str) -> None:
        """按 ``key_hash`` 删除一条条目（compress 合并后移除源条目时用）。"""
        with self._lock:
            try:
                self._conn.execute(
                    "DELETE FROM memory_entries WHERE key_hash = ?", (key_hash,)
                )
                self._conn.commit()
            except sqlite3.Error as exc:
                self._rollback_quietly()
                raise MemoryStoreError(self._db_path, exc) from exc

    def clear(self) -> None:
        """清空持久化存储（重置会话 / 测试隔离时用）。"""
        with self._lock:
            try:
                self._conn.execute("DELETE FROM memory_entries")
                self._conn.commit()
            except sqlite3.Error as exc:
                self._rollback_quietly()
                raise MemoryStoreError(self._db_path, exc) from exc

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
