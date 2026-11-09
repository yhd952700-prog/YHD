"""H2：``finish_reason=length`` 必须收口成 ``truncated``，不能记 completed。

断言网关层契约：已流出的正文保留；``done`` 帧带 ``status=truncated``、
``truncated=true``、中文错误说明与机器 reason；跨模型切换时附带
``fell_back_from``。
"""
from __future__ import annotations

import json
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import src.gateway.chat as chat_module


class _TruncatedAssistant:
    provider = SimpleNamespace(
        model="primary-model",
        last_fell_back_from=["primary-model"],
    )
    turn = 1
    last_turn_correlation_id = "corr-h2"
    last_turn_status = "truncated"
    last_turn_reason = "truncated"
    last_turn_error = "回复达到长度上限被截断，请发送「继续」或换用支持更长输出的模型。"
    last_turn_technical_detail = "finish_reason=length"

    def begin_turn(self) -> None:
        pass

    def end_turn(self) -> None:
        pass

    def preflight_chat(self, message: str):
        return None

    def serialized_turn(self):
        return nullcontext()

    def chat_stream(self, message: str):
        yield "被截断的"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(
        chat_module,
        "get_assistant",
        lambda *a, **k: _TruncatedAssistant(),
    )
    chat_module._dedup_entries.clear()
    from src.gateway.main import get_app

    with TestClient(get_app()) as test_client:
        yield test_client
    chat_module._sessions.clear()
    chat_module._session_users.clear()
    chat_module._dedup_entries.clear()


def _events(body: str) -> list:
    parsed = []
    for block in body.split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data: "):
                parsed.append(json.loads(line[len("data: ") :]))
    return parsed


def test_truncated_done_frame_marks_truncated_and_keeps_content(client, auth_headers):
    resp = client.post(
        "/v1/chat/stream",
        json={"message": "写一个长回答", "use_knowledge": False},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    events = _events(resp.text)
    tokens = [e["content"] for e in events if e.get("type") == "token"]
    assert tokens == ["被截断的"]

    done = next(e for e in events if e.get("type") == "done")
    assert done["status"] == "truncated"
    assert done["truncated"] is True
    assert done["reason"] == "truncated"
    assert "长度上限" in done["error"]
    assert done["fell_back_from"] == ["primary-model"]
