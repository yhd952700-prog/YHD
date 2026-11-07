"""聊天链路缺陷回归（真实 HTTP + 真实 LiuHaoAssistant）：

- P1-8 流式失败：done 帧必须带 status=error，失败不记用量，前端可区分
- P1-9 失败的 provider 解析不留下空会话；会话数有上限且旧 provider 被关闭
- P1-15 OpenAI client 复用并在关闭路径释放
"""

from __future__ import annotations

import json
import threading

import pytest
from fastapi.testclient import TestClient

from src.ai.liuhao import LiuHaoAssistant
from src.ai.model_config import ModelConfigError
from src.ai.providers import BaseProvider, OpenAIProvider
from src.gateway import chat as chat_module
from src.gateway.main import get_app


class _FailingProvider(BaseProvider):
    def __init__(self) -> None:
        super().__init__(name="failing-provider", model="failing-model")

    def generate(self, prompt: str, **kwargs):
        raise RuntimeError("upstream provider unavailable")


class _OkProvider(BaseProvider):
    def __init__(self) -> None:
        super().__init__(name="ok-provider", model="ok-model")
        self.close_calls = 0

    def generate(self, prompt: str, **kwargs) -> str:
        return "ok"

    def close(self) -> None:
        self.close_calls += 1


@pytest.fixture(autouse=True)
def clean_sessions():
    with chat_module._sessions_lock:
        chat_module._sessions.clear()
        chat_module._session_users.clear()
        chat_module._session_last_used.clear()
    yield
    with chat_module._sessions_lock:
        chat_module._sessions.clear()
        chat_module._session_users.clear()
        chat_module._session_last_used.clear()


def _read_sse(text: str) -> list:
    frames = []
    for line in text.splitlines():
        if line.startswith("data: "):
            frames.append(json.loads(line[len("data: "):]))
    return frames


def _assistant(provider) -> LiuHaoAssistant:
    return LiuHaoAssistant(name="liuhao-test", provider=provider)


def test_p1_8_stream_failure_marks_done_and_skips_usage(monkeypatch, auth_headers):
    assistant = _assistant(_FailingProvider())
    recorded: list = []
    monkeypatch.setattr(chat_module, "get_assistant", lambda *a, **k: assistant)
    monkeypatch.setattr(
        chat_module, "record_usage", lambda **kwargs: recorded.append(kwargs) or True
    )

    client = TestClient(get_app())
    with client.stream(
        "POST",
        "/v1/chat/stream",
        json={"message": "hello", "session_id": "s-fail", "use_knowledge": False},
        headers=auth_headers,
    ) as resp:
        assert resp.status_code == 200
        body = "".join(resp.iter_text())

    frames = _read_sse(body)
    done = [f for f in frames if f.get("type") == "done"]
    assert len(done) == 1
    assert done[0]["status"] == "error"
    assert "生成失败" in str(done[0].get("error") or "") or "unavailable" in str(
        done[0].get("error") or ""
    )
    assert done[0]["usage_recorded"] is False
    assert recorded == [], "失败轮次不得写入用量账本"


def test_p1_8_stream_success_still_reports_completed(monkeypatch, auth_headers):
    assistant = _assistant(_OkProvider())
    recorded: list = []
    monkeypatch.setattr(chat_module, "get_assistant", lambda *a, **k: assistant)
    monkeypatch.setattr(
        chat_module, "record_usage", lambda **kwargs: recorded.append(kwargs) or True
    )

    client = TestClient(get_app())
    with client.stream(
        "POST",
        "/v1/chat/stream",
        json={"message": "hello", "session_id": "s-ok", "use_knowledge": False},
        headers=auth_headers,
    ) as resp:
        assert resp.status_code == 200
        body = "".join(resp.iter_text())

    done = [f for f in _read_sse(body) if f.get("type") == "done"][-1]
    assert done["status"] == "completed"
    assert done["usage_recorded"] is True
    assert len(recorded) == 1


def test_p1_8_non_stream_failure_skips_usage(monkeypatch, auth_headers):
    assistant = _assistant(_FailingProvider())
    recorded: list = []
    monkeypatch.setattr(chat_module, "get_assistant", lambda *a, **k: assistant)
    monkeypatch.setattr(
        chat_module, "record_usage", lambda **kwargs: recorded.append(kwargs) or True
    )

    client = TestClient(get_app())
    resp = client.post(
        "/v1/chat",
        json={"message": "hello", "session_id": "s-fail-2", "use_knowledge": False},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "error"
    assert recorded == []


def test_p1_9_failed_provider_resolution_leaves_no_empty_sessions(
    monkeypatch, auth_headers
):
    def fails(*args, **kwargs):
        raise ModelConfigError(409, "当前账号没有可用的对话模型，请联系管理员")

    monkeypatch.setattr(chat_module, "provider_for_task", fails)
    client = TestClient(get_app())

    for index in range(10):
        resp = client.post(
            "/v1/chat",
            json={
                "message": "hello",
                "session_id": f"member-session-{index}",
                "use_knowledge": False,
            },
            headers=auth_headers,
        )
        assert resp.status_code == 409, resp.text

    assert chat_module._sessions == {}, "409 请求不得留下空会话"
    assert chat_module._session_users == {}


def test_p1_9_session_capacity_and_provider_close(monkeypatch):
    monkeypatch.setenv("LIUHAO_CHAT_MAX_SESSIONS", "2")
    providers: list = []

    def factory(*args, **kwargs):
        provider = _OkProvider()
        providers.append(provider)
        return provider

    monkeypatch.setattr(chat_module, "provider_for_task", factory)

    first = chat_module.get_assistant("s1", "user-a")
    chat_module.get_assistant("s2", "user-a")
    chat_module.get_assistant("s3", "user-a")

    with chat_module._sessions_lock:
        assert len(chat_module._sessions) == 2
        assert chat_module._session_key("user-a", "s1") not in chat_module._sessions
        assert chat_module._session_key("user-a", "s3") in chat_module._sessions

    assert first is not None
    # s1 被 LRU 淘汰时必须关闭其 provider，不能把 client 句柄漏在进程里。
    assert providers[0].close_calls >= 1


def test_p1_16_usage_write_failure_is_visible_in_health_and_readiness(
    monkeypatch, tmp_path
):
    """账本写失败必须被上层感知：返回 False、健康计数上升、readiness 降级。"""
    from src.gateway import account_usage

    monkeypatch.setattr(account_usage, "_failures_total", 0)
    monkeypatch.setattr(account_usage, "_consecutive_failures", 0)
    monkeypatch.setattr(account_usage, "_last_error", None)
    monkeypatch.setattr(account_usage, "_last_error_at", None)

    # 真实失败：把数据库路径变成一个目录，sqlite 无法打开。
    bad_db = tmp_path / "usage.sqlite3"
    bad_db.mkdir()
    monkeypatch.setenv("LIUHAO_ACCOUNT_USAGE_DB", str(bad_db))

    wrote = account_usage.record_usage(
        actor="alice", key_owner="alice", model="m", tokens_in=1, tokens_out=1
    )
    assert wrote is False, "写失败必须返回 False，不能假装成功"

    health = account_usage.usage_health()
    assert health["failures_total"] == 1
    assert health["consecutive_failures"] == 1
    assert health["last_error"], "失败原因必须留存供诊断"

    client = TestClient(get_app())
    resp = client.get("/v1/ready")
    assert resp.status_code == 503, resp.text
    checks = resp.json()["checks"]
    assert checks["usage_ledger"]["status"] == "degraded"
    assert checks["usage_ledger"]["consecutive_failures"] == 1


def test_p1_15_openai_client_is_reused_and_closed(monkeypatch):
    created: list = []

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.close_calls = 0
            created.append(self)

        def close(self):
            self.close_calls += 1

    import openai

    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)

    provider = OpenAIProvider(name="openai-test", model="m", api_key="k")
    client_a = provider._get_client()
    client_b = provider._get_client()
    assert client_a is client_b
    assert len(created) == 1, "同一 provider 不应每次调用都新建 client"

    provider.close()
    assert created[0].close_calls == 1
    client_c = provider._get_client()
    assert client_c is not client_a
    assert len(created) == 2
    provider.close()
