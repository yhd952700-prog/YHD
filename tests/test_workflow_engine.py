"""工作流运行引擎的行为测试（不触真 Agent，用假 bridge）。

覆盖：审批挂起→通过→继续→完成、拒绝终止、超时失效、失败重试、
条件分支、并行+汇聚、定义校验。
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, Optional

import pytest

from src.workflow.definition import (
    DefinitionError,
    builtin_submit_approve_execute_archive,
    normalise_definition,
)
from src.workflow.engine import (
    ExecutionBridge,
    NodeFailure,
    WorkflowRuntime,
)
from src.workflow.store import WorkflowStore


class FakeBridge(ExecutionBridge):
    def __init__(self) -> None:
        self.submitted: list[str] = []
        self.ran: list[str] = []
        self.notices: list[str] = []
        self.fail_times = 0
        self.stop_checks = 0

    def submit_task(self, *, instruction, scope, project_id, owner_account_id, employee_name):
        self.submitted.append(instruction)
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
        if should_stop is not None:
            self.stop_checks += 1
            assert not should_stop()
        if self.fail_times > 0:
            self.fail_times -= 1
            raise NodeFailure("模拟执行失败", code="fake_failure")
        return {
            "goal_id": "goal-agent",
            "state": "completed",
            "summary": "已产出周报",
            "artifacts": ["weekly.md"],
            "task_count": 1,
        }

    def notify(self, *, message, level, run_id, definition_name, node_name):
        self.notices.append(message)
        return {"delivered": ["event"], "message": message}


@pytest.fixture()
def store(tmp_path):
    return WorkflowStore(str(tmp_path / "workflow_test.db"))


def _definition(nodes, edges, *, triggers=None, variables=None):
    return {
        "schema_version": 1,
        "name": "测试流程",
        "description": "",
        "enabled": True,
        "variables": variables or {},
        "nodes": nodes,
        "edges": edges,
        "triggers": triggers or [{"type": "manual"}],
    }


def _record(store: WorkflowStore, payload: Dict[str, Any], owner: str = "root") -> Dict[str, Any]:
    normalised = normalise_definition(payload, owner_account_id=owner, created_by=owner)
    return store.create_definition(normalised, owner_account_id=owner, created_by=owner)


def _wait_run(
    store: WorkflowStore,
    run_id: str,
    statuses: set[str],
    *,
    timeout: float = 8.0,
) -> Dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        run = store.get_run(run_id)
        if run and run["status"] in statuses:
            return run
        time.sleep(0.02)
    raise AssertionError(f"运行 {run_id} 未在 {timeout}s 内到达 {statuses}：{store.get_run(run_id)}")


# ── 主流程：提交 → 审批 → 执行 → 归档 ────────────────────────


def test_submit_approve_execute_archive(store):
    bridge = FakeBridge()
    runtime = WorkflowRuntime(store, bridge)
    record = _record(store, builtin_submit_approve_execute_archive())

    run = runtime.start_run(
        record,
        inputs={"instruction": "写一份本周周报"},
        submitted_by="root",
        owner_account_id="root",
        background=False,
    )
    assert run["status"] == "waiting_approval"
    assert run["current_node_id"] == "approve"
    assert bridge.ran == []

    approvals = store.list_approvals(status="pending")
    assert len(approvals) == 1
    approval = approvals[0]
    assert approval["submitted_by"] == "root"
    assert approval["operation"]
    assert approval["expires_at"] > time.time()

    runtime.apply_approval_decision(
        approval["approval_id"], decision="approved", decided_by="root", comment="可以做"
    )
    finished = _wait_run(store, run["run_id"], {"completed", "failed", "cancelled"})
    assert finished["status"] == "completed", finished
    assert bridge.ran == ["写一份本周周报"]
    assert bridge.notices == ["流程「提交 → 审批 → 执行 → 归档」执行完成"]

    steps = store.list_steps(run["run_id"])
    by_node = {step["node_id"]: step for step in steps}
    assert set(by_node) == {"start", "approve", "execute", "notify", "archive"}
    for onode in ("start", "approve", "execute", "notify", "archive"):
        assert by_node[onode]["status"] == "completed", by_node[onode]
        assert by_node[onode]["duration_ms"] is not None
    assert by_node["execute"]["outputs"]["goal_id"] == "goal-agent"
    assert by_node["execute"]["outputs"]["summary"] == "已产出周报"
    assert by_node["approve"]["outputs"]["outcome"] == "approved"
    assert by_node["archive"]["outputs"]["archived"] is True


def test_rejection_terminates_run(store):
    bridge = FakeBridge()
    runtime = WorkflowRuntime(store, bridge)
    record = _record(store, builtin_submit_approve_execute_archive())

    run = runtime.start_run(
        record,
        inputs={"instruction": "不该被执行"},
        submitted_by="root",
        owner_account_id="root",
        background=False,
    )
    approval = store.list_approvals(status="pending")[0]
    runtime.apply_approval_decision(
        approval["approval_id"], decision="rejected", decided_by="root", comment="范围太大"
    )
    final = store.get_run(run["run_id"])
    assert final["status"] == "cancelled"
    assert final["outcome"] == "rejected"
    assert "范围太大" in final["error"]
    assert bridge.ran == []
    step = store.get_step_by_node(run["run_id"], "approve")
    assert step["status"] == "rejected"


def test_approval_timeout_expires_and_terminates(store):
    runtime = WorkflowRuntime(store, FakeBridge())
    definition = builtin_submit_approve_execute_archive()
    definition["nodes"][1]["config"]["timeout_seconds"] = 60
    record = _record(store, definition)

    run = runtime.start_run(
        record,
        inputs={"instruction": "等不到审批"},
        submitted_by="root",
        owner_account_id="root",
        background=False,
    )
    assert run["status"] == "waiting_approval"
    runtime.expire_overdue(now=time.time() + 3600)

    final = store.get_run(run["run_id"])
    assert final["status"] == "cancelled"
    assert final["outcome"] == "expired"
    approval = store.list_approvals()[0]
    assert approval["status"] == "expired"
    step = store.get_step_by_node(run["run_id"], "approve")
    assert step["status"] == "failed"
    assert step["error_code"] == "approval_expired"


def test_decide_after_expiry_is_rejected(store):
    runtime = WorkflowRuntime(store, FakeBridge())
    record = _record(store, builtin_submit_approve_execute_archive())
    runtime.start_run(
        record,
        inputs={"instruction": "x"},
        submitted_by="root",
        owner_account_id="root",
        background=False,
    )
    approval = store.list_approvals(status="pending")[0]
    store.expire_overdue_approvals(now=time.time() + 7200)
    with pytest.raises(Exception) as excinfo:
        store.decide_approval(
            approval["approval_id"], decision="approved", decided_by="root"
        )
    assert getattr(excinfo.value, "code", "") in {
        "approval_expired",
        "approval_already_decided",
    }
    assert "expired" in str(getattr(excinfo.value, "message", ""))


# ── 重试 / 条件 / 并行 ─────────────────────────────────────


def test_agent_failure_retries_then_succeeds(store):
    bridge = FakeBridge()
    bridge.fail_times = 1
    runtime = WorkflowRuntime(store, bridge, sleep=lambda _: None)
    definition = _definition(
        nodes=[
            {"id": "start", "type": "start", "name": "开始", "config": {}},
            {
                "id": "run",
                "type": "agent",
                "name": "执行",
                "config": {
                    "instruction": "做点事",
                    "retry": {"max_attempts": 2, "backoff_seconds": 0},
                    "timeout_seconds": 30,
                },
            },
            {"id": "end", "type": "end", "name": "结束", "config": {}},
        ],
        edges=[
            {"from": "start", "to": "run"},
            {"from": "run", "to": "end"},
        ],
    )
    record = _record(store, definition)
    run = runtime.start_run(
        record, submitted_by="root", owner_account_id="root", background=False
    )
    assert run["status"] == "completed"
    step = store.get_step_by_node(run["run_id"], "run")
    assert step["attempt"] == 2
    assert step["status"] == "completed"
    assert bridge.ran == ["做点事", "做点事"]


def test_condition_branch_follows_step_output(store):
    bridge = FakeBridge()
    runtime = WorkflowRuntime(store, bridge)
    definition = _definition(
        nodes=[
            {"id": "start", "type": "start", "name": "开始", "config": {}},
            {
                "id": "task",
                "type": "task",
                "name": "创建任务",
                "config": {"instruction": "先提交"},
            },
            {"id": "done", "type": "notify", "name": "已完成分支", "config": {"message": "A"}},
            {"id": "pending", "type": "notify", "name": "进行中分支", "config": {"message": "B"}},
            {"id": "end", "type": "end", "name": "结束", "config": {}},
        ],
        edges=[
            {"from": "start", "to": "task"},
            {
                "from": "task",
                "to": "done",
                "condition": {"field": "steps.task.state", "op": "eq", "value": "completed"},
            },
            {"from": "task", "to": "pending"},
            {"from": "done", "to": "end"},
            {"from": "pending", "to": "end"},
        ],
    )
    record = _record(store, definition)
    run = runtime.start_run(
        record, submitted_by="root", owner_account_id="root", background=False
    )
    assert run["status"] == "completed"
    assert bridge.notices == ["B"]


def test_parallel_branches_run_and_join(store):
    bridge = FakeBridge()
    runtime = WorkflowRuntime(store, bridge)
    definition = _definition(
        nodes=[
            {"id": "start", "type": "start", "name": "开始", "config": {}},
            {"id": "fork", "type": "parallel", "name": "并行", "config": {}},
            {"id": "left", "type": "notify", "name": "左分支", "config": {"message": "左"}},
            {"id": "right", "type": "notify", "name": "右分支", "config": {"message": "右"}},
            {"id": "join", "type": "join", "name": "汇聚", "config": {"mode": "all"}},
            {"id": "end", "type": "end", "name": "结束", "config": {}},
        ],
        edges=[
            {"from": "start", "to": "fork"},
            {"from": "fork", "to": "left"},
            {"from": "fork", "to": "right"},
            {"from": "left", "to": "join"},
            {"from": "right", "to": "join"},
            {"from": "join", "to": "end"},
        ],
    )
    record = _record(store, definition)
    run = runtime.start_run(
        record, submitted_by="root", owner_account_id="root", background=False
    )
    assert run["status"] == "completed", run
    assert sorted(bridge.notices) == ["右", "左"]
    steps = store.list_steps(run["run_id"])
    by_node = {step["node_id"]: step for step in steps}
    assert by_node["left"]["status"] == "completed"
    assert by_node["right"]["status"] == "completed"
    assert by_node["join"]["status"] == "completed"
    assert by_node["fork"]["status"] == "completed"
    assert by_node["fork"]["outputs"]["branches"] == 2


# ── 定义校验 ────────────────────────────────────────────────


def test_definition_rejects_cycle(store):
    payload = _definition(
        nodes=[
            {"id": "start", "type": "start", "name": "开始", "config": {}},
            {"id": "fork", "type": "parallel", "name": "并行", "config": {}},
            {"id": "a", "type": "notify", "name": "A", "config": {"message": "a"}},
            {"id": "b", "type": "notify", "name": "B", "config": {"message": "b"}},
            {"id": "end", "type": "end", "name": "结束", "config": {}},
        ],
        edges=[
            {"from": "start", "to": "fork"},
            {"from": "fork", "to": "a"},
            {"from": "fork", "to": "end"},
            {"from": "a", "to": "b"},
            {"from": "b", "to": "a"},
        ],
    )
    with pytest.raises(DefinitionError) as excinfo:
        normalise_definition(payload)
    assert "环路" in excinfo.value.message


def test_definition_rejects_approval_inside_parallel(store):
    payload = _definition(
        nodes=[
            {"id": "start", "type": "start", "name": "开始", "config": {}},
            {"id": "fork", "type": "parallel", "name": "并行", "config": {}},
            {"id": "ap", "type": "approval", "name": "分支内审批", "config": {}},
            {"id": "join", "type": "join", "name": "汇聚", "config": {}},
            {"id": "end", "type": "end", "name": "结束", "config": {}},
        ],
        edges=[
            {"from": "start", "to": "fork"},
            {"from": "fork", "to": "ap"},
            {"from": "ap", "to": "join"},
            {"from": "join", "to": "end"},
        ],
    )
    with pytest.raises(DefinitionError) as excinfo:
        normalise_definition(payload)
    assert "并行分支" in excinfo.value.message


def test_definition_rejects_duplicate_start(store):
    payload = _definition(
        nodes=[
            {"id": "s1", "type": "start", "name": "开始1", "config": {}},
            {"id": "s2", "type": "start", "name": "开始2", "config": {}},
            {"id": "end", "type": "end", "name": "结束", "config": {}},
        ],
        edges=[
            {"from": "s1", "to": "end"},
            {"from": "s2", "to": "end"},
        ],
    )
    with pytest.raises(DefinitionError) as excinfo:
        normalise_definition(payload)
    assert "start" in excinfo.value.message


def test_default_bridge_resolves_real_execution_layer(store):
    """默认 bridge 必须真的接到 ``AIStateManager``，而不是只有假 bridge 能跑。

    这条用例刻意不注入 ``FakeBridge``。此前 ``_manager`` 里写的是
    ``src.ai.state_manager``——仓库里不存在这个模块，假 bridge 测试完全不经过
    导入路径，缺陷只在仓库级 import 门禁里暴露。这里同时钉住模块位置、可构造性
    与调用签名：模块名或方法签名任何一处漂移都会让这条用例变红。
    """
    import inspect

    from src.gateway.ai_management import AIStateManager
    from src.workflow.engine import GatewayExecutionBridge

    bridge = GatewayExecutionBridge()
    manager = bridge._manager()
    assert isinstance(manager, AIStateManager)

    # bridge 传给执行层的每个关键字，真实方法都必须接受（否则运行期 TypeError）。
    inspect.signature(AIStateManager.create_and_execute_goal).bind(
        manager,
        natural_language="检查默认实现",
        scope="L1",
        background=True,
        project_id=None,
        pinned_agent=None,
        owner_account_id="",
    )
    inspect.signature(AIStateManager.get_goal).bind(manager, "goal-id")

    # 不注入 bridge 时，运行时默认就该用这个能真跑的实现。
    runtime = WorkflowRuntime(store=store)
    assert isinstance(runtime.bridge, GatewayExecutionBridge)
