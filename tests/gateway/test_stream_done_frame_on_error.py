"""R11：流中抛出真异常时必须补 ``done`` 帧（带中文文案），不能只留半截内容。

背景（真实缺陷）：服务端在流式生成途中抛异常时，生成器直接结束，不发
``done``。用户看到已流出的半截内容、没有任何错误卡，误以为那就是完整回答。

修复后 ``/v1/chat/stream`` 的每条非断连异常路径都补一帧 ``done``：
``status="error"`` + ``error``（中性中文）+ ``reason``（机器标签）。
"""

from __future__ import annotations

import json
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import src.gateway.chat as chat_module


class _HalfThenRaiseAssistant:
    """正常输出一个 token 后抛出内部异常。"""

    provider = SimpleNamespace(model="fake-model")
    turn = 1
    last_turn_correlation_id = "corr-r11"
    last_turn_status = "completed"

    def begin_turn(self) -> None:
        pass

    def end_turn(self) -> None:
        pass

    def preflight_chat(self, message: str):
        return None

    def serialized_turn(self):
        return nullcontext()

    def chat_stream(self, message: str):
        yield "半截"
        raise RuntimeError("boom")


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(
        chat_module,
        "get_assistant",
        lambda *a, **k: _HalfThenRaiseAssistant(),
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


def test_stream_exception_always_emits_done_frame(client, auth_headers):
    resp = client.post(
        "/v1/chat/stream",
        json={"message": "你好", "session_id": "s-r11", "use_knowledge": False},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text

    events = _events(resp.text)
    tokens = [e for e in events if e.get("type") == "token"]
    assert tokens and tokens[0]["content"] == "半截"

    done = events[-1]
    assert done.get("type") == "done", f"最后必须补 done 帧，实际：{events}"
    assert done["status"] == "error"
    assert done.get("error"), "done 必须带中文错误文案，前端据此渲染错误卡"
    assert "这次没有拿到回复" in done["error"]
    assert done.get("usage_recorded") is False, "失败轮次不得记成已完成"
