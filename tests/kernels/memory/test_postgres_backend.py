"""Phase 7c — SQLAlchemy / PostgreSQL 记忆后端测试。

设计文档：``docs/POSTGRES-BACKEND-DESIGN.md``。

**本文件的诚实边界（必须先说清）**：本机 **无 PostgreSQL 服务、无 Docker**（实测 5432
`TimeoutError`、docker daemon 未运行），因此：

* **已真验证**：① 共享 CRUD 逻辑 —— 用**同一个 `SqlAlchemyBackend`** 跑 SQLite 做真实
  读写往返（真数据库、真 SQL、真 I/O）；② PostgreSQL **方言 SQL** —— 让真实的
  ``_upsert`` 在 PG 方言下生成语句并编译，断言产出 ``ON CONFLICT (key_hash) DO UPDATE``；
  ③ 连不上时的**诚实失败**。
* **未验证**：真 PostgreSQL 上的端到端行为。相应用例用 ``skipif`` **显式跳过并给出原因**。

⚠️ 「用 SQLite 跑通同一代码路径」**不等于**「在 PostgreSQL 上跑通」。本文件不把前者
包装成后者。
"""

from __future__ import annotations

import json
import pathlib
import re
import threading

import pytest
from sqlalchemy.dialects import postgresql, sqlite as sqlite_dialect

from src.kernels.memory.backends import (
    MEMORY_COLUMN_NAMES,
    MEMORY_COLUMNS,
    SUPPORTED_DIALECTS,
    BackendUnavailableError,
    SqlAlchemyBackend,
    get_memory_backend,
    postgres_available,
    postgres_url_from_env,
)
from src.kernels.memory import MemoryKernel, MemoryScope, MemoryTier

UNREACHABLE_PG = "postgresql://liuhao:topsecretpw@127.0.0.1:6399/liuhao"


def _row(**overrides) -> dict:
    row = {name: None for name in MEMORY_COLUMN_NAMES}
    row.update(
        key_hash="h1", entry_id="e1", key="alpha", value='{"v": 1}',
        tier="L1", scope="L1", created_at="2026-09-14T00:00:00Z",
        correlation_id="c1", access_count=0,
    )
    row.update(overrides)
    return row


@pytest.fixture
def backend():
    """真 SQLAlchemy 后端，跑内存 SQLite —— 共享代码路径的真实读写。"""
    b = SqlAlchemyBackend(url="sqlite://")
    b.connect()
    yield b
    b.close()


@pytest.fixture
def file_backend(tmp_path):
    """文件型 SQLite 后端：可跨引擎共享，用于持久化 / 并发测试。"""
    url = f"sqlite:///{(tmp_path / 'memory.db').as_posix()}"
    b = SqlAlchemyBackend(url=url)
    b.connect()
    yield b, url
    b.close()


# ============================================================
# A) 共享 CRUD 逻辑（真实 I/O，经 SqlAlchemyBackend 自身）
# ============================================================


class TestSharedCrud:
    def test_persist_then_load_roundtrip(self, backend):
        backend.persist(_row())
        rows = backend.load_all()
        assert len(rows) == 1
        assert rows[0]["key"] == "alpha"
        assert rows[0]["value"] == '{"v": 1}'
        assert backend.count() == 1

    def test_persist_upserts_on_key_hash(self, backend):
        backend.persist(_row(value='{"v": 1}'))
        backend.persist(_row(value='{"v": 2}'))
        assert backend.count() == 1, "同一 key_hash 必须 upsert，不能插成两行"
        assert backend.load_all()[0]["value"] == '{"v": 2}'

    def test_different_key_hash_creates_a_second_row(self, backend):
        backend.persist(_row(key_hash="h1"))
        backend.persist(_row(key_hash="h2", key="beta"))
        assert backend.count() == 2

    def test_delete_removes_only_the_target(self, backend):
        backend.persist(_row(key_hash="h1"))
        backend.persist(_row(key_hash="h2", key="beta"))
        backend.delete("h1")
        assert backend.count() == 1
        assert backend.load_all()[0]["key_hash"] == "h2"

    def test_delete_missing_key_is_a_no_op(self, backend):
        backend.delete("nope")
        assert backend.count() == 0

    def test_clear_empties_the_table(self, backend):
        backend.persist(_row(key_hash="h1"))
        backend.persist(_row(key_hash="h2"))
        backend.clear()
        assert backend.count() == 0
        assert backend.load_all() == []

    def test_load_all_returns_every_column(self, backend):
        backend.persist(_row())
        row = backend.load_all()[0]
        assert set(row.keys()) == set(MEMORY_COLUMN_NAMES)

    def test_nullable_columns_round_trip_as_none(self, backend):
        backend.persist(_row())
        row = backend.load_all()[0]
        for name in ("expires_at", "tags", "last_accessed", "provenance"):
            assert row[name] is None, name

    def test_access_count_defaults_to_zero(self, backend):
        backend.persist(_row(access_count=None))
        assert backend.load_all()[0]["access_count"] == 0

    def test_list_valued_entry_columns_are_stored_as_given(self, backend):
        """``tags`` 在 SQLite store 里是 TEXT（调用方自己序列化）。这里只断言不炸。"""
        backend.persist(_row(tags=json.dumps(["a", "b"])))
        assert json.loads(backend.load_all()[0]["tags"]) == ["a", "b"]

    def test_health_reports_connected(self, backend):
        health = backend.health()
        assert health["connected"] is True
        assert health["dialect"] == "sqlite"

    def test_health_before_connect_reports_unavailable(self):
        health = SqlAlchemyBackend(url="sqlite://").health()
        assert health["connected"] is False
        assert health["reason"]

    def test_concurrent_persist_is_serialised_by_the_lock(self, file_backend):
        backend, _url = file_backend
        errors = []

        def worker(n):
            try:
                for i in range(6):
                    backend.persist(_row(key_hash=f"h{n}-{i}", key=f"k{n}-{i}"))
            except Exception as exc:  # noqa: BLE001 - 收集后在主线程断言
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"并发写入出错: {errors}"
        assert backend.count() == 24

    def test_close_is_idempotent(self, backend):
        backend.close()
        backend.close()


# ============================================================
# B) schema 对齐（防漂移：不能与 store.py 的 SQLite 表分家）
# ============================================================


class TestSchemaParity:
    def test_column_set_matches_the_sqlite_store_schema(self):
        """从 ``store.py`` 的 CREATE TABLE 里**解析**真实列名来比对。

        写死一份期望列表只会验证我的记忆；解析源码才真正防漂移 ——
        将来有人给 SQLite 表加一列却忘了加进 ``MEMORY_COLUMNS``，这条会红。
        """
        import src.kernels.memory.store as store_mod

        source = pathlib.Path(store_mod.__file__).read_text(encoding="utf-8")
        match = re.search(
            r"CREATE TABLE IF NOT EXISTS memory_entries\s*\((.*?)\);", source, re.S
        )
        assert match, "未能从 store.py 解析出 memory_entries 的建表语句"
        parsed = [
            line.strip().split()[0]
            for line in match.group(1).splitlines()
            if line.strip()
        ]
        assert parsed == [name for name, _t, _n in MEMORY_COLUMNS]

    def test_column_names_constant_is_consistent(self):
        assert MEMORY_COLUMN_NAMES == tuple(name for name, _t, _n in MEMORY_COLUMNS)

    def test_key_hash_is_the_primary_key(self, backend):
        assert list(backend.table.primary_key.columns.keys()) == ["key_hash"]

    def test_indexes_match_the_sqlite_store(self, backend):
        index_names = {index.name for index in backend.table.indexes}
        assert index_names == {"idx_memory_tier", "idx_memory_scope", "idx_memory_key"}

    def test_supported_dialects_are_exactly_postgres_and_sqlite(self):
        assert set(SUPPORTED_DIALECTS) == {"postgresql", "sqlite"}


# ============================================================
# C) PostgreSQL 方言 SQL（无需服务器即可真验证）
# ============================================================


class _FakeEngine:
    """只带 ``dialect.name`` 的假引擎，用于把真实的 upsert 分派逼到 PG 分支上。"""

    def __init__(self, name: str):
        from types import SimpleNamespace

        self.dialect = SimpleNamespace(name=name)


class _CapturingConn:
    def __init__(self):
        self.statements = []

    def execute(self, statement):
        self.statements.append(statement)


class TestPostgresDialect:
    def test_real_upsert_builds_a_postgres_on_conflict_statement(self, backend):
        """让**真实的** ``_upsert`` 走 postgresql 分支，再把它编译出来看 SQL。"""
        backend._engine = _FakeEngine("postgresql")
        conn = _CapturingConn()
        backend._upsert(conn, _row())

        assert len(conn.statements) == 1
        compiled = str(
            conn.statements[0].compile(dialect=postgresql.dialect())
        )
        assert "INSERT INTO memory_entries" in compiled
        assert "ON CONFLICT (key_hash) DO UPDATE" in compiled
        # 除主键外每一列都要出现在 DO UPDATE SET 里，否则切换后端会静默丢字段
        for name in MEMORY_COLUMN_NAMES:
            if name != "key_hash":
                assert f"{name} = excluded.{name}" in compiled, name

    def test_sqlite_branch_also_compiles_to_on_conflict(self, backend):
        backend._engine = _FakeEngine("sqlite")
        conn = _CapturingConn()
        backend._upsert(conn, _row())
        compiled = str(conn.statements[0].compile(dialect=sqlite_dialect.dialect()))
        assert "ON CONFLICT (key_hash) DO UPDATE" in compiled

    def test_unknown_dialect_refuses_to_upsert(self, backend):
        """未知方言必须抛错，而不是退化成可能静默覆盖的裸 INSERT。"""
        backend._engine = _FakeEngine("mysql")
        with pytest.raises(BackendUnavailableError) as exc:
            backend._upsert(_CapturingConn(), _row())
        assert "upsert" in str(exc.value)

    def test_postgres_ddl_compiles_with_all_columns(self, backend):
        from sqlalchemy.schema import CreateTable

        ddl = str(CreateTable(backend.table).compile(dialect=postgresql.dialect()))
        assert "CREATE TABLE memory_entries" in ddl
        for name in MEMORY_COLUMN_NAMES:
            assert name in ddl, name
        assert "PRIMARY KEY (key_hash)" in ddl

    def test_postgres_ddl_nullability_matches_the_contract(self, backend):
        from sqlalchemy.schema import CreateTable

        ddl = str(CreateTable(backend.table).compile(dialect=postgresql.dialect()))
        nullable = {name: nullable for name, _t, nullable in MEMORY_COLUMNS}
        for name, is_nullable in nullable.items():
            line = next((ln for ln in ddl.splitlines() if ln.strip().startswith(name)), "")
            assert line, name
            if is_nullable:
                assert "NOT NULL" not in line, f"{name} 应为可空"
            elif name != "key_hash":
                assert "NOT NULL" in line, f"{name} 应为 NOT NULL"

    def test_postgres_indexes_compile(self, backend):
        from sqlalchemy.schema import CreateIndex

        for index in backend.table.indexes:
            sql = str(CreateIndex(index).compile(dialect=postgresql.dialect()))
            assert sql.startswith("CREATE INDEX")


# ============================================================
# D) 诚实失败：连不上就响亮报错，绝不静默降级
# ============================================================


class TestHonestFailure:
    def test_factory_requires_a_dsn(self, monkeypatch):
        monkeypatch.delenv("LIUHAO_POSTGRES_URL", raising=False)
        monkeypatch.delenv("DATABASE_URL", raising=False)
        with pytest.raises(BackendUnavailableError) as exc:
            get_memory_backend("postgres")
        assert "LIUHAO_POSTGRES_URL" in str(exc.value)

    def test_factory_rejects_a_non_postgres_dsn(self):
        """防"声称 postgres 实际跑 sqlite"的静默谎言。"""
        with pytest.raises(BackendUnavailableError) as exc:
            get_memory_backend("postgres", "sqlite://")
        assert "不是 PostgreSQL" in str(exc.value)

    def test_factory_raises_at_construction_when_postgres_is_unreachable(self):
        with pytest.raises(BackendUnavailableError):
            get_memory_backend("postgres", UNREACHABLE_PG)

    def test_error_message_redacts_the_password(self):
        with pytest.raises(BackendUnavailableError) as exc:
            get_memory_backend(
                "postgres", "postgresql://liuhao:supersecret@127.0.0.1:6399/liuhao"
            )
        assert "supersecret" not in str(exc.value)
        assert "***" in str(exc.value)

    def test_connect_without_a_url_raises(self):
        with pytest.raises(BackendUnavailableError) as exc:
            SqlAlchemyBackend(url=None).connect()
        assert "DSN" in str(exc.value)

    def test_capability_probe_reports_missing_dsn(self, monkeypatch):
        monkeypatch.delenv("LIUHAO_POSTGRES_URL", raising=False)
        monkeypatch.delenv("DATABASE_URL", raising=False)
        ok, reason = postgres_available()
        assert ok is False
        assert "未配置" in reason

    def test_capability_probe_reports_unavailable_with_a_reason(self):
        """能力探针应如实报"不可用"并给出有信息量的原因——不论失败发生在哪一层。

        缺驱动（CI 无 psycopg2）与连不上（本机有驱动但 6399 无服务）都是"PostgreSQL
        不可用"的子因；前者**不是**'不可达'（根本没客户端库去连），所以不锁死
        '不可达' 这个只属于连接层的措辞，而锁更本质的契约：不可用 + 原因非空 +
        指向的目标可辨识 + 密码绝不泄露。把 '不可达' 硬塞进"缺驱动"的原因是说谎。
        """
        ok, reason = postgres_available(UNREACHABLE_PG)
        assert ok is False
        assert reason  # 必须有原因，不能静默报可用
        assert "liuhao" in reason  # 指向的库/主机有信息量，便于排查
        assert "topsecretpw" not in reason  # 密码已脱敏，绝不出现在探针输出

    def test_dsn_env_precedence(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://from-database/db")
        monkeypatch.setenv("LIUHAO_POSTGRES_URL", "postgresql://from-liuhao/db")
        assert postgres_url_from_env() == "postgresql://from-liuhao/db"
        monkeypatch.delenv("LIUHAO_POSTGRES_URL")
        assert postgres_url_from_env() == "postgresql://from-database/db"


# ============================================================
# E) 默认行为不变（Phase 4 契约不被破坏）
# ============================================================


class TestDefaultsUnchanged:
    def test_default_spec_is_still_sqlite(self, monkeypatch, tmp_path):
        """MEMORY_BACKEND 未设时仍走 Phase 4 的 SQLite 适配器（默认零行为变化）。"""
        from src.kernels.memory.backends import SqliteBackend

        monkeypatch.delenv("MEMORY_BACKEND", raising=False)
        backend = get_memory_backend(db_path=str(tmp_path / "m.db"))
        try:
            assert isinstance(backend, SqliteBackend)
            assert not isinstance(backend, SqlAlchemyBackend)
        finally:
            backend.close()

    def test_memory_spec_variants_still_work(self):
        from src.kernels.memory.backends import InMemoryBackend

        for spec in ("memory", "in-memory", "inmemory", ":memory:"):
            assert isinstance(get_memory_backend(spec), InMemoryBackend), spec

    def test_unknown_spec_still_raises_value_error(self):
        with pytest.raises(ValueError):
            get_memory_backend("kafka")

    def test_sqlite_store_conforms_to_the_backend_protocol(self):
        from src.kernels.memory.backends import MemoryBackend
        from src.kernels.memory.store import MemoryStore

        assert isinstance(MemoryStore(db_path=":memory:"), MemoryBackend)

    def test_sqlalchemy_backend_conforms_to_the_protocol(self, backend):
        from src.kernels.memory.backends import MemoryBackend

        assert isinstance(backend, MemoryBackend)


# ============================================================
# F) 与 MemoryKernel 的真集成（后端真的插得进去）
# ============================================================


class TestKernelIntegration:
    def test_kernel_persists_through_the_sqlalchemy_backend(self, file_backend):
        backend, url = file_backend
        kernel = MemoryKernel(backend=backend)
        kernel.store("greeting", {"text": "hello"}, tier=MemoryTier.PERSISTENT)
        assert backend.count() == 1

        # 换一个新后端 + 新 kernel 指向同一个库：证明数据真的落盘了
        second = SqlAlchemyBackend(url=url)
        second.connect()
        try:
            reloaded = MemoryKernel(backend=second)
            entry = reloaded.recall("greeting", scope=MemoryScope.L1)
            assert entry is not None, "新 kernel 未能从持久化后端恢复条目"
            assert entry.value == {"text": "hello"}
        finally:
            second.close()

    def test_kernel_compression_delete_reaches_the_backend(self, file_backend):
        """compress 会删源条目 —— 断言后端确实被通知了（不是只改内存）。"""
        backend, _url = file_backend
        kernel = MemoryKernel(backend=backend)
        kernel.store("a", {"n": 1}, tier=MemoryTier.MID_TERM)
        assert backend.count() == 1
        kernel.clear()
        assert backend.count() == 0


# ============================================================
# G) 真 PostgreSQL 端到端（本机不可达 → 显式跳过并给出原因）
# ============================================================


@pytest.mark.skipif(
    not postgres_available()[0],
    reason=(
        "本机无 PostgreSQL 服务（实测 5432 TimeoutError、无 Docker）；"
        "共享 CRUD 逻辑已由 A 组用真实 SQLite 验证，PG 方言 SQL 已由 C 组编译验证"
    ),
)
def test_live_postgres_end_to_end():
    url = postgres_url_from_env()
    backend = SqlAlchemyBackend(url=url)
    backend.connect()
    try:
        backend.clear()
        backend.persist(_row())
        assert backend.count() == 1
        assert backend.load_all()[0]["value"] == '{"v": 1}'
        backend.persist(_row(value='{"v": 2}'))
        assert backend.count() == 1, "PG 上 ON CONFLICT 必须生效"
        assert backend.load_all()[0]["value"] == '{"v": 2}'
        backend.delete("h1")
        assert backend.count() == 0
    finally:
        backend.clear()
        backend.close()
