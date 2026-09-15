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


# =====================================================================
# TTL enforcement (kernel-spec/memory.md §8 failure-path test 1)
# =====================================================================

class TestTtlEnforcement:
    def test_recall_returns_none_for_expired_entry(self, mk):
        # store with an already-elapsed ttl: recall must not hand it back.
        mk.store("k", "v", ttl=timedelta(seconds=-5))
        assert mk.recall("k") is None

    def test_recall_still_returns_live_entry(self, mk):
        mk.store("k", "v", ttl=timedelta(minutes=5))
        assert mk.recall("k") is not None

    def test_expiry_is_enforced_before_any_cleanup(self, mk):
        # Nothing sweeps here: TTL must hold on read, not only via auto_cleanup.
        mk.store("k", "v", ttl=timedelta(seconds=-1))
        assert mk.stats()["total_entries"] == 1  # still stored ...
        assert mk.recall("k") is None            # ... but not recallable

    def test_scope_filter_excludes_expired(self, mk):
        mk.store("live", "v", ttl=timedelta(minutes=5))
        mk.store("dead", "v", ttl=timedelta(seconds=-5))
        assert {e.key for e in mk.scope_filter(MemoryScope.L0)} == {"live"}

    def test_persistent_tier_never_auto_expires(self, mk):
        # PERSISTENT means "> 30 days"; a 30-day expiry would contradict it.
        e = mk.store("k", "v", tier=MemoryTier.PERSISTENT)
        assert e.expires_at is None
        assert mk.recall("k") is not None


# =====================================================================
# scope_filter age semantics
# =====================================================================

class TestScopeFilterAge:
    def test_min_age_keeps_entries_at_least_that_old(self, mk):
        old = mk.store("old", "v")
        old.created_at = utc_now() - timedelta(days=10)
        mk.store("fresh", "v")
        res = mk.scope_filter(MemoryScope.L0, min_age=timedelta(days=1))
        assert {e.key for e in res} == {"old"}

    def test_max_age_keeps_entries_at_most_that_old(self, mk):
        old = mk.store("old", "v")
        old.created_at = utc_now() - timedelta(days=10)
        mk.store("fresh", "v")
        res = mk.scope_filter(MemoryScope.L0, max_age=timedelta(days=1))
        assert {e.key for e in res} == {"fresh"}


# =====================================================================
# auto_cleanup is real (kernel-spec/memory.md §9: dead tier manager removed)
# =====================================================================

class TestAutoCleanup:
    def test_auto_cleanup_evicts_expired_from_memory_and_store(self, mk):
        mk.store("live", "v", ttl=timedelta(minutes=5))
        mk.store("dead", "v", ttl=timedelta(seconds=-5))
        result = mk.auto_cleanup()
        assert result.get("mid_term", 0) == 1
        assert mk.stats()["total_entries"] == 1
        # The persisted row is gone too, not just the in-memory copy.
        assert mk._store.count() == 1

    def test_auto_cleanup_reports_zero_when_nothing_expired(self, mk):
        mk.store("live", "v", ttl=timedelta(minutes=5))
        assert sum(mk.auto_cleanup().values()) == 0
        assert mk.stats()["total_entries"] == 1

    def test_auto_cleanup_leaves_persistent_entries(self, mk):
        mk.store("p", "v", tier=MemoryTier.PERSISTENT)
        assert mk.auto_cleanup().get("persistent", 0) == 0
        assert mk.recall("p") is not None


class TestDeadCodeRemoval:
    def test_dead_tier_manager_symbols_are_gone(self):
        # Guards against re-introducing the disconnected MemoryTierManager
        # facade that made auto_cleanup a silent no-op.
        import src.kernels.memory as memory_module

        assert not hasattr(memory_module, "MemoryTierManager")
        assert not hasattr(memory_module, "get_tier_manager")
