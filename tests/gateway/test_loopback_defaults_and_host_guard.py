"""默认只本机可达 + Host 白名单 + 生产调试面收敛的回归测试。

覆盖用户要求的安全基线：
  * 所有启动入口默认绑 ``127.0.0.1``（不是 ``0.0.0.0``）；
  * 显式对外绑定会打印醒目警告；
  * 陌生 ``Host`` 返回 400（DNS rebinding 防护）；
  * 写方法带白名单外 ``Origin`` 返回 403（不依赖 CORS 预检）；
  * ``APP_ENV=production`` 下 ``/docs``、``/openapi.json`` 404，``/v1/health`` 仍 200；
  * 非生产环境 ``/docs`` 仍可用；
  * 同源控制台请求（``Origin: http://127.0.0.1:<port>``）不被拦。
"""
from __future__ import annotations

import asyncio
import importlib.util
import socket
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.routing import WebSocketRoute
from starlette.websockets import WebSocketDisconnect

ROOT = Path(__file__).resolve().parents[2]


def _load_script(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ==================== 默认只绑回环 ====================


def test_gateway_entry_defaults_to_loopback(monkeypatch):
    from src.gateway import __main__ as entry

    monkeypatch.delenv("HOST", raising=False)
    monkeypatch.delenv("PORT", raising=False)
    assert entry.DEFAULT_HOST == "127.0.0.1"
    assert entry.resolve_bind() == ("127.0.0.1", entry.DEFAULT_PORT)


def test_desktop_entrypoints_default_to_loopback():
    from src.desktop import __main__ as desktop_cli
    from src.desktop import service
    from src.desktop.app import DesktopApp

    assert service.DEFAULT_HOST == "127.0.0.1"
    assert desktop_cli.build_parser().parse_args([]).host == "127.0.0.1"
    assert DesktopApp.__init__.__kwdefaults__["host"] == "127.0.0.1"


def test_start_launcher_defaults_to_loopback():
    module = _load_script("liuhao_start_under_test", "scripts/start_liuhao.py")
    assert module.DEFAULT_API_HOST == "127.0.0.1"
    assert module._is_loopback("127.0.0.1") is True
    assert module._is_loopback("0.0.0.0") is False


def test_https_proxy_defaults_to_loopback(monkeypatch):
    monkeypatch.delenv("LIUHAO_TLS_HOST", raising=False)
    module = _load_script("https_proxy_under_test", "scripts/ops/https_proxy.py")
    assert module.LISTEN_HOST == "127.0.0.1"


def test_explicit_public_bind_warns(monkeypatch, caplog):
    from src.gateway import __main__ as entry

    monkeypatch.delenv("HOST", raising=False)
    import logging

    with caplog.at_level(logging.WARNING):
        entry.warn_if_exposed("0.0.0.0", 8080)
    assert "已对外暴露，局域网可访问" in caplog.text
    caplog.clear()
    entry.warn_if_exposed("127.0.0.1", 8080)
    assert "已对外暴露" not in caplog.text


# ==================== 运行时配置 ====================


def test_runtime_config_has_no_wildcard_trusted_hosts():
    from src.gateway.main import load_runtime_config

    hosts = load_runtime_config()["trusted_hosts"]
    assert hosts, "Host 白名单不能为空"
    assert "*" not in hosts
    assert "127.0.0.1" in hosts


def test_explicit_wildcard_bind_enables_ip_literal_access(monkeypatch):
    # 显式对外（0.0.0.0）时容器/局域网只能靠 IP 访问；放行 IP 字面量，
    # 但域名仍必须进白名单（陌生域名 Host 依然 400）。
    from src.gateway.main import load_runtime_config

    monkeypatch.setenv("LIUHAO_LISTEN_HOST", "0.0.0.0")
    runtime = load_runtime_config()
    assert runtime["host_guard_allow_ip_literals"] is True


# ==================== Host / Origin 校验 ====================


@pytest.fixture
def client(monkeypatch, tmp_path):
    """隔离身份库后启动真实 app（非生产，Host=testserver 属于放行名单）。"""
    monkeypatch.setenv("LIUHAO_HUMAN_IDENTITIES_BACKEND", "file")
    monkeypatch.setenv("LIUHAO_HUMAN_IDENTITIES_FILE", str(tmp_path / "humans.json"))
    monkeypatch.setenv(
        "LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY", "loopback-guard-test-key"
    )
    import src.kernels.identity as identity_module

    monkeypatch.setattr(identity_module, "_global_manager", None)
    from src.gateway.main import get_app

    with TestClient(get_app()) as test_client:
        yield test_client


def test_unknown_host_is_400(client):
    resp = client.get("/v1/health", headers={"Host": "evil.example.com"})
    assert resp.status_code == 400
    assert "Invalid host header" in resp.text


def test_loopback_host_with_port_is_accepted(client):
    resp = client.get("/v1/health", headers={"Host": "127.0.0.1:8080"})
    assert resp.status_code == 200


def test_cross_site_write_origin_is_403(client):
    resp = client.post(
        "/v1/auth/login",
        json={"username": "x", "password": "y"},
        headers={"Origin": "http://evil.example.com"},
    )
    assert resp.status_code == 403
    assert resp.json()["error"] == "origin_not_allowed"


def test_same_origin_write_is_not_blocked(client):
    resp = client.post(
        "/v1/auth/login",
        json={"username": "x", "password": "y"},
        headers={"Origin": "http://127.0.0.1:8080"},
    )
    assert resp.status_code != 403


def test_ip_origin_with_other_port_is_403(client):
    """同主机但端口不同不是同源：不能因为主机是 127.0.0.1 就放行。"""
    resp = client.post(
        "/v1/auth/login",
        json={"username": "x", "password": "y"},
        headers={"Origin": "http://127.0.0.1:9999"},
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"] == "origin_not_allowed"


def test_ip_origin_with_https_scheme_is_403(client):
    """同主机/同端口但协议不同也不是同源。"""
    for origin in ("https://127.0.0.1", "https://127.0.0.1:8080"):
        resp = client.post(
            "/v1/auth/login",
            json={"username": "x", "password": "y"},
            headers={"Origin": origin},
        )
        assert resp.status_code == 403, (origin, resp.text)


def test_configured_domain_origin_is_exact_match(monkeypatch, tmp_path):
    """配置的域名按完整来源放行；换端口或换协议必须 403。"""
    monkeypatch.setenv("LIUHAO_CORS_ALLOW_ORIGINS", "https://console.example.com")
    monkeypatch.setenv("LIUHAO_HUMAN_IDENTITIES_BACKEND", "file")
    monkeypatch.setenv(
        "LIUHAO_HUMAN_IDENTITIES_FILE", str(tmp_path / "humans.json")
    )
    monkeypatch.setenv(
        "LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY", "origin-exact-test-key"
    )
    import src.kernels.identity as identity_module

    monkeypatch.setattr(identity_module, "_global_manager", None)
    from src.gateway.main import get_app

    with TestClient(get_app()) as test_client:
        exact = test_client.post(
            "/v1/auth/login",
            json={"username": "x", "password": "y"},
            headers={"Origin": "https://console.example.com"},
        )
        assert exact.status_code != 403, exact.text

        for origin in (
            "https://console.example.com:8443",
            "http://console.example.com",
        ):
            resp = test_client.post(
                "/v1/auth/login",
                json={"username": "x", "password": "y"},
                headers={"Origin": origin},
            )
            assert resp.status_code == 403, (origin, resp.text)


def test_read_with_unknown_origin_is_not_blocked(client):
    resp = client.get("/v1/health", headers={"Origin": "http://evil.example.com"})
    assert resp.status_code == 200


# ==================== 生产调试面收敛 ====================


def _production_app(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("LIUHAO_CORS_ALLOW_ORIGINS", raising=False)
    import src.kernels.identity as identity_module

    monkeypatch.setattr(identity_module, "_global_manager", None)
    from src.gateway.main import get_app

    return get_app()


def test_production_disables_docs_and_keeps_health(monkeypatch, tmp_path):
    app = _production_app(monkeypatch, tmp_path)
    with TestClient(app, base_url="http://127.0.0.1:8080") as test_client:
        assert test_client.get("/docs").status_code == 404
        assert test_client.get("/redoc").status_code == 404
        assert test_client.get("/openapi.json").status_code == 404
        assert test_client.get("/v1/health").status_code == 200


def test_production_cors_has_no_localhost_dev_origins(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("LIUHAO_CORS_ALLOW_ORIGINS", raising=False)
    from src.gateway.main import load_runtime_config

    origins = load_runtime_config()["cors"]["allow_origins"]
    assert all("localhost" not in origin and "127.0.0.1" not in origin for origin in origins)


def test_development_docs_are_still_available(client):
    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").status_code == 200


# ==================== 生产：本机回环 + 实际端口放行 ====================


def _production_loopback_app(monkeypatch, tmp_path):
    """production + 本次监听端口 8137（模拟安装版随机端口）。"""
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("LIUHAO_CORS_ALLOW_ORIGINS", raising=False)
    monkeypatch.delenv("PORT", raising=False)
    monkeypatch.setenv("LIUHAO_LISTEN_PORT", "8137")
    monkeypatch.setenv("LIUHAO_HUMAN_IDENTITIES_BACKEND", "file")
    monkeypatch.setenv("LIUHAO_HUMAN_IDENTITIES_FILE", str(tmp_path / "humans.json"))
    monkeypatch.setenv(
        "LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY", "production-loopback-origin-key"
    )
    import src.kernels.identity as identity_module

    monkeypatch.setattr(identity_module, "_global_manager", None)
    from src.gateway.main import get_app

    return get_app()


def test_production_loopback_same_port_write_is_allowed(monkeypatch, tmp_path):
    """安装版的真实路径：浏览器从 http://127.0.0.1:8137 发写请求必须放行。

    修复前的症状是登录/注册全部 403（``reason=cross_site_write``），因为
    Origin 白名单只有写死的开发端口。这里用真实 production 应用 + 真实
    TestClient 对端地址走一遍。
    """
    app = _production_loopback_app(monkeypatch, tmp_path)
    with TestClient(
        app,
        base_url="http://127.0.0.1:8137",
        client=("127.0.0.1", 50000),
    ) as test_client:
        for origin in (
            "http://127.0.0.1:8137",
            "http://localhost:8137",
            "http://[::1]:8137",
        ):
            response = test_client.post(
                "/v1/auth/login",
                json={"username": "x", "password": "y"},
                headers={"Origin": origin},
            )
            assert response.status_code != 403, (origin, response.text)

        # 非回环来源继续拒绝，行为不变。
        evil = test_client.post(
            "/v1/auth/login",
            json={"username": "x", "password": "y"},
            headers={"Origin": "http://evil.example"},
        )
        assert evil.status_code == 403
        assert evil.json()["error"] == "origin_not_allowed"

        # 回环但端口不是本次实际监听端口：仍然 403。
        wrong_port = test_client.post(
            "/v1/auth/login",
            json={"username": "x", "password": "y"},
            headers={"Origin": "http://127.0.0.1:9999"},
        )
        assert wrong_port.status_code == 403
        assert wrong_port.json()["error"] == "origin_not_allowed"


def test_production_loopback_origin_from_remote_peer_is_403(monkeypatch, tmp_path):
    """Origin 写着回环、但 TCP 对端不是本机：不能放行（防伪造来源）。"""
    app = _production_loopback_app(monkeypatch, tmp_path)
    with TestClient(
        app,
        base_url="http://127.0.0.1:8137",
        client=("10.0.0.5", 50000),
    ) as test_client:
        response = test_client.post(
            "/v1/auth/login",
            json={"username": "x", "password": "y"},
            headers={"Origin": "http://127.0.0.1:8137"},
        )
        assert response.status_code == 403
        assert response.json()["error"] == "origin_not_allowed"


# ==================== 生产：对外绑定 + LAN 同源写放行 ====================


def _production_lan_app(monkeypatch, tmp_path, host="0.0.0.0"):
    """production + 显式对外绑定 + 本次监听端口 8137（模拟手机/局域网访问）。"""
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("LIUHAO_CORS_ALLOW_ORIGINS", raising=False)
    monkeypatch.delenv("PORT", raising=False)
    monkeypatch.setenv("LIUHAO_LISTEN_HOST", host)
    monkeypatch.setenv("LIUHAO_LISTEN_PORT", "8137")
    monkeypatch.setenv("LIUHAO_HUMAN_IDENTITIES_BACKEND", "file")
    monkeypatch.setenv("LIUHAO_HUMAN_IDENTITIES_FILE", str(tmp_path / "humans.json"))
    monkeypatch.setenv(
        "LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY", "production-lan-origin-key"
    )
    import src.kernels.identity as identity_module

    monkeypatch.setattr(identity_module, "_global_manager", None)
    from src.gateway.main import get_app

    return get_app()


def test_production_lan_same_origin_write_is_allowed(monkeypatch, tmp_path):
    """手机从 http://<LAN IP>:<实际端口> 打开的页面，写请求必须放行。

    修复前的症状：读能通、写全部 403 ``origin_not_allowed``，手机端登录
    直接失败。这里用真实 production 应用 + 真实 TestClient 走一遍。
    """
    app = _production_lan_app(monkeypatch, tmp_path)
    with TestClient(
        app,
        base_url="http://192.168.1.20:8137",
        client=("192.168.1.20", 50000),
    ) as test_client:
        response = test_client.post(
            "/v1/auth/login",
            json={"username": "x", "password": "y"},
            headers={
                "Origin": "http://192.168.1.20:8137",
                "User-Agent": (
                    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                    "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"
                ),
            },
        )
        assert response.status_code != 403, response.text


def test_production_lan_cross_site_write_is_still_403(monkeypatch, tmp_path):
    """不能因为对外绑定就把任意来源放开：跨站 Origin 继续 403。"""
    app = _production_lan_app(monkeypatch, tmp_path)
    with TestClient(
        app,
        base_url="http://192.168.1.20:8137",
        client=("192.168.1.20", 50000),
    ) as test_client:
        for origin in ("http://evil.example.com", "https://192.168.1.20:8137"):
            response = test_client.post(
                "/v1/auth/login",
                json={"username": "x", "password": "y"},
                headers={"Origin": origin},
            )
            assert response.status_code == 403, (origin, response.text)
            assert response.json()["error"] == "origin_not_allowed"

        # 同主机但端口不是本次实际监听端口：也不是同源。
        wrong_port = test_client.post(
            "/v1/auth/login",
            json={"username": "x", "password": "y"},
            headers={"Origin": "http://192.168.1.20:9999"},
        )
        assert wrong_port.status_code == 403


def test_loopback_bind_does_not_open_lan_origin(monkeypatch, tmp_path):
    """只绑回环时，LAN Origin 不应被"对外绑定"规则放行。"""
    app = _production_lan_app(monkeypatch, tmp_path, host="127.0.0.1")
    with TestClient(
        app,
        base_url="http://127.0.0.1:8137",
        client=("127.0.0.1", 50000),
    ) as test_client:
        response = test_client.post(
            "/v1/auth/login",
            json={"username": "x", "password": "y"},
            headers={"Origin": "http://192.168.1.20:8137"},
        )
        assert response.status_code == 403


def test_lan_write_origins_never_wildcard(monkeypatch):
    """自动放行的 LAN 来源是精确 URL 集合，永远不含 ``*``。"""
    monkeypatch.setenv("LIUHAO_LISTEN_HOST", "0.0.0.0")
    monkeypatch.setenv("LIUHAO_LISTEN_PORT", "8137")
    from src.gateway.main import lan_write_origins

    origins = lan_write_origins(8137)
    assert origins, "对外绑定时应枚举出至少一个精确来源"
    assert "*" not in origins
    assert all(origin.startswith("http://") and origin.endswith(":8137") for origin in origins)


# ==================== WebSocket scope：Host 闸门的覆盖钉子 ====================

#: 显式覆盖标记：``HostGuardMiddleware`` 当前只校验 ``scope["type"] == "http"``，
#: websocket scope 原样放行。这不是"跳过"——下面的用例会同时断言路由表、
#: 守卫在 websocket scope 上的真实行为与本标记三者一致；任何一项漂移都会让
#: 用例变红，逼着把 Host/Origin 校验扩展到 websocket scope 再改这里和文档。
WS_SCOPE_GUARDED = False

#: 与标记同步更新的文档位置（只提示位置，不代写内容）。
WS_HOST_GUARD_DOC = "docs/knowledge_base/24-security/security-hardening.md"


def _websocket_route_paths(routes, out=None) -> list:
    """收集 WebSocket 路由路径（穿透 FastAPI 0.141 的惰性 ``_IncludedRouter``）。

    ``app.routes`` 顶层只列出包装器（``path is None``），真实路由在
    ``original_router.routes`` 里；``Mount`` 的子路由则是相对路径。
    """
    if out is None:
        out = []
    for route in routes:
        inner = getattr(route, "original_router", None)
        if inner is not None:
            _websocket_route_paths(inner.routes, out)
            continue
        if isinstance(route, WebSocketRoute):
            out.append(getattr(route, "path", "") or "")
            continue
        child = getattr(route, "app", None)
        child_routes = getattr(child, "routes", None) if child is not None else None
        if child_routes:
            _websocket_route_paths(child_routes, out)
    return out


class _RecordingInner:
    """内层 ASGI 应用：只记录是否被调用，不发送任何响应。"""

    def __init__(self) -> None:
        self.reached = False

    async def __call__(self, scope, receive, send) -> None:
        self.reached = True


def _host_guard_websocket_verdict(host: str) -> tuple:
    """把 websocket scope 送进**真实**的 ``HostGuardMiddleware``。

    返回 ``(是否拒绝, 是否到达内层应用)``；拒绝的表现为发出 ``websocket.close``。
    """
    from src.gateway.main import HostGuardMiddleware

    inner = _RecordingInner()
    guard = HostGuardMiddleware(inner, allowed_hosts=["127.0.0.1"], loopback_port=8080)
    sent = []

    async def send(message) -> None:
        sent.append(message)

    async def receive() -> dict:
        return {"type": "websocket.connect"}

    scope = {
        "type": "websocket",
        "path": "/ws/events",
        "headers": [(b"host", host.encode("ascii"))],
        "client": ("127.0.0.1", 50000),
        "query_string": b"",
        "scheme": "ws",
    }
    asyncio.run(guard(scope, receive, send))
    rejected = any(item.get("type") == "websocket.close" for item in sent)
    return rejected, inner.reached


def test_websocket_routes_must_not_bypass_the_host_guard(client):
    """钉子用例：WS 路由进网关前，Host 校验必须先扩展到 websocket scope。

    现状的安全靠"网关没挂 WS 路由"——那是侥幸，不是机制。这里刻意不写跳过：
    路由表、HostGuard 在 websocket scope 上的真实行为、``WS_SCOPE_GUARDED``
    三者必须一致。一旦有人把 WS 路由挂进网关而守卫仍只看 HTTP，本用例变红。
    """
    ws_paths = _websocket_route_paths(client.app.routes)
    evil_rejected, evil_reached = _host_guard_websocket_verdict("evil.example.com")

    if ws_paths and not WS_SCOPE_GUARDED:
        pytest.fail(
            "网关路由表里已经出现 WebSocket 路由 "
            f"{ws_paths}，但 HostGuardMiddleware 仍未校验 websocket scope"
            f"（伪造 Host: evil.example.com 直接到达内层应用={evil_reached}，"
            "没有 websocket.close）。请把 Host 校验扩展到 websocket scope，"
            "否则这是跨站读取面（改完后同步更新 "
            f"{WS_HOST_GUARD_DOC} 的现网状态）。"
        )
    if WS_SCOPE_GUARDED:
        assert evil_rejected and not evil_reached, (
            "WS_SCOPE_GUARDED=True，但伪造 Host: evil.example.com 的 websocket "
            "scope 没有被拒绝。"
        )
        # 只拒绝陌生 Host 可能是"全部拒绝"的假实现：合法回环 Host 必须放行。
        legit_rejected, legit_reached = _host_guard_websocket_verdict("127.0.0.1:8080")
        assert not legit_rejected and legit_reached, (
            "WS_SCOPE_GUARDED=True，但合法回环 Host 被误杀：扩展 Host 校验时"
            "不能连本机来源一起拒绝。"
        )
        return

    # 当前无 WS 路由：显式钉住"未覆盖"，不能靠跳过就算过。
    assert evil_reached and not evil_rejected, (
        "HostGuardMiddleware 已经开始拒绝 websocket scope 的伪造 Host：请把 "
        "WS_SCOPE_GUARDED 改为 True，并同步更新 "
        f"{WS_HOST_GUARD_DOC}。"
    )


def test_gateway_websocket_handshake_is_denied_and_the_channel_is_legacy_only(client):
    """钉住当前真实状态：网关没有 WS 路由、握手被拒；无鉴权事件通道只在 legacy。"""
    assert _websocket_route_paths(client.app.routes) == []

    # 无匹配路由时 Starlette 直接发 ``websocket.close``（不 accept）：TestClient
    # 里表现为 ``WebSocketDisconnect``；真实网络栈上是 HTTP 403（见下面的实测）。
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/events") as websocket:
            websocket.send_text("ping")

    # ``src/api/events_ws.py`` 那个无鉴权、订阅全量事件的事件通道只挂在 legacy
    # 独立服务 ``src/api/server.py`` 上，没有进网关。
    from src.api.server import app as legacy_app

    assert "/ws/events" in _websocket_route_paths(legacy_app.routes)


@pytest.fixture
def live_gateway(monkeypatch, tmp_path):
    """真实 uvicorn 监听 127.0.0.1 的临时端口（不用 TestClient）。"""
    monkeypatch.setenv("LIUHAO_HUMAN_IDENTITIES_BACKEND", "file")
    monkeypatch.setenv("LIUHAO_HUMAN_IDENTITIES_FILE", str(tmp_path / "humans.json"))
    monkeypatch.setenv("LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY", "ws-live-key")
    import src.kernels.identity as identity_module

    monkeypatch.setattr(identity_module, "_global_manager", None)
    import uvicorn

    from src.gateway.main import get_app

    config = uvicorn.Config(get_app(), host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(400):
        if server.started:
            break
        time.sleep(0.05)
    assert server.started, "受控网关未能在 20s 内启动"
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield port
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def test_gateway_rejects_websocket_handshake_with_403_on_a_real_http_socket(live_gateway):
    """真实网络栈实测：未挂 WS 路由的网关对 ``/ws/events`` 握手返回 403。

    ASGI 规定服务端在 ``accept`` 之前发 ``websocket.close`` 时，真实服务器必须
    回 HTTP 403；TestClient 不走那条路径（只抛 ``WebSocketDisconnect``），所以
    这里用原始 socket 打真实 uvicorn，才能钉住用户可见的状态码。
    """
    port = live_gateway
    request = (
        b"GET /ws/events HTTP/1.1\r\n"
        b"Host: 127.0.0.1:%d\r\n" % port
        + b"Upgrade: websocket\r\n"
        b"Connection: Upgrade\r\n"
        b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
        b"Sec-WebSocket-Version: 13\r\n\r\n"
    )
    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        sock.sendall(request)
        head = sock.recv(4096).decode("latin-1")
    status_line = head.splitlines()[0] if head else ""
    assert " 403 " in status_line, (status_line, head)
