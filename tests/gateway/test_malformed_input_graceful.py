"""异常输入「优雅拒绝」回归 —— 模糊测试暴露的 H1~M9 / L1~L2。

全部通过真实 HTTP（TestClient），独立临时工作区，不碰正在运行的实例。
每个用例都对应一条线上缺陷：改前红、改后绿。
"""

from __future__ import annotations

import json
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


def _headers(subject: str = "boss", scope: str = "L0") -> Dict[str, str]:
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


def _deep_body(depth: int = 1200) -> bytes:
    return (
        b'{"text":"deep","x":' + b"[" * depth + b"]" * depth + b"}"
    )


# ── H1：超深嵌套 JSON 必须在解析阶段被 422，而不是 500 ─────────────

@pytest.mark.parametrize(
    "path",
    [
        "/v1/chat",
        "/v1/chat/stream",
        "/v1/goals",
        "/v1/goals/assign",
        "/v1/employees",
        "/v1/projects",
        "/v1/models",
        "/v1/accounts/register",
        "/v1/memory/entries",
        "/v1/knowledge/documents",
        "/knowledge/documents",
    ],
)
def test_deep_nested_json_is_422_not_500(client, path):
    response = client.post(
        path, content=_deep_body(), headers={**_headers(), "content-type": "application/json"}
    )
    assert response.status_code == 422, response.text
    assert "嵌套层级过深" in response.text
    assert response.text.isprintable()


# ── H2/H3：422 文案必须中文、字段名可读、input 截断 ──────────────

def test_missing_field_is_chinese_with_readable_label(client):
    response = client.post("/v1/projects", json={}, headers=_headers())
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["detail"][0]["msg"].startswith("缺少必填字段：")
    assert "name" not in body["detail"][0]["msg"]
    assert "项目名称" in body["detail"][0]["msg"]


def test_wrong_type_is_chinese(client):
    response = client.post("/v1/projects", json={"name": 123}, headers=_headers())
    assert response.status_code == 422, response.text
    assert "必须是文本" in response.text
    assert "Input should be" not in response.text


def test_body_not_object_is_chinese(client):
    response = client.post("/v1/projects", json=[1, 2, 3], headers=_headers())
    assert response.status_code == 422, response.text
    assert "请求体必须是 JSON 对象" in response.text


def test_error_body_truncates_huge_input(client):
    response = client.post("/v1/projects", json={"name": "a" * 8001}, headers=_headers())
    assert response.status_code == 422, response.text
    assert len(response.content) < 1500, f"错误体仍然过大: {len(response.content)} bytes"
    assert "a" * 8001 not in response.text
    assert "已截断" in response.text


# ── M1：非法 UTF-8 中文、与格式错误同状态码 ─────────────────────

def test_invalid_utf8_is_chinese_400(client):
    response = client.post(
        "/v1/projects", content=b"\xff\xfe{}",
        headers={**_headers(), "content-type": "application/json"},
    )
    assert response.status_code == 400, response.text
    assert "UTF-8" in response.text
    assert "There was an error parsing the body" not in response.text


def test_malformed_json_is_chinese_400(client):
    response = client.post(
        "/v1/projects", content=b'{"name": "x"',
        headers={**_headers(), "content-type": "application/json"},
    )
    assert response.status_code == 400, response.text
    assert "不是合法的 JSON" in response.text


# ── M2：纯空白内容 422 ────────────────────────────────────────

def test_whitespace_only_chat_message_rejected(client):
    response = client.post("/v1/chat", json={"message": "   \t\n"}, headers=_headers())
    assert response.status_code == 422, response.text
    assert "不能为空" in response.text


def test_whitespace_only_goal_rejected(client):
    response = client.post("/v1/goals", json={"natural_language": "\u3000"}, headers=_headers())
    assert response.status_code == 422, response.text
    assert "不能为空" in response.text


def test_whitespace_only_memory_rejected(client):
    response = client.post("/v1/memory/entries", json={"text": "   "}, headers=_headers())
    assert response.status_code == 422, response.text
    assert "不能为空" in response.text


def test_whitespace_only_knowledge_document_rejected(client):
    response = client.post("/v1/knowledge/documents", json={"text": "   "}, headers=_headers())
    assert response.status_code == 422, response.text
    assert "不能为空" in response.text


def test_whitespace_only_employee_name_rejected(client):
    response = client.post(
        "/v1/employees",
        json={"name": "\u3000", "role_description": "测试", "default_capabilities": []},
        headers=_headers(),
    )
    assert response.status_code == 422, response.text
    assert "不能为空" in response.text


# ── M3：NUL / 控制字符入口拒绝 + 落盘失败回滚内存 ───────────────

def test_nul_in_project_name_rejected(client):
    response = client.post("/v1/projects", json={"name": "a\u0000b"}, headers=_headers())
    assert response.status_code == 422, response.text
    assert "控制字符" in response.text


def test_persist_failure_rolls_back_memory(client, monkeypatch):
    import src.gateway.projects as projects_module

    def _boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(projects_module, "atomic_write_text", _boom)
    response = client.post("/v1/projects", json={"name": "来回滚项目"}, headers=_headers())
    assert response.status_code == 200, response.text
    assert response.json()["persisted"] is False

    monkeypatch.undo()
    listing = client.get("/v1/projects", headers=_headers())
    assert listing.status_code == 200, listing.text
    names = [item["name"] for item in listing.json().get("projects", [])]
    assert "来回滚项目" not in names


# ── M7：深嵌套 metadata 422 且不落库，检索不被打穿 ─────────────

def test_deep_metadata_rejected_and_search_still_ok(client):
    nested: dict = {"leaf": True}
    for _ in range(300):
        nested = {"n": nested}
    response = client.post(
        "/v1/knowledge/documents",
        json={"text": "健康文档", "metadata": nested},
        headers=_headers(),
    )
    assert response.status_code == 422, response.text
    assert "嵌套层级过深" in response.text

    search = client.post(
        "/v1/knowledge/search", json={"query": "健康文档", "top_k": 4}, headers=_headers()
    )
    assert search.status_code == 200, search.text


def test_poisoned_index_row_is_skipped_not_breaking_search(tmp_path):
    """已落库的坏 metadata 不能把整条检索链路打成 400。"""

    import sqlite3

    from src.knowledge.vector_store import PersistentVectorStore as SqliteVectorStore

    db_path = tmp_path / "vectors.sqlite3"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE knowledge_vectors (doc_id TEXT PRIMARY KEY, embedding TEXT NOT NULL,"
        " metadata TEXT NOT NULL DEFAULT '{}', updated_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )
    conn.execute(
        "CREATE TABLE knowledge_document_hashes (content_hash TEXT PRIMARY KEY,"
        " document_id TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )
    conn.execute(
        "CREATE TABLE knowledge_index_meta (key TEXT PRIMARY KEY,"
        " value TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )
    deep_metadata = '{"a":' * 1200 + "1" + "}" * 1200
    conn.execute(
        "INSERT INTO knowledge_vectors (doc_id, embedding, metadata) VALUES (?,?,?)",
        ("poison", json.dumps([0.1] * 8), deep_metadata),
    )
    conn.commit()
    conn.close()

    store = SqliteVectorStore(db_path=str(db_path))
    hits = store.search([0.1] * 8, top_k=4)
    assert all(hit[0] != "poison" for hit in hits)


# ── M5：body 预检先于身份校验（缺必填给 422，不是 401/503） ───────

def test_missing_body_field_beats_auth(client):
    response = client.post("/v1/models", json={})
    assert response.status_code == 422, response.text
    assert "缺少必填字段" in response.text


# ── M6：429 带 Retry-After ───────────────────────────────────

def test_login_throttle_returns_retry_after(client):
    payload = {"principal": "boss", "secret": "wrong-secret"}
    for _ in range(5):
        client.post("/v1/auth/login", json=payload)
    sixth = client.post("/v1/auth/login", json=payload)
    assert sixth.status_code == 429, sixth.text
    assert sixth.headers.get("Retry-After")
    assert sixth.headers.get("X-RateLimit-Limit") == "5"
    assert "登录失败次数过多" in sixth.text


# ── M4：memory / goal 幂等 ────────────────────────────────────

def test_memory_write_is_idempotent_in_window(client):
    payload = {"text": "幂等记忆-唯一内容", "source": "user", "tags": ["a"]}
    first = client.post("/v1/memory/entries", json=payload, headers=_headers())
    second = client.post("/v1/memory/entries", json=payload, headers=_headers())
    assert first.status_code == 201 and second.status_code == 201, second.text
    assert first.json()["entry"]["id"] == second.json()["entry"]["id"]
    assert second.json().get("deduplicated") is True


def test_goal_accepts_client_request_id_and_dedupes(client):
    payload = {
        "natural_language": "写一个 hello.txt 文件",
        "scope": "L1",
        "plan_mode": "auto",
        "background": True,
        "client_request_id": "goal-idem-1",
    }
    first = client.post("/v1/goals", json=payload, headers=_headers())
    second = client.post("/v1/goals", json=payload, headers=_headers())
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["goal_id"] == second.json()["goal_id"]
    assert second.json().get("deduplicated") is True


# ── M8/M9：超长字段 ───────────────────────────────────────────

def test_long_session_id_rejected(client):
    response = client.post(
        "/v1/chat", json={"message": "hi", "session_id": "s" * 10000}, headers=_headers()
    )
    assert response.status_code == 422, response.text
    assert "会话 ID" in response.text


def test_long_upload_filename_rejected(client):
    filename = "a" * 3004 + ".txt"
    response = client.post(
        "/v1/knowledge/documents/upload",
        files={"file": (filename, b"hello world", "text/plain")},
        headers=_headers(),
    )
    assert response.status_code == 422, response.text
    assert "文件名过长" in response.text


# ── L1：全角数字 query 中文提示 ─────────────────────────────────

def test_fullwidth_digit_query_is_chinese(client):
    response = client.get("/v1/memory/entries?limit=１００", headers=_headers())
    assert response.status_code == 422, response.text
    assert "半角数字" in response.text
    assert "int_parsing" not in response.text.split('"type"')[0]
