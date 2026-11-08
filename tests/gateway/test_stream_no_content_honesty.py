"""No-content upstream must fail, never "complete" and never bill.

The defect
----------
A stub that accepted the connection but wrote no response body was reported as
a finished turn and a usage row was written, so the UI/账单 both claimed the
model had produced an answer. That is the same family of fake-success we fixed
for empty completions: a turn with no content is a failure.

Variants covered here, all over a real HTTP socket (no SDK mocking):

1. ``silent``   — 200 headers, then nothing until the read timeout;
2. ``keepalive``— only ``: keepalive`` / ``event: ping``, then close;
3. ``half``     — one real token, then the connection closes without ``[DONE]``;
4. ``empty``    — 200 with an empty body.

Expected conclusions:
* 1/2/4 have no content -> ``status=error``, no usage row, readable Chinese;
* 3 keeps the received token, marks ``status=incomplete``, never ``completed``.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from src.ai.liuhao import LiuHaoAssistant
from src.ai.providers import OpenAIProvider
from src.gateway import chat as chat_module
from src.gateway.main import get_app


class _StubHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # keep pytest output clean
        return

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        self.server.request_count = getattr(self.server, "request_count", 0) + 1
        # 记录每次上游请求的到达时刻：用来断言"重试是立即发生的、没有第三次"，
        # 而不必把 TestClient/应用启动的固定开销算进超时行为里。
        self.server.request_started_at.append(time.monotonic())
        probe = getattr(self.server, "usage_probe", None)
        if probe is not None:
            # 在**这一次**上游请求开始时快照用量行数：证明失败的那次没记账。
            self.server.usage_snapshots.append(int(probe()))
        mode = self.server.mode
        if mode == "flaky":
            if self.server.request_count == 1:
                # 第一次：连上但不写响应体 -> 客户端读超时。
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                time.sleep(1.2)
                self.close_connection = True
                return
            # 第二次：正常返回，重试成功。
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            chunk = json.dumps(
                {"choices": [{"delta": {"content": "重试成功"}}]}, ensure_ascii=False
            )
            self.wfile.write(f"data: {chunk}\n\ndata: [DONE]\n\n".encode("utf-8"))
            self.wfile.flush()
            self.close_connection = True
            return
        if mode in ("silent", "always_silent"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            # 保持连接但不写任何响应体：客户端只能靠读超时退出。
            time.sleep(1.2)
            self.close_connection = True
            return
        if mode == "keepalive":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for _ in range(5):
                self.wfile.write(b": keepalive\n\nevent: ping\n\n")
                self.wfile.flush()
                time.sleep(0.1)
            self.close_connection = True
            return
        if mode == "half":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            chunk = json.dumps(
                {"choices": [{"delta": {"content": "半"}}]}, ensure_ascii=False
            )
            self.wfile.write(f"data: {chunk}\n\n".encode("utf-8"))
            self.wfile.flush()
            time.sleep(0.05)
            self.close_connection = True
            return
        if mode == "empty":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(500)
        self.end_headers()


@pytest.fixture()
def stub_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _StubHandler)
    server.daemon_threads = True
    server.request_count = 0
    server.request_started_at = []
    server.usage_snapshots = []
    server.usage_probe = None
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


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


def _provider(server) -> OpenAIProvider:
    return OpenAIProvider(
        "stub-provider",
        "stub-model",
        api_key="stub-key",
        base_url=f"http://127.0.0.1:{server.server_address[1]}",
        timeout=0.6,
        first_token_timeout=0.6,
        retry_base_delay=0.0,
    )


def _run_stream(
    monkeypatch, auth_headers, server, mode: str, session: str, *, recorded=None, setup=None
):
    server.mode = mode
    if recorded is None:
        recorded = []
    if setup is not None:
        # 让 stub 能在"刚好开始处理某次上游请求"时读取当时的用量行数。
        setup(server, recorded)
    assistant = LiuHaoAssistant(name="liuhao-test", provider=_provider(server))
    monkeypatch.setattr(chat_module, "get_assistant", lambda *a, **k: assistant)
    monkeypatch.setattr(
        chat_module, "record_usage", lambda **kwargs: recorded.append(kwargs) or True
    )
    client = TestClient(get_app())
    with client.stream(
        "POST",
        "/v1/chat/stream",
        json={"message": "hello", "session_id": session, "use_knowledge": False},
        headers=auth_headers,
    ) as resp:
        assert resp.status_code == 200
        body = "".join(resp.iter_text())
    frames = []
    for line in body.splitlines():
        if line.startswith("data: "):
            frames.append(json.loads(line[len("data: "):]))
    done = [f for f in frames if f.get("type") == "done"][-1]
    text = "".join(
        f.get("content", "") for f in frames if f.get("type") == "token"
    )
    return done, text, frames, recorded


def test_silent_upstream_times_out_without_billing(
    monkeypatch, auth_headers, stub_server
):
    done, text, frames, recorded = _run_stream(
        monkeypatch, auth_headers, stub_server, "silent", "s-silent"
    )
    assert done["status"] == "error", "无内容的一轮不能是 completed"
    assert done["usage_recorded"] is False
    assert recorded == [], "超时/无内容不得写入用量账本"
    assert "首次加载较慢" in str(done.get("error") or "")
    assert "Key" not in str(done.get("error") or "")
    assert "首次加载较慢" in text, "失败文案必须发给前端，而不是留一块空白"
    # 重试一次是可见的，不是静默空等。
    notices = [f for f in frames if f.get("type") == "notice"]
    assert notices and notices[0]["code"] == "retrying"


def test_keepalive_only_is_an_empty_response_not_a_success(
    monkeypatch, auth_headers, stub_server
):
    done, text, _frames, recorded = _run_stream(
        monkeypatch, auth_headers, stub_server, "keepalive", "s-keepalive"
    )
    assert done["status"] == "error"
    assert done["usage_recorded"] is False
    assert recorded == []
    assert "模型没有返回内容" in str(done.get("error") or "")
    assert "模型没有返回内容" in text


def test_empty_200_body_is_an_empty_response(monkeypatch, auth_headers, stub_server):
    done, text, _frames, recorded = _run_stream(
        monkeypatch, auth_headers, stub_server, "empty", "s-empty"
    )
    assert done["status"] == "error"
    assert done["usage_recorded"] is False
    assert recorded == []
    assert "模型没有返回内容" in str(done.get("error") or "")
    assert "模型没有返回内容" in text


def test_half_stream_is_incomplete_and_not_completed(
    monkeypatch, auth_headers, stub_server
):
    done, text, _frames, recorded = _run_stream(
        monkeypatch, auth_headers, stub_server, "half", "s-half"
    )
    assert done["status"] == "incomplete", "半截流不能被当成完整回复"
    assert done["status"] != "completed"
    assert done["usage_recorded"] is False
    assert recorded == [], "不完整的一轮不记用量"
    assert "半" in text, "已收到的内容必须保留"
    assert "回复可能不完整" in str(done.get("error") or "")
    assert stub_server.request_count == 1, "已经吐过字的流不能重放，否则用户看到两段回答"


def test_retry_is_capped_and_double_timeout_fails_fast(
    monkeypatch, auth_headers, stub_server
):
    """重试也超时：最多两次请求、最多等两倍阈值，最终给出可读文案。"""
    started = time.monotonic()
    done, _text, frames, recorded = _run_stream(
        monkeypatch, auth_headers, stub_server, "always_silent", "s-double"
    )
    elapsed = time.monotonic() - started
    starts = list(stub_server.request_started_at)
    assert len(starts) == 2, "只能重试一次，不能无限重试"
    # 单看上游请求的到达时刻，剔掉 TestClient/应用启动的固定开销：第二次
    # 请求必须在第一次 0.6 秒超时后紧接着发出（退避可配且为 0），用户不会
    # 在这条路径上被长时间挂住。
    gap = starts[1] - starts[0]
    assert gap <= 2.0, f"重试间隔过长（{gap:.2f}s），用户会在这一步干等"
    # 重试也超时后不能再冒出第三次请求（给可能的第三次留出超过一个阈值的观察期）。
    time.sleep(1.2)
    assert stub_server.request_count == 2, "重试也超时后不允许出现第三次请求"
    # 宽松的绝对上限只用来兜住"整条链路挂死"，不承担"启动快慢"的判定。
    assert elapsed < 12.0, f"两次 0.6 秒超时不该挂住整条链路（实际 {elapsed:.2f}s）"
    assert done["status"] == "error"
    assert done["usage_recorded"] is False
    assert recorded == []
    assert "首次加载较慢" in done["error"]
    assert "stub-model" in done["error"], "最终文案必须带实际模型名"
    notices = [f for f in frames if f.get("type") == "notice"]
    assert len(notices) == 1, "只提示一次重试"


def test_failed_first_attempt_bills_nothing_and_retry_bills_once(
    monkeypatch, auth_headers, stub_server
):
    """超时重试一次后成功：失败那次 0 行，成功那次恰好 1 行。"""
    recorded: list = []

    def setup(server, rows):
        server.usage_probe = lambda: len(rows)

    done, text, frames, recorded = _run_stream(
        monkeypatch,
        auth_headers,
        stub_server,
        "flaky",
        "s-flaky",
        recorded=recorded,
        setup=setup,
    )
    assert stub_server.request_count == 2, "失败一次后必须真的重试（且只重试一次）"
    assert stub_server.usage_snapshots == [0, 0], (
        "第一次失败时用量为 0；重试请求到达时用量仍为 0，不能边失败边记账"
    )
    assert len(recorded) == 1, "整轮只允许记 1 行用量，重试不能重复计费"
    assert done["status"] == "completed"
    assert done["usage_recorded"] is True
    assert "重试成功" in text
    notices = [f for f in frames if f.get("type") == "notice"]
    assert notices and notices[0]["code"] == "retrying"
