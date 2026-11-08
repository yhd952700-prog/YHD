"""P0 回归：Key 文本形态统一、聊天认「设为默认」并逐级回落。

两个用户实测问题：

1. 填了带首尾空格的 Key，请求头里出现 ``Bearer   sk-...  ``，httpx 直接抛
   ``Illegal header value`` —— 请求根本没发出去，用户却以为 Key 本身错了。
2. 配置多个模型时，聊天跳过用户设为默认的可用 Ollama，去用另一个坏 Key 的
   模型，失败后也不回落 —— 界面上的「设为默认」成了装饰。

这里全部断言**行为**：真实 SQLite 存储 + 真实 provider + 本地假上游，
不打网络、不依赖本机模型。
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from src.ai.model_config import (
    ModelConfigError,
    get_model_config_store,
)
from src.ai.model_router import provider_for_task
from src.ai.providers import (
    BaseProvider,
    FailoverProvider,
    INVALID_KEY_MESSAGE,
    ProviderChainError,
    ProviderHTTPError,
    ProviderStreamIncompleteError,
    provider_from_config,
    user_facing_failure,
)


# ==================== 本地假上游 ====================
class _Upstream:
    """一个进程内 HTTP 服务：/good 正常、/bad 401、/down 500。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        upstream = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # 静音
                pass

            def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler 约定
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b"{}"
                try:
                    payload = json.loads(body or b"{}")
                except ValueError:
                    payload = {}
                upstream.calls.append(
                    {
                        "path": self.path,
                        "auth": self.headers.get("Authorization") or "",
                        "body": payload,
                    }
                )
                if "/bad/" in self.path:
                    self._json(
                        401,
                        {
                            "error": {
                                "message": "Incorrect API key provided",
                                "type": "invalid_request_error",
                                "code": "invalid_api_key",
                            }
                        },
                    )
                    return
                if "/down/" in self.path:
                    self._json(500, {"error": {"message": "boom"}})
                    return
                if self.path.endswith("/api/chat"):
                    self._json(
                        200,
                        {
                            "model": payload.get("model", "m"),
                            "message": {"role": "assistant", "content": "来自本地 Ollama"},
                            "done": True,
                        },
                    )
                    return
                if self.path.endswith("/chat/completions"):
                    if payload.get("stream"):
                        self._sse()
                        return
                    self._json(
                        200,
                        {
                            "id": "chatcmpl-1",
                            "object": "chat.completion",
                            "created": 0,
                            "model": payload.get("model", "m"),
                            "choices": [
                                {
                                    "index": 0,
                                    "message": {"role": "assistant", "content": "来自云端"},
                                    "finish_reason": "stop",
                                }
                            ],
                        },
                    )
                    return
                self._json(404, {"error": {"message": "not found"}})

            def _json(self, status: int, payload: dict) -> None:
                raw = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _sse(self) -> None:
                frame = {
                    "id": "chatcmpl-1",
                    "object": "chat.completion.chunk",
                    "created": 0,
                    "model": "m",
                    "choices": [
                        {"index": 0, "delta": {"content": "云端流"}, "finish_reason": None}
                    ],
                }
                raw = (
                    f"data: {json.dumps(frame)}\n\n".encode()
                    + b"data: [DONE]\n\n"
                )
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def base(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def url(self, prefix: str) -> str:
        return f"{self.base}/{prefix}"

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


@pytest.fixture
def upstream(monkeypatch):
    # 不能继承外层代理，否则 127.0.0.1 会被绕到代理上。
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    server = _Upstream()
    try:
        yield server
    finally:
        server.close()


@pytest.fixture
def store():
    return get_model_config_store()


def _create(store, **overrides):
    payload = {
        "name": "model",
        "provider": "mock",
        "model": "mock-model",
        "timeout": 10.0,
    }
    payload.update(overrides)
    return store.create(payload, "admin")


# ==================== 一、Key 文本形态 ====================
class TestApiKeyNormalization:
    def test_surrounding_spaces_are_stripped_before_storage(self, store):
        created = _create(store, name="spaced", api_key="   sk-live-abcdefgh   ")
        assert store.resolve(created["id"])["api_key"] == "sk-live-abcdefgh"
        assert store.get_public(created["id"])["api_key_masked"] == "sk-l…efgh"

    def test_full_width_spaces_are_stripped_too(self, store):
        created = _create(store, name="fullwidth", api_key="\u3000sk-fullwidth\u3000")
        assert store.resolve(created["id"])["api_key"] == "sk-fullwidth"

    def test_interior_newline_is_refused_with_actionable_chinese(self, store):
        with pytest.raises(ModelConfigError) as info:
            _create(store, name="newline", api_key="sk-abc\ndef")
        assert info.value.status_code == 400
        assert "重新复制" in info.value.detail
        assert store.list_models() == []

    def test_interior_full_width_space_is_refused(self, store):
        with pytest.raises(ModelConfigError) as info:
            _create(store, name="inner-space", api_key="sk-abc\u3000def")
        assert info.value.status_code == 400

    def test_overlong_key_is_refused_instead_of_truncated(self, store):
        with pytest.raises(ModelConfigError) as info:
            _create(store, name="too-long", api_key="sk-" + "a" * 5000)
        assert info.value.status_code == 400
        assert "过长" in info.value.detail

    def test_stored_key_is_stripped_again_when_building_the_provider(self, store):
        created = _create(store, name="builder", api_key="  sk-build-me  ")
        provider = provider_from_config(store.resolve(created["id"]))
        assert provider.api_key == "sk-build-me"

    def test_real_401_is_still_classified_as_invalid_key(self, store, upstream):
        """trim 不得污染分类：真 401 仍要判「Key 无效」，不能退化成通用失败。"""
        created = _create(
            store,
            name="bad-key",
            provider="openai",
            model="gpt-4o-mini",
            base_url=f"{upstream.url('bad')}/v1",
            api_key="   sk-bad-key-with-spaces   ",
        )
        provider = provider_from_config(store.resolve(created["id"]))
        assert provider.api_key == "sk-bad-key-with-spaces"

        with pytest.raises(ProviderHTTPError) as info:
            provider.chat([{"role": "user", "content": "hi"}])

        assert info.value.upstream_kind == "invalid_key"
        assert INVALID_KEY_MESSAGE in user_facing_failure(info.value)
        # 请求确实发出去了，且请求头里的 Key 已被修剪（没有多余空格）。
        assert upstream.calls[0]["auth"] == "Bearer sk-bad-key-with-spaces"


# ==================== 二、默认模型优先与顺序回落 ====================
def _chat_models(store, upstream):
    """默认 = 可用的本地 Ollama；另一个是坏 Key 的云端模型（顺序在后）。"""
    local = _create(
        store,
        name="local-ollama",
        provider="ollama",
        model="qwen2.5:3b",
        base_url=upstream.url("good"),
    )
    cloud = _create(
        store,
        name="cloud-bad",
        provider="openai",
        model="gpt-4o-mini",
        base_url=f"{upstream.url('bad')}/v1",
        api_key="sk-cloud-bad-abcdefgh",
    )
    store.mark_verified(local["id"], True)
    store.set_default(local["id"], "admin")
    return local, cloud


class TestChatHonoursTheDefaultModel:
    def test_default_model_is_the_first_choice(self, store, upstream):
        _chat_models(store, upstream)

        provider = provider_for_task("chat")

        assert isinstance(provider, FailoverProvider)
        assert provider.models[0].startswith("local-ollama")
        assert provider.models[1].startswith("cloud-bad")

        reply = provider.chat([{"role": "user", "content": "你好"}])
        assert reply == "来自本地 Ollama"
        assert upstream.calls[0]["path"] == "/good/api/chat"

    def test_failure_falls_back_in_order(self, store, upstream):
        broken = _create(
            store,
            name="local-broken",
            provider="ollama",
            model="qwen2.5:3b",
            base_url=upstream.url("down"),
        )
        backup = _create(
            store,
            name="cloud-good",
            provider="openai",
            model="gpt-4o-mini",
            base_url=f"{upstream.url('good')}/v1",
            api_key="sk-cloud-good-abcdefgh",
        )
        store.mark_verified(broken["id"], True)
        store.set_default(broken["id"], "admin")

        provider = provider_for_task("chat")
        reply = provider.chat([{"role": "user", "content": "你好"}])

        assert reply == "来自云端"
        assert [call["path"] for call in upstream.calls] == [
            "/down/api/chat",
            "/good/v1/chat/completions",
        ]
        assert backup["is_default"] is False

    def test_stream_falls_back_before_the_first_token(self, store, upstream):
        cloud = _create(
            store,
            name="cloud-bad",  # 字母序在前，但默认模型仍是本地那个
            provider="openai",
            model="gpt-4o-mini",
            base_url=f"{upstream.url('bad')}/v1",
            api_key="sk-cloud-bad-abcdefgh",
        )
        local = _create(
            store,
            name="local-good",
            provider="ollama",
            model="qwen2.5:3b",
            base_url=upstream.url("good"),
        )
        store.mark_verified(local["id"], True)
        store.set_default(local["id"], "admin")

        provider = provider_for_task("chat")
        assert provider.models[0].startswith("local-good")
        chunks = list(provider.chat_stream([{"role": "user", "content": "你好"}]))
        assert "".join(chunks) == "来自本地 Ollama"
        assert store.get_public(cloud["id"])["is_default"] is False

    def test_all_failures_report_each_model_and_reason(self, store, upstream):
        first = _create(
            store,
            name="first-bad-key",
            provider="openai",
            model="gpt-4o-mini",
            base_url=f"{upstream.url('bad')}/v1",
            api_key="sk-first-bad-abcdefgh",
        )
        second = _create(
            store,
            name="second-down",
            provider="ollama",
            model="qwen2.5:3b",
            base_url=upstream.url("down"),
        )
        store.mark_verified(first["id"], True)
        store.set_default(first["id"], "admin")

        provider = provider_for_task("chat")
        with pytest.raises(ProviderChainError) as info:
            provider.chat([{"role": "user", "content": "你好"}])

        message = str(info.value)
        assert "first-bad-key" in message
        assert "second-down" in message
        assert "API Key 无效" in message  # 401 → 具体原因，不是通用失败
        assert "HTTP 500" in message
        assert "sk-first-bad" not in message  # 密钥不得进用户可见文案
        # 展示层直接使用这条消息，不再套"这次没有拿到回复"。
        assert user_facing_failure(info.value) == message
        assert [call["path"] for call in upstream.calls] == [
            "/bad/v1/chat/completions",
            "/down/api/chat",
        ]


class TestStreamFailureAfterFirstTokenDoesNotSwitchModel:
    def test_second_model_is_not_used_after_tokens_were_emitted(self):
        class _Half(BaseProvider):
            def __init__(self):
                super().__init__("half", "half-model")

            def chat_stream(self, messages, **kwargs):
                yield "前半段"
                raise ProviderStreamIncompleteError("half", "连接提前结束")

        class _Never(BaseProvider):
            def __init__(self):
                super().__init__("never", "never-model")
                self.called = False

            def chat_stream(self, messages, **kwargs):
                self.called = True
                yield "不该出现"

        never = _Never()
        chain = FailoverProvider([_Half(), never], labels=["半截", "备选"])

        got = []
        with pytest.raises(ProviderStreamIncompleteError):
            for token in chain.chat_stream([]):
                got.append(token)

        assert got == ["前半段"]
        assert never.called is False


# ==================== 三、未测通不得设为默认 ====================
class TestDefaultRequiresVerification:
    def test_create_cannot_ask_for_default(self, store):
        with pytest.raises(ModelConfigError) as info:
            _create(store, name="new", is_default=True)
        assert info.value.status_code == 409
        assert "测试连接" in info.value.detail

    def test_unverified_model_cannot_be_set_default(self, store):
        first = _create(store, name="first")
        second = _create(store, name="second")
        with pytest.raises(ModelConfigError) as info:
            store.set_default(second["id"], "admin")
        assert info.value.status_code == 409
        assert "测试连接" in info.value.detail
        assert store.default_id() == first["id"]

    def test_failed_probe_revokes_permission(self, store):
        first = _create(store, name="first")
        second = _create(store, name="second")
        store.mark_verified(second["id"], True)
        store.set_default(second["id"], "admin")

        store.mark_verified(second["id"], False)  # 最近一次测试失败
        assert store.get_public(second["id"])["verified"] is False
        assert store.default_id() == second["id"], "默认位保持，但不再允许重新设默认"

        third = _create(store, name="third")
        with pytest.raises(ModelConfigError):
            store.set_default(second["id"], "admin")
        assert third["is_default"] is False

    def test_changing_the_key_requires_a_new_test(self, store):
        first = _create(store, name="first", api_key="sk-original-abcdefgh")
        store.mark_verified(first["id"], True)
        store.set_default(first["id"], "admin")

        store.update(first["id"], {"api_key": "sk-changed-abcdefgh"}, "admin")

        assert store.get_public(first["id"])["verified"] is False
        with pytest.raises(ModelConfigError) as info:
            store.set_default(first["id"], "admin")
        assert "测试连接" in info.value.detail

    def test_verification_is_exposed_to_the_console(self, store):
        created = _create(store, name="first")
        assert store.get_public(created["id"])["verified"] is False
        store.mark_verified(created["id"], True)
        public = store.get_public(created["id"])
        assert public["verified"] is True
        assert public["last_verified_at"]
