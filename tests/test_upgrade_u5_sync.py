"""U5 多端同步：双数据目录真演练。

A 建数据 → 打包 → B 合并验在位 → B 改 → 反向打包 → A 验；
同刻冲突留痕不覆盖；墓碑删除传播；聊天 append-only 逐条合并；
知识文档（假服务，同接口）同步；配对令牌只存哈希；篡改包被拒；
同步默认关拒绝运行。
"""
from __future__ import annotations

import json
import os
from contextlib import contextmanager

import pytest

from src.ai.conversation_store import ConversationStore
from src.sync import service as sync_service
from src.sync import state as sync_state
from src.sync.adapters import SyncAdapter
from src.sync.engine import verify_bundle
from src.workbench.store import WorkbenchStore

OWNER = "sync-owner"


@contextmanager
def data_dir(path):
    old = os.environ.get("LIUHAO_DATA_DIR")
    os.environ["LIUHAO_DATA_DIR"] = str(path)
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("LIUHAO_DATA_DIR", None)
        else:
            os.environ["LIUHAO_DATA_DIR"] = old


class FakeKnowledge:
    """与真实知识服务同接口的内存实现（list/chunks/ingest/delete）。"""

    def __init__(self):
        self.docs = {}  # doc_id -> {title, source, text, updated_at}

    def list_documents(self, limit=200, offset=0, scope=None):
        rows = [
            {"document_id": doc_id, "title": d["title"], "source": d["source"],
             "updated_at": d["updated_at"]}
            for doc_id, d in sorted(self.docs.items())
        ]
        return {"documents": rows[offset:offset + limit], "total": len(rows)}

    def document_chunks(self, document_id, scope=None):
        doc = self.docs[document_id]
        return {"chunks": [{"text": doc["text"]}] if doc["text"] else []}

    def ingest(self, text, doc_id=None, metadata=None, owner_account_id=None):
        existing = self.docs.get(doc_id)
        self.docs[doc_id] = {
            "title": (metadata or {}).get("title", ""),
            "source": (metadata or {}).get("source", ""),
            "text": text,
            "updated_at": "2026-01-01T00:00:00+00:00" if existing is None else existing["updated_at"],
        }
        return {"deduplicated": False}

    def delete_document(self, document_id, scope=None):
        self.docs.pop(document_id, None)
        return {"deleted": True}


class Side:
    def __init__(self, root):
        self.root = root
        self.workbench = WorkbenchStore(str(root / "workbench.db"))
        self.conversations = ConversationStore(str(root / "conversations.db"))
        self.knowledge = FakeKnowledge()
        self.adapter = SyncAdapter(
            OWNER,
            workbench=self.workbench,
            conversations=self.conversations,
            knowledge_service=self.knowledge,
        )

    def bundle(self, since=""):
        with data_dir(self.root):
            return sync_service.build_local_bundle(OWNER, since, adapter=self.adapter)

    def apply(self, bundle):
        with data_dir(self.root):
            return sync_service.apply_remote_bundle(bundle, OWNER, adapter=self.adapter)


@pytest.fixture()
def sides(tmp_path):
    return Side(tmp_path / "a"), Side(tmp_path / "b")


def _seed_side_a(a: Side) -> None:
    a.workbench.upsert_group_for_sync(OWNER, {
        "id": "group-1", "name": "销售组", "folded": False, "order_index": 0,
        "created_at": "2026-01-01T09:00:00+00:00", "updated_at": "2026-01-01T09:00:00+00:00",
    })
    a.workbench.upsert_bot_for_sync(OWNER, {
        "id": "bot-1", "name": "小销", "job": "销售助理", "group_id": "group-1",
        "created_at": "2026-01-01T09:00:00+00:00", "updated_at": "2026-01-01T09:00:00+00:00",
    })
    a.conversations.append("liuhao-sync-owner-s1", 1, "user", "你好", owner_principal=OWNER)
    a.conversations.append("liuhao-sync-owner-s1", 1, "assistant", "你好，请问需要什么帮助？", owner_principal=OWNER)
    a.knowledge.docs["doc-1"] = {
        "title": "报价须知", "source": "manual", "text": "报价有效期 7 天。",
        "updated_at": "2026-01-01T09:00:00+00:00",
    }
    with data_dir(a.root):
        from src.gateway.account_settings import _settings_path

        path = _settings_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"allow_self_registration": True}, fh)


def test_a_to_b_full_sync(sides):
    a, b = sides
    _seed_side_a(a)
    bundle = a.bundle()
    verify_bundle(bundle)
    report = b.apply(bundle)
    assert report["applied"] >= 4  # 分组 + Bot + 知识 + 设置（会话走 append-only 单独计数）

    bot = b.workbench.get_bot([OWNER], "bot-1")
    assert bot is not None and bot["name"] == "小销" and bot["group_id"] == "group-1"
    group = b.workbench.get_group([OWNER], "group-1")
    assert group is not None and group["name"] == "销售组"
    turns = b.conversations.load_turns("liuhao-sync-owner-s1")
    assert [t["content"] for t in turns] == ["你好", "你好，请问需要什么帮助？"]
    assert b.knowledge.docs["doc-1"]["text"] == "报价有效期 7 天。"
    with data_dir(b.root):
        from src.gateway.account_settings import load_settings

        assert load_settings().get("allow_self_registration") is True


def test_b_edit_flows_back_to_a(sides):
    a, b = sides
    _seed_side_a(a)
    b.apply(a.bundle())
    b.workbench.upsert_bot_for_sync(OWNER, {
        "id": "bot-1", "name": "小销 Pro", "job": "销售助理", "group_id": "group-1",
        "created_at": "2026-01-01T09:00:00+00:00", "updated_at": "2026-01-02T09:00:00+00:00",
    })
    b.conversations.append("liuhao-sync-owner-s1", 2, "user", "再帮我查一下库存", owner_principal=OWNER)
    report = a.apply(b.bundle())
    assert report["applied"] >= 1
    assert a.workbench.get_bot([OWNER], "bot-1")["name"] == "小销 Pro"
    contents = [t["content"] for t in a.conversations.load_turns("liuhao-sync-owner-s1")]
    assert "再帮我查一下库存" in contents and len(contents) == 3


def test_same_timestamp_conflict_kept_and_logged(sides):
    a, b = sides
    _seed_side_a(a)
    b.apply(a.bundle())
    # 两边同一时刻各改各的：同 updated_at、不同内容 → 冲突，保留本地并留痕。
    b.workbench.upsert_bot_for_sync(OWNER, {
        "id": "bot-1", "name": "B 的改法", "job": "销售助理", "group_id": "group-1",
        "created_at": "2026-01-01T09:00:00+00:00", "updated_at": "2026-01-03T09:00:00+00:00",
    })
    a.workbench.upsert_bot_for_sync(OWNER, {
        "id": "bot-1", "name": "A 的改法", "job": "销售助理", "group_id": "group-1",
        "created_at": "2026-01-01T09:00:00+00:00", "updated_at": "2026-01-03T09:00:00+00:00",
    })
    report = a.apply(b.bundle())
    assert a.workbench.get_bot([OWNER], "bot-1")["name"] == "A 的改法"
    assert any(c["id"] == "bot-1" for c in report["conflicts"])
    with data_dir(a.root):
        conflicts = sync_state.read_conflicts()
    assert any(c.get("id") == "bot-1" for c in conflicts)


def test_tombstone_delete_propagates(sides):
    a, b = sides
    _seed_side_a(a)
    b.apply(a.bundle())
    assert b.workbench.get_bot([OWNER], "bot-1") is not None
    with data_dir(a.root):
        sync_state.record_tombstone("workbench_bots", "bot-1", "2026-01-05T09:00:00+00:00")
    report = b.apply(a.bundle())
    assert report["deleted_applied"] >= 1
    assert b.workbench.get_bot([OWNER], "bot-1") is None


def test_conversation_divergent_turn_conflicts_not_overwritten(sides):
    a, b = sides
    _seed_side_a(a)
    b.apply(a.bundle())
    # B 在同一 (turn, role) 写了不同内容（分叉）→ A 合并时只留痕不覆盖。
    b.conversations.append("liuhao-sync-owner-s1", 1, "user", "被改掉的问题", owner_principal=OWNER)
    # append 是追加语义：B 本地会出现两条 (1, user)；同步包按 B 的 load_turns
    # 顺序携带，A 侧同 key 已有不同内容 → 冲突留痕。
    report = a.apply(b.bundle())
    a_contents = [t["content"] for t in a.conversations.load_turns("liuhao-sync-owner-s1")]
    assert a_contents[0] == "你好"
    assert any(c["kind"] == "conversations" for c in report["conflicts"])


def test_tampered_bundle_rejected(sides):
    a, _b = sides
    _seed_side_a(a)
    bundle = a.bundle()
    bundle["entities"][0]["payload"]["name"] = "被篡改"
    with pytest.raises(ValueError):
        verify_bundle(bundle)


def test_pairing_token_stored_hashed_only(tmp_path):
    with data_dir(tmp_path):
        token = sync_state.create_pairing_token(OWNER)
        assert sync_state.verify_pairing_token(token) is True
        assert sync_state.verify_pairing_token("wrong-token") is False
        raw = (tmp_path / "sync" / "state.json").read_text(encoding="utf-8")
        assert token not in raw  # 明文令牌绝不落盘


def test_sync_disabled_by_default_refuses(tmp_path):
    with data_dir(tmp_path):
        state = sync_state.load_state()
        assert state["enabled"] is False
        sync_state.add_peer("peer-x", "http://127.0.0.1:9", "tok")
        with pytest.raises(RuntimeError, match="还没开启"):
            sync_service.sync_with_peer("peer-x", OWNER)


def test_chat_tool_reachability(tmp_path):
    with data_dir(tmp_path):
        from src.ai.tools.kernel_tools import make_tools

        tools = {t.name: t for t in make_tools(OWNER, include_disabled=True)}
        assert "device_sync" in tools
        out = json.loads(tools["device_sync"].fn(action="status"))
        assert out["status"] == "ok" and out["enabled"] is False
        out = json.loads(tools["device_sync"].fn(action="pair"))
        assert out["status"] == "ok" and out["pairing_token"]
        out = json.loads(tools["device_sync"].fn(action="enable"))
        assert out["enabled"] is True
