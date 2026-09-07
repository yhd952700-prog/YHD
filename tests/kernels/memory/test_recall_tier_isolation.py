"""Memory Kernel — same-key cross-tier isolation (Sprint 2: tier_filter).

These assertions lock in the fix for the dead ``key_hash`` bug: storing the
same key in two tiers must keep the entries independent, and ``recall`` must
honour ``tier_filter`` instead of returning whichever entry it finds first.
"""
from src.kernels.memory import (
    MemoryKernel,
    MemoryScope,
    MemoryTier,
)


def test_same_key_different_tiers_coexist():
    mk = MemoryKernel()
    mk.store("k", "short", tier=MemoryTier.SHORT_TERM, scope=MemoryScope.L1)
    mk.store("k", "long", tier=MemoryTier.LONG_TERM, scope=MemoryScope.L1)
    # Both are stored independently (store keys on tier:value).
    assert mk.stats()["total_entries"] == 2


def test_recall_respects_tier_filter():
    mk = MemoryKernel()
    mk.store("k", "short", tier=MemoryTier.SHORT_TERM, scope=MemoryScope.L1)
    mk.store("k", "long", tier=MemoryTier.LONG_TERM, scope=MemoryScope.L1)
    # Filtering to LONG_TERM must return only the long-term entry.
    e = mk.recall("k", tier_filter=[MemoryTier.LONG_TERM])
    assert e is not None
    assert e.tier is MemoryTier.LONG_TERM
    assert e.value == "long"
    # And the short-term entry is reachable when filtered the other way.
    e2 = mk.recall("k", tier_filter=[MemoryTier.SHORT_TERM])
    assert e2 is not None
    assert e2.tier is MemoryTier.SHORT_TERM
    assert e2.value == "short"


def test_recall_without_filter_returns_some_tier():
    mk = MemoryKernel()
    mk.store("k", "short", tier=MemoryTier.SHORT_TERM, scope=MemoryScope.L1)
    mk.store("k", "long", tier=MemoryTier.LONG_TERM, scope=MemoryScope.L1)
    # Without a filter, recall still succeeds (any tier) and is isolated
    # from unrelated keys.
    e = mk.recall("k")
    assert e is not None
    assert mk.recall("does-not-exist") is None
