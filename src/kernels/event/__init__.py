"""Event Kernel — Unified Event Bus + Correlation IDs

The Event Kernel provides a unified event bus for inter-kernel
communication and cross-cutting concerns. All events carry correlation
IDs for end-to-end traceability.

依据 Definition Lock §112: Event Kernel 必须能够
- Publish events to typed channels
- Subscribe to events with scope filtering
- Maintain correlation IDs across event chains
- Support event replay and dead letter handling
- Provide ordering guarantees per correlation ID
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from src._time import utc_now
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set
from collections import defaultdict
import uuid
import threading

from src.kernels._crosscutting import kernel_action


class EventScope(str, Enum):
    """Event permission scope L0-L7."""
    L0 = "L0"  # Human only
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"
    L4 = "L4"
    L5 = "L5"
    L6 = "L6"
    L7 = "L7"  # Full autonomy


class EventPriority(str, Enum):
    """Event priority levels."""
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class Event:
    """Unified event structure with correlation ID."""
    type: str
    source: str
    data: Any = None
    correlation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    causation_id: Optional[str] = None  # ID of event that caused this one
    scope: EventScope = EventScope.L0
    priority: EventPriority = EventPriority.NORMAL
    timestamp: datetime = field(default_factory=utc_now)
    metadata: Dict[str, Any] = field(default_factory=dict)
    tags: Set[str] = field(default_factory=set)

    def __post_init__(self):
        if self.causation_id is None:
            self.causation_id = self.correlation_id

    def with_correlation(self, correlation_id: str) -> "Event":
        """Create a new event with same correlation chain."""
        new_event = Event(
            type=self.type,
            source=self.source,
            data=self.data,
            correlation_id=correlation_id,
            causation_id=self.correlation_id,
            scope=self.scope,
            priority=self.priority,
            metadata=self.metadata.copy(),
            tags=self.tags.copy(),
        )
        return new_event


@dataclass
class Subscription:
    """Event subscription with handler and filters."""
    id: str
    event_type: str
    handler: Callable[[Event], None]
    scope: EventScope = EventScope.L0
    filters: Dict[str, Any] = field(default_factory=dict)
    correlation_filter: Optional[str] = None
    created_at: datetime = field(default_factory=utc_now)
    active: bool = True

    def matches(self, event: Event) -> bool:
        """Check if event matches this subscription."""
        if not self.active:
            return False
        if self.event_type != "*" and self.event_type != event.type:
            return False

        # Scope check: subscription scope must be >= event scope
        scope_order = {s: i for i, s in enumerate(EventScope)}
        if scope_order[self.scope] < scope_order[event.scope]:
            return False

        # Correlation filter
        if self.correlation_filter and self.correlation_filter != event.correlation_id:
            return False

        # Custom filters
        for key, value in self.filters.items():
            if key not in event.metadata or event.metadata[key] != value:
                return False

        return True


class DeadLetterEntry:
    """Failed event for retry/analysis."""

    def __init__(
        self,
        event: Event,
        error: Exception,
        subscription_id: str,
        attempt: int = 1
    ):
        self.event = event
        self.error = error
        self.subscription_id = subscription_id
        self.attempt = attempt
        self.timestamp = utc_now()
        self.resolved = False


class EventBus:
    """Unified event bus with correlation ID tracking."""

    def __init__(self):
        self._subscriptions: Dict[str, List[Subscription]] = defaultdict(list)
        self._event_history: List[Event] = []
        self._max_history = 10000
        self._dead_letters: List[DeadLetterEntry] = []
        self._lock = threading.RLock()
        self._correlation_index: Dict[str, List[Event]] = defaultdict(list)

    @kernel_action("event.publish")
    def publish(self, event: Event) -> str:
        """Publish an event to all matching subscribers."""
        with self._lock:
            # Store in history
            self._event_history.append(event)
            if len(self._event_history) > self._max_history:
                self._event_history = self._event_history[-self._max_history:]

            # Index by correlation ID
            self._correlation_index[event.correlation_id].append(event)

            # Find matching subscriptions
            subscriptions = self._subscriptions.get(event.type, [])
            subscriptions.extend(self._subscriptions.get("*", []))

            for sub in subscriptions:
                if sub.matches(event):
                    try:
                        sub.handler(event)
                    except Exception as e:
                        # Dead letter handling
                        dl = DeadLetterEntry(event, e, sub.id)
                        self._dead_letters.append(dl)

            return event.correlation_id

    def publish_async(self, event: Event) -> str:
        """Publish event asynchronously (fire and forget)."""
        # In production, this would use a thread pool or async queue
        return self.publish(event)

    @kernel_action("event.subscribe")
    def subscribe(
        self,
        event_type: str,
        handler: Callable[[Event], None],
        scope: EventScope = EventScope.L0,
        filters: Optional[Dict[str, Any]] = None,
        correlation_id: Optional[str] = None
    ) -> str:
        """Subscribe to events. Returns subscription ID."""
        sub = Subscription(
            id=str(uuid.uuid4()),
            event_type=event_type,
            handler=handler,
            scope=scope,
            filters=filters or {},
            correlation_filter=correlation_id,
        )
        with self._lock:
            self._subscriptions[event_type].append(sub)
        return sub.id

    @kernel_action("event.unsubscribe")
    def unsubscribe(self, subscription_id: str) -> bool:
        """Unsubscribe by ID."""
        with self._lock:
            for event_type, subs in self._subscriptions.items():
                for i, sub in enumerate(subs):
                    if sub.id == subscription_id:
                        subs.pop(i)
                        return True
        return False

    def get_subscriptions(self, event_type: Optional[str] = None) -> List[Subscription]:
        """Get all subscriptions, optionally filtered by event type."""
        with self._lock:
            if event_type:
                return list(self._subscriptions.get(event_type, []))
            result = []
            for subs in self._subscriptions.values():
                result.extend(subs)
            return result

    def get_event_history(
        self,
        correlation_id: Optional[str] = None,
        event_type: Optional[str] = None,
        since: Optional[datetime] = None,
        limit: int = 100
    ) -> List[Event]:
        """Get event history with filters."""
        with self._lock:
            if correlation_id:
                events = self._correlation_index.get(correlation_id, [])
            else:
                events = self._event_history

            if event_type:
                events = [e for e in events if e.type == event_type]
            if since:
                events = [e for e in events if e.timestamp >= since]

            return events[-limit:]

    def get_correlation_chain(self, correlation_id: str) -> List[Event]:
        """Get full event chain for a correlation ID."""
        with self._lock:
            return list(self._correlation_index.get(correlation_id, []))

    def get_dead_letters(self, unresolved_only: bool = True) -> List[DeadLetterEntry]:
        """Get dead letter events."""
        with self._lock:
            if unresolved_only:
                return [dl for dl in self._dead_letters if not dl.resolved]
            return list(self._dead_letters)

    @kernel_action("event.retry_dead_letter")
    def retry_dead_letter(self, index: int) -> bool:
        """Retry a dead letter event."""
        with self._lock:
            if 0 <= index < len(self._dead_letters):
                dl = self._dead_letters[index]
                dl.attempt += 1
                try:
                    # Re-publish to same subscriptions
                    self.publish(dl.event)
                    dl.resolved = True
                    return True
                except Exception:
                    return False
        return False

    @kernel_action("event.clear_history")
    def clear_history(self) -> None:
        """Clear event history (use with caution)."""
        with self._lock:
            self._event_history.clear()
            self._correlation_index.clear()

    def stats(self) -> Dict[str, Any]:
        """Get bus statistics."""
        with self._lock:
            return {
                "total_subscriptions": sum(len(s) for s in self._subscriptions.values()),
                "subscription_types": len(self._subscriptions),
                "event_history_size": len(self._event_history),
                "dead_letters_total": len(self._dead_letters),
                "dead_letters_unresolved": len([dl for dl in self._dead_letters if not dl.resolved]),
                "correlation_chains": len(self._correlation_index),
            }


# Global event bus instance
_global_bus: Optional[EventBus] = None
_global_lock = threading.Lock()


def get_event_bus() -> EventBus:
    """Get or create the global event bus."""
    global _global_bus
    if _global_bus is None:
        with _global_lock:
            if _global_bus is None:
                _global_bus = EventBus()
    return _global_bus


# Convenience functions
def publish_event(
    type: str,
    source: str,
    data: Any = None,
    correlation_id: Optional[str] = None,
    causation_id: Optional[str] = None,
    scope: EventScope = EventScope.L0,
    priority: EventPriority = EventPriority.NORMAL,
    metadata: Optional[Dict[str, Any]] = None,
    tags: Optional[Set[str]] = None
) -> str:
    """Publish an event with convenience parameters."""
    event = Event(
        type=type,
        source=source,
        data=data,
        correlation_id=correlation_id or str(uuid.uuid4()),
        causation_id=causation_id,
        scope=scope,
        priority=priority,
        metadata=metadata or {},
        tags=tags or set(),
    )
    return get_event_bus().publish(event)


def subscribe_event(
    event_type: str,
    handler: Callable[[Event], None],
    scope: EventScope = EventScope.L0,
    filters: Optional[Dict[str, Any]] = None,
    correlation_id: Optional[str] = None
) -> str:
    """Subscribe to events with convenience parameters."""
    return get_event_bus().subscribe(event_type, handler, scope, filters, correlation_id)


def get_event_chain(correlation_id: str) -> List[Event]:
    """Get full event chain for correlation ID."""
    return get_event_bus().get_correlation_chain(correlation_id)


def create_correlation_id() -> str:
    """Generate a new correlation ID."""
    return str(uuid.uuid4())
