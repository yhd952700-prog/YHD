"""消息总线后端（Phase 7b）—— 把 :mod:`src.distribution.msg_bus` 的"宣称"落成"实现"。

设计文档：``docs/MESSAGE-BUS-BACKEND-DESIGN.md``。

修复的源头是 ``msg_bus.py`` 里五处静默失败（redis 发布丢消息却返回成功 ID、
consume 恒空、initialize 谎报回退、订阅回调从不触发、发布计数用错指标）。
本模块提供两种**真**后端：

* :class:`InMemoryBusBackend` —— 进程内队列 + **真订阅派发** + 真计数。
* :class:`RedisStreamsBusBackend` —— 真 redis-py Streams（``xadd``/``xread``/``xdel``）。

共同纪律：**后端不可用即抛 :class:`BusUnavailableError`**，绝不静默降级。
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Protocol, Tuple
from urllib.parse import urlsplit, urlunsplit

if TYPE_CHECKING:  # pragma: no cover - 仅类型检查期
    from .msg_bus import Message

logger = logging.getLogger("liuhao.distribution.bus_backends")


class BusUnavailableError(RuntimeError):
    """消息总线后端不可用。

    抛出它而不是"回退到内存/静默丢弃"，是本模块存在的全部理由：
    一个会谎报成功的总线比一个坏掉的总线更危险。
    """


# ============================================================
# 订阅注册表（两种后端共用的本地扇出）
# ============================================================


class SubscriberRegistry:
    """本地订阅者注册与扇出。

    修复 D4：旧实现的 ``subscribe()`` 只把回调塞进 list，**没有任何派发点**，
    因此订阅永远不生效。这里把派发做成发布路径的一部分。
    """

    def __init__(self) -> None:
        self._subs: Dict[str, List[Callable[["Message"], None]]] = {}
        self._failures = 0

    def add(self, topic: str, callback: Callable[["Message"], None]) -> str:
        self._subs.setdefault(topic, []).append(callback)
        return f"sub_{topic}_{id(callback)}"

    def remove(self, topic: str, subscription_id: str) -> bool:
        callbacks = self._subs.get(topic)
        if callbacks is None:
            return False
        remaining = [cb for cb in callbacks if f"sub_{topic}_{id(cb)}" != subscription_id]
        removed = len(remaining) != len(callbacks)
        self._subs[topic] = remaining
        return removed

    def dispatch(self, topic: str, message: "Message") -> Tuple[int, int]:
        """扇出到该 topic 的订阅者。返回 ``(成功数, 失败数)``。

        **永不抛**：一个坏回调不应让发布方失败。失败的异常记日志并计数，
        以便通过 stats 暴露出去（而不是静默吞掉）。
        """
        delivered = 0
        failed = 0
        for callback in list(self._subs.get(topic, [])):
            try:
                callback(message)
                delivered += 1
            except Exception as exc:  # noqa: BLE001 - 故意兜住任意回调异常
                failed += 1
                self._failures += 1
                logger.error("订阅回调失败 topic=%s: %s", topic, exc)
        return delivered, failed

    def topics(self) -> List[str]:
        return list(self._subs.keys())

    @property
    def callback_count(self) -> int:
        return sum(len(cbs) for cbs in self._subs.values())

    @property
    def failure_count(self) -> int:
        return self._failures


# ============================================================
# 协议
# ============================================================


class BusBackend(Protocol):
    """消息总线后端契约。

    只保留 :class:`~src.distribution.msg_bus.MessageBus` 真正需要的面，
    不做臆测性扩展。
    """

    name: str

    def publish(self, topic: str, message: "Message") -> str:
        ...

    def consume(
        self, topic: str, count: int = 1, timeout: Optional[float] = None
    ) -> List["Message"]:
        ...

    def health(self) -> Dict[str, Any]:
        ...

    def stats(self) -> Dict[str, Any]:
        ...

    def close(self) -> None:
        ...


def _next_message_id(counter: int) -> str:
    """保持与历史一致的 ID 格式（``msg_N``）—— 不悄悄换格式。"""
    return f"msg_{counter}"


def redact_url(url: str) -> str:
    """隐去 URL 里的口令，供 health/stats 输出使用。

    健康检查端点不应把 DSN 口令泄露出去。
    """
    try:
        parts = urlsplit(url)
    except Exception:  # pragma: no cover - 极端畸形输入
        return "<unparsable>"
    if not parts.password:
        return url
    netloc = parts.netloc.replace(f":{parts.password}@", ":***@")
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


# ============================================================
# 内存后端
# ============================================================


class InMemoryBusBackend:
    """进程内队列后端（单进程 pub/sub + 队列语义）。"""

    name = "memory"

    #: 每个 topic 最多保留的消息数（与历史实现一致）。
    MAX_PER_TOPIC = 1000

    def __init__(self, max_per_topic: int = MAX_PER_TOPIC) -> None:
        self._queues: Dict[str, List["Message"]] = {}
        self._counter = 0
        self._published = 0
        self._consumed = 0
        self._max_per_topic = max_per_topic
        self.subscribers = SubscriberRegistry()
        self._closed = False

    # --- 生命周期 ---
    def connect(self) -> None:
        """内存后端永远可用，无需连接。"""
        self._closed = False

    def close(self) -> None:
        self._closed = True
        self._queues.clear()

    # --- 发布 / 消费 ---
    def publish(self, topic: str, message: "Message") -> str:
        self._counter += 1
        message.message_id = message.message_id or _next_message_id(self._counter)
        # 让 MessageBus 侧的 counter 与后端一致（ID 单调，便于排查）
        queue = self._queues.setdefault(topic, [])
        queue.append(message)
        if len(queue) > self._max_per_topic:
            del queue[: len(queue) - self._max_per_topic]
        self._published += 1
        self.subscribers.dispatch(topic, message)
        return message.message_id

    def consume(
        self, topic: str, count: int = 1, timeout: Optional[float] = None
    ) -> List["Message"]:
        queue = self._queues.get(topic, [])
        taken = queue[:count]
        self._queues[topic] = queue[count:]
        self._consumed += len(taken)
        return list(taken)

    # --- 观测 ---
    def health(self) -> Dict[str, Any]:
        return {
            "backend": self.name,
            "connected": not self._closed,
            "subscribed_topics": self.subscribers.topics(),
            "topic_message_counts": {t: len(m) for t, m in self._queues.items()},
            "pending_messages": sum(len(m) for m in self._queues.values()),
        }

    def stats(self) -> Dict[str, Any]:
        return {
            "backend": self.name,
            "published": self._published,
            "consumed": self._consumed,
            "pending_messages": sum(len(m) for m in self._queues.values()),
            "active_subscribers": self.subscribers.callback_count,
            "subscriber_failures": self.subscribers.failure_count,
        }


# ============================================================
# Redis Streams 后端
# ============================================================


class RedisStreamsBusBackend:
    """基于 Redis Streams 的真后端。

    ``client`` 可注入以便离线测试（不需要真 redis 即可验证命令序列）。
    ``connect()`` 会 ``ping()``；连不上就抛 :class:`BusUnavailableError`。
    """

    name = "redis"

    DEFAULT_URL = "redis://localhost:6379/0"
    DEFAULT_PREFIX = "liuhao:bus:"

    def __init__(
        self,
        url: Optional[str] = None,
        client: Any = None,
        stream_prefix: str = DEFAULT_PREFIX,
        maxlen: Optional[int] = 10_000,
        connect_timeout: float = 2.0,
    ) -> None:
        self._url = url or self.DEFAULT_URL
        self._client = client
        self._injected_client = client is not None
        self._prefix = stream_prefix
        self._maxlen = maxlen
        self._connect_timeout = connect_timeout
        self._last_ids: Dict[str, str] = {}
        self._counter = 0
        self._published = 0
        self._consumed = 0
        self._connected = False
        self.subscribers = SubscriberRegistry()

    # --- 生命周期 ---
    def connect(self) -> None:
        if self._client is None:
            try:
                import redis  # 延迟导入：未装 redis 时给出清晰错误而非 ImportError 噪音
            except ImportError as exc:
                raise BusUnavailableError(
                    "redis 模式需要 redis-py：pip install 'redis>=5.0.0'"
                ) from exc
            self._client = redis.Redis.from_url(
                self._url, socket_connect_timeout=self._connect_timeout
            )
        try:
            pong = self._client.ping()
        except Exception as exc:
            raise BusUnavailableError(
                f"无法连接 Redis（{redact_url(self._url)}）：{type(exc).__name__}: {exc}"
            ) from exc
        if not pong:
            raise BusUnavailableError(f"Redis ping 失败（{redact_url(self._url)}）")
        self._connected = True

    def close(self) -> None:
        client, self._client = self._client, None
        self._connected = False
        if client is not None and not self._injected_client:
            try:
                client.close()
            except Exception:  # noqa: BLE001 - 关闭失败不影响调用方
                logger.debug("关闭 redis 连接时出错", exc_info=True)

    # --- 内部 ---
    @property
    def _client_or_raise(self) -> Any:
        if self._client is None:
            raise BusUnavailableError("Redis 后端尚未连接，请先调用 connect()")
        return self._client

    def _stream_key(self, topic: str) -> str:
        return f"{self._prefix}{topic}"

    def _serialise(self, message: "Message") -> str:
        try:
            return json.dumps(message.to_dict())
        except (TypeError, ValueError) as exc:
            # 不静默 str() 兜底 —— 静默降级正是本模块要消灭的东西。
            raise BusUnavailableError(
                f"消息载荷无法 JSON 序列化（topic={message.topic}）：{exc}"
            ) from exc

    def _deserialise(self, raw: Any) -> "Message":
        from .msg_bus import Message  # 延迟导入，避免与 msg_bus 循环

        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return Message.from_dict(json.loads(raw))

    # --- 发布 / 消费 ---
    def publish(self, topic: str, message: "Message") -> str:
        client = self._client_or_raise
        self._counter += 1
        message.message_id = message.message_id or _next_message_id(self._counter)
        key = self._stream_key(topic)
        entry_id = client.xadd(
            key, {"data": self._serialise(message)}, maxlen=self._maxlen, approximate=True
        )
        # 保留 redis 侧 entry id，便于跨进程排查（不改变对外返回的 message_id 语义）
        message.headers = dict(message.headers or {})
        message.headers["_redis_entry_id"] = (
            entry_id.decode("utf-8") if isinstance(entry_id, bytes) else str(entry_id)
        )
        self._published += 1
        self.subscribers.dispatch(topic, message)
        return message.message_id

    def consume(
        self, topic: str, count: int = 1, timeout: Optional[float] = None
    ) -> List["Message"]:
        client = self._client_or_raise
        key = self._stream_key(topic)
        last_id = self._last_ids.get(topic, "0")
        block = None if not timeout else max(1, int(timeout * 1000))

        try:
            response = client.xread({key: last_id}, count=count, block=block)
        except Exception as exc:
            raise BusUnavailableError(f"Redis xread 失败（{key}）：{exc}") from exc

        if not response:
            return []

        messages: List["Message"] = []
        entry_ids: List[str] = []
        for _stream, entries in response:
            for entry_id, fields in entries:
                raw_id = entry_id.decode("utf-8") if isinstance(entry_id, bytes) else str(entry_id)
                entry_ids.append(raw_id)
                payload = fields.get(b"data") if isinstance(next(iter(fields), b""), bytes) else fields.get("data")
                messages.append(self._deserialise(payload))
            if entries:
                last = entries[-1][0]
                self._last_ids[topic] = (
                    last.decode("utf-8") if isinstance(last, bytes) else str(last)
                )

        # 队列语义（与 memory 后端一致）：消费即移除，避免 stream 无界增长。
        if entry_ids:
            try:
                client.xdel(key, *entry_ids)
            except Exception:  # noqa: BLE001 - 删除失败不改变"已读到"的事实
                logger.warning("xdel 失败 key=%s ids=%s", key, entry_ids)

        self._consumed += len(messages)
        return messages

    # --- 观测 ---
    def health(self) -> Dict[str, Any]:
        if not self._connected:
            return {
                "backend": self.name,
                "connected": False,
                "url": redact_url(self._url),
                "reason": "尚未连接（connect() 未调用或已失败）",
            }
        try:
            ok = bool(self._client_or_raise.ping())
        except Exception as exc:  # noqa: BLE001 - 健康检查要给出结论而不是抛
            return {
                "backend": self.name,
                "connected": False,
                "url": redact_url(self._url),
                "reason": f"ping 失败：{type(exc).__name__}: {exc}",
            }
        return {
            "backend": self.name,
            "connected": ok,
            "url": redact_url(self._url),
            "stream_prefix": self._prefix,
        }

    def stats(self) -> Dict[str, Any]:
        return {
            "backend": self.name,
            "connected": self._connected,
            "published": self._published,
            "consumed": self._consumed,
            "active_subscribers": self.subscribers.callback_count,
            "subscriber_failures": self.subscribers.failure_count,
        }


# ============================================================
# 工厂
# ============================================================

SUPPORTED_MODES = ("memory", "redis")


def get_bus_backend(
    mode: str = "memory",
    config: Optional[Dict[str, Any]] = None,
    client: Any = None,
) -> BusBackend:
    """构造后端并**立即连接**；失败抛 :class:`BusUnavailableError`。

    :param mode: ``"memory"`` 或 ``"redis"``。
    :param config: redis 模式用 ``{"url": ..., "stream_prefix": ...}``。
    :param client: 可注入的 redis client（测试用）。注入时视为已连接。
    """
    config = config or {}

    if mode == "memory":
        backend = InMemoryBusBackend()
        backend.connect()
        return backend

    if mode == "redis":
        backend = RedisStreamsBusBackend(
            url=config.get("url"),
            client=client,
            stream_prefix=config.get("stream_prefix", RedisStreamsBusBackend.DEFAULT_PREFIX),
            maxlen=config.get("maxlen", 10_000),
            connect_timeout=config.get("connect_timeout", 2.0),
        )
        backend.connect()
        return backend

    raise ValueError(f"Unsupported bus mode: {mode}. Supported: {list(SUPPORTED_MODES)}")
