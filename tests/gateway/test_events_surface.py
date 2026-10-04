"""事件产品面 —— /v1/events 真实近期事件（只读）。

为什么这个测试存在
------------------
LHX-C-008 事件内核长期只通过 ``src/api/server.py`` 上的一条 WebSocket 暴露（非产品
app），没有任何"人类可见"的只读出口。本测试证明 ``GET /v1/events`` 是真的：

- 走真实 bearer 令牌闸门（未带令牌 → 401）；
- 通过事件内核真实 emit 至少一条事件后，``GET /v1/events`` 能把它真实取回
  （emission → retrieval 闭环，绝不返回空壳或编造事件）；
- ``limit`` 参数确实生效。

隔离约定
--------
事件内核的全局总线单例是进程级共享的（这正是产品真实行为：事件会随运行累积），本测试
**不重置**它——重置会切断已安装的 execution→alert 绑定订阅，反而可能削弱其他测试。
因此这里用一条**唯一标记**事件来诚实证明 emission→retrieval，而不依赖"总线此刻为空"。
各业务面依赖的环境变量经 monkeypatch 指向临时目录，绝不污染真实数据；鉴权走真实
bearer 令牌（与网关 validator 同一签发/校验路径），不是 stub。
"""

from __future__ import annotations

import os

os.environ.setdefault("LIUHAO_WORKSPACE_ROOT", os.environ.get("LIUHAO_WORKSPACE_ROOT", ""))

import uuid

import pytest
from fastapi.testclient import TestClient

from src.gateway.main import get_app


def _auth_headers() -> dict:
    """真实 bearer 令牌（网关 validator 同一签发路径）。"""
    from src.security import get_jwt_handler

    token, _payload = get_jwt_handler().create_token(subject="test-human")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """重定向业务面依赖的环境变量到临时目录；绝不碰真实数据。"""
    monkeypatch.setenv("LIUHAO_WORKSPACE_ROOT", str(tmp_path / "ws"))
    monkeypatch.setenv("LIUHAO_ALERTS_STORE_PATH", str(tmp_path / "alerts.json"))
    monkeypatch.setenv("AUDIT_DB_PATH", str(tmp_path / "audit.db"))
    monkeypatch.setenv("HTTP_PROXY", "")
    monkeypatch.setenv("HTTPS_PROXY", "")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    yield


@pytest.fixture
def client():
    app = get_app()
    with TestClient(app) as c:
        yield c, _auth_headers()


def test_events_requires_auth(client):
    c, _h = client
    r = c.get("/v1/events")
    assert r.status_code == 401, r.text


def test_events_returns_real_published_event(client):
    """emission → retrieval 闭环：真实 emit 一条事件，必须能被真实取回。"""
    c, h = client
    from src.kernels.event import EventPriority, EventScope, publish_event

    marker = f"surface_test_{uuid.uuid4().hex}"
    publish_event(
        type=marker,
        source="test.events_surface",
        data={"probe": True, "n": 1},
        scope=EventScope.L0,
        priority=EventPriority.NORMAL,
    )

    r = c.get("/v1/events", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    # 诚实标注数据来源：内核总线历史缓冲，而非任何造假的副缓冲区。
    assert body["source"] == "event_bus_history", body
    types = {e["type"] for e in body["events"]}
    assert marker in types, body
    # 这条事件是真实发出的；data 必须原样返回，不丢失、不修饰。
    matched = [e for e in body["events"] if e["type"] == marker]
    assert matched[0]["data"] == {"probe": True, "n": 1}, matched
    assert matched[0]["source"] == "test.events_surface", matched
    # 返回的是真实发生过的事件，而非空壳。
    assert body["count"] >= 1, body


def test_events_limit_works(client):
    """limit 参数确实取最近 N 条，且包含我们刚发出的那条。"""
    c, h = client
    from src.kernels.event import EventPriority, EventScope, publish_event

    base = f"surface_limit_{uuid.uuid4().hex}"
    for i in range(5):
        publish_event(
            type=f"{base}_{i}",
            source="test.events_surface",
            data={"i": i},
            scope=EventScope.L0,
            priority=EventPriority.NORMAL,
        )

    r = c.get("/v1/events?limit=3", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["limit"] == 3, body
    assert body["count"] == 3, body
    # limit=3 返回最近 3 条；最近发出的 {base}_4 必在其中。
    types = {e["type"] for e in body["events"]}
    assert f"{base}_4" in types, body
    # 越界值被钳制：le=1000，因此 limit=3 不会变成 1000。
    assert body["count"] == 3, body
