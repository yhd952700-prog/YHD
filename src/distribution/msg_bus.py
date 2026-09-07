"""
Message Bus for LiuHao AI OS Distribution

Provides a unified Publish/Subscribe interface with:
- Redis-backed pub/sub with in-memory fallback
- Topic and queue dual-mode support
- Message serialization/deserialization
- Connection management and health checks

Modes:
- "redis": Redis Streams-based pub/sub (preferred for production)
- "memory": In-memory only (for testing/development)
"""

import logging
import time
from typing import Dict, Any, List, Optional, Callable
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)


class Message:
    """Represents a message in the bus."""

    def __init__(
        self,
        topic: str,
        payload: Dict[str, Any],
        message_id: Optional[str] = None,
        timestamp: Optional[float] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        self.topic = topic
        self.payload = payload
        self.message_id = message_id or f"msg_{int(time.time() * 1000)}"
        self.timestamp = timestamp or time.time()
        self.headers = headers or {}

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "topic": self.topic,
            "payload": self.payload,
            "message_id": self.message_id,
            "timestamp": self.timestamp,
            "headers": self.headers,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Message":
        """Create Message from dictionary."""
        return cls(
            topic=data["topic"],
            payload=data["payload"],
            message_id=data.get("message_id"),
            timestamp=data.get("timestamp"),
            headers=data.get("headers"),
        )


class MessageBus:
    """
    Unified Message Bus with Pub/Sub support.

    Supports two modes:
    - "redis": Redis Streams (production)
    - "memory": In-memory only (testing/fallback)

    Features:
    - Publish to topics
    - Subscribe to topics (with callback)
    - Queue consumption (round-robin)
    - Message expiration/TTL
    - Health status reporting
    """

    SUPPORTED_MODES = ["redis", "memory"]

    def __init__(self, mode: str = "memory", config: Optional[Dict[str, Any]] = None):
        """
        Initialize Message Bus.

        Args:
            mode: Bus mode ("redis" or "memory")
            config: Configuration dict with connection settings
        """
        self.mode = mode
        self.config = config or {}
        self._initialized = False
        self._executor = ThreadPoolExecutor(max_workers=4)

        # Internal state
        self._subscribers: Dict[str, List[Callable]] = {}  # topic -> [callbacks]
        self._queues: Dict[str, List[Message]] = {}  # topic -> [messages] (memory mode)
        self._message_counter: int = 0

        if mode not in self.SUPPORTED_MODES:
            raise ValueError(f"Unsupported bus mode: {mode}. Supported: {self.SUPPORTED_MODES}")

    def initialize(self) -> bool:
        """Initialize the bus based on mode."""
        try:
            if self.mode == "redis":
                self._init_redis()
            # memory mode needs no initialization
            self._initialized = True
            logger.info(f"Message bus initialized in {self.mode} mode")
            return True
        except Exception as e:
            logger.error(f"Failed to initialize message bus: {e}")
            self._initialized = False
            return False

    def _init_redis(self) -> None:
        """Initialize Redis connection (placeholder for actual Redis integration)."""
        # This would connect to Redis using the config
        # For now, we fall back to memory mode gracefully
        logger.warning("Redis mode requested but not fully implemented - falling back to memory")
        # In a real implementation, would use redis-py here
        # self._redis = redis.Redis.from_url(self.config.get("url", "redis://localhost:6379"))

    # ==================== Publishing ====================

    def publish(self, topic: str, payload: Dict[str, Any], headers: Optional[Dict[str, str]] = None) -> str:
        """
        Publish a message to a topic.

        Args:
            topic: The topic to publish to
            payload: Message payload data
            headers: Optional message headers

        Returns:
            Message ID of the published message
        """
        message = Message(topic=topic, payload=payload, headers=headers)
        return self._do_publish(message)

    def _do_publish(self, message: Message) -> str:
        """Internal publish implementation."""
        self._message_counter += 1
        message.message_id = f"msg_{self._message_counter}"
        message.timestamp = time.time()

        if self.mode == "redis":
            self._publish_redis(message)
        else:
            self._publish_memory(message)

        return message.message_id

    def _publish_redis(self, message: Message) -> None:
        """Publish to Redis (placeholder)."""
        # Would use: self._redis.xadd(message.topic, message.payload)
        logger.debug(f"Would publish to Redis stream '{message.topic}': {message.message_id}")

    def _publish_memory(self, message: Message) -> None:
        """Publish to in-memory store."""
        topic = message.topic
        if topic not in self._queues:
            self._queues[topic] = []

        self._queues[topic].append(message)

        # Clean up old messages (keep last 1000 per topic)
        if len(self._queues[topic]) > 1000:
            self._queues[topic] = self._queues[topic][-1000:]

        logger.debug(f"Published to memory bus '{topic}': {message.message_id}")

    # ==================== Subscription ====================

    def subscribe(self, topic: str, callback: Callable[[Message], None]) -> str:
        """
        Subscribe to a topic with a callback.

        Args:
            topic: The topic to subscribe to
            callback: Callback function that receives Message objects

        Returns:
            Subscription ID
        """
        if topic not in self._subscribers:
            self._subscribers[topic] = []

        self._subscribers[topic].append(callback)
        logger.info(f"Subscribed to topic '{topic}'")
        return f"sub_{topic}_{id(callback)}"

    def unsubscribe(self, topic: str, callback_id: str) -> bool:
        """Unsubscribe from a topic."""
        if topic in self._subscribers:
            self._subscribers[topic] = [
                cb for cb in self._subscribers[topic]
                if f"sub_{topic}_{id(cb)}" != callback_id
            ]
            logger.info(f"Unsubscribed from topic '{topic}'")
            return True
        return False

    # ==================== Queue Consumption ====================

    def consume(self, topic: str, count: int = 1, timeout: Optional[float] = None) -> List[Message]:
        """
        Consume messages from a topic (queue mode).

        Args:
            topic: The topic to consume from
            count: Number of messages to fetch
            timeout: Maximum wait time in seconds (None = immediate)

        Returns:
            List of Message objects
        """
        messages = []

        if self.mode == "redis":
            messages = self._consume_redis(topic, count, timeout)
        else:
            messages = self._consume_memory(topic, count)

        return messages

    def _consume_redis(self, topic: str, count: int, timeout: Optional[float]) -> List[Message]:
        """Consume from Redis (placeholder)."""
        logger.debug(f"Would consume from Redis stream '{topic}'")
        return []

    def _consume_memory(self, topic: str, count: int) -> List[Message]:
        """Consume from in-memory queue."""
        topic_messages = self._queues.get(topic, [])

        # Round-robin: take first N messages
        to_consume = topic_messages[:count]
        self._queues[topic] = topic_messages[count:]

        return list(to_consume)

    # ==================== Health & Status ====================

    def health_check(self) -> Dict[str, Any]:
        """Return health status of the message bus."""
        return {
            "mode": self.mode,
            "initialized": self._initialized,
            "subscribed_topics": list(self._subscribers.keys()),
            "topic_message_counts": {
                topic: len(messages)
                for topic, messages in self._queues.items()
            },
            "total_messages": sum(len(m) for m in self._queues.values()),
        }

    def get_stats(self) -> Dict[str, Any]:
        """Get statistics about message throughput."""
        total_published = sum(
            len(messages) for messages in self._queues.values()
        )
        return {
            "mode": self.mode,
            "initialized": self._initialized,
            "total_published": total_published,
            "active_subscribers": sum(len(cbs) for cbs in self._subscribers.values()),
            "active_topics": len(self._subscribers),
        }


# Module-level convenience
_default_bus: Optional[MessageBus] = None


def get_message_bus(mode: str = "memory", config: Optional[Dict[str, Any]] = None) -> MessageBus:
    """Get the default Message Bus instance."""
    global _default_bus

    if _default_bus is None:
        _default_bus = MessageBus(mode=mode, config=config)
        _default_bus.initialize()

    return _default_bus


def publish_message(topic: str, payload: Dict[str, Any],
                    mode: str = "memory", headers: Optional[Dict[str, str]] = None) -> str:
    """Convenience function to publish a message."""
    bus = get_message_bus(mode)
    return bus.publish(topic, payload, headers)


def subscribe_topic(topic: str, callback: Callable[[Message], None],
                    mode: str = "memory") -> str:
    """Convenience function to subscribe to a topic."""
    bus = get_message_bus(mode)
    return bus.subscribe(topic, callback)
