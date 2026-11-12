"""R5b：GZip 只豁免 ``/v1/chat/stream``，其余接口与静态缓存头保持原样。

GZipMiddleware 会延迟 ``http.response.start`` 到首个 body chunk，SSE 在首 token
之前没有 chunk，浏览器拿不到响应头 → 前端 45 秒看门狗误判超时。反向用例
保证没有把"凡 SSE 都豁免"写成全局开关。
"""

from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import src.gateway.chat as chat_module
from src.gateway.console_static import cache_control_for


class _BigReplyAssistant:
    provider = SimpleNamespace(model="fake-model")
    turn = 1
    last_turn_correlation_id = "corr-gzip"

    def begin_turn(self) -> None:
        pass

    def end_turn(self) -> None:
        pass

    def preflight_chat(self, message: str):
        return None

    def serialized_turn(self):
        return nullcontext()

    def chat_stream(self, message: str):
        yield "长" * 2000


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(chat_module, "get_assistant", lambda *a, **k: _BigReplyAssistant())
    chat_module._dedup_entries.clear()
    from src.gateway.main import get_app

    with TestClient(get_app()) as test_client:
        yield test_client
    chat_module._sessions.clear()
    chat_module._session_users.clear()
    chat_module._dedup_entries.clear()


def test_chat_stream_is_not_gzip_compressed(client, auth_headers):
    resp = client.post(
        "/v1/chat/stream",
        json={"message": "你好", "session_id": "s-gzip", "use_knowledge": False},
        headers={"Accept-Encoding": "gzip", **auth_headers},
    )
    assert resp.status_code == 200, resp.text
    assert "content-encoding" not in {k.lower() for k in resp.headers}


def test_other_json_endpoints_still_gzip(client):
    resp = client.get("/openapi.json", headers={"Accept-Encoding": "gzip"})
    assert resp.status_code == 200
    assert len(resp.content) > 1000
    assert resp.headers.get("content-encoding", "").lower() == "gzip"


def test_hashed_static_assets_keep_long_cache_header():
    assert cache_control_for("assets/index-A1b2C3d4.js") == (
        "public, max-age=31536000, immutable"
    )
