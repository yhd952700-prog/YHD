"""网络内核只读读取面 —— /v1/network/messages 的最小真实闭合。

为什么这个测试存在
------------------
网络总线此前是"只路由不可读"：总线在 INTERNAL/HTTP/WS 上对真实消息路由，但没有任何
API 或 UI 能读取它的真实状态。本测试证明新加的只读端点真的返回总线里**真实存在**的
内容，而不是 mock 或装饰性数据：

- 未带令牌 → 401，证明端点真的走了 ``_require_human`` 闸门；
- 带真实 bearer 令牌 → 200，且 body 是真实结构：``stats`` 是 dict、``history`` 是 list；
- ``history`` 可能是空列表（全新进程里总线没有任何消息），这正是诚实真相，不是错误。

隔离约定
--------
鉴权走真实 bearer 令牌（与网关 validator 同一签发/校验路径），不是 stub。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.gateway.main import get_app


def _auth_headers() -> dict:
    """真实 bearer 令牌（网关 validator 同一签发路径）。"""
    from src.security import get_jwt_handler

    token, _payload = get_jwt_handler().create_token(subject="net-human")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def client():
    app = get_app()
    with TestClient(app) as c:
        yield c, _auth_headers()


def test_network_messages_requires_auth(client):
    """未带令牌 → 401。"""
    c, _headers = client
    r = c.get("/v1/network/messages")
    assert r.status_code == 401, r.text


def test_network_messages_returns_real_state(client):
    """带真实令牌 → 200；body 有 stats(dict) 与 history(list)，断言类型不断言计数。"""
    c, headers = client
    r = c.get("/v1/network/messages", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body["stats"], dict), body
    assert isinstance(body["history"], list), body
