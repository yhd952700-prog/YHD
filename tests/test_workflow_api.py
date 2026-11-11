"""工作流 / 审批中心 HTTP 层的端到端测试（TestClient + 假执行桥）。"""

from __future__ import annotations

import time
from typing import Callable, Dict, Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.workflow.engine as engine_module
import src.workflow.scheduler as scheduler_module
import src.workflow.store as store_module
from src.gateway.ai_management import GoalViewer
from src.gateway.approvals import build_router as build_approvals_router
from src.gateway.workflow import build_router as build_workflow_router
from src.workflow.definition import builtin_submit_approve_execute_archive
from src.workflow.engine import ExecutionBridge, NodeFailure, WorkflowRuntime


class FakeBridge(ExecutionBridge):
    def __init__(self) -> None:
        self.ran: list[str] = []
        self.notices: list[str] = []
        self.fail_next = False

    def submit_task(self, *, instruction, scope, project_id, owner_account_id, employee_name):
        return {"goal_id": "goal-task", "state": "running"}

    def run_agent(
        self,
        *,
        instruction,
        scope,
        project_id,
        owner_account_id,
        employee_name,
        timeout_seconds,
        should_stop: Optional[Callable[[], bool]] = None,
    ):
        self.ran.append(instruction)
        if self.fail_next:
            self.fail_next = False
            raise NodeFailure("模拟执行失败", code="fake_failure")
        return {
            "goal_id": "goal-agent",
            "state": "completed",
            "summary": "已执行",
            "artifacts": [],
            "task_count": 0,
        }

    def notify(self, *, message, level, run_id, definition_name, node_name):
        self.notices.append(message)
        return {"delivered": ["event"], "message": message}


@pytest.fixture()
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("LIUHAO_WORKFLOW_DB", str(tmp_path / "workflow_api.db"))
    monkeypatch.setenv("LIUHAO_WORKFLOW_SCHEDULER", "0")
    store_module.reset_workflow_stores()
    engine_module.reset_workflow_runtime()
    scheduler_module.reset_workflow_scheduler()
    bridge = FakeBridge()
    runtime = WorkflowRuntime(store=store_module.get_workflow_store(), bridge=bridge)
    engine_module._runtime = runtime
    yield {"bridge": bridge, "runtime": runtime, "store": store_module.get_workflow_store()}
    engine_module.reset_workflow_runtime()
    scheduler_module.reset_workflow_scheduler()
    store_module.reset_workflow_stores()


def _client(viewer: GoalViewer) -> TestClient:
    app = FastAPI()
    app.include_router(build_workflow_router(lambda: viewer))
    app.include_router(build_approvals_router(lambda: viewer))
    return TestClient(app)


ROOT = GoalViewer(principal="root", subtree=("root",), is_root=True)


def _create_definition(client: TestClient, name: str = "提交 → 审批 → 执行 → 归档") -> Dict:
    payload = builtin_submit_approve_execute_archive()
    payload["name"] = name
    response = client.post("/v1/workflow/definitions", json=payload)
    assert response.status_code == 201, response.text
    return response.json()["definition"]


def _wait_run(client: TestClient, run_id: str, *, timeout: float = 8.0) -> Dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        response = client.get(f"/v1/workflow/runs/{run_id}")
        assert response.status_code == 200, response.text
        run = response.json()["run"]
        if run["status"] in {"completed", "failed", "cancelled"}:
            return response.json()
        time.sleep(0.03)
    raise AssertionError(f"运行未在 {timeout}s 内结束：{response.json()}")


# ── 定义 CRUD ──────────────────────────────────────────────


def test_definition_crud_export_import(env):
    with _client(ROOT) as client:
        created = _create_definition(client, "定义CRUD")
        def_id = created["def_id"]
        assert created["node_count"] == 6
        assert created["enabled"] is True

        listing = client.get("/v1/workflow/definitions").json()["definitions"]
        assert [item["def_id"] for item in listing] == [def_id]

        fetched = client.get(f"/v1/workflow/definitions/{def_id}").json()["definition"]
        assert fetched["name"] == "定义CRUD"
        assert fetched["nodes"][0]["type"] == "start"

        payload = builtin_submit_approve_execute_archive()
        payload["name"] = "定义CRUD-改"
        payload["description"] = "改过"
        updated = client.put(f"/v1/workflow/definitions/{def_id}", json=payload)
        assert updated.status_code == 200, updated.text
        assert updated.json()["definition"]["version"] == 2

        disabled = client.post(
            f"/v1/workflow/definitions/{def_id}/enable", json={"enabled": False}
        )
        assert disabled.status_code == 200
        assert disabled.json()["definition"]["enabled"] is False
        run_attempt = client.post(f"/v1/workflow/definitions/{def_id}/run", json={"inputs": {}})
        assert run_attempt.status_code == 409
        client.post(f"/v1/workflow/definitions/{def_id}/enable", json={"enabled": True})

        duplicated = client.post(f"/v1/workflow/definitions/{def_id}/duplicate", json={})
        assert duplicated.status_code == 201
        assert "副本" in duplicated.json()["definition"]["name"]

        exported = client.get(f"/v1/workflow/definitions/{def_id}/export").json()
        assert exported["kind"] == "liuhao.workflow.definition"
        assert "owner_account_id" not in exported["definition"]

        imported = client.post("/v1/workflow/import", json=exported)
        assert imported.status_code == 201, imported.text
        assert imported.json()["definition"]["def_id"] != def_id


def test_definition_validation_error_is_400(env):
    with _client(ROOT) as client:
        response = client.post(
            "/v1/workflow/definitions",
            json={"name": "坏流程", "nodes": [], "edges": []},
        )
        assert response.status_code == 400
        assert response.json()["detail"]["code"] in {"invalid_definition", "invalid_node"}


# ── 运行 + 审批闭环 ────────────────────────────────────────


def test_run_waits_for_approval_then_completes(env):
    bridge = env["bridge"]
    with _client(ROOT) as client:
        definition = _create_definition(client, "审批闭环")
        started = client.post(
            f"/v1/workflow/definitions/{definition['def_id']}/run",
            json={"inputs": {"instruction": "写一份周报"}},
        )
        assert started.status_code == 201, started.text
        run = started.json()["run"]
        assert run["status"] == "waiting_approval"

        queue = client.get("/v1/approvals", params={"status": "pending"}).json()["approvals"]
        assert len(queue) == 1
        approval = queue[0]
        assert approval["submitted_by"] == "root"
        assert approval["operation"]
        assert approval["reason"]
        assert approval["remaining_seconds"] > 0
        assert approval["run"]["run_id"] == run["run_id"]

        detail = client.get(f"/v1/approvals/{approval['approval_id']}").json()["approval"]
        assert detail["can_decide"] is True
        assert detail["approval_id"] == approval["approval_id"]

        decided = client.post(
            f"/v1/approvals/{approval['approval_id']}/approve",
            json={"comment": "同意执行"},
        )
        assert decided.status_code == 200, decided.text
        assert decided.json()["approval"]["status"] == "approved"

        finished = _wait_run(client, run["run_id"])
        assert finished["run"]["status"] == "completed"
        assert bridge.ran == ["写一份周报"]
        assert bridge.notices
        step_nodes = {step["node_id"] for step in finished["steps"]}
        assert {"start", "approve", "execute", "notify", "archive"} == step_nodes
        for step in finished["steps"]:
            assert step["status"] == "completed"
        execute_step = next(step for step in finished["steps"] if step["node_id"] == "execute")
        assert execute_step["outputs"]["goal_id"] == "goal-agent"
        assert execute_step["duration_ms"] is not None

        # 重复处理同一条审批必须被拒绝，不能重复驱动流程。
        again = client.post(f"/v1/approvals/{approval['approval_id']}/approve", json={})
        assert again.status_code == 409


def test_reject_terminates_workflow(env):
    bridge = env["bridge"]
    with _client(ROOT) as client:
        definition = _create_definition(client, "拒绝终止")
        run = client.post(
            f"/v1/workflow/definitions/{definition['def_id']}/run",
            json={"inputs": {"instruction": "别执行"}},
        ).json()["run"]
        approval = client.get("/v1/approvals").json()["approvals"][0]
        decided = client.post(
            f"/v1/approvals/{approval['approval_id']}/reject",
            json={"comment": "不符合要求"},
        )
        assert decided.status_code == 200
        finished = client.get(f"/v1/workflow/runs/{run['run_id']}").json()
        assert finished["run"]["status"] == "cancelled"
        assert finished["run"]["outcome"] == "rejected"
        assert bridge.ran == []
        approve_step = next(step for step in finished["steps"] if step["node_id"] == "approve")
        assert approve_step["status"] == "rejected"


def test_cancel_run_and_pending_queue(env):
    with _client(ROOT) as client:
        definition = _create_definition(client, "取消运行")
        run = client.post(
            f"/v1/workflow/definitions/{definition['def_id']}/run",
            json={"inputs": {"instruction": "先挂着"}},
        ).json()["run"]
        cancelled = client.post(f"/v1/workflow/runs/{run['run_id']}/cancel")
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["run"]["status"] == "cancelled"
        assert client.get("/v1/approvals").json()["approvals"] == []


def test_approval_queue_isolated_by_account(env):
    with _client(ROOT) as client:
        definition = _create_definition(client, "隔离")
        client.post(
            f"/v1/workflow/definitions/{definition['def_id']}/run",
            json={"inputs": {"instruction": "x"}},
        )
        bob = GoalViewer(principal="bob", subtree=("bob",))
        with _client(bob) as other:
            assert other.get("/v1/approvals").json()["approvals"] == []
            assert other.get("/v1/workflow/definitions").json()["definitions"] == []


def test_summary_and_templates(env):
    with _client(ROOT) as client:
        templates = client.get("/v1/workflow/templates").json()["templates"]
        assert templates and templates[0]["name"].startswith("提交")
        nodes = client.get("/v1/workflow/node-types").json()["types"]
        assert {"approval", "agent", "notify", "parallel", "join"} <= {
            item["type"] for item in nodes
        }
        summary = client.get("/v1/workflow/summary").json()
        assert summary["definitions"] == 0
        assert summary["pending_approvals"] == 0
