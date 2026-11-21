"""技能包生态（大升级第二季 V7）：包格式补齐 + 生命周期聊天可达。

要证明的几件事：

- 清单新增 ``author`` / ``dependencies`` 两种格式（SKILL.md / JSON）都能
  解析，形状不对直接报错，不静默丢弃；
- 安装前有预览（``POST /v1/skills/inspect`` + 聊天 inspect 工具）：
  摊开权限声明与依赖核对，且**不产生任何安装**；
- 依赖未满足时安装/升级被拒并点名，什么都不落盘；被依赖的技能卸载
  被拒并点名依赖方；
- 升级后注册表版本/作者/依赖同步新值；
- 聊天写操作（装/升/卸）不带 confirm 只返回待确认，确认后才执行；
  列表/预览工具默认进聊天工具集，写工具受注册闸（B 档默认关）。

全链真机演练（含聊天调用与审批流）见
``upgrade2-evidence/v7_drill.py`` / ``v7-drill-result.json``。
"""

from __future__ import annotations

import io
import json
import zipfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.gateway.skill_import import (
    SkillImportError,
    normalize_dependencies,
    parse_agent_skill,
    parse_json_manifest,
    version_satisfies,
)
from src.gateway.skills import router as skills_router


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
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


def _headers() -> dict:
    return {"Authorization": f"Bearer {_token()}"}


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(skills_router)
    with TestClient(app) as test_client:
        yield test_client


def _zip(entries: dict) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


def _skill_md(name: str, *, version: str = "1.0.0", extra: str = "") -> str:
    return (
        "---\n"
        f"name: {name}\n"
        "description: V7 测试技能\n"
        f"version: {version}\n"
        "author: 测试作者\n"
        f"{extra}"
        "---\n\n正文。\n"
    )


def _post_file(client, url: str, filename: str, payload: bytes):
    return client.post(
        url,
        headers=_headers(),
        files={"file": (filename, payload, "application/octet-stream")},
    )


# ---------------------------------------------------------------------- #
# 清单解析：author / dependencies
# ---------------------------------------------------------------------- #
def test_agent_skill_parses_author_and_dependencies():
    spec = parse_agent_skill(
        "SKILL.md",
        _skill_md(
            "依赖方",
            extra="dependencies:\n  - 基础技能>=1.2.0\n  - 另一个技能\n",
        ),
    )
    assert spec.author == "测试作者"
    assert spec.dependencies == [
        {"name": "基础技能", "version": ">=1.2.0"},
        {"name": "另一个技能", "version": ""},
    ]


def test_agent_skill_author_from_metadata_fallback():
    spec = parse_agent_skill(
        "SKILL.md",
        "---\nname: 元数据作者\ndescription: x\nmetadata:\n  author: 备用作者\n---\n\n正文。\n",
    )
    assert spec.author == "备用作者"


def test_json_manifest_parses_author_and_dependencies():
    document = {
        "name": "JSON技能",
        "description": "x",
        "version": "2.0.0",
        "author": "JSON作者",
        "dependencies": [{"name": "基础技能", "min_version": "1.0.0"}],
    }
    spec = parse_json_manifest("skill.json", json.dumps(document))
    assert spec.author == "JSON作者"
    assert spec.dependencies == [{"name": "基础技能", "version": ">=1.0.0"}]


def test_dependencies_bad_shape_rejected():
    with pytest.raises(SkillImportError):
        normalize_dependencies([{"version": ">=1.0.0"}])
    with pytest.raises(SkillImportError):
        normalize_dependencies(["技能名>=abc"])
    with pytest.raises(SkillImportError):
        normalize_dependencies("不是列表")


def test_version_satisfies_table():
    assert version_satisfies("1.2.0", ">=1.0.0") is True
    assert version_satisfies("0.9.0", ">=1.0.0") is False
    assert version_satisfies("1.0", ">=1.0.0") is True
    assert version_satisfies("", ">=1.0.0") is None
    assert version_satisfies("1.0.0", "") is True


# ---------------------------------------------------------------------- #
# HTTP：预览 / 依赖核对 / 升级同步 / 卸载守卫
# ---------------------------------------------------------------------- #
def test_inspect_previews_without_installing(client):
    payload = _zip({"SKILL.md": _skill_md("预览技能")})
    response = _post_file(client, "/v1/skills/inspect", "pkg.zip", payload)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == "预览技能"
    assert body["author"] == "测试作者"
    assert body["dependencies_satisfied"] is True
    assert body["package_hash"]
    listing = client.get("/v1/skills", headers=_headers())
    assert listing.json() in ([], {"skills": []}) or not listing.json().get(
        "skills", listing.json()
    )


def test_import_refused_when_dependency_missing(client):
    payload = _zip(
        {
            "SKILL.md": _skill_md(
                "依赖方", extra="dependencies:\n  - 不存在的技能>=2.0.0\n"
            )
        }
    )
    response = _post_file(client, "/v1/skills/import", "pkg.zip", payload)
    assert response.status_code == 400, response.text
    assert "不存在的技能" in response.json()["detail"]
    listing = client.get("/v1/skills", headers=_headers()).json()
    rows = listing.get("skills", listing) if isinstance(listing, dict) else listing
    assert rows == []


def _install_zip(client, name: str, *, version: str = "1.0.0", extra: str = ""):
    payload = _zip({"SKILL.md": _skill_md(name, version=version, extra=extra)})
    response = _post_file(client, "/v1/skills/import", "pkg.zip", payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_import_ok_when_dependency_satisfied_and_fields_kept(client):
    _install_zip(client, "基础技能", version="1.2.0")
    body = _install_zip(
        client, "依赖方", extra="dependencies:\n  - 基础技能>=1.0.0\n"
    )
    assert body["author"] == "测试作者"
    assert body["dependencies"] == [
        {"name": "基础技能", "version": ">=1.0.0"}
    ]


def test_upgrade_syncs_version_fields(client):
    created = _install_zip(client, "可升级技能", version="1.0.0")
    skill_id = created["id"]
    payload = _zip({"SKILL.md": _skill_md("可升级技能", version="2.0.0")})
    response = _post_file(
        client, f"/v1/skills/{skill_id}/upgrade", "pkg.zip", payload
    )
    assert response.status_code == 200, response.text
    assert response.json()["version"] == "2.0.0"


def test_upgrade_wrong_name_refused(client):
    created = _install_zip(client, "原技能")
    payload = _zip({"SKILL.md": _skill_md("另一个技能", version="2.0.0")})
    response = _post_file(
        client, f"/v1/skills/{created['id']}/upgrade", "pkg.zip", payload
    )
    assert response.status_code == 400, response.text


def test_delete_depended_skill_conflict(client):
    base = _install_zip(client, "基础技能", version="1.2.0")
    _install_zip(client, "依赖方", extra="dependencies:\n  - 基础技能>=1.0.0\n")
    response = client.delete(f"/v1/skills/{base['id']}", headers=_headers())
    assert response.status_code == 409, response.text
    assert "依赖方" in response.json()["detail"]


# ---------------------------------------------------------------------- #
# 聊天工具：确认门 + 注册闸
# ---------------------------------------------------------------------- #
def _tools(include_disabled: bool):
    from src.ai.tools.kernel_tools import make_tools

    return {
        tool.name: tool
        for tool in make_tools("test-boss", include_disabled=include_disabled)
    }


def test_chat_manage_tools_registration_gate():
    default_tools = _tools(include_disabled=False)
    assert "list_skill_packages" in default_tools
    assert "inspect_skill_package" in default_tools
    # B 档默认关：写工具默认不进聊天工具集，技能页开启后才出现。
    assert "install_skill_package" not in default_tools
    all_tools = _tools(include_disabled=True)
    for name in (
        "install_skill_package",
        "upgrade_skill_package",
        "uninstall_skill_package",
    ):
        assert name in all_tools


def test_chat_install_confirm_gate_and_lifecycle(tmp_path):
    tools = _tools(include_disabled=True)
    package_dir = tmp_path / "pkg"
    package_dir.mkdir()
    (package_dir / "SKILL.md").write_text(
        _skill_md("聊天安装技能"), encoding="utf-8"
    )
    pending = json.loads(tools["install_skill_package"].fn(path=str(package_dir)))
    assert pending["status"] == "pending_confirmation"
    assert pending["package"]["name"] == "聊天安装技能"
    listed = json.loads(tools["list_skill_packages"].fn())
    assert listed["skills"] == []

    installed = json.loads(
        tools["install_skill_package"].fn(path=str(package_dir), confirm=True)
    )
    assert installed["status"] == "installed"
    skill_id = installed["skill_id"]

    un_pending = json.loads(tools["uninstall_skill_package"].fn(skill_id=skill_id))
    assert un_pending["status"] == "pending_confirmation"
    uninstalled = json.loads(
        tools["uninstall_skill_package"].fn(skill_id=skill_id, confirm=True)
    )
    assert uninstalled["status"] == "uninstalled"
    listed = json.loads(tools["list_skill_packages"].fn())
    assert listed["skills"] == []
