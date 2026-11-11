"""技能中心后端面 —— ``/v1/skills`` 的真实行为。

为什么这个测试存在
------------------
前端（commit ``b64aadf1``）已把"技能中心"做完，但后端没有 ``/v1/skills``，
所有创建/导入动作都被降级禁用。这里用真实 bearer 令牌 + 真实落盘（临时目录）
证明新接口是能用的，并且**不撒谎**：

- 未带令牌 → 401；写操作要求管理员声明；
- 风险 / 所需权限 / 使用员工都在读取时从现有数据推导，不采信前端自报；
- 有员工在用时删除 → 409 并点名，不静默级联；
- 高危 / 风险未知的启用必须带 ``confirm`` + ``acknowledge_risk``，没有一键全开；
- 技能库损坏 → 503，而不是返回空列表假装"没有技能"。

``main.py`` 刻意不改（路由挂载留给统一集成），所以这里用最小 FastAPI 应用
只挂本模块的 ``router``；鉴权依赖与线上走的是同一条 ``policy`` 实现。
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.gateway.skills import router as skills_router


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """技能库与员工库都落在用例临时目录，绝不碰真实工作区数据。"""
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("LIUHAO_WORKSPACE_ROOT", str(workspace))
    monkeypatch.setenv("LIUHAO_DATA_DIR", str(tmp_path / "data"))
    yield workspace


def _token(scope: str = "L0") -> str:
    from src.security import get_jwt_handler

    token, _payload = get_jwt_handler().create_token(
        subject="test-human", metadata={"kind": "human", "scope": scope}
    )
    return token


def _headers(scope: str = "L0") -> dict:
    return {"Authorization": f"Bearer {_token(scope)}"}


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(skills_router)
    with TestClient(app) as test_client:
        yield test_client


def _create(client, name: str = "周报生成", description: str = "把周报整理成要点") -> dict:
    response = client.post(
        "/v1/skills",
        headers=_headers(),
        json={"name": name, "description": description},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _import(client, filename: str, payload: bytes) -> object:
    return client.post(
        "/v1/skills/import",
        headers=_headers(),
        files={"file": (filename, payload, "application/octet-stream")},
    )


# ---------------------------------------------------------------------- #
# 鉴权与基础 CRUD
# ---------------------------------------------------------------------- #
def test_list_requires_auth(client):
    assert client.get("/v1/skills").status_code == 401


def test_write_requires_admin_claim(client):
    response = client.post(
        "/v1/skills",
        headers=_headers(scope="L1"),
        json={"name": "无权限技能"},
    )
    assert response.status_code == 403, response.text


def test_create_list_edit_delete(client):
    created = _create(client)
    assert created["source"] == "user"
    assert created["risk"] == "low"
    assert created["permissions"] == []
    assert created["used_by"] == []
    assert created["enabled"] is True
    assert created["capabilities"] == []

    body = client.get("/v1/skills", headers=_headers()).json()
    assert body["total"] == 1
    assert body["skills"][0]["id"] == created["id"]

    patched = client.patch(
        f"/v1/skills/{created['id']}",
        headers=_headers(),
        json={"name": "周报生成（改）", "description": "新说明"},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["name"] == "周报生成（改）"
    assert patched.json()["description"] == "新说明"

    removed = client.delete(f"/v1/skills/{created['id']}", headers=_headers())
    assert removed.status_code == 200, removed.text
    assert removed.json()["deleted"] is True
    assert client.get("/v1/skills", headers=_headers()).json()["total"] == 0


def test_duplicate_name_rejected(client):
    _create(client, name="同名技能")
    response = client.post(
        "/v1/skills", headers=_headers(), json={"name": "同名技能"}
    )
    assert response.status_code == 409, response.text
    assert "同名技能" in response.json()["detail"]


def test_blank_name_rejected(client):
    response = client.post("/v1/skills", headers=_headers(), json={"name": "   "})
    assert response.status_code == 400, response.text


def test_unknown_skill_returns_404(client):
    assert client.delete("/v1/skills/skill_nope", headers=_headers()).status_code == 404
    assert client.get("/v1/skills", headers=_headers()).status_code == 200
    r = client.post("/v1/skills/skill_nope/test", headers=_headers())
    assert r.status_code == 404, r.text
    r = client.patch(
        "/v1/skills/skill_nope", headers=_headers(), json={"name": "x"}
    )
    assert r.status_code == 404, r.text


# ---------------------------------------------------------------------- #
# 测试动作：真实核对能力，而不是跑演示脚本
# ---------------------------------------------------------------------- #
def test_test_action_is_honest_for_plain_skill(client):
    created = _create(client)
    result = client.post(f"/v1/skills/{created['id']}/test", headers=_headers())
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["passed"] is None
    assert "没有声明任何执行器能力" in body["detail"]


def test_import_json_and_test_against_real_catalog(client):
    manifest = {
        "name": "落盘文件",
        "description": "把结果写进工作区",
        "version": "1.2.0",
        "capabilities": ["file_write"],
    }
    response = _import(client, "writer.json", json.dumps(manifest).encode("utf-8"))
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["source"] == "imported"
    assert body["version"] == "1.2.0"
    # 真实目录里 local_file_write 的 risk=MEDIUM，服务端必须如实抬到 medium。
    assert body["risk"] == "medium", body
    # 导入的外部技能非低风险，不默认启用。
    assert body["enabled"] is False

    tested = client.post(f"/v1/skills/{body['id']}/test", headers=_headers())
    assert tested.status_code == 200, tested.text
    assert tested.json()["passed"] is True
    assert tested.json()["missing_capabilities"] == []

    enabled = client.post(f"/v1/skills/{body['id']}/enable", headers=_headers())
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["enabled"] is True


def test_import_markdown(client):
    text = "# 会议纪要\n\n把会议录音整理成三段式纪要。\n\n## 步骤\n1. 提取结论\n"
    response = _import(client, "meeting.md", text.encode("utf-8"))
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["name"] == "会议纪要"
    assert body["description"] == "把会议录音整理成三段式纪要。"
    assert body["source"] == "imported"
    assert body["risk"] == "low"
    # 行为变更：导入一律不自动启用（旧版本低风险会自动启用）。
    assert body["enabled"] is False
    assert body["format"] == "agent-skill"
    assert body["execution_allowed"] is False
    assert any(row["field"] == "frontmatter" for row in body["import_report"])


def test_import_agent_skill_frontmatter(client):
    """按 Agent Skills 规范读 name/description/license/allowed-tools/metadata。"""
    text = (
        "---\n"
        "name: pdf-text-extract\n"
        "description: 从 PDF 里抽取正文并整理成要点\n"
        "license: MIT\n"
        "allowed-tools: Read Bash\n"
        "metadata:\n"
        "  author: example\n"
        "  version: 1.0.0\n"
        "x-custom-field: 未映射的值\n"
        "---\n\n"
        "# PDF 文本抽取\n\n"
        "按 `scripts/extract.py` 抽取正文。\n"
    )
    response = _import(client, "pdf-text-extract/SKILL.md", text.encode("utf-8"))
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["name"] == "pdf-text-extract"
    assert body["name_source"] == "frontmatter"
    assert body["license"] == "MIT"
    assert body["allowed_tools"] == ["Read", "Bash"]
    assert body["metadata"]["author"] == "example"
    assert body["has_scripts"] is True
    assert body["script_refs"] == ["scripts/extract.py"]
    assert body["execution_allowed"] is False
    fields = {row["field"]: row for row in body["import_report"]}
    assert fields["license"]["status"] == "stored_raw"
    assert fields["allowed-tools"]["status"] == "stored_raw"
    assert fields["metadata"]["status"] == "stored_raw"
    assert fields["x-custom-field"]["status"] == "unsupported"


def test_skill_md_without_name_uses_directory_not_filename(client):
    """name 缺省时用目录名；绝不能再出现叫「SKILL」的技能。"""
    text = "---\ndescription: 没有 name 的技能\n---\n\n正文。\n"
    response = _import(client, "release-notes/SKILL.md", text.encode("utf-8"))
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["name"] == "release-notes"
    assert body["name_source"] == "directory"

    # 根目录下的 SKILL.md 没有 name：用占位名，而不是把文件名当技能名。
    root = _import(client, "SKILL.md", text.encode("utf-8"))
    assert root.status_code == 201, root.text
    assert root.json()["name"] != "SKILL"
    assert root.json()["name_source"] == "placeholder"


def test_mcp_config_is_rejected_with_tool_center_hint(client):
    payload = json.dumps(
        {"mcpServers": {"filesystem": {"command": "npx", "args": ["-y", "fs"]}}}
    ).encode("utf-8")
    response = _import(client, "mcp-server.json", payload)
    assert response.status_code == 415, response.text
    detail = response.json()["detail"]
    assert "MCP" in detail
    assert "工具中心" in detail


def test_import_rejects_archive_and_oversize(client):
    rejected = _import(client, "bundle.zip", b"PK\x03\x04")
    assert rejected.status_code == 415, rejected.text
    assert "压缩包" in rejected.json()["detail"]

    too_big = _import(client, "big.md", b"# x\n" + b"a" * (256 * 1024))
    assert too_big.status_code == 413, too_big.text


# ---------------------------------------------------------------------- #
# 风险未知 / 高危：必须走确认路径
# ---------------------------------------------------------------------- #
def test_unknown_capability_is_unknown_risk_and_needs_confirmation(client):
    manifest = {"name": "幽灵能力", "capabilities": ["does_not_exist"]}
    response = _import(client, "ghost.json", json.dumps(manifest).encode("utf-8"))
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["risk"] == "unknown", body
    assert body["enabled"] is False
    assert body["missing_capabilities"] == ["does_not_exist"]

    tested = client.post(f"/v1/skills/{body['id']}/test", headers=_headers())
    assert tested.json()["passed"] is False
    assert "does_not_exist" in tested.json()["detail"]

    blocked = client.post(f"/v1/skills/{body['id']}/enable", headers=_headers())
    assert blocked.status_code == 409, blocked.text
    assert "不提供一键全开" in blocked.json()["detail"]

    wrong_ack = client.post(
        f"/v1/skills/{body['id']}/enable",
        headers=_headers(),
        json={"confirm": True, "acknowledge_risk": "low"},
    )
    assert wrong_ack.status_code == 409, wrong_ack.text

    confirmed = client.post(
        f"/v1/skills/{body['id']}/enable",
        headers=_headers(),
        json={"confirm": True, "acknowledge_risk": "unknown"},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["enabled"] is True


def test_high_risk_skill_requires_explicit_confirmation(client, monkeypatch):
    """高危判定来自真实目录口径；这里注入一个 high 能力以覆盖该分支。"""
    from src.gateway import skills as skills_module

    monkeypatch.setattr(
        skills_module,
        "_load_capability_index",
        lambda: (
            {
                "shell_exec": {
                    "risk": "high",
                    "permissions": {"policy.approve"},
                    "tools": ["local_shell"],
                }
            },
            None,
        ),
    )
    manifest = {"name": "跑命令", "capabilities": ["shell_exec"]}
    response = _import(client, "shell.json", json.dumps(manifest).encode("utf-8"))
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["risk"] == "high"
    assert body["permissions"] == ["policy.approve"]
    assert body["enabled"] is False

    assert (
        client.post(f"/v1/skills/{body['id']}/enable", headers=_headers()).status_code
        == 409
    )
    ok = client.post(
        f"/v1/skills/{body['id']}/enable",
        headers=_headers(),
        json={"confirm": True, "acknowledge_risk": "high"},
    )
    assert ok.status_code == 200, ok.text

    disabled = client.post(f"/v1/skills/{body['id']}/disable", headers=_headers())
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["enabled"] is False


def test_no_bulk_enable_endpoint(client):
    response = client.post("/v1/skills/enable-all", headers=_headers(), json={})
    assert response.status_code in (404, 405), response.text


# ---------------------------------------------------------------------- #
# 员工占用：读取真实员工库，删除不静默级联
# ---------------------------------------------------------------------- #
def _write_employees(workspace, rows: dict) -> None:
    (workspace / "liuhao_employees.json").write_text(
        json.dumps({"version": 1, "employees": rows}, ensure_ascii=False),
        encoding="utf-8",
    )


def test_used_by_reads_real_bindings_and_blocks_delete(client, _isolate):
    """used_by 来自真实绑定表；能力交集只作为「可能相关（推导）」出现。"""
    workspace = _isolate
    manifest = {"name": "落盘文件", "capabilities": ["file_write"]}
    created = _import(client, "writer.json", json.dumps(manifest).encode("utf-8")).json()

    _write_employees(
        workspace,
        {
            "张三": {
                "name": "张三",
                "employment_status": "active",
                "default_capabilities": ["file_write"],
            },
            "李四": {
                "name": "李四",
                "employment_status": "active",
                "default_capabilities": ["python_compute"],
            },
        },
    )

    # 没有绑定：used_by 为空；但张三的能力与技能声明有交集 -> 推导为「可能相关」。
    row = {
        item["id"]: item
        for item in client.get("/v1/skills", headers=_headers()).json()["skills"]
    }[created["id"]]
    assert row["used_by"] == [], row
    assert row["related_employees"] == ["张三"], row
    assert row["related_employees_derived"] is True
    assert "推导" in row["related_employees_note"]

    # 绑定不存在的员工 -> 404，不写脏数据。
    ghost = client.post(
        f"/v1/skills/{created['id']}/bind",
        headers=_headers(),
        json={"employee": "王五"},
    )
    assert ghost.status_code == 404, ghost.text

    bound = client.post(
        f"/v1/skills/{created['id']}/bind",
        headers=_headers(),
        json={"employee": "张三"},
    )
    assert bound.status_code == 201, bound.text
    assert bound.json()["binding"]["employee"] == "张三"
    assert bound.json()["binding"]["created_by"]

    # 幂等：重复绑定不新增记录。
    again = client.post(
        f"/v1/skills/{created['id']}/bind",
        headers=_headers(),
        json={"employee": "张三"},
    )
    assert again.status_code == 201
    assert again.json()["binding"]["id"] == bound.json()["binding"]["id"]

    listed = client.get("/v1/skills", headers=_headers()).json()
    row = {item["id"]: item for item in listed["skills"]}[created["id"]]
    assert row["used_by"] == ["张三"], row

    blocked = client.delete(f"/v1/skills/{created['id']}", headers=_headers())
    assert blocked.status_code == 409, blocked.text
    assert "张三" in blocked.json()["detail"]
    assert client.get("/v1/skills", headers=_headers()).json()["total"] == 1

    unbound = client.delete(
        f"/v1/skills/{created['id']}/bind/张三", headers=_headers()
    )
    assert unbound.status_code == 200, unbound.text
    removed = client.delete(f"/v1/skills/{created['id']}", headers=_headers())
    assert removed.status_code == 200, removed.text


def test_binding_survives_store_write_and_lists_by_employee(client, _isolate):
    workspace = _isolate
    created = _create(client, name="周报生成")
    _write_employees(
        workspace,
        {"张三": {"name": "张三", "employment_status": "active"}},
    )
    assert (
        client.post(
            f"/v1/skills/{created['id']}/bind",
            headers=_headers(),
            json={"employee": "张三"},
        ).status_code
        == 201
    )
    bindings = client.get("/v1/skills/bindings", headers=_headers()).json()
    assert bindings["total"] == 1
    assert bindings["bindings"][0]["skill_id"] == created["id"]

    by_employee = client.get(
        "/v1/skills/bindings", params={"employee": "张三"}, headers=_headers()
    ).json()
    assert by_employee["total"] == 1

    per_skill = client.get(
        f"/v1/skills/{created['id']}/bindings", headers=_headers()
    ).json()
    assert per_skill["bindings"][0]["created_by"]


def test_corrupt_store_is_reported_not_empty(client, _isolate):
    workspace = _isolate
    (workspace / "liuhao_skills.json").write_text("{not json", encoding="utf-8")
    response = client.get("/v1/skills", headers=_headers())
    assert response.status_code == 503, response.text
    assert "技能库" in response.json()["detail"]
