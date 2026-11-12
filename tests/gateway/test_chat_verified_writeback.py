"""⑤ verified 只能由**真实调用**触发，mock/模拟/回退一律不许标「已验证」。

真机冒烟：真实 DeepSeek 对话成功后模型列表仍显示「未测试」。修复是回写
``verified``，但判据必须是"这轮确实走了真实 provider"——否则演示/降级应答
也会把模型标成已验证，这个字段立刻失去意义。
"""
from __future__ import annotations

import types

import pytest

from src.ai.providers import MockProvider
from src.gateway import chat as chat_module


class _FakeStore:
    def __init__(self, rows):
        self.rows = rows
        self.marked = []

    def resolve(self, model_id):
        return self.rows[model_id]

    def mark_verified(self, model_id, ok, *, actor=None):
        self.marked.append((model_id, ok, actor))


@pytest.fixture
def install_store(monkeypatch):
    def _install(rows):
        store = _FakeStore(rows)
        monkeypatch.setattr(
            "src.ai.model_config.get_model_config_store", lambda: store
        )
        return store

    return _install


def _assistant(provider):
    return types.SimpleNamespace(provider=provider)


def test_real_provider_success_marks_verified(install_store):
    store = install_store({"m-real": {"provider": "deepseek"}})
    provider = types.SimpleNamespace(model_config_id="m-real")

    chat_module._mark_served_model_verified(_assistant(provider), "admin")

    assert store.marked == [("m-real", True, "admin")]


def test_mock_provider_type_is_never_marked(install_store):
    """provider 类型是 mock 时，即使这一轮"成功"也不能写成真实可用。"""
    store = install_store({"m-mock": {"provider": "mock"}})
    provider = types.SimpleNamespace(model_config_id="m-mock")

    chat_module._mark_served_model_verified(_assistant(provider), "admin")

    assert store.marked == []


def test_unconfigured_provider_type_is_never_marked(install_store):
    store = install_store({"m-empty": {"provider": ""}})
    provider = types.SimpleNamespace(model_config_id="m-empty")

    chat_module._mark_served_model_verified(_assistant(provider), "admin")

    assert store.marked == []


def test_mock_provider_instance_is_never_marked(install_store):
    """MockProvider 实例（默认/演示路径）不能借配置行把模型标成已验证。"""
    store = install_store({"m-demo": {"provider": "deepseek"}})
    provider = MockProvider("mock", "mock")
    provider.model_config_id = "m-demo"

    chat_module._mark_served_model_verified(_assistant(provider), "admin")

    assert store.marked == []


def test_failover_success_is_never_marked(install_store):
    """切到备用模型后主配置未必可用，不能替主配置冒领「已验证」。"""
    store = install_store({"m-primary": {"provider": "deepseek"}})
    provider = types.SimpleNamespace(
        model_config_id="m-primary", last_fell_back_from="m-other"
    )

    chat_module._mark_served_model_verified(_assistant(provider), "admin")

    assert store.marked == []


def test_missing_model_config_id_is_ignored(install_store):
    store = install_store({})
    chat_module._mark_served_model_verified(
        _assistant(types.SimpleNamespace(model_config_id="")), "admin"
    )
    assert store.marked == []


def test_store_failure_does_not_break_the_chat(install_store):
    """回写失败只是少一次状态更新，不能把已经成功的对话变成异常。"""
    store = install_store({})  # resolve 会 KeyError
    provider = types.SimpleNamespace(model_config_id="m-missing")

    chat_module._mark_served_model_verified(_assistant(provider), "admin")

    assert store.marked == []
