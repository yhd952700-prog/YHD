"""项目面「可用性」回归 —— 完整用户路径 + 边界 + 多账号视角。

覆盖（真实 HTTP，独立临时工作区，不碰正在压测的实例）：

1. 新建项目 → 看到「目标数 / 最近活动 / 产出」 → 归档 / 恢复 → 删除；
2. 边界：同名项目 409、空名/超长 422（中文说明）、空项目可直接删、
   有目标的项目先 409 报影响面、``force=true`` 才删且目标不被删；
3. 多账号：成员只看自己的项目；主账号可聚合 + ``view_account`` 切视角；
   越权（读 / 归档 / 恢复 / 删除 / 切他人视角）一律中性 404（响应体逐字同形）。

目标通过**真实执行内核**产生（与 ``test_goal_artifacts_linkage`` 同路径：
确定性分解器写出 ``hello.txt``），从而项目里的产出是真实产物而不是测试造的假数据。
"""

from __future__ import annotations

from typing import Dict

import pytest
from fastapi.testclient import TestClient

_PROVIDER_ENVS = (
    "LIUHAO_EMBEDDING_PROVIDER",
    "LIUHAO_EMBEDDING_MODEL",
    "LIUHAO_EMBEDDING_API_KEY",
    "LIUHAO_KNOWLEDGE_ALLOW_MOCK",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OLLAMA_BASE_URL",
    "LIUHAO_OLLAMA_BASE_URL",
    "LIUHAO_ENV",
    "APP_ENV",
)


def _headers(subject: str, scope: str = "L0") -> Dict[str, str]:
    from src.security import get_jwt_handler

    token, _payload = get_jwt_handler().create_token(
        subject=subject, metadata={"kind": "human", "scope": scope}
    )
    return {"Authorization": f"Bearer {token}"}


def _make_tree(root: str = "boss", members=("alice", "bob")) -> None:
    from src.kernels.identity import get_identity_manager

    manager = get_identity_manager()
    manager.register_root_account(root)
    for member in members:
        manager.create_member_account(actor=root, principal=member)


@pytest.fixture
def client(tmp_path, monkeypatch):
    for name in _PROVIDER_ENVS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LIUHAO_ENV", "development")
    monkeypatch.setenv("LIUHAO_KNOWLEDGE_ALLOW_MOCK", "1")
    monkeypatch.setenv("LIUHAO_EMBEDDING_PROVIDER", "mock")
    monkeypatch.setenv("LIUHAO_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("LIUHAO_HUMAN_IDENTITIES_BACKEND", "file")
    monkeypatch.setenv("LIUHAO_HUMAN_IDENTITIES_FILE", str(tmp_path / "humans.json"))
    monkeypatch.setenv("AUDIT_DB_PATH", str(tmp_path / "audit.db"))
    monkeypatch.setenv("HTTP_PROXY", "")
    monkeypatch.setenv("HTTPS_PROXY", "")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")

    import src.kernels.identity as identity_module
    from src.gateway.ai_management import AIStateManager
    from src.gateway.projects import ProjectStore
    from src.knowledge.documents import reset_knowledge_service

    monkeypatch.setattr(identity_module, "_global_manager", None)
    ProjectStore._instance = None
    AIStateManager._instance = None
    reset_knowledge_service()
    _make_tree()

    from src.gateway.main import get_app

    with TestClient(get_app()) as test_client:
        yield test_client

    ProjectStore._instance = None
    AIStateManager._instance = None
    reset_knowledge_service()
    monkeypatch.setattr(identity_module, "_global_manager", None)


def _neutral(message_body) -> None:
    detail = message_body.get("detail", message_body)
    message = detail.get("message") if isinstance(detail, dict) else detail
    assert message == "资源不存在或无权访问", message_body


def _assert_neutral_404(resp, missing_resp=None):
    assert resp.status_code == 404, resp.text
    _neutral(resp.json())
    if missing_resp is not None:
        assert missing_resp.status_code == 404
        assert resp.json() == missing_resp.json()


def _execute_goal_with_artifact(project_id: str, owner: str) -> Dict:
    """走真实执行内核：确定性任务写出 ``hello.txt``，并把目标挂到项目。"""
    from src.gateway.ai_management import AIStateManager

    return AIStateManager().create_and_execute_goal(
        "write a file named hello.txt containing 'Hello World'",
        background=False,
        project_id=project_id,
        owner_account_id=owner,
    )


# ─── 1. 完整用户路径 ─────────────────────────────────────────


def test_full_project_path_create_attach_outputs_archive_restore_delete(client):
    alice = _headers("alice")
    created = client.post(
        "/v1/projects",
        json={"name": "Q4 客户交付", "description": "把 Q4 的交付目标集中到这里"},
        headers=alice,
    )
    assert created.status_code == 200, created.text
    project_id = created.json()["project_id"]

    # 空项目：三项指标如实为 0，不编造。
    listed = client.get("/v1/projects", headers=alice).json()
    entry = next(p for p in listed["projects"] if p["project_id"] == project_id)
    assert entry["goal_count"] == 0
    assert entry["output_count"] == 0
    assert entry["outputs"] == []
    assert entry["last_activity_at"] is not None
    assert entry["archived_at"] is None

    # 把目标挂到项目（真实执行，真实写出 hello.txt）。
    goal = _execute_goal_with_artifact(project_id, "alice")
    assert goal["goal_id"]

    detail = client.get(f"/v1/projects/{project_id}", headers=alice).json()
    assert detail["goal_count"] == 1
    assert detail["goals"][0]["goal_id"] == goal["goal_id"]
    assert detail["output_count"] >= 1
    output_paths = {item["path"] for item in detail["outputs"]}
    assert "hello.txt" in output_paths
    owner = next(i["owner"] for i in detail["outputs"] if i["path"] == "hello.txt")
    assert owner == "alice"
    assert detail["last_activity_at"] > detail["created_at"]

    # 归档：默认列表隐藏，include_archived 仍能看到；详情照常可读。
    archived = client.post(f"/v1/projects/{project_id}/archive", headers=alice)
    assert archived.status_code == 200, archived.text
    assert archived.json()["archived_at"] is not None
    default_ids = {p["project_id"] for p in client.get("/v1/projects", headers=alice).json()["projects"]}
    assert project_id not in default_ids
    with_archived = client.get(
        "/v1/projects", params={"include_archived": "true"}, headers=alice
    ).json()
    assert project_id in {p["project_id"] for p in with_archived["projects"]}
    assert client.get(f"/v1/projects/{project_id}", headers=alice).status_code == 200

    # 恢复：重新出现在默认列表。
    restored = client.post(f"/v1/projects/{project_id}/restore", headers=alice)
    assert restored.status_code == 200, restored.text
    assert restored.json()["archived_at"] is None
    assert project_id in {
        p["project_id"] for p in client.get("/v1/projects", headers=alice).json()["projects"]
    }

    # 删除有目标的项目：先 409 报影响面，项目仍在。
    denied = client.delete(f"/v1/projects/{project_id}", headers=alice)
    assert denied.status_code == 409, denied.text
    body = denied.json()
    assert body["code"] == "project_has_goals"
    assert body["goal_count"] == 1
    assert "目标" in body["message"]
    assert client.get(f"/v1/projects/{project_id}", headers=alice).status_code == 200

    # 明确 force 才真正删除；目标本身不被删除，只解除归属。
    forced = client.delete(
        "/v1/projects/" + project_id, params={"force": "true"}, headers=alice
    )
    assert forced.status_code == 200, forced.text
    assert forced.json()["goal_count"] == 1
    _assert_neutral_404(client.get(f"/v1/projects/{project_id}", headers=alice))
    goals = client.get("/v1/goals", headers=alice).json()
    assert goal["goal_id"] in {g["goal_id"] for g in goals["goals"]}
    goal_detail = client.get(f"/v1/goals/{goal['goal_id']}", headers=alice).json()
    assert goal_detail["project_id"] is None


# ─── 2. 边界 ─────────────────────────────────────────────────


def test_duplicate_names_and_input_limits(client):
    alice = _headers("alice")
    bob = _headers("bob")

    first = client.post(
        "/v1/projects", json={"name": "客户交付", "description": ""}, headers=alice
    )
    assert first.status_code == 200, first.text

    # 同一账号：去空白 + 大小写不敏感的同名 → 409（带可读原因）
    dup = client.post(
        "/v1/projects", json={"name": "  客户交付  ", "description": ""}, headers=alice
    )
    assert dup.status_code == 409, dup.text
    assert dup.json()["code"] == "project_name_taken"
    assert "同名" in dup.json()["message"]

    # 不同账号可以同名：项目归属各自的账号。
    assert client.post(
        "/v1/projects", json={"name": "客户交付", "description": ""}, headers=bob
    ).status_code == 200

    # 空名 / 纯空白 / 超长：422 且是中文可读说明，不是 500。
    for payload in (
        {"name": "", "description": ""},
        {"name": "   ", "description": ""},
        {"name": "长" * 201, "description": ""},
        {"name": "ok", "description": "描" * 2001},
    ):
        resp = client.post("/v1/projects", json=payload, headers=alice)
        assert resp.status_code == 422, resp.text
        assert "项目" in resp.text

    # 空项目（无目标）可以直接删除，不需要 force。
    empty = client.post(
        "/v1/projects", json={"name": "临时", "description": ""}, headers=alice
    ).json()["project_id"]
    removed = client.delete(f"/v1/projects/{empty}", headers=alice)
    assert removed.status_code == 200, removed.text
    assert removed.json()["goal_count"] == 0


# ─── 3. 多账号视角 ────────────────────────────────────────────


def test_member_isolation_and_root_view_account(client):
    boss = _headers("boss")
    alice = _headers("alice")
    bob = _headers("bob")

    alice_pid = client.post(
        "/v1/projects", json={"name": "alice 的", "description": ""}, headers=alice
    ).json()["project_id"]
    bob_pid = client.post(
        "/v1/projects", json={"name": "bob 的", "description": ""}, headers=bob
    ).json()["project_id"]

    # 成员列表只含自己的项目。
    alice_ids = {p["project_id"] for p in client.get("/v1/projects", headers=alice).json()["projects"]}
    assert alice_pid in alice_ids and bob_pid not in alice_ids

    # 越权读取 / 归档 / 恢复 / 删除：与不存在逐字同形（中性 404）。
    missing = "/v1/projects/does-not-exist"
    _assert_neutral_404(
        client.get(f"/v1/projects/{bob_pid}", headers=alice),
        client.get(missing, headers=alice),
    )
    _assert_neutral_404(
        client.post(f"/v1/projects/{bob_pid}/archive", headers=alice),
        client.post("/v1/projects/does-not-exist/archive", headers=alice),
    )
    _assert_neutral_404(
        client.post(f"/v1/projects/{bob_pid}/restore", headers=alice),
        client.post("/v1/projects/does-not-exist/restore", headers=alice),
    )
    _assert_neutral_404(client.delete(f"/v1/projects/{bob_pid}", headers=alice))
    # 越权请求没有真的动到 bob 的项目。
    bob_detail = client.get(f"/v1/projects/{bob_pid}", headers=bob)
    assert bob_detail.status_code == 200
    assert bob_detail.json()["archived_at"] is None

    # 主账号：整棵子树可见；可按成员视角过滤。
    boss_ids = {p["project_id"] for p in client.get("/v1/projects", headers=boss).json()["projects"]}
    assert {alice_pid, bob_pid} <= boss_ids

    only_alice = client.get(
        "/v1/projects", params={"view_account": "alice"}, headers=boss
    )
    assert only_alice.status_code == 200, only_alice.text
    assert {p["project_id"] for p in only_alice.json()["projects"]} == {alice_pid}
    assert only_alice.json()["view_account"] == "alice"

    # 成员不能切到别人的视角；未知账号同样中性 404（不暴露账号是否存在）。
    _assert_neutral_404(
        client.get("/v1/projects", params={"view_account": "bob"}, headers=alice)
    )
    _assert_neutral_404(
        client.get("/v1/projects", params={"view_account": "nobody"}, headers=boss)
    )
