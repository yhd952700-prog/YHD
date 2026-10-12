"""#345 续 —— ``src/providers/self_host.py`` 的 stub 必须 fail-closed，不得伪造成功。

缺陷形态（修复前）
------------------
``src/providers/self_host.py`` 是与 ``src/providers/openai.py`` **成对**的契约 stub
（``llm_base`` 的模块文档把 ``openai`` 与 ``self_host`` 并列描述为"没有客户端却返回
伪造载荷、``health_check()`` 无条件返回 healthy"的两个 stub）。提交 ``c3643614`` 只修好了
``openai`` 那一个，``self_host`` 原样保留：

* ``chat`` 把**调用方自己的 prompt 原样回显**当作答，并挂上型号名 ``self-hosted-model``；
* ``generate`` 回显 prompt；
* ``embeddings`` 返回 ``self-hosted-embedding`` 的全零向量；
* ``health_check()`` 无条件返回 ``{"status": "healthy"}`` —— 结构上不可能报故障的探针。

它可经 ``src.providers.registry.get_provider("self_host")`` 与知识库嵌入管线到达；
``src/knowledge/embedding.py`` 的文档甚至**谎称**该 stub 会抛
``ProviderNotConfiguredError``（它从不抛），于是 ``EmbeddingPipeline(provider_key="self_host")``
在**未 opt-in** 时静默返回 1536 维全零向量，正是 ``6a8889ab`` 要消灭的那类静默伪造。

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
  ⇒ ``pytest.raises`` 抓不到异常，用例 Failed（必红）；
* 修复前 ``EmbeddingPipeline(provider_key="self_host").embed()`` 返回全零向量，
  而 ``test_embedding_pipeline_self_host_fails_closed_not_zeros`` 断言必抛 ⇒ 必红。
"""

from __future__ import annotations

import pytest

from src.providers.llm_base import ProviderNotConfiguredError
from src.providers.registry import get_provider
from src.providers.self_host import SelfHostProvider

_OPT_IN = "LIUHAO_ALLOW_SIMULATED_EXECUTION"


@pytest.fixture
def provider() -> SelfHostProvider:
    return SelfHostProvider()


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
    assert health["provider"] == "self_host"
    # 修复前这里是常量 {"status": "healthy", "provider": "self_host"}，
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
    assert "self-hosted-model" not in str(payload), (
        "模拟载荷仍冒充真实型号名 —— 这正是 #345 描述的伪造"
    )


def test_simulated_generate_is_labelled(provider, opted_in):
    payload = provider.generate("hi")
    assert payload["simulated"] is True
    assert "self-hosted-model" not in str(payload)


def test_simulated_embeddings_is_labelled(provider, opted_in):
    payload = provider.embeddings(["hi"])
    assert payload["simulated"] is True
    assert "self-hosted-embedding" not in str(payload)


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
    provider = SelfHostProvider()

    monkeypatch.delenv(_OPT_IN, raising=False)
    with pytest.raises(ProviderNotConfiguredError):
        call(provider)

    monkeypatch.setenv(_OPT_IN, "1")
    payload = call(provider)
    assert payload.get("simulated") is True


# ---------------------------------------------------------------------------
# 跨模块：知识库嵌入管线不得再把 self_host 的静默失败变成全零向量
# ---------------------------------------------------------------------------


def test_embedding_pipeline_self_host_fails_closed_not_zeros(no_opt_in):
    """修复前：``EmbeddingPipeline(provider_key="self_host").embed(...)`` 返回 len=1536
    全零向量且**不抛**（静默伪造）；``pytest.raises`` 抓不到异常 ⇒ 必红。

    ``src/knowledge/embedding.py`` 的文档断言 self_host stub 会抛
    ``ProviderNotConfiguredError``；本用例让该断言第一次成为事实。
    """
    from src.knowledge.embedding import EmbeddingPipeline

    with pytest.raises(ProviderNotConfiguredError):
        EmbeddingPipeline(provider_key="self_host").embed("the quick brown fox")


def test_embedding_pipeline_self_host_has_no_zero_vector_fallback(no_opt_in):
    """结构性不变式：只要 embed() 在未 opt-in 时**不抛**却返回全零向量就红。"""
    from src.knowledge.embedding import EmbeddingPipeline

    try:
        vector = EmbeddingPipeline(provider_key="self_host").embed("the quick brown fox")
    except ProviderNotConfiguredError:
        return  # 正确：诚实失败
    pytest.fail(
        "embed() 未抛出 provider 的失败，而是返回了 "
        f"len={len(vector)} all_zero={all(v == 0.0 for v in vector)} 的伪造向量"
    )


# ---------------------------------------------------------------------------
# 接线未被破坏：注册表仍能解析 self_host 的全部别名到本适配器
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("alias", ["self_host", "self-host", "selfhost"])
def test_registry_still_resolves_self_host_aliases(alias):
    assert isinstance(get_provider(alias), SelfHostProvider)
