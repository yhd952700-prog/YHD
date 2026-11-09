"""代理绕过与错误文案的回归测试（红一/红二/黄一/黄二）。

这些断言在修复前必须是失败的：它们锁住的是"开着系统代理连本机服务"
以及"首次 5xx 被说成对方故障"这两类会把用户引向错误方向的行为。
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from src.ai import providers as P


def _fake_response(status: int, text: str, headers=None):
    return SimpleNamespace(status_code=status, text=text, headers=headers or {})


# --------------------------------------------------------------------------- #
# 红一：OpenAI 兼容 provider 对回环/本机地址不继承系统代理
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "base_url",
    [
        "http://127.0.0.1:8000/v1",
        "http://127.0.0.1:11434/v1",
        "http://localhost:1234/v1",
        "http://[::1]:8000/v1",
        "http://0.0.0.0:8000/v1",
        "http://192.168.1.10:8000/v1",
    ],
)
def test_env_proxy_is_not_inherited_for_local_targets(monkeypatch, base_url):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    provider = P.OpenAIProvider("openai", model="stub-ok", api_key="k", base_url=base_url)
    assert provider._env_proxy_applies() is False


def test_env_proxy_still_applies_for_remote_targets(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    provider = P.OpenAIProvider("openai", model="gpt-4o-mini", api_key="k", base_url="https://api.openai.com/v1")
    assert provider._env_proxy_applies() is True


def test_explicit_proxy_wins_even_for_local_targets(monkeypatch):
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    provider = P.OpenAIProvider(
        "openai", model="stub-ok", api_key="k", base_url="http://127.0.0.1:8000/v1", proxy="http://127.0.0.1:9"
    )
    assert provider._env_proxy_applies() is False
    assert provider._client_trust_env is False


def test_net_guard_local_service_targets():
    from src.ai.net_guard import is_local_service_target

    assert is_local_service_target("http://127.0.0.1:8000/v1")
    assert is_local_service_target("http://localhost:11434")
    assert is_local_service_target("http://[::1]:8000")
    assert is_local_service_target("http://0.0.0.0:8000")
    assert is_local_service_target("http://192.168.1.10:8000")
    assert not is_local_service_target("https://api.openai.com/v1")
    assert not is_local_service_target("https://api.deepseek.com/v1")


def test_local_5xx_is_reported_as_local_unreachable():
    exc = P.ProviderHTTPError("模型服务返回 503", status_code=503, upstream_kind="local_unreachable")
    copy = P.user_facing_failure(exc, model="stub-ok")
    assert "连不上本地服务" in copy
    assert "稍后重试" not in copy
    assert "联系服务提供方" not in copy


def test_local_connection_failure_uses_local_copy():
    exc = ConnectionError("connection refused")
    setattr(exc, "_liuhao_local_target", True)
    copy = P.user_facing_failure(exc, model="stub-ok")
    assert "连不上本地服务" in copy
    assert "联系服务提供方" not in copy


# --------------------------------------------------------------------------- #
# 红二：首次 5xx（此前从未成功）必须提示核对接口地址
# --------------------------------------------------------------------------- #
def test_first_5xx_without_prior_success_mentions_endpoint():
    with pytest.raises(P.ProviderHTTPError) as caught:
        P._raise_upstream_http("模型服务", _fake_response(550, "Bad Gateway"), had_success=False)
    exc = caught.value
    # 既有契约：5xx 一律是 server_error（不改 upstream_kind，避免影响既有断言）。
    # "此前从未成功"由独立属性携带，文案据此提示核对地址。
    assert exc.upstream_kind == "server_error"
    assert exc.never_succeeded is True
    copy = P.user_facing_failure(exc, model="deepseek-chat")
    assert "核对接口地址" in copy
    assert "联系服务提供方" not in copy


def test_5xx_after_success_keeps_server_error_copy():
    with pytest.raises(P.ProviderHTTPError) as caught:
        P._raise_upstream_http("模型服务", _fake_response(550, "Bad Gateway"), had_success=True)
    assert caught.value.upstream_kind == "server_error"
    assert P.user_facing_failure(caught.value, model="m") == P._with_model(P.SERVER_ERROR_MESSAGE, "m")


def _provider_with_transport(status: int):
    """真 SDK + MockTransport：验证 ``had_success=self._ever_succeeded`` 的接线。

    用公网 IP 字面量（不需要 DNS）而不是回环：回环上的 5xx 会按红一归为
    "连不上本地服务"，那是另一条（正确的）分支，测不到红二。
    """
    import httpx
    import openai

    base_url = "https://1.1.1.1/v1"
    provider = P.OpenAIProvider(
        "openai", model="stub-ok", api_key="k", base_url=base_url
    )
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            status, json={"error": {"message": "upstream"}}
        )
    )
    provider._client = openai.OpenAI(
        api_key="k",
        base_url=base_url,
        max_retries=0,
        http_client=httpx.Client(transport=transport),
    )
    return provider


def test_first_5xx_through_real_sdk_tells_user_to_check_the_url():
    provider = _provider_with_transport(503)
    with pytest.raises(P.ProviderHTTPError) as caught:
        provider.chat([{"role": "user", "content": "hi"}])
    exc = caught.value
    assert exc.never_succeeded is True, "实例从未成功过，5xx 必须标记 never_succeeded"
    copy = P.user_facing_failure(exc, model="stub-ok")
    assert "核对接口地址" in copy
    assert "联系服务提供方" not in copy


def test_5xx_after_one_success_does_not_blame_the_url():
    provider = _provider_with_transport(503)
    provider._ever_succeeded = True
    with pytest.raises(P.ProviderHTTPError) as caught:
        provider.chat([{"role": "user", "content": "hi"}])
    exc = caught.value
    assert exc.never_succeeded is False
    copy = P.user_facing_failure(exc, model="stub-ok")
    assert "核对接口地址" not in copy
    assert P.SERVER_ERROR_MESSAGE in copy


# --------------------------------------------------------------------------- #
# 黄一：模型名不存在要与通用 bad_request 分开
# --------------------------------------------------------------------------- #
def test_model_missing_400_is_not_bad_request():
    body = json.dumps(
        {
            "error": {
                "message": (
                    "The supported API model names are deepseek-flash, deepseek-v4-pro, "
                    "but you passed deepseek-chat-xxx."
                ),
                "type": "invalid_request_error",
            }
        }
    )
    with pytest.raises(P.ProviderHTTPError) as caught:
        P._raise_upstream_http("模型服务", _fake_response(400, body))
    exc = caught.value
    assert exc.upstream_kind == "model_not_found"
    copy = P.user_facing_failure(exc, model="deepseek-chat-xxx")
    assert copy.startswith(P.MODEL_NOT_FOUND_MESSAGE)
    assert P.BAD_REQUEST_MESSAGE not in copy


# --------------------------------------------------------------------------- #
# 黄二：读超时要说明"等待超时"并给出已重试次数
# --------------------------------------------------------------------------- #
class _AlwaysTimeout(P.BaseProvider):
    def __init__(self):
        super().__init__("stub", "stub-model", api_key="k", timeout=5)
        self.calls = 0

    def chat(self, messages, **kwargs):
        self.calls += 1
        raise P.ProviderTimeoutError("stub", 5, before_first_token=True, model="stub-model")


def test_retry_exhausted_timeout_reports_waited_and_retry_count():
    provider = _AlwaysTimeout()
    with pytest.raises(P.ProviderTimeoutError) as caught:
        provider.chat_with_retry([])
    assert provider.calls == 2, "超时只允许重试一次"
    copy = P.user_facing_failure(caught.value, model="stub-model")
    assert "等待模型响应超时" in copy
    assert "已自动重试 1 次" in copy
    # 既有承诺不能丢：冷启动慢仍要作为可能原因保留。
    assert "首次加载较慢" in copy
