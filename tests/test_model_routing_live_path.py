"""模型路由接入真实调用链：从模型管理选模型、未命中回落默认、决策可核对。

路由本身复用 ``src/model_gateway`` 的 ``LiuhaoModelRouter``；这里验证的是
"存进模型管理的模型能否被路由选中 / 落空是否回落默认 / 日志能否对上账"。
"""

from __future__ import annotations

import logging

import pytest

from src.ai import model_router as routing


def _profile(
    model_id: str, provider: str, model: str, base_url: str = ""
) -> routing.ModelProfile:
    return routing.ModelProfile(
        model_id=model_id,
        provider=provider,
        model_name=model,
        tier=routing._tier_for(provider),
        capabilities=routing._capabilities_for_provider(provider),
        base_url=base_url,
        vendor=routing.vendor_for(provider, base_url),
    )


class FakeStore:
    def __init__(self, records):
        self._records = records

    def list_models(self, *, visible_to=None, allow_unowned=False):
        # 与 ModelConfigStore.list_models 的新签名保持一致；测试桩不做过滤，
        # 归属过滤由 tests/ai 下的真实存储用例覆盖。
        return list(self._records)


RECORDS = [
    {
        "id": "m-openai",
        "provider": "openai",
        "model": "gpt-4o-mini",
        "enabled": True,
        "is_default": False,
    },
    {
        "id": "m-ollama",
        "provider": "ollama",
        "model": "qwen2.5:3b",
        "enabled": True,
        "is_default": True,
    },
    {
        "id": "m-disabled",
        "provider": "anthropic",
        "model": "claude-sonnet",
        "enabled": False,
        "is_default": False,
    },
]


class TestCatalogFromStore:
    def test_only_enabled_records_become_profiles(self):
        profiles = routing.catalog_from_store(store=FakeStore(RECORDS))
        assert set(profiles) == {"m-openai", "m-ollama"}

    def test_provider_tier_mapping(self):
        profiles = routing.catalog_from_store(store=FakeStore(RECORDS))
        assert profiles["m-ollama"].tier is routing.ModelTier.LOCAL
        assert profiles["m-openai"].tier is routing.ModelTier.CLOUD

    def test_incomplete_record_is_skipped(self):
        profiles = routing.catalog_from_store(
            store=FakeStore([{"id": "broken", "provider": "", "model": "x"}])
        )
        assert profiles == {}

    def test_empty_store_is_an_empty_catalog_not_an_error(self):
        assert routing.catalog_from_store(store=FakeStore([])) == {}

    def test_vendor_comes_from_base_url_not_the_protocol_label(self):
        """DeepSeek 以 OpenAI 兼容协议注册时，真实厂商是 DeepSeek。"""
        profiles = routing.catalog_from_store(
            store=FakeStore(
                [
                    {
                        "id": "m-ds",
                        "provider": "openai",
                        "model": "deepseek-chat",
                        "enabled": True,
                        "base_url": "https://api.deepseek.com/v1",
                    }
                ]
            )
        )
        profile = profiles["m-ds"]
        assert profile.provider == "openai"  # 路由仍按协议适配器走，不改行为
        assert profile.vendor == "deepseek"  # 日志/审计/界面用真实厂商


class TestRouteFromStore:
    def test_chat_alias_routes_within_saved_models(self):
        result = routing.route_task_from_store("chat", store=FakeStore(RECORDS))
        assert result.selected_model.model_id in {"m-openai", "m-ollama"}
        assert result.reason

    def test_embedding_prefers_a_provider_with_embeddings(self):
        result = routing.route_task_from_store("embedding", store=FakeStore(RECORDS))
        assert result.selected_model.provider in {"ollama", "openai"}

    def test_no_models_raises_so_the_caller_can_fall_back(self):
        with pytest.raises(routing.NoModelAvailableError):
            routing.route_task_from_store("chat", store=FakeStore([]))

    def test_capability_miss_raises_instead_of_silently_downgrading(self):
        with pytest.raises(routing.NoModelAvailableError) as info:
            routing.route_task_from_store("reasoning", store=FakeStore(RECORDS))
        assert "reasoning" in str(info.value)

    def test_unknown_task_type_is_explicit(self):
        with pytest.raises(routing.NoModelAvailableError) as info:
            routing.route_task_from_store("nonsense", store=FakeStore(RECORDS))
        assert "Unknown task_type" in str(info.value)


class TestLiveProviderEntry:
    @pytest.fixture
    def resolved(self, monkeypatch):
        calls = []
        sentinel = object()

        def fake_resolve(model_id=None, **kwargs):
            calls.append(model_id)
            return sentinel

        monkeypatch.setattr(routing, "resolve_provider", fake_resolve)
        return calls, sentinel

    def test_routes_then_resolves_the_selected_model(self, monkeypatch, resolved, caplog):
        calls, sentinel = resolved
        profiles = routing.catalog_from_store(store=FakeStore(RECORDS))
        monkeypatch.setattr(
            routing, "catalog_from_store", lambda store=None, **kwargs: dict(profiles)
        )

        with caplog.at_level(logging.INFO, logger="liuhao.ai.model_router"):
            provider = routing.provider_for_task("chat")

        assert provider is sentinel
        assert len(calls) == 1
        assert calls[0] in {"m-openai", "m-ollama"}
        assert "model routing decision" in caplog.text
        assert "task=chat" in caplog.text

    def test_explicit_model_id_wins_over_routing(
        self, monkeypatch, resolved, caplog
    ):
        calls, sentinel = resolved
        monkeypatch.setattr(
            routing,
            "catalog_from_store",
            lambda store=None, **kwargs: {"x": _profile("x", "mock", "m")},
        )
        with caplog.at_level(logging.INFO, logger="liuhao.ai.model_router"):
            provider = routing.provider_for_task("chat", model_id="m-explicit")

        assert provider is sentinel
        assert calls == ["m-explicit"]
        assert "explicit model_id=m-explicit" in caplog.text

    def test_empty_store_falls_back_to_default_still_logged(
        self, monkeypatch, resolved, caplog
    ):
        calls, sentinel = resolved
        monkeypatch.setattr(routing, "catalog_from_store", lambda store=None, **kwargs: {})
        with caplog.at_level(logging.INFO, logger="liuhao.ai.model_router"):
            provider = routing.provider_for_task("chat")

        assert provider is sentinel
        assert calls == [None]  # 默认模型 = 历史 resolve_provider() 行为
        assert "model routing fallback" in caplog.text

    def test_kill_switch_skips_routing(self, monkeypatch, resolved, caplog):
        calls, sentinel = resolved
        monkeypatch.setenv("LIUHAO_MODEL_ROUTER", "off")
        monkeypatch.setattr(
            routing,
            "catalog_from_store",
            lambda store=None, **kwargs: {"x": _profile("x", "mock", "m")},
        )
        with caplog.at_level(logging.INFO, logger="liuhao.ai.model_router"):
            provider = routing.provider_for_task("chat")

        assert provider is sentinel
        assert calls == [None]
        assert "disabled by LIUHAO_MODEL_ROUTER" in caplog.text

    def test_profile_alias_chat_points_at_default_profile(self):
        assert routing._resolve_profile("chat").prefer_tier is routing.ModelTier.CLOUD
        assert routing._resolve_profile("summarization").max_cost_per_token == 0.001

    def test_log_records_vendor_beside_the_protocol_provider(self, monkeypatch, caplog):
        class FakeProvider:
            base_url = "https://api.deepseek.com/v1"

        monkeypatch.setattr(routing, "resolve_provider", lambda *a, **k: FakeProvider())
        monkeypatch.setattr(
            routing,
            "catalog_from_store",
            lambda store=None, **kwargs: {
                "ds": _profile(
                    "ds", "openai", "deepseek-chat", "https://api.deepseek.com/v1"
                )
            },
        )
        with caplog.at_level(logging.INFO, logger="liuhao.ai.model_router"):
            routing.provider_for_task("chat")

        assert "provider=openai" in caplog.text
        assert "vendor=deepseek" in caplog.text
