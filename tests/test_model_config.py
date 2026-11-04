"""模型配置中心：持久化、密钥加密、默认唯一与运行时热加载。

这些用例刻意断言**行为**而不是实现细节：密钥以密文落库、接口形态只露脱敏串、
保存后解析到新模型不需要重置任何进程级单例。
"""
from __future__ import annotations

import json

import pytest

from src.ai import providers
from src.ai.model_config import (
    ModelConfigError,
    get_model_config_store,
    mask_secret,
)
from src.ai.providers import (
    AIProviderConfigError,
    ai_provider_status,
    get_provider,
    resolve_provider,
)


@pytest.fixture
def store():
    return get_model_config_store()


def _create(store, **overrides):
    payload = {
        "name": "local-ollama",
        "provider": "mock",
        "model": "mock-model",
        "timeout": 30.0,
    }
    payload.update(overrides)
    return store.create(payload, "admin")


class TestPersistenceAndMasking:
    def test_empty_registry_is_honest(self, store):
        assert store.list_models() == []
        assert store.default_id() is None
        assert store.resolve() is None

    def test_create_roundtrip_and_first_model_becomes_default(self, store):
        created = _create(store, name="first", model="m-1")
        assert created["is_default"] is True
        listed = store.list_models()
        assert [m["name"] for m in listed] == ["first"]
        assert store.default_id() == created["id"]
        assert store.resolve()["model"] == "m-1"

    def test_second_model_does_not_steal_default(self, store):
        first = _create(store, name="first")
        second = _create(store, name="second", is_default=False)
        assert store.default_id() == first["id"]
        assert second["is_default"] is False

    def test_explicit_default_is_exclusive(self, store):
        first = _create(store, name="first")
        second = _create(store, name="second", is_default=True)
        assert store.default_id() == second["id"]
        assert store.get_public(first["id"])["is_default"] is False

    def test_set_default_switches_immediately(self, store):
        first = _create(store, name="first")
        second = _create(store, name="second")
        store.set_default(second["id"], "admin")
        assert store.resolve()["id"] == second["id"]
        assert store.get_public(first["id"])["is_default"] is False

    def test_delete_default_promotes_the_oldest_remaining(self, store):
        first = _create(store, name="first", is_default=True)
        second = _create(store, name="second")
        result = store.delete(first["id"], "admin")
        assert result["default_id"] == second["id"]
        assert store.default_id() == second["id"]

    def test_disabled_model_is_not_resolved_but_stays_listed(self, store):
        created = _create(store, name="first")
        store.update(created["id"], {"enabled": False}, "admin")
        assert store.resolve() is None
        assert store.resolve(created["id"]) is None
        assert store.get_public(created["id"])["enabled"] is False

    def test_resolve_accepts_name_or_id(self, store):
        created = _create(store, name="by-name")
        assert store.resolve("by-name")["id"] == created["id"]
        assert store.resolve(created["id"])["id"] == created["id"]

    def test_duplicate_name_is_refused(self, store):
        _create(store, name="twin")
        with pytest.raises(ModelConfigError) as exc:
            _create(store, name="twin")
        assert exc.value.status_code == 409

    def test_unknown_provider_and_blank_fields_are_refused(self, store):
        with pytest.raises(ModelConfigError):
            _create(store, provider="nope")
        with pytest.raises(ModelConfigError):
            _create(store, name="  ")
        with pytest.raises(ModelConfigError):
            _create(store, base_url="ftp://example.com")

    def test_masking_only_reveals_edges(self):
        assert mask_secret(None) is None
        assert mask_secret("short") == "****"
        assert mask_secret("sk-abcdefghijklmnop") == "sk-a…mnop"


class TestSecretsAtRest:
    def test_key_is_ciphertext_in_the_database(self, store):
        secret = "sk-live-do-not-leak-1234567890"
        created = _create(store, name="cloud", provider="mock", api_key=secret)
        conn = store._connect()
        raw = conn.execute(
            "SELECT api_key_enc FROM ai_model_configs WHERE id = ?", (created["id"],)
        ).fetchone()["api_key_enc"]
        assert raw.startswith("enc:v1:")
        assert secret not in raw
        # 公开投影只有脱敏串，且没有任何密文/明文。
        public = store.get_public(created["id"])
        assert public["api_key_set"] is True
        assert public["api_key_masked"] == "sk-l…7890"
        dumped = json.dumps(public)
        assert secret not in dumped
        assert "enc:v1:" not in dumped

    def test_resolved_config_carries_the_plaintext_for_building_only(self, store):
        secret = "sk-resolve-only-123456"
        created = _create(store, provider="mock", api_key=secret)
        assert store.resolve(created["id"])["api_key"] == secret

    def test_plaintext_row_is_refused_not_treated_as_secret(self, store):
        created = _create(store, provider="mock")
        conn = store._connect()
        conn.execute(
            "UPDATE ai_model_configs SET api_key_enc = 'raw-plaintext' WHERE id = ?",
            (created["id"],),
        )
        conn.commit()
        with pytest.raises(ModelConfigError) as exc:
            store.resolve(created["id"])
        assert "明文" in exc.value.detail


class TestProviderHotReload:
    def test_saved_model_is_used_without_restarting(self, store, monkeypatch):
        monkeypatch.setattr(providers, "_provider_instance", None)
        _create(store, name="one", provider="mock", model="model-one")
        first = resolve_provider()
        assert first.model == "model-one"

        _create(store, name="two", provider="mock", model="model-two", is_default=True)
        second = resolve_provider()
        assert second.model == "model-two"
        assert second is not first, "each resolution must build from current config"

    def test_request_can_pin_a_non_default_model(self, store, monkeypatch):
        monkeypatch.setattr(providers, "_provider_instance", None)
        _create(store, name="default", provider="mock", model="model-default")
        pinned = _create(store, name="pinned", provider="mock", model="model-pinned")
        assert resolve_provider(pinned["id"]).model == "model-pinned"

    def test_empty_registry_falls_back_to_env(self, store, monkeypatch):
        monkeypatch.setattr(providers, "_provider_instance", None)
        monkeypatch.setenv("AI_PROVIDER_TYPE", "mock")
        monkeypatch.setenv("AI_PROVIDER_MODEL", "env-model")
        assert resolve_provider().model == "env-model"


class TestInvalidProviderTypeFailsLoudly:
    def test_invalid_type_raises_instead_of_silent_mock(self, monkeypatch):
        monkeypatch.setattr(providers, "_provider_instance", None)
        monkeypatch.setenv("AI_PROVIDER_TYPE", "definitely-not-a-provider")
        with pytest.warns(RuntimeWarning), pytest.raises(AIProviderConfigError):
            get_provider()

    def test_health_status_reports_invalid_instead_of_real(self, monkeypatch):
        monkeypatch.setenv("AI_PROVIDER_TYPE", "definitely-not-a-provider")
        status = ai_provider_status()
        assert status["mode"] == "invalid"
        assert status["type"] == "definitely-not-a-provider"
        assert "supported" in status["error"]

    def test_valid_type_still_reports_real(self, monkeypatch):
        monkeypatch.setenv("AI_PROVIDER_TYPE", "ollama")
        assert ai_provider_status()["mode"] == "real"


class TestKeyEnvAliases:
    def test_generic_key_wins_over_alias(self, monkeypatch):
        monkeypatch.setattr(
            providers,
            "_provider_env",
            lambda key, default="": {
                "AI_PROVIDER_KEY": "generic-key",
                "OPENAI_API_KEY": "alias-key",
            }.get(key, default),
        )
        assert providers._resolve_provider_api_key("openai") == "generic-key"

    def test_alias_is_read_when_generic_key_absent(self, monkeypatch):
        monkeypatch.setattr(
            providers,
            "_provider_env",
            lambda key, default="": {"DEEPSEEK_API_KEY": "alias-key"}.get(key, default),
        )
        assert providers._resolve_provider_api_key("deepseek") == "alias-key"

    def test_no_key_yields_placeholder(self, monkeypatch):
        monkeypatch.setattr(providers, "_provider_env", lambda key, default="": default)
        assert providers._resolve_provider_api_key("openai") == "[REDACTED]"

    def test_aliases_match_the_console_roster(self):
        from src.ai.roster_payload import PROVIDER_KEY_ENVS

        for name in ("openai", "anthropic", "google", "moonshot", "deepseek"):
            assert providers.PROVIDER_KEY_ALIASES[name] == PROVIDER_KEY_ENVS[name], (
                f"{name}: 后端读取的变量名必须与名册提示一致"
            )
