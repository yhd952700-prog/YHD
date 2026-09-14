"""Phase 4 — Memory backend abstraction + typed memory 测试。

覆盖（离线、无外部依赖）：
- MemoryBackend Protocol 结构化一致（既有 MemoryStore 天然符合）
- get_memory_backend 工厂：默认 sqlite / memory；postgres fail-closed；未知报错
- InMemoryBackend 全接口 roundtrip
- MemoryKernel 默认行为不变（不传 backend，行为与历史逐字节一致）
- MemoryKernel 注入后端可用
- 5 类记忆（MemoryType）约定层 roundtrip + 类型隔离
"""

import pytest

from src.kernels.memory import (
    MemoryKernel,
    MemoryScope,
    MemoryType,
    _TYPE_MAP,
)
from src.kernels.memory.backends import (
    BackendUnavailableError,
    InMemoryBackend,
    MemoryBackend,
    SqliteBackend,
    get_memory_backend,
)
from src.kernels.memory.store import MemoryStore


def test_protocol_conformance():
    """既有 MemoryStore 必须结构性地满足 MemoryBackend（零改动）。"""
    store = MemoryStore(":memory:")
    assert isinstance(store, MemoryBackend)
    store.close()


def test_factory_defaults():
    assert isinstance(get_memory_backend(None, db_path=":memory:"), SqliteBackend)
    assert isinstance(get_memory_backend("sqlite", db_path=":memory:"), SqliteBackend)
    assert isinstance(get_memory_backend("memory"), InMemoryBackend)


def test_factory_fail_closed(monkeypatch):
    # Postgres 未配置 DSN → 诚实抛错（不静默降级到 sqlite/memory）
    #
    # 修复前 vs 之后（Phase 7c，2026-09-14）：
    #   旧断言 `pytest.raises(NotImplementedError)` 是在断言"这个后端还没做" ——
    #   那是对**占位实现**的断言。Phase 7c 把它实现成真后端后，该断言自然失效，
    #   但它想守的业务意图（**fail-closed、绝不静默换存储**）必须保留：
    #   现在没有 DSN 时抛的是 BackendUnavailableError，信息里明确指出该设哪个环境变量。
    #   注意仍然"响亮失败" —— 只是从"计划中"变成"缺配置，请做 X"。
    monkeypatch.delenv("LIUHAO_POSTGRES_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(BackendUnavailableError) as exc:
        get_memory_backend("postgres")
    assert "LIUHAO_POSTGRES_URL" in str(exc.value)

    # 声称 postgres 却给了 sqlite DSN → 必须拒绝（防"静默换引擎"）
    with pytest.raises(BackendUnavailableError):
        get_memory_backend("postgres", "sqlite://")

    # 未知后端 → 明确报错
    with pytest.raises(ValueError):
        get_memory_backend("nonsense-backend")


def test_inmemory_backend_roundtrip():
    be = InMemoryBackend()
    assert be.count() == 0
    be.persist({"key_hash": "mid_term:k1", "key": "k1", "value": "v1"})
    be.persist({"key_hash": "mid_term:k2", "key": "k2", "value": "v2"})
    assert be.count() == 2
    rows = be.load_all()
    assert {r["key_hash"] for r in rows} == {"mid_term:k1", "mid_term:k2"}
    # upsert by key_hash (覆盖式)
    be.persist({"key_hash": "mid_term:k1", "key": "k1", "value": "v1b"})
    assert be.count() == 2
    be.delete("mid_term:k2")
    assert be.count() == 1
    be.clear()
    assert be.count() == 0
    be.close()


def test_kernel_default_unchanged():
    """不传 backend：行为与历史一致（SQLite 路径）。"""
    mk = MemoryKernel(db_path=":memory:")
    mk.store("k", {"v": 1}, scope=MemoryScope.L1)
    got = mk.recall("k", scope=MemoryScope.L1)
    assert got is not None and got.value == {"v": 1}


def test_kernel_with_injected_backend():
    be = InMemoryBackend()
    mk = MemoryKernel(backend=be)
    mk.store("k", {"v": 2}, scope=MemoryScope.L1)
    # 写入直接落到注入的后端
    assert be.count() == 1
    assert mk.recall("k", scope=MemoryScope.L1).value == {"v": 2}


def test_typed_memory_roundtrip():
    mk = MemoryKernel(backend=InMemoryBackend())
    e = mk.remember(
        MemoryType.EPISODIC, "run:1", {"goal": "g1"}, scope=MemoryScope.L1
    )
    assert "episodic" in e.tags
    # 类型内命中
    hit = mk.recall_type(MemoryType.EPISODIC, "run:1", scope=MemoryScope.L1)
    assert hit is not None and hit.value == {"goal": "g1"}
    # 类型隔离：别的类型（不同 tier）取不到同 key
    assert mk.recall_type(MemoryType.SEMANTIC, "run:1", scope=MemoryScope.L1) is None


def test_type_map_is_complete_and_valid():
    for mt in MemoryType:
        tier, tag = _TYPE_MAP[mt]
        assert isinstance(tag, str) and tag
        assert tier is not None
