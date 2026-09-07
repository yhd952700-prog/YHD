"""Memory Kernel compression (tier consolidation) unit tests.

Covers ``MemoryKernel.compress``: cross-tier promotion with provenance,
lossless default summarization, injectable custom summarizer, scope safety
(no cross-scope merge), and the honest no-op cases (empty tier, terminal tier).
"""
from datetime import datetime, timedelta

import pytest

from src.kernels.memory import (
    MemoryKernel,
    MemoryScope,
    MemoryTier,
    compress_memory,
)


@pytest.fixture
def mk() -> MemoryKernel:
    return MemoryKernel()


# =====================================================================
# Consolidation happy path
# =====================================================================

class TestCompress:
    def test_compress_promotes_short_to_mid_and_removes_sources(self, mk):
        mk.store("a", "1", tier=MemoryTier.SHORT_TERM)
        mk.store("b", "2", tier=MemoryTier.SHORT_TERM)
        mk.store("c", "3", tier=MemoryTier.SHORT_TERM)

        result = mk.compress(source_tier=MemoryTier.SHORT_TERM)

        assert result is not None
        assert result.source_tier is MemoryTier.SHORT_TERM
        assert result.target_tier is MemoryTier.MID_TERM
        assert result.source_count == 3
        assert len(result.source_ids) == 3

        # Source entries are gone; target is retrievable at MID_TERM.
        assert mk.recall("a") is None
        assert mk.recall("b") is None
        assert mk.recall("c") is None
        target = mk.recall(result.target_key, tier_filter=MemoryTier.MID_TERM)
        assert target is not None
        assert target.tier is MemoryTier.MID_TERM

    def test_default_summarizer_is_lossless(self, mk):
        mk.store("a", "1", tier=MemoryTier.SHORT_TERM, tags={"x"})
        mk.store("b", "2", tier=MemoryTier.SHORT_TERM, tags={"y"})

        result = mk.compress(source_tier=MemoryTier.SHORT_TERM)

        assert result.value["kind"] == "consolidation"
        assert result.value["count"] == 2
        keys = {item["key"] for item in result.value["items"]}
        assert keys == {"a", "b"}

    def test_custom_summarizer_is_used(self, mk):
        mk.store("a", "hello", tier=MemoryTier.SHORT_TERM)
        mk.store("b", "world", tier=MemoryTier.SHORT_TERM)

        def summarizer(entries):
            return " ".join(e.value for e in entries)

        result = mk.compress(
            source_tier=MemoryTier.SHORT_TERM,
            summarizer=summarizer,
        )

        assert result.value in ("hello world", "world hello")

    def test_provenance_recorded_on_target(self, mk):
        e = mk.store("a", "1", tier=MemoryTier.SHORT_TERM)
        result = mk.compress(source_tier=MemoryTier.SHORT_TERM)

        target = mk.recall(result.target_key, tier_filter=MemoryTier.MID_TERM)
        assert target.provenance is not None
        assert target.provenance["consolidated_from"] == [e.id]
        assert target.provenance["source_tier"] == "short_term"
        assert target.provenance["count"] == 1


# =====================================================================
# Scope safety
# =====================================================================

class TestCompressScope:
    def test_compress_only_merges_same_scope(self, mk):
        mk.store("lo", "1", tier=MemoryTier.SHORT_TERM, scope=MemoryScope.L1)
        mk.store("hi", "2", tier=MemoryTier.SHORT_TERM, scope=MemoryScope.L5)

        result = mk.compress(source_tier=MemoryTier.SHORT_TERM, scope=MemoryScope.L1)

        assert result is not None
        assert result.source_count == 1  # only the L1 entry
        # The L5 entry must remain untouched (no cross-scope leak).
        assert mk.recall("hi", scope=MemoryScope.L5) is not None
        assert mk.recall("lo") is None

    def test_compress_higher_scope_does_not_touch_lower(self, mk):
        mk.store("lo", "1", tier=MemoryTier.SHORT_TERM, scope=MemoryScope.L1)
        mk.store("hi", "2", tier=MemoryTier.SHORT_TERM, scope=MemoryScope.L5)

        result = mk.compress(source_tier=MemoryTier.SHORT_TERM, scope=MemoryScope.L5)

        assert result is not None
        assert result.source_count == 1  # only the L5 entry
        assert mk.recall("lo", scope=MemoryScope.L1) is not None


# =====================================================================
# Honest no-op / boundary cases
# =====================================================================

class TestCompressBoundary:
    def test_empty_tier_returns_none(self, mk):
        assert mk.compress(source_tier=MemoryTier.SHORT_TERM) is None

    def test_persistent_has_nowhere_to_promote(self, mk):
        mk.store("a", "1", tier=MemoryTier.PERSISTENT)
        assert mk.compress(source_tier=MemoryTier.PERSISTENT) is None
        # Entry still present (compress must not mutate on no-op).
        assert mk.recall("a", tier_filter=MemoryTier.PERSISTENT) is not None

    def test_max_entries_caps_consolidation(self, mk):
        for i in range(5):
            mk.store(f"k{i}", str(i), tier=MemoryTier.SHORT_TERM)

        result = mk.compress(source_tier=MemoryTier.SHORT_TERM, max_entries=2)

        assert result.source_count == 2
        # The other 3 remain in short-term.
        remaining = [e for e in mk.scope_filter(MemoryScope.L1) if e.tier is MemoryTier.SHORT_TERM]
        assert len(remaining) == 3

    def test_older_than_filter(self, mk):
        old = mk.store("old", "1", tier=MemoryTier.SHORT_TERM)
        old.created_at = datetime.utcnow() - timedelta(hours=2)
        mk.store("fresh", "2", tier=MemoryTier.SHORT_TERM)

        result = mk.compress(source_tier=MemoryTier.SHORT_TERM, older_than=timedelta(hours=1))

        assert result.source_count == 1
        assert result.source_ids == [old.id]
        assert mk.recall("fresh") is not None

    def test_explicit_target_tier(self, mk):
        mk.store("a", "1", tier=MemoryTier.MID_TERM)
        result = mk.compress(
            source_tier=MemoryTier.MID_TERM,
            target_tier=MemoryTier.LONG_TERM,
        )

        assert result is not None
        assert result.target_tier is MemoryTier.LONG_TERM
        assert mk.recall("a", tier_filter=MemoryTier.MID_TERM) is None
        assert mk.recall(result.target_key, tier_filter=MemoryTier.LONG_TERM) is not None


# =====================================================================
# Global convenience helper
# =====================================================================

class TestCompressHelper:
    def test_compress_memory_helper(self):
        from src.kernels.memory import store_memory, recall_memory

        store_memory("gk", "gv", tier=MemoryTier.SHORT_TERM, scope=MemoryScope.L1)
        result = compress_memory(source_tier=MemoryTier.SHORT_TERM)

        assert result is not None
        assert result.source_count == 1
        assert recall_memory("gk", scope=MemoryScope.L1) is None
