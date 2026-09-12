"""Memory Kernel unit tests.

Covers: store (with tier- and ttl-derived expiry), recall (with scope
filtering and access counting), scope_filter, stats, and the global
convenience helpers.

Defect-evidence test (``test_defect_*``) asserts scope-required behavior
that is currently violated; marked ``xfail`` and documented.
"""
from datetime import timedelta
from src._time import utc_now

import pytest

from src.kernels.memory import (
    MemoryKernel,
    MemoryScope,
    MemoryTier,
    memory_stats,
    recall_memory,
    store_memory,
)


@pytest.fixture
def mk() -> MemoryKernel:
    return MemoryKernel(db_path=":memory:")


# =====================================================================
# Store
# =====================================================================

class TestStore:
    def test_store_returns_entry(self, mk):
        e = mk.store("k", "v")
        assert e.key == "k"
        assert e.value == "v"
        assert e.tier == MemoryTier.MID_TERM

    def test_store_mid_term_has_expiry(self, mk):
        e = mk.store("k", "v")
        assert e.expires_at is not None
        assert e.expires_at > utc_now()

    def test_store_ttl_overrides_tier_expiry(self, mk):
        e = mk.store("k", "v", ttl=timedelta(minutes=1))
        delta = (e.expires_at - utc_now()).total_seconds()
        assert 50 < delta < 70


# =====================================================================
# Recall & scope
# =====================================================================

class TestRecall:
    def test_recall_returns_stored(self, mk):
        mk.store("k", "v")
        e = mk.recall("k")
        assert e is not None
        assert e.value == "v"

    def test_recall_unknown_returns_none(self, mk):
        assert mk.recall("ghost") is None

    def test_recall_increments_access_count(self, mk):
        mk.store("k", "v")
        assert mk.recall("k").access_count == 1
        assert mk.recall("k").access_count == 2

    def test_recall_allows_higher_entry_for_lower_query(self, mk):
        # Entry at L5 is accessible to a lower query scope (L1).
        mk.store("k", "v", scope=MemoryScope.L5)
        e = mk.recall("k", scope=MemoryScope.L1)
        assert e is not None

    def test_recall_blocks_lower_entry_for_higher_query(self, mk):
        # Entry at L1 is NOT accessible to a higher query scope (L5).
        mk.store("k", "v", scope=MemoryScope.L1)
        e = mk.recall("k", scope=MemoryScope.L5)
        assert e is None


# =====================================================================
# scope_filter
# =====================================================================

class TestScopeFilter:
    def test_filter_by_scope(self, mk):
        mk.store("a", "1", scope=MemoryScope.L1)
        mk.store("b", "2", scope=MemoryScope.L5)
        res = mk.scope_filter(MemoryScope.L3)
        assert {e.key for e in res} == {"b"}

    def test_filter_by_max_access_count(self, mk):
        mk.store("a", "1")
        mk.recall("a")  # access_count = 1
        mk.store("b", "2")
        res = mk.scope_filter(MemoryScope.L0, max_access_count=0)
        assert {e.key for e in res} == {"b"}


# =====================================================================
# Stats
# =====================================================================

class TestStats:
    def test_stats_counts(self, mk):
        mk.store("a", "1", tier=MemoryTier.SHORT_TERM, scope=MemoryScope.L1)
        s = mk.stats()
        assert s["total_entries"] == 1
        assert s["by_tier"]["short_term"] == 1
        assert s["by_scope"]["L1"] == 1
        assert s["total_access_count"] == 0


# =====================================================================
# Global convenience helpers
# =====================================================================

class TestGlobalHelpers:
    def test_store_and_recall_memory(self):
        e = store_memory("gk", "gv", scope=MemoryScope.L1)
        assert e.key == "gk"
        got = recall_memory("gk", scope=MemoryScope.L1)
        assert got is not None
        assert got.value == "gv"

    def test_memory_stats(self):
        s = memory_stats()
        assert "total_entries" in s


# =====================================================================
# Defect
# =====================================================================

class TestDefects:
    def test_defect_recall_respects_tier_filter(self, mk):
        # Store the same key in two tiers with distinct values.
        mk.store("k", "short", tier=MemoryTier.SHORT_TERM)
        mk.store("k", "long", tier=MemoryTier.LONG_TERM)
        e = mk.recall("k", tier_filter=[MemoryTier.LONG_TERM])
        # Required: tier filter should return only the LONG_TERM entry.
        assert e.tier is MemoryTier.LONG_TERM
