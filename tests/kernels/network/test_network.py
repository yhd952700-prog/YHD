"""Network Kernel unit tests.

Covers: builtin adapters/routes, internal delivery via registered
handler, message round-trip (to_dict/from_dict), adapter
serialize/deserialize, route add/remove and pattern matching, no-route
failure, message history and correlation chains, and bus stats.
"""
import pytest

from src.kernels.network import (
    InternalAdapter,
    Message,
    MessageStatus,
    NetworkBus,
    ProtocolType,
    Route,
)


@pytest.fixture
def bus() -> NetworkBus:
    return NetworkBus()


# =====================================================================
# Initialization
# =====================================================================

class TestInit:
    def test_builtin_adapters_registered(self, bus):
        assert bus.get_adapter(ProtocolType.INTERNAL) is not None
        assert bus.get_adapter(ProtocolType.HTTP) is not None
        assert bus.get_adapter(ProtocolType.WEBSOCKET) is not None

    def test_default_wildcard_route_present(self, bus):
        # The default route maps everything to the internal adapter.
        msg = bus.send("t", "x", "src", "any-dest")
        assert msg.protocol == ProtocolType.INTERNAL


# =====================================================================
# Delivery
# =====================================================================

class TestDelivery:
    def test_internal_delivered_with_handler(self, bus):
        received = []
        bus.register_internal_handler("dest1", lambda m: received.append(m))
        msg = bus.send("t", "payload", "src", "dest1")
        assert msg.status == MessageStatus.DELIVERED
        assert len(received) == 1
        assert received[0].content == "payload"

    def test_internal_no_handler_fails(self, bus):
        msg = bus.send("t", "payload", "src", "unknown-dest")
        assert msg.status == MessageStatus.FAILED


# =====================================================================
# Message serialization
# =====================================================================

class TestSerialization:
    def test_message_roundtrip(self, bus):
        m = Message(
            type="t", content={"x": 1}, source="s",
            destination="d", protocol=ProtocolType.HTTP,
        )
        m2 = Message.from_dict(m.to_dict())
        assert m2.type == m.type
        assert m2.content == m.content
        assert m2.protocol == m.protocol

    def test_adapter_serialize_deserialize(self, bus):
        adapter = bus.get_adapter(ProtocolType.HTTP)
        m = Message(type="t", content="hi", source="s", destination="d")
        raw = adapter.serialize(m)
        m2 = adapter.deserialize(raw)
        assert m2.content == "hi"
        assert m2.id == m.id


# =====================================================================
# Routing
# =====================================================================

class TestRouting:
    def test_add_route_and_deliver(self, bus):
        # Higher priority than the default "*" route (priority 100).
        bus.add_route(Route(
            id="svc", pattern="svc.*", protocol=ProtocolType.HTTP,
            adapter="http", priority=200,
        ))
        m = bus.route(Message(type="t", content="x", source="s", destination="svc.foo"))
        assert m.status == MessageStatus.DELIVERED
        assert m.protocol == ProtocolType.HTTP

    def test_pattern_prefix_match(self, bus):
        bus.remove_route("internal_default")
        bus.add_route(Route(
            id="svc", pattern="svc.*", protocol=ProtocolType.HTTP,
            adapter="http", priority=200,
        ))
        m = bus.route(Message(type="t", content="x", source="s", destination="svc.foo"))
        assert m.protocol == ProtocolType.HTTP

    def test_remove_route(self, bus):
        assert bus.remove_route("internal_default") is True

    def test_send_no_route_fails(self, bus):
        bus.remove_route("internal_default")
        m = bus.send("t", "x", "s", "anything")
        assert m.status == MessageStatus.FAILED
        assert "No route" in m.metadata["error"]


# =====================================================================
# History & correlation
# =====================================================================

class TestHistory:
    def test_message_history_and_correlation(self, bus):
        bus.register_internal_handler("d", lambda m: None)
        bus.send("t", "x", "s", "d", correlation_id="corr-1")
        bus.send("t2", "y", "s", "d", correlation_id="corr-1")
        hist = bus.get_message_history(correlation_id="corr-1")
        assert len(hist) == 2
        chain = bus.get_correlation_chain("corr-1")
        assert len(chain) == 2

    def test_stats(self, bus):
        bus.register_internal_handler("d", lambda m: None)
        bus.send("t", "x", "s", "d")
        s = bus.stats()
        assert s["total_messages"] == 1
        assert s["adapters"] >= 3
        assert s["routes"] >= 1


# =====================================================================
# Scope enforcement (Permissioned)
# =====================================================================

class TestScopeEnforcement:
    def test_add_route_invalid_scope_rejected(self, bus):
        with pytest.raises(ValueError, match="Invalid route scope"):
            bus.add_route(Route(
                id="bad", pattern="x", protocol=ProtocolType.INTERNAL,
                adapter="internal", scope="L99",
            ))

    def test_route_message_scope_exceeding_route_denied(self, bus):
        # Default wildcard route has scope L1; a L5 message is out-of-scope.
        m = bus.route(Message(
            type="t", content="x", source="s", destination="any",
            metadata={"scope": "L5"},
        ))
        assert m.status == MessageStatus.FAILED
        assert "exceeds route scope" in m.metadata["error"]

    def test_route_invalid_message_scope_denied(self, bus):
        m = bus.route(Message(
            type="t", content="x", source="s", destination="any",
            metadata={"scope": "L99"},
        ))
        assert m.status == MessageStatus.FAILED
        assert "Invalid message scope" in m.metadata["error"]

    def test_route_within_scope_delivered(self, bus):
        bus.register_internal_handler("d", lambda m: None)
        m = bus.route(Message(
            type="t", content="x", source="s", destination="d",
            metadata={"scope": "L1"},
        ))
        assert m.status == MessageStatus.DELIVERED
