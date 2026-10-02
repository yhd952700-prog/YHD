"""P1 真实 AI Employee 生命周期（按名 hire / list / pause / resume / remove）。

被修的真实缺陷
--------------
``AIStateManager`` 此前只硬编码了单个默认员工 ``liuhao-default``，没有任何按名
雇佣/移除的入口；agent id 也是每个员工内部的 ``agent_0``，多员工下同名 id 会
撞车，导致 pause/resume-by-id 无法指向某个具体员工的 agent。本测试覆盖：

- ``POST /v1/employees`` 按名雇佣 → 201；重复名 → 409；
- ``GET  /v1/employees`` 列出 store 中**所有**真实员工（含默认与按名雇佣的）；
- 暂停/恢复某个按名员工的 agent（跨员工按唯一 id 解析）；
- ``DELETE /v1/employees/{name}`` 移除后不再出现在列表；删除默认 → 403；缺员 → 404。

隔离约定
--------
- ``LIUHAO_WORKSPACE_ROOT`` 在**导入时**即重定向到临时目录（题目要求），且每个用例
  再用 ``tmp_path`` 隔离到独立子目录，确保绝不会写真实 workspace。
- ``AIStateManager`` 是进程级单例，每个用例前重置，使其重新 seed 到新的临时目录。
- 鉴权走真实的 bearer 令牌（与网关 ``require_human_principal`` 同一签发/校验路径），
  不是 stub。
"""

from __future__ import annotations

import os
import tempfile

# REDIR 数据到临时目录（导入时即生效，题目要求）。
os.environ.setdefault("LIUHAO_WORKSPACE_ROOT", tempfile.mkdtemp(prefix="liuhao-emp-lifecycle-"))

import pytest
from fastapi.testclient import TestClient

from src.gateway.main import get_app
from src.gateway.ai_management import AIStateManager


def _auth_headers() -> dict:
    """真实 bearer 令牌（网关 validator 同一签发路径）。"""
    from src.security import get_jwt_handler

    token, _payload = get_jwt_handler().create_token(subject="test-human")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """每用例独立临时 workspace + 重置 manager 单例。"""
    monkeypatch.setenv("LIUHAO_WORKSPACE_ROOT", str(tmp_path))
    AIStateManager._instance = None
    yield


@pytest.fixture
def client():
    app = get_app()
    with TestClient(app) as c:
        yield c, _auth_headers()


def _names(body: dict) -> list:
    return [e["name"] for e in body["employees"]]


def test_by_name_employee_lifecycle(client):
    c, headers = client

    # 首次列表：默认 seed 员工已存在。
    r = c.get("/v1/employees", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "liuhao-default" in _names(body), "default seed must be present"
    assert body["employee_count"] >= 1

    # 按名雇佣一个员工 → 201。
    r = c.post(
        "/v1/employees",
        json={"name": "alice", "agent_count": 2, "agent_types": ["planner", "executor"]},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    alice = r.json()
    assert alice["name"] == "alice"
    assert alice["agent_count"] == 2
    assert len(alice["agents"]) == 2

    # 重复名 → 409（绝不静默覆盖）。
    r = c.post(
        "/v1/employees",
        json={"name": "alice", "agent_count": 2, "agent_types": ["planner", "executor"]},
        headers=headers,
    )
    assert r.status_code == 409, r.text

    # 出现在全员列表。
    r = c.get("/v1/employees", headers=headers)
    assert r.status_code == 200, r.text
    names = _names(r.json())
    assert "alice" in names
    alice_entry = [e for e in r.json()["employees"] if e["name"] == "alice"][0]
    agent_ids = [a["id"] for a in alice_entry["agents"]]
    assert agent_ids == ["alice-a0", "alice-a1"], "agent ids must be globally unique"

    # 暂停该员工的一个 agent（跨员工按唯一 id 解析）。
    target = agent_ids[0]
    r = c.post(f"/v1/employees/{target}/pause", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "paused"

    # 列表里该 agent 确实处于 paused。
    r = c.get("/v1/employees", headers=headers)
    alice_entry = [e for e in r.json()["employees"] if e["name"] == "alice"][0]
    statuses = {a["id"]: a["status"] for a in alice_entry["agents"]}
    assert statuses[target] == "paused"

    # 恢复。
    r = c.post(f"/v1/employees/{target}/resume", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "idle"

    r = c.get("/v1/employees", headers=headers)
    alice_entry = [e for e in r.json()["employees"] if e["name"] == "alice"][0]
    statuses = {a["id"]: a["status"] for a in alice_entry["agents"]}
    assert statuses[target] == "idle"

    # 移除该员工 → 200，且不再列出。
    r = c.delete("/v1/employees/alice", headers=headers)
    assert r.status_code == 200, r.text
    r = c.get("/v1/employees", headers=headers)
    assert "alice" not in _names(r.json())


def test_default_protection_and_missing(client):
    c, headers = client

    # 删除默认 seed 员工 → 403（保护网关依赖）。
    r = c.delete("/v1/employees/liuhao-default", headers=headers)
    assert r.status_code == 403, r.text

    # 默认员工仍在。
    r = c.get("/v1/employees", headers=headers)
    assert "liuhao-default" in _names(r.json())

    # 移除不存在的员工 → 404。
    r = c.delete("/v1/employees/ghost", headers=headers)
    assert r.status_code == 404, r.text


def test_unauthenticated_is_rejected(client):
    c, _headers = client
    # 不带令牌应被 401（验证端点确实走了 require_human_principal 闸门）。
    r = c.get("/v1/employees")
    assert r.status_code == 401, r.text
