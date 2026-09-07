"""Network Kernel — Protocol Adapters + Communication Bus

The Network Kernel provides protocol adaptation and communication bus
for inter-kernel and inter-agent messaging.

依据 Definition Lock §112: Network Kernel 必须能够
- Route messages between kernels/agents
- Adapt multiple protocols (A2A, MCP, gRPC, HTTP, WebSocket)
- Maintain correlation IDs across protocol boundaries
- Support message serialization/deserialization
- Handle protocol-specific error mapping
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set
import uuid
import threading
import json

from src.kernels._crosscutting import kernel_action


class ProtocolType(str, Enum):
    """Supported communication protocols."""
    A2A = "a2a"           # Agent-to-Agent
    MCP = "mcp"           # Model Context Protocol
    GRPC = "grpc"         # gRPC
    HTTP = "http"         # HTTP/REST
    WEBSOCKET = "websocket"  # WebSocket
    INTERNAL = "internal" # In-process


class MessageStatus(str, Enum):
    """Message delivery status."""
    PENDING = "pending"
    SENT = "sent"
    DELIVERED = "delivered"
    FAILED = "failed"
    ACKNOWLEDGED = "acknowledged"
    EXPIRED = "expired"


class MessagePriority(str, Enum):
    """Message priority."""
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class Message:
    """Unified message structure across protocols."""
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    type: str = ""  # Message type/category
    content: Any = None
    source: str = ""
    destination: str = ""
    protocol: ProtocolType = ProtocolType.INTERNAL
    correlation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    causation_id: Optional[str] = None
    priority: MessagePriority = MessagePriority.NORMAL
    status: MessageStatus = MessageStatus.PENDING
    headers: Dict[str, str] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.utcnow)
    sent_at: Optional[datetime] = None
    delivered_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    retry_count: int = 0
    max_retries: int = 3

    def __post_init__(self):
        if self.causation_id is None:
            self.causation_id = self.correlation_id

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "id": self.id,
            "type": self.type,
            "content": self.content,
            "source": self.source,
            "destination": self.destination,
            "protocol": self.protocol.value,
            "correlation_id": self.correlation_id,
            "causation_id": self.causation_id,
            "priority": self.priority.value,
            "status": self.status.value,
            "headers": self.headers,
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat(),
            "sent_at": self.sent_at.isoformat() if self.sent_at else None,
            "delivered_at": self.delivered_at.isoformat() if self.delivered_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Message":
        """Deserialize from dictionary."""
        msg = cls(
            id=data.get("id", str(uuid.uuid4())[:8]),
            type=data.get("type", ""),
            content=data.get("content"),
            source=data.get("source", ""),
            destination=data.get("destination", ""),
            protocol=ProtocolType(data.get("protocol", "internal")),
            correlation_id=data.get("correlation_id", str(uuid.uuid4())),
            causation_id=data.get("causation_id"),
            priority=MessagePriority(data.get("priority", "normal")),
            status=MessageStatus(data.get("status", "pending")),
            headers=data.get("headers", {}),
            metadata=data.get("metadata", {}),
            retry_count=data.get("retry_count", 0),
            max_retries=data.get("max_retries", 3),
        )
        # Parse timestamps
        for field_name in ["created_at", "sent_at", "delivered_at", "expires_at"]:
            if data.get(field_name):
                try:
                    setattr(msg, field_name, datetime.fromisoformat(data[field_name]))
                except (ValueError, TypeError):
                    pass
        return msg


# Valid L0-L7 scopes (same hierarchy as resource/capability/security kernels).
_VALID_SCOPES = {f"L{i}" for i in range(8)}


def _is_valid_scope(scope: str) -> bool:
    """Return True iff ``scope`` is a valid L0-L7 scope label."""
    return scope in _VALID_SCOPES


def _scope_rank(scope: str) -> int:
    """Numeric rank for the L0-L7 scope hierarchy (L0=0 … L7=7)."""
    return int(scope[1:])


@dataclass
class Route:
    """Message routing rule."""
    id: str
    pattern: str  # Destination pattern (supports wildcards)
    protocol: ProtocolType
    adapter: str  # Adapter name to use
    priority: int = 0  # Higher = more specific
    scope: str = "L1"  # Authorized scope ceiling for this route (L0-L7)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AdapterConfig:
    """Protocol adapter configuration."""
    name: str
    protocol: ProtocolType
    endpoint: str
    config: Dict[str, Any] = field(default_factory=dict)
    enabled: bool = True


class ProtocolAdapter:
    """Base class for protocol adapters."""

    def __init__(self, config: AdapterConfig):
        self.config = config
        self.name = config.name
        self.protocol = config.protocol

    def send(self, message: Message) -> bool:
        """Send message via this protocol. Override in subclasses."""
        raise NotImplementedError

    def receive(self) -> Optional[Message]:
        """Receive message via this protocol. Override in subclasses."""
        raise NotImplementedError

    def serialize(self, message: Message) -> bytes:
        """Serialize message for transport."""
        return json.dumps(message.to_dict()).encode("utf-8")

    def deserialize(self, data: bytes) -> Message:
        """Deserialize message from transport."""
        return Message.from_dict(json.loads(data.decode("utf-8")))


class InternalAdapter(ProtocolAdapter):
    """In-process message adapter (direct function calls)."""

    def __init__(self):
        super().__init__(AdapterConfig(
            name="internal",
            protocol=ProtocolType.INTERNAL,
            endpoint="inproc://",
        ))
        self._handlers: Dict[str, Callable[[Message], None]] = {}

    def register_handler(self, destination: str, handler: Callable[[Message], None]):
        """Register a message handler for a destination."""
        self._handlers[destination] = handler

    def send(self, message: Message) -> bool:
        """Deliver message directly to handler."""
        handler = self._handlers.get(message.destination)
        if handler:
            message.status = MessageStatus.DELIVERED
            message.delivered_at = datetime.utcnow()
            try:
                handler(message)
                return True
            except Exception:
                message.status = MessageStatus.FAILED
                return False
        return False

    def receive(self) -> Optional[Message]:
        """Not applicable for internal adapter."""
        return None


class HTTPAdapter(ProtocolAdapter):
    """HTTP/REST protocol adapter."""

    def __init__(self, config: AdapterConfig):
        super().__init__(config)
        # In production, would use httpx or aiohttp
        self._client = None

    def send(self, message: Message) -> bool:
        """Send via HTTP POST."""
        # Simulated - in production would make actual HTTP request
        message.status = MessageStatus.SENT
        message.sent_at = datetime.utcnow()
        # Simulate delivery
        message.status = MessageStatus.DELIVERED
        message.delivered_at = datetime.utcnow()
        return True

    def receive(self) -> Optional[Message]:
        """Receive via HTTP (server mode)."""
        return None


class WebSocketAdapter(ProtocolAdapter):
    """WebSocket protocol adapter."""

    def __init__(self, config: AdapterConfig):
        super().__init__(config)
        self._connections: Dict[str, Any] = {}

    def send(self, message: Message) -> bool:
        """Send via WebSocket."""
        # Simulated
        message.status = MessageStatus.SENT
        message.sent_at = datetime.utcnow()
        message.status = MessageStatus.DELIVERED
        message.delivered_at = datetime.utcnow()
        return True

    def receive(self) -> Optional[Message]:
        """Receive via WebSocket."""
        return None


class NetworkBus:
    """Unified network communication bus."""

    def __init__(self):
        self._adapters: Dict[ProtocolType, ProtocolAdapter] = {}
        self._routes: List[Route] = []
        self._message_history: List[Message] = []
        self._max_history = 10000
        self._lock = threading.RLock()

        # Register built-in adapters
        self._register_builtin_adapters()

    def _register_builtin_adapters(self):
        """Register built-in protocol adapters."""
        # Internal adapter (always available)
        internal = InternalAdapter()
        self._adapters[ProtocolType.INTERNAL] = internal

        # HTTP adapter
        http = HTTPAdapter(AdapterConfig(
            name="http",
            protocol=ProtocolType.HTTP,
            endpoint="http://localhost:8080",
        ))
        self._adapters[ProtocolType.HTTP] = http

        # WebSocket adapter
        ws = WebSocketAdapter(AdapterConfig(
            name="websocket",
            protocol=ProtocolType.WEBSOCKET,
            endpoint="ws://localhost:8081",
        ))
        self._adapters[ProtocolType.WEBSOCKET] = ws

        # Default routes
        self.add_route(Route(
            id="internal_default",
            pattern="*",
            protocol=ProtocolType.INTERNAL,
            adapter="internal",
            priority=100,
        ))

    @kernel_action("network.register_adapter")
    def register_adapter(self, adapter: ProtocolAdapter) -> None:
        """Register a protocol adapter."""
        with self._lock:
            self._adapters[adapter.protocol] = adapter

    def get_adapter(self, protocol: ProtocolType) -> Optional[ProtocolAdapter]:
        """Get adapter by protocol."""
        with self._lock:
            return self._adapters.get(protocol)

    @kernel_action("network.add_route")
    def add_route(self, route: Route) -> None:
        """Add a routing rule (scope-validated: route scope must be L0-L7)."""
        if not _is_valid_scope(route.scope):
            raise ValueError(f"Invalid route scope: {route.scope!r} (must be L0-L7)")
        with self._lock:
            self._routes.append(route)
            # Sort by priority (highest first)
            self._routes.sort(key=lambda r: r.priority, reverse=True)

    @kernel_action("network.remove_route")
    def remove_route(self, route_id: str) -> bool:
        """Remove a routing rule."""
        with self._lock:
            for i, route in enumerate(self._routes):
                if route.id == route_id:
                    self._routes.pop(i)
                    return True
        return False

    def _match_route(self, destination: str) -> Optional[Route]:
        """Find matching route for destination."""
        for route in self._routes:
            if self._pattern_match(route.pattern, destination):
                return route
        return None

    def _pattern_match(self, pattern: str, destination: str) -> bool:
        """Match destination against pattern (supports * wildcard)."""
        if pattern == "*":
            return True
        if pattern.endswith("*"):
            prefix = pattern[:-1]
            return destination.startswith(prefix)
        if pattern.startswith("*"):
            suffix = pattern[1:]
            return destination.endswith(suffix)
        return pattern == destination

    @kernel_action("network.route")
    def route(self, message: Message) -> Message:
        """Route a message to its destination (scope-enforced).

        The message scope (read from ``message.metadata["scope"]``, default
        L1) must be a valid L0-L7 label and must not exceed the matched
        route's authorized scope ceiling. Out-of-scope messages are marked
        FAILED with an explanatory error instead of being delivered.
        """
        with self._lock:
            # Find route
            route = self._match_route(message.destination)
            if not route:
                message.status = MessageStatus.FAILED
                message.metadata["error"] = f"No route found for {message.destination}"
                return message

            # Scope enforcement (Permissioned): the message may only be
            # routed if its scope is valid and within the route's ceiling.
            msg_scope = message.metadata.get("scope", "L1")
            if not _is_valid_scope(msg_scope):
                message.status = MessageStatus.FAILED
                message.metadata["error"] = f"Invalid message scope: {msg_scope!r}"
                return message
            if _scope_rank(msg_scope) > _scope_rank(route.scope):
                message.status = MessageStatus.FAILED
                message.metadata["error"] = (
                    f"Message scope {msg_scope} exceeds route scope {route.scope}"
                )
                return message

            # Get adapter
            adapter = self._adapters.get(route.protocol)
            if not adapter:
                message.status = MessageStatus.FAILED
                message.metadata["error"] = f"No adapter for protocol {route.protocol}"
                return message

            # Update message with route info
            message.protocol = route.protocol
            message.headers["x-route-id"] = route.id
            message.headers["x-adapter"] = route.adapter

            # Store in history
            self._message_history.append(message)
            if len(self._message_history) > self._max_history:
                self._message_history = self._message_history[-self._max_history:]

            # Send via adapter
            message.status = MessageStatus.SENT
            message.sent_at = datetime.utcnow()
            success = adapter.send(message)

            if success:
                message.status = MessageStatus.DELIVERED
                message.delivered_at = datetime.utcnow()
            else:
                message.status = MessageStatus.FAILED

            return message

    def send(
        self,
        type: str,
        content: Any,
        source: str,
        destination: str,
        protocol: Optional[ProtocolType] = None,
        correlation_id: Optional[str] = None,
        priority: MessagePriority = MessagePriority.NORMAL,
        headers: Optional[Dict[str, str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Message:
        """Send a message (convenience method)."""
        message = Message(
            type=type,
            content=content,
            source=source,
            destination=destination,
            protocol=protocol or ProtocolType.INTERNAL,
            correlation_id=correlation_id or str(uuid.uuid4()),
            priority=priority,
            headers=headers or {},
            metadata=metadata or {},
        )
        return self.route(message)

    def register_internal_handler(self, destination: str, handler: Callable[[Message], None]) -> None:
        """Register an internal message handler."""
        internal = self._adapters.get(ProtocolType.INTERNAL)
        if isinstance(internal, InternalAdapter):
            internal.register_handler(destination, handler)

    def get_message_history(
        self,
        correlation_id: Optional[str] = None,
        source: Optional[str] = None,
        destination: Optional[str] = None,
        limit: int = 100
    ) -> List[Message]:
        """Get message history with filters."""
        with self._lock:
            messages = self._message_history
            if correlation_id:
                messages = [m for m in messages if m.correlation_id == correlation_id]
            if source:
                messages = [m for m in messages if m.source == source]
            if destination:
                messages = [m for m in messages if m.destination == destination]
            return messages[-limit:]

    def get_correlation_chain(self, correlation_id: str) -> List[Message]:
        """Get all messages in a correlation chain."""
        with self._lock:
            return [m for m in self._message_history if m.correlation_id == correlation_id]

    def stats(self) -> Dict[str, Any]:
        """Get bus statistics."""
        with self._lock:
            by_protocol = {}
            by_status = {}
            for msg in self._message_history:
                p = msg.protocol.value
                s = msg.status.value
                by_protocol[p] = by_protocol.get(p, 0) + 1
                by_status[s] = by_status.get(s, 0) + 1

            return {
                "total_messages": len(self._message_history),
                "by_protocol": by_protocol,
                "by_status": by_status,
                "routes": len(self._routes),
                "adapters": len(self._adapters),
            }


# Global network bus instance
_global_bus: Optional[NetworkBus] = None
_global_lock = threading.Lock()


def get_network_bus() -> NetworkBus:
    """Get or create the global network bus."""
    global _global_bus
    if _global_bus is None:
        with _global_lock:
            if _global_bus is None:
                _global_bus = NetworkBus()
    return _global_bus


# Convenience functions
def send_message(
    type: str,
    content: Any,
    source: str,
    destination: str,
    protocol: Optional[ProtocolType] = None,
    correlation_id: Optional[str] = None,
    priority: MessagePriority = MessagePriority.NORMAL,
) -> Message:
    """Send a message via the network bus."""
    return get_network_bus().send(type, content, source, destination, protocol, correlation_id, priority)


def register_internal_handler(destination: str, handler: Callable[[Message], None]) -> None:
    """Register an internal message handler."""
    get_network_bus().register_internal_handler(destination, handler)


def route_message(message: Message) -> Message:
    """Route a message."""
    return get_network_bus().route(message)


def get_message_chain(correlation_id: str) -> List[Message]:
    """Get message chain for correlation ID."""
    return get_network_bus().get_correlation_chain(correlation_id)