"""网关/安全加固的回归测试（H1/H2/H3/H4/M4/M5/M6/M8 + 0-10 系列）。

所有断言都走真实组件：真实 JWT handler、真实 FastAPI app、真实中间件。
没有 stub 掉签名校验或权限判定。
"""
from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import jwt as pyjwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.gateway.http_limits import BodyLimitMiddleware
from src.gateway.rate_limiter import RateLimiter, resolve_client_ip
from src.security.jwt_handler import MAX_ACCESS_TTL_SECONDS


@pytest.fixture
def client(monkeypatch, tmp_path):
    """隔离身份库后启动真实 app。"""
    monkeypatch.setenv("LIUHAO_HUMAN_IDENTITIES_BACKEND", "file")
    monkeypatch.setenv("LIUHAO_HUMAN_IDENTITIES_FILE", str(tmp_path / "humans.json"))
    monkeypatch.setenv(
        "LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY", "gateway-hardening-test-key"
    )
    import src.kernels.identity as identity_module

    monkeypatch.setattr(identity_module, "_global_manager", None)
    from src.gateway.main import get_app

    with TestClient(get_app()) as test_client:
        yield test_client


def _token(*, scope=None, kind="human", roles=None, subject="test-human", permissions=None):
    from src.security import get_jwt_handler

    metadata = {"kind": kind}
    if scope is not None:
        metadata["scope"] = scope
    token, _ = get_jwt_handler().create_token(
        subject=subject,
        roles=roles or [],
        permissions=permissions or [],
        metadata=metadata,
    )
    return {"Authorization": f"Bearer {token}"}


# ==================== H1/H2/M8：权限模型 ====================


class TestPermissionModel:
    def test_l1_human_cannot_write_models(self, client):
        headers = _token(scope="L1")
        resp = client.post(
            "/v1/models",
            headers=headers,
            json={"name": "x", "provider": "mock", "model": "m", "timeout": 5},
        )
        assert resp.status_code == 403

    def test_l1_human_cannot_issue_approval_grants(self, client):
        headers = _token(scope="L1")
        resp = client.post(
            "/v1/policy/approvals",
            headers=headers,
            json={
                "actions": ["execution.execute"],
                "target": "unit-test-target",
                "reason": "nope",
            },
        )
        assert resp.status_code == 403

    def test_l1_human_can_update_own_profile(self, client):
        """画像更新是读语义，不能被"POST/PUT 就需要管理员"拦死。"""
        headers = _token(scope="L1")
        resp = client.put(
            "/v1/profile", headers=headers, json={"preferred_name": "L1 用户"}
        )
        assert resp.status_code == 200

    def test_l1_human_can_reach_chat(self, client):
        """对话是读语义；这里没有配置 provider，但鉴权必须放行（不能 403）。"""
        headers = _token(scope="L1")
        resp = client.post(
            "/v1/chat",
            headers=headers,
            json={"message": "你好", "session_id": "l1-session"},
        )
        assert resp.status_code != 403

    def test_token_without_human_kind_is_rejected(self, client):
        headers = _token(scope="L0", kind="service")
        resp = client.get("/v1/chat/sessions", headers=headers)
        assert resp.status_code == 403

    def test_unknown_scope_does_not_grant_admin(self, client):
        headers = _token(scope="L9")
        resp = client.post(
            "/v1/policy/approvals",
            headers=headers,
            json={
                "actions": ["execution.execute"],
                "target": "unit-test-target",
                "reason": "unknown scope",
            },
        )
        assert resp.status_code == 403

    def test_missing_scope_does_not_fall_back_to_l0(self, client):
        headers = _token(scope=None)
        resp = client.post(
            "/v1/policy/approvals",
            headers=headers,
            json={
                "actions": ["execution.execute"],
                "target": "unit-test-target",
                "reason": "missing scope",
            },
        )
        assert resp.status_code == 403

    def test_l0_can_issue_approval_grants(self, client):
        from src.kernels.identity import get_identity_manager

        get_identity_manager().create_human_identity(
            "test-human", permissions={"owner"}, display_name="test-human"
        )
        headers = _token(scope="L0")
        resp = client.post(
            "/v1/policy/approvals",
            headers=headers,
            json={
                # 必须是被拦截门管辖的 HIGH/CRITICAL 动作；`execution.execute`
                # 属于 MEDIUM，会被 `_validated_actions` 拒绝，无法证明 L0 路径。
                "actions": ["identity.grant_permission"],
                "target": "unit-test-target",
                "reason": "sovereign",
            },
        )
        assert resp.status_code in (200, 201)


# ==================== H3/H4/L3：认证收口 ====================


class TestAuthHardening:
    def test_logout_rejects_forged_token(self, client):
        resp = client.post(
            "/v1/auth/logout", headers={"Authorization": "Bearer not-a-jwt"}
        )
        assert resp.status_code == 401

    def test_logout_without_token_is_still_idempotent(self, client):
        resp = client.post("/v1/auth/logout")
        assert resp.status_code == 200
        assert resp.json()["logged_out"] is True

    def test_revocation_store_failure_fails_closed(self, monkeypatch):
        from src.security import get_jwt_handler

        handler = get_jwt_handler()
        token, _ = handler.create_token(
            subject="revoked-human", metadata={"kind": "human", "scope": "L0"}
        )

        def _boom(*_args, **_kwargs):
            raise RuntimeError("revocation sqlite unavailable")

        monkeypatch.setattr(handler._revocations, "contains", _boom)
        with pytest.raises(pyjwt.InvalidTokenError):
            handler.validate_access_token(token)

    def test_access_token_ttl_is_clamped(self):
        from src.security.jwt_handler import create_access_token

        _token, payload = create_access_token(subject="ttl-human", ttl=999_999_999)
        assert payload.exp - payload.iat <= MAX_ACCESS_TTL_SECONDS


# ==================== M6/L1：审计与事件 ====================


class TestAuditEventIsolation:
    def test_non_l0_events_are_forced_to_own_subject(self, client, monkeypatch):
        from src.gateway import audit as audit_module

        calls = []

        def _fake_read_events(**kwargs):
            calls.append(kwargs)
            return []

        monkeypatch.setattr(audit_module, "_read_events", _fake_read_events)
        headers = _token(scope="L1", subject="alice")
        resp = client.get("/v1/audit/events", headers=headers)
        assert resp.status_code == 200
        assert calls and calls[0]["principal"] == "alice"

    def test_non_l0_cannot_read_other_subject(self, client):
        headers = _token(scope="L1", subject="alice")
        resp = client.get(
            "/v1/audit/events", params={"principal": "bob"}, headers=headers
        )
        assert resp.status_code == 403

    def test_l0_may_read_any_subject(self, client, monkeypatch):
        from src.gateway import audit as audit_module

        calls = []

        def _fake_read_events(**kwargs):
            calls.append(kwargs)
            return []

        monkeypatch.setattr(audit_module, "_read_events", _fake_read_events)
        headers = _token(scope="L0", subject="root")
        resp = client.get(
            "/v1/audit/events", params={"principal": "bob"}, headers=headers
        )
        assert resp.status_code == 200
        assert calls[0]["principal"] == "bob"

    def test_audit_details_are_redacted(self):
        from src.gateway.audit import _row_to_event

        row = (
            1, "e-1", "TOOL_INVOKED", "alice", "L0", "2026-01-01T00:00:00Z",
            "corr-1", "success",
            '{"action": "tool", "api_key": "sk-real-secret", "note": "%s"}' % ("x" * 400),
            "hash", "prev", "sha256", "link",
        )
        event = _row_to_event(row)
        assert event["details"]["api_key"] == "[redacted]"
        assert event["details"]["note"].endswith("...[truncated]")


# ==================== M4/M5：网关防护 ====================


class TestGatewayGuards:
    def test_body_limit_rejects_declared_oversize(self):
        app = FastAPI()

        @app.post("/v1/echo")
        def echo():
            return {"ok": True}

        app.add_middleware(BodyLimitMiddleware, max_body_bytes=16)
        with TestClient(app) as client:
            resp = client.post("/v1/echo", content=b"x" * 32)
        assert resp.status_code == 413

    def test_body_limit_rejects_stream_without_length(self):
        app = FastAPI()

        @app.post("/v1/echo")
        def echo():
            return {"ok": True}

        app.add_middleware(BodyLimitMiddleware, max_body_bytes=16)

        def _gen():
            for _ in range(8):
                yield b"xxxx"  # 32 bytes, no Content-Length

        with TestClient(app) as client:
            resp = client.post("/v1/echo", content=_gen())
        assert resp.status_code == 413

    def test_bucket_map_is_bounded(self):
        limiter = RateLimiter(max_buckets=2, idle_ttl_seconds=1)
        for index in range(5):
            limiter._get_bucket(f"ip:10.0.0.{index}", [])
        assert limiter.bucket_count() <= 2

    def test_xff_is_ignored_from_untrusted_peer(self):
        assert resolve_client_ip("203.0.113.9", "10.0.0.1", ["10.0.0.0/8"]) == "203.0.113.9"

    def test_xff_is_honoured_behind_trusted_proxy(self):
        ip = resolve_client_ip("10.0.0.2", "10.0.0.1, 198.51.100.7", ["10.0.0.0/8"])
        assert ip == "198.51.100.7"

    def test_cross_origin_redirect_is_refused(self):
        from src.gateway.models import _guard_probe_target
        from fastapi import HTTPException

        class _Redirector(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", "http://localhost:9/internal")
                self.end_headers()

            def log_message(self, *_args):
                pass

        server = HTTPServer(("127.0.0.1", 0), _Redirector)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_address[1]}"
            with pytest.raises(HTTPException) as caught:
                _guard_probe_target({"base_url": url})
            assert caught.value.status_code == 400
            assert "重定向" in caught.value.detail
        finally:
            server.shutdown()
            server.server_close()

    def test_presets_never_contain_keys(self, client):
        resp = client.get("/v1/models/presets", headers=_token(roles=["admin"]))
        assert resp.status_code == 200
        presets = resp.json()["presets"]
        providers = {p["provider"] for p in presets}
        assert {"openai", "deepseek", "moonshot", "anthropic", "google", "ollama"} <= providers
        for preset in presets:
            assert preset["api_key"] is None

    def test_saving_private_base_url_is_refused(self, client):
        headers = _token(roles=["admin"])
        resp = client.post(
            "/v1/models",
            headers=headers,
            json={
                "name": "ssrf",
                "provider": "openai",
                "model": "gpt-4o-mini",
                "base_url": "http://10.0.0.5:8080/v1",
                "timeout": 5,
            },
        )
        assert resp.status_code == 400

    def test_local_ollama_base_url_is_allowed(self, client):
        headers = _token(roles=["admin"])
        resp = client.post(
            "/v1/models",
            headers=headers,
            json={
                "name": "ollama-local",
                "provider": "ollama",
                "model": "llama3.1:8b",
                "base_url": "http://127.0.0.1:11434",
                "timeout": 5,
            },
        )
        assert resp.status_code == 200


class TestAuditProbeLoudFailure:
    def test_audit_emit_failure_logs_error_and_increments_metric(
        self, monkeypatch, caplog
    ):
        import src.ai.audit as audit_module
        import src.observability.metrics as metrics_module
        from src.gateway import models as models_module

        def _boom(*args, **kwargs):
            raise RuntimeError("audit backend down")

        monkeypatch.setattr(audit_module, "emit", _boom)
        recorded = []

        class _FakeCounter:
            def labels(self, **kwargs):
                recorded.append(kwargs)
                return self

            def inc(self):
                pass

        monkeypatch.setattr(metrics_module, "provider_errors_total", _FakeCounter())
        with caplog.at_level("ERROR"):
            models_module._audit_probe(
                {"provider": "deepseek", "model": "deepseek-chat"}, "success", "ok"
            )
        assert any("audit" in record.getMessage().lower() for record in caplog.records)
        assert recorded and recorded[0]["error_type"] == "audit_emit_failed"


class TestServerTimeoutConfig:
    """``server.timeouts`` 不再是死配置：idle 真实映射，其余显式标注未启用。"""

    def test_idle_seconds_maps_to_uvicorn_keep_alive(self):
        from src.gateway.main import uvicorn_timeout_kwargs

        runtime = {"timeouts": {"read_seconds": 30, "write_seconds": 60, "idle_seconds": 120}}
        assert uvicorn_timeout_kwargs(runtime) == {"timeout_keep_alive": 120}

    def test_read_write_are_reported_as_unenforced(self):
        from src.gateway.main import unenforced_timeouts

        runtime = {"timeouts": {"read_seconds": 30, "write_seconds": 60, "idle_seconds": 120}}
        assert unenforced_timeouts(runtime) == ["read_seconds", "write_seconds"]

    def test_missing_timeouts_produce_no_options(self):
        from src.gateway.main import uvicorn_timeout_kwargs

        assert uvicorn_timeout_kwargs({}) == {}
