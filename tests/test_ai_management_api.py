"""AI Employee / Goal / Workflow 管理 REST API 测试。

覆盖：
- GET /v1/employees — 列出真实 agent pool
- GET /v1/employees/{id} — 获取单个 agent 详情
- POST /v1/employees/{id}/pause — 暂停 agent
- POST /v1/employees/{id}/resume — 恢复 agent
- GET /v1/goals — 列出已执行 Goal
- POST /v1/goals — 创建+执行 Goal（真实 AgentRuntime）
- GET /v1/goals/{id} — 获取 Goal 详情
- GET /v1/workflows — 列出 Workflow
- GET /v1/workflows/{id} — 获取 Workflow 状态
- 鉴权：未携带令牌应 401
"""

import pytest
from fastapi.testclient import TestClient

from src.gateway.main import get_app
from src.gateway.ai_management import AIStateManager


@pytest.fixture
def client():
    """创建带鉴权的测试客户端。"""
    app = get_app()
    return TestClient(app)


# auth_headers fixture 来自 conftest.py（用 get_jwt_handler().create_token 签发真实令牌）


@pytest.fixture(autouse=True)
def reset_state_manager():
    """每个测试前重置 AIStateManager 单例，避免跨测试污染。"""
    AIStateManager._instance = None
    yield
    AIStateManager._instance = None


class TestEmployeesEndpoint:
    """AI Employee 端点测试。"""

    def test_list_employees_returns_real_agents(self, client, auth_headers):
        """GET /v1/employees 应返回真实的 agent pool。"""
        resp = client.get("/v1/employees", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "agents" in data
        assert "count" in data
        assert "stats" in data
        assert data["count"] == len(data["agents"])
        assert data["count"] > 0
        # 每个 agent 必须有真实字段
        agent = data["agents"][0]
        for field in ("id", "agent_type", "name", "status",
                      "completed_tasks", "failed_tasks"):
            assert field in agent

    def test_get_employee_detail(self, client, auth_headers):
        """GET /v1/employees/{id} 应返回单个 agent 详情。"""
        # 先列出拿到 agent_id
        resp = client.get("/v1/employees", headers=auth_headers)
        agent_id = resp.json()["agents"][0]["id"]

        resp = client.get(f"/v1/employees/{agent_id}", headers=auth_headers)
        assert resp.status_code == 200
        detail = resp.json()
        assert detail["id"] == agent_id
        assert "metadata" in detail
        assert "checkpoint_state" in detail

    def test_get_nonexistent_employee_404(self, client, auth_headers):
        """GET /v1/employees/不存在 应返回 404。"""
        resp = client.get("/v1/employees/nonexistent", headers=auth_headers)
        assert resp.status_code == 404

    def test_pause_and_resume_agent(self, client, auth_headers):
        """POST pause → resume 应正确切换 agent 状态。"""
        resp = client.get("/v1/employees", headers=auth_headers)
        agent_id = resp.json()["agents"][0]["id"]

        # 暂停
        resp = client.post(f"/v1/employees/{agent_id}/pause", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["paused"] is True
        assert resp.json()["status"] == "paused"

        # 恢复
        resp = client.post(f"/v1/employees/{agent_id}/resume", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["resumed"] is True
        assert resp.json()["status"] == "idle"

    def test_pause_nonexistent_agent_404(self, client, auth_headers):
        """POST pause 不存在的 agent 应返回 404。"""
        resp = client.post("/v1/employees/ghost/pause", headers=auth_headers)
        assert resp.status_code == 404


class TestGoalsEndpoint:
    """Goal 端点测试。"""

    def test_create_goal_executes_and_returns_completed(self, client, auth_headers):
        """POST /v1/goals 应创建并执行 Goal，返回真实结果。"""
        body = {
            "natural_language": "python: result = sum(i*i for i in range(1, 11))",
            "scope": "L1",
        }
        resp = client.post("/v1/goals", json=body, headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "goal_id" in data
        assert data["state"] in ("completed", "failed")
        assert data["natural_language"] == body["natural_language"]
        assert "trace" in data
        assert len(data["trace"]) > 0

    def test_list_goals_after_creation(self, client, auth_headers):
        """GET /v1/goals 在创建后应能列出。"""
        # 先创建一个
        body = {"natural_language": "python: result = 1+1", "scope": "L1"}
        create_resp = client.post("/v1/goals", json=body, headers=auth_headers)
        goal_id = create_resp.json()["goal_id"]

        # 列出
        resp = client.get("/v1/goals", headers=auth_headers)
        assert resp.status_code == 200
        goals = resp.json()["goals"]
        assert any(g["goal_id"] == goal_id for g in goals)

    def test_get_goal_detail(self, client, auth_headers):
        """GET /v1/goals/{id} 应返回详情。"""
        body = {"natural_language": "python: result = 42", "scope": "L1"}
        create_resp = client.post("/v1/goals", json=body, headers=auth_headers)
        goal_id = create_resp.json()["goal_id"]

        resp = client.get(f"/v1/goals/{goal_id}", headers=auth_headers)
        assert resp.status_code == 200
        detail = resp.json()
        assert detail["goal_id"] == goal_id
        assert "trace" in detail
        assert "evaluation" in detail

    def test_get_nonexistent_goal_404(self, client, auth_headers):
        """GET /v1/goals/不存在 应返回 404。"""
        resp = client.get("/v1/goals/nonexistent", headers=auth_headers)
        assert resp.status_code == 404


class TestWorkflowsEndpoint:
    """Workflow 端点测试。"""

    def test_list_workflows_empty(self, client, auth_headers):
        """GET /v1/workflows 在无 Goal 时应返回空列表。"""
        resp = client.get("/v1/workflows", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "workflows" in data
        assert "count" in data
        assert data["count"] == 0

    def test_list_workflows_after_goal(self, client, auth_headers):
        """创建 Goal 后 GET /v1/workflows 应包含该 Goal。"""
        body = {"natural_language": "python: result = 100", "scope": "L1"}
        create_resp = client.post("/v1/goals", json=body, headers=auth_headers)
        goal_id = create_resp.json()["goal_id"]

        resp = client.get("/v1/workflows", headers=auth_headers)
        assert resp.status_code == 200
        wfs = resp.json()["workflows"]
        assert any(w["goal_id"] == goal_id for w in wfs)

    def test_get_workflow_detail(self, client, auth_headers):
        """GET /v1/workflows/{id} 应返回 workflow 状态。"""
        body = {"natural_language": "python: result = 0", "scope": "L1"}
        create_resp = client.post("/v1/goals", json=body, headers=auth_headers)
        goal_id = create_resp.json()["goal_id"]

        resp = client.get(f"/v1/workflows/{goal_id}", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["goal_id"] == goal_id
        assert "tasks" in data
        assert "trace" in data

    def test_get_nonexistent_workflow_404(self, client, auth_headers):
        """GET /v1/workflows/不存在 应返回 404。"""
        resp = client.get("/v1/workflows/nonexistent", headers=auth_headers)
        assert resp.status_code == 404


class TestAuthGate:
    """鉴权闸门测试。"""

    def test_employees_without_token_401(self, client):
        """未携带令牌访问 /v1/employees 应返回 401。"""
        resp = client.get("/v1/employees")
        assert resp.status_code == 401

    def test_goals_without_token_401(self, client):
        """未携带令牌访问 /v1/goals 应返回 401。"""
        resp = client.get("/v1/goals")
        assert resp.status_code == 401

    def test_workflows_without_token_401(self, client):
        """未携带令牌访问 /v1/workflows 应返回 401。"""
        resp = client.get("/v1/workflows")
        assert resp.status_code == 401

    def test_create_goal_without_token_401(self, client):
        """未携带令牌 POST /v1/goals 应返回 401。"""
        resp = client.post("/v1/goals", json={"natural_language": "test"})
        assert resp.status_code == 401
