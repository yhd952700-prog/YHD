"""组织架构模板回归 —— 内置参考、自定义 CRUD、三模式、冲突与回滚。

重点是三条产品约束：

1. **不自动套用**：只读列表/详情不会改组织树；没点 apply 就绝不落库。
2. **内置只隐藏不真删**：内置模板删除返回 409 ``ORG_TEMPLATE_BUILTIN_HIDE_ONLY``；
   复制成自定义后才可改名/改树/硬删。
3. **预览即契约**：apply 必须带 preview_token；组织或模板变了令牌失效，整笔不写。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

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


def _headers(subject: str = "boss", *, kind: str = "human", scope: str = "L0") -> Dict[str, str]:
    from src.security import get_jwt_handler

    token, _payload = get_jwt_handler().create_token(
        subject=subject, metadata={"kind": kind, "scope": scope}
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    for name in _PROVIDER_ENVS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LIUHAO_ENV", "development")
    monkeypatch.setenv("LIUHAO_KNOWLEDGE_ALLOW_MOCK", "1")
    monkeypatch.setenv("LIUHAO_EMBEDDING_PROVIDER", "mock")
    monkeypatch.setenv("LIUHAO_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("LIUHAO_ORG_DB", str(tmp_path / "data" / "org_store.db"))
    monkeypatch.setenv("LIUHAO_HUMAN_IDENTITIES_BACKEND", "file")
    monkeypatch.setenv("LIUHAO_HUMAN_IDENTITIES_FILE", str(tmp_path / "humans.json"))
    monkeypatch.setenv("AUDIT_DB_PATH", str(tmp_path / "audit.db"))
    monkeypatch.setenv("HTTP_PROXY", "")
    monkeypatch.setenv("HTTPS_PROXY", "")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")

    import src.kernels.identity as identity_module
    from src.ai.employee_store import EmployeeStore
    from src.gateway.ai_management import AIStateManager
    from src.knowledge.documents import reset_knowledge_service

    monkeypatch.setattr(identity_module, "_global_manager", None)
    AIStateManager._instance = None
    reset_knowledge_service()

    from src.kernels.identity import get_identity_manager

    manager = get_identity_manager()
    manager.register_root_account("boss")
    manager.create_member_account(actor="boss", principal="alice")
    EmployeeStore().create_employee("analyst", role_description="数据分析")

    from src.gateway.main import get_app

    with TestClient(get_app()) as test_client:
        yield test_client

    AIStateManager._instance = None
    reset_knowledge_service()
    monkeypatch.setattr(identity_module, "_global_manager", None)


def _error(resp) -> Dict[str, Any]:
    body = resp.json()
    if isinstance(body, dict) and "code" in body:
        return body
    detail = body.get("detail", body) if isinstance(body, dict) else body
    return detail if isinstance(detail, dict) else {"message": detail}


def _create_node(
    client: TestClient, name: str, kind: str = "department", parent_id: Optional[str] = None
) -> Dict[str, Any]:
    resp = client.post(
        "/v1/org/nodes",
        headers=_headers(),
        json={"name": name, "kind": kind, "parent_id": parent_id},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["node"]


def _tree(client: TestClient) -> Dict[str, Any]:
    resp = client.get("/v1/org/tree", headers=_headers())
    assert resp.status_code == 200, resp.text
    return resp.json()


def _flat(nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for node in nodes:
        out.append(node)
        out.extend(_flat(node.get("children") or []))
    return out


def _keys(root: Dict[str, Any]) -> List[str]:
    out = [root["key"]]
    for child in root.get("children") or []:
        out.extend(_keys(child))
    return out


def _templates(client: TestClient, include_hidden: bool = False) -> Dict[str, Any]:
    resp = client.get(
        "/v1/org/templates",
        headers=_headers(),
        params={"include_hidden": "true" if include_hidden else "false"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _detail(client: TestClient, template_id: str = "builtin.software-dev") -> Dict[str, Any]:
    resp = client.get(f"/v1/org/templates/{template_id}", headers=_headers())
    assert resp.status_code == 200, resp.text
    return resp.json()


def _preview(client: TestClient, template_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    resp = client.post(
        f"/v1/org/templates/{template_id}/preview", headers=_headers(), json=body
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_builtin_templates_are_reference_only_and_do_not_touch_tree(client):
    data = _templates(client)
    assert data["total"] == 4
    names = {item["name"] for item in data["templates"]}
    assert {"软件研发", "内容运营", "电商", "个人单兵"} <= names
    assert all(item["source"] == "builtin" for item in data["templates"])
    # 只读列表不会自动套用：组织树仍然为空。
    assert _tree(client)["node_count"] == 0


def test_builtin_is_readonly_copy_then_edit_and_hide_only(client):
    builtin = _detail(client)["template_id"]
    # 内置不能直接改。
    resp = client.patch(
        f"/v1/org/templates/{builtin}", headers=_headers(), json={"name": "改名"}
    )
    assert resp.status_code == 409, resp.text
    assert _error(resp)["code"] == "ORG_TEMPLATE_BUILTIN_READONLY"

    # 内置不能真删，只能隐藏。
    resp = client.delete(f"/v1/org/templates/{builtin}", headers=_headers())
    assert resp.status_code == 409, resp.text
    assert _error(resp)["code"] == "ORG_TEMPLATE_BUILTIN_HIDE_ONLY"

    resp = client.post(f"/v1/org/templates/{builtin}/hide", headers=_headers())
    assert resp.status_code == 200, resp.text
    assert builtin not in {item["template_id"] for item in _templates(client)["templates"]}
    assert builtin in {
        item["template_id"] for item in _templates(client, include_hidden=True)["templates"]
    }
    resp = client.post(f"/v1/org/templates/{builtin}/unhide", headers=_headers())
    assert resp.status_code == 200, resp.text

    # 复制后可改、可删；内置本身不受影响。
    resp = client.post(
        f"/v1/org/templates/{builtin}/copy", headers=_headers(), json={"name": "我们的研发"}
    )
    assert resp.status_code == 200, resp.text
    copied = resp.json()["template_id"]
    resp = client.patch(
        f"/v1/org/templates/{copied}",
        headers=_headers(),
        json={"name": "我们的研发 v2"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["revision"] == 2
    resp = client.delete(f"/v1/org/templates/{copied}", headers=_headers())
    assert resp.status_code == 200, resp.text
    assert _detail(client, builtin)["name"] == "软件研发"


def test_import_export_roundtrip_and_invalid_payload(client):
    payload = _detail(client)["payload"]
    resp = client.post(
        "/v1/org/templates/import",
        headers=_headers(),
        params={"name": "导入的研发"},
        json=payload,
    )
    assert resp.status_code == 200, resp.text
    imported = resp.json()["template_id"]
    exported = client.get(f"/v1/org/templates/{imported}/export", headers=_headers())
    assert exported.status_code == 200, exported.text
    assert exported.json()["template"]["name"] == "导入的研发"
    assert _keys(exported.json()["template"]["root"]) == _keys(payload["template"]["root"])

    bad = client.post(
        "/v1/org/templates/import",
        headers=_headers(),
        json={"kind": "wrong", "schema_version": 1, "template": {}},
    )
    assert bad.status_code == 422, bad.text
    assert _error(bad)["code"] == "ORG_TEMPLATE_INVALID"


def test_preview_is_read_only_and_apply_requires_token(client):
    detail = _detail(client)
    keys = _keys(detail["payload"]["template"]["root"])
    body = {"mode": "subtree", "target_parent_id": None, "selected_keys": keys}
    plan = _preview(client, detail["template_id"], body)
    assert plan["summary"]["create"] == len(keys)
    assert plan["summary"]["conflict"] == 0
    # 预览不落库。
    assert _tree(client)["node_count"] == 0

    resp = client.post(
        f"/v1/org/templates/{detail['template_id']}/apply", headers=_headers(), json=body
    )
    assert resp.status_code == 409, resp.text
    assert _error(resp)["code"] == "ORG_TEMPLATE_STALE_PREVIEW"

    resp = client.post(
        f"/v1/org/templates/{detail['template_id']}/apply",
        headers=_headers(),
        json={**body, "preview_token": plan["preview_token"]},
    )
    assert resp.status_code == 200, resp.text
    applied = resp.json()
    assert applied["summary"]["created_nodes"] == len(keys)
    assert applied["version"]["action"] == "template_apply"
    tree = _tree(client)
    assert tree["node_count"] == len(keys)
    assert tree["latest_version"]["action"] == "template_apply"


def test_preview_requires_explicit_selection(client):
    detail = _detail(client)
    resp = client.post(
        f"/v1/org/templates/{detail['template_id']}/preview",
        headers=_headers(),
        json={"mode": "subtree", "target_parent_id": None},
    )
    assert resp.status_code == 422, resp.text
    assert _error(resp)["code"] == "ORG_TEMPLATE_SELECTION_INVALID"

    resp = client.post(
        f"/v1/org/templates/{detail['template_id']}/preview",
        headers=_headers(),
        json={"mode": "subtree", "selected_keys": ["not-in-template"]},
    )
    assert resp.status_code == 422, resp.text
    assert _error(resp)["code"] == "ORG_TEMPLATE_SELECTION_INVALID"


def test_merge_detects_kind_conflict_and_rename_resolution(client):
    anchor = _create_node(client, "研发中心", "department")
    existing = _create_node(client, "产品部", "team", anchor["node_id"])
    detail = _detail(client)
    keys = _keys(detail["payload"]["template"]["root"])
    body = {
        "mode": "merge",
        "target_node_id": anchor["node_id"],
        "selected_keys": keys,
    }
    plan = _preview(client, detail["template_id"], body)
    conflicts = [item for item in plan["conflicts"] if item["code"] == "ORG_TEMPLATE_KIND_CONFLICT"]
    assert [item["key"] for item in conflicts] == ["product"]
    assert plan["summary"]["merge"] >= 1
    assert _tree(client)["node_count"] == 2

    # 未处理冲突不能套用。
    resp = client.post(
        f"/v1/org/templates/{detail['template_id']}/apply",
        headers=_headers(),
        json={**body, "preview_token": plan["preview_token"]},
    )
    assert resp.status_code == 409, resp.text
    assert _error(resp)["code"] == "ORG_TEMPLATE_CONFLICT"

    resolved_body = {
        **body,
        "resolutions": [{"key": "product", "action": "rename", "new_name": "产品部（模板）"}],
    }
    plan = _preview(client, detail["template_id"], resolved_body)
    assert not [item for item in plan["conflicts"] if item["resolved"] is None]
    resp = client.post(
        f"/v1/org/templates/{detail['template_id']}/apply",
        headers=_headers(),
        json={**resolved_body, "preview_token": plan["preview_token"]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["summary"]["renamed_nodes"] == 1
    names = {node["name"] for node in _flat(_tree(client)["roots"])}
    assert "产品部（模板）" in names
    # 原有 team 仍在，未被模板覆盖。
    assert existing["name"] in names


def test_replace_preview_shows_removals_and_apply_creates_version(client):
    _create_node(client, "旧部门", "department")
    detail = _detail(client)
    keys = _keys(detail["payload"]["template"]["root"])
    body = {"mode": "replace", "selected_keys": keys}
    plan = _preview(client, detail["template_id"], body)
    assert plan["removals"]["nodes"] == 1
    assert plan["removals"]["assignments"] == 0
    assert plan["rollback"]["available"] is True
    resp = client.post(
        f"/v1/org/templates/{detail['template_id']}/apply",
        headers=_headers(),
        json={**body, "preview_token": plan["preview_token"]},
    )
    assert resp.status_code == 200, resp.text
    result = resp.json()
    assert result["summary"]["removed_nodes"] == 1
    assert result["summary"]["created_nodes"] == len(keys)
    tree = _tree(client)
    assert tree["node_count"] == len(keys)
    assert "旧部门" not in {node["name"] for node in _flat(tree["roots"])}


def test_stale_preview_token_rejected_after_tree_changes(client):
    detail = _detail(client)
    keys = _keys(detail["payload"]["template"]["root"])
    body = {"mode": "subtree", "target_parent_id": None, "selected_keys": keys}
    plan = _preview(client, detail["template_id"], body)
    _create_node(client, "预览后新增", "department")
    resp = client.post(
        f"/v1/org/templates/{detail['template_id']}/apply",
        headers=_headers(),
        json={**body, "preview_token": plan["preview_token"]},
    )
    assert resp.status_code == 409, resp.text
    assert _error(resp)["code"] == "ORG_TEMPLATE_STALE_PREVIEW"
    assert _tree(client)["node_count"] == 1


def test_member_cannot_read_another_accounts_custom_template(client):
    payload = _detail(client)["payload"]
    resp = client.post("/v1/org/templates/import", headers=_headers(), json=payload)
    assert resp.status_code == 200, resp.text
    template_id = resp.json()["template_id"]

    resp = client.get(
        f"/v1/org/templates/{template_id}", headers=_headers("alice", scope="L1")
    )
    assert resp.status_code == 404, resp.text
    # 越权与不存在同形：不泄露"这个 id 存在"。
    resp = client.get("/v1/org/templates/tpl_does_not_exist", headers=_headers())
    assert resp.status_code == 404, resp.text
    assert _error(resp)["message"] == "资源不存在或无权访问"
