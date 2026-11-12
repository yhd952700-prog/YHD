"""Working Memory — short-term attentional buffer.

Working memory is the "mental scratchpad" — what you're actively thinking
about right now. It has limited capacity (the famous "7 ± 2 items" from
Miller's law) and items decay over time unless actively attended to.

Why working memory matters:
  1. **Focus** — keeps relevant information active so it's fast to access
  2. **Capacity limit** — forces prioritisation (not everything can be active)
  3. **Attentional control** — important items get promoted, irrelevant ones fade
  4. **Integration** — combines items from different sources for reasoning

This models Baddeley's working memory model:
  - **Phonological loop** — verbal/linguistic information
  - **Visuospatial sketchpad** — visual/spatial information
  - **Episodic buffer** — integrated episodes
  - **Central executive** — attention controller

For our purposes, we simplify to a prioritised buffer with decay.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional
import time
import uuid


class MemoryPriority(str, Enum):
    """Priority levels for working memory items."""
    CRITICAL = "critical"    # Must stay — core goal, active task
    HIGH = "high"            # Important — relevant context
    MEDIUM = "medium"        # Normal — supporting info
    LOW = "low"              # Peripheral — might be dropped
    FLEETING = "fleeting"    # Temporary — decays immediately


# Priority weights (higher = stays longer)
_PRIORITY_WEIGHTS = {
    MemoryPriority.CRITICAL: 10.0,
    MemoryPriority.HIGH: 5.0,
    MemoryPriority.MEDIUM: 2.0,
    MemoryPriority.LOW: 1.0,
    MemoryPriority.FLEETING: 0.3,
}


@dataclass
class WorkingMemoryItem:
    """A single item in working memory."""
    id: str
    content: Any
    item_type: str = "general"  # "fact", "goal", "task", "thought", "sensory"
    priority: MemoryPriority = MemoryPriority.MEDIUM
    source: str = "system"      # Where this came from
    created_at: float = field(default_factory=time.time)
    last_accessed: float = field(default_factory=time.time)
    access_count: int = 0
    activation: float = 1.0     # Current activation level (0-1)
    decay_rate: float = 0.1     # How fast activation decays
    tags: List[str] = field(default_factory=list)

    @property
    def age_ms(self) -> float:
        """Age of the item in milliseconds."""
        return (time.time() - self.created_at) * 1000

    @property
    def time_since_access_ms(self) -> float:
        """Time since last access in milliseconds."""
        return (time.time() - self.last_accessed) * 1000


class WorkingMemory:
    """A limited-capacity working memory buffer.

    Features:
      - Capacity limit (default 7 items, Miller's magic number)
      - Priority-based eviction (low priority drops first)
      - Activation decay (items fade if not accessed)
      - Attentional refresh (accessing an item boosts its activation)
      - Chunking (related items can be grouped into chunks)

    Usage::

        wm = WorkingMemory(capacity=7)

        # Add items
        wm.add("User's name is Alice", item_type="fact",
              priority=MemoryPriority.HIGH)
        wm.add("Goal: write a report", item_type="goal",
              priority=MemoryPriority.CRITICAL)

        # Get active items
        active = wm.active_items()

        # Find items by type/tag
        goals = wm.get_by_type("goal")

        # Periodically decay
        wm.decay()
    """

    def __init__(
        self,
        capacity: int = 7,
        decay_interval_ms: float = 30000,  # Decay every 30 seconds
        min_activation: float = 0.1,       # Drop below this = removed
    ):
        self.capacity = capacity
        self.decay_interval_ms = decay_interval_ms
        self.min_activation = min_activation

        self._items: Dict[str, WorkingMemoryItem] = {}
        self._last_decay: float = time.time()
        self._chunks: Dict[str, List[str]] = {}  # chunk_id -> [item_ids]

    # ------------------------------------------------------------------
    # Basic operations
    # ------------------------------------------------------------------

    def add(
        self,
        content: Any,
        item_type: str = "general",
        priority: MemoryPriority = MemoryPriority.MEDIUM,
        source: str = "system",
        tags: Optional[List[str]] = None,
    ) -> WorkingMemoryItem:
        """Add an item to working memory.

        If at capacity, the lowest priority item is evicted.
        """
        # First, run decay if needed
        self._maybe_decay()

        # Check capacity — evict if needed
        if len(self._items) >= self.capacity:
            self._evict_lowest()

        # If still at capacity after eviction, force eviction
        if len(self._items) >= self.capacity:
            self._evict_one()

        item_id = f"wm_{uuid.uuid4().hex[:8]}"
        item = WorkingMemoryItem(
            id=item_id,
            content=content,
            item_type=item_type,
            priority=priority,
            source=source,
            tags=tags or [],
            activation=self._initial_activation(priority),
            decay_rate=self._decay_rate_for_priority(priority),
        )

        self._items[item_id] = item
        return item

    def get(self, item_id: str) -> Optional[WorkingMemoryItem]:
        """Get an item and refresh its activation."""
        item = self._items.get(item_id)
        if item:
            self._refresh(item)
        return item

    def remove(self, item_id: str) -> bool:
        """Remove an item from working memory."""
        if item_id in self._items:
            del self._items[item_id]
            return True
        return False

    def clear(self) -> None:
        """Clear all items."""
        self._items.clear()
        self._chunks.clear()

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def active_items(self, limit: Optional[int] = None) -> List[WorkingMemoryItem]:
        """Get all items above the activation threshold, sorted by priority.

        Items are refreshed as a side effect (attentional refresh).
        """
        self._maybe_decay()

        items = [
            item for item in self._items.values()
            if item.activation >= self.min_activation
        ]

        # Sort by priority weight * activation
        items.sort(
            key=lambda i: _PRIORITY_WEIGHTS.get(i.priority, 1.0) * i.activation,
            reverse=True,
        )

        return items[:limit] if limit else items

    def get_by_type(self, item_type: str) -> List[WorkingMemoryItem]:
        """Get all items of a given type."""
        self._maybe_decay()
        return [
            item for item in self._items.values()
            if item.item_type == item_type
            and item.activation >= self.min_activation
        ]

    def get_by_tag(self, tag: str) -> List[WorkingMemoryItem]:
        """Get all items with a given tag."""
        self._maybe_decay()
        return [
            item for item in self._items.values()
            if tag in item.tags
            and item.activation >= self.min_activation
        ]

    def search(self, query: str) -> List[WorkingMemoryItem]:
        """Simple text search across item content."""
        self._maybe_decay()
        query_lower = query.lower()
        results = []

        for item in self._items.values():
            if item.activation < self.min_activation:
                continue

            content_str = str(item.content).lower()
            if query_lower in content_str:
                results.append(item)

        return results

    # ------------------------------------------------------------------
    # Attentional operations
    # ------------------------------------------------------------------

    def focus(self, item_id: str) -> Optional[WorkingMemoryItem]:
        """Focus attention on an item — maximise its activation."""
        item = self._items.get(item_id)
        if item:
            item.activation = min(1.0, item.activation + 0.5)
            item.last_accessed = time.time()
            item.access_count += 1
        return item

    def defocus(self, item_id: str) -> Optional[WorkingMemoryItem]:
        """Defocus an item — reduce its activation."""
        item = self._items.get(item_id)
        if item:
            item.activation = max(0.0, item.activation - 0.3)
        return item

    def promote(self, item_id: str, new_priority: MemoryPriority) -> bool:
        """Promote an item to a higher priority."""
        item = self._items.get(item_id)
        if item is None:
            return False

        old_weight = _PRIORITY_WEIGHTS.get(item.priority, 0)
        new_weight = _PRIORITY_WEIGHTS.get(new_priority, 0)

        if new_weight > old_weight:
            item.priority = new_priority
            item.activation = min(1.0, item.activation + 0.2)
            return True
        return False

    def chunk(self, item_ids: List[str], chunk_label: str) -> Optional[str]:
        """Group items into a chunk (memory chunking).

        Chunking is the cognitive strategy of grouping individual items
        into higher-level units, effectively increasing capacity.
        """
        # Verify all items exist
        for iid in item_ids:
            if iid not in self._items:
                return None

        chunk_id = f"chunk_{uuid.uuid4().hex[:6]}"
        self._chunks[chunk_id] = list(item_ids)

        # Boost the items (they get attention from being chunked)
        for iid in item_ids:
            if iid in self._items:
                self._items[iid].tags.append(f"chunk:{chunk_label}")
                self._items[iid].activation = min(1.0, self._items[iid].activation + 0.1)

        return chunk_id

    # ------------------------------------------------------------------
    # Decay and maintenance
    # ------------------------------------------------------------------

    def decay(self) -> int:
        """Apply decay to all items. Returns number of items removed."""
        now = time.time()
        removed = 0
        to_remove: List[str] = []

        for item_id, item in self._items.items():
            # Time since last access, in decay intervals
            time_since = (now - item.last_accessed) * 1000  # ms
            intervals = time_since / self.decay_interval_ms

            # Activation decreases over time, weighted by decay_rate and priority
            decay_amount = item.decay_rate * min(intervals, 5.0)  # Cap at 5 intervals
            item.activation = max(0.0, item.activation - decay_amount)

            # Drop below threshold?
            if item.activation < self.min_activation:
                # Critical items get a reprieve (can't drop below LOW)
                if item.priority == MemoryPriority.CRITICAL:
                    item.activation = self.min_activation + 0.01
                else:
                    to_remove.append(item_id)

        # Remove dead items
        for item_id in to_remove:
            del self._items[item_id]
            removed += 1

        self._last_decay = now
        return removed

    def maintenance_rehearsal(self) -> int:
        """Refresh all high-priority items (like mental rehearsal).

        This models the "inner voice" that repeats things to keep them
        in working memory. Returns number of items refreshed.
        """
        refreshed = 0
        for item in self._items.values():
            if item.priority in (MemoryPriority.CRITICAL, MemoryPriority.HIGH):
                self._refresh(item)
                refreshed += 1
        return refreshed

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    @property
    def item_count(self) -> int:
        return len(self._items)

    @property
    def is_full(self) -> bool:
        return len(self._items) >= self.capacity

    def stats(self) -> Dict[str, Any]:
        """Get working memory statistics."""
        priority_counts: Dict[str, int] = {}
        type_counts: Dict[str, int] = {}
        total_activation = 0.0

        for item in self._items.values():
            p = item.priority.value
            priority_counts[p] = priority_counts.get(p, 0) + 1

            t = item.item_type
            type_counts[t] = type_counts.get(t, 0) + 1

            total_activation += item.activation

        avg_activation = total_activation / max(1, len(self._items))

        return {
            "item_count": len(self._items),
            "capacity": self.capacity,
            "is_full": self.is_full,
            "utilization": round(len(self._items) / self.capacity, 2),
            "avg_activation": round(avg_activation, 3),
            "priority_distribution": priority_counts,
            "type_distribution": type_counts,
            "chunks": len(self._chunks),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _maybe_decay(self) -> None:
        """Apply decay if enough time has passed."""
        now = time.time()
        elapsed_ms = (now - self._last_decay) * 1000
        if elapsed_ms >= self.decay_interval_ms:
            self.decay()

    def _refresh(self, item: WorkingMemoryItem) -> None:
        """Refresh an item's activation (access = attention)."""
        item.last_accessed = time.time()
        item.access_count += 1
        # Boost activation slightly (attention refreshes memory)
        item.activation = min(1.0, item.activation + 0.15)

    def _initial_activation(self, priority: MemoryPriority) -> float:
        """Initial activation level based on priority."""
        base = {
            MemoryPriority.CRITICAL: 1.0,
            MemoryPriority.HIGH: 0.8,
            MemoryPriority.MEDIUM: 0.6,
            MemoryPriority.LOW: 0.4,
            MemoryPriority.FLEETING: 0.2,
        }
        return base.get(priority, 0.5)

    def _decay_rate_for_priority(self, priority: MemoryPriority) -> float:
        """Decay rate — higher priority decays slower."""
        rates = {
            MemoryPriority.CRITICAL: 0.02,
            MemoryPriority.HIGH: 0.05,
            MemoryPriority.MEDIUM: 0.1,
            MemoryPriority.LOW: 0.2,
            MemoryPriority.FLEETING: 0.5,
        }
        return rates.get(priority, 0.1)

    def _evict_lowest(self) -> int:
        """Evict all items below min_activation."""
        to_remove = [
            iid for iid, item in self._items.items()
            if item.activation < self.min_activation
            and item.priority != MemoryPriority.CRITICAL
        ]
        for iid in to_remove:
            del self._items[iid]
        return len(to_remove)

    def _evict_one(self) -> bool:
        """Force-evict the lowest-scoring item."""
        if not self._items:
            return False

        # Find the item with lowest priority * activation
        lowest_id = min(
            self._items.keys(),
            key=lambda iid: (
                _PRIORITY_WEIGHTS.get(self._items[iid].priority, 0)
                * self._items[iid].activation
            )
        )

        # Never evict critical items unless there's no other choice
        lowest = self._items[lowest_id]
        if lowest.priority == MemoryPriority.CRITICAL:
            # Find the next lowest
            non_critical = [
                iid for iid, item in self._items.items()
                if item.priority != MemoryPriority.CRITICAL
            ]
            if non_critical:
                lowest_id = min(
                    non_critical,
                    key=lambda iid: (
                        _PRIORITY_WEIGHTS.get(self._items[iid].priority, 0)
                        * self._items[iid].activation
                    )
                )
            else:
                # All are critical — still need to evict one
                pass  # Keep the originally selected one

        del self._items[lowest_id]
        return True
