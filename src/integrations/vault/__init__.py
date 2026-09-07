"""
Vault Integration for LiuHao AI OS

Provides secure secret management via HashiCorp Vault.
Supports KV secrets engine for API keys and Transit engine for encryption.

Usage:
    from src.integrations.vault import get_secret_manager

    sm = get_secret_manager()
    sm.store_api_key("key-123", "OpenAI Key", "sk-...", {"provider": "openai"})
    secret = sm.retrieve_api_key("key-123")
"""

from .client import VaultClient, VaultConfig
from .secret_manager import SecretManager

# Singleton secret manager instance
_secret_manager: SecretManager = None


def get_vault_client(config: VaultConfig = None) -> VaultClient:
    """Get the singleton VaultClient."""
    return VaultClient.get_instance(config=config)


def get_secret_manager(client: VaultClient = None) -> SecretManager:
    """Get the singleton SecretManager."""
    global _secret_manager
    if _secret_manager is None:
        _secret_manager = SecretManager(client=client)
    return _secret_manager


def reset_singletons():
    """Reset all singleton instances (for testing)."""
    global _secret_manager
    VaultClient.reset_instance()
    _secret_manager = None
