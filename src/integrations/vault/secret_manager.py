"""
Secret Manager for LiuHao AI OS

Business logic layer over VaultClient.
Integrates with the existing api_keys and ai_models database tables.
"""

from typing import Any, Dict, Optional, List
from datetime import datetime, timedelta
import hashlib
import secrets as _secrets
import logging

from .client import VaultClient, VaultConfig

logger = logging.getLogger(__name__)


class SecretManager:
    """
    Business logic for secret management.

    Integrates Vault with the database layer:
    - API keys stored in Vault KV store, referenced by DB id
    - Model provider credentials encrypted via Vault Transit
    - Automatic key rotation with versioning
    """

    def __init__(self, client: Optional[VaultClient] = None):
        self._client = client or VaultClient.get_instance()

    # --- API Key Management ---

    def store_api_key(self, key_id: str, key_name: str, api_key: str, metadata: Optional[Dict] = None) -> bool:
        """Store an API key in Vault, referenced by database id."""
        path = f"api-keys/{key_id}"
        secret_data = {
            "key_name": key_name,
            "api_key": api_key,
            "key_hash": self._hash_key(api_key),
            "metadata": metadata or {},
            "created_at": datetime.now().isoformat(),
        }
        return self._client.write_secret(path, secret_data)

    def retrieve_api_key(self, key_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve an API key from Vault by database id."""
        return self._client.read_secret(f"api-keys/{key_id}")

    def verify_api_key(self, key_id: str, provided_key: str) -> bool:
        """Verify an API key against the stored hash."""
        secret = self.retrieve_api_key(key_id)
        if not secret:
            return False
        return self._hash_key(provided_key) == secret.get("key_hash")

    def rotate_api_key(self, key_id: str, new_key: Optional[str] = None) -> str:
        """Rotate an API key. Generates new key if not provided.

        Returns the new key (old key remains for grace period).
        """
        old_secret = self.retrieve_api_key(key_id)
        if not old_secret:
            logger.error(f"Cannot rotate key {key_id}: not found")
            return ""

        new_key = new_key or self._generate_key()
        new_secret = {
            **old_secret,
            "api_key": new_key,
            "key_hash": self._hash_key(new_key),
            "rotated_from": old_secret.get("api_key", ""),
            "rotation_count": old_secret.get("rotation_count", 0) + 1,
            "rotated_at": datetime.now().isoformat(),
        }
        # Update in Vault
        self._client.write_secret(f"api-keys/{key_id}", new_secret)

        # Archive old key for grace period
        old_key_id = f"api-keys/{key_id}/archive/{old_secret.get('rotation_count', 0)}"
        archived = {
            **old_secret,
            "archived_at": datetime.now().isoformat(),
            "status": "rotated",
        }
        self._client.write_secret(old_key_id, archived)

        logger.info(f"Rotated API key: {key_id}")
        return new_key

    # --- Provider Credential Management ---

    def store_provider_credential(self, provider: str, model: str, credentials: Dict[str, Any]) -> bool:
        """Store LLM provider credentials."""
        key_name = f"provider-{provider}-{model}".replace("/", "-")
        encrypted = self._client.encrypt(key_name, str(credentials))
        path = f"providers/{provider}/{model}"
        return self._client.write_secret(path, {
            "provider": provider,
            "model": model,
            "encrypted_credentials_ref": encrypted,
            "key_name": key_name,
            "created_at": datetime.now().isoformat(),
        })

    def retrieve_provider_credential(self, provider: str, model: str) -> Optional[Dict[str, Any]]:
        """Retrieve and decrypt LLM provider credentials."""
        path = f"providers/{provider}/{model}"
        secret = self._client.read_secret(path)
        if not secret:
            return None
        encrypted_ref = secret.get("encrypted_credentials_ref")
        if not encrypted_ref:
            return None
        plaintext = self._client.decrypt(secret.get("key_name", ""), encrypted_ref)
        if not plaintext:
            return None
        import json
        return json.loads(plaintext)

    # --- Key Rotation Policy ---

    def check_rotation_needed(self, key_id: str, max_age_days: int = 90) -> bool:
        """Check if an API key needs rotation based on age."""
        secret = self.retrieve_api_key(key_id)
        if not secret:
            return False
        created_str = secret.get("created_at")
        if not created_str:
            return True
        try:
            created = datetime.fromisoformat(created_str)
            return datetime.now() - created > timedelta(days=max_age_days)
        except (ValueError, TypeError):
            return True

    def rotate_due_keys(self, max_age_days: int = 90) -> Dict[str, str]:
        """Find and rotate all keys due for rotation.

        Returns dict of key_id -> status.
        """
        results = {}
        keys = self._client.list_secrets("api-keys/")
        for key_id in keys:
            # Skip archive paths
            if "/archive/" in key_id:
                continue
            if self.check_rotation_needed(key_id, max_age_days):
                try:
                    new_key = self.rotate_api_key(key_id)
                    results[key_id] = f"rotated (new key: {new_key[:8]}...)"
                except Exception as e:
                    results[key_id] = f"failed: {str(e)}"
            else:
                results[key_id] = "ok"
        return results

    # --- Helpers ---

    @staticmethod
    def _hash_key(api_key: str) -> str:
        """Hash an API key for verification (SHA-256)."""
        return hashlib.sha256(api_key.encode()).hexdigest()

    @staticmethod
    def _generate_key() -> str:
        """Generate a new API key."""
        return f"sk-liu-{_secrets.token_urlsafe(32)}"
