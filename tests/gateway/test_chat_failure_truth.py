"""聊天链路缺陷回归（真实 HTTP + 真实 LiuHaoAssistant）：

- P1-8 流式失败：done 帧必须带 status=error，失败不记用量，前端可区分
- P1-9 失败的 provider 解析不留下空会话；会话数有上限且旧 provider 被关闭
- P1-15 OpenAI client 复用并在关闭路径释放
"""

from __future__ import annotations

import json
import threading
import uuid

import pytest
from fastapi.testclient import TestClient

from src.ai.liuhao import LiuHaoAssistant
from src.ai.model_config import ModelConfigError
from src.ai.providers import (
    BaseProvider,
    MockProvider,
    MODEL_NOT_CONFIGURED_MESSAGE,
    OpenAIProvider,
)
from src.gateway import chat as chat_module
from src.gateway.main import get_app


class _FailingProvider(BaseProvider):
    def __init__(self) -> None:
        super().__init__(name="failing-provider", model="failing-model")

    def generate(self, prompt: str, **kwargs):
        raise RuntimeError(
            "HTTPSConnectionPool(host='api.deepseek.com', port=443): "
            "Max retries exceeded with url: /chat/completions"
        )


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
    assert done[0]["usage_recorded"] is False
    assert recorded == [], "失败轮次不得写入用量账本"

    # D3：用户侧只能看到中性中文 + 可操作提示；原始异常不得出现在流里。
    error = str(done[0].get("error") or "")
    assert "这次没有拿到回复" in error
    assert "设置 → 模型" in error
    assert "api.deepseek.com" not in body
    assert "HTTPSConnectionPool" not in body
    assert "[生成失败]" not in body


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


class _RecordingOkProvider(_OkProvider):
    def __init__(self) -> None:
        super().__init__()
        self.seen: list = []

    def chat(self, messages, **kwargs) -> str:
        self.seen.append([dict(m) for m in messages])
        return "ok"


def test_d3_failed_turn_is_neutral_and_never_enters_context():
    """D3：失败文案中性、无原始异常；失败轮次不进历史/下一轮上下文。"""
    name = f"liuhao-d3-{uuid.uuid4().hex[:8]}"
    assistant = LiuHaoAssistant(name=name, provider=_FailingProvider())

    first = assistant.chat("hello")
    assert first["status"] == "error"
    assert "这次没有拿到回复" in first["reply"]
    assert "设置 → 模型" in first["reply"]
    assert "HTTPSConnectionPool" not in first["reply"]
    assert "HTTPSConnectionPool" not in (assistant.last_turn_error or "")
    assert assistant.history == [], "失败轮次不得写入历史"
    assert assistant._conv_store.load_recent(name, limit=10) == [], (
        "失败轮次不得写入会话存储"
    )

    recorder = _RecordingOkProvider()
    assistant.set_provider(recorder)
    second = assistant.chat("next")
    assert second["status"] == "completed"
    sent = json.dumps(recorder.seen[0], ensure_ascii=False)
    assert "这次没有拿到回复" not in sent, "失败文案不得进入下一轮上下文"
    assert "[生成失败]" not in sent
    assert "HTTPSConnectionPool" not in sent
    assert assistant.history == [
        {"role": "user", "content": "next"},
        {"role": "assistant", "content": "ok"},
    ]


def test_d4_mock_provider_is_explicit_demo_not_internal_state():
    """D4：未配置模型时明确提示 + 下一步；不再输出内部状态转储。"""
    provider = MockProvider(name="mock", model="mock-model")
    text = provider.generate("status")
    assert text == MODEL_NOT_CONFIGURED_MESSAGE
    assert "还没有配置模型" in text
    assert "演示数据" in text
    assert "系统设置 → 模型管理" in text and "密钥" in text
    assert "LiuHao AI OS status" not in text
    assert "supports_streaming" not in text

    name = f"liuhao-d4-{uuid.uuid4().hex[:8]}"
    assistant = LiuHaoAssistant(
        name=name, provider=MockProvider(name="mock-2", model="mock-model")
    )
    result = assistant.chat("status")
    assert result["reply"] == MODEL_NOT_CONFIGURED_MESSAGE
    assistant.reset()


def _serve_upstream(monkeypatch, *, status: int, payload: dict) -> None:
    """让真实 OpenAIProvider 收到一个真实 HTTP 响应（不联网）。

    与 ``tests/test_provider_error_classification.py`` 同一手法：走真实
    ``openai`` SDK + ``httpx.MockTransport``，让 429/401 经 SDK 抛真实异常，
    再经 provider → assistant → SSE done 帧，断言最终到界面的 ``reason``。
    """
    import httpx
    import openai

    from src.ai import providers as providers_mod

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload, request=request)

    http_client = httpx.Client(
        transport=httpx.MockTransport(handler), follow_redirects=False
    )

    def _fake_get_client(self):
        return openai.OpenAI(
            api_key=self.api_key,
            base_url=self.base_url or "https://api.openai.com/v1",
            http_client=http_client,
            max_retries=0,
        )

    monkeypatch.setattr(providers_mod.OpenAIProvider, "_get_client", _fake_get_client)
    # 目标校验由 tests/test_providers.py 覆盖；这里不依赖外网 DNS。
    monkeypatch.setattr(
        providers_mod, "assert_safe_runtime_target", lambda *a, **k: None
    )


def _real_openai_provider() -> OpenAIProvider:
    return OpenAIProvider(
        name="openai-e2e",
        model="gpt-4o-mini",
        api_key="sk-e2e-123456",
        base_url="https://api.openai.com/v1",
        timeout=5.0,
    )


def test_d5_stream_done_frame_carries_quota_exhausted_reason(monkeypatch, auth_headers):
    """真实 429 配额耗尽 → SSE done 帧 ``reason=quota_exhausted``。

    这是"单测绿、用户还是看不懂"的回归：分类已经在 provider 里做对，
    但 done 帧只发 ``error`` 时，前端拿不到分类，只能显示笼统失败。
    """
    _serve_upstream(
        monkeypatch,
        status=429,
        payload={
            "error": {
                "message": "You exceeded your current quota, please check your plan.",
                "type": "insufficient_quota",
                "code": "insufficient_quota",
            }
        },
    )
    assistant = _assistant(_real_openai_provider())
    monkeypatch.setattr(chat_module, "get_assistant", lambda *a, **k: assistant)

    client = TestClient(get_app())
    with client.stream(
        "POST",
        "/v1/chat/stream",
        json={"message": "hello", "session_id": "s-quota", "use_knowledge": False},
        headers=auth_headers,
    ) as resp:
        assert resp.status_code == 200
        body = "".join(resp.iter_text())

    done = [f for f in _read_sse(body) if f.get("type") == "done"][-1]
    assert done["status"] == "error"
    assert done.get("reason") == "quota_exhausted", done
    # 中性文案：上游英文与 Key 都不能出现在流里。
    assert "insufficient" not in body.lower()
    assert "sk-e2e" not in body


def test_d5_non_stream_response_carries_key_revoked_reason(monkeypatch, auth_headers):
    """真实 401 ``revoked`` → ``/v1/chat`` 响应 ``reason=key_revoked``。"""
    _serve_upstream(
        monkeypatch,
        status=401,
        payload={
            "error": {
                "message": "This API key has been revoked.",
                "type": "invalid_request_error",
                "code": "api_key_revoked",
            }
        },
    )
    assistant = _assistant(_real_openai_provider())
    monkeypatch.setattr(chat_module, "get_assistant", lambda *a, **k: assistant)

    client = TestClient(get_app())
    resp = client.post(
        "/v1/chat",
        json={"message": "hello", "session_id": "s-revoked", "use_knowledge": False},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["status"] == "error"
    assert payload["reason"] == "key_revoked", payload
    # 主文案区：只能有可行动的中文，不得出现上游英文/type/Key。
    reply = payload["reply"]
    assert "API Key 无效或已失效" in reply
    assert "This API key" not in reply
    assert "invalid_request_error" not in reply
    assert "sk-e2e" not in reply
    # 上游 message 只允许放在可展开的「技术详情」：只 message，脱敏 + 截断。
    detail = payload["technical_detail"]
    assert isinstance(detail, str) and detail
    assert "This API key has been revoked." in detail
    assert len(detail) <= 120
    assert "invalid_request_error" not in detail
    # 全文除脱敏占位 sk-*** 外，不得出现 Key / 认证头痕迹。
    redacted = resp.text.replace("sk-***", "")
    assert "sk-" not in redacted
    assert "Bearer" not in redacted
    assert "api_key" not in redacted


def test_d5_stream_technical_detail_only_in_done_frame(monkeypatch, auth_headers):
    """流式 401：上游原文只进 done.technical_detail，不进 token 正文。"""
    _serve_upstream(
        monkeypatch,
        status=401,
        payload={
            "error": {
                "message": "This API key has been revoked.",
                "type": "invalid_request_error",
                "code": "api_key_revoked",
            }
        },
    )
    assistant = _assistant(_real_openai_provider())
    monkeypatch.setattr(chat_module, "get_assistant", lambda *a, **k: assistant)

    client = TestClient(get_app())
    with client.stream(
        "POST",
        "/v1/chat/stream",
        json={
            "message": "hello",
            "session_id": "s-revoked-stream",
            "use_knowledge": False,
        },
        headers=auth_headers,
    ) as resp:
        assert resp.status_code == 200
        body = "".join(resp.iter_text())

    frames = _read_sse(body)
    tokens = "".join(
        f.get("content", "") for f in frames if f.get("type") == "token"
    )
    done = [f for f in frames if f.get("type") == "done"][-1]
    assert done["status"] == "error"
    assert done["reason"] == "key_revoked"
    assert "API Key 无效或已失效" in done["error"]
    assert "This API key" not in tokens
    assert "invalid_request_error" not in done["error"]
    assert "This API key has been revoked." in done["technical_detail"]
    assert len(done["technical_detail"]) <= 120


def test_d5_success_response_has_no_reason(monkeypatch, auth_headers):
    """成功轮次 ``reason`` 必须为空，避免前端把正常回复渲染成失败卡片。"""
    assistant = _assistant(_OkProvider())
    monkeypatch.setattr(chat_module, "get_assistant", lambda *a, **k: assistant)

    client = TestClient(get_app())
    resp = client.post(
        "/v1/chat",
        json={"message": "hi", "session_id": "s-ok-reason", "use_knowledge": False},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "completed"
    assert resp.json().get("reason") is None


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
