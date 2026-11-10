"""组织架构数据面（企业工作台 P3A）回归 —— 真实 HTTP + 独立临时工作区。

覆盖用户要求验收的路径：

1. 空库**不预设任何部门/岗位**（模板不落库）；
2. 六种节点类型可组成任意层级（企业/事业部/分公司/部门/小组/岗位），另加一棵扁平组织；
3. 同级重名：同一父节点下大小写不敏感唯一；**不同父节点可重名**；
4. 删除策略：有子节点默认 409 + 影响面，``cascade=true`` 才级联；
5. 负责人变更（成员就地提升 / 换人自动降级旧负责人）、汇报关系、重复挂载 409；
6. 历史与回滚：回滚"当时存在、现在被删"的节点能重建；历史 append-only（回滚只增版）；
7. 影响检查三类告警：有人失去负责人 / 任务无人接管 / 组织内任职失效；
   任务库读不到时 ``unattended_tasks.checked=false``，不把"查不到"说成"无风险"；
8. 多账号隔离：越权读/写一律中性 404，响应体与"不存在"逐字同形；
9. 审计：写操作真实落``org.*``事件。
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


def _headers(subject: str, scope: str = "L0") -> Dict[str, str]:
    from src.security import get_jwt_handler

    token, _payload = get_jwt_handler().create_token(
        subject=subject, metadata={"kind": "human", "scope": scope}
    )
    return {"Authorization": f"Bearer {token}"}


def _make_tree(root: str = "boss", members=("alice",)) -> None:
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
    _make_tree()

    store = EmployeeStore()
    store.create_employee("analyst", role_description="数据分析")
    store.create_employee("writer", role_description="内容撰写")
    store.create_employee("planner", role_description="计划排期")

    from src.gateway.main import get_app

    with TestClient(get_app()) as test_client:
        yield test_client

    AIStateManager._instance = None
    reset_knowledge_service()
    monkeypatch.setattr(identity_module, "_global_manager", None)


# ── 小工具 ────────────────────────────────────────────────────


def _create(
    client: TestClient,
    name: str,
    kind: str = "department",
    parent_id: Optional[str] = None,
    subject: str = "boss",
) -> Dict[str, Any]:
    resp = client.post(
        "/v1/org/nodes",
        headers=_headers(subject),
        json={"name": name, "kind": kind, "parent_id": parent_id},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["node"]


def _tree(client: TestClient, subject: str = "boss", **params) -> Dict[str, Any]:
    resp = client.get("/v1/org/tree", headers=_headers(subject), params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _flat(nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for node in nodes:
        out.append(node)
        out.extend(_flat(node.get("children") or []))
    return out


def _detail(resp) -> Dict[str, Any]:
    """结构化错误体会被网关摊到顶层（code/message/detail），两种形状都兼容。"""
    body = resp.json()
    if isinstance(body, dict) and "code" in body:
        return body
    detail = body.get("detail", body) if isinstance(body, dict) else body
    return detail if isinstance(detail, dict) else {"message": detail}


# ── 1. 空库不预设 ─────────────────────────────────────────────


def test_empty_org_has_no_preset_departments(client):
    tree = _tree(client)
    assert tree["node_count"] == 0
    assert tree["roots"] == []
    assert tree["assignment_count"] == 0
    assert tree["latest_version"] is None


# ── 2. 任意层级 + 扁平组织 ────────────────────────────────────


def test_six_level_hierarchy_and_flat_org(client):
    company = _create(client, "鎏灏科技", "company")
    unit = _create(client, "智能体事业部", "business_unit", company["node_id"])
    branch = _create(client, "上海分公司", "branch", unit["node_id"])
    dept = _create(client, "产品部", "department", branch["node_id"])
    team = _create(client, "增长组", "team", dept["node_id"])
    position = _create(client, "增长负责人", "position", team["node_id"])
    flat = _create(client, "独立工作室", "company")

    tree = _tree(client)
    assert tree["node_count"] == 7
    assert {root["node_id"] for root in tree["roots"]} == {company["node_id"], flat["node_id"]}

    flat_nodes = {node["node_id"]: node for node in _flat(tree["roots"])}
    assert flat_nodes[position["node_id"]]["depth"] == 5
    assert flat_nodes[flat["node_id"]]["depth"] == 0
    assert flat_nodes[dept["node_id"]]["parent_id"] == branch["node_id"]

    detail = client.get(
        f"/v1/org/nodes/{position['node_id']}", headers=_headers("boss")
    )
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert [item["name"] for item in body["ancestors"]] == [
        "鎏灏科技",
        "智能体事业部",
        "上海分公司",
        "产品部",
        "增长组",
    ]


# ── 3. 同级重名范围 ───────────────────────────────────────────


def test_sibling_name_uniqueness_scope(client):
    root = _create(client, "总部", "company")
    dept_a = _create(client, "研发部", "department", root["node_id"])
    dept_b = _create(client, "市场部", "department", root["node_id"])

    # 不同父节点可以重名：真实企业里两个部门各有一个"综合组"很正常。
    _create(client, "综合组", "team", dept_a["node_id"])
    _create(client, "综合组", "team", dept_b["node_id"])

    # 同一父节点下大小写不敏感唯一。
    _create(client, "Team-A", "team", dept_a["node_id"])
    dup = client.post(
        "/v1/org/nodes",
        headers=_headers("boss"),
        json={"name": "team-a", "kind": "team", "parent_id": dept_a["node_id"]},
    )
    assert dup.status_code == 409, dup.text
    assert _detail(dup)["code"] == "ORG_DUP_NAME"

    # 相同名字放到另一个父节点下仍然可以。
    ok = client.post(
        "/v1/org/nodes",
        headers=_headers("boss"),
        json={"name": "team-a", "kind": "team", "parent_id": dept_b["node_id"]},
    )
    assert ok.status_code == 201, ok.text


# ── 4. 删除策略 ───────────────────────────────────────────────


def test_delete_refuses_children_then_cascades(client):
    root = _create(client, "总部", "company")
    dept = _create(client, "交付部", "department", root["node_id"])
    team = _create(client, "实施组", "team", dept["node_id"])
    assert team["parent_id"] == dept["node_id"]

    refused = client.delete(f"/v1/org/nodes/{root['node_id']}", headers=_headers("boss"))
    assert refused.status_code == 409, refused.text
    detail = _detail(refused)
    assert detail["code"] == "ORG_NODE_HAS_CHILDREN"
    impact = detail["impact"]
    assert impact["allowed"] is False
    blocker = impact["blockers"][0]
    assert blocker["code"] == "ORG_NODE_HAS_CHILDREN"
    assert set(blocker["node_ids"]) == {dept["node_id"], team["node_id"]}

    # 拒绝时不能有任何写入。
    assert _tree(client)["node_count"] == 3

    removed = client.delete(
        f"/v1/org/nodes/{root['node_id']}",
        headers=_headers("boss"),
        params={"cascade": "true"},
    )
    assert removed.status_code == 200, removed.text
    body = removed.json()
    assert body["removed_nodes"] == 3
    assert body["impact"]["allowed"] is True
    assert _tree(client)["node_count"] == 0


# ── 5. 负责人 / 成员 / 汇报关系 ───────────────────────────────


def test_lead_and_assignment_flows(client):
    dept = _create(client, "运营部", "department")
    lead = client.put(
        f"/v1/org/nodes/{dept['node_id']}/lead",
        headers=_headers("boss"),
        json={"subject_type": "employee", "subject_id": "analyst"},
    )
    assert lead.status_code == 200, lead.text
    lead_assignment = lead.json()["assignment"]
    assert lead_assignment["is_lead"] is True
    assert lead_assignment["is_primary"] is True
    assert lead_assignment["subject_label"].startswith("analyst")

    member = client.post(
        f"/v1/org/nodes/{dept['node_id']}/members",
        headers=_headers("boss"),
        json={
            "subject_type": "employee",
            "subject_id": "writer",
            "reports_to_assignment_id": lead_assignment["assignment_id"],
        },
    )
    assert member.status_code == 201, member.text
    writer_assignment = member.json()["assignment"]
    assert writer_assignment["reports_to_assignment_id"] == lead_assignment["assignment_id"]

    duplicate = client.post(
        f"/v1/org/nodes/{dept['node_id']}/members",
        headers=_headers("boss"),
        json={"subject_type": "employee", "subject_id": "writer"},
    )
    assert duplicate.status_code == 409, duplicate.text
    assert _detail(duplicate)["code"] == "ORG_ASSIGNMENT_EXISTS"

    unknown = client.put(
        f"/v1/org/nodes/{dept['node_id']}/lead",
        headers=_headers("boss"),
        json={"subject_type": "employee", "subject_id": "ghost"},
    )
    assert unknown.status_code == 422, unknown.text
    assert _detail(unknown)["code"] == "UNKNOWN_EMPLOYEE"

    human = client.post(
        f"/v1/org/nodes/{dept['node_id']}/members",
        headers=_headers("boss"),
        json={"subject_type": "human", "subject_id": "alice"},
    )
    assert human.status_code == 201, human.text

    # 换负责人：writer 就地提升，analyst 自动降为普通成员。
    promoted = client.patch(
        f"/v1/org/assignments/{writer_assignment['assignment_id']}",
        headers=_headers("boss"),
        json={"is_lead": True},
    )
    assert promoted.status_code == 200, promoted.text
    assert promoted.json()["assignment"]["is_lead"] is True

    detail = client.get(f"/v1/org/nodes/{dept['node_id']}", headers=_headers("boss"))
    assignments = detail.json()["assignments"]
    by_subject = {item["subject_id"]: item for item in assignments}
    assert by_subject["writer"]["is_lead"] is True
    assert by_subject["analyst"]["is_lead"] is False
    assert by_subject["alice"]["is_lead"] is False

    # writer 原本汇报给 analyst；analyst 再汇报给 writer 就是真环，必须拒绝。
    cycle = client.patch(
        f"/v1/org/assignments/{by_subject['analyst']['assignment_id']}",
        headers=_headers("boss"),
        json={"reports_to_assignment_id": writer_assignment["assignment_id"]},
    )
    assert cycle.status_code == 409, cycle.text
    assert _detail(cycle)["code"] == "ORG_REPORT_CYCLE"

    # 给新负责人挂一个真实下属，验证"有下级时默认拒绝取消负责人"。
    subordinate = client.post(
        f"/v1/org/nodes/{dept['node_id']}/members",
        headers=_headers("boss"),
        json={
            "subject_type": "employee",
            "subject_id": "planner",
            "reports_to_assignment_id": writer_assignment["assignment_id"],
        },
    )
    assert subordinate.status_code == 201, subordinate.text

    blocked = client.delete(
        f"/v1/org/nodes/{dept['node_id']}/lead", headers=_headers("boss")
    )
    assert blocked.status_code == 409, blocked.text
    assert _detail(blocked)["code"] == "ORG_ASSIGNMENT_HAS_REPORTS"

    cleared = client.delete(
        f"/v1/org/nodes/{dept['node_id']}/lead",
        headers=_headers("boss"),
        params={"clear_reports": "true"},
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["reports_unbound"] == 1

    detail = client.get(f"/v1/org/nodes/{dept['node_id']}", headers=_headers("boss"))
    body = detail.json()
    assert body["lead"] is None
    assert all(not item["is_lead"] for item in body["assignments"])
    assert all(
        not item["reports_to_assignment_id"] for item in body["assignments"]
    )


# ── 6. 移动与环检测 ───────────────────────────────────────────


def test_move_node_and_cycle_rejected(client):
    root = _create(client, "总部", "company")
    dept_a = _create(client, "A 部门", "department", root["node_id"])
    dept_b = _create(client, "B 部门", "department", root["node_id"])
    team = _create(client, "A 小组", "team", dept_a["node_id"])

    cycle = client.post(
        f"/v1/org/nodes/{dept_a['node_id']}/move",
        headers=_headers("boss"),
        json={"parent_id": team["node_id"]},
    )
    assert cycle.status_code == 409, cycle.text
    assert _detail(cycle)["code"] == "ORG_CYCLE"

    impact = client.post(
        "/v1/org/impact",
        headers=_headers("boss"),
        json={"action": "move_node", "node_id": dept_a["node_id"], "target_parent_id": team["node_id"]},
    )
    assert impact.status_code == 200, impact.text
    assert impact.json()["allowed"] is False
    assert impact.json()["blockers"][0]["code"] == "ORG_CYCLE"

    moved = client.post(
        f"/v1/org/nodes/{team['node_id']}/move",
        headers=_headers("boss"),
        json={"parent_id": dept_b["node_id"]},
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["node"]["parent_id"] == dept_b["node_id"]

    tree = _tree(client)
    by_name = {node["name"]: node for node in _flat(tree["roots"])}
    assert by_name["A 小组"]["parent_id"] == dept_b["node_id"]


# ── 7. 历史与回滚 ─────────────────────────────────────────────


def test_rollback_rebuilds_deleted_nodes_and_is_append_only(client):
    root = _create(client, "总部", "company")
    dept = _create(client, "研发部", "department", root["node_id"])
    position = _create(client, "后端工程师", "position", dept["node_id"])
    lead = client.put(
        f"/v1/org/nodes/{dept['node_id']}/lead",
        headers=_headers("boss"),
        json={"subject_type": "employee", "subject_id": "analyst"},
    )
    assert lead.status_code == 200, lead.text

    before_tree = _tree(client)
    before_versions = client.get("/v1/org/versions", headers=_headers("boss")).json()
    target_version = before_versions["versions"][0]
    assert target_version["seq"] == before_versions["total"]
    before_ids = {node["node_id"] for node in _flat(before_tree["roots"])}
    before_assignments = {
        item["assignment_id"] for node in _flat(before_tree["roots"]) for item in node["assignments"]
    }
    assert position["node_id"] in before_ids

    # 删掉整棵树 → 回滚"当时存在、现在被删"的节点。
    deleted = client.delete(
        f"/v1/org/nodes/{root['node_id']}",
        headers=_headers("boss"),
        params={"cascade": "true"},
    )
    assert deleted.status_code == 200, deleted.text
    assert _tree(client)["node_count"] == 0

    md_before_rollback = client.get("/v1/org/versions", headers=_headers("boss")).json()
    assert md_before_rollback["total"] == before_versions["total"] + 1  # 删除本身也记一版

    rolled = client.post(
        f"/v1/org/versions/{target_version['version_id']}/rollback",
        headers=_headers("boss"),
    )
    assert rolled.status_code == 200, rolled.text
    body = rolled.json()
    assert body["restored_nodes"] == len(before_ids)
    assert body["version"]["action"] == "rollback"
    assert body["version"]["seq"] == md_before_rollback["total"] + 1

    after_tree = _tree(client)
    after_ids = {node["node_id"] for node in _flat(after_tree["roots"])}
    after_assignments = {
        item["assignment_id"] for node in _flat(after_tree["roots"]) for item in node["assignments"]
    }
    assert after_ids == before_ids
    assert after_assignments == before_assignments
    after_by_id = {node["node_id"]: node for node in _flat(after_tree["roots"])}
    for node in _flat(before_tree["roots"]):
        assert after_by_id[node["node_id"]]["parent_id"] == node["parent_id"]

    # append-only：历史只增不改，目标版本快照仍保留当时的节点。
    final_versions = client.get("/v1/org/versions", headers=_headers("boss")).json()
    assert final_versions["total"] == md_before_rollback["total"] + 1
    seqs = [item["seq"] for item in final_versions["versions"]]
    assert seqs == sorted(seqs, reverse=True)
    snapshot = client.get(
        f"/v1/org/versions/{target_version['version_id']}", headers=_headers("boss")
    ).json()
    assert {node["node_id"] for node in snapshot["snapshot"]["nodes"]} == before_ids


def test_rename_and_rollback_restores_old_name(client):
    node = _create(client, "旧名字", "department")
    versions = client.get("/v1/org/versions", headers=_headers("boss")).json()
    create_version = versions["versions"][0]

    renamed = client.patch(
        f"/v1/org/nodes/{node['node_id']}",
        headers=_headers("boss"),
        json={"name": "新名字", "description": "改名验证"},
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["node"]["name"] == "新名字"

    empty = client.patch(
        f"/v1/org/nodes/{node['node_id']}", headers=_headers("boss"), json={}
    )
    assert empty.status_code == 422, empty.text
    assert _detail(empty)["code"] == "ORG_EMPTY_PATCH"

    rolled = client.post(
        f"/v1/org/versions/{create_version['version_id']}/rollback",
        headers=_headers("boss"),
    )
    assert rolled.status_code == 200, rolled.text
    tree = _tree(client)
    assert tree["node_count"] == 1
    assert tree["roots"][0]["name"] == "旧名字"
    assert tree["roots"][0]["description"] == ""
    # 回滚不改写历史：改名那一版仍然保存"新名字"。
    rename_version = client.get(
        "/v1/org/versions", headers=_headers("boss"), params={"limit": 10}
    ).json()["versions"]
    update_entry = next(item for item in rename_version if item["action"] == "update_node")
    snapshot = client.get(
        f"/v1/org/versions/{update_entry['version_id']}", headers=_headers("boss")
    ).json()
    assert snapshot["snapshot"]["nodes"][0]["name"] == "新名字"


# ── 8. 影响检查三类告警 ───────────────────────────────────────
def _inject_running_goal(employee: str, owner: str = "boss") -> None:
    """在真实执行内核的热集合里放一个未终结目标（读路径与线上一致）。"""
    from src.gateway.ai_management import AIStateManager

    manager = AIStateManager()
    with manager._goal_lock:
        manager._goals["goal-org-impact"] = {
            "goal_id": "goal-org-impact",
            "state": "executing",
            "owner_account_id": owner,
            "natural_language": "整理季度经营报表",
            "tasks": [
                {
                    "task_id": "task-1",
                    "status": "running",
                    "assigned_employee": employee,
                    "description": "汇总数据",
                }
            ],
        }


def test_impact_reports_three_warning_categories(client):
    root = _create(client, "总部", "company")
    dept = _create(client, "数据部", "department", root["node_id"])
    position = _create(client, "分析师", "position", dept["node_id"])

    lead = client.put(
        f"/v1/org/nodes/{dept['node_id']}/lead",
        headers=_headers("boss"),
        json={"subject_type": "employee", "subject_id": "analyst"},
    ).json()["assignment"]
    member = client.post(
        f"/v1/org/nodes/{position['node_id']}/members",
        headers=_headers("boss"),
        json={
            "subject_type": "employee",
            "subject_id": "writer",
            "reports_to_assignment_id": lead["assignment_id"],
        },
    )
    assert member.status_code == 201, member.text

    _inject_running_goal("analyst")

    resp = client.post(
        "/v1/org/impact",
        headers=_headers("boss"),
        json={"action": "delete_node", "node_id": dept["node_id"], "cascade": True},
    )
    assert resp.status_code == 200, resp.text
    impact = resp.json()
    warnings = impact["warnings"]

    assert impact["allowed"] is True
    assert warnings["orphaned_lead"]["count"] >= 1
    assert warnings["orphaned_lead"]["label"] == "有人失去负责人"

    assert warnings["unattended_tasks"]["checked"] is True
    assert warnings["unattended_tasks"]["count"] >= 1
    task_item = warnings["unattended_tasks"]["items"][0]
    assert task_item["employee"] == "analyst"
    assert task_item["task_id"] == "task-1"
    assert warnings["unattended_tasks"]["label"] == "任务无人接管"

    assert warnings["revoked_authority"]["count"] == 2
    assert warnings["revoked_authority"]["label"] == "组织内任职失效"
    assert "权限已被收回" not in warnings["revoked_authority"]["note"]
    assert "不代表账号权限被收回" in warnings["revoked_authority"]["note"]

    # 预演不写盘。
    assert _tree(client)["node_count"] == 3


def test_impact_marks_tasks_unchecked_when_store_unreadable(client, monkeypatch):
    dept = _create(client, "运营部", "department")
    client.put(
        f"/v1/org/nodes/{dept['node_id']}/lead",
        headers=_headers("boss"),
        json={"subject_type": "employee", "subject_id": "analyst"},
    )

    import src.gateway.ai_management as ai_management

    class _BrokenManager:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("store offline")

    monkeypatch.setattr(ai_management, "AIStateManager", _BrokenManager)

    resp = client.post(
        "/v1/org/impact",
        headers=_headers("boss"),
        json={"action": "delete_node", "node_id": dept["node_id"], "cascade": True},
    )
    assert resp.status_code == 200, resp.text
    unattended = resp.json()["warnings"]["unattended_tasks"]
    assert unattended["checked"] is False
    assert unattended["count"] is None
    assert "未检查" in unattended["reason"]


# ── 9. 多账号隔离 ─────────────────────────────────────────────


def test_cross_account_access_is_neutral_404(client):
    node = _create(client, "总部", "company")

    # 成员看不到主账号的组织树，也读不到具体节点。
    alice_tree = _tree(client, subject="alice")
    assert alice_tree["node_count"] == 0

    missing = client.get("/v1/org/nodes/does-not-exist", headers=_headers("alice"))
    peek = client.get(f"/v1/org/nodes/{node['node_id']}", headers=_headers("alice"))
    assert peek.status_code == 404, peek.text
    assert peek.json() == missing.json()

    # 成员写入主账号节点：同样是中性 404，不泄露存在性。
    write = client.patch(
        f"/v1/org/nodes/{node['node_id']}",
        headers=_headers("alice"),
        json={"name": "偷改"},
    )
    assert write.status_code == 404, write.text
    assert write.json() == missing.json()

    # 成员不能切到主账号视角；主账号可以只读查看成员（空）树。
    denied = client.get(
        "/v1/org/tree", headers=_headers("alice"), params={"view_account": "boss"}
    )
    assert denied.status_code == 404, denied.text
    allowed = client.get(
        "/v1/org/tree", headers=_headers("boss"), params={"view_account": "alice"}
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["node_count"] == 0
    assert _tree(client)["node_count"] == 1


# ── 10. 审计 ─────────────────────────────────────────────────


def test_mutations_write_audit_events(client):
    resp = client.post(
        "/v1/org/nodes",
        headers=_headers("boss"),
        json={"name": "审计部", "kind": "department"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["audit_written"] is True

    events = client.get(
        "/v1/audit/events",
        headers=_headers("boss"),
        params={"action": "org.create_node", "limit": 10},
    )
    assert events.status_code == 200, events.text
    body = events.json()
    assert body["count"] >= 1
    details = body["events"][0].get("details") or {}
    assert details.get("action") == "org.create_node"
    assert details.get("name") == "审计部"
