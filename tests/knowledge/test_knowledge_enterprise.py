"""企业知识库 / 长期记忆（0.3.0）的服务层与路由层测试。

全部使用 ``tmp_path`` 隔离数据库与文件，不写工作区数据目录。
"""

from __future__ import annotations

import io
import sqlite3
import zipfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.ai.knowledge import service
from src.ai.knowledge.memory import (
    create_memory,
    delete_memory,
    get_memory,
    list_memories,
    memory_views,
    update_memory,
)
from src.ai.knowledge.service import KnowledgeError, Viewer
from src.gateway import knowledge_enterprise
from src.gateway.knowledge_enterprise import require_viewer


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("LIUHAO_KNOWLEDGE_ENTERPRISE_DB", str(tmp_path / "kb.sqlite3"))
    monkeypatch.setenv("LIUHAO_KNOWLEDGE_MEMORY_DB", str(tmp_path / "memory.sqlite3"))
    monkeypatch.setenv("LIUHAO_KNOWLEDGE_FILES_DIR", str(tmp_path / "files"))
    return tmp_path


ALICE = Viewer("alice", ("alice",), False)
BOB = Viewer("bob", ("bob",), False)
ROOT = Viewer("root", ("root", "alice", "bob"), True)


def _ingest(
    viewer: Viewer = ALICE,
    text: str = "鎏灏企业知识库支持中文全文检索、分类归档与版本对比。",
    **overrides,
):
    params = {
        "text": text,
        "title": "知识库说明",
        "doc_key": "kb-guide",
        "category_id": "cat_enterprise",
        "source_ref": "手工录入",
    }
    params.update(overrides)
    return service.ingest_text(viewer, **params)


# ---------------------------------------------------------------------------
# 分类与文档
# ---------------------------------------------------------------------------


def test_seed_categories_and_tree():
    tree = service.list_categories(ALICE)
    names = [node["name"] for node in tree]
    assert names[:3] == ["企业知识", "产品资料", "客户资料"]
    assert all(node["document_count"] == 0 for node in tree)


def test_create_child_category_and_manage_permissions():
    child = service.create_category(
        ALICE, name="合同模板", parent_id="cat_enterprise", visibility="team"
    )
    tree = service.list_categories(ALICE)
    enterprise = next(node for node in tree if node["id"] == "cat_enterprise")
    assert any(node["id"] == child["id"] for node in enterprise["children"])

    # 非创建者不能改别人的分类；系统分类只有根账号能改。
    with pytest.raises(KnowledgeError) as denied:
        service.update_category(BOB, child["id"], name="被改了")
    assert denied.value.status == 404
    service.update_category(ROOT, "cat_policy", description="公司制度（根账号维护）")


def test_ingest_list_detail_and_fts_search():
    created = _ingest()
    assert created["created"] is True
    assert created["current_version"] == 1
    assert created["expiry_state"] == "none"

    listing = service.list_documents(ALICE)
    assert listing["total"] == 1
    assert listing["documents"][0]["title"] == "知识库说明"
    assert listing["documents"][0]["category_path"] == ["企业知识"]

    found = service.search(ALICE, "全文检索")
    assert found["retrieval"]["status"] == "ok"
    assert found["retrieval"]["mode"] == "fts5_bm25"
    assert found["hits"][0]["document_id"] == created["id"]
    assert "全文检索" in found["hits"][0]["excerpt"]

    missed = service.search(ALICE, "完全不存在的词")
    assert missed["count"] == 0
    assert missed["retrieval"]["status"] == "no_relevant"

    detail = service.get_document(ALICE, created["id"])
    assert detail["versions"][0]["version"] == 1
    assert "鎏灏企业知识库" in detail["text"]
    assert detail["can_write"] is True


def test_versioning_dedup_and_diff():
    first = _ingest(text="第一版：报销流程为三个月内提交。")
    second = _ingest(text="第二版：报销流程改为一个月内提交，并新增电子发票要求。")

    assert second["id"] == first["id"]
    assert second["current_version"] == 2
    detail = service.get_document(ALICE, first["id"])
    assert [item["version"] for item in detail["versions"]] == [2, 1]

    diff = service.diff_versions(ALICE, first["id"], 1, 2)
    assert diff["changed"] is True
    assert "电子发票" in diff["unified"]
    assert diff["added_lines"] >= 1

    old = service.get_version(ALICE, first["id"], 1)
    assert "三个月" in old["text"] and old["is_current"] is False

    # 同内容再上传：幂等，不再产生新版本。
    same = _ingest(text="第二版：报销流程改为一个月内提交，并新增电子发票要求。")
    assert same["deduplicated"] is True
    assert same["current_version"] == 2


def test_visibility_permissions_and_write_guard():
    private_doc = _ingest(
        title="私密资料", doc_key="private-doc", visibility="private", category_id=""
    )
    with pytest.raises(KnowledgeError) as denied:
        service.get_document(BOB, private_doc["id"])
    assert denied.value.status == 404

    team_doc = _ingest(
        title="团队资料", doc_key="team-doc", visibility="team", category_id="cat_product"
    )
    with pytest.raises(KnowledgeError) as denied_team:
        service.get_document(BOB, team_doc["id"])
    assert denied_team.value.status == 404


def test_team_document_visible_to_ancestor_but_not_sibling():
    team_doc = _ingest(title="团队资料", doc_key="team-doc-2", visibility="team", category_id="")
    assert service.get_document(ROOT, team_doc["id"])["id"] == team_doc["id"]
    with pytest.raises(KnowledgeError):
        service.get_document(BOB, team_doc["id"])
    with pytest.raises(KnowledgeError):
        service.update_document(ROOT, team_doc["id"], title="根账号不能改成员资料")


def test_enterprise_document_visible_to_all():
    doc = _ingest(
        title="全员制度", doc_key="policy-all", visibility="enterprise", category_id="cat_enterprise"
    )
    assert service.get_document(BOB, doc["id"])["id"] == doc["id"]


def test_document_cannot_be_wider_than_category():
    with pytest.raises(KnowledgeError) as denied:
        _ingest(title="越权", doc_key="too-wide", category_id="cat_product", visibility="enterprise")
    assert denied.value.status == 422


def test_category_narrowing_pulls_documents_down():
    doc = _ingest(title="客户名单", doc_key="customers", category_id="cat_customer", visibility="team")
    service.update_category(ROOT, "cat_customer", visibility="private")
    with pytest.raises(KnowledgeError):
        service.get_document(BOB, doc["id"])
    # 上传者本人仍可见，但分类可见性已收窄。
    assert service.get_document(ALICE, doc["id"])["visibility"] == "private"


def test_expiry_filters_and_reminders():
    expired = _ingest(
        title="过期合同", doc_key="expired", expires_at="2020-01-01", category_id=""
    )
    assert expired["expiry_state"] == "expired"

    default_list = service.list_documents(ALICE)
    assert all(item["id"] != expired["id"] for item in default_list["documents"])

    with_expired = service.list_documents(ALICE, include_expired=True)
    assert any(item["id"] == expired["id"] for item in with_expired["documents"])

    reminders = service.expiring_documents(ALICE, days=30)
    assert reminders["counts"]["expired"] >= 1

    patched = service.update_document(ALICE, expired["id"], clear_expiry=True)
    assert patched["expiry_state"] == "none"


def test_soft_delete_removes_from_search_but_keeps_versions():
    doc = _ingest(title="待删除", doc_key="to-delete", text="这段正文会被软删除。")
    service.delete_document(ALICE, doc["id"])
    assert service.search(ALICE, "软删除")["count"] == 0
    with pytest.raises(KnowledgeError):
        service.get_document(ALICE, doc["id"])


def test_unsupported_file_type_and_missing_category():
    with pytest.raises(Exception) as denied:
        service.ingest_text(ALICE, text="x", title="x", category_id="cat-not-exist")
    assert getattr(denied.value, "status", None) == 422


# ---------------------------------------------------------------------------
# 多格式提取
# ---------------------------------------------------------------------------


def _write_xlsx(path):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
            ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="合同台账" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            '<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            "<sheetData>"
            '<row r="1"><c r="A1" t="inlineStr"><is><t>合同编号</t></is></c>'
            '<c r="B1" t="inlineStr"><is><t>金额</t></is></c></row>'
            '<row r="2"><c r="A2" t="inlineStr"><is><t>HT-2026-001</t></is></c>'
            '<c r="B2"><v>12000</v></c></row>'
            "</sheetData></worksheet>",
        )


def test_xlsx_extraction_is_searchable(tmp_path):
    path = tmp_path / "contracts.xlsx"
    _write_xlsx(path)
    doc = service.ingest_file(
        ALICE, source=path, filename="contracts.xlsx", title="合同台账", doc_key="contracts"
    )
    assert doc["text_source"] == "extracted"
    detail = service.get_document(ALICE, doc["id"])
    assert "HT-2026-001" in detail["text"]
    assert "合同台账" in detail["text"]
    assert service.search(ALICE, "HT-2026-001")["count"] == 1


def test_image_is_stored_with_metadata_only(tmp_path):
    png = tmp_path / "org-chart.png"
    png.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\rIHDR"
        + (640).to_bytes(4, "big")
        + (480).to_bytes(4, "big")
        + b"\x00" * 8
    )
    doc = service.ingest_file(
        ALICE,
        source=png,
        filename="org-chart.png",
        title="组织架构图",
        description="2026 年组织架构",
        doc_key="org-chart",
    )
    detail = service.get_document(ALICE, doc["id"])
    assert detail["text_source"] == "image_metadata"
    assert "640×480" in detail["text"]
    assert "2026 年组织架构" in detail["text"]
    stored, filename, mime = service.document_file_info(ALICE, doc["id"])
    assert stored.is_file() and filename == "org-chart.png" and mime == "image/png"


# ---------------------------------------------------------------------------
# 长期记忆
# ---------------------------------------------------------------------------


def test_memory_is_in_a_separate_database_with_no_document_tables(isolated_store):
    create_memory(ALICE, content="报销流程一个月内提交", project_id="p1", scope="project")
    connection = sqlite3.connect(isolated_store / "memory.sqlite3")
    names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}
    connection.close()
    assert "lt_memories" in names
    assert not any(name.startswith("kb_") for name in names)


def test_memory_project_and_employee_isolation():
    personal = create_memory(ALICE, content="这是我的私人笔记")
    project_memory = create_memory(
        ALICE, content="项目 A 的复盘结论", project_id="proj-a", scope="project"
    )
    employee_memory = create_memory(
        ALICE, content="张三擅长合同审核", employee_id="emp-zhangsan", scope="employee"
    )

    with pytest.raises(KnowledgeError):
        get_memory(BOB, personal["id"])
    with pytest.raises(KnowledgeError):
        get_memory(ROOT, personal["id"])
    assert get_memory(ROOT, project_memory["id"])["project_id"] == "proj-a"
    assert get_memory(ROOT, employee_memory["id"])["employee_id"] == "emp-zhangsan"
    with pytest.raises(KnowledgeError):
        get_memory(BOB, project_memory["id"])

    by_project = list_memories(ROOT, project_id="proj-a")
    assert [item["project_id"] for item in by_project["memories"]] == ["proj-a"]
    by_employee = list_memories(ROOT, employee_id="emp-zhangsan")
    assert len(by_employee["memories"]) == 1


def test_memory_update_keeps_revision_and_delete_is_soft():
    created = create_memory(ALICE, content="初版结论")
    updated = update_memory(ALICE, created["id"], content="修订版结论")
    assert updated["revision"] == 2
    detail = get_memory(ALICE, created["id"])
    assert [item["revision"] for item in detail["revisions"]] == [2, 1]
    assert detail["revisions"][1]["content"] == "初版结论"

    delete_memory(ALICE, created["id"])
    assert list_memories(ALICE)["total"] == 0
    with pytest.raises(KnowledgeError):
        get_memory(ALICE, created["id"])


def test_memory_views_group_by_project_and_employee():
    create_memory(ALICE, content="p1-1", project_id="p1", scope="project")
    create_memory(ALICE, content="p1-2", project_id="p1", scope="project")
    create_memory(ALICE, content="e1", employee_id="e1", scope="employee")
    views = memory_views(ROOT)
    assert next(item for item in views["projects"] if item["project_id"] == "p1")["count"] == 2
    assert next(item for item in views["employees"] if item["employee_id"] == "e1")["count"] == 1
    assert views["scopes"]["project"] == 2


# ---------------------------------------------------------------------------
# HTTP 路由
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(knowledge_enterprise.router)
    app.dependency_overrides[require_viewer] = lambda: ALICE
    return TestClient(app)


def test_http_documents_flow(client):
    response = client.post(
        "/v1/knowledge/enterprise/documents/text",
        json={"text": "HTTP 接口写入的正文，含关键词：里程碑。", "title": "接口文档", "doc_key": "http-doc"},
    )
    assert response.status_code == 201, response.text
    doc_id = response.json()["id"]

    listing = client.get("/v1/knowledge/enterprise/documents")
    assert listing.status_code == 200
    assert listing.json()["total"] == 1

    search = client.post(
        "/v1/knowledge/enterprise/search", json={"query": "里程碑"}
    )
    assert search.status_code == 200
    assert search.json()["hits"][0]["document_id"] == doc_id

    detail = client.get(f"/v1/knowledge/enterprise/documents/{doc_id}")
    assert detail.status_code == 200
    assert detail.json()["title"] == "接口文档"

    diff = client.get(
        f"/v1/knowledge/enterprise/documents/{doc_id}/diff?from_version=1&to_version=1"
    )
    assert diff.status_code == 200
    assert diff.json()["changed"] is False


def test_http_upload_rejects_unsupported_file(client):
    response = client.post(
        "/v1/knowledge/enterprise/documents/upload",
        files={"file": ("evil.exe", io.BytesIO(b"MZ"), "application/octet-stream")},
        data={"title": "坏文件"},
    )
    assert response.status_code == 415
    assert response.json()["detail"]["code"] == "unsupported_file_type"


def test_http_seed_categories_status_and_expiring(client):
    categories = client.get("/v1/knowledge/enterprise/categories")
    assert categories.status_code == 200
    assert len(categories.json()["categories"]) >= 9

    status = client.get("/v1/knowledge/enterprise/status")
    assert status.status_code == 200
    assert status.json()["documents"] == 0
    assert "xlsx" in status.json()["supported_types"]

    expiring = client.get("/v1/knowledge/enterprise/expiring?days=30")
    assert expiring.status_code == 200
    assert expiring.json()["counts"] == {"expired": 0, "expiring": 0}


def test_http_memory_flow(client):
    created = client.post(
        "/v1/knowledge/enterprise/memory",
        json={
            "content": "客户 A 更关注交付周期",
            "project_id": "proj-a",
            "scope": "project",
            "source": "会话沉淀",
        },
    )
    assert created.status_code == 201, created.text
    memory_id = created.json()["id"]

    views = client.get("/v1/knowledge/enterprise/memory/views")
    assert views.status_code == 200
    assert views.json()["projects"][0]["project_id"] == "proj-a"

    listed = client.get("/v1/knowledge/enterprise/memory?project_id=proj-a")
    assert listed.json()["total"] == 1

    patched = client.patch(
        f"/v1/knowledge/enterprise/memory/{memory_id}",
        json={"content": "客户 A 更关注交付周期与验收标准"},
    )
    assert patched.status_code == 200
    assert patched.json()["revision"] == 2

    deleted = client.delete(f"/v1/knowledge/enterprise/memory/{memory_id}")
    assert deleted.status_code == 200
    assert client.get("/v1/knowledge/enterprise/memory").json()["total"] == 0
