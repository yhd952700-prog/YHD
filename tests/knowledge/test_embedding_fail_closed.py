"""#345 续 —— ``EmbeddingPipeline.embed()`` 不得把 provider 的诚实失败吞成全零向量。

缺陷形态（修复前）
------------------
``EmbeddingPipeline.embed()``（``src/knowledge/embedding.py``）用
``except Exception: pass`` 吞掉 provider 抛出的一切异常，再兜底
``return [0.0] * 1536``。于是 ``provider_key="openai"``（一个无真实后端、已按 #345
契约 fail-closed 的 stub）在**未 opt-in** 时本应抛 ``ProviderNotConfiguredError``，
却被吞掉，调用方拿到的是一段 **1536 维全零向量**。

为什么这比抛错严重
------------------
全零向量是一段"看起来完全合法"的嵌入——维度对、类型对、无异常。它会**静默**污染
下游检索与相似度计算（所有文档与查询相似度恒为 0）。这不是降级，是伪造数据：
上游（``src/providers/openai.py``，提交 c3643614）已经诚实化，这里又把它变回谎言。

修复后语义（本文件守护）
------------------------
* 未 opt-in：``embed()`` 必须把 ``ProviderNotConfiguredError`` **原样抛出**，绝不
  返回全零向量；
* opt-in 模拟（``LIUHAO_ALLOW_SIMULATED_EXECUTION=1``，复用仓库既有契约）：允许返回
  provider 标注的模拟向量；
* 显式声明的 ``mock`` provider 仍按契约返回零向量——那是**诚实契约**，不是吞错，
  其行为不得改变。
"""

from __future__ import annotations

import pytest

from src.knowledge.embedding import EmbeddingPipeline, embed_text
from src.providers.llm_base import ProviderNotConfiguredError

_OPT_IN = "LIUHAO_ALLOW_SIMULATED_EXECUTION"


@pytest.fixture(autouse=True)
def _no_opt_in(monkeypatch):
    """默认关闭模拟，逼出 fail-closed 路径。"""
    monkeypatch.delenv(_OPT_IN, raising=False)


# ---------------------------------------------------------------------------
# 未 opt-in：必须 fail-closed，而不是伪造一个全零向量
# ---------------------------------------------------------------------------


def test_unconfigured_openai_embed_raises_instead_of_returning_zeros():
    """修复前：这里 return 了 ``len=1536, all_zero=True``，``pytest.raises`` 抓不到异常 ⇒ 必红。"""
    pipeline = EmbeddingPipeline(provider_key="openai")
    with pytest.raises(ProviderNotConfiguredError):
        pipeline.embed("the quick brown fox")


def test_embed_never_fabricates_a_zero_vector():
    """结构性不变式（反橡皮图章）。

    无论将来谁改实现，只要 ``embed()`` 在 provider 失败时**不抛**却返回一段全零向量，
    这条断言都会红——它把"静默伪造"钉死。
    """
    pipeline = EmbeddingPipeline(provider_key="openai")
    try:
        vector = pipeline.embed("the quick brown fox")
    except ProviderNotConfiguredError:
        return  # 正确：诚实失败
    pytest.fail(
        "embed() 未抛出 provider 的失败，而是返回了 "
        f"len={len(vector)} all_zero={all(v == 0.0 for v in vector)} 的伪造向量"
    )


def test_unconfigured_embed_documents_also_propagates():
    """``embed_documents`` 逐条调用 ``embed`` —— 同样不得吞错伪造。"""
    pipeline = EmbeddingPipeline(provider_key="openai")
    with pytest.raises(ProviderNotConfiguredError):
        pipeline.embed_documents(["one", "two"])


# ---------------------------------------------------------------------------
# opt-in 模拟：允许返回标注载荷
# ---------------------------------------------------------------------------


def test_opted_in_openai_embed_returns_the_provider_vector(monkeypatch):
    monkeypatch.setenv(_OPT_IN, "1")
    pipeline = EmbeddingPipeline(provider_key="openai")
    vector = pipeline.embed("the quick brown fox")
    # opt-in 下 openai stub 返回带 simulated=True 的零向量载荷；embed 取第一个向量。
    assert len(vector) == 1536


# ---------------------------------------------------------------------------
# 显式 mock：行为不得改变（诚实契约，非吞错）
# ---------------------------------------------------------------------------


def test_mock_provider_contract_is_unchanged():
    vector = embed_text("hello", provider_key="mock")
    assert len(vector) == 1536
    assert all(v == 0.0 for v in vector), "显式 mock 的零向量契约被破坏了"


def test_embed_service_delegates_without_swallowing():
    """``EmbeddingService`` 只是转发；它不得引入任何兜底。"""
    from src.knowledge.embedding import EmbeddingService

    service = EmbeddingService(provider_name="openai")
    with pytest.raises(ProviderNotConfiguredError):
        service.embed("the quick brown fox")
