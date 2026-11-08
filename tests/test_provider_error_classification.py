"""上游错误分类的端到端回归：真实 HTTP 响应走完整 provider 路径。

为什么要这组用例：``providers`` 会把上游异常换成中性中文（防止泄露密钥 /
内网地址），分类如果发生在脱敏之后，"额度耗尽"就会退化成"限流"（用户一直
重试而不去充值）、"Key 已吊销"退化成"Key 无效"（用户一直重试而不换 Key）。

这里不用"直接给分类函数喂字符串"的写法：用真实 openai SDK + ``httpx``
MockTransport 造出**真实的 HTTP 429/401 响应**，让 SDK 抛真实异常，
经 ``OpenAIProvider.generate`` → ``ProviderHTTPError`` → 网关 ``_probe``
一路断言最终 ``reason``。
"""
from __future__ import annotations

import httpx
import pytest


def _cfg(**overrides):
    cfg = {
        "name": "e2e",
        "provider": "openai",
        "model": "gpt-4o-mini",
        "base_url": "https://api.openai.com/v1",
        "api_key": "sk-e2e-123456",
        "timeout": 5.0,
        "enabled": True,
        "is_default": False,
    }
    cfg.update(overrides)
    return cfg


def _serve(monkeypatch, *, status: int, payload: dict) -> None:
    """让 OpenAIProvider 收到一个真实的 HTTP 响应（不联网）。"""
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
    # 目标校验在 tests/test_providers.py 覆盖；这里不依赖外网 DNS。
    monkeypatch.setattr(
        providers_mod, "assert_safe_runtime_target", lambda *a, **k: None
    )


def _probe(monkeypatch) -> dict:
    from src.gateway import models as gateway_models

    monkeypatch.setattr(gateway_models, "_guard_probe_target", lambda cfg: None)
    return gateway_models._probe(_cfg())


def test_quota_exhausted_reaches_the_gateway(monkeypatch):
    """真实 429 + ``insufficient_quota`` → ``quota_exhausted``（不是限流）。"""
    _serve(
        monkeypatch,
        status=429,
        payload={
            "error": {
                "message": (
                    "You exceeded your current quota, please check your plan "
                    "and billing details."
                ),
                "type": "insufficient_quota",
                "code": "insufficient_quota",
            }
        },
    )
    result = _probe(monkeypatch)
    assert result["ok"] is False
    assert result["reason"] == "quota_exhausted"
    # 用户看到的是"去充值"，不是"稍后重试"。
    assert "充值" in result["detail"]
    assert "insufficient" not in result["detail"].lower()
    assert "sk-e2e" not in str(result)


def test_key_revoked_reaches_the_gateway(monkeypatch):
    """真实 401 + ``revoked`` → ``key_revoked``（不是 invalid_key）。"""
    _serve(
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
    result = _probe(monkeypatch)
    assert result["ok"] is False
    assert result["reason"] == "key_revoked"
    assert "吊销" in result["detail"]
    # 主文案不得出现上游英文/type；原文只允许进「技术详情」。
    assert "revoked" not in result["detail"].lower()
    assert "invalid_request_error" not in result["detail"]
    detail = result["technical_detail"]
    assert "This API key has been revoked." in detail
    assert len(detail) <= 120
    assert "invalid_request_error" not in detail
    # 全文除脱敏占位 sk-*** 外，不得出现 Key / 认证头痕迹。
    redacted = str(result).replace("sk-***", "")
    assert "sk-" not in redacted
    assert "Bearer" not in redacted
    assert "api_key" not in redacted


def test_plain_rate_limit_is_still_rate_limited(monkeypatch):
    """没有额度信号的 429 仍归为限流——别把所有 429 都当成额度耗尽。"""
    _serve(
        monkeypatch,
        status=429,
        payload={
            "error": {
                "message": "Rate limit reached for requests per minute.",
                "type": "requests",
                "code": "rate_limit_exceeded",
            }
        },
    )
    result = _probe(monkeypatch)
    assert result["reason"] == "rate_limited"
    assert "稍后" in result["detail"]


def test_plain_401_is_invalid_key(monkeypatch):
    _serve(
        monkeypatch,
        status=401,
        payload={
            "error": {
                "message": "Incorrect API key provided: sk-e2e-123456.",
                "type": "invalid_request_error",
                "code": "invalid_api_key",
            }
        },
    )
    result = _probe(monkeypatch)
    assert result["reason"] == "invalid_key"
    # Key 不能回显到用户可见文案里。
    assert "sk-e2e" not in str(result)


def test_provider_error_carries_machine_signal_but_neutral_text(monkeypatch):
    """provider 层：异常消息已中性化，``upstream_kind``/状态码仍在。"""
    from src.ai.providers import OpenAIProvider, ProviderHTTPError

    _serve(
        monkeypatch,
        status=429,
        payload={
            "error": {
                "message": "You exceeded your current quota.",
                "code": "insufficient_quota",
            }
        },
    )
    provider = OpenAIProvider(
        name="e2e",
        model="gpt-4o-mini",
        api_key="sk-e2e-123456",
        base_url="https://api.openai.com/v1",
        timeout=5.0,
    )
    with pytest.raises(ProviderHTTPError) as exc_info:
        provider.generate("ping")
    err = exc_info.value
    assert err.status_code == 429
    assert err.upstream_kind == "quota_exhausted"
    assert "insufficient" not in str(err).lower()
    assert "quota" not in str(err).lower()


def test_ollama_quota_body_is_classified_too(monkeypatch):
    """Ollama 路径同样在脱敏前分类（真实 HTTP 402 + 余额不足）。"""
    import requests

    from src.ai import providers as providers_mod
    from src.ai.providers import OllamaProvider, ProviderHTTPError

    class _Response:
        status_code = 402
        text = '{"error":"insufficient balance"}'

        def json(self):
            return {"error": "insufficient balance"}

    monkeypatch.setattr(requests, "post", lambda url, **kwargs: _Response())
    provider = OllamaProvider(
        name="o", model="qwen2.5:3b", base_url="http://127.0.0.1:11434", timeout=5
    )
    with pytest.raises(ProviderHTTPError) as exc_info:
        provider.generate("ping")
    assert exc_info.value.upstream_kind == "quota_exhausted"
    assert "insufficient" not in str(exc_info.value).lower()
    assert providers_mod  # 仅用于断言模块可导入


class _FakeHttpResponse:
    """DeepSeekProvider 用 requests，这里造一个同形状的响应。"""

    def __init__(self, status_code: int, text: str, payload: dict | None = None):
        self.status_code = status_code
        self.text = text
        self._payload = payload or {}
        self.closed = False

    def json(self):
        return self._payload

    def close(self):
        self.closed = True


def _serve_deepseek(monkeypatch, response: _FakeHttpResponse) -> list:
    import requests

    from src.ai import providers as providers_mod

    calls: list = []

    def fake_post(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return response

    monkeypatch.setattr(requests, "post", fake_post)
    # 不联网做 DNS：DeepSeek 的运行时目标校验由 tests/test_providers.py 覆盖。
    monkeypatch.setattr(
        providers_mod, "assert_safe_runtime_target", lambda *a, **k: None
    )
    return calls


def _deepseek():
    from src.ai.providers import DeepSeekProvider

    return DeepSeekProvider(
        name="deepseek-e2e",
        model="deepseek-chat",
        api_key="sk-b15e-test-key",
        base_url="https://api.deepseek.com/v1",
        timeout=5.0,
    )


def test_deepseek_invalid_key_is_classified_and_neutral(monkeypatch):
    """DeepSeek 401 → ``invalid_key``；上游英文与 Key 都不能进异常消息。"""
    from src.ai.providers import ProviderHTTPError

    _serve_deepseek(
        monkeypatch,
        _FakeHttpResponse(
            401,
            '{"error":{"message":"Authentication Fails (no such user)","type":"authentication_error"}}',
        ),
    )
    with pytest.raises(ProviderHTTPError) as exc_info:
        _deepseek().chat([{"role": "user", "content": "ping"}])
    err = exc_info.value
    assert err.status_code == 401
    assert err.upstream_kind == "invalid_key"
    assert "Authentication" not in str(err)
    assert "sk-b15e" not in str(err)


def test_deepseek_insufficient_balance_is_quota_exhausted(monkeypatch):
    """DeepSeek 402 + 余额不足 → ``quota_exhausted``（不是笼统失败）。"""
    from src.ai.providers import ProviderHTTPError

    calls = _serve_deepseek(
        monkeypatch,
        _FakeHttpResponse(402, '{"error":{"message":"Insufficient Balance"}}'),
    )
    with pytest.raises(ProviderHTTPError) as exc_info:
        _deepseek().chat([{"role": "user", "content": "ping"}])
    err = exc_info.value
    assert err.upstream_kind == "quota_exhausted"
    assert "Insufficient" not in str(err)
    assert calls and calls[0]["url"] == "https://api.deepseek.com/v1/chat/completions"
    # 安全口径：不跟随重定向、不走环境代理。
    assert calls[0]["allow_redirects"] is False
    assert calls[0]["proxies"] == {"http": None, "https": None}


def test_deepseek_chat_returns_message_content(monkeypatch):
    """非流式路径正常返回 content（不把上游 JSON 原文兜底给用户）。"""
    response = _FakeHttpResponse(
        200,
        "{}",
        {
            "choices": [{"message": {"content": "你好，我是 DeepSeek。"}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 8},
        },
    )
    _serve_deepseek(monkeypatch, response)
    assert _deepseek().chat([{"role": "user", "content": "hi"}]) == "你好，我是 DeepSeek。"
    assert response.closed is True


def test_deepseek_chat_stream_yields_deltas(monkeypatch):
    """流式路径按 SSE ``data:`` 行解析 delta，``[DONE]`` 结束。"""
    response = _FakeHttpResponse(200, "")
    response.iter_lines = lambda: iter(
        [
            b'data: {"choices":[{"delta":{"content":"\xe4\xbd\xa0"}}]}',
            b"",
            b'data: {"choices":[{"delta":{"content":"\xe5\xa5\xbd"}}]}',
            b"data: [DONE]",
        ]
    )
    _serve_deepseek(monkeypatch, response)
    chunks = list(_deepseek().chat_stream([{"role": "user", "content": "hi"}]))
    assert chunks == ["你", "好"]
    assert response.closed is True
