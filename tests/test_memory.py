"""
Tests for MemoryManager with Mem0 integration
"""
import pytest
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from src.knowledge.memory import (
    MemoryManager,
    MemoryItem,
    MemoryTier,
    InMemoryBackend,
    Mem0Backend,
    create_memory_manager,
)


class TestInMemoryBackend:
    """Test the in-memory fallback backend"""

    def setup_method(self):
        self.backend = InMemoryBackend()
        self.user_id = "test_user"

    def test_add_and_get(self):
        item = MemoryItem(
            content="Test memory",
            tier=MemoryTier.SEMANTIC,
            user_id=self.user_id
        )
        memory_id = self.backend.add(item)
        assert memory_id == item.id

        retrieved = self.backend.get(memory_id, self.user_id)
        assert retrieved is not None
        assert retrieved.content == "Test memory"
        assert retrieved.tier == MemoryTier.SEMANTIC
        assert retrieved.access_count == 1

    def test_search(self):
        # Add multiple items
        items = [
            MemoryItem(content="User likes Python", tier=MemoryTier.SEMANTIC, user_id=self.user_id),
            MemoryItem(content="User dislikes Java", tier=MemoryTier.SEMANTIC, user_id=self.user_id),
            MemoryItem(content="Current task: coding", tier=MemoryTier.WORKING, user_id=self.user_id),
        ]
        for item in items:
            self.backend.add(item)

        # Search
        results = self.backend.search("Python", user_id=self.user_id, limit=5)
        assert len(results) == 1
        assert "Python" in results[0].content

        # Search with tier filter
        results = self.backend.search("coding", user_id=self.user_id, tier=MemoryTier.WORKING)
        assert len(results) == 1
        assert results[0].tier == MemoryTier.WORKING

    def test_update(self):
        item = MemoryItem(content="Original", tier=MemoryTier.SEMANTIC, user_id=self.user_id)
        self.backend.add(item)

        item.content = "Updated"
        item.importance = 0.9
        assert self.backend.update(item) is True

        retrieved = self.backend.get(item.id, self.user_id)
        assert retrieved.content == "Updated"
        assert retrieved.importance == 0.9

    def test_delete(self):
        item = MemoryItem(content="To delete", tier=MemoryTier.SEMANTIC, user_id=self.user_id)
        self.backend.add(item)

        assert self.backend.delete(item.id, self.user_id) is True
        assert self.backend.get(item.id, self.user_id) is None

    def test_get_all(self):
        for tier in [MemoryTier.SEMANTIC, MemoryTier.EPISODIC, MemoryTier.WORKING]:
            item = MemoryItem(content=f"Memory in {tier.value}", tier=tier, user_id=self.user_id)
            self.backend.add(item)

        all_items = self.backend.get_all(user_id=self.user_id)
        assert len(all_items) == 3

        semantic_items = self.backend.get_all(user_id=self.user_id, tier=MemoryTier.SEMANTIC)
        assert len(semantic_items) == 1


class TestMemoryManager:
    """Test the unified MemoryManager"""

    def setup_method(self):
        self.mm = create_memory_manager(user_id="test_manager", use_mem0=False)

    def test_remember_and_recall(self):
        mem_id = self.mm.remember(
            "Important fact: User's name is LiuHao",
            tier=MemoryTier.SEMANTIC,
            importance=0.95,
            tags=["identity", "critical"]
        )
        assert mem_id is not None

        results = self.mm.recall("LiuHao", limit=5)
        assert len(results) >= 1
        assert any("LiuHao" in r.content for r in results)

    def test_tier_operations(self):
        # Add to different tiers
        self.mm.remember("Working memory item", tier=MemoryTier.WORKING)
        self.mm.remember("Episodic memory item", tier=MemoryTier.EPISODIC)
        self.mm.remember("Semantic memory item", tier=MemoryTier.SEMANTIC)

        working = self.mm.get_tier_memories(MemoryTier.WORKING)
        episodic = self.mm.get_tier_memories(MemoryTier.EPISODIC)
        semantic = self.mm.get_tier_memories(MemoryTier.SEMANTIC)

        assert len(working) == 1
        assert len(episodic) == 1
        assert len(semantic) == 1

    def test_get_stats(self):
        self.mm.remember("Item 1", tier=MemoryTier.SEMANTIC)
        self.mm.remember("Item 2", tier=MemoryTier.SEMANTIC)
        self.mm.remember("Item 3", tier=MemoryTier.EPISODIC)

        stats = self.mm.get_stats()
        assert stats["total"] == 3
        assert stats["by_tier"]["semantic"] == 2
        assert stats["by_tier"]["episodic"] == 1

    def test_consolidation(self):
        # Add working memory with high importance
        self.mm.remember(
            "High importance working memory",
            tier=MemoryTier.WORKING,
            importance=0.9
        )

        # Add episodic with high access count (simulate)
        items = self.mm.get_tier_memories(MemoryTier.EPISODIC)
        if items:
            items[0].access_count = 25
            self.mm.update_memory(items[0])

        result = self.mm.consolidate()
        assert "promoted" in result

    def test_export_memories(self):
        self.mm.remember("Export test", tier=MemoryTier.SEMANTIC, tags=["export"])
        exported = self.mm.export_memories()

        assert len(exported) >= 1
        assert exported[0]["content"] == "Export test"
        assert "semantic" in exported[0]["tier"]

    def test_forget(self):
        mem_id = self.mm.remember("To be forgotten", tier=MemoryTier.SEMANTIC)
        assert self.mm.forget(mem_id) is True
        assert self.mm.get_memory(mem_id) is None


class TestMemoryItem:
    """Test MemoryItem dataclass"""

    def test_to_mem0_format(self):
        item = MemoryItem(
            content="Test content",
            tier=MemoryTier.SEMANTIC,
            importance=0.8,
            tags=["tag1", "tag2"],
            user_id="user123",
            session_id="session456",
            agent_id="agent789"
        )
        mem0_format = item.to_mem0_format()

        assert mem0_format["memory"] == "Test content"
        assert mem0_format["metadata"]["tier"] == "semantic"
        assert mem0_format["metadata"]["importance"] == 0.8
        assert mem0_format["metadata"]["tags"] == ["tag1", "tag2"]
        assert mem0_format["metadata"]["user_id"] == "user123"

    def test_from_mem0_result(self):
        result = {
            "id": "mem-123",
            "memory": "Restored memory",
            "metadata": {
                "tier": "episodic",
                "importance": 0.7,
                "tags": ["restored"],
                "user_id": "user123",
                "session_id": "session456",
                "agent_id": "agent789",
                "access_count": 5,
            }
        }
        item = MemoryItem.from_mem0_result(result)

        assert item.id == "mem-123"
        assert item.content == "Restored memory"
        assert item.tier == MemoryTier.EPISODIC
        assert item.importance == 0.7
        assert item.tags == ["restored"]
        assert item.access_count == 5


if __name__ == "__main__":
    pytest.main([__file__, "-v"])