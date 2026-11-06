"""AI 员工实体（最小可用）回归。

覆盖要求逐条：

1. 创建 / 列表 / 改名 / 停用 / 删除；字段（名字、职责、默认能力、owner、状态）
   真实持久化，重启后仍在。
2. 按 owner 过滤：别人的员工一律 404（与"不存在"文案相同）；主账号可看子树
   员工但只能读；子账号之间互不可见。
3. 指名派活：``POST /v1/goals`` 的 ``employee_name`` 与
   ``POST /v1/goals/assign`` 的 ``employee_name`` 都能把任务钉给该员工。
4. 结果里可见员工归属：``assigned_employee`` / ``duration_ms`` / ``success``。
5. 单员工失败不影响其他员工。

执行层是真实 ``AgentRuntime``（离线确定性拆解器），因此任务归属与成败都来自
真实执行记录，不是 stub 造出来的。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.gateway.ai_management import AIStateManager
from src.gateway.main import get_app


def _headers(subject: str) -> dict:
    """用网关同一套 JWT 签发器铸造真实令牌（sub = 账号名）。"""
    from src.security import get_jwt_handler

    token, _payload = get_jwt_handler().create_token(
        subject=subject, metadata={"kind": "human", "scope": "L0"}
    )
    return {"Authorization": f"Bearer {token}"}


def _make_tree(root: str = "boss", members=("alice", "bob")) -> None:
    """在身份内核里建一棵真实账号树：root 为主账号，members 为其下级。"""
    from src.kernels.identity import get_identity_manager

    manager = get_identity_manager()
    manager.register_root_account(root)
    for member in members:
        manager.create_member_account(actor=root, principal=member)


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("LIUHAO_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.delenv("LIUHAO_DEFAULT_ACCOUNT_ID", raising=False)
    AIStateManager._instance = None
    yield
    AIStateManager._instance = None


@pytest.fixture
def client():
    return TestClient(get_app())


def _hire(client, headers, name: str, **extra) -> dict:
    payload = {"name": name, "agent_count": 1, "agent_types": ["general"], **extra}
    resp = client.post("/v1/employees", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _employees(client, headers) -> dict:
    resp = client.get("/v1/employees", headers=headers)
    assert resp.status_code == 200, resp.text
    return {item["name"]: item for item in resp.json()["employees"]}


def _assign(client, headers, **payload) -> object:
    return client.post(
        "/v1/goals/assign",
        json={"instruction": "python: result = 1+1", "background": False, **payload},
        headers=headers,
    )


# ─── 1. 创建 / 字段 / 持久化 ───────────────────────────────────

def test_create_employee_with_real_fields_and_list(client):
    headers = _headers("test-human")
    caps = client.get("/v1/employees/capabilities", headers=headers)
    assert caps.status_code == 200, caps.text
    assert "python_compute" in caps.json()["ids"]

    body = _hire(
        client,
        headers,
        "analyst",
        role_description="负责数据清洗与计算",
        default_capabilities=["python_compute"],
    )
    assert body["role_description"] == "负责数据清洗与计算"
    assert body["default_capabilities"] == ["python_compute"]
    assert body["employment_status"] == "active"
    assert body["owner_account_id"] == "test-human"
    assert [a["id"] for a in body["agents"]] == ["analyst-a0"]

    listed = _employees(client, headers)
    assert "analyst" in listed
    assert listed["analyst"]["role_description"] == "负责数据清洗与计算"


def test_unknown_capability_is_rejected(client):
    headers = _headers("test-human")
    resp = client.post(
        "/v1/employees",
        json={"name": "ghost", "default_capabilities": ["teleport"]},
        headers=headers,
    )
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    message = detail["message"] if isinstance(detail, dict) else str(detail)
    assert "teleport" in message


def test_employee_survives_restart(client):
    headers = _headers("test-human")
    _hire(client, headers, "writer", role_description="负责写文件")

    # 模拟进程重启：丢掉内存里的单例，再重新走一次真实落盘读取。
    AIStateManager._instance = None
    restarted = TestClient(get_app())
    listed = _employees(restarted, headers)
    assert "writer" in listed
    assert listed["writer"]["role_description"] == "负责写文件"
    assert listed["writer"]["employment_status"] == "active"


# ─── 2. 改名 / 停用 / 删除 ────────────────────────────────────

def test_rename_employee_keeps_agent_ids_stable(client):
    headers = _headers("test-human")
    _hire(client, headers, "analyst")

    resp = client.patch(
        "/v1/employees/analyst", json={"name": "senior-analyst"}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["renamed"] is True
    assert resp.json()["employee"]["name"] == "senior-analyst"
    # agent id 不跟着改：历史任务的归属仍然能解析到这位员工。
    assert [a["id"] for a in resp.json()["employee"]["agents"]] == ["analyst-a0"]

    listed = _employees(client, headers)
    assert "analyst" not in listed
    assert "senior-analyst" in listed

    # 改名后仍然可以用新名字派活。
    assigned = _assign(client, headers, employee_name="senior-analyst")
    assert assigned.status_code == 201, assigned.text
    assert assigned.json()["assigned_employee"] == "senior-analyst"


def test_disable_blocks_assignment_until_enabled(client):
    headers = _headers("test-human")
    _hire(client, headers, "analyst")

    ok = _assign(client, headers, employee_name="analyst")
    assert ok.status_code == 201, ok.text

    resp = client.patch(
        "/v1/employees/analyst",
        json={"employment_status": "disabled"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["employee"]["employment_status"] == "disabled"

    blocked = _assign(client, headers, employee_name="analyst")
    assert blocked.status_code == 409, blocked.text
    payload = blocked.json()
    code = payload.get("code") or payload.get("detail", {}).get("code")
    assert code == "employee_disabled"

    resp = client.patch(
        "/v1/employees/analyst",
        json={"employment_status": "active"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    resumed = _assign(client, headers, employee_name="analyst")
    assert resumed.status_code == 201, resumed.text


def test_delete_employee_and_seed_protection(client):
    headers = _headers("test-human")
    _hire(client, headers, "temp")

    assert client.delete("/v1/employees/temp", headers=headers).status_code == 200
    assert "temp" not in _employees(client, headers)
    assert client.delete("/v1/employees/temp", headers=headers).status_code == 404

    # seed 默认员工必须受保护，否则网关自身会坏掉。
    resp = client.delete("/v1/employees/liuhao-default", headers=headers)
    assert resp.status_code == 403, resp.text


# ─── 3. 跨账号：404、主账号只读 ───────────────────────────────

def test_cross_owner_employees_are_invisible_and_404(client):
    _make_tree(root="boss", members=("alice", "bob"))
    alice = _headers("alice")
    bob = _headers("bob")
    boss = _headers("boss")

    _hire(client, alice, "alice-analyst")
    _hire(client, bob, "bob-analyst")
    _hire(client, boss, "boss-analyst")

    alice_view = _employees(client, alice)
    assert "alice-analyst" in alice_view
    assert "bob-analyst" not in alice_view
    assert "boss-analyst" not in alice_view

    # 别人的员工：读取/改动/删除/派活一律 404，且与"不存在"文案逐字相同。
    missing = client.patch(
        "/v1/employees/no-such-employee",
        json={"role_description": "x"},
        headers=bob,
    )
    foreign = client.patch(
        "/v1/employees/alice-analyst",
        json={"role_description": "x"},
        headers=bob,
    )
    assert foreign.status_code == 404
    assert missing.status_code == 404
    assert foreign.json()["detail"] == missing.json()["detail"]

    assert client.delete("/v1/employees/alice-analyst", headers=bob).status_code == 404
    assert _assign(client, bob, employee_name="alice-analyst").status_code == 404

    # 主账号可看整棵树的员工，但只能读：改/删别人的员工仍然 404。
    boss_view = _employees(client, boss)
    assert {"alice-analyst", "bob-analyst", "boss-analyst"} <= set(boss_view)
    assert boss_view["alice-analyst"]["read_only"] is True
    assert boss_view["boss-analyst"]["read_only"] is False
    assert client.patch(
        "/v1/employees/alice-analyst",
        json={"role_description": "root override"},
        headers=boss,
    ).status_code == 404
    assert client.delete("/v1/employees/alice-analyst", headers=boss).status_code == 404

    # 自己的员工仍然可改。
    assert client.patch(
        "/v1/employees/alice-analyst",
        json={"role_description": "数据清洗"},
        headers=alice,
    ).status_code == 200


# ─── 4. 指名派活 + 结果可见（谁做了什么 / 耗时 / 成败） ──────────

def test_named_employee_via_goals_pins_every_task(client):
    headers = _headers("test-human")
    _hire(client, headers, "coder")

    resp = client.post(
        "/v1/goals",
        json={
            "natural_language": "python: result = 1+1",
            "employee_name": "coder",
            "background": False,
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    entry = resp.json()
    assert entry["assigned_employee"] == "coder"
    assert entry["tasks"], entry
    for task in entry["tasks"]:
        assert task["assigned_agent"] == "coder-a0"
        assert task["assigned_employee"] == "coder"
        assert task["success"] is True
        assert task["duration_ms"] is not None and task["duration_ms"] >= 0

    detail = client.get(f"/v1/goals/{entry['goal_id']}", headers=headers)
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["assigned_employee"] == "coder"
    assert body["employees"][0]["employee"] == "coder"
    assert body["employees"][0]["completed"] >= 1


def test_assign_endpoint_requires_exactly_one_target(client):
    headers = _headers("test-human")
    _hire(client, headers, "coder")

    both = _assign(client, headers, employee_name="coder", agent_id="coder-a0")
    assert both.status_code == 400, both.text

    neither = _assign(client, headers)
    assert neither.status_code == 400, neither.text

    # 单独指派：产物/结果归该员工，也归发起账号。
    ok = _assign(client, headers, employee_name="coder")
    assert ok.status_code == 201, ok.text
    body = ok.json()
    assert body["assigned_employee"] == "coder"
    assert body["owner_account_id"] == "test-human"


def test_one_employee_failure_does_not_affect_others(client):
    headers = _headers("test-human")
    _hire(client, headers, "steady")
    _hire(client, headers, "fragile")

    good = _assign(client, headers, employee_name="steady")
    assert good.status_code == 201, good.text
    assert good.json()["state"] in ("completed", "completed_with_errors")

    bad = _assign(
        client, headers, employee_name="fragile",
        instruction="python: result = 1/0",
    )
    assert bad.status_code == 201, bad.text
    bad_body = bad.json()
    assert bad_body["assigned_employee"] == "fragile"
    assert any(t.get("success") is False for t in bad_body["tasks"])

    # 另一个员工不受影响：再派一次仍然成功。
    again = _assign(client, headers, employee_name="steady")
    assert again.status_code == 201, again.text
    assert all(t.get("success") for t in again.json()["tasks"])
