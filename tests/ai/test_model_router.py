"""Phase 5 — Model Router 测试（离线，无网络）。

覆盖：
- catalog 覆盖 Cloud/Local/Special 三层
- task→model：privacy 偏好 LOCAL；vision/context 能力过滤
- ⚠️ 根因：成本/时延约束**真正生效**（gateway 原实现是 no-op）
- 无候选 → 抛 NoModelAvailableError（不静默）
- router 关闭时 get_provider_for_task() 透传 get_provider()（零行为变化）
"""

import pytest

from src.ai import model_router as mr
from src.ai.providers import ProviderType, get_provider, reset_provider


def test_default_catalog_covers_three_tiers():
    tiers = {p.tier for p in mr.DEFAULT_CATALOG}
    assert mr.ModelTier.CLOUD in tiers
    assert mr.ModelTier.LOCAL in tiers
    assert mr.ModelTier.SPECIAL in tiers


def test_privacy_task_prefers_local():
    result = mr.route_task("privacy")
    prof = mr.get_profile(result.selected_model.model_id)
    assert prof is not None
    assert prof.tier == mr.ModelTier.LOCAL
    assert mr.provider_type_for(result) == ProviderType.OLLAMA


def test_cost_constraint_is_enforced():
    # gateway 原实现里 max_cost_per_token 是 `pass`；本 bridge 必须真的过滤。
    result = mr.route_task(None, max_cost_per_token=0.001, prefer_tier=None)
    prof = mr.get_profile(result.selected_model.model_id)
    assert prof is not None
    assert prof.cost_per_1k_tokens <= 0.001
    assert result.selected_model.model_id != "openai:gpt-5"  # 0.005 必须被排除


def test_latency_constraint_is_enforced():
    result = mr.route_task(None, max_latency_ms=1500, prefer_tier=None)
    prof = mr.get_profile(result.selected_model.model_id)
    assert prof is not None
    assert prof.typical_latency_ms <= 1500


def test_vision_capability_filter():
    result = mr.route_task("vision")
    # 文档写 "vision"，属性是 supports_vision —— bridge 必须归一化，否则全被滤掉。
    assert getattr(result.selected_model.capabilities, "supports_vision") is True


def test_min_context_filter():
    result = mr.route_task(None, min_context=100000, prefer_tier=None)
    assert result.selected_model.capabilities.max_context_tokens >= 100000


def test_no_candidate_raises_not_silent():
    with pytest.raises(mr.NoModelAvailableError):
        mr.route_task(None, required_capabilities=["embeddings"])
    with pytest.raises(mr.NoModelAvailableError):
        mr.route_task("does-not-exist")


def test_router_off_transparently_returns_default_provider(monkeypatch):
    monkeypatch.delenv("LIUHAO_MODEL_ROUTER", raising=False)
    reset_provider()
    try:
        assert mr.get_provider_for_task("reasoning") is get_provider()
    finally:
        reset_provider()


def test_router_on_same_provider_reuses_singleton(monkeypatch):
    monkeypatch.setenv("LIUHAO_MODEL_ROUTER", "on")
    monkeypatch.setenv("AI_PROVIDER_TYPE", ProviderType.OLLAMA)
    reset_provider()
    try:
        # privacy → LOCAL(ollama) == configured ollama ⇒ 复用既有单例
        assert mr.get_provider_for_task("privacy") is get_provider()
    finally:
        reset_provider()
        monkeypatch.delenv("AI_PROVIDER_TYPE", raising=False)
