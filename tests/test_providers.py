"""Provider 默认行为回归测试（方向②：端到端真实运行）。

锁定 ``get_provider()`` 的默认安全行为：
- 无 ``AI_PROVIDER_TYPE`` 时默认 MockProvider（不触发假 key 网络调用）
- 默认 model 为 ``mock-model``（非占位符 ``[REDACTED]``）
- 显式指定 type 时仍按 env 生效（openai -> OpenAIProvider）
"""

import pytest


def _clear_env(monkeypatch):
    for k in (
        "AI_PROVIDER_TYPE",
        "AI_PROVIDER_MODEL",
        "AI_PROVIDER_KEY",
        "AI_PROVIDER_NAME",
    ):
        monkeypatch.delenv(k, raising=False)


def test_get_provider_defaults_to_mock(monkeypatch):
    _clear_env(monkeypatch)
    from src.ai.providers import MockProvider, get_provider, reset_provider

    reset_provider()
    try:
        p = get_provider()
        assert isinstance(p, MockProvider)
    finally:
        reset_provider()


def test_get_provider_default_model_not_placeholder(monkeypatch):
    _clear_env(monkeypatch)
    from src.ai.providers import get_provider, reset_provider

    reset_provider()
    try:
        p = get_provider()
        assert p.model == "mock-model"
        assert "[REDACTED]" not in p.model
    finally:
        reset_provider()


def test_get_provider_respects_explicit_type(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER_TYPE", "openai")
    monkeypatch.setenv("AI_PROVIDER_MODEL", "gpt-4o-mini")
    from src.ai.providers import OpenAIProvider, get_provider, reset_provider

    reset_provider()
    try:
        p = get_provider()
        assert isinstance(p, OpenAIProvider)
        assert p.model == "gpt-4o-mini"
    finally:
        reset_provider()
