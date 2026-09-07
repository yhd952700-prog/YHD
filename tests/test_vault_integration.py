"""
Tests for Vault integration in LiuHao AI OS

Tests the VaultClient and SecretManager in offline mode
(no Vault server required). Also tests connection logic.
"""

import pytest
import os
from datetime import datetime, timedelta

# Set dev mode environment
os.environ["VAULT_DEV_ROOT_TOKEN"] = "dev-only-secret-token"
os.environ["VAULT_DEV_ADDR"] = "http://127.0.0.1:8200"
os.environ["VAULT_ADDR"] = "http://127.0.0.1:8200"

from src.integrations.vault import (
    get_vault_client, get_secret_manager, reset_singletons,
)
from src.integrations.vault.client import VaultClient, VaultConfig
from src.integrations.vault.secret_manager import SecretManager


@pytest.fixture(autouse=True)
def reset_vault():
    """Reset singletons before each test."""
    reset_singletons()
    yield
    reset_singletons()


class TestVaultConfig:
    """Test VaultConfig."""

    def test_from_env(self):
        """Config can be created from environment."""
        config = VaultConfig.from_env()
        assert config.addr == "http://127.0.0.1:8200"
        assert config.token == "dev-only-secret-token"
        assert config.mount == "secret"
        assert config.kv_version == 2

    def test_is_dev_mode(self):
        """Dev mode detection works."""
        config = VaultConfig(
            addr="http://127.0.0.1:8200",
            token="dev-only-secret-token",
        )
        assert config.is_dev_mode() is True

    def test_is_not_dev_mode(self):
        """Non-dev config detected correctly."""
        config = VaultConfig(
            addr="https://vault.prod.internal",
            token="prod-token",
        )
        assert config.is_dev_mode() is False


class TestVaultClientOffline:
    """Test VaultClient in offline mode (no hvac connection)."""

    def test_singleton(self):
        """VaultClient is a singleton."""
        client1 = VaultClient.get_instance()
        client2 = VaultClient.get_instance()
        assert client1 is client2

    def test_connect_returns_false_without_hvac(self):
        """Connection fails gracefully without hvac."""
        client = VaultClient.get_instance()
        result = client.connect()
        # Should return False (no hvac installed)
        assert result is False
        assert client.connected is False

    def test_write_secret_offline(self):
        """Secrets cached in offline mode."""
        client = VaultClient.get_instance()
        client.connect()  # Will fail, use offline cache

        result = client.write_secret("test/secret", {"key": "value"})
        assert result is True

    def test_read_secret_offline(self):
        """Secrets retrievable from offline cache."""
        client = VaultClient.get_instance()
        client.connect()

        client.write_secret("test/secret", {"key": "value"})
        result = client.read_secret("test/secret")
        assert result is not None
        assert result["key"] == "value"

    def test_read_secret_not_found_offline(self):
        """Returns None for non-existent secrets."""
        client = VaultClient.get_instance()
        client.connect()

        result = client.read_secret("nonexistent/secret")
        assert result is None

    def test_delete_secret_offline(self):
        """Secrets deleted from offline cache."""
        client = VaultClient.get_instance()
        client.connect()

        client.write_secret("test/secret", {"key": "value"})
        result = client.delete_secret("test/secret")
        assert result is True

        result = client.read_secret("test/secret")
        assert result is None

    def test_list_secrets_offline(self):
        """List secrets from offline cache."""
        client = VaultClient.get_instance()
        client.connect()

        client.write_secret("api-keys/key1", {"api_key": "key1"})
        client.write_secret("api-keys/key2", {"api_key": "key2"})

        secrets = client.list_secrets()
        assert "api-keys/key1" in secrets
        assert "api-keys/key2" in secrets

    def test_encrypt_offline(self):
        """Encryption works in offline mode."""
        client = VaultClient.get_instance()
        client.connect()

        ciphertext = client.encrypt("test-key", "my-secret-data")
        assert ciphertext is not None
        assert ciphertext.startswith("offline-encrypted:")

    def test_reset_instance(self):
        """Singleton reset works."""
        client1 = VaultClient.get_instance()
        assert client1 is not None

        VaultClient.reset_instance()
        client2 = VaultClient.get_instance()
        assert client1 is not client2


class TestSecretManager:
    """Test SecretManager business logic."""

    @pytest.fixture
    def sm(self):
        """Get a fresh SecretManager with offline client."""
        client = VaultClient.get_instance()
        client.connect()
        return SecretManager(client=client)

    def test_store_and_retrieve_api_key(self, sm):
        """API keys can be stored and retrieved."""
        key_id = "test-key-001"
        key_name = "TestKey"
        api_key = "sk-test-secret-key"

        result = sm.store_api_key(key_id, key_name, api_key)
        assert result is True

        secret = sm.retrieve_api_key(key_id)
        assert secret is not None
        assert secret["key_name"] == key_name
        assert secret["api_key"] == api_key
        assert "key_hash" in secret

    def test_verify_api_key(self, sm):
        """API key verification works."""
        key_id = "test-key-002"
        api_key = "sk-verify-test-key"

        sm.store_api_key(key_id, "VerifyKey", api_key)

        assert sm.verify_api_key(key_id, api_key) is True
        assert sm.verify_api_key(key_id, "wrong-key") is False
        assert sm.verify_api_key("nonexistent", api_key) is False

    def test_rotate_api_key(self, sm):
        """Key rotation generates new key."""
        key_id = "test-key-003"
        old_key = "sk-old-key"

        sm.store_api_key(key_id, "RotatableKey", old_key)

        new_key = sm.rotate_api_key(key_id)
        assert new_key != ""
        assert new_key.startswith("sk-liu-")

        # New key should verify
        assert sm.verify_api_key(key_id, new_key) is True
        # Old key should not verify
        assert sm.verify_api_key(key_id, old_key) is False

        # Check metadata
        secret = sm.retrieve_api_key(key_id)
        assert secret["rotation_count"] == 1

    def test_store_and_retrieve_provider_credential(self, sm):
        """Provider credentials encrypted and stored."""
        provider = "openai"
        model = "gpt-4"
        credentials = {"api_key": "sk-provider-key", "base_url": "https://api.openai.com"}

        result = sm.store_provider_credential(provider, model, credentials)
        assert result is True

    def test_check_rotation_needed(self, sm):
        """Rotation check based on key age."""
        key_id = "test-key-004"

        # Fresh key - should not need rotation
        sm.store_api_key(key_id, "FreshKey", "sk-fresh")
        assert sm.check_rotation_needed(key_id) is False

        # Old key - should need rotation
        old_key_id = "test-key-005"
        sm.store_api_key(old_key_id, "OldKey", "sk-old")
        # Manually modify the cached secret to have an old timestamp
        client = sm._client
        cache_key = f"api-keys/{old_key_id}"
        cached = client._offline_cache.get(cache_key)
        cached["data"]["created_at"] = (datetime.now() - timedelta(days=100)).isoformat()

        assert sm.check_rotation_needed(old_key_id, max_age_days=90) is True

    def test_hash_key_consistency(self, sm):
        """Key hashing is consistent."""
        key = "sk-test-12345"
        hash1 = sm._hash_key(key)
        hash2 = sm._hash_key(key)
        assert hash1 == hash2

    def test_generate_key_format(self, sm):
        """Generated keys follow expected format."""
        key = sm._generate_key()
        assert key.startswith("sk-liu-")
        assert len(key) > 20
