"""Memory Kernel — Multi-tier + scoping

The Memory Kernel provides multi-tier memory storage with L0-L7 scope filtering
and CRUD-style access (store / recall / scope-filter / stats) for context
augmentation and state persistence. There is deliberately no single-entry
update/delete: mutating stored state is done via ``compress`` (which deletes the
entries it consolidates) or ``clear`` (which wipes the store).

依据 Definition Lock §112: Memory Kernel 必须能够
- store(data, scope, tags, ttl)
- recall(query, scope, tags)
- scope_filter(query, max_scope)
- Support multi-tier: short-term, mid-term, long-term
- Correlation with event system for audit trails
"""
from __future__ import annotations
from src.kernels._base import KernelLifecycle, KernelStateError

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from src._time import utc_now
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set
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


class MemoryType(str, Enum):
    """Logical memory types (Phase 4), mapped onto the existing tier model.

    These are a *convention layer* over ``MemoryTier`` + tags — no new storage
    engine and no new ``MemoryTier`` member (which would break existing callers).
    """
    SHORT_TERM = "short_term"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"
    IDENTITY = "identity"


# MemoryType -> (tier, tag). See docs/MEMORY-SYSTEM-DESIGN.md §4.
_TYPE_MAP: Dict[MemoryType, tuple] = {
    MemoryType.SHORT_TERM: (MemoryTier.SHORT_TERM, "short_term"),
    MemoryType.EPISODIC: (MemoryTier.MID_TERM, "episodic"),
    MemoryType.SEMANTIC: (MemoryTier.LONG_TERM, "semantic"),
    MemoryType.PROCEDURAL: (MemoryTier.LONG_TERM, "procedural"),
    MemoryType.IDENTITY: (MemoryTier.PERSISTENT, "identity"),
}


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


def auto_cleanup() -> Dict[str, int]:
    """Evict expired entries from the canonical memory kernel.

    Delegates to ``MemoryKernel.auto_cleanup``. The previous module-level
    implementation routed through a ``MemoryTierManager`` singleton whose
    per-tier dicts nothing ever populated, so it returned all-zero forever and
    left every expired row in place -- a silent no-op reported as success.
    """
    return get_memory_kernel().auto_cleanup()


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
    backend: Optional[Any] = field(default=None, repr=False)
    _store: Optional[Any] = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self.lifecycle = KernelLifecycle.UNINITIALIZED
        # Phase 4: injectable backend. When ``backend`` is None (default) this
        # reproduces historical behaviour byte-for-byte — a SQLite MemoryStore
        # at ``db_path`` (with the same env/default path resolution).
        self._store = self.backend if self.backend is not None else MemoryStore(db_path=self.db_path)
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
                expires_at = utc_now() + ttl
            elif tier == MemoryTier.SHORT_TERM:
                expires_at = utc_now() + timedelta(minutes=5)
            elif tier == MemoryTier.MID_TERM:
                expires_at = utc_now() + timedelta(hours=1)
            elif tier == MemoryTier.LONG_TERM:
                expires_at = utc_now() + timedelta(days=1)
            elif tier == MemoryTier.PERSISTENT:
                # PERSISTENT is defined as "> 30 days" (see MemoryTier). A
                # 30-day expiry would delete it exactly at that boundary and
                # make the *most* durable tier the first to vanish, so
                # persistent memory carries no automatic expiry.
                expires_at = None

            entry = MemoryEntry(
                id=str(uuid.uuid4())[:8],
                key=key,
                value=value,
                tier=tier,
                scope=scope,
                created_at=utc_now(),
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

        Expired entries (``expires_at <= now``) are never returned: a ``ttl``
        is enforced on read, not merely by an explicit ``auto_cleanup``.
        """
        with self._lock:
            now = utc_now()
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
                if entry.expires_at is not None and entry.expires_at <= now:
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
            chosen.last_accessed = utc_now()
            return chosen

    # ------------------------------------------------------------------ #
    # Phase 4 — typed memory convenience layer (additive; design §4)
    # ------------------------------------------------------------------ #
    def remember(
        self,
        memory_type: MemoryType,
        key: str,
        value: Any,
        scope: MemoryScope = MemoryScope.L1,
        tags: Optional[Set[str]] = None,
        ttl: Optional[timedelta] = None,
    ) -> MemoryEntry:
        """Store an entry under a logical memory type (tier+tag convention)."""
        tier, tag = _TYPE_MAP[memory_type]
        merged = set(tags or ()) | {tag}
        return self.store(key, value, tier=tier, scope=scope, tags=merged, ttl=ttl)

    def recall_type(
        self,
        memory_type: MemoryType,
        key: str,
        scope: MemoryScope = MemoryScope.L1,
    ) -> Optional[MemoryEntry]:
        """Recall an entry stored via ``remember`` for the same memory type."""
        tier, tag = _TYPE_MAP[memory_type]
        entry = self.recall(key, scope=scope, tier_filter=[tier])
        if entry is None or tag not in entry.tags:
            return None
        return entry

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
        """Filter memory entries by scope and optional criteria.

        *min_age* keeps entries **at least** that old; *max_age* keeps entries
        **at most** that old. (Those two comparisons used to run the wrong way
        round -- ``min_age`` returned only entries *younger* than the bound and
        ``max_age`` only *older* ones, the exact inverse of the names.) Expired
        entries are excluded, consistent with ``recall``.
        """
        with self._lock:
            now = utc_now()
            scope_order = {s: i for i, s in enumerate(MemoryScope)}
            min_idx = scope_order[query_scope]

            results = [
                entry for entry in self._entries.values()
                if scope_order[entry.scope] >= min_idx
                and (entry.expires_at is None or entry.expires_at > now)
            ]

            if max_access_count is not None:
                results = [e for e in results if e.access_count <= max_access_count]

            if min_age is not None:
                # "at least this old": created at or before now - min_age.
                results = [e for e in results if e.created_at and e.created_at <= now - min_age]

            if max_age is not None:
                # "at most this old": created at or after now - max_age.
                results = [e for e in results if e.created_at and e.created_at >= now - max_age]

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
            now = utc_now()
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

    @kernel_action("memory.auto_cleanup")
    def auto_cleanup(self) -> Dict[str, int]:
        """Evict expired entries from every tier (memory and persistence).

        Walks the same ``_entries`` that ``store``/``recall`` use and deletes
        the matching persisted rows, so the returned per-tier counts are rows
        that are actually gone. ``PERSISTENT`` entries carry no expiry (see
        ``store``) and so are never evicted here.
        """
        with self._lock:
            now = utc_now()
            per_tier: Dict[str, int] = {t.value: 0 for t in MemoryTier}
            dead: List[str] = []
            for key_hash, entry in self._entries.items():
                if entry.expires_at is not None and entry.expires_at <= now:
                    dead.append(key_hash)
                    per_tier[entry.tier.value] += 1
            for key_hash in dead:
                self._entries.pop(key_hash, None)
                self._store.delete(key_hash)
            return per_tier

    def clear(self) -> None:
        """清空所有记忆条目（内存 + 持久化后端）。

        供「重置会话」与测试隔离使用；身份/审计等其它 kernel 不受影响。
        """
        with self._lock:
            self._entries.clear()
            self._store.clear()

    def initialize(self) -> None:
        self.lifecycle = KernelLifecycle.READY

    def shutdown(self) -> None:
        self.lifecycle = KernelLifecycle.STOPPED

    def pause(self) -> None:
        if self.lifecycle not in (KernelLifecycle.READY, KernelLifecycle.UNINITIALIZED):
            raise KernelStateError(f"cannot pause from {self.lifecycle}")
        self.lifecycle = KernelLifecycle.PAUSED

    def resume(self) -> None:
        if self.lifecycle is not KernelLifecycle.PAUSED:
            raise KernelStateError(f"cannot resume from {self.lifecycle}")
        self.lifecycle = KernelLifecycle.READY


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
        _global_kernel.initialize()  # 存在即 READY：构造完成即视为就绪
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
