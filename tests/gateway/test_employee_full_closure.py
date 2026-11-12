"""员工管理完整闭环（功能完整性）端到端回归。

覆盖用户要求逐项：

1. 雇佣（单人/批量）→ 列表 → 改名/改职责 → 停用 → 重新启用 → 删除，
   每一步都做"重启"（重置 ``AIStateManager`` 单例，强制从磁盘重读）验证持久化；
2. 员工与账号的归属：子账号只能看到/管理自己名下的员工，主账号可见全部子树；
3. 员工记忆归属：员工产生的记忆归其所属账号，主账号可见，子账号不可见他支；
4. 状态显示：空闲/执行中/出错/完成 + "最后一次做什么、什么时候"，且不含
   ``planner-0`` / ``liuhao-default`` 这类内部标识；
5. 并发派活：同一员工同时被派两个任务时固定为**拒绝**（409），不丢任务、不串结果。

全部走真实 FastAPI + 真实 JWT + 真实员工库文件；执行层用受控 stub（本文件验证
员工闭环，不重复验证内核任务编排）。
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from types import SimpleNamespace

os.environ.setdefault(
    "LIUHAO_WORKSPACE_ROOT", tempfile.mkdtemp(prefix="liuhao-emp-closure-")
)

import pytest
from fastapi.testclient import TestClient

from src.gateway.ai_management import AIStateManager
from src.gateway.main import get_app


def _headers(subject: str) -> dict:
    """网关同一套 JWT 签发器；subject 即账号名。"""
    from src.security import get_jwt_handler

    token, _payload = get_jwt_handler().create_token(
        subject=subject, metadata={"kind": "human", "scope": "L0"}
    )
    return {"Authorization": f"Bearer {token}"}


def _make_tree(root: str = "boss", members=("alice", "bob")) -> None:
    from src.kernels.identity import get_identity_manager

    manager = get_identity_manager()
    manager.register_root_account(root)
    for member in members:
        manager.create_member_account(actor=root, principal=member)


class _StubRuntime:
    """受控执行层：返回一个真实的完成态，不跑真实模型。"""

    def __init__(self) -> None:
        self.calls: list = []

    def run_goal(self, **kwargs):
        self.calls.append(kwargs.get("goal_id"))
        goal_id = kwargs.get("goal_id") or "stub"
        return SimpleNamespace(
            goal_id=goal_id,
            state=SimpleNamespace(value="completed"),
            correlation_id=f"stub-{goal_id}",
            error=None,
            replan_count=0,
            replan_suggested=False,
            memory_keys=[],
            persist_errors=[],
            trace=None,
            context=None,
            evaluation=None,
        )


class _BlockingRuntime(_StubRuntime):
    """第一个目标卡住不返回，用来制造"同一员工并发派活"的真实时序。"""

    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()
        self.entered_count = 0

    def run_goal(self, **kwargs):
        self.entered_count += 1
        self.entered.set()
        assert self.release.wait(timeout=30), "test did not release the runtime"
        return super().run_goal(**kwargs)


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("LIUHAO_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.delenv("LIUHAO_DEFAULT_ACCOUNT_ID", raising=False)
    AIStateManager._instance = None
    yield
    AIStateManager._instance = None


@pytest.fixture
def client():
    with TestClient(get_app()) as test_client:
        yield test_client


def _restart() -> AIStateManager:
    """模拟进程重启：丢掉单例（含所有内存状态），下次调用只能从磁盘恢复。"""
    AIStateManager._instance = None
    return AIStateManager()


def _entries(c: TestClient, headers: dict) -> list:
    r = c.get("/v1/employees", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["employees"]


def _entry(c: TestClient, headers: dict, name: str) -> dict:
    for item in _entries(c, headers):
        if item["name"] == name:
            return item
    raise AssertionError(f"employee {name!r} not in list")


def _names(c: TestClient, headers: dict) -> set:
    return {item["name"] for item in _entries(c, headers)}


# ─── 1. 单人全生命周期 + 每步重启 ──────────────────────────────


def test_single_employee_lifecycle_survives_restart(client):
    c = client
    headers = _headers("solo")

    r = c.post(
        "/v1/employees",
        json={
            "name": "writer",
            "agent_count": 2,
            "agent_types": ["planner", "executor"],
            "role_description": "写产品文案",
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    employee_id = r.json()["employee_id"]
    assert employee_id, "员工必须有稳定 id（改名不断线）"

    entry = _entry(c, headers, "writer")
    assert entry["status"] == "idle"
    assert entry["status_label"] == "空闲"
    assert entry["role_description"] == "写产品文案"
    assert entry["owner_account_id"] == "solo"

    # 改名 + 改职责 → 重启后仍是同一位员工（稳定 id 不变）。
    r = c.patch(
        "/v1/employees/writer",
        json={"name": "editor", "role_description": "写长文与专栏"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    _restart()
    assert "writer" not in _names(c, headers)
    entry = _entry(c, headers, "editor")
    assert entry["employee_id"] == employee_id
    assert entry["role_description"] == "写长文与专栏"

    # 停用 → 重启后仍停用，且状态显示"已停用"。
    r = c.patch(
        "/v1/employees/editor", json={"employment_status": "disabled"}, headers=headers
    )
    assert r.status_code == 200, r.text
    _restart()
    entry = _entry(c, headers, "editor")
    assert entry["employment_status"] == "disabled"
    assert entry["status"] == "disabled"

    # 重新启用 → 重启后回到"空闲"。
    r = c.patch(
        "/v1/employees/editor", json={"employment_status": "active"}, headers=headers
    )
    assert r.status_code == 200, r.text
    _restart()
    entry = _entry(c, headers, "editor")
    assert entry["employment_status"] == "active"
    assert entry["status"] == "idle"

    # 删除 → 重启后不再出现。
    r = c.delete("/v1/employees/editor", headers=headers)
    assert r.status_code == 200, r.text
    _restart()
    assert "editor" not in _names(c, headers)


# ─── 2. 批量雇佣 ─────────────────────────────────────────────


def test_batch_hire_reports_each_item_and_persists(client):
    c = client
    headers = _headers("solo")
    c.post(
        "/v1/employees",
        json={"name": "already", "agent_count": 1, "agent_types": ["general"]},
        headers=headers,
    )

    r = c.post(
        "/v1/employees/batch",
        json={
            "employees": [
                {"name": "batch-a", "agent_count": 1, "agent_types": ["general"]},
                {"name": "batch-b", "agent_count": 2,
                 "agent_types": ["planner", "executor"], "role_description": "做调研"},
                {"name": "already", "agent_count": 1, "agent_types": ["general"]},
                {"name": "bad-cap", "default_capabilities": ["no-such-capability"]},
            ]
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["created_count"] == 2
    assert body["existing_count"] == 1
    assert body["failed_count"] == 1
    assert body["failed"][0]["name"] == "bad-cap"
    assert body["failed"][0]["code"]

    _restart()
    names = _names(c, headers)
    assert {"batch-a", "batch-b", "already"} <= names
    assert "bad-cap" not in names
    assert _entry(c, headers, "batch-b")["role_description"] == "做调研"


# ─── 3. 账号归属隔离 ──────────────────────────────────────────


def test_subaccounts_only_see_their_own_employees(client):
    c = client
    _make_tree()
    alice = _headers("alice")
    bob = _headers("bob")
    boss = _headers("boss")

    assert c.post(
        "/v1/employees",
        json={"name": "alice-emp", "agent_count": 1, "agent_types": ["general"]},
        headers=alice,
    ).status_code == 201
    assert c.post(
        "/v1/employees",
        json={"name": "bob-emp", "agent_count": 1, "agent_types": ["general"]},
        headers=bob,
    ).status_code == 201

    alice_names = _names(c, alice)
    bob_names = _names(c, bob)
    assert "alice-emp" in alice_names and "bob-emp" not in alice_names
    assert "bob-emp" in bob_names and "alice-emp" not in bob_names

    # 不能改/删别人的员工（与不存在同一 404，不暴露存在与否）。
    assert c.patch(
        "/v1/employees/alice-emp", json={"role_description": "越权"}, headers=bob
    ).status_code == 404
    assert c.delete("/v1/employees/alice-emp", headers=bob).status_code == 404
    assert c.get("/v1/employees/alice-emp/memories", headers=bob).status_code == 404

    # 主账号可见整棵子树，但只读。
    boss_names = _names(c, boss)
    assert {"alice-emp", "bob-emp"} <= boss_names
    boss_entry = _entry(c, boss, "alice-emp")
    assert boss_entry["read_only"] is True
    assert boss_entry["owner_account_id"] == "alice"


# ─── 4. 状态 / 最后一次活动 / 员工记忆归属 ────────────────────


def test_employee_status_last_activity_and_memory_ownership(client):
    c = client
    _make_tree()
    alice = _headers("alice")
    bob = _headers("bob")
    boss = _headers("boss")

    assert c.post(
        "/v1/employees",
        json={"name": "worker", "agent_count": 2,
              "agent_types": ["planner", "executor"], "role_description": "处理反馈"},
        headers=alice,
    ).status_code == 201

    mgr = AIStateManager()
    stub = _StubRuntime()
    mgr._runtime = stub

    r = c.post(
        "/v1/goals/assign",
        json={"employee_name": "worker", "instruction": "整理本周客户反馈",
              "background": False},
        headers=alice,
    )
    assert r.status_code in (200, 201), r.text
    assert r.json()["state"] == "completed"

    # 状态/最近活动：显示"已完成 + 做了什么 + 时间"，且不含内部标识。
    entry = _entry(c, alice, "worker")
    assert entry["status"] == "completed"
    assert entry["status_label"] == "已完成"
    activity = entry["last_activity"]
    assert activity and activity["description"] == "整理本周客户反馈"
    assert activity["at"], "必须带时间（什么时候）"
    # 人可读字段不含内部标识（agents[].id 是 pause/resume/派活要用的机器键，
    # 界面已不再渲染它）。
    human_facing = json.dumps(
        {
            "display_name": entry["display_name"],
            "status_label": entry["status_label"],
            "last_activity": activity,
        },
        ensure_ascii=False,
    )
    assert "worker-a0" not in human_facing
    assert "worker-planner" not in human_facing
    assert "planner-0" not in human_facing

    # 员工记忆归所属账号 alice。
    r = c.get("/v1/employees/worker/memories", headers=alice)
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["owner_account_id"] == "alice"
    assert payload["count"] >= 1
    assert "整理本周客户反馈" in payload["memories"][0]["text"]

    # 别的子账号不可见（404），主账号可见（只读汇总）。
    assert c.get("/v1/employees/worker/memories", headers=bob).status_code == 404
    r = c.get("/v1/employees/worker/memories", headers=boss)
    assert r.status_code == 200, r.text
    assert r.json()["count"] == payload["count"]

    # 重启后状态与最近活动仍在（不是内存假象）。
    _restart()
    entry = _entry(c, alice, "worker")
    assert entry["status"] == "completed"
    assert entry["last_activity"]["description"] == "整理本周客户反馈"
    assert entry["stats"]["total_goals_completed"] == 1


# ─── 5. 同一员工并发派活：固定拒绝，不丢任务不串结果 ──────────


def test_concurrent_assignment_to_same_employee_is_rejected(client):
    c = client
    _make_tree()
    alice = _headers("alice")
    assert c.post(
        "/v1/employees",
        json={"name": "solo-worker", "agent_count": 1, "agent_types": ["general"]},
        headers=alice,
    ).status_code == 201

    mgr = AIStateManager()
    stub = _BlockingRuntime()
    mgr._runtime = stub

    first = c.post(
        "/v1/goals/assign",
        json={"employee_name": "solo-worker", "instruction": "第一件事",
              "background": True},
        headers=alice,
    )
    assert first.status_code in (200, 201), first.text
    first_goal = first.json()["goal_id"]
    assert stub.entered.wait(timeout=30), "第一个目标没有开始执行"

    # 同一员工的第二个任务：固定 409，不排队、不执行、不串结果。
    second = c.post(
        "/v1/goals/assign",
        json={"employee_name": "solo-worker", "instruction": "第二件事",
              "background": True},
        headers=alice,
    )
    assert second.status_code == 409, second.text
    assert "正在执行" in second.json()["detail"]
    assert stub.entered_count == 1, "被拒绝的任务绝不能被执行"

    stub.release.set()
    deadline = time.time() + 30
    while time.time() < deadline:
        r = c.get(f"/v1/goals/{first_goal}", headers=alice)
        if r.status_code == 200 and r.json().get("state") != "running":
            break
        time.sleep(0.05)
    else:  # pragma: no cover - 超时才算失败
        raise AssertionError("第一个目标没有在超时内结束")

    # 释放后再次派活成功：前一个结果没有被覆盖/串号。
    third = c.post(
        "/v1/goals/assign",
        json={"employee_name": "solo-worker", "instruction": "第三件事",
              "background": False},
        headers=alice,
    )
    assert third.status_code in (200, 201), third.text
    assert third.json()["goal_id"] != first_goal
    assert len(stub.calls) == 2
    assert stub.entered_count == 2
    entry = _entry(c, alice, "solo-worker")
    assert entry["last_activity"]["description"] == "第三件事"


# ─── 6. 20 并发混合请求 × 10 轮：0 丢写 ─────────────────────


def test_twenty_concurrent_mixed_requests_ten_rounds(client):
    """20 线程并发做 创建→改名→指派→删除，重复 10 轮，一条都不能丢。"""
    from concurrent.futures import ThreadPoolExecutor

    c = client
    headers = _headers("solo")
    AIStateManager()._runtime = _StubRuntime()

    for round_no in range(10):
        # 目标接口有分钟级限流；本测试验证的是并发写入正确性，不是限流，
        # 每轮重置桶以免 200 次请求被 429 打断。
        import src.gateway.rate_limiter as rate_limiter_module

        rate_limiter_module._default_limiter = None

        def flow(index: int) -> tuple:
            name = f"r{round_no}-emp{index}"
            statuses = []
            r = c.post(
                "/v1/employees",
                json={"name": name, "agent_count": 1, "agent_types": ["general"]},
                headers=headers,
            )
            statuses.append(("create", r.status_code))
            r = c.patch(
                f"/v1/employees/{name}",
                json={"role_description": f"第 {index} 号"},
                headers=headers,
            )
            statuses.append(("rename", r.status_code))
            r = c.post(
                "/v1/goals/assign",
                json={"employee_name": name, "instruction": f"任务 {index}",
                      "background": False},
                headers=headers,
            )
            statuses.append(("assign", r.status_code))
            r = c.delete(f"/v1/employees/{name}", headers=headers)
            statuses.append(("delete", r.status_code))
            return statuses

        with ThreadPoolExecutor(max_workers=20) as pool:
            results = list(pool.map(flow, range(20)))
        for statuses in results:
            for action, code in statuses:
                assert code in (200, 201), f"round {round_no} {action} -> {code}"

        remaining = _names(c, headers)
        leaked = {f"r{round_no}-emp{i}" for i in range(20)} & remaining
        assert leaked == set(), f"round {round_no} 有员工没删掉：{leaked}"

