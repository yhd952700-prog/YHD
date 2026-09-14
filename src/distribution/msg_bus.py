"""
Message Bus for LiuHao AI OS Distribution

Provides a unified Publish/Subscribe interface with:
- Redis Streams-backed queue/pub-sub, or in-memory for dev/test
- Topic and queue dual-mode support
- Message serialization/deserialization
- Connection management and health checks

Modes:
- "redis": Redis Streams-based (preferred for production)
- "memory": In-process only (for testing/development)

诚实性说明（Phase 7b，2026-09-14）
--------------------------------
本模块此前有**五处静默失败**（详见 ``docs/MESSAGE-BUS-BACKEND-DESIGN.md``）：
redis 模式发布丢消息却返回合法 ID、consume 恒返回空、``initialize()`` 日志说
"回退到内存"却没真的回退（``health_check()`` 因此谎报 ``initialized=True``）、
memory 模式的订阅回调**从不触发**、``total_published`` 统计的是"队列剩余量"。

现在：真实后端位于 :mod:`src.distribution.bus_backends`，后端不可用时
**抛 :class:`BusUnavailableError`**（响亮失败），不再静默降级。memory 模式的
对外语义（``msg_N`` ID、消费即移除、每 topic 上限）保持不变。
"""

import logging
import time
from typing import Dict, Any, List, Optional, Callable

from .bus_backends import BusUnavailableError, get_bus_backend

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
    - "memory": In-process only (testing/development)

    Features:
    - Publish to topics
    - Subscribe to topics (with callback, dispatched on publish)
    - Queue consumption (consume-and-remove)
    - Health status reporting

    后端不可用时 :meth:`publish` / :meth:`consume` 抛
    :class:`~src.distribution.bus_backends.BusUnavailableError`，
    :meth:`health_check` 如实返回 ``initialized=False`` 与 ``init_error``。
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
        self._init_error: Optional[str] = None
        self._backend = None
        self._closed = False
        # 消息 ID 计数器仍留在 MessageBus 侧：保持历史 ``msg_N`` 格式逐字节不变。
        self._message_counter: int = 0

        if mode not in self.SUPPORTED_MODES:
            raise ValueError(f"Unsupported bus mode: {mode}. Supported: {self.SUPPORTED_MODES}")

    def initialize(self) -> bool:
        """Initialize the bus by constructing a **real** backend.

        诚实性：失败时保持 ``self.mode`` 不变、记录 ``_init_error``、返回 False。
        旧实现会打一条 "falling back to memory" 的日志却什么都不改 —— 于是
        ``health_check()`` 谎报 ``initialized=True``，redis 模式静默丢消息。
        """
        try:
            self._backend = get_bus_backend(self.mode, self.config)
        except BusUnavailableError as e:
            self._init_error = str(e)
            self._initialized = False
            self._backend = None
            logger.error("消息总线初始化失败（mode=%s）：%s", self.mode, e)
            return False
        except Exception as e:  # noqa: BLE001 - 配置错误等也要如实报告，不吞
            self._init_error = f"{type(e).__name__}: {e}"
            self._initialized = False
            self._backend = None
            logger.error("消息总线初始化异常（mode=%s）：%s", self.mode, e)
            return False

        self._init_error = None
        self._initialized = True
        logger.info("消息总线已初始化：mode=%s backend=%s", self.mode, self._backend.name)
        return True

    def _ensure_ready(self):
        """确保后端可用：未初始化则**懒初始化一次**，仍失败即抛。

        memory 模式因此保持"拿来就能用"的旧体验；redis 模式则响亮失败，
        而不是静默丢消息。
        """
        if self._initialized and self._backend is not None:
            return self._backend
        self.initialize()
        if not self._initialized or self._backend is None:
            raise BusUnavailableError(
                f"消息总线不可用（mode={self.mode}）：{self._init_error or '初始化失败'}"
            )
        return self._backend

    @property
    def is_available(self) -> bool:
        """后端是否已就绪（不触发懒初始化）。"""
        return self._initialized and self._backend is not None

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

        Raises:
            BusUnavailableError: 后端不可用。**绝不静默丢弃消息后返回一个合法 ID。**
        """
        message = Message(topic=topic, payload=payload, headers=headers)
        self._message_counter += 1
        message.message_id = f"msg_{self._message_counter}"
        message.timestamp = time.time()
        return self._ensure_ready().publish(topic, message)

    # ==================== Subscription ====================

    def subscribe(self, topic: str, callback: Callable[[Message], None]) -> str:
        """
        Subscribe to a topic with a callback.

        修复 D4：回调现在**真的会在 publish 时被调用**（旧实现只登记不派发）。

        Args:
            topic: The topic to subscribe to
            callback: Callback function that receives Message objects

        Returns:
            Subscription ID
        """
        subscription_id = self._ensure_ready().subscribers.add(topic, callback)
        logger.info("Subscribed to topic '%s'", topic)
        return subscription_id

    def unsubscribe(self, topic: str, callback_id: str) -> bool:
        """Unsubscribe from a topic. 返回是否真的移除了一个订阅。"""
        if not self.is_available:
            return False
        removed = self._backend.subscribers.remove(topic, callback_id)
        if removed:
            logger.info("Unsubscribed from topic '%s'", topic)
        return removed

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

        Raises:
            BusUnavailableError: 后端不可用。**绝不返回空列表假装"没有消息"。**
        """
        return self._ensure_ready().consume(topic, count, timeout)

    # ==================== Lifecycle ====================

    def close(self) -> None:
        """关闭后端连接（幂等）。"""
        if self._backend is not None:
            try:
                self._backend.close()
            finally:
                self._backend = None
                self._initialized = False
                self._closed = True

    # ==================== Health & Status ====================

    def health_check(self) -> Dict[str, Any]:
        """Return health status of the message bus.

        修复 D3：不再谎报健康。未就绪时 ``status="unavailable"`` 且带 ``init_error``。
        """
        if not self.is_available:
            return {
                "mode": self.mode,
                "status": "unavailable",
                "initialized": False,
                "init_error": self._init_error or "尚未初始化",
            }
        backend_health = self._backend.health()
        connected = bool(backend_health.get("connected"))
        return {
            "mode": self.mode,
            "status": "healthy" if connected else "unavailable",
            "initialized": True,
            "backend": backend_health,
        }

    def get_stats(self) -> Dict[str, Any]:
        """Get statistics about message throughput.

        修复 D5：``total_published`` 用真实发布计数器，不再拿"队列剩余量"冒充。
        """
        if not self.is_available:
            return {
                "mode": self.mode,
                "initialized": False,
                "init_error": self._init_error or "尚未初始化",
                "total_published": 0,
                "active_subscribers": 0,
                "active_topics": 0,
            }
        backend_stats = self._backend.stats()
        return {
            "mode": self.mode,
            "initialized": True,
            "total_published": backend_stats.get("published", 0),
            "total_consumed": backend_stats.get("consumed", 0),
            "active_subscribers": backend_stats.get("active_subscribers", 0),
            "active_topics": len(self._backend.subscribers.topics()),
            "backend": backend_stats,
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
