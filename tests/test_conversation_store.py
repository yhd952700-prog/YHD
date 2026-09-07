"""会话持久化（ConversationStore）测试。

验证跨进程重启恢复多轮对话历史的底层存储能力：append/load 往返、
跨实例恢复、recent 截断、principal 隔离、clear、轮数追踪。
"""

from __future__ import annotations

from src.ai.conversation_store import ConversationStore


def make_store(tmp_path) -> ConversationStore:
    return ConversationStore(db_path=str(tmp_path / "conv.db"))


def test_append_load_roundtrip(tmp_path):
    s = make_store(tmp_path)
    s.append("alice", 1, "user", "hi")
    s.append("alice", 1, "assistant", "hello")
    assert s.load("alice") == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
    assert s.get_last_turn("alice") == 1


def test_cross_instance_restore(tmp_path):
    """同一 db 路径的第二个实例（模拟进程重启）应读到第一个实例的内容。"""
    db = tmp_path / "conv.db"
    s1 = ConversationStore(db_path=str(db))
    s1.append("alice", 1, "user", "who are you")
    s1.append("alice", 1, "assistant", "I am liuhao")

    s2 = ConversationStore(db_path=str(db))
    assert s2.load("alice") == [
        {"role": "user", "content": "who are you"},
        {"role": "assistant", "content": "I am liuhao"},
    ]
    assert s2.get_last_turn("alice") == 1


def test_load_recent_preserves_order(tmp_path):
    s = make_store(tmp_path)
    for i in range(5):
        s.append("alice", i + 1, "user", f"q{i}")
        s.append("alice", i + 1, "assistant", f"a{i}")
    recent = s.load_recent("alice", 4)
    assert recent == [
        {"role": "user", "content": "q3"},
        {"role": "assistant", "content": "a3"},
        {"role": "user", "content": "q4"},
        {"role": "assistant", "content": "a4"},
    ]


def test_clear_removes_only_that_principal(tmp_path):
    s = make_store(tmp_path)
    s.append("alice", 1, "user", "hi")
    s.append("bob", 1, "user", "yo")
    s.clear("alice")
    assert s.load("alice") == []
    assert s.get_last_turn("alice") == 0
    # bob 不受影响
    assert s.load("bob") == [{"role": "user", "content": "yo"}]


def test_principal_isolation(tmp_path):
    s = make_store(tmp_path)
    s.append("alice", 1, "user", "alice msg")
    s.append("bob", 1, "user", "bob msg")
    assert [m["content"] for m in s.load("alice")] == ["alice msg"]
    assert [m["content"] for m in s.load("bob")] == ["bob msg"]


def test_stats_counts_turns(tmp_path):
    s = make_store(tmp_path)
    s.append("alice", 1, "user", "a")
    s.append("alice", 2, "user", "b")
    s.append("bob", 1, "user", "c")
    st = s.stats()
    assert st["total_turns"] == 3
    assert st["principals"] == {"alice": 2, "bob": 1}
