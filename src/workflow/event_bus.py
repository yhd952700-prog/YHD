"""Lightweight workflow event bus for Phase 3 automation primitives."""

from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional


class EventBus:
    """A tiny in-memory event bus for workflow event publication."""

    def __init__(self):
        self._handlers: Dict[str, List[Callable[[Dict[str, Any]], None]]] = defaultdict(list)

    def subscribe(self, event_type: str, handler: Callable[[Dict[str, Any]], None]) -> None:
        self._handlers[event_type].append(handler)

    def unsubscribe(self, event_type: str, handler: Callable[[Dict[str, Any]], None]) -> None:
        if handler in self._handlers.get(event_type, []):
            self._handlers[event_type].remove(handler)

    def publish(self, event: Dict[str, Any]) -> None:
        event_type = event.get("type") if isinstance(event, dict) else str(event)
        for handler in list(self._handlers.get(event_type, [])):
            handler(event)

    def emit(self, event_type: str, data: Optional[Dict[str, Any]] = None) -> None:
        payload = {"type": event_type, "data": data or {}}
        self.publish(payload)
