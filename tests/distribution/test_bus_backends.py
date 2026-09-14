"""Phase 7b 消息总线测试：后端化 + 消除五处静默失败。

设计文档：``docs/MESSAGE-BUS-BACKEND-DESIGN.md``。

本文件的核心价值是**证明旧缺陷真的被修掉了**，而不只是"新代码能跑"：

* D1/D2/D3：redis 无服务时 ``publish``/``consume`` **必须抛异常**，``health_check``
  必须如实说 ``unavailable``——三条都有独立的负向断言，因为"静默成功"正是旧 bug。
* D4：订阅回调**必须真的被调用**（旧实现只登记不派发）。
* D5：``total_published`` 必须是真计数，不能是"队列剩余量"。

另有一组用**注入 fake client** 的测试，在不依赖真实 Redis 的前提下验证
``xadd``/``xread``/``xdel`` 的真实调用序列；真 Redis 端到端用 ``skipif`` 显式跳过并给出原因。
"""

from __future__ import annotations

import json

import pytest

from src.distribution.bus_backends import (
    BusUnavailableError,
    InMemoryBusBackend,
    RedisStreamsBusBackend,
    get_bus_backend,
    redact_url,
)
from src.distribution.msg_bus import Message, MessageBus

#: 一个几乎必定拒绝连接的地址（端口 1），用来制造"redis 不可达"。
UNREACHABLE_URL = "redis://127.0.0.1:6399/0"
FAST_FAIL = {"url": UNREACHABLE_URL, "connect_timeout": 0.3}


def _unreachable_bus() -> MessageBus:
    return MessageBus("redis", dict(FAST_FAIL))


# ============================================================
# fake redis：让真实 redis 代码路径可以离线验证
# ============================================================


class FakeRedis:
    """最小 redis-py 替身，只实现后端真正用到的四个命令。

    条目 ID 用零填充以便字典序 == 数值序（``_last_ids`` 游标依赖正确排序）。
    """

    def __init__(self) -> None:
        self.streams: dict = {}
        self.calls: list = []
        self._seq = 0

    def ping(self) -> bool:
        self.calls.append("ping")
        return True

    def xadd(self, key, fields, maxlen=None, approximate=None):
        self._seq += 1
        entry_id = f"{self._seq:010d}-0"
        self.streams.setdefault(key, []).append((entry_id, fields))
        self.calls.append(("xadd", key, fields))
        return entry_id.encode("utf-8")

    def xread(self, streams, count=None, block=None):
        self.calls.append(("xread", dict(streams), count, block))
        response = []
        for key, last_id in streams.items():
            entries = [e for e in self.streams.get(key, []) if e[0] > last_id][:count]
            if entries:
                response.append([key.encode("utf-8"), entries])
        return response

    def xdel(self, key, *ids):
        self.calls.append(("xdel", key, ids))
        wanted = set(ids)
        before = len(self.streams.get(key, []))
        self.streams[key] = [e for e in self.streams.get(key, []) if e[0] not in wanted]
        return before - len(self.streams[key])

    def close(self):
        self.calls.append("close")


@pytest.fixture
def fake_redis() -> FakeRedis:
    return FakeRedis()


def _redis_bus(client) -> MessageBus:
    bus = MessageBus("redis", {"stream_prefix": "test:bus:"})
    bus._backend = get_bus_backend("redis", {"stream_prefix": "test:bus:"}, client=client)
    bus._initialized = True
    bus._init_error = None
    return bus


def _xadd_json(fields) -> dict:
    """取出 xadd 载荷并反序列化。

    后端传的是 ``{"data": <json str>}``（真 redis-py 会自行编码成 bytes），
    所以这里对 str/bytes 都容忍，避免测试把实现细节钉死。
    """
    raw = None
    for key in ("data", b"data"):
        if key in fields:
            raw = fields[key]
            break
    assert raw is not None, f"xadd 载荷缺少 data 字段: {fields!r}"
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    return json.loads(raw)


# ============================================================
# A) memory 模式：回归 + D4/D5 修复
# ============================================================


class TestMemoryMode:
    def test_publish_consume_roundtrip(self):
        bus = MessageBus("memory")
        message_id = bus.publish("orders", {"id": 7})
        assert message_id == "msg_1"

        messages = bus.consume("orders")
        assert len(messages) == 1
        assert messages[0].payload == {"id": 7}
        assert messages[0].message_id == "msg_1"

    def test_message_id_format_is_preserved(self):
        """旧格式 ``msg_N`` 必须逐字保留（不许悄悄换成时间戳格式）。"""
        bus = MessageBus("memory")
        ids = [bus.publish("t", {"n": i}) for i in range(3)]
        assert ids == ["msg_1", "msg_2", "msg_3"]

    def test_consume_removes_messages(self):
        bus = MessageBus("memory")
        bus.publish("t", {"n": 1})
        assert len(bus.consume("t")) == 1
        assert bus.consume("t") == []

    def test_consume_respects_count(self):
        bus = MessageBus("memory")
        for i in range(5):
            bus.publish("t", {"n": i})
        assert [m.payload["n"] for m in bus.consume("t", count=2)] == [0, 1]
        assert len(bus.consume("t", count=10)) == 3

    def test_subscriber_callback_is_actually_dispatched(self):
        """D4：旧实现 ``subscribe()`` 只登记不派发，回调永不触发。"""
        bus = MessageBus("memory")
        seen = []
        bus.subscribe("t", lambda m: seen.append(m.message_id))
        bus.publish("t", {"n": 1})
        assert seen == ["msg_1"], "订阅回调没有被派发 —— D4 回归了"

    def test_unsubscribe_stops_dispatch(self):
        bus = MessageBus("memory")
        seen = []
        sub_id = bus.subscribe("t", lambda m: seen.append(m.message_id))
        assert bus.unsubscribe("t", sub_id) is True
        bus.publish("t", {"n": 1})
        assert seen == []

    def test_unsubscribe_unknown_returns_false(self):
        bus = MessageBus("memory")
        assert bus.unsubscribe("nope", "sub_nope_1") is False

    def test_failing_subscriber_does_not_break_publish(self):
        bus = MessageBus("memory")

        def boom(_message):
            raise RuntimeError("callback exploded")

        bus.subscribe("t", boom)
        assert bus.publish("t", {"n": 1}) == "msg_1"  # 不抛
        assert bus.get_stats()["backend"]["subscriber_failures"] == 1

    def test_total_published_is_a_real_counter_not_queue_depth(self):
        """D5：旧实现用"队列剩余量"冒充已发布数，消费越多数字越小。"""
        bus = MessageBus("memory")
        for i in range(3):
            bus.publish("t", {"n": i})
        bus.consume("t", count=2)
        stats = bus.get_stats()
        assert stats["total_published"] == 3, "发布计数被消费影响了 —— D5 回归了"
        assert stats["total_consumed"] == 2

    def test_lazy_initialization_without_explicit_initialize(self):
        bus = MessageBus("memory")
        assert bus.is_available is False
        assert bus.publish("t", {"n": 1}) == "msg_1"
        assert bus.is_available is True

    def test_max_per_topic_cap_is_enforced(self):
        backend = InMemoryBusBackend(max_per_topic=3)
        backend.connect()
        for i in range(5):
            backend.publish("t", Message(topic="t", payload={"n": i}))
        assert len(backend._queues["t"]) == 3
        assert [m.payload["n"] for m in backend.consume("t", count=10)] == [2, 3, 4]

    def test_health_is_honest_in_memory_mode(self):
        bus = MessageBus("memory")
        bus.publish("t", {"n": 1})
        health = bus.health_check()
        assert health["status"] == "healthy"
        assert health["initialized"] is True
        assert health["backend"]["pending_messages"] == 1


# ============================================================
# B) redis 模式不可达：必须"响亮失败"，绝不静默成功
# ============================================================


class TestRedisUnavailableIsLoud:
    def test_initialize_returns_false_and_records_the_reason(self):
        bus = _unreachable_bus()
        assert bus.initialize() is False
        assert bus.is_available is False
        assert "Redis" in (bus._init_error or "")

    def test_initialize_does_not_silently_switch_to_memory(self):
        """D3：旧实现日志说"回退到内存"却什么都没改，这里反过来断言 mode 不变。"""
        bus = _unreachable_bus()
        bus.initialize()
        assert bus.mode == "redis"
        assert bus.get_stats()["mode"] == "redis"

    def test_publish_raises_instead_of_dropping_the_message(self):
        """D1（核心）：旧实现返回一个合法 ID 但消息根本不存在。"""
        bus = _unreachable_bus()
        with pytest.raises(BusUnavailableError) as exc:
            bus.publish("orders", {"id": 1})
        assert "redis" in str(exc.value).lower()

    def test_consume_raises_instead_of_returning_empty(self):
        """D2：旧实现恒返回 []，消费者永远分不清"没消息"和"总线坏了"。"""
        bus = _unreachable_bus()
        with pytest.raises(BusUnavailableError):
            bus.consume("orders")

    def test_subscribe_raises_rather_than_silently_never_firing(self):
        bus = _unreachable_bus()
        with pytest.raises(BusUnavailableError):
            bus.subscribe("orders", lambda m: None)

    def test_health_check_does_not_claim_healthy(self):
        """D3（核心）：健康检查绝不能在后端已死时报 healthy。"""
        bus = _unreachable_bus()
        bus.initialize()
        health = bus.health_check()
        assert health["status"] == "unavailable"
        assert health["initialized"] is False
        assert health["init_error"]

    def test_stats_are_zeroed_not_fabricated(self):
        bus = _unreachable_bus()
        bus.initialize()
        stats = bus.get_stats()
        assert stats["total_published"] == 0
        assert stats["initialized"] is False
        assert stats["init_error"]


# ============================================================
# C) redis 真实代码路径（注入 fake client）
# ============================================================


class TestRedisRealPath:
    def test_publish_calls_xadd_with_serialised_payload(self, fake_redis):
        bus = _redis_bus(fake_redis)
        message_id = bus.publish("orders", {"id": 7})
        assert message_id == "msg_1"

        kind, key, fields = fake_redis.calls[-1]
        assert kind == "xadd"
        assert key == "test:bus:orders"
        decoded = _xadd_json(fields)
        assert decoded["payload"] == {"id": 7}
        assert decoded["message_id"] == "msg_1"

    def test_consume_reads_then_deletes(self, fake_redis):
        bus = _redis_bus(fake_redis)
        bus.publish("orders", {"id": 7})
        fake_redis.calls.clear()

        messages = bus.consume("orders")
        assert [m.payload for m in messages] == [{"id": 7}]
        kinds = [c[0] if isinstance(c, tuple) else c for c in fake_redis.calls]
        assert "xread" in kinds and "xdel" in kinds

    def test_roundtrip_preserves_message_identity(self, fake_redis):
        bus = _redis_bus(fake_redis)
        published_id = bus.publish("orders", {"id": 7}, headers={"k": "v"})
        consumed = bus.consume("orders")
        assert len(consumed) == 1
        assert consumed[0].message_id == published_id
        assert consumed[0].headers["k"] == "v"

    def test_redis_entry_id_is_recorded_on_the_message_headers(self, fake_redis):
        """xadd 返回的 entry id 被记到 header 上，便于跨进程排查。

        注意：它是在**序列化之后**才写进 headers 的，因此不会回灌到 stream 里
        （否则每次消费都会把上一次的 entry id 带着走）。
        """
        bus = _redis_bus(fake_redis)
        captured = []
        bus.subscribe("orders", lambda m: captured.append(m))
        bus.publish("orders", {"id": 7})
        assert captured[0].headers["_redis_entry_id"] == "0000000001-0"

        _kind, _key, fields = fake_redis.calls[-1]
        decoded = _xadd_json(fields)
        assert "_redis_entry_id" not in decoded["headers"]

    def test_subscriber_dispatches_in_redis_mode_too(self, fake_redis):
        bus = _redis_bus(fake_redis)
        seen = []
        bus.subscribe("orders", lambda m: seen.append(m.message_id))
        bus.publish("orders", {"id": 7})
        assert seen == ["msg_1"]

    def test_consume_is_non_blocking_by_default(self, fake_redis):
        bus = _redis_bus(fake_redis)
        bus.publish("orders", {"id": 7})
        bus.consume("orders")
        xread = [c for c in fake_redis.calls if isinstance(c, tuple) and c[0] == "xread"][0]
        assert xread[3] is None  # block=None

    def test_consume_passes_block_when_timeout_given(self, fake_redis):
        bus = _redis_bus(fake_redis)
        bus.consume("orders", timeout=0.5)
        xread = [c for c in fake_redis.calls if isinstance(c, tuple) and c[0] == "xread"][0]
        assert xread[3] == 500

    def test_non_serialisable_payload_raises_instead_of_being_stringified(self, fake_redis):
        bus = _redis_bus(fake_redis)
        with pytest.raises(BusUnavailableError) as exc:
            bus.publish("orders", {"bad": {1, 2, 3}})  # set 不可 JSON 序列化
        assert "JSON" in str(exc.value)

    def test_health_reports_connected_with_injected_client(self, fake_redis):
        bus = _redis_bus(fake_redis)
        health = bus.health_check()
        assert health["status"] == "healthy"
        assert health["backend"]["connected"] is True

    def test_health_does_not_leak_the_password(self, fake_redis):
        backend = RedisStreamsBusBackend(
            url="redis://user:supersecret@redis.internal:6379/0", client=fake_redis
        )
        backend.connect()
        assert "supersecret" not in json.dumps(backend.health())

    def test_health_reports_unavailable_when_ping_fails(self, fake_redis):
        def broken_ping():
            raise ConnectionError("connection lost")

        fake_redis.ping = broken_ping
        backend = RedisStreamsBusBackend(client=fake_redis)
        backend._connected = True
        health = backend.health()
        assert health["connected"] is False
        assert "connection lost" in health["reason"]


# ============================================================
# D) 工厂 / 工具
# ============================================================


class TestFactoryAndHelpers:
    def test_memory_backend_factory(self):
        backend = get_bus_backend("memory")
        assert isinstance(backend, InMemoryBusBackend)
        assert backend.name == "memory"

    def test_unknown_mode_raises_value_error(self):
        with pytest.raises(ValueError):
            get_bus_backend("kafka")

    def test_message_bus_rejects_unknown_mode(self):
        with pytest.raises(ValueError):
            MessageBus("kafka")

    def test_redact_url_hides_the_password(self):
        assert redact_url("redis://user:secret@host:6379/0") == "redis://user:***@host:6379/0"

    def test_redact_url_leaves_passwordless_urls_alone(self):
        url = "redis://localhost:6379/0"
        assert redact_url(url) == url

    def test_close_is_idempotent(self):
        bus = MessageBus("memory")
        bus.publish("t", {"n": 1})
        bus.close()
        bus.close()
        assert bus.is_available is False

    def test_convenience_functions_use_the_default_bus(self, monkeypatch):
        import src.distribution.msg_bus as mod

        monkeypatch.setattr(mod, "_default_bus", None)
        seen = []
        mod.subscribe_topic("t", lambda m: seen.append(m.payload))
        message_id = mod.publish_message("t", {"n": 9})
        assert message_id == "msg_1"
        assert seen == [{"n": 9}]


# ============================================================
# F) 元验证（反橡皮图章）：证明旧行为确实在撒谎，且新行为不再撒
# ============================================================


class TestAntiRubberStamp:
    """这一组的目的不是"新代码能跑"，而是**证明 bug 真实存在过**。

    依据 skill ``silent-success-audit`` 第四步：护栏必须双向断言 ——
    一边复刻修复前的旧逻辑、断言它确实返回了假成功；一边断言新代码在同样条件下
    改为响亮失败。只断言后者，可能只是"代码变了"，而不是"谎报被消掉了"。
    """

    def test_legacy_redis_publish_returned_a_valid_id_while_storing_nothing(self):
        """逐字复刻修复前的 ``_do_publish`` + ``_publish_redis``。

        旧 ``_publish_redis`` 只写一条 debug 日志，但 ``_do_publish`` 仍
        ``return message.message_id`` —— 调用方拿到合法 ID，消息不存在。
        """

        class LegacyRedisBus:
            def __init__(self):
                self._message_counter = 0
                self.stored = []  # 旧实现里这个列表永远是空的

            def _publish_redis(self, message):
                pass  # 旧实现：只 logger.debug(...)

            def publish(self, topic, payload):
                self._message_counter += 1
                message_id = f"msg_{self._message_counter}"
                self._publish_redis(Message(topic=topic, payload=payload))
                return message_id

        legacy = LegacyRedisBus()
        returned = legacy.publish("orders", {"id": 1})

        assert returned == "msg_1", "旧实现返回了一个合法长相的消息 ID（谎报）"
        assert legacy.stored == [], "旧实现什么都没存（真相）—— 这就是 D1"

    def test_new_publish_refuses_precisely_where_the_legacy_one_lied(self):
        """同样的不可达条件下，新实现必须抛错而不是返回合法 ID。"""
        bus = _unreachable_bus()
        with pytest.raises(BusUnavailableError):
            bus.publish("orders", {"id": 1})

    def test_legacy_consume_was_indistinguishable_from_an_empty_queue(self):
        """D2 的本质：旧 ``_consume_redis`` 恒返回 []，把"总线坏了"伪装成"没消息"。"""

        def legacy_consume_redis(_topic, _count, _timeout):
            return []  # 旧实现：逐字如此

        assert legacy_consume_redis("orders", 1, None) == []
        with pytest.raises(BusUnavailableError):
            _unreachable_bus().consume("orders")

    def test_legacy_total_published_shrank_as_you_consumed(self):
        """D5：旧公式 ``sum(len(queue) for queue in queues)`` —— 消费越多数字越小。

        一个"已发布总数"随消费下降，是明显的谎报（它其实是"队列剩余量"）。
        """
        queues = {"orders": [1, 2, 3]}

        def legacy_total_published():
            return sum(len(messages) for messages in queues.values())

        assert legacy_total_published() == 3
        queues["orders"] = queues["orders"][2:]  # 消费 2 条
        assert legacy_total_published() == 1, "旧'发布总数'缩水了 —— 这就是 D5"

        bus = MessageBus("memory")
        for i in range(3):
            bus.publish("orders", {"n": i})
        bus.consume("orders", count=2)
        assert bus.get_stats()["total_published"] == 3, "新实现不再缩水"

    def test_legacy_health_check_could_not_report_failure(self):
        """D3：旧 ``health_check`` 的返回里**没有任何字段能表达"后端已死"**。

        它只有 ``mode``/``initialized``/订阅与队列计数，而 ``initialized`` 在
        ``_init_redis`` 之后被无条件置 True ⇒ 结构上就无法报故障。
        """
        legacy_keys = {"mode", "initialized", "subscribed_topics",
                       "topic_message_counts", "total_messages"}
        assert "status" not in legacy_keys, "旧结构里没有可表达健康与否的字段"

        health = _unreachable_bus().health_check()
        assert "status" in health and health["status"] == "unavailable"


# ============================================================
# G) 真 Redis 端到端（本机无服务 → 显式跳过并给出原因）
# ============================================================


def _live_redis_reachable() -> bool:
    try:
        import redis

        client = redis.Redis.from_url("redis://localhost:6379/0", socket_connect_timeout=0.5)
        return bool(client.ping())
    except Exception:
        return False


@pytest.mark.skipif(
    not _live_redis_reachable(),
    reason="本地无 Redis 服务（实测不可达）；fail-closed 行为已由 C 组用注入 client 覆盖",
)
def test_live_redis_end_to_end():
    bus = MessageBus("redis", {"url": "redis://localhost:6379/0", "stream_prefix": "test:live:"})
    assert bus.initialize() is True
    try:
        message_id = bus.publish("e2e", {"hello": "world"})
        messages = bus.consume("e2e")
        assert [m.message_id for m in messages] == [message_id]
        assert messages[0].payload == {"hello": "world"}
    finally:
        bus.close()
