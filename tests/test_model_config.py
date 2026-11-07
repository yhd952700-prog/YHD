"""模型配置中心：持久化、密钥加密、默认唯一与运行时热加载。

这些用例刻意断言**行为**而不是实现细节：密钥以密文落库、接口形态只露脱敏串、
保存后解析到新模型不需要重置任何进程级单例。
"""
from __future__ import annotations

import json
import os

import pytest

import src.ai.model_config as model_config_module
from src.ai import providers
from src.ai.model_config import (
    NEEDS_REENTRY_CODE,
    NEEDS_REENTRY_TEXT,
    ModelConfigError,
    ModelConfigStore,
    get_model_config_store,
    mask_secret,
    migrate_env_config_to_store,
    vendor_for,
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

    def test_arbitrary_model_name_is_accepted(self, store):
        """模型名不做白名单校验：带厂商前缀/版本后缀/斜杠都应原样保存。"""
        created = _create(
            store,
            name="custom",
            provider="openai",
            model="acme/private-model:v9-beta",
            base_url="https://llm.example.com/v1",
            api_key="sk-test-123456",
        )
        assert created["model"] == "acme/private-model:v9-beta"
        assert store.resolve(created["id"])["model"] == "acme/private-model:v9-beta"


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


class TestUndecryptableCredential:
    """密钥文件与密文不匹配时的用户可见面。

    触发条件：密钥文件不是精确 32 字节（换了机器/恢复了旧数据/被替换），
    或早先版本把首尾命中 ASCII 空白的 32 字节密钥 ``strip()`` 裁短后按口令
    派生，导致读回密钥与生成时不一致。用户看到的必须是可操作文案，且配置
    不能被静默清空。
    """

    @staticmethod
    def _new_store(tmp_path, key_path):
        return ModelConfigStore(
            db_path=str(tmp_path / "registry.sqlite3"), key_path=str(key_path)
        )

    @staticmethod
    def _pin_key_generation(monkeypatch, key: bytes) -> None:
        """只固定 32 字节密钥生成；16 字节仍走真随机（uuid4 依赖 os.urandom）。"""
        real = os.urandom
        monkeypatch.setattr(
            model_config_module.os,
            "urandom",
            lambda n: key if n == 32 else real(n),
        )

    def test_key_with_whitespace_edges_survives_restart(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.delenv(model_config_module.MODEL_CONFIG_KEY_ENV, raising=False)
        # 生成时密钥末尾恰为空格（随机密钥约 4-9% 概率命中），旧实现再次读取时
        # strip() 裁成 31 字节 → 口令派生 → 与生成时的密钥不一致。
        key = b"A" * 31 + b" "
        self._pin_key_generation(monkeypatch, key)
        key_path = tmp_path / "model_config.key"
        first = self._new_store(tmp_path, key_path)
        created = first.create(
            {
                "name": "whitespace-key",
                "provider": "mock",
                "model": "mock-model",
                "api_key": "sk-abcdefghijklmnop",
            },
            "admin",
        )
        assert created["api_key_status"] == "ok"
        assert created["api_key_masked"] == "sk-a…mnop"
        assert key_path.read_bytes() == key

        reopened = self._new_store(tmp_path, key_path)
        public = reopened.get_public(created["id"])
        assert public["api_key_status"] == "ok"
        assert public["api_key_masked"] == "sk-a…mnop"

    def test_key_file_with_trailing_newline_still_decrypts(self, tmp_path, monkeypatch):
        """文本方式保存的 32 字节密钥（多一个换行）保持兼容。"""
        monkeypatch.delenv(model_config_module.MODEL_CONFIG_KEY_ENV, raising=False)
        key_path = tmp_path / "model_config.key"
        key_path.write_bytes(b"B" * 32 + b"\n")
        store = self._new_store(tmp_path, key_path)
        created = store.create(
            {
                "name": "newline-key",
                "provider": "mock",
                "model": "mock-model",
                "api_key": "sk-abcdefghijklmnop",
            },
            "admin",
        )
        assert created["api_key_masked"] == "sk-a…mnop"
        reopened = self._new_store(tmp_path, key_path)
        assert reopened.get_public(created["id"])["api_key_status"] == "ok"

    def test_mismatched_key_is_neutral_and_does_not_clear_config(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.delenv(model_config_module.MODEL_CONFIG_KEY_ENV, raising=False)
        self._pin_key_generation(monkeypatch, b"K" * 32)
        key_path = tmp_path / "model_config.key"
        first = self._new_store(tmp_path, key_path)
        created = first.create(
            {
                "name": "deepseek",
                "provider": "mock",
                "model": "mock-model",
                "api_key": "sk-abcdefghijklmnop",
            },
            "admin",
        )
        # 模拟换机器/恢复旧数据：密文还在，密钥文件变成另一份。
        self._pin_key_generation(monkeypatch, b"Z" * 32)
        key_path.write_bytes(b"Z" * 32)
        reopened = self._new_store(tmp_path, key_path)

        listed = reopened.list_models()
        assert len(listed) == 1, "密钥不匹配不得清空用户模型配置"
        item = listed[0]
        assert item["id"] == created["id"]
        assert item["api_key_set"] is True
        assert item["api_key_status"] == "needs_reentry"
        assert item["api_key_masked"] == NEEDS_REENTRY_TEXT
        payload = json.dumps(listed, ensure_ascii=False)
        for internal in ("InvalidTag", "Traceback", "（密文无法解密）", "解密失败"):
            assert internal not in payload

        with pytest.raises(ModelConfigError) as excinfo:
            reopened.resolve(created["id"])
        assert excinfo.value.code == NEEDS_REENTRY_CODE
        assert excinfo.value.status_code == 409
        assert "重新填写" in excinfo.value.detail

    def test_healthy_store_keeps_normal_masked_value(self, store):
        """正常流程（存在密钥文件、32 字节）保持原有脱敏形态。"""
        created = _create(store, api_key="sk-abcdefghijklmnop")
        assert created["api_key_status"] == "ok"
        assert created["api_key_masked"] == "sk-a…mnop"

class TestVendorLabel:
    """``provider`` 是协议适配器名；``vendor`` 才是真实厂商（不改路由）。"""

    @pytest.mark.parametrize(
        ("provider", "base_url", "expected"),
        [
            ("openai", "https://api.deepseek.com/v1", "deepseek"),
            ("openai", "https://api.openai.com/v1", "openai"),
            ("openai", None, "openai"),
            ("openai", "https://api.anthropic.com", "anthropic"),
            ("openai", "https://generativelanguage.googleapis.com", "google"),
            ("deepseek", "https://api.openai.com/v1", "deepseek"),
            ("ollama", "http://localhost:11434", "ollama"),
            ("openai", "http://127.0.0.1:8000/v1", "local"),
            ("openai", "https://openrouter.ai/api/v1", "openrouter.ai"),
        ],
    )
    def test_vendor_mapping(self, provider, base_url, expected):
        assert vendor_for(provider, base_url) == expected

    def test_public_payload_carries_provider_and_vendor(self, store):
        created = store.create(
            {
                "name": "deepseek",
                "provider": "openai",
                "model": "deepseek-chat",
                "base_url": "https://api.deepseek.com/v1",
                "api_key": "sk-abcdefghijklmnop",
            },
            "admin",
        )
        assert created["provider"] == "openai"  # 已有配置/路由字段不变
        assert created["vendor"] == "deepseek"
        listed = store.list_models()
        assert listed[0]["vendor"] == "deepseek"


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


class TestLegacyEnvMigration:
    """旧 ``.env`` 那一套配置 → 可编辑模型记录的一次性迁移。"""

    def _enable(self, monkeypatch, provider="ollama", model="qwen2.5:3b", **env):
        monkeypatch.setenv(model_config_module.MODEL_CONFIG_MIGRATE_ENV, "1")
        monkeypatch.setenv("AI_PROVIDER_TYPE", provider)
        monkeypatch.setenv("AI_PROVIDER_MODEL", model)
        monkeypatch.delenv("AI_PROVIDER_KEY", raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, value)

    def test_env_ollama_becomes_an_editable_default(self, store, monkeypatch):
        self._enable(monkeypatch, OLLAMA_BASE_URL="http://127.0.0.1:11434")

        result = migrate_env_config_to_store(store)

        assert result and result["migrated"] is True
        listed = store.list_models()
        assert len(listed) == 1
        item = listed[0]
        assert item["provider"] == "ollama"
        assert item["model"] == "qwen2.5:3b"
        assert item["base_url"] == "http://127.0.0.1:11434"
        assert item["is_default"] is True
        # 迁移走的同一条创建路径 → 旧模型立即可被解析使用。
        assert store.resolve()["model"] == "qwen2.5:3b"
        # 第二次启动不重复添加。
        assert migrate_env_config_to_store(store) is None
        assert len(store.list_models()) == 1

    def test_migrated_key_is_encrypted_and_usable(self, store, monkeypatch):
        secret = "sk-legacy-do-not-leak-123456"
        self._enable(
            monkeypatch,
            provider="openai",
            model="deepseek-chat",
            AI_PROVIDER_KEY=secret,
            OPENAI_BASE_URL="https://api.deepseek.com/v1",
        )

        migrate_env_config_to_store(store)

        item = store.list_models()[0]
        assert item["api_key_set"] is True
        assert secret not in json.dumps(item)
        resolved = store.resolve(item["id"])
        assert resolved["api_key"] == secret
        raw = store._connect().execute(
            "SELECT api_key_enc FROM ai_model_configs WHERE id = ?", (item["id"],)
        ).fetchone()["api_key_enc"]
        assert raw.startswith("enc:v1:")
        assert secret not in raw

    def test_existing_user_config_is_never_overwritten(self, store, monkeypatch):
        mine = _create(store, name="mine", provider="mock", model="kept")
        self._enable(monkeypatch)

        result = migrate_env_config_to_store(store)

        assert result and result["migrated"] is False
        assert [m["name"] for m in store.list_models()] == ["mine"]
        assert store.resolve(mine["id"])["model"] == "kept"

    def test_deleted_migration_is_not_recreated_on_next_boot(self, store, monkeypatch):
        self._enable(monkeypatch)
        migrate_env_config_to_store(store)
        created_id = store.list_models()[0]["id"]
        store.delete(created_id, "admin")

        assert migrate_env_config_to_store(store) is None
        assert store.list_models() == []

    def test_skip_switch_and_mock_config_migrate_nothing(self, store, monkeypatch):
        self._enable(monkeypatch)
        monkeypatch.setenv(model_config_module.MODEL_CONFIG_MIGRATE_ENV, "0")
        assert migrate_env_config_to_store(store) is None
        assert store.list_models() == []

        monkeypatch.setenv(model_config_module.MODEL_CONFIG_MIGRATE_ENV, "1")
        monkeypatch.setenv("AI_PROVIDER_TYPE", "mock")
        result = migrate_env_config_to_store(store)
        assert result and result["migrated"] is False
        assert store.list_models() == []
