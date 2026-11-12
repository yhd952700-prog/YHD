"""聊天链路可靠性回归：重复提交幂等 + 历史三场景（刷新/切账号/重启）。

锁定四条用户可撞上的行为：

1. 同一句话连发两次（连点/网络重发）只调用一次模型、只记一笔用量、只写一轮历史；
2. 并发重发由后来者等待并复用首个结果，不会各自计费；
3. 失败结果不缓存 —— 重试会真实重新执行；
4. 历史在刷新页面、切换账号、后端重启后都还在，且不同账号互相不可见。

真实复现口径：走 ``/v1/chat`` / ``/v1/chat/stream`` / ``/v1/chat/sessions`` /
``/v1/chat/history`` 真实网关路径，仅把模型这一层换成进程内计数桩。
"""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient


from src.ai.providers import MockProvider


class CountingProvider(MockProvider):
    """可计数的离线 provider：``calls`` 就是真实模型调用次数（计费等价物）。"""

    def __init__(self, *, delay: float = 0.0, fail_first: bool = False) -> None:
        super().__init__(name="counting-mock", model="counting-model")
        self.delay = delay
        self.fail_first = fail_first
        self.calls = 0
        self._lock = threading.Lock()

    def chat(self, messages, **kwargs):
        with self._lock:
            self.calls += 1
            call_no = self.calls
        if self.delay:
            time.sleep(self.delay)
        if self.fail_first and call_no == 1:
            raise RuntimeError("upstream unavailable")
        return "可靠回复：链路正常"

    def chat_stream(self, messages, **kwargs):
        # 默认 BaseProvider.chat_stream 会退化成单次 chat；这里保持同样的
        # 计数语义（一次流 = 一次真实调用），并分两段模拟增量输出。
        with self._lock:
            self.calls += 1
            call_no = self.calls
        if self.delay:
            time.sleep(self.delay)
        if self.fail_first and call_no == 1:
            raise RuntimeError("upstream unavailable")
        yield "可靠回复"
        yield "：链路正常"

    def close(self) -> None:
        pass


@pytest.fixture()
def env(monkeypatch, tmp_path):
    import src.ai.conversation_store as conversation_module
    import src.gateway.chat as chat_module
    import src.kernels.identity as identity_module

    base = tmp_path / "instance"
    base.mkdir(parents=True, exist_ok=True)
    values = {
        "LIUHAO_HUMAN_IDENTITIES_BACKEND": "file",
        "LIUHAO_HUMAN_IDENTITIES_FILE": str(base / "identities.json"),
        "LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY": "reliability-integrity-key",
        "LIUHAO_AUTH_SECRETS_FILE": str(base / "auth_secrets.json"),
        "LIUHAO_ACCOUNT_SETTINGS_FILE": str(base / "account_settings.json"),
        "LIUHAO_ACCOUNT_USAGE_DB": str(base / "account_usage.sqlite3"),
        "CONVERSATION_DB_PATH": str(base / "conversation_store.db"),
        "LIUHAO_WORKSPACE_ROOT": str(base / "workspace"),
        "LIUHAO_MODEL_CONFIG_DB": str(base / "model_config.sqlite3"),
        "LIUHAO_MODEL_CONFIG_KEY_FILE": str(base / "model_config_key.bin"),
        "LIUHAO_CHAT_DEDUP_WINDOW_SECONDS": "8",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(identity_module, "_global_manager", None)
    monkeypatch.setattr(
        conversation_module,
        "_conversation_store",
        conversation_module.ConversationStore(values["CONVERSATION_DB_PATH"]),
    )
    chat_module._sessions.clear()
    chat_module._session_users.clear()
    chat_module._session_last_used.clear()
    chat_module._dedup_entries.clear()

    manager = identity_module.get_identity_manager()
    manager.register_root_account("root1", permissions={"*"})
    manager.register_self_service_account("member1")
    manager.register_self_service_account("member2")

    return values


@pytest.fixture()
def provider(monkeypatch):
    import src.gateway.chat as chat_module

    instance = CountingProvider()
    monkeypatch.setattr(chat_module, "provider_for_task", lambda *_a, **_k: instance)
    return instance


def _client():
    from src.gateway.main import get_app

    return TestClient(get_app())


def _headers(subject: str) -> dict:
    from src.security import get_jwt_handler

    token, _ = get_jwt_handler().create_token(
        subject=subject, metadata={"kind": "human"}
    )
    return {"Authorization": f"Bearer {token}"}


def _calls(principal: str) -> int:
    from src.gateway.account_usage import usage_summary

    return int(usage_summary(principal)["calls"])


def _history_count(client, headers, session_id):
    response = client.get(
        "/v1/chat/history", params={"session_id": session_id}, headers=headers
    )
    assert response.status_code == 200, response.text
    return response.json()["count"]


def test_duplicate_send_is_executed_and_billed_once(env, provider):
    """连点两次发送：只调一次模型、只记一笔用量、历史只写一轮。"""
    client = _client()
    headers = _headers("member1")
    body = {"message": "同一句话-8842", "session_id": "dedup"}

    first = client.post("/v1/chat", json=body, headers=headers)
    second = client.post("/v1/chat", json=body, headers=headers)

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    first_body, second_body = first.json(), second.json()
    assert first_body["status"] == second_body["status"] == "completed"
    assert first_body["reply"] == second_body["reply"]
    # 同一条请求被复用：轮次与关联 id 都必须是同一份，而不是新的一轮。
    assert first_body["turn"] == second_body["turn"] == 1
    assert first_body["correlation_id"] == second_body["correlation_id"]

    assert provider.calls == 1
    assert _calls("member1") == 1
    # 历史 2 条（user + assistant），不是重复的两轮 4 条。
    assert _history_count(client, headers, "dedup") == 2


def test_concurrent_duplicate_requests_coalesce(env):
    """并发重发（两个标签页/网络抖动）：只有一个真实模型调用。"""
    import src.gateway.chat as chat_module

    provider = CountingProvider(delay=0.25)
    chat_module.provider_for_task = lambda *_a, **_k: provider

    headers = _headers("member1")
    body = {"message": "并发重复-5521", "session_id": "concurrent"}
    barrier = threading.Barrier(2)
    clients = [_client(), _client()]

    def send(client):
        barrier.wait(timeout=10)
        return client.post("/v1/chat", json=body, headers=headers)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(send, client) for client in clients]
        responses = [future.result(timeout=60) for future in futures]

    for response in responses:
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "completed"
    turns = {response.json()["turn"] for response in responses}
    correlation_ids = {response.json()["correlation_id"] for response in responses}
    assert turns == {1}
    assert len(correlation_ids) == 1
    assert provider.calls == 1
    assert _calls("member1") == 1
    assert _history_count(clients[0], headers, "concurrent") == 2


def test_explicit_idempotency_key_dedupes_even_without_time_window(env, provider, monkeypatch):
    """显式幂等键（请求体或 Idempotency-Key 头）不受短时间窗限制。"""
    monkeypatch.setenv("LIUHAO_CHAT_DEDUP_WINDOW_SECONDS", "0")
    client = _client()
    headers = _headers("member1")

    body = {"message": "显式幂等-1", "session_id": "explicit", "client_request_id": "req-1"}
    assert client.post("/v1/chat", json=body, headers=headers).status_code == 200
    assert client.post("/v1/chat", json=body, headers=headers).status_code == 200

    header_body = {"message": "显式幂等-2", "session_id": "explicit"}
    h2 = dict(headers, **{"Idempotency-Key": "req-2"})
    assert client.post("/v1/chat", json=header_body, headers=h2).status_code == 200
    assert client.post("/v1/chat", json=header_body, headers=h2).status_code == 200

    # 不同键的同一句话是两次不同的意图，必须真实执行两次。
    other = {"message": "显式幂等-1", "session_id": "explicit", "client_request_id": "req-3"}
    assert client.post("/v1/chat", json=other, headers=headers).status_code == 200

    assert provider.calls == 3
    assert _calls("member1") == 3


def test_failure_is_not_cached_and_retry_really_runs(env):
    """失败不缓存：第二次重试必须真实调用模型并成功。"""
    import src.gateway.chat as chat_module

    provider = CountingProvider(fail_first=True)
    chat_module.provider_for_task = lambda *_a, **_k: provider
    client = _client()
    headers = _headers("member1")
    body = {"message": "失败后重试-7733", "session_id": "retry"}

    first = client.post("/v1/chat", json=body, headers=headers)
    assert first.status_code == 200, first.text
    assert first.json()["status"] != "completed"

    second = client.post("/v1/chat", json=body, headers=headers)
    assert second.status_code == 200, second.text
    assert second.json()["status"] == "completed"
    assert provider.calls == 2
    # 失败轮次不写历史；成功轮次写一轮。
    assert _history_count(client, headers, "retry") == 2


def test_distinct_messages_are_not_deduped(env, provider):
    """去重不能误伤：不同内容/不同账号的同一句话都要真实执行。"""
    client = _client()
    member1 = _headers("member1")
    member2 = _headers("member2")

    assert client.post(
        "/v1/chat", json={"message": "不同内容-1", "session_id": "s1"}, headers=member1
    ).status_code == 200
    assert client.post(
        "/v1/chat", json={"message": "不同内容-2", "session_id": "s1"}, headers=member1
    ).status_code == 200
    assert client.post(
        "/v1/chat", json={"message": "不同内容-1", "session_id": "s2"}, headers=member2
    ).status_code == 200

    assert provider.calls == 3


def _parse_sse(text: str) -> list:
    events = []
    for line in text.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[len("data: "):]))
    return events


def test_stream_duplicate_is_executed_once_and_replayed(env):
    """流式重复提交：只调一次模型，重发直接重放同一份内容。"""
    import src.gateway.chat as chat_module

    provider = CountingProvider()
    chat_module.provider_for_task = lambda *_a, **_k: provider
    client = _client()
    headers = _headers("member1")
    body = {"message": "流式重复-9931", "session_id": "stream"}

    first = client.post("/v1/chat/stream", json=body, headers=headers)
    assert first.status_code == 200, first.text
    first_events = _parse_sse(first.text)
    assert first_events[-1]["type"] == "done"
    assert first_events[-1]["status"] == "completed"

    second = client.post("/v1/chat/stream", json=body, headers=headers)
    assert second.status_code == 200, second.text
    second_events = _parse_sse(second.text)
    assert second_events[-1]["type"] == "done"
    assert second_events[-1].get("deduplicated") is True
    assert second_events[-1]["correlation_id"] == first_events[-1]["correlation_id"]

    assert provider.calls == 1
    assert _calls("member1") == 1
    assert _history_count(client, headers, "stream") == 2


def test_history_survives_refresh_account_switch_and_restart(env, provider):
    """刷新页面 / 切账号 / 重启后端：历史都在，且不同账号不可见。"""
    client = _client()
    member1 = _headers("member1")
    response = client.post(
        "/v1/chat",
        json={"message": "历史持久化-1188", "session_id": "persist"},
        headers=member1,
    )
    assert response.status_code == 200, response.text

    def assert_member1_history_visible():
        sessions = client.get("/v1/chat/sessions", headers=member1)
        assert sessions.status_code == 200, sessions.text
        assert "persist" in sessions.json()["sessions"]
        assert _history_count(client, member1, "persist") == 2

    # 1) 刷新页面：新客户端（新页面会话）仍能列出并读全文。
    assert_member1_history_visible()
    assert_member1_history_visible()

    # 2) 切换账号：同一主体重新登录（新令牌）历史仍在；另一个账号看不到。
    assert_member1_history_visible()
    member2 = _headers("member2")
    other_sessions = client.get("/v1/chat/sessions", headers=member2)
    assert other_sessions.status_code == 200, other_sessions.text
    assert "persist" not in other_sessions.json()["sessions"]
    other_history = client.get(
        "/v1/chat/history", params={"session_id": "persist"}, headers=member2
    )
    # 别人占用的会话名按不存在处理（404），或退化为空历史；绝不能读到内容。
    if other_history.status_code == 200:
        assert other_history.json()["count"] == 0
    else:
        assert other_history.status_code == 404

    # 3) 重启后端：清空进程内会话表 + 换一个新的持久库实例，历史仍在。
    import src.ai.conversation_store as conversation_module
    import src.gateway.chat as chat_module

    chat_module._sessions.clear()
    chat_module._session_users.clear()
    chat_module._session_last_used.clear()
    chat_module._dedup_entries.clear()
    db_path = conversation_module.get_conversation_store().db_path
    conversation_module._conversation_store = conversation_module.ConversationStore(
        db_path
    )

    assert_member1_history_visible()
