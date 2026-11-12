"""Anthropic 端点归一化：禁止再拼出 ``/v1/v1/messages``。

anthropic SDK 的资源路径自带 ``/v1``，且 base_url 是**字符串拼接**而不是 URL
解析。预设/用户填 ``https://api.anthropic.com/v1`` 时，最终 URL 会变成
``https://api.anthropic.com/v1/v1/messages``，聊天直接 404。

这些用例锁住两件事：
1. ``AnthropicProvider._get_client`` 交给 SDK 的 base_url 已剥掉尾部 ``/v1``；
2. 拉取模型的 ``model_discovery._request_target`` 对带/不带 ``/v1`` 两种输入
   都只产出**一个** ``/v1``（这条路径不走归一化函数，靠自身兼容分支）。
"""
from __future__ import annotations

import anthropic
import pytest

from src.ai import model_discovery
from src.ai import providers as providers_mod


def _final_messages_url(client) -> str:
    """复现 SDK 的真实 URL 拼接，返回 /v1/messages 的最终地址。

    直接调用 SDK 自己的 ``_prepare_url``：``messages.create`` 走的就是它
    （``base_url.raw_path`` 字符串拼接），比自己手写一份更贴近真实请求。
    """
    return str(client._prepare_url("/v1/messages"))


@pytest.mark.parametrize(
    "raw",
    [
        "https://api.anthropic.com",
        "https://api.anthropic.com/",
        "https://api.anthropic.com/v1",
        "https://api.anthropic.com/v1/",
        "  https://api.anthropic.com/v1  ",
    ],
)
def test_provider_client_always_hits_single_v1(raw: str):
    """四种写法（带/不带 /v1、带/不带尾斜杠、带空白）都只落一个 /v1。"""
    provider = providers_mod.AnthropicProvider(
        "anthropic", "claude-3-5-sonnet-latest", "sk-test", base_url=raw
    )
    try:
        url = _final_messages_url(provider._get_client())
    finally:
        provider.close()
    assert url == "https://api.anthropic.com/v1/messages"


def test_client_base_url_passed_to_sdk_has_no_trailing_v1():
    """缺陷本体在"传给 SDK 的值"上，单独断言，失败时一眼看出是谁改回去了。"""
    provider = providers_mod.AnthropicProvider(
        "anthropic",
        "claude-3-5-sonnet-latest",
        "sk-test",
        base_url="https://api.anthropic.com/v1",
    )
    try:
        client = provider._get_client()
        assert provider.base_url == "https://api.anthropic.com/v1"  # 原值不动
        assert client.base_url.raw_path in (b"/", b"")
    finally:
        provider.close()


def test_unnormalized_base_url_reproduces_double_v1():
    """根因证据：不经归一化时，SDK 自己确实拼出 ``/v1/v1/messages``。

    这条同时是"什么时候可以删掉归一化"的探针：若未来 SDK 改成 URL 解析、
    不再重复拼接，本用例会失败，提示可以简化 ``_anthropic_base_url``。
    """
    client = anthropic.Anthropic(
        api_key="sk-test", base_url="https://api.anthropic.com/v1"
    )
    try:
        assert _final_messages_url(client) == "https://api.anthropic.com/v1/v1/messages"
    finally:
        client.close()


@pytest.mark.parametrize(
    "raw",
    [
        "https://api.anthropic.com/v1",
        "https://api.anthropic.com",
        "https://api.anthropic.com/v1/",
    ],
)
def test_model_discovery_still_lists_with_single_v1(raw: str):
    """拉取模型这条路径不经过归一化函数，必须靠自身兼容分支仍然正确。"""
    url, headers, is_ollama = model_discovery._request_target("anthropic", raw)
    assert url == "https://api.anthropic.com/v1/models"
    assert "anthropic-version" in headers
    assert is_ollama is False
