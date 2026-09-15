"""Network Kernel unit tests.

Covers: builtin adapters/routes, internal delivery via registered
handler, message round-trip (to_dict/from_dict), adapter
serialize/deserialize, route add/remove and pattern matching, no-route
failure, message history and correlation chains, and bus stats.
"""
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from src.kernels.network import (
    Message,
    MessageStatus,
    NetworkBus,
    ProtocolType,
    Route,
)


# Local server used to exercise the (now real) HTTP adapter honestly.
class _TestHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def log_message(self, *args):
        pass


def _start_server():
    server = HTTPServer(("127.0.0.1", 0), _TestHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


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
        # The HTTP adapter now performs a real POST; point it at a live
        # local server so the DELIVERED assertion reflects genuine delivery
        # rather than the old simulated success.
        server = _start_server()
        try:
            http_adapter = bus.get_adapter(ProtocolType.HTTP)
            http_adapter.config.endpoint = "http://127.0.0.1:{}".format(
                server.server_address[1]
            )
            m = bus.route(Message(type="t", content="x", source="s", destination="svc.foo"))
            assert m.status == MessageStatus.DELIVERED
            assert m.protocol == ProtocolType.HTTP
        finally:
            server.shutdown()
            server.server_close()
            http_adapter = bus.get_adapter(ProtocolType.HTTP)
            if http_adapter is not None:
                http_adapter.close()

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
        # Each built-in transport now has its own same-priority catch-all
        # (see _match_route), so "no route at all" means removing all three.
        for route_id in ("internal_default", "http_default", "websocket_default"):
            assert bus.remove_route(route_id) is True
        m = bus.send("t", "x", "s", "anything")
        assert m.status == MessageStatus.FAILED
        assert "No route" in m.metadata["error"]


# =====================================================================
# Protocol-aware routing (regression: the internal catch-all used to
# silently overwrite any protocol the caller explicitly requested)
# =====================================================================

class TestProtocolAwareRouting:
    def test_explicit_http_protocol_reaches_http_route(self, bus):
        m = bus.route(Message(
            type="t", content="x", source="s", destination="no-specific-route",
            protocol=ProtocolType.HTTP,
        ))
        assert m.protocol is ProtocolType.HTTP
        assert m.headers.get("x-route-id") == "http_default"
        assert m.headers.get("x-adapter") == "http"

    def test_explicit_websocket_protocol_reaches_websocket_route(self, bus):
        m = bus.route(Message(
            type="t", content="x", source="s", destination="no-specific-route",
            protocol=ProtocolType.WEBSOCKET,
        ))
        assert m.protocol is ProtocolType.WEBSOCKET
        assert m.headers.get("x-route-id") == "websocket_default"
        assert m.headers.get("x-adapter") == "websocket"

    def test_unspecified_protocol_still_defaults_to_internal(self, bus):
        m = bus.route(Message(
            type="t", content="x", source="s", destination="no-specific-route",
        ))
        assert m.protocol is ProtocolType.INTERNAL
        assert m.headers.get("x-route-id") == "internal_default"

    def test_more_specific_route_still_beats_protocol_tiebreak(self, bus):
        # A specific route must win on priority even though the message's
        # protocol (defaulted INTERNAL) matches the internal catch-all.
        bus.add_route(Route(
            id="svc", pattern="svc.*", protocol=ProtocolType.HTTP,
            adapter="http", priority=200,
        ))
        m = bus.route(Message(type="t", content="x", source="s", destination="svc.foo"))
        assert m.headers.get("x-route-id") == "svc"
        assert m.protocol is ProtocolType.HTTP

    def test_unreachable_protocol_is_recorded_not_silent(self, bus):
        # No A2A route/adapter exists: the swap to internal must be visible.
        m = bus.route(Message(
            type="t", content="x", source="s", destination="no-specific-route",
            protocol=ProtocolType.A2A,
        ))
        assert m.protocol is ProtocolType.INTERNAL
        assert m.metadata.get("protocol_downgraded") == "a2a -> internal"
        assert m.headers.get("x-protocol-downgraded") == "a2a -> internal"


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


# =====================================================================
# Lifecycle is behaviour, not a label (pause/stop must refuse traffic)
# =====================================================================

class TestLifecycleGating:
    def test_paused_bus_refuses_route(self, bus):
        bus.initialize()
        bus.pause()
        m = bus.route(Message(type="t", content="x", source="s", destination="d"))
        assert m.status is MessageStatus.FAILED
        assert "paused" in m.metadata["error"]

    def test_stopped_bus_refuses_route(self, bus):
        bus.initialize()
        bus.shutdown()
        m = bus.route(Message(type="t", content="x", source="s", destination="d"))
        assert m.status is MessageStatus.FAILED
        assert "stopped" in m.metadata["error"]

    def test_resume_restores_routing(self, bus):
        received = []
        bus.register_internal_handler("d", lambda m: received.append(m))
        bus.initialize()
        bus.pause()
        bus.resume()
        m = bus.route(Message(type="t", content="x", source="s", destination="d"))
        assert m.status is MessageStatus.DELIVERED
        assert len(received) == 1

    def test_shutdown_closes_adapters(self, bus, monkeypatch):
        closed = []
        http_adapter = bus.get_adapter(ProtocolType.HTTP)
        monkeypatch.setattr(http_adapter, "close", lambda: closed.append("http"))
        bus.shutdown()
        assert closed == ["http"]


# =====================================================================
# httpx is a lazy, optional dependency (module import must not depend on it)
# =====================================================================

class TestHttpxIsLazy:
    def test_module_has_no_top_level_httpx(self):
        import src.kernels.network as net
        assert not hasattr(net, "httpx")

    def test_http_client_built_on_first_send_only(self, bus):
        adapter = bus.get_adapter(ProtocolType.HTTP)
        assert adapter._client is None

    def test_missing_httpx_fails_honestly(self, bus, monkeypatch):
        # ``sys.modules[name] = None`` makes ``import httpx`` raise ImportError.
        monkeypatch.setitem(sys.modules, "httpx", None)
        adapter = bus.get_adapter(ProtocolType.HTTP)
        m = Message(type="t", content="x", source="s", destination="d")
        assert adapter.send(m) is False
        assert "http adapter unavailable" in m.metadata["error"]
        assert m.status is MessageStatus.FAILED


# =====================================================================
# Optional history persistence (opt-in; default stays in-memory)
# =====================================================================

class TestHistoryPersistence:
    def test_default_bus_writes_nothing(self, bus, tmp_path):
        bus.register_internal_handler("d", lambda m: None)
        bus.send("t", "x", "s", "d")
        assert list(tmp_path.iterdir()) == []
        assert bus.stats()["history_persist_errors"] == []

    def test_history_survives_a_restart(self, tmp_path):
        path = tmp_path / "history.jsonl"
        first = NetworkBus(history_path=str(path))
        first.register_internal_handler("d", lambda m: None)
        first.send("t", "hello", "s", "d")
        assert path.exists()

        second = NetworkBus(history_path=str(path))
        history = second.get_message_history()
        assert len(history) == 1
        assert history[0].content == "hello"
        assert second.stats()["history_persist_errors"] == []

    def test_persistence_failure_is_surfaced_not_swallowed(self, tmp_path):
        # A directory cannot be opened for appending: the failure must land in
        # stats() rather than vanish or crash the send.
        bus = NetworkBus(history_path=str(tmp_path))
        bus.register_internal_handler("d", lambda m: None)
        m = bus.send("t", "x", "s", "d")
        assert m.status is MessageStatus.DELIVERED  # delivery unaffected
        assert bus.stats()["history_persist_errors"]
