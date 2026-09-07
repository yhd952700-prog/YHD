"""Memory Kernel 持久化后端测试。

验证 memory kernel 经 SQLite 落盘后：跨实例（模拟进程重启）恢复、复杂
value 序列化 round-trip、compress 落盘语义、clear 清空、同 key 覆盖。
"""
from __future__ import annotations

from src.kernels.memory import (
    MemoryKernel,
    MemoryScope,
    MemoryTier,
)


def test_persist_and_reload_across_instances(tmp_path):
    """同一 db_path 的两个独立实例共享持久化条目（模拟进程重启）。"""
    db = str(tmp_path / "memory.db")

    k1 = MemoryKernel(db_path=db)
    k1.store(
        "name", "liuhao",
        tier=MemoryTier.PERSISTENT, scope=MemoryScope.L1, tags={"identity"},
    )

    # 第二个实例 = 重启后的新进程。
    k2 = MemoryKernel(db_path=db)
    got = k2.recall("name", scope=MemoryScope.L1)
    assert got is not None
    assert got.value == "liuhao"
    assert got.tier is MemoryTier.PERSISTENT
    assert "identity" in got.tags


def test_value_roundtrip_complex_types(tmp_path):
    """value 为嵌套 dict/list 时应无损 round-trip。"""
    db = str(tmp_path / "memory.db")
    k = MemoryKernel(db_path=db)
    original = {"a": 1, "b": [2, 3], "c": {"d": "e"}}
    k.store("data", original, tier=MemoryTier.LONG_TERM)

    k2 = MemoryKernel(db_path=db)
    got = k2.recall("data")
    assert got.value == original


def test_overwrite_same_key_persists_latest(tmp_path):
    """同一 key 重复 store 应覆盖持久化（upsert），重启后读到最新值。"""
    db = str(tmp_path / "memory.db")
    k = MemoryKernel(db_path=db)
    k.store("k", "first", tier=MemoryTier.MID_TERM)
    k.store("k", "second", tier=MemoryTier.MID_TERM)

    k2 = MemoryKernel(db_path=db)
    assert k2.recall("k").value == "second"


def test_tier_isolation_survives_reload(tmp_path):
    """同 key 不同 tier 各自独立，重启后仍可按 tier 过滤。"""
    db = str(tmp_path / "memory.db")
    k = MemoryKernel(db_path=db)
    k.store("k", "short", tier=MemoryTier.SHORT_TERM)
    k.store("k", "long", tier=MemoryTier.LONG_TERM)

    k2 = MemoryKernel(db_path=db)
    assert k2.recall("k", tier_filter=[MemoryTier.SHORT_TERM]).value == "short"
    assert k2.recall("k", tier_filter=[MemoryTier.LONG_TERM]).value == "long"


def test_compress_persists_target_and_removes_sources(tmp_path):
    """compress 落盘：写入合并后的 target、删除被合并的 source。"""
    db = str(tmp_path / "memory.db")
    k = MemoryKernel(db_path=db)
    k.store("m1", "v1", tier=MemoryTier.SHORT_TERM)
    k.store("m2", "v2", tier=MemoryTier.SHORT_TERM)

    result = k.compress(source_tier=MemoryTier.SHORT_TERM)
    assert result is not None

    k2 = MemoryKernel(db_path=db)
    # 源条目已被合并移除。
    assert k2.recall("m1", tier_filter=[MemoryTier.SHORT_TERM]) is None
    assert k2.recall("m2", tier_filter=[MemoryTier.SHORT_TERM]) is None
    # 合并后的 target 持久化在 MID_TERM。
    assert k2.recall(result.target_key, tier_filter=[MemoryTier.MID_TERM]) is not None


def test_clear_empties_both_memory_and_store(tmp_path):
    """clear 同时清空内存态与持久化后端。"""
    db = str(tmp_path / "memory.db")
    k = MemoryKernel(db_path=db)
    k.store("x", "y", tier=MemoryTier.PERSISTENT)
    assert k.stats()["total_entries"] == 1

    k.clear()
    assert k.stats()["total_entries"] == 0

    k2 = MemoryKernel(db_path=db)
    assert k2.stats()["total_entries"] == 0


def test_in_memory_backend_does_not_persist(tmp_path):
    """``:memory:`` 后端为纯内存，不产生磁盘文件（向后兼容既有单测）。"""
    k = MemoryKernel(db_path=":memory:")
    k.store("volatile", "value", tier=MemoryTier.MID_TERM)
    assert k.stats()["total_entries"] == 1
    # 无持久化副作用：同 db_path 新实例为空。
    k2 = MemoryKernel(db_path=":memory:")
    assert k2.stats()["total_entries"] == 0


def test_corrupt_row_is_skipped_on_load(tmp_path):
    """脏数据不应拖垮 kernel 启动（诚实跳过坏条目）。"""
    db = str(tmp_path / "memory.db")
    k = MemoryKernel(db_path=db)
    k.store("good", "ok", tier=MemoryTier.MID_TERM)
    # 直接向 store 注入一条 value 为非法 JSON 的坏行。
    k._store.persist({
        "key_hash": "mid_term:bad",
        "entry_id": "deadbeef",
        "key": "bad",
        "value": "{not-valid-json",
        "tier": "mid_term",
        "scope": "L1",
        "created_at": "2026-01-01T00:00:00",
        "expires_at": None,
        "tags": "[]",
        "correlation_id": "cccccccc",
        "access_count": 0,
        "last_accessed": None,
        "provenance": None,
    })

    k2 = MemoryKernel(db_path=db)
    # 好条目仍在，坏条目被跳过。
    assert k2.recall("good").value == "ok"
    assert k2.recall("bad") is None
