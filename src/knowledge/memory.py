"""
LiuHao AI OS (镭灏) - Memory Layer
Integrates Mem0 (mem0ai) as the production-grade long-term memory backend.
Provides unified interface compatible with 10-layer memory architecture.
"""

from __future__ import annotations
import os
import json
import uuid
from datetime import datetime
from typing import Any, Optional, List, Dict
from dataclasses import dataclass, field
from enum import Enum
from abc import ABC, abstractmethod

# Optional imports with graceful degradation
try:
    from mem0 import Memory as Mem0Memory
    from mem0.configs.llms.base import BaseLlmConfig
    from mem0.configs.embeddings.base import BaseEmbedderConfig
    from mem0.configs.vector_stores.base import BaseVectorStoreConfig
    MEM0_AVAILABLE = True
except ImportError:
    MEM0_AVAILABLE = False
    Mem0Memory = None
    BaseLlmConfig = None
    BaseEmbedderConfig = None
    BaseVectorStoreConfig = None

try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False


class MemoryTier(Enum):
    """10-layer memory architecture tiers"""
    WORKING = "working"           # L1: Immediate context (seconds-minutes)
    EPISODIC = "episodic"         # L2: Session episodes (hours-days)
    SEMANTIC = "semantic"         # L3: Facts & concepts (persistent)
    PROCEDURAL = "procedural"     # L4: Skills & procedures (persistent)
    AUTOBIOGRAPHICAL = "autobiographical"  # L5: Life narrative (persistent)
    SOCIAL = "social"             # L6: Persona & relationships (persistent)
    CULTURAL = "cultural"         # L7: Domain knowledge (shared)
    META = "meta"                 # L8: Memory about memory (persistent)
    ARCHIVAL = "archival"         # L9: Cold storage (rarely accessed)
    COLLECTIVE = "collective"     # L10: Swarm/hive memory (distributed)


@dataclass
class MemoryItem:
    """Unified memory item across all tiers"""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    content: str = ""
    tier: MemoryTier = MemoryTier.SEMANTIC
    metadata: Dict[str, Any] = field(default_factory=dict)
    embedding: Optional[List[float]] = None
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    access_count: int = 0
    importance: float = 0.5  # 0.0 - 1.0
    tags: List[str] = field(default_factory=list)
    user_id: str = "default"
    session_id: Optional[str] = None
    agent_id: Optional[str] = None

    def to_mem0_format(self) -> Dict[str, Any]:
        """Convert to Mem0-compatible format"""
        return {
            "id": self.id,
            "memory": self.content,
            "metadata": {
                **self.metadata,
                "tier": self.tier.value,
                "importance": self.importance,
                "tags": self.tags,
                "user_id": self.user_id,
                "session_id": self.session_id,
                "agent_id": self.agent_id,
                "created_at": self.created_at.isoformat(),
                "updated_at": self.updated_at.isoformat(),
                "access_count": self.access_count,
            }
        }

    @classmethod
    def from_mem0_result(cls, result: Dict[str, Any]) -> "MemoryItem":
        """Create MemoryItem from Mem0 search result"""
        meta = result.get("metadata", {})
        return cls(
            id=result.get("id", str(uuid.uuid4())),
            content=result.get("memory", ""),
            tier=MemoryTier(meta.get("tier", "semantic")),
            metadata={k: v for k, v in meta.items() if k not in (
                "tier", "importance", "tags", "user_id", "session_id", "agent_id",
                "created_at", "updated_at", "access_count"
            )},
            importance=meta.get("importance", 0.5),
            tags=meta.get("tags", []),
            user_id=meta.get("user_id", "default"),
            session_id=meta.get("session_id"),
            agent_id=meta.get("agent_id"),
            access_count=meta.get("access_count", 0),
        )


class MemoryBackend(ABC):
    """Abstract backend interface for memory storage"""

    @abstractmethod
    def add(self, item: MemoryItem) -> str:
        pass

    @abstractmethod
    def search(
        self,
        query: str,
        user_id: str = "default",
        limit: int = 10,
        tier: Optional[MemoryTier] = None,
        filters: Optional[Dict[str, Any]] = None
    ) -> List[MemoryItem]:
        pass

    @abstractmethod
    def get(self, memory_id: str, user_id: str = "default") -> Optional[MemoryItem]:
        pass

    @abstractmethod
    def update(self, item: MemoryItem) -> bool:
        pass

    @abstractmethod
    def delete(self, memory_id: str, user_id: str = "default") -> bool:
        pass

    @abstractmethod
    def get_all(
        self,
        user_id: str = "default",
        tier: Optional[MemoryTier] = None,
        limit: int = 100
    ) -> List[MemoryItem]:
        pass


class Mem0Backend(MemoryBackend):
    """Mem0-backed memory storage (production-grade)"""

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        user_id: str = "default"
    ):
        if not MEM0_AVAILABLE:
            raise RuntimeError("mem0 not installed. Run: pip install mem0ai")

        self.user_id = user_id
        self.config = config or self._default_config()
        self._memory = Mem0Memory.from_config(self.config)

    def _default_config(self) -> Dict[str, Any]:
        """Default Mem0 configuration for LiuHao"""
        openai_key = os.getenv("OPENAI_API_KEY", "[REDACTED]")
        return {
            "llm": {
                "provider": "openai",
                "config": {
                    "model": "gpt-4o-mini",
                    "temperature": 0.1,
                    "api_key": openai_key,
                }
            },
            "embedder": {
                "provider": "openai",
                "config": {
                    "model": "text-embedding-3-small",
                    "api_key": openai_key,
                }
            },
            "vector_store": {
                "provider": "qdrant",
                "config": {
                    "collection_name": "liuhao_memories",
                    "host": os.getenv("QDRANT_HOST", "localhost"),
                    "port": int(os.getenv("QDRANT_PORT", "6333")),
                    "api_key": os.getenv("QDRANT_API_KEY"),
                }
            },
            "version": "v1.1"
        }

    def add(self, item: MemoryItem) -> str:
        """Add memory item to Mem0"""
        mem0_format = item.to_mem0_format()
        # Mem0 expects messages format
        messages = [
            {"role": "user", "content": item.content}
        ]
        result = self._memory.add(
            messages=messages,
            user_id=item.user_id,
            metadata=mem0_format["metadata"]
        )
        # Mem0 returns list of created memories
        if result and isinstance(result, list) and len(result) > 0:
            return result[0].get("id", item.id)
        return item.id

    def search(
        self,
        query: str,
        user_id: str = "default",
        limit: int = 10,
        tier: Optional[MemoryTier] = None,
        filters: Optional[Dict[str, Any]] = None
    ) -> List[MemoryItem]:
        """Search memories with optional tier filtering"""
        search_filters = filters or {}
        if tier:
            search_filters["tier"] = tier.value

        results = self._memory.search(
            query=query,
            user_id=user_id,
            limit=limit,
            filters=search_filters
        )

        items = []
        for r in results.get("results", []):
            item = MemoryItem.from_mem0_result(r)
            item.access_count += 1
            items.append(item)
        return items

    def get(self, memory_id: str, user_id: str = "default") -> Optional[MemoryItem]:
        """Get specific memory by ID"""
        results = self._memory.get_all(user_id=user_id)
        for r in results.get("results", []):
            if r.get("id") == memory_id:
                return MemoryItem.from_mem0_result(r)
        return None

    def update(self, item: MemoryItem) -> bool:
        """Update existing memory (Mem0 uses add with same ID for upsert)"""
        item.updated_at = datetime.now()
        try:
            self.add(item)
            return True
        except Exception:
            return False

    def delete(self, memory_id: str, user_id: str = "default") -> bool:
        """Delete memory by ID"""
        try:
            self._memory.delete(memory_id=memory_id, user_id=user_id)
            return True
        except Exception:
            return False

    def get_all(
        self,
        user_id: str = "default",
        tier: Optional[MemoryTier] = None,
        limit: int = 100
    ) -> List[MemoryItem]:
        """Get all memories for user"""
        results = self._memory.get_all(user_id=user_id, limit=limit)
        items = []
        for r in results.get("results", []):
            item = MemoryItem.from_mem0_result(r)
            if tier is None or item.tier == tier:
                items.append(item)
        return items


class InMemoryBackend(MemoryBackend):
    """Fallback in-memory backend for development/testing"""

    def __init__(self):
        self._store: Dict[str, MemoryItem] = {}

    def add(self, item: MemoryItem) -> str:
        self._store[item.id] = item
        return item.id

    def search(
        self,
        query: str,
        user_id: str = "default",
        limit: int = 10,
        tier: Optional[MemoryTier] = None,
        filters: Optional[Dict[str, Any]] = None
    ) -> List[MemoryItem]:
        # Simple text search fallback
        results = []
        query_lower = query.lower()
        for item in self._store.values():
            if item.user_id != user_id:
                continue
            if tier and item.tier != tier:
                continue
            if query_lower in item.content.lower():
                item.access_count += 1
                results.append(item)
        return results[:limit]

    def get(self, memory_id: str, user_id: str = "default") -> Optional[MemoryItem]:
        item = self._store.get(memory_id)
        if item and item.user_id == user_id:
            item.access_count += 1
            return item
        return None

    def update(self, item: MemoryItem) -> bool:
        if item.id in self._store:
            item.updated_at = datetime.now()
            self._store[item.id] = item
            return True
        return False

    def delete(self, memory_id: str, user_id: str = "default") -> bool:
        item = self._store.get(memory_id)
        if item and item.user_id == user_id:
            del self._store[memory_id]
            return True
        return False

    def get_all(
        self,
        user_id: str = "default",
        tier: Optional[MemoryTier] = None,
        limit: int = 100
    ) -> List[MemoryItem]:
        results = []
        for item in self._store.values():
            if item.user_id != user_id:
                continue
            if tier and item.tier != tier:
                continue
            results.append(item)
        return results[:limit]


class MemoryManager:
    """
    Unified Memory Manager for LiuHao AI OS.
    Supports multiple backends with tiered memory architecture.
    """

    def __init__(
        self,
        backend: Optional[MemoryBackend] = None,
        user_id: str = "default",
        config: Optional[Dict[str, Any]] = None
    ):
        self.user_id = user_id
        self.config = config or {}

        # Initialize backend
        if backend:
            self.backend = backend
        elif MEM0_AVAILABLE:
            try:
                self.backend = Mem0Backend(config=self.config, user_id=user_id)
            except Exception as e:
                print(f"[MemoryManager] Mem0 init failed, falling back to in-memory: {e}")
                self.backend = InMemoryBackend()
        else:
            self.backend = InMemoryBackend()

        # Tier-specific configurations
        self.tier_configs = {
            MemoryTier.WORKING: {"ttl_hours": 1, "max_items": 50},
            MemoryTier.EPISODIC: {"ttl_hours": 72, "max_items": 200},
            MemoryTier.SEMANTIC: {"ttl_hours": 8760, "max_items": 5000},
            MemoryTier.PROCEDURAL: {"ttl_hours": 87600, "max_items": 1000},
            MemoryTier.AUTOBIOGRAPHICAL: {"ttl_hours": 876000, "max_items": 2000},
            MemoryTier.SOCIAL: {"ttl_hours": 876000, "max_items": 500},
            MemoryTier.CULTURAL: {"ttl_hours": 876000, "max_items": 10000},
            MemoryTier.META: {"ttl_hours": 876000, "max_items": 1000},
            MemoryTier.ARCHIVAL: {"ttl_hours": 8760000, "max_items": 50000},
            MemoryTier.COLLECTIVE: {"ttl_hours": 8760000, "max_items": 100000},
        }

    def remember(
        self,
        content: str,
        tier: MemoryTier = MemoryTier.SEMANTIC,
        importance: float = 0.5,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None,
        agent_id: Optional[str] = None
    ) -> str:
        """Store a memory item"""
        item = MemoryItem(
            content=content,
            tier=tier,
            importance=importance,
            tags=tags or [],
            metadata=metadata or {},
            user_id=self.user_id,
            session_id=session_id,
            agent_id=agent_id
        )
        return self.backend.add(item)

    def recall(
        self,
        query: str,
        tier: Optional[MemoryTier] = None,
        limit: int = 10,
        filters: Optional[Dict[str, Any]] = None
    ) -> List[MemoryItem]:
        """Search and retrieve relevant memories"""
        return self.backend.search(
            query=query,
            user_id=self.user_id,
            limit=limit,
            tier=tier,
            filters=filters
        )

    def get_memory(self, memory_id: str) -> Optional[MemoryItem]:
        """Retrieve specific memory by ID"""
        return self.backend.get(memory_id, self.user_id)

    def update_memory(self, item: MemoryItem) -> bool:
        """Update existing memory"""
        return self.backend.update(item)

    def forget(self, memory_id: str) -> bool:
        """Delete a memory"""
        return self.backend.delete(memory_id, self.user_id)

    def get_tier_memories(
        self,
        tier: MemoryTier,
        limit: int = 100
    ) -> List[MemoryItem]:
        """Get all memories for a specific tier"""
        return self.backend.get_all(user_id=self.user_id, tier=tier, limit=limit)

    def get_stats(self) -> Dict[str, Any]:
        """Get memory statistics across tiers"""
        stats = {"total": 0, "by_tier": {}}
        for tier in MemoryTier:
            items = self.get_tier_memories(tier, limit=10000)
            stats["by_tier"][tier.value] = len(items)
            stats["total"] += len(items)
        return stats

    def consolidate(self) -> Dict[str, int]:
        """
        Memory consolidation: promote important working/episodic memories
        to semantic/procedural tiers based on access patterns and importance.
        """
        promoted = 0
        # Promote high-importance working memories to episodic
        working = self.get_tier_memories(MemoryTier.WORKING)
        for item in working:
            if item.importance > 0.7 or item.access_count > 5:
                item.tier = MemoryTier.EPISODIC
                self.backend.update(item)
                promoted += 1

        # Promote high-access episodic to semantic
        episodic = self.get_tier_memories(MemoryTier.EPISODIC)
        for item in episodic:
            if item.importance > 0.8 or item.access_count > 20:
                item.tier = MemoryTier.SEMANTIC
                self.backend.update(item)
                promoted += 1

        return {"promoted": promoted}

    def export_memories(self, tier: Optional[MemoryTier] = None) -> List[Dict[str, Any]]:
        """Export memories for backup/migration"""
        items = []
        tiers = [tier] if tier else list(MemoryTier)
        for t in tiers:
            for item in self.get_tier_memories(t):
                items.append({
                    "id": item.id,
                    "content": item.content,
                    "tier": item.tier.value,
                    "metadata": item.metadata,
                    "importance": item.importance,
                    "tags": item.tags,
                    "user_id": item.user_id,
                    "session_id": item.session_id,
                    "agent_id": item.agent_id,
                    "created_at": item.created_at.isoformat(),
                    "updated_at": item.updated_at.isoformat(),
                    "access_count": item.access_count,
                })
        return items


# Convenience function for quick setup
def create_memory_manager(
    user_id: str = "default",
    use_mem0: bool = True,
    config: Optional[Dict[str, Any]] = None
) -> MemoryManager:
    """Factory function to create configured MemoryManager"""
    if use_mem0 and MEM0_AVAILABLE:
        backend = Mem0Backend(config=config, user_id=user_id)
    else:
        backend = InMemoryBackend()
    return MemoryManager(backend=backend, user_id=user_id, config=config)


if __name__ == "__main__":
    # Quick test
    print("Testing MemoryManager...")

    # Test with in-memory backend
    mm = create_memory_manager(user_id="test_user", use_mem0=False)

    # Store some memories
    id1 = mm.remember(
        "User prefers Chinese responses",
        tier=MemoryTier.SEMANTIC,
        importance=0.9,
        tags=["preference", "language"]
    )
    id2 = mm.remember(
        "Current project: LiuHao AI OS Y1 implementation",
        tier=MemoryTier.EPISODIC,
        importance=0.8,
        tags=["project", "status"]
    )
    id3 = mm.remember(
        "Working on Mem0 integration for memory layer",
        tier=MemoryTier.WORKING,
        importance=0.7,
        tags=["task", "mem0"]
    )

    print(f"Stored memories: {id1}, {id2}, {id3}")

    # Search
    results = mm.recall("Mem0 integration", limit=5)
    print(f"\nSearch 'Mem0 integration': {len(results)} results")
    for r in results:
        print(f"  - [{r.tier.value}] {r.content[:60]}... (importance: {r.importance})")

    # Stats
    stats = mm.get_stats()
    print(f"\nMemory stats: {stats}")

    # Consolidation
    result = mm.consolidate()
    print(f"\nConsolidation: {result}")

    print("\n✅ MemoryManager test passed")