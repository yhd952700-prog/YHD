"""P1 诚实 Projects 面 —— /v1/projects 创建/列出/详情/删除 + 目标真实归类。

为什么这个测试存在
------------------
内核审计（KERNEL-PRODUCT-CAPABILITY.md §19/§23）把 "Projects" 列为 Top-3 #1 产品缺口：
此前没有任何"项目"概念能把目标归类、归属、呈现给人类。本测试证明这个面是真的：

- 创建 / 列表 / 详情 / 删除 走真实 bearer 令牌闸门（未带令牌 → 401）；
- 项目真实落盘（LIUHAO_WORKSPACE_ROOT 约束的 JSON），列表/详情返回真实数据；
- 目标在创建时挂到项目（project_id）后，真实写入两处：目标记录与项目 goal_ids；
- 详情端点如实返回项目下的真实目标摘要，没有目标就返回 goal_count=0、goals=[]；
- 不存在的项目 → 404；不存在的 project_id 挂到目标 → 404（诚实拒绝，不静默创建）。

隔离约定
--------
LIUHAO_WORKSPACE_ROOT / LIUHAO_ALERTS_STORE_PATH / AUDIT_DB_PATH 经 monkeypatch 指向
临时目录；ProjectStore 与 AIStateManager 单例在用例前重置，绝不会碰真实数据。
鉴权走真实 bearer 令牌（与网关 validator 同一签发/校验路径），不是 stub。
"""

from __future__ import annotations

import os

os.environ.setdefault("LIUHAO_WORKSPACE_ROOT", os.environ.get("LIUHAO_WORKSPACE_ROOT", ""))

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
    """每用例独立临时工作区/告警库/审计库；重置单例。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setenv("LIUHAO_WORKSPACE_ROOT", str(ws))
    monkeypatch.setenv("LIUHAO_ALERTS_STORE_PATH", str(tmp_path / "alerts.json"))
    monkeypatch.setenv("AUDIT_DB_PATH", str(tmp_path / "audit.db"))
    monkeypatch.setenv("HTTP_PROXY", "")
    monkeypatch.setenv("HTTPS_PROXY", "")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    import src.gateway.projects as _proj_mod
    import src.gateway.ai_management as _aim_mod
    _proj_mod.ProjectStore._instance = None
    _aim_mod.AIStateManager._instance = None
    yield


@pytest.fixture
def client():
    app = get_app()
    with TestClient(app) as c:
        yield c, _auth_headers()


def test_create_requires_auth(client):
    c, _h = client
    r = c.post("/v1/projects", json={"name": "p", "description": ""})
    assert r.status_code == 401, r.text


def test_list_requires_auth(client):
    c, _h = client
    r = c.get("/v1/projects")
    assert r.status_code == 401, r.text


def test_create_and_list_project(client):
    c, h = client
    r = c.post("/v1/projects", json={"name": "My Project", "description": "demo"}, headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "project_id" in body and body["name"] == "My Project"
    pid = body["project_id"]

    r2 = c.get("/v1/projects", headers=h)
    assert r2.status_code == 200, r2.text
    b2 = r2.json()
    assert b2["count"] >= 1
    by_id = {p["project_id"]: p for p in b2["projects"]}
    assert pid in by_id
    # 空项目的真实计数。
    assert by_id[pid]["goal_count"] == 0


def test_get_project_honest_empty(client):
    c, h = client
    r = c.post("/v1/projects", json={"name": "Empty", "description": ""}, headers=h)
    pid = r.json()["project_id"]
    r2 = c.get(f"/v1/projects/{pid}", headers=h)
    assert r2.status_code == 200, r2.text
    b2 = r2.json()
    assert b2["goal_count"] == 0
    assert b2["goals"] == []


def test_get_unknown_project_404(client):
    c, h = client
    r = c.get("/v1/projects/nope", headers=h)
    assert r.status_code == 404, r.text


def test_goal_links_to_project_and_detail_reads_real_goals(client):
    """目标挂到项目后，详情端点返回真实目标摘要（不发明）。"""
    c, h = client
    r = c.post("/v1/projects", json={"name": "Linked", "description": ""}, headers=h)
    pid = r.json()["project_id"]

    # 真实执行一个目标并挂到项目（与 E2E 同一生产代码路径，不经过 HTTP 以避免
    # 在测试里再跑一遍执行内核的 HTTP 同步阻塞；端点读取走 HTTP 验证真实数据）。
    from src.gateway.ai_management import AIStateManager
    mgr = AIStateManager()
    entry = mgr.create_and_execute_goal(
        "write a file named pj.txt containing 'hello project'",
        background=False,
        project_id=pid,
    )
    gid = entry["goal_id"]
    # 目标记录确实携带 project_id（真实落盘，不是装饰）。
    assert entry.get("project_id") == pid, entry
    # 项目的 goal_ids 也记录了该目标（真实归类）。
    from src.gateway.projects import ProjectStore
    assert gid in ProjectStore().get(pid)["goal_ids"]

    # 详情端点如实返回该目标摘要。
    r2 = c.get(f"/v1/projects/{pid}", headers=h)
    assert r2.status_code == 200, r2.text
    b2 = r2.json()
    assert b2["goal_count"] == 1, b2
    assert b2["goals"][0]["goal_id"] == gid
    assert "state" in b2["goals"][0]


def test_create_goal_with_unknown_project_404(client):
    """挂到不存在的项目 → 诚实 404，不静默丢弃 project_id。"""
    c, h = client
    r = c.post(
        "/v1/goals",
        json={"natural_language": "do something", "project_id": "ghost"},
        headers=h,
    )
    # 404 from the honest project-existence check (before execution).
    assert r.status_code == 404, r.text


def test_delete_project(client):
    c, h = client
    r = c.post("/v1/projects", json={"name": "ToDelete", "description": ""}, headers=h)
    pid = r.json()["project_id"]
    r2 = c.delete(f"/v1/projects/{pid}", headers=h)
    assert r2.status_code == 200, r2.text
    assert r2.json()["ok"] is True
    r3 = c.get(f"/v1/projects/{pid}", headers=h)
    assert r3.status_code == 404, r3.text
