"""Real-send behaviour tests for Network Kernel adapters.

These tests prove the network layer no longer lies:

- The HTTP adapter makes a *real* HTTP POST and is only DELIVERED on a 2xx.
- The HTTP adapter reports FAILED (never DELIVERED) on any failure.
- The WebSocket adapter refuses honestly (no fake success) and advertises
  itself as unavailable up front.
- ``NetworkBus`` routing honours the adapter's truthful return value.

A local ``http.server`` runs in a background thread on a random free port for
the success paths; the failure path targets a port that is guaranteed closed.
Every server is shut down at the end of each test.
"""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
import pytest
from src.kernels.network import (
    AdapterConfig,
    HTTPAdapter,
    Message,
    MessageStatus,
    NetworkBus,
    ProtocolType,
    Route,
    WebSocketAdapter,
)


@pytest.fixture(autouse=True)
def _direct_http_no_proxy(monkeypatch):
    """Local-server tests must bypass any ambient HTTP proxy.

    The dev/sandbox environment sets HTTP_PROXY/HTTPS_PROXY to a local forward
    proxy; httpx routes loopback through it, and that proxy is flaky under full
    suite load (WinError 10054 connection reset). These tests target an explicit
    local server, so force trust_env=False on every httpx.Client they build.
    """
    _orig = httpx.Client

    def _client(*args, **kwargs):
        kwargs.setdefault("trust_env", False)
        return _orig(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", _client)


# ---------------------------------------------------------------------
# Local HTTP server helpers (real, background-threaded, random port)
# ---------------------------------------------------------------------

_received: list = []


class _RecordingHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        # A readiness probe (see _wait_for_server_ready) hits this sentinel path
        # and must not be counted as a real message delivery.
        if self.path == "/__lb_health__":
            self.send_response(200)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else b""
        _received.append({
            "path": self.path,
            "headers": dict(self.headers),
            "body": body,
        })
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def log_message(self, *args):  # silence stderr noise
        pass


def _start_server() -> HTTPServer:
    server = HTTPServer(("127.0.0.1", 0), _RecordingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    # The socket is bound + listening in __init__, but the daemon thread may not
    # have begun its accept loop yet. A client POST before that races the server
    # and gets a RST (WinError 10054) under load. Poll until it answers.
    _wait_for_server_ready(server)
    return server


def _wait_for_server_ready(server: HTTPServer, timeout: float = 5.0) -> None:
    """Block until ``server``'s accept loop is handling requests.

    Prevents a startup race (spurious WinError 10054 connection reset) that only
    surfaces under heavy GIL/scheduling contention in the full suite.
    """
    port = server.server_address[1]
    deadline = time.monotonic() + timeout
    probe = httpx.Client(timeout=0.2, trust_env=False)
    try:
        while time.monotonic() < deadline:
            try:
                if probe.post(f"http://127.0.0.1:{port}/__lb_health__", content=b"{}").status_code == 200:
                    return
            except Exception:
                time.sleep(0.005)
        raise RuntimeError("test HTTP server did not become ready to accept connections")
    finally:
        probe.close()


def _server_url(server: HTTPServer) -> str:
    host, port = server.server_address
    return f"http://{host}:{port}"


# (a) Real success path: the server genuinely receives the request.
def test_http_adapter_real_success_delivers_and_reaches_server():
    _received.clear()
    server = _start_server()
    try:
        url = _server_url(server)
        adapter = HTTPAdapter(AdapterConfig(
            name="http", protocol=ProtocolType.HTTP,
            endpoint=url, config={"timeout": 3.0},
        ))
        try:
            msg = Message(
                type="greeting", content={"hello": "world"},
                source="src", destination="dst", protocol=ProtocolType.HTTP,
            )
            result = adapter.send(msg)

            assert result is True, "send() must return True on a real 2xx response"
            assert msg.status == MessageStatus.DELIVERED
            assert msg.delivered_at is not None
            # Core assertion: the server actually got the request.
            assert len(_received) == 1, "local server must have received the POST"
            got = json.loads(_received[0]["body"].decode("utf-8"))
            assert got["type"] == "greeting"
            assert got["content"] == {"hello": "world"}
        finally:
            adapter.close()
    finally:
        server.shutdown()
        server.server_close()


# (b) Real failure path: unreachable endpoint must yield FAILED, never DELIVERED.
#     This test is the guardrail — reverting to fake delivery will make it red.
def test_http_adapter_unreachable_fails_honestly():
    adapter = HTTPAdapter(AdapterConfig(
        name="http", protocol=ProtocolType.HTTP,
        endpoint="http://127.0.0.1:1",  # port 1 is never open -> connection refused
        config={"timeout": 2.0},
    ))
    try:
        msg = Message(
            type="t", content="x", source="s", destination="d",
            protocol=ProtocolType.HTTP,
        )
        result = adapter.send(msg)

        assert result is False, "send() must return False when no server answers"
        assert msg.status == MessageStatus.FAILED
        assert msg.status != MessageStatus.DELIVERED
        assert msg.delivered_at is None
        assert msg.metadata.get("error"), "failure reason must be recorded"
    finally:
        adapter.close()


# (c) WebSocket adapter must not lie: explicit unavailability + FAILED send.
def test_websocket_adapter_does_not_fake_success():
    adapter = WebSocketAdapter(AdapterConfig(
        name="websocket", protocol=ProtocolType.WEBSOCKET,
        endpoint="ws://localhost:8081",
    ))
    msg = Message(type="t", content="x", source="s", destination="d")
    result = adapter.send(msg)

    assert result is False, "WebSocket send must not fake success"
    assert msg.status != MessageStatus.DELIVERED
    assert msg.status == MessageStatus.FAILED
    assert msg.metadata.get("error"), "reason must be recorded"
    # Callers can detect the dead end up front.
    assert adapter.is_available() is False
    assert adapter.supports_send is False


# (d) Bus end-to-end: routing honours the adapter's truthful return value.
def test_bus_routes_http_to_real_server_and_unreachable_to_failed():
    _received.clear()
    bus = NetworkBus()

    # Route a dedicated prefix to the HTTP adapter (higher priority than the
    # default "*" => internal route).
    bus.add_route(Route(
        id="http_real", pattern="http.*", protocol=ProtocolType.HTTP,
        adapter="http", priority=200, scope="L1",
    ))

    server = _start_server()
    try:
        url = _server_url(server)
        http_adapter = bus.get_adapter(ProtocolType.HTTP)
        assert http_adapter is not None

        # --- Success branch: point the real adapter at our live server. ---
        http_adapter.config.endpoint = url
        ok_msg = bus.send("t", {"k": "v"}, "src", "http.foo")
        assert ok_msg.status == MessageStatus.DELIVERED
        assert len(_received) == 1, "bus routed message must reach the server"

        # --- Failure branch: point it at a closed port. ---
        http_adapter.config.endpoint = "http://127.0.0.1:1"
        bad_msg = bus.send("t", "x", "src", "http.bar")
        assert bad_msg.status == MessageStatus.FAILED
        assert bad_msg.status != MessageStatus.DELIVERED
        assert bad_msg.metadata.get("error")
    finally:
        server.shutdown()
        server.server_close()
        http_adapter = bus.get_adapter(ProtocolType.HTTP)
        if http_adapter is not None:
            http_adapter.close()
