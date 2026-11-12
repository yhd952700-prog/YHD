"""上下文超限：``/v1/chat/stream`` 的中文响应体与"不重试"行为。

背景（真实缺陷）：上游返回 ``context_length_exceeded`` / ``maximum context
length`` / ``prompt is too long`` 时，原先被归进 ``upstream_error``，用户看到
「请检查密钥与网络连接」——方向完全相反。这里钉死三件事：

1. token 帧与 done 帧的文案都是「对话太长，请新开会话或分段发送…」；
2. done 帧的 ``reason`` 是独立的 ``context_length_exceeded``；
3. 上游只被调用一次（上下文超限不重试）。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import src.gateway.chat as chat_module
from src.ai.liuhao import LiuHaoAssistant
from src.ai.providers import BaseProvider, ProviderHTTPError


class _OverflowProvider(BaseProvider):
    def __init__(self):
        super().__init__("stub", "stub-model", retry_base_delay=0.0)
        self.calls = 0

    def chat_stream(self, messages, **kwargs):
        self.calls += 1
        raise ProviderHTTPError(
            "模型服务返回 HTTP 400",
            status_code=400,
            upstream_kind="context_length_exceeded",
        )


@pytest.fixture
def client(monkeypatch):
    provider = _OverflowProvider()
    assistant = LiuHaoAssistant(name="liuhao-overflow-body", provider=provider)
    monkeypatch.setattr(chat_module, "get_assistant", lambda *a, **k: assistant)
    chat_module._dedup_entries.clear()
    from src.gateway.main import get_app

    with TestClient(get_app()) as test_client:
        test_client.provider = provider
        yield test_client
    chat_module._sessions.clear()
    chat_module._session_users.clear()
    chat_module._dedup_entries.clear()


def _events(body: str):
    parsed = []
    for block in body.split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data: "):
                parsed.append(json.loads(line[len("data: "):]))
    return parsed


def test_context_overflow_has_its_own_copy_and_is_not_retried(client, auth_headers):
    resp = client.post(
        "/v1/chat/stream",
        json={
            "message": "把前面 3 小时的对话再总结一次",
            "session_id": "s-overflow-body",
            "use_knowledge": False,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text

    events = _events(resp.text)
    tokens = [e for e in events if e.get("type") == "token"]
    done = events[-1]

    assert tokens and "对话太长" in tokens[-1]["content"]
    assert "请检查密钥" not in tokens[-1]["content"]
    assert done["status"] == "error"
    assert done["reason"] == "context_length_exceeded"
    assert "对话太长" in done["error"]
    assert client.provider.calls == 1, "上下文超限重试不会让历史变短"
