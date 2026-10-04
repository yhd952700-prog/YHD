"""P8 诚实信任读取面 + 雇佣裁决门 —— GET /v1/trust/{entity_id} 与雇佣扣信任门的最小真实闭合。

为什么这个测试存在
------------------
信任内核（LHX-C-010）长期 PRIMITIVE-ONLY：它的决策函数只在两个孤儿模块里被调用，
从未走上真实产品路径。本测试证明新加的只读出口与雇佣裁决门真的生效，而不是装饰：

- ``GET /v1/trust/{entity_id}`` 返回信任内核**真实**状态（revoked / scores / chain_summary
  / stats），绝不编造；未带令牌 → 401；带令牌且从未撤销的主体 → 200 且 ``revoked==False``；
- 在同一进程内 ``get_trust_manager().revoke("revoked-alice")`` 后，读取该实体 →
  ``revoked==True``（证明读取面真的走了撤销注册表）；
- ``POST /employees`` 雇佣一个被撤销的身份 → 403，且正文带拒绝原因（证明信任门真的
  拦在雇佣之前，而不是摆设）；
- ``POST /employees`` 雇佣一个**未被撤销**的良性身份 → 201（证明门不回退：正常路径照常）。

隔离约定
--------
鉴权走真实 bearer 令牌（与网关 validator 同一签发/校验路径），不是 stub。
"""

from __future__ import annotations

import os

os.environ.setdefault("LIUHAO_WORKSPACE_ROOT", os.environ.get("LIUHAO_WORKSPACE_ROOT", ""))

import pytest
from fastapi.testclient import TestClient

from src.gateway.main import get_app
from src.kernels.trust import get_trust_manager


def _auth_headers() -> dict:
    """真实 bearer 令牌（网关 validator 同一签发路径）。"""
    from src.security import get_jwt_handler

    token, _payload = get_jwt_handler().create_token(subject="test-human")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """每用例独立临时环境；绝不会碰真实 workspace / 告警 / 审计库。"""
    monkeypatch.setenv("LIUHAO_WORKSPACE_ROOT", str(tmp_path / "ws"))
    monkeypatch.setenv("LIUHAO_ALERTS_STORE_PATH", str(tmp_path / "alerts.json"))
    monkeypatch.setenv("AUDIT_DB_PATH", str(tmp_path / "audit.db"))
    yield


@pytest.fixture
def client():
    app = get_app()
    with TestClient(app) as c:
        yield c, _auth_headers()


def test_trust_read_requires_auth(client):
    """未带令牌 → 401。"""
    c, _headers = client
    r = c.get("/v1/trust/benign-entity")
    assert r.status_code == 401, r.text


def test_trust_read_benign_entity(client):
    """带令牌 + 从未撤销的实体 → 200，revoked==False，scores 为 dict，entity_id 回显。"""
    c, headers = client
    entity = "trust-benign-entity"
    r = c.get(f"/v1/trust/{entity}", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["entity_id"] == entity, body
    assert body["revoked"] is False, body
    assert isinstance(body["scores"], dict), body
    # 真实来源：stats 是 manager.stats() 的字典。
    assert isinstance(body["stats"], dict), body
    # 自信任链探针永远返回真实链（含自环）。
    assert isinstance(body["chain_summary"], dict), body


def test_trust_read_revoked_entity(client):
    """同一进程内撤销后读取 → revoked==True。"""
    c, headers = client
    # 真实走信任内核撤销注册表（幂等）。
    get_trust_manager().revoke("revoked-alice", reason="test")
    r = c.get("/v1/trust/revoked-alice", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["entity_id"] == "revoked-alice", body
    assert body["revoked"] is True, body


def test_hire_revoked_identity_refused(client):
    """雇佣被撤销的身份 → 403 且带拒绝原因。"""
    c, headers = client
    # 确保该身份在撤销注册表中（幂等）。
    get_trust_manager().revoke("revoked-alice", reason="test")
    r = c.post(
        "/v1/employees",
        headers=headers,
        json={"name": "revoked-alice", "agent_count": 1, "agent_types": ["general"]},
    )
    assert r.status_code == 403, r.text
    assert "revoked" in r.json()["detail"].lower(), r.json()


def test_hire_benign_identity_succeeds(client):
    """雇佣未被撤销的良性身份 → 201（门不回退）。"""
    c, headers = client
    r = c.post(
        "/v1/employees",
        headers=headers,
        json={"name": "trust-benign-bob", "agent_count": 1, "agent_types": ["general"]},
    )
    assert r.status_code == 201, r.text
