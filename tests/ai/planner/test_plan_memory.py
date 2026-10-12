"""Tests for the PlanMemory module."""
from __future__ import annotations

import json
import os
import tempfile
import pytest

from src.ai.planner.plan_memory import PlanMemory, PlanRecord


class TestPlanRecord:
    """Test the PlanRecord dataclass."""

    def test_success_rate_no_uses(self):
        """A never-used plan has 0% success rate."""
        record = PlanRecord(
            goal_text="test",
            goal_hash="abc123",
            plan_data={},
        )
        assert record.total_uses == 0
        assert record.success_rate == 0.0

    def test_success_rate_all_success(self):
        """All-success plan has 100% success rate."""
        record = PlanRecord(
            goal_text="test",
            goal_hash="abc123",
            plan_data={},
            success_count=5,
            failure_count=0,
        )
        assert record.total_uses == 5
        assert record.success_rate == 1.0

    def test_success_rate_mixed(self):
        """Mixed results: 3/5 = 60%."""
        record = PlanRecord(
            goal_text="test",
            goal_hash="abc123",
            plan_data={},
            success_count=3,
            failure_count=2,
        )
        assert record.total_uses == 5
        assert abs(record.success_rate - 0.6) < 0.001

    def test_to_dict_and_back(self):
        """PlanRecord should round-trip through to_dict/from_dict."""
        original = PlanRecord(
            goal_text="create a file",
            goal_hash="abc123",
            plan_data={"tasks": [{"id": "t1", "name": "Write"}]},
            strategy="llm",
            success_count=3,
            failure_count=1,
            avg_duration_ms=150.5,
            last_used=12345.0,
            created_at=10000.0,
            tags=["file", "write"],
            scope="L2",
        )

        d = original.to_dict()
        restored = PlanRecord.from_dict(d)

        assert restored.goal_text == original.goal_text
        assert restored.goal_hash == original.goal_hash
        assert restored.plan_data == original.plan_data
        assert restored.strategy == original.strategy
        assert restored.success_count == original.success_count
        assert restored.failure_count == original.failure_count
        assert restored.avg_duration_ms == original.avg_duration_ms
        assert restored.tags == original.tags
        assert restored.scope == original.scope


class TestPlanMemory:
    """Test the PlanMemory class."""

    def test_store_and_lookup_exact(self):
        """Store a plan and look it up by exact goal text."""
        memory = PlanMemory()
        plan_data = {"tasks": [{"id": "t1", "name": "Test"}]}

        memory.store("create a test file", plan_data, strategy="llm")

        result = memory.lookup("create a test file")
        assert result is not None
        assert result.plan_data == plan_data
        assert result.strategy == "llm"

    def test_lookup_not_found(self):
        """Lookup for an unknown goal should return None."""
        memory = PlanMemory()
        result = memory.lookup("nonexistent goal")
        assert result is None

    def test_lookup_scope_mismatch(self):
        """Lookup with wrong scope should return None."""
        memory = PlanMemory()
        memory.store("test", {"tasks": []}, scope="L2")

        result = memory.lookup("test", scope="L1")
        assert result is None

    def test_lookup_scope_match(self):
        """Lookup with matching scope should return the plan."""
        memory = PlanMemory()
        memory.store("test", {"tasks": []}, scope="L2")

        result = memory.lookup("test", scope="L2")
        assert result is not None

    def test_store_updates_existing(self):
        """Storing the same goal again should update the existing record."""
        memory = PlanMemory()
        plan_data_1 = {"tasks": [{"id": "t1"}]}
        plan_data_2 = {"tasks": [{"id": "t1"}, {"id": "t2"}]}

        memory.store("test goal", plan_data_1, strategy="rule")
        first = memory.lookup("test goal")
        assert first is not None
        assert len(first.plan_data["tasks"]) == 1

        memory.store("test goal", plan_data_2, strategy="llm")
        second = memory.lookup("test goal")
        assert second is not None
        assert len(second.plan_data["tasks"]) == 2
        assert second.strategy == "llm"

    def test_record_result_success(self):
        """Recording success should increment success_count."""
        memory = PlanMemory()
        memory.store("test", {"tasks": []})

        result = memory.record_result("test", success=True, duration_ms=100.0)
        assert result is not None
        assert result.success_count == 1
        assert result.failure_count == 0
        assert result.avg_duration_ms > 0

    def test_record_result_failure(self):
        """Recording failure should increment failure_count."""
        memory = PlanMemory()
        memory.store("test", {"tasks": []})

        result = memory.record_result("test", success=False)
        assert result is not None
        assert result.success_count == 0
        assert result.failure_count == 1

    def test_record_result_unknown_goal(self):
        """Recording result for unknown goal returns None."""
        memory = PlanMemory()
        result = memory.record_result("unknown", success=True)
        assert result is None

    def test_min_success_rate_filter(self):
        """Plans below min_success_rate should not be returned."""
        memory = PlanMemory(min_success_rate=0.8)
        memory.store("good plan", {"tasks": []})
        memory.store("bad plan", {"tasks": []})

        # Good plan: 9/10 = 90%
        for _ in range(9):
            memory.record_result("good plan", success=True)
        memory.record_result("good plan", success=False)

        # Bad plan: 2/10 = 20%
        for _ in range(2):
            memory.record_result("bad plan", success=True)
        for _ in range(8):
            memory.record_result("bad plan", success=False)

        assert memory.lookup("good plan") is not None
        assert memory.lookup("bad plan") is None

    def test_lru_eviction(self):
        """Oldest plans should be evicted when max_size is reached."""
        memory = PlanMemory(max_size=3)

        memory.store("goal 1", {"tasks": []})
        memory.store("goal 2", {"tasks": []})
        memory.store("goal 3", {"tasks": []})
        assert memory.size == 3

        # Access goal 1 to make it recently used
        memory.lookup("goal 1")

        # Add goal 4 — should evict goal 2 (oldest)
        memory.store("goal 4", {"tasks": []})
        assert memory.size == 3
        assert memory.lookup("goal 2") is None
        assert memory.lookup("goal 1") is not None  # Was accessed recently
        assert memory.lookup("goal 3") is not None
        assert memory.lookup("goal 4") is not None

    def test_size_property(self):
        """size should reflect the number of stored plans."""
        memory = PlanMemory()
        assert memory.size == 0

        memory.store("a", {})
        assert memory.size == 1

        memory.store("b", {})
        assert memory.size == 2

    def test_stats(self):
        """stats() should return memory statistics."""
        memory = PlanMemory()
        memory.store("g1", {})
        memory.store("g2", {})
        memory.record_result("g1", success=True)
        memory.record_result("g1", success=False)

        stats = memory.stats()
        assert stats["size"] == 2
        assert stats["total_executions"] == 2
        assert stats["total_success"] == 1
        assert stats["total_failure"] == 1

    def test_clear(self):
        """clear() should remove all plans."""
        memory = PlanMemory()
        memory.store("a", {})
        memory.store("b", {})
        assert memory.size == 2

        memory.clear()
        assert memory.size == 0

    def test_persistence_roundtrip(self):
        """Memory should persist and load correctly from disk."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            path = f.name

        try:
            # Store and persist
            memory1 = PlanMemory(persist_path=path)
            memory1.store("test goal", {"tasks": [{"id": "t1"}]}, strategy="llm", scope="L2")
            memory1.record_result("test goal", success=True, duration_ms=50.0)

            # Load in new instance
            memory2 = PlanMemory(persist_path=path)
            result = memory2.lookup("test goal")

            assert result is not None
            assert result.strategy == "llm"
            assert result.scope == "L2"
            assert result.success_count == 1
            assert result.plan_data["tasks"][0]["id"] == "t1"
        finally:
            os.unlink(path)

    def test_lookup_similar(self):
        """lookup_similar should find plans with similar goals."""
        memory = PlanMemory()
        memory.store("create a python file with tests", {"tasks": [{"id": "t1"}]})
        memory.store("write a javascript function", {"tasks": [{"id": "t2"}]})
        memory.store("deploy to production server", {"tasks": [{"id": "t3"}]})

        # Search for something very similar — "create a python file" shares
        # most words with the first stored goal
        results = memory.lookup_similar("create a python file with tests", top_k=2, min_similarity=0.3)
        assert len(results) > 0
        # First result should be the exact match (highest similarity)
        assert "python" in results[0].goal_text.lower()

    def test_case_insensitive_lookup(self):
        """Lookup should be case-insensitive."""
        memory = PlanMemory()
        memory.store("Create a File", {"tasks": []})

        result = memory.lookup("create a file")
        assert result is not None

    def test_whitespace_normalized_lookup(self):
        """Lookup should normalize whitespace."""
        memory = PlanMemory()
        memory.store("create   a\n\nfile", {"tasks": []})

        result = memory.lookup("create a file")
        assert result is not None
