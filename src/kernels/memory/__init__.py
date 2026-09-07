"""Memory Kernel — Multi-tier + scoping

The Memory Kernel provides multi-tier memory storage with L0-L7 scope filtering
and complete CRUD operations for context augmentation and state persistence.

依据 Definition Lock §112: Memory Kernel 必须能够
- store(data, scope, tags, ttl)
- recall(query, scope, tags)
- scope_filter(query, max_scope)
- Support multi-tier: short-term, mid-term, long-term
- Correlation with event system for audit trails
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
import uuid
import threading
import json

from src.kernels._crosscutting import kernel_action
from src.kernels.memory.store import MemoryStore


class MemoryTier(str, Enum):
    """Memory storage tiers."""
    SHORT_TERM = "short_term"  # < 5 minutes
    MID_TERM = "mid_term"      # 5 minutes - 24 hours
    LONG_TERM = "long_term"    # 24 hours - 30 days
    PERSISTENT = "persistent"  # > 30 days


class MemoryScope(str, Enum):
    """Memory scope L0-L7."""
    L0 = "L0"
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"
    L4 = "L4"
    L5 = "L5"
    L6 = "L6"
    L7 = "L7"


@dataclass
class MemoryEntry:
    """A single memory entry with scope and tier information."""
    id: str
    key: str
    value: Any
    tier: MemoryTier
    scope: MemoryScope
    created_at: datetime
    expires_at: Optional[datetime]
    tags: Set[str] = field(default_factory=set)
    correlation_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    access_count: int = 0
    last_accessed: Optional[datetime] = None
    provenance: Optional[Dict[str, Any]] = None  # consolidation lineage (source ids)


@dataclass
class MemoryCompression:
    """Result of consolidating multiple entries into a single summary entry.

    ``source_ids`` is the provenance lineage: the ids of the entries that were
    compressed away. The target entry can be retrieved via ``target_key`` at
    ``target_tier``.
    """
    source_ids: List[str]
    source_tier: MemoryTier
    target_tier: MemoryTier
    target_key: str
    target_id: str
    source_count: int
    value: Any


# Tier promotion order (fast/volatile -> slow/durable).
_TIER_ORDER: List[MemoryTier] = [
    MemoryTier.SHORT_TERM,
    MemoryTier.MID_TERM,
    MemoryTier.LONG_TERM,
    MemoryTier.PERSISTENT,
]


def _default_summarizer(entries: List[MemoryEntry]) -> Any:
    """Default lossless consolidator.

    Packs every source entry's key/value/scope/tags into an inspectable dict so
    no information is dropped. This is a *lossless* consolidation (N entries ->
    1 entry), not a semantic summary — inject a custom ``summarizer`` (e.g. an
    LLM callable) for genuine semantic compression.
    """
    return {
        "kind": "consolidation",
        "count": len(entries),
        "items": [
            {
                "key": e.key,
                "value": e.value,
                "scope": e.scope.value,
                "tags": sorted(e.tags),
            }
            for e in entries
        ],
    }


def _tier_ttl(tier: MemoryTier) -> timedelta:
    """Default time-to-live for a tier (mirrors ``store`` default expiry)."""
    return {
        MemoryTier.SHORT_TERM: timedelta(minutes=5),
        MemoryTier.MID_TERM: timedelta(hours=1),
        MemoryTier.LONG_TERM: timedelta(days=1),
        MemoryTier.PERSISTENT: timedelta(days=30),
    }[tier]


class MemoryTierManager:
    """Manages memory tier lifecycle and TTL enforcement."""
    
    _instance: Optional['MemoryTierManager'] = None
    _lock = threading.Lock()
    
    def __new__(cls) -> 'MemoryTierManager':
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance
    
    def __init__(self) -> None:
        if self._initialized:
            return
        self._short_term: Dict[str, MemoryEntry] = {}
        self._mid_term: Dict[str, MemoryEntry] = {}
        self._long_term: Dict[str, MemoryEntry] = {}
        self._persistent: Dict[str, MemoryEntry] = {}
        self._lock = threading.RLock()
        self._initialized = True
    
    def _evict_tier(self, tier_dict: Dict[str, MemoryEntry], 
                    tier_name: str, max_age: timedelta) -> List[str]:
        """Evict entries exceeding max age from a tier."""
        now = datetime.utcnow()
        evicted = []
        keys_to_remove = []
        
        for key, entry in tier_dict.items():
            if entry.expires_at and entry.expires_at <= now:
                keys_to_remove.append(key)
                evicted.append(key)
        
        for key in keys_to_remove:
            tier_dict.pop(key, None)
        
        return evicted
    
    @kernel_action("memory.auto_cleanup")
    def auto_cleanup(self) -> Dict[str, int]:
        """Auto-cleanup expired entries across all tiers."""
        now = datetime.utcnow()
        results = {}
        
        # Short-term: < 5 minutes
        results['short_term'] = len(self._evict_tier(self._short_term, 'short_term', timedelta(minutes=5)))
        
        # Mid-term: 5 min - 24 hours
        results['mid_term'] = len(self._evict_tier(self._mid_term, 'mid_term', timedelta(hours=1)))
        
        # Long-term: 1 day - 30 days
        results['long_term'] = len(self._evict_tier(self._long_term, 'long_term', timedelta(days=1)))
        
        # Persistent: > 30 days (manual review only)
        # No automatic eviction for persistent
        
        return results


# Global tier manager
_tier_manager: Optional[MemoryTierManager] = None


def get_tier_manager() -> MemoryTierManager:
    """Get the global tier manager instance."""
    global _tier_manager
    if _tier_manager is None:
        _tier_manager = MemoryTierManager()
    return _tier_manager


def auto_cleanup() -> Dict[str, int]:
    """Auto-cleanup expired memory entries."""
    return get_tier_manager().auto_cleanup()


def _entry_to_row(entry: MemoryEntry, key_hash: str) -> Dict[str, Any]:
    """把一条 MemoryEntry 序列化为持久化字段字典（约定见 store.py docstring）。"""
    return {
        "key_hash": key_hash,
        "entry_id": entry.id,
        "key": entry.key,
        "value": json.dumps(entry.value, ensure_ascii=False, default=str),
        "tier": entry.tier.value,
        "scope": entry.scope.value,
        "created_at": entry.created_at.isoformat(),
        "expires_at": entry.expires_at.isoformat() if entry.expires_at else None,
        "tags": json.dumps(sorted(entry.tags)) if entry.tags else "[]",
        "correlation_id": entry.correlation_id,
        "access_count": entry.access_count,
        "last_accessed": entry.last_accessed.isoformat() if entry.last_accessed else None,
        "provenance": json.dumps(entry.provenance, default=str) if entry.provenance else None,
    }


def _entry_from_row(row: Dict[str, Any]) -> MemoryEntry:
    """从持久化字段字典重建一条 MemoryEntry。"""
    def _load(value: Any) -> Any:
        return json.loads(value) if isinstance(value, str) and value else None

    tags = _load(row["tags"]) or []
    return MemoryEntry(
        id=row["entry_id"],
        key=row["key"],
        value=_load(row["value"]),
        tier=MemoryTier(row["tier"]),
        scope=MemoryScope(row["scope"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        expires_at=datetime.fromisoformat(row["expires_at"]) if row["expires_at"] else None,
        tags=set(tags),
        correlation_id=row["correlation_id"],
        access_count=row["access_count"],
        last_accessed=datetime.fromisoformat(row["last_accessed"]) if row["last_accessed"] else None,
        provenance=_load(row["provenance"]),
    )


@dataclass
class MemoryKernel:
    """Complete Memory Kernel implementation.

    持久化：``db_path=None`` 时走默认落盘路径（env ``MEMORY_DB_PATH``，兜底
    ``D:/LiuHao-AI-OS/memory_store.db``），跨进程重启保留记忆；传 ``":memory:"``
    为纯内存（测试隔离用）。
    """
    
    _entries: Dict[str, MemoryEntry] = field(default_factory=dict, init=False)
    _lock: threading.RLock = field(default_factory=threading.RLock)
    db_path: Optional[str] = None
    _store: Optional[MemoryStore] = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self._store = MemoryStore(db_path=self.db_path)
        self._load_persisted()

    def _load_persisted(self) -> None:
        """启动时从持久化后端重建 ``_entries``（坏条目诚实跳过，不拖垮启动）。"""
        with self._lock:
            for row in self._store.load_all():
                try:
                    self._entries[row["key_hash"]] = _entry_from_row(row)
                except Exception:
                    continue

    @kernel_action("memory.store")
    def store(
        self,
        key: str,
        value: Any,
        tier: MemoryTier = MemoryTier.MID_TERM,
        scope: MemoryScope = MemoryScope.L1,
        tags: Optional[Set[str]] = None,
        ttl: Optional[timedelta] = None
    ) -> MemoryEntry:
        """Store a memory entry."""
        with self._lock:
            expires_at = None
            if ttl:
                expires_at = datetime.utcnow() + ttl
            elif tier == MemoryTier.SHORT_TERM:
                expires_at = datetime.utcnow() + timedelta(minutes=5)
            elif tier == MemoryTier.MID_TERM:
                expires_at = datetime.utcnow() + timedelta(hours=1)
            elif tier == MemoryTier.LONG_TERM:
                expires_at = datetime.utcnow() + timedelta(days=1)
            elif tier == MemoryTier.PERSISTENT:
                expires_at = datetime.utcnow() + timedelta(days=30)
            
            entry = MemoryEntry(
                id=str(uuid.uuid4())[:8],
                key=key,
                value=value,
                tier=tier,
                scope=scope,
                created_at=datetime.utcnow(),
                expires_at=expires_at,
                tags=tags or set(),
            )
            
            # Store in appropriate tier
            key_hash = f"{tier.value}:{key}"
            self._entries[key_hash] = entry

            # 写穿落盘（跨进程重启保留记忆）。
            self._store.persist(_entry_to_row(entry, key_hash))

            return entry
    
    def recall(
        self,
        key: str,
        scope: MemoryScope = MemoryScope.L1,
        tier_filter: Optional[List[MemoryTier]] = None,
        tags: Optional[Set[str]] = None
    ) -> Optional[MemoryEntry]:
        """Recall a memory entry by key.

        *tier_filter* selects which tiers are eligible. It may be:
          - ``None``                -> any tier,
          - a single ``MemoryTier`` -> only that tier,
          - a list/set/tuple        -> only the listed tiers.
        This makes same-key entries stored in different tiers independently
        recallable (the previous ``key_hash`` computation here was dead
        code that never filtered by tier).
        """
        with self._lock:
            # Normalize tier_filter into a set of admissible tiers (or None).
            allowed_tiers: Optional[Set[MemoryTier]] = None
            if tier_filter is not None:
                if isinstance(tier_filter, (list, tuple, set)):
                    allowed_tiers = set(tier_filter)
                else:
                    allowed_tiers = {tier_filter}

            candidates: List[MemoryEntry] = []
            for entry in self._entries.values():
                if entry.key != key:
                    continue
                if allowed_tiers is not None and entry.tier not in allowed_tiers:
                    continue
                if not self._scope_matches(entry.scope, scope):
                    continue
                if tags is not None and not tags.issubset(entry.tags):
                    continue
                candidates.append(entry)

            if not candidates:
                return None

            # When several tiers match, prefer the most recently created.
            chosen = max(candidates, key=lambda e: e.created_at)
            chosen.access_count += 1
            chosen.last_accessed = datetime.utcnow()
            return chosen
    
    def _scope_matches(self, entry_scope: MemoryScope, query_scope: MemoryScope) -> bool:
        """Check if entry scope satisfies query scope constraint."""
        scope_order = {s: i for i, s in enumerate(MemoryScope)}
        return scope_order[entry_scope] >= scope_order[query_scope]
    
    def scope_filter(
        self,
        query_scope: MemoryScope,
        max_access_count: Optional[int] = None,
        min_age: Optional[timedelta] = None,
        max_age: Optional[timedelta] = None
    ) -> List[MemoryEntry]:
        """Filter memory entries by scope and optional criteria."""
        with self._lock:
            scope_order = {s: i for i, s in enumerate(MemoryScope)}
            min_idx = scope_order[query_scope]
            
            results = [
                entry for entry in self._entries.values()
                if scope_order[entry.scope] >= min_idx
            ]
            
            if max_access_count is not None:
                results = [e for e in results if e.access_count <= max_access_count]
            
            if min_age is not None:
                now = datetime.utcnow()
                results = [e for e in results if e.created_at and e.created_at >= now - min_age]
            
            if max_age is not None:
                now = datetime.utcnow()
                results = [e for e in results if e.created_at and e.created_at < now - max_age]
            
            return sorted(results, key=lambda e: e.created_at, reverse=True)
    
    def stats(self) -> Dict[str, Any]:
        """Get memory kernel statistics."""
        with self._lock:
            by_tier = {}
            by_scope = {}
            total_access = 0
            
            for entry in self._entries.values():
                tier_name = entry.tier.value
                by_tier[tier_name] = by_tier.get(tier_name, 0) + 1
                
                scope_name = entry.scope.value
                by_scope[scope_name] = by_scope.get(scope_name, 0) + 1
                
                total_access += entry.access_count
            
            return {
                "total_entries": len(self._entries),
                "by_tier": by_tier,
                "by_scope": by_scope,
                "total_access_count": total_access,
                "avg_access_per_entry": total_access / len(self._entries) if self._entries else 0,
            }

    @kernel_action("memory.compress")
    def compress(
        self,
        source_tier: MemoryTier = MemoryTier.SHORT_TERM,
        target_tier: Optional[MemoryTier] = None,
        scope: MemoryScope = MemoryScope.L1,
        summarizer: Optional[Callable[[List[MemoryEntry]], Any]] = None,
        max_entries: Optional[int] = None,
        older_than: Optional[timedelta] = None,
        target_key: Optional[str] = None,
    ) -> Optional[MemoryCompression]:
        """Consolidate ``source_tier`` entries into a single summary at ``target_tier``.

        Compression = tier promotion: many entries in a fast, volatile tier are
        packed (or, with a custom ``summarizer``, semantically summarized) into
        one entry in a slower, longer-lived tier. Source entries are removed and
        their ids recorded as provenance on the target entry.

        Scope safety: consolidation only ever merges entries of *the same*
        scope (``scope``, default L1). Mixing scopes would leak higher-scope
        content to lower-scope recall, so it is deliberately forbidden here.

        Returns ``None`` (honest "nothing to do") when there are no candidates
        or when ``source_tier`` has no higher tier to promote into.
        """
        if target_tier is None:
            idx = _TIER_ORDER.index(source_tier)
            if idx + 1 >= len(_TIER_ORDER):
                return None  # PERSISTENT has nowhere to promote
            target_tier = _TIER_ORDER[idx + 1]

        with self._lock:
            now = datetime.utcnow()
            candidates = [
                e for e in self._entries.values()
                if e.tier == source_tier
                and e.scope == scope
                and (older_than is None or (e.created_at is not None and e.created_at <= now - older_than))
            ]
            # Newest first, then cap.
            candidates.sort(key=lambda e: e.created_at, reverse=True)
            if max_entries is not None:
                candidates = candidates[:max_entries]

            if not candidates:
                return None

            fn = summarizer or _default_summarizer
            value = fn(candidates)

            source_ids = [e.id for e in candidates]
            key = target_key or f"consolidated:{source_tier.value}:{len(source_ids)}"

            entry = MemoryEntry(
                id=str(uuid.uuid4())[:8],
                key=key,
                value=value,
                tier=target_tier,
                scope=scope,
                created_at=now,
                expires_at=now + _tier_ttl(target_tier),
                tags={"consolidated"},
                provenance={
                    "consolidated_from": source_ids,
                    "source_tier": source_tier.value,
                    "count": len(source_ids),
                },
            )

            # Store the target, then remove the compressed-away sources.
            target_key_hash = f"{target_tier.value}:{key}"
            self._entries[target_key_hash] = entry
            for e in candidates:
                self._entries.pop(f"{e.tier.value}:{e.key}", None)

            # 落盘：写入合并后的 target，删除被合并的 source。
            self._store.persist(_entry_to_row(entry, target_key_hash))
            for e in candidates:
                self._store.delete(f"{e.tier.value}:{e.key}")

            return MemoryCompression(
                source_ids=source_ids,
                source_tier=source_tier,
                target_tier=target_tier,
                target_key=key,
                target_id=entry.id,
                source_count=len(source_ids),
                value=value,
            )

    def clear(self) -> None:
        """清空所有记忆条目（内存 + 持久化后端）。

        供「重置会话」与测试隔离使用；身份/审计等其它 kernel 不受影响。
        """
        with self._lock:
            self._entries.clear()
            self._store.clear()


# Global memory kernel instance
_global_kernel: Optional[MemoryKernel] = None


def get_memory_kernel(db_path: Optional[str] = None) -> MemoryKernel:
    """Get or create the global memory kernel instance.

    ``db_path`` 仅在首次创建单例时生效（之后调用幂等忽略）；``None`` 走默认
    落盘路径。
    """
    global _global_kernel
    if _global_kernel is None:
        _global_kernel = MemoryKernel(db_path=db_path)
    return _global_kernel


def store_memory(
    key: str,
    value: Any,
    tier: MemoryTier = MemoryTier.MID_TERM,
    scope: MemoryScope = MemoryScope.L1,
    tags: Optional[Set[str]] = None,
    ttl: Optional[timedelta] = None
) -> MemoryEntry:
    """Store a memory entry."""
    return get_memory_kernel().store(key, value, tier, scope, tags, ttl)


def recall_memory(
    key: str,
    scope: MemoryScope = MemoryScope.L1,
    tier_filter: Optional[List[MemoryTier]] = None,
    tags: Optional[Set[str]] = None
) -> Optional[MemoryEntry]:
    """Recall a memory entry."""
    return get_memory_kernel().recall(key, scope, tier_filter, tags)


def filter_memory(
    query_scope: MemoryScope,
    max_access_count: Optional[int] = None,
    min_age: Optional[timedelta] = None,
    max_age: Optional[timedelta] = None
) -> List[MemoryEntry]:
    """Filter memory entries by scope."""
    return get_memory_kernel().scope_filter(query_scope, max_access_count, min_age, max_age)


def memory_stats() -> Dict[str, Any]:
    """Get memory kernel statistics."""
    return get_memory_kernel().stats()


def compress_memory(
    source_tier: MemoryTier = MemoryTier.SHORT_TERM,
    target_tier: Optional[MemoryTier] = None,
    scope: MemoryScope = MemoryScope.L1,
    summarizer: Optional[Callable[[List[MemoryEntry]], Any]] = None,
    max_entries: Optional[int] = None,
    older_than: Optional[timedelta] = None,
    target_key: Optional[str] = None,
) -> Optional[MemoryCompression]:
    """Consolidate entries across memory tiers (see ``MemoryKernel.compress``)."""
    return get_memory_kernel().compress(
        source_tier, target_tier, scope, summarizer, max_entries, older_than, target_key
    )