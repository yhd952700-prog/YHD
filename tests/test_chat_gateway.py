"""网关会话管理（多用户/session 隔离）测试。

覆盖：get_assistant 幂等性、session 隔离、历史隔离、会话列表/删除。
"""

from __future__ import annotations

import pytest

from src.ai import liuhao as lh_module
from src.ai.conversation_store import ConversationStore
from src.ai.providers import MockProvider
from src.gateway import chat as chat_module


@pytest.fixture(autouse=True)
def isolate(monkeypatch, tmp_path):
    # 清空进程内会话表，隔离会话存储，mock provider（离线）。
    chat_module._sessions.clear()
    store = ConversationStore(db_path=str(tmp_path / "conv.db"))
    monkeypatch.setattr(lh_module, "get_conversation_store", lambda: store)
    monkeypatch.setattr(chat_module, "get_conversation_store", lambda: store)
    monkeypatch.setattr(
        lh_module, "get_provider",
        lambda: MockProvider(name="test", model="mock-model"),
    )
    yield
    chat_module._sessions.clear()


def test_get_assistant_idempotent():
    a1 = chat_module.get_assistant("s1")
    a2 = chat_module.get_assistant("s1")
    assert a1 is a2


def test_get_assistant_isolated_by_session():
    a = chat_module.get_assistant("alice")
    b = chat_module.get_assistant("bob")
    assert a is not b
    assert a.principal == "liuhao-alice"
    assert b.principal == "liuhao-bob"


def test_session_history_isolated():
    """不同 session 的对话历史互不污染。"""
    a = chat_module.get_assistant("alice")
    b = chat_module.get_assistant("bob")
    a.chat("hello")
    assert a.turn == 1
    assert b.turn == 0
    assert b.history == []


def test_list_and_delete_sessions():
    chat_module.get_assistant("a")
    chat_module.get_assistant("b")
    assert set(chat_module.list_sessions()) == {"a", "b"}
    assert chat_module.delete_session("a") is True
    assert chat_module.list_sessions() == ["b"]
    assert chat_module.delete_session("nonexistent") is False


def test_delete_session_clears_persisted_history():
    """删除会话后，重建同 session 应恢复为空历史（持久化已清）。"""
    a = chat_module.get_assistant("temp")
    a.chat("will be deleted")
    assert a.turn == 1
    assert chat_module.delete_session("temp") is True
    # 重建同一 session：历史已清空，从零开始。
    a2 = chat_module.get_assistant("temp")
    assert a2.turn == 0
    assert a2.history == []


def test_chat_history_endpoint_reads_persisted():
    """history 端点从持久化 store 读历史（跨后端重启/实例重建有效）。"""
    a = chat_module.get_assistant("hist")
    a.chat("第一条")
    a.chat("第二条")

    # 模拟后端重启：清空进程内会话表，仅靠持久化 store 读历史。
    chat_module._sessions.clear()

    result = chat_module.chat_history(session_id="hist")
    assert result["session_id"] == "hist"
    assert result["count"] == 4  # 2 轮 × (user + assistant)
    roles = [h["role"] for h in result["history"]]
    assert roles == ["user", "assistant", "user", "assistant"]
    assert result["history"][0]["content"] == "第一条"

