"""Plan Memory — store and reuse historical plans.

Why plan memory matters:
  1. **Speed** — similar goals can reuse previous plans (no LLM call needed)
  2. **Quality** — proven plans get better over time (survivorship bias in reverse)
  3. **Consistency** — same goal → same plan (deterministic behaviour)
  4. **Learning** — failed plans inform future planning (avoid the same mistakes)

The memory stores:
  - Goal text → plan mapping (for exact or near-exact matches)
  - Plan success/failure history (for quality weighting)
  - Plan execution metrics (duration, resource usage)

This is separate from the general MemoryKernel — it's a planner-specific
cache with planner-specific indexing and scoring.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class PlanRecord:
    """A stored plan with execution history."""
    goal_text: str
    goal_hash: str
    plan_data: Dict[str, Any]  # Serialised ParsedPlan
    strategy: str = "unknown"
    success_count: int = 0
    failure_count: int = 0
    avg_duration_ms: float = 0.0
    last_used: float = 0.0
    created_at: float = 0.0
    tags: List[str] = field(default_factory=list)
    scope: str = "L1"

    @property
    def total_uses(self) -> int:
        return self.success_count + self.failure_count

    @property
    def success_rate(self) -> float:
        if self.total_uses == 0:
            return 0.0
        return self.success_count / self.total_uses

    def to_dict(self) -> Dict[str, Any]:
        return {
            "goal_text": self.goal_text,
            "goal_hash": self.goal_hash,
            "plan_data": self.plan_data,
            "strategy": self.strategy,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "avg_duration_ms": self.avg_duration_ms,
            "last_used": self.last_used,
            "created_at": self.created_at,
            "tags": self.tags,
            "scope": self.scope,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PlanRecord":
        return cls(
            goal_text=data["goal_text"],
            goal_hash=data["goal_hash"],
            plan_data=data["plan_data"],
            strategy=data.get("strategy", "unknown"),
            success_count=data.get("success_count", 0),
            failure_count=data.get("failure_count", 0),
            avg_duration_ms=data.get("avg_duration_ms", 0.0),
            last_used=data.get("last_used", 0.0),
            created_at=data.get("created_at", 0.0),
            tags=data.get("tags", []),
            scope=data.get("scope", "L1"),
        )


class PlanMemory:
    """Memory for storing and retrieving plans.

    Features:
      - Exact match lookup (hash-based, instant)
      - Fuzzy match lookup (keyword-based, for similar goals)
      - Quality-weighted retrieval (prefer plans with high success rate)
      - TTL and LRU eviction (bounded memory)
      - Persistence to disk (JSON format)

    Usage::

        memory = PlanMemory(max_size=1000)
        memory.store(goal_text, plan_dict, strategy="llm")

        # Later...
        record = memory.lookup("calculate 2+3")
        if record:
            use_plan(record.plan_data)
    """

    def __init__(
        self,
        max_size: int = 1000,
        min_success_rate: float = 0.0,
        persist_path: Optional[str] = None,
    ):
        """
        Args:
            max_size: Maximum number of plans to store (LRU eviction)
            min_success_rate: Minimum success rate for a plan to be retrieved
            persist_path: Optional path for JSON persistence
        """
        self.max_size = max_size
        self.min_success_rate = min_success_rate
        self.persist_path = persist_path

        self._plans: Dict[str, PlanRecord] = {}  # goal_hash -> PlanRecord
        self._access_order: List[str] = []  # LRU order

        # Load from disk if path provided
        if persist_path:
            self._load()

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    def store(
        self,
        goal_text: str,
        plan_data: Dict[str, Any],
        strategy: str = "unknown",
        scope: str = "L1",
        tags: Optional[List[str]] = None,
    ) -> PlanRecord:
        """Store a plan in memory.

        If a plan for the same goal already exists, update it with the
        new plan data only if the new one is better (higher success rate).
        """
        goal_hash = self._hash_goal(goal_text)
        now = time.time()

        existing = self._plans.get(goal_hash)
        if existing:
            # Update existing record — refresh plan data and bump access
            existing.plan_data = plan_data
            existing.last_used = now
            existing.strategy = strategy
            if tags:
                for t in tags:
                    if t not in existing.tags:
                        existing.tags.append(t)
            self._touch(goal_hash)
            return existing

        # Evict if at capacity
        if len(self._plans) >= self.max_size:
            self._evict_lru()

        record = PlanRecord(
            goal_text=goal_text,
            goal_hash=goal_hash,
            plan_data=plan_data,
            strategy=strategy,
            created_at=now,
            last_used=now,
            tags=tags or [],
            scope=scope,
        )
        self._plans[goal_hash] = record
        self._access_order.append(goal_hash)

        self._save_if_needed()
        return record

    def lookup(self, goal_text: str, scope: Optional[str] = None) -> Optional[PlanRecord]:
        """Look up a plan by exact goal match.

        Returns the best matching plan, or None if no suitable plan found.
        """
        goal_hash = self._hash_goal(goal_text)
        record = self._plans.get(goal_hash)

        if record is None:
            return None

        # Check scope
        if scope and record.scope != scope:
            return None

        # Check success rate threshold
        if record.total_uses > 0 and record.success_rate < self.min_success_rate:
            return None

        self._touch(goal_hash)
        return record

    def lookup_similar(
        self,
        goal_text: str,
        scope: Optional[str] = None,
        top_k: int = 3,
        min_similarity: float = 0.5,
    ) -> List[PlanRecord]:
        """Look up similar plans using keyword-based similarity.

        This is a lightweight fuzzy match — for production you'd want
        embeddings-based semantic search, but that requires an embedding
        model which may not be available.

        Args:
            goal_text: The goal to find similar plans for
            scope: Filter by scope
            top_k: Number of results to return
            min_similarity: Minimum similarity score (0-1)

        Returns:
            List of PlanRecord, sorted by relevance
        """
        goal_words = set(self._tokenize(goal_text))
        if not goal_words:
            return []

        scored: List[Tuple[float, PlanRecord]] = []

        for record in self._plans.values():
            if scope and record.scope != scope:
                continue
            if record.total_uses > 0 and record.success_rate < self.min_success_rate:
                continue

            record_words = set(self._tokenize(record.goal_text))
            if not record_words:
                continue

            # Jaccard similarity
            intersection = goal_words & record_words
            union = goal_words | record_words
            similarity = len(intersection) / len(union) if union else 0.0

            if similarity >= min_similarity:
                # Boost by success rate
                quality_score = similarity * (0.5 + 0.5 * record.success_rate)
                scored.append((quality_score, record))

        # Sort by score descending
        scored.sort(key=lambda x: x[0], reverse=True)
        return [record for _, record in scored[:top_k]]

    def record_result(
        self,
        goal_text: str,
        success: bool,
        duration_ms: float = 0.0,
    ) -> Optional[PlanRecord]:
        """Record the success/failure of a plan execution.

        Updates the plan's success/failure counts and average duration.
        """
        goal_hash = self._hash_goal(goal_text)
        record = self._plans.get(goal_hash)

        if record is None:
            return None

        if success:
            record.success_count += 1
        else:
            record.failure_count += 1

        # Update average duration (exponential moving average)
        if duration_ms > 0:
            if record.avg_duration_ms == 0:
                record.avg_duration_ms = duration_ms
            else:
                record.avg_duration_ms = (
                    0.7 * record.avg_duration_ms + 0.3 * duration_ms
                )

        record.last_used = time.time()
        self._touch(goal_hash)
        self._save_if_needed()

        return record

    # ------------------------------------------------------------------
    # Stats and introspection
    # ------------------------------------------------------------------

    @property
    def size(self) -> int:
        return len(self._plans)

    def stats(self) -> Dict[str, Any]:
        """Get memory statistics."""
        total_success = sum(r.success_count for r in self._plans.values())
        total_failure = sum(r.failure_count for r in self._plans.values())
        total_uses = total_success + total_failure
        overall_success_rate = (
            total_success / total_uses if total_uses > 0 else 0.0
        )

        return {
            "size": len(self._plans),
            "max_size": self.max_size,
            "total_executions": total_uses,
            "total_success": total_success,
            "total_failure": total_failure,
            "overall_success_rate": overall_success_rate,
            "hit_rate_estimate": 0.0,  # Would need to track lookups
        }

    def clear(self) -> None:
        """Clear all stored plans."""
        self._plans.clear()
        self._access_order.clear()
        self._save_if_needed()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _hash_goal(goal_text: str) -> str:
        """Create a stable hash of a goal for exact-match lookup.

        Normalises whitespace and case to catch near-duplicates.
        """
        normalised = " ".join(goal_text.lower().split())
        return hashlib.md5(normalised.encode("utf-8")).hexdigest()

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        """Simple tokenizer for keyword similarity."""
        # Lowercase, split on non-alphanumeric, filter short tokens
        import re
        tokens = re.findall(r"[a-zA-Z0-9_\u4e00-\u9fff]+", text.lower())
        return [t for t in tokens if len(t) >= 2]

    def _touch(self, goal_hash: str) -> None:
        """Mark a plan as recently used (for LRU)."""
        if goal_hash in self._access_order:
            self._access_order.remove(goal_hash)
        self._access_order.append(goal_hash)

    def _evict_lru(self) -> None:
        """Evict the least recently used plan."""
        if self._access_order:
            oldest = self._access_order.pop(0)
            self._plans.pop(oldest, None)

    def _save_if_needed(self) -> None:
        """Save to disk if persist_path is set."""
        if not self.persist_path:
            return
        try:
            data = {
                "plans": [r.to_dict() for r in self._plans.values()],
                "access_order": self._access_order,
            }
            with open(self.persist_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            # Persistence failure should never break the planner
            pass

    def _load(self) -> None:
        """Load from disk."""
        if not self.persist_path:
            return
        try:
            with open(self.persist_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for plan_data in data.get("plans", []):
                record = PlanRecord.from_dict(plan_data)
                self._plans[record.goal_hash] = record
            self._access_order = data.get("access_order", [])
        except (FileNotFoundError, json.JSONDecodeError):
            # No file or corrupt file — start fresh
            pass
