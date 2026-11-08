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

import importlib.util
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

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
