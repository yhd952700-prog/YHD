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


def test_get_conversation_store_is_singleton_under_concurrent_first_use(
    tmp_path, monkeypatch
):
    """冷会话构造已并发化：首次取全局存储必须只构造一次。

    不加锁时多个线程会各自打开同一个 sqlite 库，实测直接撞
    ``database is locked`` 并留下多份连接。
    """
    import threading
    import time

    import src.ai.conversation_store as store_module

    constructed = []
    real_cls = store_module.ConversationStore

    class SlowStore(real_cls):
        def __init__(self, *args, **kwargs):
            time.sleep(0.02)  # 放大竞争窗口
            constructed.append(self)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(store_module, "ConversationStore", SlowStore)
    monkeypatch.setattr(store_module, "DEFAULT_DB_PATH", str(tmp_path / "singleton.db"))
    monkeypatch.setattr(store_module, "_conversation_store", None)

    results = []
    barrier = threading.Barrier(16)

    def fetch() -> None:
        barrier.wait(timeout=10)
        results.append(store_module.get_conversation_store())

    threads = [threading.Thread(target=fetch) for _ in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert len(results) == 16
    assert len(constructed) == 1, "并发首次调用只能构造一个全局存储"
    assert len({id(item) for item in results}) == 1


def test_append_turn_writes_both_rows_with_single_audit(tmp_path, monkeypatch):
    """一轮问答一次事务写入两行，且只产生一条 append 审计。"""
    import src.kernels.audit as audit_kernel

    actions = []
    real_log_event = audit_kernel.log_event

    def counting_log_event(*args, **kwargs):
        details = kwargs.get("details") or {}
        if details.get("action") == "p5.conversation.append":
            actions.append(details)
        return real_log_event(*args, **kwargs)

    monkeypatch.setattr(audit_kernel, "log_event", counting_log_event)

    s = make_store(tmp_path)
    s.append_turn("alice", 3, "问", "答")

    assert s.load("alice") == [
        {"role": "user", "content": "问"},
        {"role": "assistant", "content": "答"},
    ]
    assert s.get_last_turn("alice") == 3
    assert len(actions) == 1, "一轮问答只应有一条 append 审计"


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
