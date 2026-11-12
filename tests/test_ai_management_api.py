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

import time

import pytest
from fastapi.testclient import TestClient

from src.gateway.main import get_app
from src.gateway.ai_management import AIStateManager


def _error_detail(resp) -> dict:
    """兼容两种错误体：结构化展开后 code/message 在顶层，老格式在 detail 里。"""
    body = resp.json()
    detail = body.get("detail")
    return detail if isinstance(detail, dict) else body


@pytest.fixture
def client():
    """创建带鉴权的测试客户端。"""
    app = get_app()
    return TestClient(app)


# auth_headers fixture 来自 conftest.py（用 get_jwt_handler().create_token 签发真实令牌）


@pytest.fixture(autouse=True)
def reset_state_manager(tmp_path, monkeypatch):
    """每个测试前重置 AIStateManager 单例，避免跨测试污染。

    同时做工作区隔离。`reset_state_manager` 每用例把 `AIStateManager._instance`
    置 None，于是下一次 `_ensure_runtime()` 会**重新捕获** `workspace_root()`。
    本文件原先不设 `LIUHAO_WORKSPACE_ROOT`，而 goals / employees / projects 三个
    store 的兜底路径不是共享工作区，而是**直接写仓库根**：
    `src/gateway/ai_management.py::_default_goals_path`、
    `src/ai/employee_store.py::_default_store_path`、
    `src/gateway/projects.py::_default_path` 都返回
    `<repo_root>/.liuhao_goals.json` / `.liuhao_employees.json` /
    `.liuhao_projects.json`。实测这些文件会被本文件真的写出来（2026-10-4），
    而且**不在 .gitignore 里**——一个 `git add -A` 就会把它们卷进提交。
    导出到 tmp_path 后，写入只落在临时目录。
    """
    monkeypatch.setenv("LIUHAO_WORKSPACE_ROOT", str(tmp_path))
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


# ── 单独指派员工（POST /v1/goals/assign）─────────────────────────

class TestAssignTask:
    """“我也可以单独安排员工干活” —— 执行主体由调用方点名。"""

    def test_assign_unknown_agent_is_rejected(self, client, auth_headers):
        """指定不存在的员工：404 + 明确错误码，绝不静默换成别人。"""
        resp = client.post(
            "/v1/goals/assign",
            json={"agent_id": "ghost-a999", "instruction": "整理今天的日报"},
            headers=auth_headers,
        )
        assert resp.status_code == 404
        detail = _error_detail(resp)
        assert detail["code"] == "agent_not_found"
        assert "ghost-a999" in detail["message"]
        # 明说"不会改用其他员工"，这是语义承诺，不只是文案
        assert "不会改用其他员工" in detail["message"]

    def test_assign_real_agent_pins_every_task_to_it(self, client, auth_headers):
        """指派真实员工：任务落在被点名的那位身上（不再自动分配）。"""
        agents = client.get("/v1/employees", headers=auth_headers).json()["agents"]
        agent_id = agents[0]["id"]

        resp = client.post(
            "/v1/goals/assign",
            json={
                "agent_id": agent_id,
                "instruction": "python: result = 1+1",
                "background": False,
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["assigned_agent"] == agent_id
        assert body["state"] in ("completed", "partial", "failed", "cancelled")
        for task in body["tasks"]:
            assert task["assigned_agent"] == agent_id

    def test_assign_without_token_401(self, client):
        resp = client.post(
            "/v1/goals/assign",
            json={"agent_id": "a1", "instruction": "test"},
        )
        assert resp.status_code == 401


# ── 单任务重试（POST /v1/goals/{id}/tasks/{task_id}/retry）──────

class _StubResult:
    """只承载 _result_to_dict 会读的字段（该方法在用例里被替换）。"""

    def __init__(self, tasks, replan_suggested=False):
        self.tasks_payload = tasks
        self.replan_suggested = replan_suggested


class _StubRuntime:
    def __init__(self, result):
        self._result = result
        self.calls = []

    def run_goal(self, **kwargs):
        self.calls.append(kwargs)
        return self._result


def _seed_failed_goal(mgr, goal_id="goal-retry"):
    mgr._goals[goal_id] = {
        "goal_id": goal_id,
        "state": "failed",
        "natural_language": "整理周报并发送",
        "scope": "L1",
        "error": "task failed",
        "tasks": [
            {"id": "t1", "name": "整理周报", "status": "completed",
             "result": "已整理，保持原样", "error": None, "assigned_agent": "emp-a1"},
            {"id": "t2", "name": "发送邮件", "status": "failed",
             "result": None, "error": "smtp timeout", "assigned_agent": "emp-a2"},
        ],
    }
    return goal_id


class TestRetrySingleTask:
    """失败任务单独重试：只跑这一个，其余结果原样保留。"""

    def test_retry_route_reports_goal_not_found(self, client, auth_headers):
        """HTTP 面：路由可达、鉴权生效、错误码明确。"""
        resp = client.post(
            "/v1/goals/goal-ghost/tasks/task-ghost/retry", headers=auth_headers
        )
        assert resp.status_code == 404
        detail = _error_detail(resp)
        assert detail["code"] == "goal_not_found"
        assert "goal-ghost" in detail["message"]

    def test_retry_without_token_401(self, client):
        resp = client.post("/v1/goals/goal-ghost/tasks/task-ghost/retry")
        assert resp.status_code == 401

    def test_retry_rejects_non_failed_task(self):
        """已成功的任务不能"重试"，否则就是顺带重跑。"""
        from src.gateway.ai_management import GoalWorkError

        mgr = AIStateManager()
        goal_id = _seed_failed_goal(mgr)
        with pytest.raises(GoalWorkError) as excinfo:
            mgr.retry_task(goal_id, "t1")
        assert excinfo.value.status_code == 409
        assert excinfo.value.code == "task_not_retryable"

    def test_retry_unknown_task_is_404(self):
        from src.gateway.ai_management import GoalWorkError

        mgr = AIStateManager()
        goal_id = _seed_failed_goal(mgr)
        with pytest.raises(GoalWorkError) as excinfo:
            mgr.retry_task(goal_id, "t-ghost")
        assert excinfo.value.status_code == 404
        assert excinfo.value.code == "task_not_found"

    def test_retry_does_not_rerun_or_overwrite_other_tasks(self, monkeypatch):
        """核心承诺：只重跑这一个，其余任务既不执行、也不被覆盖。"""
        mgr = AIStateManager()
        goal_id = _seed_failed_goal(mgr)

        retried = [{
            "id": "new-task-id",  # 重跑会生成新的 task id —— 网关必须保留原卡片 id
            "name": "发送邮件",
            "status": "completed",
            "result": "已发送",
            "error": None,
            "assigned_agent": "emp-a2",
        }]
        stub = _StubRuntime(_StubResult(retried))
        monkeypatch.setattr(AIStateManager, "_ensure_runtime", lambda self: stub)
        monkeypatch.setattr(
            AIStateManager,
            "_result_to_dict",
            staticmethod(lambda result, nl, scope: {
                "tasks": result.tasks_payload, "evaluation": None,
            }),
        )

        entry = mgr.retry_task(goal_id, "t2", principal="human:test")

        # 1) 内核被要求"只跑这个任务"，并锁定原执行主体
        call = stub.calls[0]
        assert call["only_task_name"] == "发送邮件"
        assert call["pinned_agent"] == "emp-a2"
        # 2) 其余任务原样保留（结果、状态、id 都没被动过）
        t1 = next(t for t in entry["tasks"] if t["id"] == "t1")
        assert t1["status"] == "completed"
        assert t1["result"] == "已整理，保持原样"
        # 3) 被重试的任务用原卡片 id 回填新结果
        t2 = next(t for t in entry["tasks"] if t["id"] == "t2")
        assert t2["status"] == "completed"
        assert t2["result"] == "已发送"
        assert len(entry["tasks"]) == 2
        assert entry["state"] == "completed"


# ── 运行中的逐任务进度（增量写回）───────────────────────────────

class TestIncrementalTaskProgress:
    """执行期间 GET /v1/goals/{id} 应该已经能看到逐条任务。"""

    def test_snapshot_updates_slot_without_clobbering_others(self):
        mgr = AIStateManager()
        mgr._goals["g-progress"] = {
            "goal_id": "g-progress",
            "state": "running",
            "tasks": [
                {"id": "t1", "name": "第一步", "status": "completed", "result": "keep"},
            ],
        }
        mgr._record_task_snapshot(
            "g-progress",
            [
                {"id": "t1", "name": "第一步", "status": "completed", "result": "keep"},
                {"id": "t2", "name": "第二步", "status": "running", "result": None},
            ],
        )
        tasks = mgr._goals["g-progress"]["tasks"]
        assert [t["id"] for t in tasks] == ["t1", "t2"]
        assert tasks[0]["result"] == "keep"
        assert tasks[1]["status"] == "running"

    def test_late_snapshot_cannot_overwrite_terminal_state(self):
        """终态落盘后，迟到的快照一律丢弃（不能把完成改回 running）。"""
        mgr = AIStateManager()
        mgr._goals["g-done"] = {
            "goal_id": "g-done",
            "state": "completed",
            "tasks": [{"id": "t1", "name": "唯一任务", "status": "completed",
                       "result": "done"}],
        }
        mgr._record_task_snapshot(
            "g-done",
            [{"id": "t1", "name": "唯一任务", "status": "running", "result": None}],
        )
        assert mgr._goals["g-done"]["tasks"][0]["status"] == "completed"
        assert mgr._goals["g-done"]["tasks"][0]["result"] == "done"

    def test_background_goal_exposes_tasks_while_running(self, client, auth_headers):
        """HTTP 面：后台目标的 ``tasks`` 不再是"执行结束才一次性写入"。"""
        resp = client.post(
            "/v1/goals",
            json={"natural_language": "python: result = 1+1", "background": True},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        goal_id = resp.json()["goal_id"]

        # 刚提交时执行线程可能还没建好计划（如实为空）；轮询到任务出现或终态。
        detail = {}
        deadline = time.time() + 10
        while time.time() < deadline:
            detail = client.get(f"/v1/goals/{goal_id}", headers=auth_headers).json()
            if detail.get("tasks") or detail.get("state") != "running":
                break
            time.sleep(0.1)

        assert isinstance(detail.get("tasks"), list)
        assert detail["tasks"], "目标（运行中或已结束）都应返回逐条任务"
        assert all("status" in t for t in detail["tasks"])


class TestRetryRecomputesWholeGoal:
    """重试后必须按真实子任务结果重算 goal 状态、artifacts 和 evaluation。"""

    def _seed_with_two_failures(self, mgr, goal_id="goal-retry-whole"):
        mgr._goals[goal_id] = {
            "goal_id": goal_id,
            "state": "failed",
            "natural_language": "整理周报并发送",
            "scope": "L1",
            "error": "task failed",
            "artifacts": ["reports/weekly.md"],
            "evaluation": {
                "outcome": "EvaluationOutcome.SUCCESS",
                "summary": "只看到一项任务，误报成功",
            },
            "tasks": [
                {"id": "t1", "name": "整理周报", "status": "completed",
                 "result": "已整理", "error": None, "assigned_agent": "emp-a1"},
                {"id": "t2", "name": "发送邮件", "status": "failed",
                 "result": None, "error": "smtp timeout", "assigned_agent": "emp-a2"},
                {"id": "t3", "name": "归档副本", "status": "failed",
                 "result": None, "error": "disk full", "assigned_agent": "emp-a3"},
            ],
        }
        return goal_id

    def test_retry_keeps_goal_failed_and_evaluation_not_success(self, monkeypatch):
        mgr = AIStateManager()
        goal_id = self._seed_with_two_failures(mgr)

        retried = [{
            "id": "new-task-id",
            "name": "发送邮件",
            "status": "completed",
            "result": "已发送",
            "error": None,
            "assigned_agent": "emp-a2",
        }]
        stub = _StubRuntime(_StubResult(retried))
        monkeypatch.setattr(AIStateManager, "_ensure_runtime", lambda self: stub)
        monkeypatch.setattr(
            AIStateManager,
            "_result_to_dict",
            staticmethod(lambda result, nl, scope: {
                "tasks": result.tasks_payload,
                "artifacts": ["outbox/sent.log"],
                "evaluation": {
                    "outcome": "EvaluationOutcome.SUCCESS",
                    "summary": "这一项成功了",
                },
            }),
        )

        entry = mgr.retry_task(goal_id, "t2", principal="human:test")

        # t3 仍然失败 -> goal 必须仍是 failed，evaluation 不能是 success
        assert entry["state"] == "failed"
        assert entry["evaluation"]["outcome"] != "success"
        assert entry["evaluation"]["outcome"] == "partial"
        # 结论必须覆盖整个 goal：成功与仍失败都要出现
        summary = entry["evaluation"]["summary"]
        assert "已发送" in summary
        assert "归档副本" in summary
        # artifacts 是真实产物清单：原来的 + 重试产生的，去重后都在
        assert entry["artifacts"] == ["reports/weekly.md", "outbox/sent.log"]

    def test_retry_becomes_completed_and_artifacts_non_empty(self, monkeypatch):
        mgr = AIStateManager()
        goal_id = self._seed_with_two_failures(mgr)

        retried = [{
            "id": "new-task-id",
            "name": "发送邮件",
            "status": "completed",
            "result": "已发送",
            "error": None,
            "assigned_agent": "emp-a2",
        }]
        # 让另一个失败任务也成功重试（真实场景：先修一个，再修另一个）
        mgr._goals[goal_id]["tasks"][2]["status"] = "completed"
        mgr._goals[goal_id]["tasks"][2]["result"] = "已归档"

        stub = _StubRuntime(_StubResult(retried))
        monkeypatch.setattr(AIStateManager, "_ensure_runtime", lambda self: stub)
        monkeypatch.setattr(
            AIStateManager,
            "_result_to_dict",
            staticmethod(lambda result, nl, scope: {
                "tasks": result.tasks_payload,
                "artifacts": ["outbox/sent.log"],
                "evaluation": {"outcome": "EvaluationOutcome.SUCCESS", "summary": ""},
            }),
        )

        entry = mgr.retry_task(goal_id, "t2", principal="human:test")

        assert entry["state"] == "completed"
        assert entry["artifacts"] == ["reports/weekly.md", "outbox/sent.log"]
        assert entry["evaluation"]["summary"].strip(), "重试后必须有结论"

