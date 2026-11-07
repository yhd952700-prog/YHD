"""数据可携带性：人可读导出、隐私边界、记忆/知识库回导。

这里刻意不 mock "导出成功"：ZIP 真的解压、Markdown 真的包含对话和时间、
记忆真的写回 SQLite 并按内容去重、知识库回导真的走 ingest 契约。
"""
from __future__ import annotations

import io
import json
import zipfile
from types import SimpleNamespace

import pytest

from src.ai.conversation_store import ConversationStore
from src.gateway.account_context import AccountContext
from src.kernels.identity._accounts import AccountNode


def _context(principal: str = "owner1", *, root: bool = False) -> AccountContext:
    node = AccountNode(
        principal=principal,
        parent_id=None if root else "root1",
        depth=0 if root else 1,
        root_id=principal if root else "root1",
    )
    return AccountContext(
        principal=principal, node=node, subtree=(principal,), standalone=False
    )


class _AuditRecorder:
    def __init__(self) -> None:
        self.events = []

    def record_event(self, **kwargs):
        self.events.append(kwargs)


@pytest.fixture()
def isolated_stores(monkeypatch, tmp_path):
    memory_path = tmp_path / "account_memory.sqlite3"
    usage_path = tmp_path / "account_usage.sqlite3"
    monkeypatch.setenv("LIUHAO_ACCOUNT_MEMORY_DB", str(memory_path))
    monkeypatch.setenv("LIUHAO_ACCOUNT_USAGE_DB", str(usage_path))
    store = ConversationStore(str(tmp_path / "conversations.db"))
    monkeypatch.setattr(
        "src.ai.conversation_store._conversation_store", store
    )
    monkeypatch.setattr(
        "src.gateway.data_portability._conversation_store", lambda: store
    )
    recorder = _AuditRecorder()
    monkeypatch.setattr(
        "src.gateway.data_portability.get_identity_manager", lambda: recorder
    )
    return SimpleNamespace(store=store, recorder=recorder, tmp_path=tmp_path)


def test_memory_export_keeps_timestamps_and_import_is_idempotent(isolated_stores):
    from src.gateway.account_memory import _connect
    from src.gateway.data_portability import collect_memories, import_memories

    with _connect() as connection:
        connection.execute(
            "INSERT INTO account_memory_entries (id, owner, text, source, tags, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("m1", "owner1", "我偏好简洁的回答", "user", '["偏好"]', "2026-11-01T09:00:00"),
        )
        # 别人的记忆：一键导出不能带出去。
        connection.execute(
            "INSERT INTO account_memory_entries (id, owner, text, source, tags, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("m2", "other", "别人的私事", "user", "[]", "2026-11-02T09:00:00"),
        )
        connection.commit()

    entries = collect_memories("owner1")
    assert [entry["text"] for entry in entries] == ["我偏好简洁的回答"]
    assert entries[0]["created_at"] == "2026-11-01T09:00:00"

    # 模拟"换一台机器"：先清掉本地这条，再导入两次验证去重。
    with _connect() as connection:
        connection.execute(
            "DELETE FROM account_memory_entries WHERE owner = ?", ("owner1",)
        )
        connection.commit()

    first = import_memories(_context("owner1"), entries)
    second = import_memories(_context("owner1"), entries)
    assert first["imported"] == 1
    assert second["imported"] == 0
    assert second["skipped"] == 1

    with _connect() as connection:
        rows = connection.execute(
            "SELECT owner, text, created_at FROM account_memory_entries ORDER BY owner"
        ).fetchall()
    assert [(row["owner"], row["text"]) for row in rows] == [
        ("other", "别人的私事"),
        ("owner1", "我偏好简洁的回答"),
    ]


def test_export_archive_is_readable_and_private(isolated_stores):
    from src.gateway import data_portability as portability

    store: ConversationStore = isolated_stores.store
    with store._lock:
        store._conn.execute(
            "INSERT INTO conversation_turns (principal_id, turn, role, content, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("liuhao-owner1-conv1", 1, "user", "帮我写一个导出脚本", 1793000000.0),
        )
        store._conn.execute(
            "INSERT INTO conversation_turns (principal_id, turn, role, content, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("liuhao-owner1-conv1", 1, "assistant", "好的，先从会话开始。", 1793000003.0),
        )
        # 别人的会话同样不能被带走。
        store._conn.execute(
            "INSERT INTO conversation_turns (principal_id, turn, role, content, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("liuhao-other-conv1", 1, "user", "别人的秘密", 1793000000.0),
        )
        store._conn.commit()

    from src.gateway.account_memory import _connect

    with _connect() as connection:
        connection.execute(
            "INSERT INTO account_memory_entries (id, owner, text, source, tags, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("m1", "owner1", "喜欢 Markdown", "user", '["格式"]', "2026-11-01T09:00:00"),
        )
        connection.commit()

    monkeypatch_returns_own_file = lambda _ctx: (  # noqa: E731
        [{"path": "reports/mine.md", "size": 3, "content": b"abc"}],
        {"files": 1, "bytes": 3, "skipped": []},
    )
    monkeypatch_knowledge_unavailable = lambda *args, **kwargs: (_ for _ in ()).throw(  # noqa: E731
        RuntimeError("knowledge unavailable")
    )
    import unittest.mock as mock

    with mock.patch.object(portability, "collect_workspace_files", monkeypatch_returns_own_file), \
            mock.patch.object(portability, "export_knowledge", monkeypatch_knowledge_unavailable), \
            mock.patch.object(portability, "collect_employees", lambda _ctx: []), \
            mock.patch.object(portability, "collect_usage", lambda _principal: {
                "principal": "owner1", "calls": 2, "tokens_in": 10, "tokens_out": 5,
                "total_tokens": 15, "by_key_owner": [],
            }):
        raw, manifest = portability.build_export_archive(_context("owner1"))

    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        names = set(archive.namelist())
        readme = archive.read("README.md").decode("utf-8")
        conversation = archive.read("conversations/001-owner1-conversation1.md").decode("utf-8") \
            if "conversations/001-owner1-conversation1.md" in names else ""
        if not conversation:
            candidates = [name for name in names if name.startswith("conversations/") and name.endswith(".md")]
            assert candidates, names
            conversation = archive.read(candidates[0]).decode("utf-8")
        memories_md = archive.read("memories.md").decode("utf-8")
        manifest_json = archive.read("manifest.json").decode("utf-8")
        settings = json.loads(archive.read("settings.json").decode("utf-8"))
        assert "帮我写一个导出脚本" in conversation
        assert "鎏灏" in conversation
        assert "2026-11-01T09:00:00" in memories_md
        assert "别人的秘密" not in conversation
        assert "别人的私事" not in archive.read("memories.json").decode("utf-8")
        assert "files/reports/mine.md" in names
        assert "markdown" in readme.lower()
        assert manifest["contents"]["knowledge_status"] == "unavailable"
        assert manifest["privacy"]["other_accounts_data"] is False
        assert settings["account"]["principal"] == "owner1"
        assert settings["memory_custody"]["enabled"] is False
        assert "d:\\" not in manifest_json.lower()
        assert "c:\\" not in manifest_json.lower()


def test_usage_csv_neutralises_formula_injection():
    from src.gateway.data_portability import _usage_csv

    stream = _usage_csv({
        "principal": "=cmd|' /C calc'!A0",
        "calls": 1,
        "tokens_in": 2,
        "tokens_out": 3,
        "total_tokens": 5,
        "by_key_owner": [{"key_owner": "@evil", "calls": 1, "tokens_in": 2,
                          "tokens_out": 3, "total_tokens": 5}],
    })
    assert "'=cmd" in stream
    assert "'@evil" in stream


def test_root_export_does_not_take_members_conversations(isolated_stores):
    from src.gateway import data_portability as portability

    store: ConversationStore = isolated_stores.store
    with store._lock:
        store._conn.execute(
            "INSERT INTO conversation_turns (principal_id, turn, role, content, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("liuhao-root1-own", 1, "user", "根自己的对话", 1793000000.0),
        )
        store._conn.execute(
            "INSERT INTO conversation_turns (principal_id, turn, role, content, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("liuhao-member1-private", 1, "user", "成员的私密对话", 1793000000.0),
        )
        store._conn.commit()

    import unittest.mock as mock

    root_context = AccountContext(
        principal="root1",
        node=AccountNode(principal="root1", parent_id=None, depth=0, root_id="root1"),
        subtree=("root1", "member1"),
        standalone=False,
    )
    with mock.patch.object(portability, "collect_workspace_files", lambda _ctx: ([], {"files": 0, "bytes": 0, "skipped": []})), \
            mock.patch.object(portability, "export_knowledge", lambda *a, **k: ([], {})), \
            mock.patch.object(portability, "collect_employees", lambda _ctx: []), \
            mock.patch.object(portability, "collect_usage", lambda _principal: {
                "principal": "root1", "calls": 0, "tokens_in": 0, "tokens_out": 0,
                "total_tokens": 0, "by_key_owner": [],
            }):
        raw, _ = portability.build_export_archive(root_context)

    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        conversations = [
            archive.read(name).decode("utf-8")
            for name in archive.namelist()
            if name.startswith("conversations/") and name.endswith(".md")
        ]
    assert any("根自己的对话" in text for text in conversations)
    assert all("成员的私密对话" not in text for text in conversations)


class _FakeKnowledgeService:
    def __init__(self, documents=None):
        self.documents = documents or []
        self.chunks = {}
        self.ingested = []

    def status(self):
        return {"enabled": True, "reason": None}

    def list_documents(self, limit=50, offset=0, scope=None):
        rows = self.documents[offset:offset + limit]
        return {"documents": rows, "total": len(self.documents)}

    def document_chunks(self, document_id, scope=None):
        return {"chunks": [{"text": text} for text in self.chunks.get(document_id, [])]}

    def ingest(self, text, doc_id=None, metadata=None, owner_account_id=None, **_kwargs):
        self.ingested.append(
            {
                "text": text,
                "doc_id": doc_id,
                "metadata": metadata,
                "owner": owner_account_id,
            }
        )
        return {"deduplicated": False}


def test_knowledge_export_can_be_reimported_without_owner_prefix():
    from src.gateway.data_portability import export_knowledge, import_knowledge

    fake = _FakeKnowledgeService([
        {"document_id": "owner1--handbook", "title": "员工手册", "source": "handbook.md"}
    ])
    fake.chunks["owner1--handbook"] = ["第一章 总则", "第二章 考勤"]
    documents, manifest = export_knowledge(_context("owner1"), service=fake)
    assert manifest["documents"] == 1
    exported = documents[0]
    assert exported["document_id"] == "handbook"
    assert exported["text"] == "第一章 总则\n\n第二章 考勤"

    target = _FakeKnowledgeService()
    result = import_knowledge(_context("owner1"), documents, service=target)
    assert result["imported"] == 1
    assert target.ingested[0]["doc_id"] == "handbook"
    assert target.ingested[0]["owner"] == "owner1"
    assert target.ingested[0]["text"] == "第一章 总则\n\n第二章 考勤"


def test_conversation_markdown_shows_speaker_and_time():
    from src.gateway.data_portability import _conversation_markdown

    markdown = _conversation_markdown(
        "s1",
        [
            {"role": "user", "content": "你好", "created_at": 1793000000.0},
            {"role": "assistant", "content": "你好，我在。", "created_at": 1793000002.0},
        ],
    )
    assert "# 会话：s1" in markdown
    assert "## 我 · " in markdown
    assert "## 鎏灏 · " in markdown
    assert "你好，我在。" in markdown
