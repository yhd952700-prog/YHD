"""Owner（超级管理员）权限模型回归。

覆盖 F 组契约（与 ``tests/gateway/test_account_scope_three_layers.py`` 同构的
真实 HTTP 环境：独立临时数据目录 + 真实 FastAPI 应用）：

1. 首次注册的账号就是 Owner（``GET /v1/owner/me`` 的 ``origin`` 为
   ``first_registration``；登录响应与 ``/v1/auth/me`` 带 ``is_owner``）；
2. Owner 经 ``POST /v1/owner/members`` 邀请普通用户（默认最小权限），
   普通用户激活后能登录；
3. 普通用户调任何 Owner 接口一律 403（不是 200 空数据，也不泄露他人数据）；
4. 普通用户不能改 Owner（权限/状态），Owner 不能改自己（403 OWNER_PROTECTED）；
5. 跨用户任务隔离：Owner 的全部任务里能看到成员的目标；成员自己的
   ``GET /v1/goals`` 里看不到 Owner 的目标；
6. Owner 能改成员权限、停用/恢复成员，改动进审计且 Owner 能查到。
"""
from __future__ import annotations

from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient

PASSWORD = "owner-test-password-1"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    base = tmp_path
    values = {
        "LIUHAO_HUMAN_IDENTITIES_BACKEND": "file",
        "LIUHAO_HUMAN_IDENTITIES_FILE": str(base / "identities.json"),
        "LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY": "owner-model-test-key",
        "LIUHAO_AUTH_SECRETS_FILE": str(base / "auth_secrets.json"),
        "LIUHAO_ACCOUNT_SETTINGS_FILE": str(base / "account_settings.json"),
        "LIUHAO_ACCOUNT_MEMORY_DB": str(base / "account_memory.sqlite3"),
        "LIUHAO_ACCOUNT_USAGE_DB": str(base / "account_usage.sqlite3"),
        "LIUHAO_ALERTS_STORE_PATH": str(base / "alerts.json"),
        "AUDIT_DB_PATH": str(base / "audit_store.db"),
        "AUDIT_DB_BACKUP_PATH": str(base / "audit_store.db.backup"),
        "LIUHAO_WORKSPACE_ROOT": str(base / "workspace"),
        "LIUHAO_MODEL_CONFIG_DB": str(base / "model_config.sqlite3"),
        "LIUHAO_MODEL_CONFIG_KEY_FILE": str(base / "model_config_key.bin"),
        "LIUHAO_JWT_REVOCATION_DB": str(base / "jwt_revocation.db"),
        "LIUHAO_DATA_DIR": str(base / "data"),
        "LIUHAO_ACCOUNT_MAX_DEPTH": "3",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)

    import src.ai.conversation_store as conversation_store
    import src.kernels.identity as identity_module
    from src.ai.model_config import reset_model_config_store
    from src.gateway.ai_management import AIStateManager
    from src.gateway.projects import ProjectStore

    monkeypatch.setattr(identity_module, "_global_manager", None)
    monkeypatch.setattr(AIStateManager, "_instance", None)
    monkeypatch.setattr(ProjectStore, "_instance", None)
    monkeypatch.setattr(
        conversation_store,
        "_conversation_store",
        conversation_store.ConversationStore(str(base / "conversation_store.db")),
    )
    reset_model_config_store()
    yield values
    reset_model_config_store()


@pytest.fixture()
def client(env):
    from src.gateway.main import get_app

    with TestClient(get_app()) as test_client:
        yield test_client


def _bearer(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _register_root(client: TestClient, principal: str = "owner1") -> Dict[str, str]:
    response = client.post(
        "/v1/accounts/register",
        json={"principal": principal, "password": PASSWORD},
    )
    assert response.status_code == 200, response.text
    login = client.post(
        "/v1/auth/login", json={"principal": principal, "secret": PASSWORD}
    )
    assert login.status_code == 200, login.text
    assert login.json()["is_owner"] is True
    assert login.json()["is_root"] is True
    return _bearer(login.json()["access_token"])


def _login(client: TestClient, principal: str) -> Dict[str, str]:
    response = client.post(
        "/v1/auth/login", json={"principal": principal, "secret": PASSWORD}
    )
    assert response.status_code == 200, response.text
    return _bearer(response.json()["access_token"])


def _invite_member(client: TestClient, owner_headers: Dict[str, str], principal: str) -> Dict[str, str]:
    """Owner 邀请成员并激活，返回该成员的登录头。"""
    created = client.post(
        "/v1/owner/members",
        json={"principal": principal, "role": "viewer"},
        headers=owner_headers,
    )
    assert created.status_code == 201, created.text
    assert created.json()["account"]["principal"] == principal
    assert created.json()["account"]["is_owner"] is False
    code = created.json()["activation_code"]
    assert code
    activated = client.post(
        "/v1/accounts/activate",
        json={"principal": principal, "code": code, "password": PASSWORD},
    )
    assert activated.status_code == 200, activated.text
    return _login(client, principal)


def _error_code(response) -> str | None:
    body = response.json()
    if not isinstance(body, dict):
        return None
    if isinstance(body.get("code"), str):
        return body["code"]
    detail = body.get("detail")
    if isinstance(detail, dict) and isinstance(detail.get("code"), str):
        return detail["code"]
    return None


@pytest.fixture()
def pair(client):
    """Owner + 一名普通成员（member1）。"""
    owner = _register_root(client)
    member = _invite_member(client, owner, "member1")
    return {"owner": owner, "member": member}


def test_first_registered_account_is_owner(client):
    owner = _register_root(client)
    me = client.get("/v1/owner/me", headers=owner)
    assert me.status_code == 200, me.text
    body = me.json()
    assert body["is_owner"] is True
    assert body["owner_principal"] == "owner1"
    assert body["origin"] == "first_registration"
    assert body["member_count"] == 0
    assert body["migration_notes"] == []

    auth_me = client.get("/v1/auth/me", headers=owner)
    assert auth_me.status_code == 200, auth_me.text
    assert auth_me.json()["is_owner"] is True
    assert auth_me.json()["is_root"] is True


def test_owner_overview_and_members_empty_state(client):
    owner = _register_root(client)
    overview = client.get("/v1/owner/overview", headers=owner)
    assert overview.status_code == 200, overview.text
    body = overview.json()
    assert body["members"]["total"] == 1  # 只有 Owner 本人
    assert body["tasks"]["goals"] == 0
    assert body["tasks"]["tasks"] == 0
    assert body["audit"]["checked"] is True
    assert body["audit"]["events"] is not None and body["audit"]["events"] >= 0

    members = client.get("/v1/owner/members", headers=owner)
    assert members.status_code == 200, members.text
    assert members.json()["count"] == 1
    assert members.json()["members"][0]["principal"] == "owner1"
    assert members.json()["members"][0]["is_owner"] is True
    # 结构字段不外泄。
    assert "parent_id" not in members.json()["members"][0]
    assert "depth" not in members.json()["members"][0]
    assert "root_id" not in members.json()["members"][0]

    tasks = client.get("/v1/owner/tasks", headers=owner)
    assert tasks.status_code == 200, tasks.text
    assert tasks.json()["goals"] == []
    assert tasks.json()["task_count"] == 0


def test_member_is_denied_from_every_owner_endpoint(client, pair):
    member = pair["member"]
    for method, path in [
        ("GET", "/v1/owner/me"),
        ("GET", "/v1/owner/overview"),
        ("GET", "/v1/owner/members"),
        ("GET", "/v1/owner/tasks"),
        ("GET", "/v1/owner/audit"),
    ]:
        response = client.request(method, path, headers=member)
        assert response.status_code == 403, (method, path, response.text)
        assert _error_code(response) == "OWNER_ACCESS_DENIED"
        # 403 里不能出现别人的任何数据。
        assert "owner1" not in response.text

    denied_invite = client.post(
        "/v1/owner/members", json={"principal": "sneaky", "role": "viewer"}, headers=member
    )
    assert denied_invite.status_code == 403
    assert _error_code(denied_invite) == "OWNER_ACCESS_DENIED"

    denied_perm = client.patch(
        "/v1/owner/members/owner1/permissions",
        json={"grant": ["account:grant"], "revoke": []},
        headers=member,
    )
    assert denied_perm.status_code == 403
    assert _error_code(denied_perm) == "OWNER_ACCESS_DENIED"

    denied_status = client.patch(
        "/v1/owner/members/owner1/status",
        json={"status": "suspended", "reason": ""},
        headers=member,
    )
    assert denied_status.status_code == 403
    assert _error_code(denied_status) == "OWNER_ACCESS_DENIED"


def test_member_cannot_modify_owner_and_owner_cannot_modify_self(client, pair):
    owner, member = pair["owner"], pair["member"]

    # 普通用户改 Owner 权限：连 Owner 门禁都过不了（403，不是 404/200）。
    response = client.patch(
        "/v1/owner/members/owner1/permissions",
        json={"grant": [], "revoke": ["account:grant"]},
        headers=member,
    )
    assert response.status_code == 403

    # Owner 改自己权限/状态：403 OWNER_PROTECTED（防自降级）。
    response = client.patch(
        "/v1/owner/members/owner1/permissions",
        json={"grant": [], "revoke": ["account:grant"]},
        headers=owner,
    )
    assert response.status_code == 403, response.text
    assert _error_code(response) == "OWNER_PROTECTED"

    response = client.patch(
        "/v1/owner/members/owner1/status",
        json={"status": "suspended", "reason": ""},
        headers=owner,
    )
    assert response.status_code == 403, response.text
    assert _error_code(response) == "OWNER_PROTECTED"

    # Owner 本人改完仍能登录：没有被自己停用。
    assert client.post(
        "/v1/auth/login", json={"principal": "owner1", "secret": PASSWORD}
    ).status_code == 200


def test_owner_can_change_member_permissions_and_status(client, pair):
    owner, member = pair["owner"], pair["member"]

    updated = client.patch(
        "/v1/owner/members/member1/permissions",
        json={"grant": ["account:manage_children"], "revoke": []},
        headers=owner,
    )
    assert updated.status_code == 200, updated.text
    assert "account:manage_children" in updated.json()["account"]["permissions"]

    suspended = client.patch(
        "/v1/owner/members/member1/status",
        json={"status": "suspended", "reason": "test"},
        headers=owner,
    )
    assert suspended.status_code == 200, suspended.text
    # 被停用的成员登录即 403。
    denied_login = client.post(
        "/v1/auth/login", json={"principal": "member1", "secret": PASSWORD}
    )
    assert denied_login.status_code == 403, denied_login.text

    restored = client.patch(
        "/v1/owner/members/member1/status",
        json={"status": "active", "reason": ""},
        headers=owner,
    )
    assert restored.status_code == 200, restored.text
    assert client.post(
        "/v1/auth/login", json={"principal": "member1", "secret": PASSWORD}
    ).status_code == 200


def test_owner_sees_all_tasks_member_sees_only_own(client, pair):
    from src.gateway.ai_management import AIStateManager

    owner, member = pair["owner"], pair["member"]
    manager = AIStateManager()
    with manager._goal_lock:
        manager._goals["goal-owner"] = {
            "goal_id": "goal-owner",
            "owner_account_id": "owner1",
            "state": "completed",
            "natural_language": "Owner 的目标",
            "created_at": 1000.0,
            "tasks": [
                {"task_id": "t1", "status": "completed", "assigned_employee": "小李"}
            ],
        }
        manager._goals["goal-member"] = {
            "goal_id": "goal-member",
            "owner_account_id": "member1",
            "state": "running",
            "natural_language": "成员的目标",
            "created_at": 1001.0,
            "tasks": [{"task_id": "t2", "status": "running"}],
        }

    owner_tasks = client.get("/v1/owner/tasks", headers=owner)
    assert owner_tasks.status_code == 200, owner_tasks.text
    body = owner_tasks.json()
    assert body["goal_count"] == 2
    assert body["task_count"] == 2
    assert body["owners"] == ["member1", "owner1"]
    by_id = {goal["goal_id"]: goal for goal in body["goals"]}
    assert by_id["goal-member"]["owner_account_id"] == "member1"
    assert by_id["goal-owner"]["tasks"][0]["assignees"] == ["小李"]

    # 成员自己的目标列表里只有自己的目标：Owner 的目标连计数都不进入。
    member_goals = client.get("/v1/goals", headers=member)
    assert member_goals.status_code == 200, member_goals.text
    assert [g["goal_id"] for g in member_goals.json()["goals"]] == ["goal-member"]

    # 成员调 Owner 全部任务接口仍是 403（不是"只返回自己的"那种 200）。
    denied = client.get("/v1/owner/tasks", headers=member)
    assert denied.status_code == 403


def test_owner_audit_lists_owner_writes(client, pair):
    owner = pair["owner"]
    response = client.get("/v1/owner/audit", headers=owner, params={"limit": 100})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["read_scope"]["mode"] == "subtree"
    actions = {event.get("action") for event in body["events"]}
    assert "owner.member.create" in actions
    assert "owner.access.denied" not in actions  # 本用例无人越权
    # 每条事件都有 actor，不含凭据类字段。
    for event in body["events"]:
        assert event.get("principal_id")
        assert "password" not in str(event.get("details", {}))


def test_owner_denial_is_audited(client, pair):
    owner, member = pair["owner"], pair["member"]
    denied = client.get("/v1/owner/overview", headers=member)
    assert denied.status_code == 403
    response = client.get(
        "/v1/owner/audit",
        headers=owner,
        params={"action": "owner.access.denied", "limit": 10},
    )
    assert response.status_code == 200, response.text
    events = response.json()["events"]
    assert len(events) >= 1
    assert events[0]["principal_id"] == "member1"
    assert events[0]["outcome"] == "denied"


def test_owner_reads_are_audited(client, pair):
    """Owner 看总览/成员/全部任务（"谁看了什么"）进审计；查审计本身不自追加。"""
    owner = pair["owner"]
    assert client.get("/v1/owner/overview", headers=owner).status_code == 200
    assert client.get("/v1/owner/members", headers=owner).status_code == 200
    assert client.get("/v1/owner/tasks", headers=owner).status_code == 200
    response = client.get("/v1/owner/audit", headers=owner, params={"limit": 100})
    assert response.status_code == 200, response.text
    actions = {event.get("action") for event in response.json()["events"]}
    assert "owner.overview.read" in actions
    assert "owner.members.read" in actions
    assert "owner.tasks.read" in actions
    # 查审计本身不写审计：反复查审计不会让事件数无限增长。
    first_count = response.json()["count"]
    again = client.get("/v1/owner/audit", headers=owner, params={"limit": 100})
    assert again.status_code == 200, again.text
    assert again.json()["count"] == first_count
