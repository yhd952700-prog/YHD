"""三层账号树（主 → 子 → 孙）与资源作用域的真实 HTTP 回归。

覆盖派单里的 5 项硬要求：

1. 员工 / 模型 / 目标 / 记忆 / 会话 / 工件逐类按子树收敛（根看整棵子树；
   成员看自己可见范围；孙账号只有自己）；
2. 兄弟 / 父级 / 树外访问一律拒绝，且拒绝体与"资源不存在"逐字一致；
3. Key 作用域：根可用自己的 Key，也可（在成员开启共享后）使用子账号的 Key；
   成员只能用自己的 Key；借用记在借用者账上，不记成 Key 主人的"自己的用量"；
4. 接口、界面字段与错误文案不出现"创建者 / 上级账号 / 父级 / 层级"等结构词；
5. 同一账号树下并发建子账号、并发改权限不产生半成品（身份已建但节点缺失、
   或权限集被后写覆盖）。

所有用例使用独立临时数据目录 + 真实 FastAPI 应用；不触碰真实用户数据。
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Iterable, List

import pytest
from fastapi.testclient import TestClient

PASSWORD = "scopepass123"

#: 结构字段：接口返回里出现任何一个都算泄露层级/创建关系。
_STRUCT_FIELDS = (
    "parent_id",
    "parent",
    "depth",
    "root_id",
    "created_by",
    "creator",
    "created_by_principal",
    "superior",
    "upstream",
)

#: 用户可见文案里的层级词（与 display.ACCOUNT_HIERARCHY_WORDS 同源）。
_USER_TEXT_KEYS = ("detail", "notice", "message", "user_message", "hint")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """把身份库 / 凭据 / 模型 / 记忆 / 用量 / 会话全部落到临时目录。"""
    base = tmp_path
    values = {
        "LIUHAO_HUMAN_IDENTITIES_BACKEND": "file",
        "LIUHAO_HUMAN_IDENTITIES_FILE": str(base / "identities.json"),
        "LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY": "account-scope-three-layers-key",
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


# --------------------------------------------------------------------------- #
# 树与请求辅助
# --------------------------------------------------------------------------- #


def _bearer(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _register_root(client: TestClient, principal: str = "root1") -> Dict[str, str]:
    response = client.post(
        "/v1/accounts/register",
        json={"principal": principal, "password": PASSWORD},
    )
    assert response.status_code == 200, response.text
    return _login(client, principal)


def _login(client: TestClient, principal: str) -> Dict[str, str]:
    response = client.post(
        "/v1/auth/login", json={"principal": principal, "secret": PASSWORD}
    )
    assert response.status_code == 200, response.text
    return _bearer(response.json()["access_token"])


def _create_child(
    client: TestClient,
    headers: Dict[str, str],
    principal: str,
    *,
    permissions: Iterable[str] | None = None,
) -> Dict[str, str]:
    body: Dict[str, Any] = {"principal": principal}
    if permissions is not None:
        body["permissions"] = list(permissions)
    created = client.post("/v1/accounts", json=body, headers=headers)
    assert created.status_code == 200, created.text
    code = created.json()["activation_code"]
    activated = client.post(
        "/v1/accounts/activate",
        json={"principal": principal, "code": code, "password": PASSWORD},
    )
    assert activated.status_code == 200, activated.text
    token = activated.json().get("access_token") or activated.json().get("token")
    assert token
    return _bearer(token)


class Tree:
    """root1 → (child1, child2)，child1 → grand1 → great1。

    第 4 层 ``great1`` 只为验证"子树内但非直接下级 = 越级 403"而存在；
    其余用例不给它创建任何资源，因此不影响各自的集合断言。
    """

    def __init__(self, client: TestClient):
        from src.kernels.identity._accounts import DEFAULT_MEMBER_SCOPES

        self.client = client
        self.root = _register_root(client)
        self.child1 = _create_child(
            client, self.root, "child1", permissions=DEFAULT_MEMBER_SCOPES
        )
        self.child2 = _create_child(
            client, self.root, "child2", permissions=DEFAULT_MEMBER_SCOPES
        )
        self.grand1 = _create_child(
            client, self.child1, "grand1", permissions=DEFAULT_MEMBER_SCOPES
        )
        self.great1 = _create_child(
            client, self.grand1, "great1", permissions=DEFAULT_MEMBER_SCOPES
        )
        # 无任何作用域的叶子：验证"没有建下级权限"的拒绝路径。
        self.leaf = _create_child(client, self.grand1, "leaf", permissions=[])


@pytest.fixture()
def tree(client):
    return Tree(client)


# --------------------------------------------------------------------------- #
# 断言辅助
# --------------------------------------------------------------------------- #


def _user_texts(value: Any) -> List[str]:
    texts: List[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in _USER_TEXT_KEYS and isinstance(item, str):
                texts.append(item)
            texts.extend(_user_texts(item))
    elif isinstance(value, list):
        for item in value:
            texts.extend(_user_texts(item))
    return texts


def _assert_no_hierarchy_leak(body: Any) -> None:
    from src.gateway.display import ACCOUNT_HIERARCHY_WORDS

    # 结构字段：只检查字典键（字符串值里出现 "parent" 可能只是普通文本）。
    def keys(value: Any) -> Iterable[str]:
        if isinstance(value, dict):
            for key, item in value.items():
                yield str(key)
                yield from keys(item)
        elif isinstance(value, list):
            for item in value:
                yield from keys(item)

    assert not [key for key in keys(body) if key in _STRUCT_FIELDS], body
    hits = [
        (word, text)
        for text in _user_texts(body)
        for word in ACCOUNT_HIERARCHY_WORDS
        if word in text
    ]
    assert not hits, f"用户可见文案出现层级词：{hits}"


def _detail_message(response) -> Any:
    body = response.json()
    detail = body.get("detail", body)
    if isinstance(detail, dict):
        return detail.get("message", detail)
    return detail


def _error_code(response) -> str | None:
    """稳定错误码；兼容 ``{"detail": {"code": …}}`` 与扁平 ``{"code": …}`` 两种错误体。"""
    body = response.json()
    if not isinstance(body, dict):
        return None
    if isinstance(body.get("code"), str):
        return body["code"]
    detail = body.get("detail")
    if isinstance(detail, dict) and isinstance(detail.get("code"), str):
        return detail["code"]
    return None


def _assert_neutral_404(response, *, missing=None) -> None:
    assert response.status_code == 404, response.text
    assert _detail_message(response) == "资源不存在或无权访问", response.text
    if missing is not None:
        assert response.json() == missing.json(), (
            response.text,
            missing.text,
        )


def _employee_names(client: TestClient, headers: Dict[str, str]) -> set[str]:
    response = client.get("/v1/employees", headers=headers)
    assert response.status_code == 200, response.text
    return {item["name"] for item in response.json()["employees"]}


def _goal_owners(client: TestClient, headers: Dict[str, str]) -> Dict[str, str]:
    response = client.get("/v1/goals", headers=headers)
    assert response.status_code == 200, response.text
    return {
        item["goal_id"]: item.get("owner_account_id")
        for item in response.json()["goals"]
    }


# --------------------------------------------------------------------------- #
# 1) 账号树本身
# --------------------------------------------------------------------------- #


def test_tree_lists_only_direct_children_and_hides_structure(tree, client):
    listed = client.get("/v1/accounts", headers=tree.root)
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert {m["principal"] for m in body["members"]} == {"child1", "child2"}
    assert body["count"] == 2
    _assert_no_hierarchy_leak(body)

    child_view = client.get("/v1/accounts", headers=tree.child1).json()
    assert {m["principal"] for m in child_view["members"]} == {"grand1"}
    _assert_no_hierarchy_leak(child_view)

    grand_view = client.get("/v1/accounts", headers=tree.grand1).json()
    assert {m["principal"] for m in grand_view["members"]} == {"great1", "leaf"}
    assert grand_view["count"] == 2

    leaf_view = client.get("/v1/accounts", headers=tree.great1).json()
    assert leaf_view["members"] == [] and leaf_view["count"] == 0

    whole = client.get("/v1/accounts/tree", headers=tree.root)
    assert whole.status_code == 200, whole.text
    _assert_no_hierarchy_leak(whole.json())
    flat = str(whole.json())
    assert "child1" in flat and "child2" in flat and "grand1" in flat


def test_cross_branch_account_access_is_neutral_404(tree, client):
    # 兄弟、父级：与不存在逐字一致，不暴露"存在但无权"。
    sibling = client.get("/v1/accounts/child2", headers=tree.child1)
    missing = client.get("/v1/accounts/no-such-account", headers=tree.child1)
    _assert_neutral_404(sibling, missing=missing)
    _assert_no_hierarchy_leak(sibling.json())

    parent = client.get("/v1/accounts/root1", headers=tree.child1)
    _assert_neutral_404(parent, missing=missing)

    # 孙账号看不到祖父 / 叔伯。
    for target in ("root1", "child2"):
        _assert_neutral_404(
            client.get(f"/v1/accounts/{target}", headers=tree.grand1),
            missing=missing,
        )

    # 子树内可读：child1 能读 grand1，root 能读任何成员。
    assert client.get("/v1/accounts/grand1", headers=tree.child1).status_code == 200
    assert client.get("/v1/accounts/grand1", headers=tree.root).status_code == 200

    # 写类：越界 404；子树内但非直接下级 403（越级）。
    _assert_neutral_404(
        client.patch(
            "/v1/accounts/child2/permissions",
            json={"grant": [], "revoke": []},
            headers=tree.child1,
        ),
        missing=client.patch(
            "/v1/accounts/no-such-account/permissions",
            json={"grant": [], "revoke": []},
            headers=tree.child1,
        ),
    )
    leapfrog = client.patch(
        "/v1/accounts/great1/permissions",
        json={"grant": [], "revoke": []},
        headers=tree.child1,
    )
    assert leapfrog.status_code == 403, leapfrog.text
    assert _error_code(leapfrog) == "ACCOUNT_LEAPFROG_DENIED"
    _assert_no_hierarchy_leak(leapfrog.json())

    # 直接下级仍可管理（不误伤）。
    direct = client.patch(
        "/v1/accounts/grand1/permissions",
        json={"grant": [], "revoke": []},
        headers=tree.child1,
    )
    assert direct.status_code == 400, direct.text  # 空变更，但已通过权限判定
    assert _error_code(direct) == "ACCOUNT_NO_CHANGE"

    # 无管理权限的叶子账号建下级：与"接口不存在"同为中性 404。
    assert tree.leaf
    _assert_neutral_404(
        client.post("/v1/accounts", json={"principal": "great2"}, headers=tree.leaf)
    )


# --------------------------------------------------------------------------- #
# 2) 员工 / 目标（GoalViewer 按子树收敛）
# --------------------------------------------------------------------------- #


def test_employee_scope_follows_subtree(tree, client):
    for name, headers in (
        ("emp-root", tree.root),
        ("emp-child1", tree.child1),
        ("emp-child2", tree.child2),
        ("emp-grand1", tree.grand1),
    ):
        created = client.post(
            "/v1/employees",
            json={"name": name, "agent_count": 1},
            headers=headers,
        )
        assert created.status_code == 201, (name, created.text)

    assert _employee_names(client, tree.root) == {
        "liuhao-default",  # 网关自带的 seed 员工，归属根账号
        "emp-root",
        "emp-child1",
        "emp-child2",
        "emp-grand1",
    }
    assert _employee_names(client, tree.child1) == {"emp-child1", "emp-grand1"}
    assert _employee_names(client, tree.child2) == {"emp-child2"}
    assert _employee_names(client, tree.grand1) == {"emp-grand1"}


def _seed_goal(owner: str, goal_id: str) -> None:
    from src.gateway.ai_management import AIStateManager

    manager = AIStateManager()
    with manager._goal_lock:
        manager._goals[goal_id] = {
            "goal_id": goal_id,
            "state": "completed",
            "natural_language": f"{owner} 的目标",
            "scope": "",
            "created_at": "2026-01-01T00:00:00+00:00",
            "owner_account_id": owner,
            "tasks": [],
            "artifacts": [],
        }


def test_goal_scope_follows_subtree_and_cross_reads_are_404(tree, client):
    for owner in ("root1", "child1", "child2", "grand1"):
        _seed_goal(owner, f"goal-{owner}")

    root_view = _goal_owners(client, tree.root)
    assert set(root_view) == {
        "goal-root1",
        "goal-child1",
        "goal-child2",
        "goal-grand1",
    }
    assert set(_goal_owners(client, tree.child1)) == {"goal-child1", "goal-grand1"}
    assert set(_goal_owners(client, tree.child2)) == {"goal-child2"}
    assert set(_goal_owners(client, tree.grand1)) == {"goal-grand1"}

    # 跨分支读详情：与不存在同为 404，错误码一致。
    foreign = client.get("/v1/goals/goal-child1", headers=tree.child2)
    missing = client.get("/v1/goals/no-such-goal", headers=tree.child2)
    assert foreign.status_code == missing.status_code == 404
    assert _error_code(foreign) == _error_code(missing)
    _assert_no_hierarchy_leak(foreign.json())


# --------------------------------------------------------------------------- #
# 3) 模型 Key 作用域
# --------------------------------------------------------------------------- #


def _create_model(
    client: TestClient,
    headers: Dict[str, str],
    *,
    name: str,
    owner: str | None = None,
) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "name": name,
        "provider": "mock",
        "model": f"mock-{name}",
        "timeout": 30.0,
    }
    if owner is not None:
        body["owner_principal"] = owner
    response = client.post("/v1/models", json=body, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def test_model_key_scope_and_borrowing(tree, client):
    child1_model = _create_model(client, tree.root, name="key-child1", owner="child1")
    child2_model = _create_model(client, tree.child2, name="key-child2")

    # 列表：各层只在自己的可见范围内。
    child1_ids = {m["id"] for m in client.get("/v1/models", headers=tree.child1).json()["models"]}
    child2_ids = {m["id"] for m in client.get("/v1/models", headers=tree.child2).json()["models"]}
    root_ids = {m["id"] for m in client.get("/v1/models", headers=tree.root).json()["models"]}
    grand_ids = {m["id"] for m in client.get("/v1/models", headers=tree.grand1).json()["models"]}

    assert child1_ids == {child1_model["id"]}
    assert child2_ids == {child2_model["id"]}
    assert {child1_model["id"], child2_model["id"]} <= root_ids
    assert grand_ids == set()

    # 成员不能用别人的 Key：兄弟的模型既不可读也不可改。
    foreign = client.get(f"/v1/models/{child2_model['id']}", headers=tree.child1)
    assert foreign.status_code == 403, foreign.text
    assert _error_code(foreign) == "ACCOUNT_KEY_NOT_OWNED"
    _assert_no_hierarchy_leak(foreign.json())

    # 成员不能把模型挂到别人名下。
    assign = client.post(
        "/v1/models",
        json={"name": "key-steal", "provider": "mock", "model": "m", "owner_principal": "child2"},
        headers=tree.child1,
    )
    assert assign.status_code == 403, assign.text
    assert _error_code(assign) == "ACCOUNT_KEY_NOT_OWNED"

    # 根：未开启共享时拿不到成员 Key；成员本人开启后可借用；再撤销即失效。
    before = client.get(f"/v1/models/{child1_model['id']}", headers=tree.root)
    assert before.status_code == 403, before.text

    shared = client.patch(
        "/v1/accounts/child1/key-sharing", json={"allow": True}, headers=tree.child1
    )
    assert shared.status_code == 200, shared.text
    after = client.get(f"/v1/models/{child1_model['id']}", headers=tree.root)
    assert after.status_code == 200, after.text

    revoked = client.patch(
        "/v1/accounts/child1/key-sharing", json={"allow": False}, headers=tree.child1
    )
    assert revoked.status_code == 200, revoked.text
    again = client.get(f"/v1/models/{child1_model['id']}", headers=tree.root)
    assert again.status_code == 403, again.text


def test_borrowed_key_usage_is_attributed_to_borrower(tree, client):
    model = _create_model(client, tree.root, name="borrow-key", owner="child1")
    assert client.patch(
        "/v1/accounts/child1/key-sharing", json={"allow": True}, headers=tree.child1
    ).status_code == 200

    chat = client.post(
        "/v1/chat",
        json={
            "message": "借用子账号 Key 的一次调用",
            "session_id": "borrow-session",
            "model_id": model["id"],
        },
        headers=tree.root,
    )
    assert chat.status_code == 200, chat.text

    # 根视角：这笔调用真实存在，调用者是 root1，Key 主人是 child1。
    root_view = client.get("/v1/accounts/child1/usage", headers=tree.root)
    assert root_view.status_code == 200, root_view.text
    payload = root_view.json()
    assert payload["callers_visible"] is True
    assert {row["actor"]: row["calls"] for row in payload["by_actor"]}["root1"] >= 1
    assert {row["key_owner"]: row["calls"] for row in payload["by_key_owner"]}["child1"] >= 1

    # 成员本人视角：自己一次都没调用，不能被记成"我的用量"。
    member_view = client.get("/v1/accounts/child1/usage", headers=tree.child1)
    assert member_view.status_code == 200, member_view.text
    member_payload = member_view.json()
    assert member_payload["calls"] == 0
    assert member_payload["total_tokens"] == 0
    assert "by_actor" not in member_payload
    assert all(row["calls"] == 0 for row in member_payload["by_key_owner"])

    # 兄弟看 child1 的用量：中性 404。
    _assert_neutral_404(
        client.get("/v1/accounts/child1/usage", headers=tree.child2)
    )


def test_explicit_model_selection_cannot_bypass_key_sharing_consent(tree, client):
    """显式 ``model_id`` 与自动路由共用同一套授权口径。

    曾经只有"读详情 / 改配置"走 ``KeyScope.can_use``，而真正发请求的
    ``/v1/chat`` 在指定 ``model_id`` 时只看可见性 —— 于是成员把
    ``allow_superior_key_use`` 关掉后，主账号仍能拿它的 Key 出网。
    """

    model = _create_model(client, tree.root, name="consent-key", owner="child1")
    body = {
        "message": "用成员 Key 回一句",
        "session_id": "consent-session",
        "model_id": model["id"],
    }

    denied = client.post("/v1/chat", json=body, headers=tree.root)
    assert denied.status_code == 403, denied.text
    payload = denied.json()
    assert payload.get("code") == "ACCOUNT_KEY_CONSENT_REQUIRED", payload
    assert payload.get("correlation_id"), payload
    _assert_no_hierarchy_leak(payload)

    # 成员本人开启授权后，同一请求才被放行。
    assert client.patch(
        "/v1/accounts/child1/key-sharing", json={"allow": True}, headers=tree.child1
    ).status_code == 200
    allowed = client.post("/v1/chat", json=body, headers=tree.root)
    assert allowed.status_code == 200, allowed.text


# --------------------------------------------------------------------------- #
# 4) 记忆 / 工件 / 会话
# --------------------------------------------------------------------------- #


def _add_memory(client: TestClient, headers: Dict[str, str], text: str) -> str:
    response = client.post(
        "/v1/memory/entries",
        json={"text": text, "kind": "note"},
        headers=headers,
    )
    assert response.status_code in (200, 201), response.text
    body = response.json()
    entry = body.get("entry") or body
    return str(entry.get("id") or entry.get("entry_id"))


def _memory_ids(client: TestClient, headers: Dict[str, str], scope: str = "self") -> set[str]:
    response = client.get(f"/v1/memory/entries?scope={scope}", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    entries = body.get("entries", [])
    return {str(item.get("id") or item.get("entry_id")) for item in entries}


def test_memory_scope_root_subtree_member_self(tree, client):
    root_id = _add_memory(client, tree.root, "root 的记忆")
    child_id = _add_memory(client, tree.child1, "child1 的记忆")
    sibling_id = _add_memory(client, tree.child2, "child2 的记忆")
    grand_id = _add_memory(client, tree.grand1, "grand1 的记忆")

    assert _memory_ids(client, tree.root, "self") == {root_id}
    assert _memory_ids(client, tree.root, "subtree") == {
        root_id,
        child_id,
        sibling_id,
        grand_id,
    }
    assert _memory_ids(client, tree.child1) == {child_id}
    assert _memory_ids(client, tree.grand1) == {grand_id}

    # 成员显式请求子树视图：按当前产品口径只允许根，成员 404 中性。
    _assert_neutral_404(
        client.get("/v1/memory/entries?scope=subtree", headers=tree.child1)
    )

    # 跨分支读单条：与不存在逐字一致。
    foreign = client.get(f"/v1/memory/entries/{child_id}", headers=tree.child2)
    missing = client.get("/v1/memory/entries/no-such-entry", headers=tree.child2)
    _assert_neutral_404(foreign, missing=missing)

    # 根可以按子树读成员单条。
    assert client.get(f"/v1/memory/entries/{child_id}", headers=tree.root).status_code == 200


def _write_workspace_file(owner: str, relative: str, content: str) -> None:
    import os
    from pathlib import Path

    root = Path(os.environ["LIUHAO_WORKSPACE_ROOT"])
    target = root / "accounts" / owner / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def _file_paths(
    client: TestClient,
    headers: Dict[str, str],
    account: str | None = None,
    path: str = "",
):
    params: Dict[str, str] = {}
    if account:
        params["account"] = account
    if path:
        params["path"] = path
    return client.get("/v1/files", params=params, headers=headers)


def test_artifact_file_scope(tree, client):
    _write_workspace_file("child1", "reports/a.txt", "child1 的产物")
    _write_workspace_file("child2", "reports/b.txt", "child2 的产物")
    _write_workspace_file("grand1", "reports/c.txt", "grand1 的产物")

    # 成员逻辑根恒为自己：根目录只看得到自己的 reports/。
    own = _file_paths(client, tree.child1)
    assert own.status_code == 200, own.text
    assert {entry["name"] for entry in own.json()["entries"]} == {"reports"}

    mine = _file_paths(client, tree.child1, path="reports")
    assert mine.status_code == 200, mine.text
    assert {entry["name"] for entry in mine.json()["entries"]} == {"a.txt"}

    content = client.get(
        "/v1/files/content", params={"path": "reports/a.txt"}, headers=tree.child1
    )
    assert content.status_code == 200, content.text
    assert "child1 的产物" in content.text

    # 兄弟 / 父级的同名相对路径读不到：与"不存在"同为中性 404。
    for name, headers in (("b.txt", tree.child1), ("a.txt", tree.child2)):
        _assert_neutral_404(
            client.get("/v1/files/content", params={"path": f"reports/{name}"}, headers=headers)
        )

    # 成员传 account= 指向兄弟 / 父级：中性 404。
    for account in ("child2", "root1"):
        _assert_neutral_404(_file_paths(client, tree.child1, account))

    # 根可按子树浏览成员空间；树外 404。
    for account, marker in (("child1", "a.txt"), ("grand1", "c.txt")):
        viewed = _file_paths(client, tree.root, account, path="reports")
        assert viewed.status_code == 200, viewed.text
        assert marker in {entry["name"] for entry in viewed.json()["entries"]}
    _assert_neutral_404(_file_paths(client, tree.root, "outsider"))



def test_session_history_isolation(tree, client):
    model = _create_model(client, tree.root, name="chat-key", owner="child1")
    assert client.patch(
        "/v1/accounts/child1/key-sharing", json={"allow": True}, headers=tree.child1
    ).status_code == 200
    chat = client.post(
        "/v1/chat",
        json={"message": "child1 的私密会话", "session_id": "shared-name", "model_id": model["id"]},
        headers=tree.child1,
    )
    assert chat.status_code == 200, chat.text

    mine = client.get("/v1/chat/history?session_id=shared-name", headers=tree.child1)
    assert mine.status_code == 200 and mine.json()["count"] > 0

    # 兄弟 / 孙账号用同名 session_id 也读不到别人的历史：
    # 该会话名已被 child1 占用，按产品口径与"没有历史"一致，中性 404。
    for headers in (tree.child2, tree.grand1):
        _assert_neutral_404(
            client.get("/v1/chat/history?session_id=shared-name", headers=headers)
        )

    _assert_no_hierarchy_leak(mine.json())


# --------------------------------------------------------------------------- #
# 5) 并发：建子账号 / 改权限不得有半成品
# --------------------------------------------------------------------------- #


def _parallel(count: int, worker) -> List[Any]:
    barrier = threading.Barrier(count)

    def run(index: int):
        barrier.wait()
        return worker(index)

    with ThreadPoolExecutor(max_workers=count) as pool:
        return list(pool.map(run, range(count)))


def test_concurrent_child_creation_is_atomic(tree, client):
    count = 8

    def create(index: int):
        return client.post(
            "/v1/accounts",
            json={"principal": f"concurrent{index}"},
            headers=tree.root,
        )

    responses = _parallel(count, create)
    assert all(r.status_code == 200 for r in responses), [
        (r.status_code, r.text) for r in responses
    ]
    codes = {f"concurrent{i}": responses[i].json()["activation_code"] for i in range(count)}

    # 每个新账号都必须同时具备"可激活的身份"和"账号树节点"，否则就是半成品。
    listed = client.get("/v1/accounts", headers=tree.root).json()
    assert {f"concurrent{i}" for i in range(count)} <= {
        m["principal"] for m in listed["members"]
    }
    assert len({m["principal"] for m in listed["members"]}) == len(listed["members"])

    def activate(index: int):
        return client.post(
            "/v1/accounts/activate",
            json={
                "principal": f"concurrent{index}",
                "code": codes[f"concurrent{index}"],
                "password": PASSWORD,
            },
        )

    activations = _parallel(count, activate)
    assert all(r.status_code == 200 for r in activations), [
        (r.status_code, r.text) for r in activations
    ]

    # 每个账号都能登录，且树里只有一份。
    tree_view = client.get("/v1/accounts/tree", headers=tree.root).json()
    flat = str(tree_view)
    for index in range(count):
        assert flat.count(f"concurrent{index}") == 1, (index, flat)


def test_concurrent_permission_updates_are_consistent(client):
    from src.kernels.identity import _accounts

    root = _register_root(client, "root2")
    _create_child(client, root, "permchild", permissions=["account:read"])

    scopes = list(_accounts.GRANTABLE_SCOPES)

    def grant(scope: str):
        return client.patch(
            "/v1/accounts/permchild/permissions",
            json={"grant": [scope], "revoke": []},
            headers=root,
        )

    granted = _parallel(len(scopes), lambda i: grant(scopes[i]))
    assert all(r.status_code == 200 for r in granted), [
        (r.status_code, r.text) for r in granted
    ]

    final = client.get("/v1/accounts/permchild", headers=root).json()["account"]
    assert set(final["permissions"]) == set(scopes), final

    def revoke(scope: str):
        return client.patch(
            "/v1/accounts/permchild/permissions",
            json={"grant": [], "revoke": [scope]},
            headers=root,
        )

    revoked = _parallel(len(scopes), lambda i: revoke(scopes[i]))
    assert all(r.status_code == 200 for r in revoked), [
        (r.status_code, r.text) for r in revoked
    ]
    final = client.get("/v1/accounts/permchild", headers=root).json()["account"]
    assert final["permissions"] == [], final
