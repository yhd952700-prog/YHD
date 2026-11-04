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


def test_rate_limit_middleware_returns_429_with_headers(monkeypatch):
    """限流命中时返回 429 + Retry-After/X-Rate-Limit-*，而不是放行。"""
    from fastapi.testclient import TestClient

    from src.gateway import rate_limiter
    from src.gateway.main import get_app

    monkeypatch.setattr(
        rate_limiter,
        "check_rate_limit",
        lambda request: (False, {"limit": 100, "remaining": 0, "reset": time.time() + 30}),
    )
    client = TestClient(get_app())
    response = client.post("/v1/chat", json={"message": "hi"})
    assert response.status_code == 429
    assert response.headers.get("Retry-After")
    assert response.headers.get("X-Rate-Limit-Limit") == "100"
    assert response.headers.get("X-Rate-Limit-Remaining") == "0"

    # 未列入限流面的路由不受影响。
    assert client.get("/v1/health").status_code == 200
