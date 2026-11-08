"""批次3 回归：JWT 吊销持久化 + 限流中间件真正挂载。

两条都是"代码看起来有、实际没生效"的缺陷：

* 吊销黑名单原来只是 handler 实例上的 ``set`` —— 重启后全部复活，多 worker
  时彼此不可见；现在必须落 SQLite 并带 TTL 清理。
* ``RateLimiter`` 只能手工调用，从未挂到 ASGI；现在通过
  ``RateLimitMiddleware`` 覆盖 chat / goals。
"""

from __future__ import annotations

import time

import pytest

from src.security.jwt_handler import JWTHandler, RevocationStore


@pytest.fixture()
def revocation_db(tmp_path, monkeypatch):
    path = str(tmp_path / "revoked.sqlite3")
    monkeypatch.setenv("LIUHAO_JWT_REVOCATION_DB", path)
    return path


def test_revocation_survives_a_new_handler_instance(revocation_db):
    """两个独立 handler（等价于重启/另一个 worker）共享同一吊销结果。"""
    import jwt

    first = JWTHandler(algorithm="HS256", secret_key="test-secret-" + "x" * 32)
    token, _payload = first.create_token(subject="human-a")
    assert first.revoke_token(token) is True

    second = JWTHandler(algorithm="HS256", secret_key="test-secret-" + "x" * 32)
    with pytest.raises(jwt.InvalidTokenError, match="revoked"):
        second.validate_token(token)


def test_revoked_refresh_token_is_refused_by_another_handler(revocation_db):
    import jwt

    from src.security.jwt_handler import TokenType

    first = JWTHandler(algorithm="HS256", secret_key="test-secret-" + "y" * 32)
    refresh, _payload = first.create_token(
        subject="human-b", token_type=TokenType.REFRESH
    )
    first.revoke_token(refresh)

    second = JWTHandler(algorithm="HS256", secret_key="test-secret-" + "y" * 32)
    with pytest.raises(jwt.InvalidTokenError, match="revoked"):
        second.validate_refresh_token(refresh)


def test_expired_rows_are_pruned_and_never_match(revocation_db):
    store = RevocationStore(revocation_db)
    with store._connect() as conn:
        conn.execute(
            "INSERT INTO revoked_tokens (jti, kind, subject, expires_at) "
            "VALUES (?, 'access', 'old', ?)",
            ("jti-old", time.time() - 1),
        )
    # 过期行既不参与命中，也会在下一次写入时被清掉。
    assert store.contains("jti-old") is False
    store.add("jti-live", expires_at=time.time() + 60)
    assert store.count() == 1
    assert store.contains("jti-live") is True


def test_rate_limit_middleware_covers_chat_and_goals_only():
    from src.gateway.rate_limiter import RateLimitMiddleware

    middleware = RateLimitMiddleware(None, paths=("/v1/chat", "/v1/goals"))
    assert middleware._matches("/v1/chat")
    assert middleware._matches("/v1/chat/stream")
    assert middleware._matches("/v1/chat/sessions/abc")
    assert middleware._matches("/v1/goals")
    assert not middleware._matches("/v1/health")
    assert not middleware._matches("/v1/models")
    assert not middleware._matches("/v1/chatty")


def test_rate_limit_keys_logged_in_users_by_principal(tmp_path, monkeypatch):
    """登录用户必须按 principal 分桶，不能静默退化成"按 IP 共用一个桶"。

    历史缺陷：``_get_key_id`` 给 ``validate_token`` 传了字符串 ``"access"``，
    而实现里是 ``payload.token_type != expected_type`` + ``expected_type.value``，
    于是每次校验都抛 ``AttributeError`` 并被 ``except Exception`` 吞掉 ——
    同一出口 IP 后的所有用户共享 100 桶 / 1 token/s，8 个用户并发聊天时
    互相把对方打成 429。
    """
    from starlette.requests import Request

    import src.security.jwt_handler as jwt_module
    from src.gateway.rate_limiter import RateLimiter
    from src.security.jwt_handler import create_access_token

    monkeypatch.setenv("LIUHAO_JWT_REVOCATION_DB", str(tmp_path / "revoked.sqlite3"))
    monkeypatch.setenv("LIUHAO_JWT_SECRET", "unit-secret-" + "z" * 32)
    monkeypatch.setattr(jwt_module, "_default_handler", None)

    def key_for(principal: str) -> str:
        token, _payload = create_access_token(subject=principal, scopes=["console"])
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/v1/chat",
            "headers": [(b"authorization", f"Bearer {token}".encode())],
            "client": ("127.0.0.1", 4242),
        }
        return RateLimiter()._get_key_id(Request(scope))

    assert key_for("alice") == "sub:alice|chat"
    assert key_for("bob") == "sub:bob|chat"


def test_chat_and_control_plane_have_separate_buckets(tmp_path, monkeypatch):
    """管理类请求不能挤占聊天额度（根账号连调 8 次 /v1/models 曾把 99 挤到 91）。"""
    from starlette.requests import Request

    import src.security.jwt_handler as jwt_module
    from src.gateway.rate_limiter import RateLimiter
    from src.security.jwt_handler import create_access_token

    monkeypatch.setenv("LIUHAO_JWT_REVOCATION_DB", str(tmp_path / "revoked.sqlite3"))
    monkeypatch.setenv("LIUHAO_JWT_SECRET", "unit-secret-" + "q" * 32)
    monkeypatch.setattr(jwt_module, "_default_handler", None)

    token, _payload = create_access_token(subject="root", scopes=["console"])

    def request_for(path: str) -> Request:
        return Request(
            {
                "type": "http",
                "method": "POST",
                "path": path,
                "headers": [(b"authorization", f"Bearer {token}".encode())],
                "client": ("127.0.0.1", 4242),
            }
        )

    limiter = RateLimiter()
    control = request_for("/v1/models")
    for _ in range(8):
        limiter.check_rate_limit(control)

    allowed, chat_info = limiter.check_rate_limit(request_for("/v1/chat"))
    _allowed, control_info = limiter.check_rate_limit(control)
    assert allowed is True
    # 8 次控制面调用只扣控制面桶；聊天桶几乎没动。
    assert chat_info["remaining"] >= 98, chat_info
    assert control_info["remaining"] <= 93, control_info


def test_jwt_requests_skip_the_api_key_table(tmp_path, monkeypatch):
    """已登录用户的请求不该再付一次 SHA256 + 全表扫描。"""
    from starlette.requests import Request

    import src.security.api_keys as api_keys_module
    import src.security.jwt_handler as jwt_module
    from src.gateway.rate_limiter import RateLimiter
    from src.security.jwt_handler import create_access_token

    monkeypatch.setenv("LIUHAO_JWT_REVOCATION_DB", str(tmp_path / "revoked.sqlite3"))
    monkeypatch.setenv("LIUHAO_JWT_SECRET", "unit-secret-" + "w" * 32)
    monkeypatch.setattr(jwt_module, "_default_handler", None)

    calls = []

    def noisy_lookup(raw_key):
        calls.append(raw_key)
        return None

    monkeypatch.setattr(api_keys_module, "validate_api_key", noisy_lookup)

    token, _payload = create_access_token(subject="alice", scopes=["console"])
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/chat",
            "headers": [(b"authorization", f"Bearer {token}".encode())],
            "client": ("127.0.0.1", 4242),
        }
    )
    allowed, info = RateLimiter().check_rate_limit(request)
    assert allowed is True
    assert info["key_id"] == "sub:alice|chat"
    assert calls == [], f"JWT 不应触发表扫描：{calls}"


def test_bearer_api_key_is_looked_up_once_per_request(tmp_path, monkeypatch):
    """非 JWT 形态仍查表，但一个请求只查一次（旧实现查两次）。"""
    from starlette.requests import Request

    import src.security.api_keys as api_keys_module
    from src.gateway.rate_limiter import RateLimiter

    calls = []
    monkeypatch.setattr(
        api_keys_module,
        "validate_api_key",
        lambda raw_key: calls.append(raw_key),
    )
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/chat",
            "headers": [(b"authorization", b"Bearer lhao_not-a-real-key")],
            "client": ("127.0.0.1", 4242),
        }
    )
    RateLimiter().check_rate_limit(request)
    assert calls == ["lhao_not-a-real-key"]


def test_rate_limit_middleware_returns_429_with_headers(monkeypatch):
    """限流命中时返回 429 + Retry-After/X-Rate-Limit-*，而不是放行。"""
    from fastapi.testclient import TestClient

    from src.gateway import rate_limiter
    from src.gateway.main import get_app

    info = {
        "limit": 100,
        "remaining": 0,
        "reset": time.time() + 30,
        "key_id": "sub:alice|chat",
    }
    monkeypatch.setattr(
        rate_limiter,
        "check_rate_limit",
        lambda request, trusted_proxies=(): (False, info),
    )
    client = TestClient(get_app())
    response = client.post("/v1/chat", json={"message": "hi"})
    assert response.status_code == 429
    assert response.headers.get("Retry-After")
    assert response.headers.get("X-Rate-Limit-Limit") == "100"
    assert response.headers.get("X-Rate-Limit-Remaining") == "0"
    # 429 必须带机器可读分类，客户端不必再解析中文文案。
    assert response.json()["code"] == "RATE_LIMITED_PRINCIPAL"
    assert "rate limit exceeded" in response.json()["detail"]

    # 匿名/无令牌命中时按 IP 归类。
    info["key_id"] = "ip:203.0.113.9|chat"
    response = client.post("/v1/chat", json={"message": "hi"})
    assert response.json()["code"] == "RATE_LIMITED_IP"

    # 未列入限流面的路由不受影响。
    assert client.get("/v1/health").status_code == 200
