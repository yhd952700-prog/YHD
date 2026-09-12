"""Trust Kernel — Trust Scores + Chain + Revocation

The Trust Kernel maintains trust scores and trust chains for agents,
capabilities, and entities. Supports trust propagation, revocation,
and scope-aware trust evaluation.

依据 Definition Lock §112: Trust Kernel 必须能够
- Assign and update trust scores (0.0 - 1.0)
- Maintain trust chains with transitive propagation
- Revoke trust with cascade effects
- Evaluate trust for access decisions
- Support scope-aware trust (L0-L7)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from src._time import utc_now
from enum import Enum
from typing import Any, Dict, List, Optional, Set
import uuid
import threading

from src.kernels._crosscutting import kernel_action


class TrustScope(str, Enum):
    """Trust evaluation scope L0-L7."""
    L0 = "L0"
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"
    L4 = "L4"
    L5 = "L5"
    L6 = "L6"
    L7 = "L7"


class TrustLevel(str, Enum):
    """Trust level categories."""
    UNTRUSTED = "untrusted"      # 0.0 - 0.2
    LOW = "low"                  # 0.2 - 0.4
    MEDIUM = "medium"            # 0.4 - 0.6
    HIGH = "high"                # 0.6 - 0.8
    VERY_HIGH = "very_high"      # 0.8 - 1.0


class TrustEventType(str, Enum):
    """Types of trust-affecting events."""
    POSITIVE = "positive"        # Successful action, good behavior
    NEGATIVE = "negative"        # Failed action, violation
    NEUTRAL = "neutral"          # Normal operation
    REVOCATION = "revocation"    # Explicit trust revocation
    PROPAGATION = "propagation"  # Trust propagated from another entity


@dataclass
class TrustEvent:
    """Record of a trust-affecting event."""
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    entity_id: str = ""
    event_type: TrustEventType = TrustEventType.NEUTRAL
    score_delta: float = 0.0  # Change to trust score
    reason: str = ""
    scope: TrustScope = TrustScope.L0
    source_entity: Optional[str] = None  # For propagation events
    correlation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=utc_now)


@dataclass
class TrustScore:
    """Trust score for an entity."""
    entity_id: str
    score: float = 0.5  # 0.0 - 1.0
    scope: TrustScope = TrustScope.L0
    level: TrustLevel = TrustLevel.MEDIUM
    confidence: float = 0.5  # How confident we are in this score
    event_count: int = 0
    positive_events: int = 0
    negative_events: int = 0
    last_updated: datetime = field(default_factory=utc_now)
    created_at: datetime = field(default_factory=utc_now)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        self._update_level()

    def _update_level(self):
        """Update trust level based on score."""
        if self.score <= 0.2:
            self.level = TrustLevel.UNTRUSTED
        elif self.score <= 0.4:
            self.level = TrustLevel.LOW
        elif self.score <= 0.6:
            self.level = TrustLevel.MEDIUM
        elif self.score <= 0.8:
            self.level = TrustLevel.HIGH
        else:
            self.level = TrustLevel.VERY_HIGH

    @property
    def is_trusted(self) -> bool:
        return self.score >= 0.5

    @property
    def is_highly_trusted(self) -> bool:
        return self.score >= 0.7


@dataclass
class TrustChainLink:
    """Single link in a trust chain."""
    from_entity: str
    to_entity: str
    trust_score: float  # Trust that from_entity places in to_entity
    scope: TrustScope
    established_at: datetime = field(default_factory=utc_now)
    expires_at: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    active: bool = True

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return utc_now() > self.expires_at


@dataclass
class TrustChain:
    """Trust chain from source to target through intermediaries."""
    source: str
    target: str
    links: List[TrustChainLink] = field(default_factory=list)
    composite_score: float = 0.0
    scope: TrustScope = TrustScope.L0
    valid: bool = True
    computed_at: datetime = field(default_factory=utc_now)

    def __post_init__(self):
        self._compute_composite()

    def _compute_composite(self):
        """Compute composite trust score from chain links."""
        if not self.links:
            self.composite_score = 0.0
            self.valid = False
            return

        # Multiplicative trust propagation with decay
        score = 1.0
        decay_factor = 0.9  # Each hop reduces trust by 10%

        for i, link in enumerate(self.links):
            if not link.active or link.is_expired:
                self.valid = False
                self.composite_score = 0.0
                return
            # Apply decay per hop
            hop_score = link.trust_score * (decay_factor ** i)
            score *= hop_score

        self.composite_score = score
        self.valid = score > 0.1  # Minimum threshold


class TrustManager:
    """Manages trust scores, chains, and revocation."""

    def __init__(self):
        self._scores: Dict[str, Dict[TrustScope, TrustScore]] = {}  # entity_id -> {scope -> TrustScore}
        self._chains: Dict[str, List[TrustChainLink]] = {}  # from_entity -> list of links
        self._events: List[TrustEvent] = []
        self._revoked: Set[str] = set()  # Revoked entity IDs
        self._lock = threading.RLock()

        # Default trust configuration
        self._default_initial_score = 0.5
        self._positive_weight = 0.1
        self._negative_weight = -0.2
        self._propagation_decay = 0.9
        self._min_score = 0.0
        self._max_score = 1.0
        self._max_events_per_entity = 1000

    def _get_score(self, entity_id: str, scope: TrustScope) -> TrustScore:
        """Get or create trust score for entity at scope."""
        if entity_id not in self._scores:
            self._scores[entity_id] = {}

        if scope not in self._scores[entity_id]:
            self._scores[entity_id][scope] = TrustScore(
                entity_id=entity_id,
                score=self._default_initial_score,
                scope=scope,
            )

        return self._scores[entity_id][scope]

    @kernel_action("trust.assign_score")
    def assign_score(
        self,
        entity_id: str,
        initial_score: float = 0.5,
        scope: TrustScope = TrustScope.L0,
        reasons: Optional[List[str]] = None,
        confidence: float = 0.5
    ) -> TrustScore:
        """Assign initial trust score to an entity."""
        with self._lock:
            if entity_id in self._revoked:
                raise ValueError(f"Entity {entity_id} is revoked")

            # Clamp score
            score_val = max(self._min_score, min(self._max_score, initial_score))

            trust_score = self._get_score(entity_id, scope)
            trust_score.score = score_val
            trust_score.confidence = confidence
            trust_score.last_updated = utc_now()
            trust_score._update_level()

            # Record event
            event = TrustEvent(
                entity_id=entity_id,
                event_type=TrustEventType.POSITIVE if score_val > 0.5 else TrustEventType.NEGATIVE,
                score_delta=score_val - 0.5,
                reason=f"Initial assignment: {', '.join(reasons or [])}",
                scope=scope,
                metadata={"initial": True, "confidence": confidence},
            )
            self._events.append(event)

            return trust_score

    @kernel_action("trust.update_score")
    def update_score(
        self,
        entity_id: str,
        delta: float,
        scope: TrustScope = TrustScope.L0,
        reason: str = "",
        event_type: TrustEventType = TrustEventType.NEUTRAL,
        correlation_id: Optional[str] = None
    ) -> TrustScore:
        """Update trust score by delta."""
        with self._lock:
            if entity_id in self._revoked:
                raise ValueError(f"Entity {entity_id} is revoked")

            trust_score = self._get_score(entity_id, scope)

            # Apply delta with confidence weighting
            old_score = trust_score.score
            new_score = max(self._min_score, min(self._max_score, old_score + delta))

            trust_score.score = new_score
            trust_score.last_updated = utc_now()
            trust_score._update_level()
            trust_score.event_count += 1

            if delta > 0:
                trust_score.positive_events += 1
            elif delta < 0:
                trust_score.negative_events += 1

            # Update confidence based on event count
            trust_score.confidence = min(1.0, trust_score.event_count / 100.0)

            # Record event
            event = TrustEvent(
                entity_id=entity_id,
                event_type=event_type,
                score_delta=new_score - old_score,
                reason=reason,
                scope=scope,
                correlation_id=correlation_id or str(uuid.uuid4()),
            )
            self._events.append(event)

            # Prune old events for THIS entity only (per-entity cap). A flood
            # of events from one entity must never silently discard another
            # entity's history.
            entity_events = [e for e in self._events if e.entity_id == entity_id]
            if len(entity_events) > self._max_events_per_entity:
                excess = len(entity_events) - self._max_events_per_entity
                drop_ids = {id(e) for e in entity_events[:excess]}
                self._events = [e for e in self._events if id(e) not in drop_ids]

            return trust_score

    def get_score(self, entity_id: str, scope: TrustScope = TrustScope.L0) -> Optional[TrustScore]:
        """Get trust score for entity at scope."""
        with self._lock:
            if entity_id in self._scores and scope in self._scores[entity_id]:
                return self._scores[entity_id][scope]
            return None

    def get_all_scores(self, entity_id: str) -> Dict[TrustScope, TrustScore]:
        """Get all trust scores for an entity across scopes."""
        with self._lock:
            return self._scores.get(entity_id, {}).copy()

    def get_score_at_scope_or_higher(self, entity_id: str, min_scope: TrustScope) -> Optional[TrustScore]:
        """Get highest scope score at or above min_scope."""
        with self._lock:
            scores = self._scores.get(entity_id, {})
            scope_order = {s: i for i, s in enumerate(TrustScope)}
            min_idx = scope_order[min_scope]

            candidates = [
                (scope, score) for scope, score in scores.items()
                if scope_order[scope] >= min_idx
            ]

            if not candidates:
                return None

            # Return highest scope (most autonomy)
            return max(candidates, key=lambda x: scope_order[x[0]])[1]

    @kernel_action("trust.establish_trust")
    def establish_trust(
        self,
        from_entity: str,
        to_entity: str,
        trust_score: float,
        scope: TrustScope = TrustScope.L0,
        expires_in: Optional[timedelta] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> TrustChainLink:
        """Establish a trust relationship (from_entity trusts to_entity)."""
        with self._lock:
            if from_entity in self._revoked or to_entity in self._revoked:
                raise ValueError("Cannot establish trust with revoked entity")

            # Clamp score
            score = max(self._min_score, min(self._max_score, trust_score))

            expires_at = None
            if expires_in:
                expires_at = utc_now() + expires_in

            link = TrustChainLink(
                from_entity=from_entity,
                to_entity=to_entity,
                trust_score=score,
                scope=scope,
                expires_at=expires_at,
                metadata=metadata or {},
            )

            if from_entity not in self._chains:
                self._chains[from_entity] = []

            # Check for existing link
            existing = None
            for existing_link in self._chains[from_entity]:
                if existing_link.to_entity == to_entity and existing_link.scope == scope:
                    existing = existing_link
                    break

            if existing:
                existing.trust_score = score
                existing.expires_at = expires_at
                existing.metadata = metadata or {}
                existing.active = True
                return existing

            self._chains[from_entity].append(link)

            # Record propagation event
            event = TrustEvent(
                entity_id=to_entity,
                event_type=TrustEventType.PROPAGATION,
                score_delta=0.0,
                reason=f"Trust established from {from_entity}",
                scope=scope,
                source_entity=from_entity,
                metadata={"trust_score": score, "link_id": id(link)},
            )
            self._events.append(event)

            return link

    def get_trust_chain(
        self,
        source: str,
        target: str,
        scope: TrustScope = TrustScope.L0,
        max_hops: int = 5
    ) -> TrustChain:
        """Compute trust chain from source to target."""
        with self._lock:
            chain = TrustChain(source=source, target=target, scope=scope)

            if source == target:
                # Self-trust
                chain.links = [TrustChainLink(
                    from_entity=source,
                    to_entity=target,
                    trust_score=1.0,
                    scope=scope,
                )]
                chain._compute_composite()
                return chain

            # BFS to find paths
            visited = set()
            queue = [(source, [])]

            while queue and len(queue[0][1]) < max_hops:
                current, path = queue.pop(0)

                if current in visited:
                    continue
                visited.add(current)

                links = self._chains.get(current, [])
                for link in links:
                    if not link.active or link.is_expired:
                        continue
                    if scope_order(link.scope) < scope_order(scope):
                        continue  # Link scope too low

                    new_path = path + [link]
                    if link.to_entity == target:
                        # Found path
                        chain.links = new_path
                        chain._compute_composite()
                        return chain

                    if link.to_entity not in visited:
                        queue.append((link.to_entity, new_path))

            # No path found
            chain.valid = False
            chain.composite_score = 0.0
            return chain

    def propagate_trust(
        self,
        source: str,
        target: str,
        scope: TrustScope = TrustScope.L0
    ) -> float:
        """Get propagated trust score from source to target."""
        chain = self.get_trust_chain(source, target, scope)
        return chain.composite_score if chain.valid else 0.0

    @kernel_action("trust.revoke")
    def revoke(self, entity_id: str, scope: Optional[TrustScope] = None, reason: str = "") -> bool:
        """Revoke trust for an entity."""
        with self._lock:
            self._revoked.add(entity_id)

            # Remove all trust scores
            if scope:
                if entity_id in self._scores and scope in self._scores[entity_id]:
                    del self._scores[entity_id][scope]
            else:
                if entity_id in self._scores:
                    del self._scores[entity_id]

            # Remove all trust chains involving this entity
            # As source
            if entity_id in self._chains:
                del self._chains[entity_id]

            # As target
            for from_entity, links in self._chains.items():
                self._chains[from_entity] = [
                    link for link in links if link.to_entity != entity_id
                ]

            # Record revocation event
            event = TrustEvent(
                entity_id=entity_id,
                event_type=TrustEventType.REVOCATION,
                score_delta=0.0,
                reason=reason or "Explicit revocation",
                scope=scope or TrustScope.L0,
                metadata={"revoked": True},
            )
            self._events.append(event)

            return True

    def is_revoked(self, entity_id: str) -> bool:
        """Check if entity is revoked."""
        with self._lock:
            return entity_id in self._revoked

    def get_trust_events(
        self,
        entity_id: Optional[str] = None,
        event_type: Optional[TrustEventType] = None,
        since: Optional[datetime] = None,
        limit: int = 100
    ) -> List[TrustEvent]:
        """Get trust events with filters."""
        with self._lock:
            events = self._events

            if entity_id:
                events = [e for e in events if e.entity_id == entity_id]
            if event_type:
                events = [e for e in events if e.event_type == event_type]
            if since:
                events = [e for e in events if e.timestamp >= since]

            return events[-limit:]

    def evaluate_trust_for_access(
        self,
        entity_id: str,
        required_level: TrustLevel = TrustLevel.MEDIUM,
        required_scope: TrustScope = TrustScope.L0
    ) -> bool:
        """Evaluate if entity meets trust requirements for access."""
        with self._lock:
            if entity_id in self._revoked:
                return False

            score = self.get_score_at_scope_or_higher(entity_id, required_scope)
            if not score:
                return False

            # Check level
            level_order = {
                TrustLevel.UNTRUSTED: 0,
                TrustLevel.LOW: 1,
                TrustLevel.MEDIUM: 2,
                TrustLevel.HIGH: 3,
                TrustLevel.VERY_HIGH: 4,
            }

            return level_order[score.level] >= level_order[required_level]

    def stats(self) -> Dict[str, Any]:
        """Get manager statistics."""
        with self._lock:
            total_entities = len(self._scores)
            total_chains = sum(len(links) for links in self._chains.values())
            total_events = len(self._events)
            revoked_count = len(self._revoked)

            by_level = {}
            for scopes in self._scores.values():
                for score in scopes.values():
                    by_level[score.level.value] = by_level.get(score.level.value, 0) + 1

            return {
                "total_entities": total_entities,
                "total_chain_links": total_chains,
                "total_events": total_events,
                "revoked_entities": revoked_count,
                "by_trust_level": by_level,
            }


def scope_order(scope: TrustScope) -> int:
    """Get numeric order for scope comparison."""
    return {s: i for i, s in enumerate(TrustScope)}[scope]


# Global trust manager instance
_global_manager: Optional[TrustManager] = None
_global_lock = threading.Lock()


def get_trust_manager() -> TrustManager:
    """Get or create the global trust manager."""
    global _global_manager
    if _global_manager is None:
        with _global_lock:
            if _global_manager is None:
                _global_manager = TrustManager()
    return _global_manager


# Convenience functions
def assign_trust_score(
    entity_id: str,
    initial_score: float = 0.5,
    scope: TrustScope = TrustScope.L0,
    reasons: Optional[List[str]] = None,
    confidence: float = 0.5
) -> TrustScore:
    """Assign initial trust score."""
    return get_trust_manager().assign_score(entity_id, initial_score, scope, reasons, confidence)


def update_trust_score(
    entity_id: str,
    delta: float,
    scope: TrustScope = TrustScope.L0,
    reason: str = "",
    event_type: TrustEventType = TrustEventType.NEUTRAL,
) -> TrustScore:
    """Update trust score."""
    return get_trust_manager().update_score(entity_id, delta, scope, reason, event_type)


def get_trust_score(entity_id: str, scope: TrustScope = TrustScope.L0) -> Optional[TrustScore]:
    """Get trust score."""
    return get_trust_manager().get_score(entity_id, scope)


def establish_trust(
    from_entity: str,
    to_entity: str,
    trust_score: float,
    scope: TrustScope = TrustScope.L0,
    expires_in: Optional[timedelta] = None,
) -> TrustChainLink:
    """Establish trust relationship."""
    return get_trust_manager().establish_trust(from_entity, to_entity, trust_score, scope, expires_in)


def get_trust_chain(source: str, target: str, scope: TrustScope = TrustScope.L0) -> TrustChain:
    """Get trust chain."""
    return get_trust_manager().get_trust_chain(source, target, scope)


def propagate_trust(source: str, target: str, scope: TrustScope = TrustScope.L0) -> float:
    """Get propagated trust score."""
    return get_trust_manager().propagate_trust(source, target, scope)


def revoke_trust(entity_id: str, scope: Optional[TrustScope] = None, reason: str = "") -> bool:
    """Revoke trust."""
    return get_trust_manager().revoke(entity_id, scope, reason)


def evaluate_trust(entity_id: str, required_level: TrustLevel = TrustLevel.MEDIUM, required_scope: TrustScope = TrustScope.L0) -> bool:
    """Evaluate trust for access."""
    return get_trust_manager().evaluate_trust_for_access(entity_id, required_level, required_scope)
