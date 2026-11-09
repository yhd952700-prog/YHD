"""模型接入层：诊断失真 / 静默截断 / 流式解析的回归用例。

这里的每个用例都让真实 openai SDK 收到**真实 HTTP 响应**（``httpx.MockTransport``
只替换传输层），走完整 ``OpenAIProvider`` 路径，不直接给分类函数喂字符串。
"""
from __future__ import annotations

import json
import ssl

import httpx
import pytest

from src.ai import providers as providers_mod
from src.ai.net_guard import RuntimeTargetRejected


BASE = "https://api.example.com/v1"


def _provider(monkeypatch, handler, *, model: str = "fake-model") -> providers_mod.OpenAIProvider:
    import openai

    client = openai.OpenAI(
        api_key="sk-test-123456",
        base_url=BASE,
        http_client=httpx.Client(
            transport=httpx.MockTransport(handler), follow_redirects=False
        ),
        max_retries=0,
    )
    monkeypatch.setattr(
        providers_mod.OpenAIProvider, "_get_client", lambda self: client
    )
    # 目标校验由 tests/test_provider_error_classification.py 覆盖；这里不触发 DNS。
    monkeypatch.setattr(
        providers_mod, "assert_safe_runtime_target", lambda *a, **k: None
    )
    return providers_mod.OpenAIProvider(
        name="fake", model=model, api_key="sk-test-123456", base_url=BASE, timeout=10
    )


def _json_handler(*, status: int, payload: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload, request=request)

    return handler


def _text_handler(*, status: int, text: str, content_type: str = "text/html"):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status,
            text=text,
            headers={"content-type": content_type},
            request=request,
        )

    return handler


@pytest.mark.parametrize(
    "status,kind,fragment",
    [
        (500, "server_error", "服务端出错"),
        (502, "server_error", "服务端出错"),
        (503, "server_error", "服务端出错"),
        (400, "bad_request", "请求不合法"),
        (407, "proxy_auth", "代理要求认证"),
    ],
)
def test_h1_http_failures_are_not_all_collapsed(
    monkeypatch, status, kind, fragment
):
    provider = _provider(
        monkeypatch,
        _json_handler(status=status, payload={"error": {"message": "upstream"}}),
    )
    with pytest.raises(providers_mod.ProviderHTTPError) as info:
        provider.chat([{"role": "user", "content": "hi"}])
    exc = info.value
    assert exc.upstream_kind == kind
    message = providers_mod.user_facing_failure(exc, model="fake-model")
    assert fragment in message
    # H1 的核心要求：这几类故障都不许把用户赶去"检查密钥"。
    assert "检查密钥" not in message


def test_h1_redirect_and_200_html_are_distinguishable(monkeypatch):
    provider = _provider(
        monkeypatch,
        _json_handler(status=302, payload={"error": {"message": "redirect"}}),
    )
    with pytest.raises(providers_mod.ProviderHTTPError) as info:
        provider.chat([{"role": "user", "content": "hi"}])
    assert info.value.upstream_kind == "redirect_blocked"
    assert "检查密钥" not in providers_mod.user_facing_failure(info.value, "fake-model")

    provider = _provider(
        monkeypatch,
        _text_handler(status=200, text="<html><body>login</body></html>"),
    )
    with pytest.raises(providers_mod.ProviderHTTPError) as info:
        provider.chat([{"role": "user", "content": "hi"}])
    assert info.value.upstream_kind == "invalid_response"
    # 200 但返回非 JSON：技术详情必须标注"疑似被网关或登录页拦截"。
    detail = providers_mod.technical_detail_for(info.value)
    assert "疑似被网关或登录页拦截" in detail
    assert "检查密钥" not in providers_mod.user_facing_failure(info.value, "fake-model")


def test_m7_404_distinguishes_endpoint_from_model(monkeypatch):
    provider = _provider(
        monkeypatch,
        _json_handler(status=404, payload={"error": {"message": "Not Found"}}),
    )
    with pytest.raises(providers_mod.ProviderHTTPError) as info:
        provider.chat([{"role": "user", "content": "hi"}])
    assert info.value.upstream_kind == "endpoint_not_found"
    assert "/v1" in providers_mod.user_facing_failure(info.value, "fake-model")

    provider = _provider(
        monkeypatch,
        _json_handler(
            status=404,
            payload={"error": {"message": "The model `fake-model` does not exist"}},
        ),
    )
    with pytest.raises(providers_mod.ProviderHTTPError) as info:
        provider.chat([{"role": "user", "content": "hi"}])
    assert info.value.upstream_kind == "model_not_found"


_TRUNC_BODY = {
    "id": "c1",
    "object": "chat.completion",
    "model": "fake-model",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "被截断的"},
            "finish_reason": "length",
        }
    ],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
}


def test_h2_non_stream_length_is_truncated_not_success(monkeypatch):
    provider = _provider(monkeypatch, _json_handler(status=200, payload=_TRUNC_BODY))
    with pytest.raises(providers_mod.ProviderTruncatedError) as info:
        provider.chat([{"role": "user", "content": "hi"}])
    assert info.value.partial_text == "被截断的"
    assert info.value.finish_reason == "length"
    assert "长度上限" in providers_mod.user_facing_failure(info.value, "fake-model")


def test_h2_stream_length_is_truncated_after_yielding_content(monkeypatch):
    body = (
        'data: {"choices":[{"delta":{"content":"被截断的"},"finish_reason":null}]}\n\n'
        'data: {"choices":[{"delta":{},"finish_reason":"length"}]}\n\n'
        "data: [DONE]\n\n"
    )
    provider = _provider(
        monkeypatch,
        _text_handler(status=200, text=body, content_type="text/event-stream"),
    )
    chunks: list[str] = []
    with pytest.raises(providers_mod.ProviderTruncatedError) as info:
        for token in provider.chat_stream([{"role": "user", "content": "hi"}]):
            chunks.append(token)
    assert "".join(chunks) == "被截断的"
    assert info.value.finish_reason == "length"


def test_m4_whole_json_response_to_stream_is_success(monkeypatch):
    payload = {
        "id": "c2",
        "object": "chat.completion",
        "model": "fake-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "完整的整段内容"},
                "finish_reason": "stop",
            }
        ],
    }
    provider = _provider(monkeypatch, _json_handler(status=200, payload=payload))
    assert (
        "".join(provider.chat_stream([{"role": "user", "content": "hi"}]))
        == "完整的整段内容"
    )


def test_l1_sse_multiline_data_is_reassembled(monkeypatch):
    body = (
        'data: {"choices":[{"delta":{"content":"多行"},\n'
        'data: "finish_reason":null}]}\n\n'
        'data: {"choices":[{"delta":{"content":"帧正常"},"finish_reason":"stop"}]}\n\n'
        "data: [DONE]\n\n"
    )
    provider = _provider(
        monkeypatch,
        _text_handler(status=200, text=body, content_type="text/event-stream"),
    )
    assert "".join(provider.chat_stream([{"role": "user", "content": "hi"}])) == "多行帧正常"


def test_m5_retry_delay_has_jitter_and_honours_retry_after():
    provider = providers_mod.OpenAIProvider(
        name="fake", model="fake-model", api_key="sk-test", base_url=BASE
    )
    exc = providers_mod.ProviderHTTPError("429", status_code=429, upstream_kind="rate_limited", retry_after=2.0)
    assert provider._retry_delay(exc, 0) >= 2.0
    # 无 Retry-After 时退避带 0.8~1.2 抖动，不再是固定 1.0。
    delays = {round(provider._retry_delay(providers_mod.ProviderHTTPError("x", status_code=429), 0), 4) for _ in range(12)}
    assert len(delays) > 1
    assert all(0.8 <= d <= 1.2 for d in delays)


def test_l5_dns_and_certificate_failures_are_not_retried():
    assert (
        providers_mod._is_retryable_failure(
            RuntimeTargetRejected("模型服务地址当前无法解析")
        )
        is False
    )
    cert = ssl.SSLCertVerificationError(
        "certificate verify failed: self-signed certificate"
    )
    assert providers_mod._is_retryable_failure(cert) is False
    wrapped = ConnectionError("Connection error.")
    wrapped.__cause__ = cert
    assert providers_mod._is_retryable_failure(wrapped) is False


def test_m1_net_guard_chinese_is_passed_through():
    exc = RuntimeTargetRejected("模型服务地址指向内网/本机，已按安全策略拒绝。")
    message = providers_mod.user_facing_failure(exc, model="fake-model")
    assert "指向内网" in message
    assert "Ollama" in message


def test_l3_proxy_keyword_is_accepted():
    provider = providers_mod.OpenAIProvider(
        "fake", "fake-model", api_key="sk-test", proxy="http://127.0.0.1:9"
    )
    assert provider.proxy == "http://127.0.0.1:9"


class _Stop(Exception):
    pass


class _StubProvider(providers_mod.BaseProvider):
    def __init__(self, label: str, *, exc: Exception | None = None, text: str = "ok"):
        super().__init__(name=label, model=f"{label}-model", api_key="sk-stub")
        self._exc = exc
        self._text = text

    def generate(self, prompt: str, **kwargs) -> str:
        if self._exc is not None:
            raise self._exc
        return self._text

    def chat(self, messages, **kwargs) -> str:
        return self.generate("", **kwargs)

    def chat_stream(self, messages, **kwargs):
        if self._exc is not None:
            raise self._exc
        yield self._text


def test_m8_non_retryable_error_does_not_silently_switch():
    chain = providers_mod.FailoverProvider(
        [
            _StubProvider("primary", exc=providers_mod.ProviderHTTPError("401", status_code=401, upstream_kind="invalid_key")),
            _StubProvider("backup", text="backup-answer"),
        ]
    )
    with pytest.raises(providers_mod.ProviderHTTPError):
        chain.chat([{"role": "user", "content": "hi"}])
    assert chain.last_fell_back_from == []


def test_m8_retryable_error_switches_with_notice_and_attribution():
    chain = providers_mod.FailoverProvider(
        [
            _StubProvider("primary", exc=providers_mod.ProviderHTTPError("503", status_code=503, upstream_kind="server_error")),
            _StubProvider("backup", text="backup-answer"),
        ]
    )
    assert chain.chat([{"role": "user", "content": "hi"}]) == "backup-answer"
    assert chain.last_fell_back_from == ["primary-model"]
    assert chain.last_served_model == "backup-model"
    assert "备用模型" in chain.last_failover_notice

    events = list(chain.chat_stream([{"role": "user", "content": "hi"}]))
    notices = [e for e in events if isinstance(e, dict)]
    assert notices and notices[0]["code"] == "failover"
    assert "backup" in notices[0]["message"]


def test_l2_anthropic_does_not_claim_streaming():
    provider = providers_mod.AnthropicProvider(
        name="anthropic", model="claude-3-5-sonnet-20241022", api_key="sk-ant-test"
    )
    assert provider.capabilities.supports_streaming is False


def test_h2_chat_does_not_send_default_max_tokens(monkeypatch):
    """chat 路径不再默认写死 ``max_tokens=1000``：不配就不传，交给模型。"""
    seen: dict = {}
    payload = {
        "id": "c3",
        "object": "chat.completion",
        "model": "fake-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "ok"},
                "finish_reason": "stop",
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content or b"{}")
        return httpx.Response(200, json=payload, request=request)

    provider = _provider(monkeypatch, handler)
    assert provider.chat([{"role": "user", "content": "hi"}]) == "ok"
    assert "max_tokens" not in seen["body"]

    monkeypatch.setenv("AI_PROVIDER_MAX_TOKENS", "4096")
    assert provider.chat([{"role": "user", "content": "hi"}]) == "ok"
    assert seen["body"]["max_tokens"] == 4096


def _cert_exception(message: str, code: int | None = None, verify_message: str | None = None):
    """构造与真实握手失败同形的 ``ssl.SSLCertVerificationError``。"""
    exc = ssl.SSLCertVerificationError(message)
    if code is not None:
        exc.verify_code = code
    if verify_message is not None:
        exc.verify_message = verify_message
    return exc


def test_m2_certificate_failure_is_not_reported_as_service_down():
    """TLS 证书失败常被 SDK 包成类名含 connection 的异常，不能报"服务没启动"。"""
    cert = _cert_exception("certificate verify failed: self-signed certificate")
    wrapped = ConnectionError("Connection error.")
    wrapped.__cause__ = cert
    message = providers_mod.user_facing_failure(wrapped, model="fake-model")
    assert "无法验证模型服务的证书" in message
    # 不同链路信任来源不同（httpx 读系统商店 / requests 只认 certifi），
    # 界面不能断言"一定读"或"一定不读"，只能给可执行动作。
    assert "信任来源不同" in message
    assert "可直连的网络或地址" in message
    assert "确认本地服务已启动" not in message
    # 不能退回旧版的含糊说法（只让人"信任证书"却不给可执行动作）。
    assert "让系统信任该证书" not in message


def test_m2_certificate_failure_split_by_actionable_cause():
    """不受信任 / 域名不匹配 / 过期，三种动作不同的证书失败必须分开说。"""
    untrusted = _cert_exception(
        "certificate verify failed: unable to get local issuer certificate",
        20,
        "unable to get local issuer certificate",
    )
    hostname = _cert_exception(
        "certificate verify failed: Hostname mismatch, certificate is not valid for 'api.example.com'.",
        62,
        "Hostname mismatch, certificate is not valid for 'api.example.com'.",
    )
    expired = _cert_exception(
        "certificate verify failed: certificate has expired",
        10,
        "certificate has expired",
    )
    not_yet = _cert_exception(
        "certificate verify failed: certificate is not yet valid",
        9,
        "certificate is not yet valid",
    )

    assert providers_mod.certificate_failure_kind(untrusted) == "certificate_untrusted"
    assert providers_mod.certificate_failure_kind(hostname) == "certificate_hostname"
    assert providers_mod.certificate_failure_kind(expired) == "certificate_expired"
    assert providers_mod.certificate_failure_kind(not_yet) == "certificate_not_yet_valid"

    untrusted_text = providers_mod.user_facing_failure(untrusted, model="m1")
    hostname_text = providers_mod.user_facing_failure(hostname, model="m2")
    expired_text = providers_mod.user_facing_failure(expired, model="m3")
    assert "私有 CA 或公司代理" in untrusted_text and "信任来源不同" in untrusted_text
    assert "不匹配" in hostname_text and "核对接口地址" in hostname_text
    assert "已过期" in expired_text and "系统时间" in expired_text
    # 三类文案都不应把英文 SSL 原文甩给用户。
    for text in (untrusted_text, hostname_text, expired_text):
        assert "certificate verify failed" not in text
        assert "SSL" not in text

    # 认不出具体原因时仍是"证书问题"，不能退回"检查网络"。
    unknown = _cert_exception("certificate verify failed", 99)
    assert providers_mod.certificate_failure_kind(unknown) == "certificate"
    assert "证书校验失败" in providers_mod.user_facing_failure(unknown, model="m4")


def test_m5_non_stream_retry_notifies_via_callback():
    """非流式重试也要把「正在重试」交给界面（回调），不能只有流式可见。"""

    class _TimeoutOnce(providers_mod.BaseProvider):
        def __init__(self):
            super().__init__(name="slow", model="slow-model", api_key="sk-test")
            self.calls = 0

        def chat(self, messages, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise TimeoutError("slow")
            return "ok"

        def generate(self, prompt, **kwargs):
            return self.chat("", **kwargs)

    provider = _TimeoutOnce()
    provider.retry_base_delay = 0.0
    notices: list[str] = []
    assert provider.chat_with_retry(
        [{"role": "user", "content": "hi"}], on_retry=notices.append
    ) == "ok"
    assert provider.calls == 2
    assert notices and "重试" in notices[0]


def test_l6_probe_separates_dns_failure_from_private_address():
    """连通性测试要能区分「解析不了」与「指向内网」，不能都叫服务端错误。"""
    from src.gateway import models as gateway_models

    assert (
        gateway_models._classify_error(
            RuntimeTargetRejected("模型服务地址当前无法解析，已停止请求；请检查主机名或网络。")
        )
        == "dns_failed"
    )
    assert (
        gateway_models._classify_error(
            RuntimeTargetRejected("模型服务地址指向内网/本机，已按安全策略拒绝。")
        )
        == "address_rejected"
    )


class _TruncatedProvider(providers_mod.BaseProvider):
    def __init__(self):
        super().__init__(name="trunc", model="trunc-model", api_key="sk-test")

    def generate(self, prompt: str, **kwargs) -> str:
        raise providers_mod.ProviderTruncatedError("模型服务", "length", "被截断的")

    def chat(self, messages, **kwargs) -> str:
        raise providers_mod.ProviderTruncatedError("模型服务", "length", "被截断的")

    def chat_stream(self, messages, **kwargs):
        yield "被截断的"
        raise providers_mod.ProviderTruncatedError("模型服务", "length")


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    from src.ai import liuhao as lh_module
    from src.ai.conversation_store import ConversationStore

    store = ConversationStore(db_path=str(tmp_path / "conv.db"))
    monkeypatch.setattr(lh_module, "get_conversation_store", lambda: store)
    return store


def test_h2_liuhao_reports_truncated_status(isolated_store):
    """上层状态机：截断轮次必须是 truncated，正文保留，不能记 completed。"""
    from src.ai.liuhao import LiuHaoAssistant

    assistant = LiuHaoAssistant(name="trunc-assistant", provider=_TruncatedProvider())
    result = assistant.chat("写一个很长的回答")
    assert result["status"] == "truncated"
    assert result["reply"] == "被截断的"
    assert "长度上限" in assistant.last_turn_error

    chunks = list(assistant.chat_stream("再来一次"))
    assert "".join(chunks) == "被截断的"
    assert assistant.last_turn_status == "truncated"
    assert "长度上限" in assistant.last_turn_error

