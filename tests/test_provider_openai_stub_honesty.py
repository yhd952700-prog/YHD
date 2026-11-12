"""#345 —— ``src/providers/openai.py`` 的 stub 必须 fail-closed，不得伪造成功。

缺陷形态（修复前）
------------------
``src/providers/openai.py`` 是一个**没有任何真实 OpenAI 客户端**的契约 stub，但它：

* ``chat`` / ``generate`` / ``embeddings`` 返回**带真实型号名**的伪造载荷
  （``"model": "gpt-4o-mini"``、``"model": "text-embedding-3-small"``），调用方
  无法区分"模型回答了"与"硬编码回显"；
* ``health_check()`` 无条件返回 ``{"status": "healthy", "provider": "openai"}`` ——
  一个**结构上不可能报故障**的探针，接上任何监控都永远是绿的。

修复后语义（本文件守护）
------------------------
* 未显式 opt-in 模拟时，三个方法**抛** :class:`ProviderNotConfiguredError`（fail-closed）；
* 显式 opt-in（``LIUHAO_ALLOW_SIMULATED_EXECUTION=1``，复用仓库既有契约，不另发明机制）
  时，返回的载荷必须带 ``simulated=True``，且**不得**再冒充真实型号名；
* ``health_check()`` 在任何情况下都**不得**返回 ``"healthy"``，并给出机器可读原因
  （``reason="not_configured"``）与可行动的下一步。

反橡皮图章（这些断言在修复前必红）
--------------------------------
* 修复前 ``health_check()`` 恒返回 ``{"status": "healthy"}``，而
  ``test_health_check_never_reports_healthy`` 断言 ``status != "healthy"`` ⇒ 必红；
* 修复前 ``embeddings()`` 直接返回伪造载荷、从不抛异常，而
  ``test_unconfigured_embeddings_fails_closed`` 断言抛 ``ProviderNotConfiguredError``
  ⇒ ``pytest.raises`` 抓不到异常，用例 Failed（必红）。
"""

from __future__ import annotations

import pytest

from src.providers.llm_base import ProviderNotConfiguredError
from src.providers.openai import OpenAIProvider
from src.providers.registry import get_provider

_OPT_IN = "LIUHAO_ALLOW_SIMULATED_EXECUTION"


@pytest.fixture
def provider() -> OpenAIProvider:
    return OpenAIProvider()


@pytest.fixture
def no_opt_in(monkeypatch):
    monkeypatch.delenv(_OPT_IN, raising=False)


@pytest.fixture
def opted_in(monkeypatch):
    monkeypatch.setenv(_OPT_IN, "1")


# ---------------------------------------------------------------------------
# health_check：一个做不到这件事的组件，永远不得报告 healthy
# ---------------------------------------------------------------------------


def test_health_check_never_reports_healthy(provider, no_opt_in):
    health = provider.health_check()
    assert health["status"] != "healthy", (
        f"未配置真实后端却报告 {health['status']!r} —— 这正是 #345 的缺陷"
    )
    assert health["reason"] == "not_configured"
    assert health.get("action"), "必须给出可行动的下一步"


def test_health_check_exposes_machine_readable_fields(provider, no_opt_in):
    health = provider.health_check()
    assert health["provider"] == "openai"
    # 修复前这里是常量 {"status": "healthy", "provider": "openai"}，
    # 不含任何能表达"后端不可用"的字段。
    assert set(health) >= {"status", "provider", "reason", "action"}


def test_health_check_still_not_healthy_under_simulation(provider, opted_in):
    """即便显式 opt-in 了模拟，它仍然不是一个真实后端 —— 依然不得报 healthy。"""
    assert provider.health_check()["status"] != "healthy"


# ---------------------------------------------------------------------------
# 伪造回答的方法：默认 fail-closed
# ---------------------------------------------------------------------------


def test_unconfigured_chat_fails_closed(provider, no_opt_in):
    with pytest.raises(ProviderNotConfiguredError):
        provider.chat([{"role": "user", "content": "hi"}])


def test_unconfigured_generate_fails_closed(provider, no_opt_in):
    with pytest.raises(ProviderNotConfiguredError):
        provider.generate("hi")


def test_unconfigured_embeddings_fails_closed(provider, no_opt_in):
    with pytest.raises(ProviderNotConfiguredError):
        provider.embeddings(["hi"])


# ---------------------------------------------------------------------------
# opt-in 模拟：允许伪造，但必须标注，且不得冒充真实型号名
# ---------------------------------------------------------------------------


def test_simulated_chat_is_labelled_and_stops_impersonating(provider, opted_in):
    payload = provider.chat([{"role": "user", "content": "hi"}])
    assert payload["simulated"] is True
    assert "gpt-4o-mini" not in str(payload), (
        "模拟载荷仍冒充真实型号名 —— 这正是 #345 描述的伪造"
    )


def test_simulated_generate_is_labelled(provider, opted_in):
    payload = provider.generate("hi")
    assert payload["simulated"] is True
    assert "gpt-4o-mini" not in str(payload)


def test_simulated_embeddings_is_labelled(provider, opted_in):
    payload = provider.embeddings(["hi"])
    assert payload["simulated"] is True
    assert "text-embedding-3-small" not in str(payload)


# ---------------------------------------------------------------------------
# 结构性不变式（反橡皮图章）：任何一条伪造路径都必须"要么关闭、要么标注"
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "call",
    [
        lambda p: p.chat([{"role": "user", "content": "x"}]),
        lambda p: p.generate("x"),
        lambda p: p.embeddings(["x"]),
    ],
    ids=["chat", "generate", "embeddings"],
)
def test_fabrication_paths_are_either_closed_or_labelled(monkeypatch, call):
    """让"静默伪造"无法回归。

    无论将来谁改实现，只要有任何一条路径在**未 opt-in** 时返回未标注的载荷，
    或 **opt-in** 时返回不带 ``simulated`` 的载荷，这条断言都会红。
    """
    provider = OpenAIProvider()

    monkeypatch.delenv(_OPT_IN, raising=False)
    with pytest.raises(ProviderNotConfiguredError):
        call(provider)

    monkeypatch.setenv(_OPT_IN, "1")
    payload = call(provider)
    assert payload.get("simulated") is True


# ---------------------------------------------------------------------------
# 接线未被破坏：注册表仍能解析 openai
# ---------------------------------------------------------------------------


def test_registry_still_resolves_openai_to_this_adapter():
    assert isinstance(get_provider("openai"), OpenAIProvider)
