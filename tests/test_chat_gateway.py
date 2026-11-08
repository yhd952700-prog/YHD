"""网关会话管理（多用户/session 隔离）测试。

覆盖：get_assistant 幂等性、session 隔离、历史隔离、会话列表/删除。
"""

from __future__ import annotations

import gc
import sqlite3
import threading
import time
import weakref

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
    # get_assistant 现在每次请求都通过 resolve_provider() 重新解析模型（支持
    # 运行时热加载），所以离线桩必须打在**这个真实接缝**上；打旧的
    # get_provider 名已不再参与网关路径。
    monkeypatch.setattr(
        lh_module, "resolve_provider",
        lambda *_a, **_k: MockProvider(name="test", model="mock-model"),
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

    result = chat_module.read_history(session_id="hist")
    assert result["session_id"] == "hist"
    assert result["count"] == 4  # 2 轮 × (user + assistant)
    roles = [h["role"] for h in result["history"]]
    assert roles == ["user", "assistant", "user", "assistant"]
    assert result["history"][0]["content"] == "第一条"


def test_concurrent_tabs_same_session_have_unique_turns(tmp_path, monkeypatch):
    """同一账号两个标签页并发写同一 session：轮次号唯一、历史不丢。

    回归：此前 chat 不加会话锁，两个请求会同时读到同一个 ``self.turn``，
    各自 +1 后写入相同轮次，导致一个 turn 落 4 行、另一些 turn 缺失，
    memory 的 ``turn:N`` 键也互相覆盖。
    """
    recorded = []
    monkeypatch.setattr(
        chat_module, "record_usage", lambda **kwargs: recorded.append(kwargs) or True
    )

    count = 8
    barrier = threading.Barrier(count)
    turns = []
    errors = []
    lock = threading.Lock()

    def send(index: int) -> None:
        try:
            barrier.wait(timeout=30)
            response = chat_module.chat(
                chat_module.ChatRequest(
                    message=f"tab-{index}", session_id="shared", use_knowledge=False
                ),
                principal="liuhao-tabs",
            )
            with lock:
                turns.append(response.turn)
        except Exception as exc:  # noqa: BLE001 - 汇总后统一断言
            with lock:
                errors.append(repr(exc))

    threads = [threading.Thread(target=send, args=(i,)) for i in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)

    assert errors == [], f"并发写同一会话出现异常：{errors[:3]}"
    assert sorted(turns) == list(range(1, count + 1))
    assert len(recorded) == count  # 每个成功轮次各记一行用量

    assistant = chat_module.get_assistant("shared", "liuhao-tabs")
    assert assistant.turn == count
    with sqlite3.connect(str(tmp_path / "conv.db")) as connection:
        rows = connection.execute(
            "SELECT turn, COUNT(*) FROM conversation_turns WHERE principal_id = ? "
            "GROUP BY turn ORDER BY turn",
            (assistant.principal,),
        ).fetchall()
    assert [int(row[0]) for row in rows] == list(range(1, count + 1))
    assert [int(row[1]) for row in rows] == [2] * count


def test_concurrent_cold_requests_construct_session_once(monkeypatch):
    """同一会话的并发冷请求只构造一次（按会话粒度串行，不重复造实例）。"""
    constructions = []
    real = chat_module.LiuHaoAssistant

    def slow_build(*args, **kwargs):
        constructions.append(kwargs.get("name"))
        time.sleep(0.05)
        return real(*args, **kwargs)

    monkeypatch.setattr(chat_module, "LiuHaoAssistant", slow_build)

    count = 8
    barrier = threading.Barrier(count)
    results = []
    errors = []
    lock = threading.Lock()

    def worker() -> None:
        try:
            barrier.wait(timeout=30)
            assistant = chat_module.get_assistant("cold", "user-cold")
            with lock:
                results.append(assistant)
        except Exception as exc:  # noqa: BLE001 - 汇总后统一断言
            with lock:
                errors.append(repr(exc))

    threads = [threading.Thread(target=worker) for _ in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)

    assert errors == [], f"并发冷启动出现异常：{errors[:3]}"
    assert len(constructions) == 1, "同一会话被重复构造"
    assert len({id(item) for item in results}) == 1, "并发请求拿到了不同实例"


def test_cold_construction_of_different_sessions_can_overlap(monkeypatch):
    """不同会话的冷构造可并行：构造不得被一把全局锁串行化。"""
    real = chat_module.LiuHaoAssistant
    state = {"active": 0, "peak": 0}
    state_lock = threading.Lock()

    def tracked_build(*args, **kwargs):
        with state_lock:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
        try:
            time.sleep(0.2)
            return real(*args, **kwargs)
        finally:
            with state_lock:
                state["active"] -= 1

    monkeypatch.setattr(chat_module, "LiuHaoAssistant", tracked_build)

    count = 4
    barrier = threading.Barrier(count)
    results = []
    errors = []
    lock = threading.Lock()

    def worker(index: int) -> None:
        try:
            barrier.wait(timeout=30)
            assistant = chat_module.get_assistant(f"cold-{index}", "user-parallel")
            with lock:
                results.append(assistant)
        except Exception as exc:  # noqa: BLE001 - 汇总后统一断言
            with lock:
                errors.append(repr(exc))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)

    assert errors == [], f"并发冷启动出现异常：{errors[:3]}"
    assert len(results) == count
    assert len({id(item) for item in results}) == count
    assert state["peak"] > 1, "冷会话构造仍被全局锁串行化"


class _CountingProvider(MockProvider):
    """记录创建/关闭次数，用来验证 provider 句柄不随构造竞争累积。"""

    created = 0
    closed = 0
    lock = threading.Lock()

    def __init__(self) -> None:
        super().__init__(name="test", model="mock-model")
        with _CountingProvider.lock:
            _CountingProvider.created += 1

    def close(self) -> None:
        with _CountingProvider.lock:
            _CountingProvider.closed += 1
        super().close()

    @classmethod
    def reset(cls) -> None:
        with cls.lock:
            cls.created = 0
            cls.closed = 0


def _isolate_provider_counts(monkeypatch) -> None:
    _CountingProvider.reset()
    monkeypatch.setattr(
        chat_module, "provider_for_task", lambda *_a, **_k: _CountingProvider()
    )


def test_failed_construction_closes_provider_and_retry_succeeds(monkeypatch):
    """构造失败不能漏 provider，也不能留下构造锁/半成品会话。"""
    _isolate_provider_counts(monkeypatch)
    real = chat_module.LiuHaoAssistant
    failures = {"left": 1}

    def flaky(*args, **kwargs):
        if failures["left"] > 0:
            failures["left"] -= 1
            raise RuntimeError("boom")
        return real(*args, **kwargs)

    monkeypatch.setattr(chat_module, "LiuHaoAssistant", flaky)
    key = chat_module._session_key("user-retry", "retry")

    with pytest.raises(RuntimeError):
        chat_module.get_assistant("retry", "user-retry")

    assert key not in chat_module._sessions
    assert chat_module._session_build_locks == {}, "失败路径必须清掉按会话构造锁"
    assert _CountingProvider.created == 1
    assert _CountingProvider.closed == 1, "构造失败必须关闭已解析的 provider"

    assistant = chat_module.get_assistant("retry", "user-retry")
    assert assistant is not None
    assert _CountingProvider.created == 2
    assert _CountingProvider.closed == 1, "重试成功后只保留一个存活 provider"


def test_discarded_duplicate_construction_hands_provider_to_winner(monkeypatch):
    """「构造期间别人已写入」的丢弃路径：provider 交给胜者、自己这份被回收。"""
    _isolate_provider_counts(monkeypatch)
    real = chat_module.LiuHaoAssistant
    key = chat_module._session_key("user-race", "race")
    captured: dict = {}

    def racing(*args, **kwargs):
        built = real(*args, **kwargs)
        captured["built"] = built
        # 模拟构造期间另一个线程抢先写入（正常路径下由按会话构造锁排除）。
        winner = real(*args, **{**kwargs, "provider": _CountingProvider()})
        captured["winner"] = winner
        with chat_module._sessions_lock:
            chat_module._sessions[key] = winner
            chat_module._session_last_used[key] = time.monotonic()
        return built

    monkeypatch.setattr(chat_module, "LiuHaoAssistant", racing)

    assistant = chat_module.get_assistant("race", "user-race")
    assert assistant is captured["winner"], "并发胜者必须被复用"
    assert assistant is not captured["built"], "被丢弃的那份不得对外返回"

    # 请求的 provider 交给胜者复用（被丢弃实例与胜者指向同一个实例，不是两份）。
    assert assistant.provider is captured["built"].provider
    assert _CountingProvider.created == 2
    assert _CountingProvider.closed == 1, "胜者被替换掉的旧 provider 必须关闭"

    # 被丢弃实例必须真的被 GC 回收（不挂在任何全局表里）。
    built_ref = weakref.ref(captured["built"])
    del captured["built"]
    gc.collect()
    assert built_ref() is None, "被丢弃的 LiuHaoAssistant 仍被引用，存在泄漏"

    chat_module._sessions.clear()
    assistant.close()
    assert _CountingProvider.closed == 2, "会话关闭后不得残留 provider"


def test_second_tab_queues_behind_session_lock_without_error(monkeypatch):
    """同一会话第二个请求是**排队**而不是报错；轮次严格串行递增。"""

    class _SlowProvider(MockProvider):
        def __init__(self) -> None:
            super().__init__(name="slow", model="mock-model")

        def chat(self, messages, **kwargs):
            time.sleep(0.25)
            return "ok"

    _CountingProvider.reset()
    monkeypatch.setattr(
        chat_module, "provider_for_task", lambda *_a, **_k: _SlowProvider()
    )
    monkeypatch.setattr(chat_module, "record_usage", lambda **_kw: True)

    timings: dict = {}
    errors: list = []
    turns: list = []

    def send(name: str) -> None:
        started = time.monotonic()
        try:
            response = chat_module.chat(
                chat_module.ChatRequest(
                    message=f"tab-{name}", session_id="queue", use_knowledge=False
                ),
                principal="liuhao-queue",
            )
            with _CountingProvider.lock:
                timings[name] = time.monotonic() - started
                turns.append(response.turn)
        except Exception as exc:  # noqa: BLE001 - 汇总后统一断言
            errors.append(repr(exc))

    first = threading.Thread(target=send, args=("first",))
    second = threading.Thread(target=send, args=("second",))
    first.start()
    time.sleep(0.05)
    second.start()
    first.join(timeout=30)
    second.join(timeout=30)

    assert errors == [], f"排队请求不应报错：{errors[:3]}"
    assert sorted(turns) == [1, 2], "两个请求必须拿到不同轮次"
    assert timings["second"] >= 0.20, "后到请求应等待前一轮完成（会话锁排队）"


def test_same_message_concurrent_tabs_bill_once(monkeypatch):
    """同一会话、同一内容并发重发：只执行一轮、只记一笔账。"""
    recorded: list = []
    monkeypatch.setattr(
        chat_module, "record_usage", lambda **kwargs: recorded.append(kwargs) or True
    )

    count = 8
    barrier = threading.Barrier(count)
    responses: list = []
    errors: list = []
    lock = threading.Lock()

    def send() -> None:
        try:
            barrier.wait(timeout=30)
            response = chat_module.chat(
                chat_module.ChatRequest(
                    message="同一句话连点八次", session_id="once", use_knowledge=False
                ),
                principal="liuhao-once",
            )
            with lock:
                responses.append(response)
        except Exception as exc:  # noqa: BLE001 - 汇总后统一断言
            with lock:
                errors.append(repr(exc))

    threads = [threading.Thread(target=send) for _ in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert errors == [], f"并发重发出现异常：{errors[:3]}"
    assert len(responses) == count
    assert len(recorded) == 1, "同一内容并发重发只能记一笔用量"
    assert len({item.correlation_id for item in responses}) == 1, (
        "去重复用的结果必须带同一个 correlation_id"
    )
    assert chat_module.get_assistant("once", "liuhao-once").turn == 1

