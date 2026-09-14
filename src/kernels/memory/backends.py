"""Memory Kernel — pluggable backend abstraction (Phase 4).

The kernel logically separates "memory logic" (``MemoryKernel`` — tiers, scopes,
TTL, compression, write-through working set) from "persistence" (this module).

Before Phase 4 the persistence seam was implicit: ``MemoryKernel`` hard-wired a
``MemoryStore`` (SQLite). This module makes the seam *explicit and swappable*
without changing the kernel's public API:

* ``MemoryBackend`` — a ``runtime_checkable`` Protocol whose method signatures are
  byte-for-byte the ones ``MemoryStore`` already exposes, so the existing SQLite
  store conforms with **zero changes**.
* ``InMemoryBackend`` — dict-backed, for tests / ephemeral runs (complements the
  existing ``":memory:"`` SQLite path with a dependency-free option).
* ``SqliteBackend`` — thin adapter that wraps the existing ``MemoryStore``.
* ``SqlAlchemyBackend`` (Phase 7c) — real SQLAlchemy-Core backend whose **target** is
  PostgreSQL, sharing one CRUD code path that can also be driven by ``sqlite://``
  for offline verification.
* ``get_memory_backend(spec)`` — factory; ``MEMORY_BACKEND`` env selects the
  default (``sqlite``), preserving current behaviour when unset.

Why SQLAlchemy rather than a bare ``psycopg2`` (实测决定，非偏好)
----------------------------------------------------------------
``psycopg2-binary`` 在本机可 import，但它 ① 不在 ``pyproject.toml`` /
``requirements.txt`` 任何清单里（幻影依赖），② 实测许可为 ``LGPL with exceptions``，
而本仓 ``oss-registry.yaml`` 的 ``restricted_licenses`` **含 LGPL-3.0**，登记册又硬性
要求"新增任何第三方依赖都必须先登记"。改用 SQLAlchemy 则**零新增依赖**：它是 MIT、
**已登记**（``oss-registry.yaml`` ``used: "yes"``）、且项目已在
``src/integrations/orm_models.py`` 使用。详见 ``docs/POSTGRES-BACKEND-DESIGN.md``。

**没有 DBAPI 驱动时 PostgreSQL 仍连不上** —— 那是事实，所以由
``BackendUnavailableError`` 如实报告并给出可操作建议，而不是静默降级到 SQLite。

This module imports only ``store`` (never the package ``__init__``) to avoid a
circular import.
"""
from __future__ import annotations

import os
import threading
from typing import Any, Dict, List, Optional, Protocol, Tuple, runtime_checkable

from src.kernels.memory.store import MemoryStore


@runtime_checkable
class MemoryBackend(Protocol):
    """Persistence contract for ``MemoryKernel``.

    Method signatures match ``MemoryStore`` exactly, so ``MemoryStore`` is a
    valid ``MemoryBackend`` by structural typing (see the conformance test).
    """

    def persist(self, entry: Dict[str, Any]) -> None:  # pragma: no cover - protocol
        ...

    def delete(self, key_hash: str) -> None:  # pragma: no cover - protocol
        ...

    def clear(self) -> None:  # pragma: no cover - protocol
        ...

    def load_all(self) -> List[Dict[str, Any]]:  # pragma: no cover - protocol
        ...

    def count(self) -> int:  # pragma: no cover - protocol
        ...

    def close(self) -> None:  # pragma: no cover - protocol
        ...


class InMemoryBackend:
    """Dict-backed backend: fast, dependency-free, non-persistent.

    Useful for test isolation and ephemeral runs where the SQLite ``:memory:``
    path (which still spins up a sqlite3 connection) is unnecessary.
    """

    def __init__(self) -> None:
        self._rows: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.RLock()

    def persist(self, entry: Dict[str, Any]) -> None:
        with self._lock:
            self._rows[entry["key_hash"]] = dict(entry)

    def delete(self, key_hash: str) -> None:
        with self._lock:
            self._rows.pop(key_hash, None)

    def clear(self) -> None:
        with self._lock:
            self._rows.clear()

    def load_all(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(row) for row in self._rows.values()]

    def count(self) -> int:
        with self._lock:
            return len(self._rows)

    def close(self) -> None:
        with self._lock:
            self._rows.clear()


class SqliteBackend:
    """Adapter exposing the existing ``MemoryStore`` through ``MemoryBackend``.

    A subclass-free wrapper (composition) so the battle-tested ``MemoryStore``
    stays untouched and remains independently importable.
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        self._store = MemoryStore(db_path=db_path)

    def persist(self, entry: Dict[str, Any]) -> None:
        self._store.persist(entry)

    def delete(self, key_hash: str) -> None:
        self._store.delete(key_hash)

    def clear(self) -> None:
        self._store.clear()

    def load_all(self) -> List[Dict[str, Any]]:
        return self._store.load_all()

    def count(self) -> int:
        return self._store.count()

    def close(self) -> None:
        self._store.close()


class BackendUnavailableError(RuntimeError):
    """持久化后端不可用。

    抛出它而不是静默降级到 SQLite / 内存，是本模块的硬纪律：一个悄悄换了存储引擎
    的后端会让"数据到底落在哪"变成不可知，比直接失败危险得多。
    """


#: 与 ``store.py`` 的 SQLite schema **逐列一致**（列名、顺序、可空性都对齐）。
#: 不另造 schema —— 否则切换后端会静默丢字段。
#: 元素为 ``(列名, 类型, 是否可空)``。
MEMORY_COLUMNS: Tuple[Tuple[str, str, bool], ...] = (
    ("key_hash", "TEXT", False),
    ("entry_id", "TEXT", False),
    ("key", "TEXT", False),
    ("value", "TEXT", False),
    ("tier", "TEXT", False),
    ("scope", "TEXT", False),
    ("created_at", "TEXT", False),
    ("expires_at", "TEXT", True),
    ("tags", "TEXT", True),
    ("correlation_id", "TEXT", False),
    ("access_count", "INTEGER", False),
    ("last_accessed", "TEXT", True),
    ("provenance", "TEXT", True),
)

MEMORY_COLUMN_NAMES: Tuple[str, ...] = tuple(name for name, _t, _n in MEMORY_COLUMNS)
TABLE_NAME = "memory_entries"

POSTGRES_URL_ENV = "LIUHAO_POSTGRES_URL"
DATABASE_URL_ENV = "DATABASE_URL"

#: 本后端支持 upsert 的方言（方言不同，UPSERT 语法不同）。
SUPPORTED_DIALECTS = ("postgresql", "sqlite")


def _redact(url: str) -> str:
    """隐去 DSN 口令后再放进错误信息 / 健康输出。

    刻意不去 import ``src.distribution.bus_backends`` 的同名工具：
    ``kernels`` 反向依赖 ``distribution`` 会造成架构倒置。
    """
    try:
        from urllib.parse import urlsplit, urlunsplit

        parts = urlsplit(url)
        if not parts.password:
            return url
        netloc = parts.netloc.replace(f":{parts.password}@", ":***@")
        return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    except Exception:  # pragma: no cover - 畸形输入
        return "<unparsable>"


def postgres_url_from_env() -> Optional[str]:
    """按优先级取 PostgreSQL DSN：``LIUHAO_POSTGRES_URL`` > ``DATABASE_URL``。"""
    for name in (POSTGRES_URL_ENV, DATABASE_URL_ENV):
        raw = (os.environ.get(name) or "").strip()
        if raw:
            return raw
    return None


class SqlAlchemyBackend:
    """SQLAlchemy-Core 持久化后端；**目标方言是 PostgreSQL**。

    同一套 CRUD 实现也支持 ``sqlite://``（用于离线验证共享逻辑）。方言按
    ``engine.dialect.name`` 显式分派；未支持的方言**抛错**，不退化成一个可能
    静默覆盖的裸 ``INSERT``。

    ``engine`` 可注入（测试用）：注入时视为已连接，且 ``close()`` 不会释放它。
    """

    def __init__(
        self,
        url: Optional[str] = None,
        engine: Any = None,
        create_schema: bool = True,
    ) -> None:
        self._url = url
        self._engine = engine
        self._injected_engine = engine is not None
        self._create_schema = create_schema
        self._table = None
        self._metadata = None
        self._lock = threading.RLock()

    # ---------------------------- 生命周期 ----------------------------
    @property
    def dialect(self) -> str:
        return self._engine.dialect.name if self._engine is not None else "<disconnected>"

    def connect(self) -> None:
        """建引擎 + 真连一次（顺带建表）。**失败即抛** :class:`BackendUnavailableError`。"""
        if self._engine is not None:
            return

        if not self._url:
            raise BackendUnavailableError(
                f"未配置 PostgreSQL DSN：请设置 {POSTGRES_URL_ENV} 或 {DATABASE_URL_ENV}。"
            )

        try:
            import sqlalchemy
        except ImportError as exc:  # pragma: no cover - 环境相关
            raise BackendUnavailableError(
                f"sqlalchemy 不可导入（{exc}）：无法启用 SQLAlchemy 记忆后端。"
            ) from exc

        try:
            engine_kwargs: Dict[str, Any] = {}
            if self._url.lower().startswith("sqlite"):
                # 本后端每次操作用一条新连接（engine.begin()/connect()），因此纯内存
                # SQLite 必须共用同一个物理连接，否则建的表在下一个操作里就"消失"了。
                engine_kwargs["connect_args"] = {"check_same_thread": False}
                if ":memory:" in self._url or self._url.rstrip("/") == "sqlite:":
                    from sqlalchemy.pool import StaticPool

                    engine_kwargs["poolclass"] = StaticPool
            self._engine = sqlalchemy.create_engine(self._url, **engine_kwargs)
        except ModuleNotFoundError as exc:
            # create_engine 会在建引擎时导入 DBAPI 驱动。
            # 即便驱动缺失，也要在消息里给出**脱敏后的** DSN —— 既便于排查，
            # 又绝不让密码随异常泄露（与下方连接失败分支保持一致，避免"有驱动时
            # 脱敏、无驱动时不脱敏"这种随环境漂移的脆行为）。
            raise BackendUnavailableError(
                f"缺少 PostgreSQL DBAPI 驱动（{exc}）。SQLAlchemy 需要驱动才能连 PG；"
                f"DSN={_redact(self._url) if self._url else '<未配置>'}；"
                f"请先选定并登记驱动（BSD 许可的 pg8000 合规成本最低），"
                f"详见 docs/POSTGRES-BACKEND-DESIGN.md §4。"
            ) from exc
        except Exception as exc:
            raise BackendUnavailableError(
                f"创建引擎失败（{_redact(self._url)}）：{type(exc).__name__}: {exc}"
            ) from exc

        if self._engine.dialect.name not in SUPPORTED_DIALECTS:
            raise BackendUnavailableError(
                f"不支持的方言 {self._engine.dialect.name!r}；"
                f"仅支持 {list(SUPPORTED_DIALECTS)}（upsert 语法按方言分派）。"
            )

        try:
            with self._engine.begin() as conn:
                if self._create_schema:
                    self._metadata_obj.create_all(conn)
        except Exception as exc:
            engine, self._engine = self._engine, None
            try:
                engine.dispose()
            except Exception:  # noqa: BLE001 - 释放失败不掩盖原始错误
                pass
            raise BackendUnavailableError(
                f"无法连接/建表（{_redact(self._url)}）：{type(exc).__name__}: {exc}"
            ) from exc

    def _ensure_ready(self) -> Any:
        if self._engine is None:
            self.connect()
        return self._engine

    # ---------------------------- schema ----------------------------
    @property
    def _metadata_obj(self):
        if self._metadata is None:
            from sqlalchemy import Column, Index, Integer, MetaData, Table, Text

            metadata = MetaData()
            columns = []
            for name, kind, nullable in MEMORY_COLUMNS:
                if name == "key_hash":
                    columns.append(Column(name, Text, primary_key=True))
                elif kind == "INTEGER":
                    columns.append(Column(name, Integer, nullable=nullable, server_default="0"))
                else:
                    columns.append(Column(name, Text, nullable=nullable))
            table = Table(TABLE_NAME, metadata, *columns)
            Index("idx_memory_tier", table.c.tier)
            Index("idx_memory_scope", table.c.scope)
            Index("idx_memory_key", table.c.key)
            self._metadata, self._table = metadata, table
        return self._metadata

    @property
    def table(self):
        self._metadata_obj
        return self._table

    # ---------------------------- 写 ----------------------------
    def _upsert(self, conn, row: Dict[str, Any]) -> None:
        """按 ``key_hash`` upsert —— 语义与 SQLite 侧逐字段一致。"""
        table = self.table
        dialect = self._ensure_ready().dialect.name
        if dialect == "postgresql":
            from sqlalchemy.dialects.postgresql import insert as dialect_insert
        elif dialect == "sqlite":
            from sqlalchemy.dialects.sqlite import insert as dialect_insert
        else:  # pragma: no cover - connect() 已挡住
            raise BackendUnavailableError(f"方言 {dialect!r} 不支持 upsert")

        stmt = dialect_insert(table).values(**row)
        stmt = stmt.on_conflict_do_update(
            index_elements=[table.c.key_hash],
            set_={name: stmt.excluded[name] for name in MEMORY_COLUMN_NAMES if name != "key_hash"},
        )
        conn.execute(stmt)

    def persist(self, entry: Dict[str, Any]) -> None:
        engine = self._ensure_ready()
        row = {name: entry.get(name) for name in MEMORY_COLUMN_NAMES}
        if row["access_count"] is None:
            row["access_count"] = 0
        with self._lock, engine.begin() as conn:
            self._upsert(conn, row)

    def delete(self, key_hash: str) -> None:
        engine = self._ensure_ready()
        table = self.table
        with self._lock, engine.begin() as conn:
            conn.execute(table.delete().where(table.c.key_hash == key_hash))

    def clear(self) -> None:
        engine = self._ensure_ready()
        table = self.table
        with self._lock, engine.begin() as conn:
            conn.execute(table.delete())

    # ---------------------------- 读 ----------------------------
    def load_all(self) -> List[Dict[str, Any]]:
        engine = self._ensure_ready()
        table = self.table
        stmt = table.select()
        with self._lock, engine.connect() as conn:
            rows = conn.execute(stmt).fetchall()
        out: List[Dict[str, Any]] = []
        for row in rows:
            mapping = row._mapping
            out.append({name: mapping[name] for name in MEMORY_COLUMN_NAMES})
        return out

    def count(self) -> int:
        from sqlalchemy import func, select

        engine = self._ensure_ready()
        table = self.table
        with self._lock, engine.connect() as conn:
            return int(conn.execute(select(func.count()).select_from(table)).scalar_one())

    def close(self) -> None:
        with self._lock:
            engine, self._engine = self._engine, None
            if engine is not None and not self._injected_engine:
                try:
                    engine.dispose()
                except Exception:  # noqa: BLE001 - 释放失败不影响调用方
                    pass

    def health(self) -> Dict[str, Any]:
        """如实报告后端状态（供就绪探针消费），**不抛异常**。"""
        if self._engine is None:
            return {
                "backend": "sqlalchemy",
                "connected": False,
                "url": _redact(self._url) if self._url else None,
                "reason": "尚未连接",
            }
        try:
            with self._engine.connect() as conn:
                conn.exec_driver_sql("SELECT 1")
        except Exception as exc:  # noqa: BLE001 - 健康检查给结论而非抛
            return {
                "backend": "sqlalchemy",
                "connected": False,
                "dialect": self.dialect,
                "url": _redact(self._url) if self._url else None,
                "reason": f"{type(exc).__name__}: {exc}",
            }
        return {
            "backend": "sqlalchemy",
            "connected": True,
            "dialect": self.dialect,
            "url": _redact(self._url) if self._url else None,
        }


def postgres_available(url: Optional[str] = None) -> Tuple[bool, str]:
    """能力探针：当前环境**真的**能连 PostgreSQL 吗？返回 ``(可用, 原因)``，不抛异常。

    分三层如实报告，便于把"未验证"与"已验证"分开：SQLAlchemy 是否可导入 →
    DBAPI 驱动是否存在 → 连接是否可达。
    """
    resolved = url or postgres_url_from_env()
    if not resolved:
        return False, f"未配置 DSN（{POSTGRES_URL_ENV} / {DATABASE_URL_ENV}）"

    try:
        import sqlalchemy
    except ImportError as exc:  # pragma: no cover - 环境相关
        return False, f"sqlalchemy 不可导入：{exc}"

    try:
        engine = sqlalchemy.create_engine(resolved)
    except ModuleNotFoundError as exc:
        return False, f"缺少 DBAPI 驱动：{exc}；DSN={_redact(resolved)}"
    except Exception as exc:
        return False, f"引擎创建失败（{_redact(resolved)}）：{type(exc).__name__}: {exc}"

    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("SELECT 1")
    except Exception as exc:
        return False, f"连接不可达（{_redact(resolved)}）：{type(exc).__name__}: {exc}"
    finally:
        try:
            engine.dispose()
        except Exception:  # noqa: BLE001
            pass
    return True, "ok"


def get_memory_backend(
    spec: Optional[str] = None, db_path: Optional[str] = None
) -> MemoryBackend:
    """Build a memory backend.

    ``spec`` precedence: explicit argument > ``MEMORY_BACKEND`` env > ``"sqlite"``.
    Passing ``None`` for spec with no env always yields the SQLite backend with
    the historical default path resolution, so behaviour is unchanged.
    """
    resolved = (spec or os.environ.get("MEMORY_BACKEND") or "sqlite").strip().lower()

    if resolved == "sqlite":
        return SqliteBackend(db_path=db_path)
    if resolved in ("memory", "in-memory", "inmemory", ":memory:"):
        return InMemoryBackend()
    if resolved in ("postgres", "postgresql"):
        # Phase 7c：真后端。`db_path` 在此语义下是 DSN（与 sqlite 分支把它当路径一致）。
        url = (db_path or postgres_url_from_env() or "").strip()
        if not url:
            raise BackendUnavailableError(
                f"MEMORY_BACKEND=postgres 需要 DSN：设置 {POSTGRES_URL_ENV} 或 "
                f"{DATABASE_URL_ENV}，或显式传入。"
            )
        if not url.lower().startswith(("postgresql", "postgres")):
            # 防"声称 postgres 实际跑 sqlite"的静默谎言。
            raise BackendUnavailableError(
                f"MEMORY_BACKEND=postgres 但 DSN 不是 PostgreSQL：{_redact(url)}。"
                f"若要跑 SQLite 请用 MEMORY_BACKEND=sqlite。"
            )
        backend = SqlAlchemyBackend(url=url)
        # 立即连接：与 sqlite / memory 两个分支一致（SqliteBackend 在构造期就建连），
        # 且让"DSN 写错 / PG 连不上"在**构造时**就响亮失败，而不是拖到首次读写。
        backend.connect()
        return backend
    raise ValueError(f"Unknown memory backend: {resolved!r}")
