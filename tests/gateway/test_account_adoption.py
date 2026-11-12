"""根的"取用"能力：复制子树员工/技能与记忆到根自己的空间。

边界（刻意设计）：
* 只复制、不向子账号空间派活 —— 子账号侧看不到主账号的任何动作；
* 来源只回给根并写审计，成员侧员工列表 / 任务 / 用量都不出现痕迹。
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def isolated(monkeypatch, tmp_path):
    values = {
        "LIUHAO_WORKSPACE_ROOT": str(tmp_path / "workspace"),
        "LIUHAO_HUMAN_IDENTITIES_BACKEND": "file",
        "LIUHAO_HUMAN_IDENTITIES_FILE": str(tmp_path / "identities.json"),
        "LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY": "adoption-test-key",
        "LIUHAO_AUTH_SECRETS_FILE": str(tmp_path / "auth_secrets.json"),
        "LIUHAO_ACCOUNT_MEMORY_DB": str(tmp_path / "account_memory.sqlite3"),
        "LIUHAO_ACCOUNT_USAGE_DB": str(tmp_path / "account_usage.sqlite3"),
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    import src.kernels.identity as identity_module
    from src.gateway.ai_management import AIStateManager

    monkeypatch.setattr(identity_module, "_global_manager", None)
    # AIStateManager 是进程级单例：换实例必须复位，否则员工库还指向上一个
    # 临时目录（真实进程里路径在启动时固定，不存在这个问题）。
    monkeypatch.setattr(AIStateManager, "_instance", None)
    return tmp_path


def _viewer(principal: str, *, is_root: bool, subtree=()):
    from src.gateway.ai_management import GoalViewer

    if is_root:
        return GoalViewer(principal=principal, subtree=(principal,) + tuple(subtree), is_root=True)
    return GoalViewer(principal=principal, subtree=(principal,) + tuple(subtree))


def test_root_adopts_member_employee_into_own_space(isolated):
    from src.gateway.ai_management import AIStateManager

    root = _viewer("root1", is_root=True, subtree=("member1",))
    member = _viewer("member1", is_root=False)

    mgr = AIStateManager()
    mgr.hire_employee(
        "member-emp",
        agent_count=2,
        agent_types=["planner", "executor"],
        role_description="负责整理成员日报",
        default_capabilities=[],
        owner_account_id="member1",
    )

    adopted = mgr.adopt_employee("member-emp", root, as_name="member-emp-copy")
    assert adopted["created"] is True
    employee = adopted["employee"]
    assert employee["role_description"] == "负责整理成员日报"
    assert employee["owner_account_id"] == "root1"
    assert employee["adopted_from"] == {
        "account_id": "member1",
        "employee_name": "member-emp",
    }
    # 技能（agent 类型）也一起复制。
    assert {agent["agent_type"] for agent in employee["agents"]} == {"planner", "executor"}

    # 根能直接指派这份副本（任务在根空间执行）。
    resolved = mgr.resolve_employee_agent("member-emp-copy", root)
    assert resolved[0].name == "member-emp-copy"
    assert resolved[0].owner_account_id == "root1"

    # 子账号侧：员工列表不出现副本，源员工原样保留。
    member_names = {row["name"] for row in mgr.list_all_employees(member)}
    assert "member-emp-copy" not in member_names
    assert "member-emp" in member_names


def test_member_cannot_adopt_and_duplicate_is_refused(isolated):
    from src.gateway.ai_management import AIStateManager, GoalWorkError

    root = _viewer("root1", is_root=True, subtree=("member1",))
    member = _viewer("member1", is_root=False)

    mgr = AIStateManager()
    mgr.hire_employee("member-emp", role_description="x", owner_account_id="member1")

    with pytest.raises(GoalWorkError) as denied:
        mgr.adopt_employee("member-emp", member)
    assert denied.value.status_code == 404

    mgr.adopt_employee("member-emp", root, as_name="member-emp-copy")
    with pytest.raises(GoalWorkError) as conflict:
        mgr.adopt_employee("member-emp", root, as_name="member-emp-copy")
    assert conflict.value.status_code == 409


def test_root_can_adopt_member_memory_without_touching_source(isolated):
    from src.gateway.account_context import AccountContext
    from src.gateway.account_memory import (
        MemoryAdoptRequest,
        MemoryWriteRequest,
        adopt_entries,
        write_entry,
    )
    from src.kernels.identity import _accounts

    root_node = _accounts.AccountNode(
        principal="root1",
        parent_id=None,
        depth=0,
        root_id="root1",
        permissions=frozenset({"*"}),
        source="account",
    )
    root_ctx = AccountContext(
        principal="root1", node=root_node, subtree=("root1", "member1")
    )
    member_node = _accounts.AccountNode(
        principal="member1",
        parent_id="root1",
        depth=1,
        root_id="root1",
        permissions=frozenset(),
        source="account",
    )
    member_ctx = AccountContext(
        principal="member1", node=member_node, subtree=("member1",)
    )

    written = write_entry(
        MemoryWriteRequest(text="成员偏好：先给结论", tags=["偏好"]), member_ctx
    )
    original_id = written["entry"]["id"]

    report = adopt_entries(MemoryAdoptRequest(), root_ctx)
    assert report == {"imported": 1, "skipped": 0, "accounts": ["member1"]}

    from src.gateway.account_memory import _query_entries

    root_entries = _query_entries(("root1",), "结论")
    assert len(root_entries) == 1
    adopted = root_entries[0]
    assert adopted["text"] == "成员偏好：先给结论"
    assert adopted["source"] == "adopt:member1"
    assert f"origin:{original_id}" in adopted["tags"]
    assert "from:member1" in adopted["tags"]

    # 源数据一条不少、一个字段不改；重复取用幂等。
    member_entries = _query_entries(("member1",), "")
    assert [entry["id"] for entry in member_entries] == [original_id]
    assert member_entries[0]["tags"] == ["偏好"]
    assert adopt_entries(MemoryAdoptRequest(), root_ctx) == {
        "imported": 0,
        "skipped": 1,
        "accounts": ["member1"],
    }

    # 非根不能取用。
    with pytest.raises(Exception) as denied:
        adopt_entries(MemoryAdoptRequest(account_id="root1"), member_ctx)
    assert getattr(denied.value, "status_code", 0) == 404
