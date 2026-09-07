"""
Vault Integration for LiuHao AI OS

Provides a Vault client with KV and Transit secrets engines.
Supports local development mode and production Vault clusters.
"""

from typing import Any, Dict, Optional, List
import threading
import os
import json
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# Try importing hvac (HashiCorp Vault client)
try:
    import hvac
    _HAS_HVAC = True
except ImportError:
    _HAS_HVAC = False
    logger.warning("hvac library not installed. Vault integration will run in offline mode.")


# Vault development server configuration
VAULT_DEV_ROOT_TOKEN = os.environ.get("VAULT_DEV_ROOT_TOKEN", "dev-only-secret-token")
VAULT_DEV_ADDR = os.environ.get("VAULT_DEV_ADDR", "http://127.0.0.1:8200")

# Production Vault configuration
VAULT_ADDR = os.environ.get("VAULT_ADDR", VAULT_DEV_ADDR)
VAULT_TOKEN = os.environ.get("VAULT_TOKEN", VAULT_DEV_ROOT_TOKEN)
VAULT_MOUNT = os.environ.get("VAULT_MOUNT", "secret")
VAULT_KV_VERSION = os.environ.get("VAULT_KV_VERSION", "2")


class VaultConfig:
    """Configuration for Vault client."""

    def __init__(
        self,
        addr: str = VAULT_ADDR,
        token: str = VAULT_TOKEN,
        mount: str = VAULT_MOUNT,
        kv_version: str = VAULT_KV_VERSION,
        skip_tls_verify: bool = False,
    ):
        self.addr = addr
        self.token = token
        self.mount = mount
        self.kv_version = int(kv_version)
        self.skip_tls_verify = skip_tls_verify

    @classmethod
    def from_env(cls) -> "VaultConfig":
        """Create configuration from environment variables."""
        return cls()

    def is_dev_mode(self) -> bool:
        """Check if running in development mode."""
        return self.token == VAULT_DEV_ROOT_TOKEN and self.addr == VAULT_DEV_ADDR


class VaultClient:
    """Thread-safe Vault client wrapper with KV and Transit engines."""

    _instance: Optional["VaultClient"] = None
    _lock = threading.Lock()

    def __new__(cls, config: Optional[VaultConfig] = None) -> "VaultClient":
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self, config: Optional[VaultConfig] = None):
        if self._initialized:
            return
        self._initialized = True
        self._config = config or VaultConfig.from_env()
        self._client: Optional["hvac.Client"] = None
        self._connected = False
        self._offline_cache: Dict[str, Any] = {}

    @classmethod
    def get_instance(cls, config: Optional[VaultConfig] = None) -> "VaultClient":
        """Get singleton VaultClient instance."""
        return cls(config=config)

    @classmethod
    def reset_instance(cls):
        """Reset the singleton instance (for testing)."""
        with cls._lock:
            cls._instance = None

    @property
    def connected(self) -> bool:
        """Check if connected to Vault."""
        return self._connected

    @property
    def config(self) -> VaultConfig:
        return self._config

    def connect(self) -> bool:
        """Connect to Vault and verify token."""
        if not _HAS_HVAC:
            logger.info("hvac not installed, running in offline cache mode")
            self._connected = False
            return False

        try:
            self._client = hvac.Client(
                url=self._config.addr,
                token=self._config.token,
                verify=not self._config.skip_tls_verify,
            )

            if self._client.is_authenticated():
                self._connected = True
                logger.info(f"Connected to Vault at {self._config.addr}")
                return True
            else:
                logger.warning("Vault token not authenticated")
                self._connected = False
                return False
        except Exception as e:
            logger.warning(f"Vault connection failed: {e}")
            self._connected = False
            return False

    # --- KV Secret Engine ---

    def write_secret(self, path: str, data: Dict[str, Any]) -> bool:
        """Write a secret to the KV store."""
        if not self._connected:
            # Offline mode: cache in memory
            self._offline_cache[path] = {
                "data": data,
                "created_at": datetime.now().isoformat(),
            }
            logger.info(f"[offline] Cached secret at {path}")
            return True

        try:
            if self._config.kv_version == 2:
                self._client.secrets.kv.v2.create_or_update_secret(
                    path=path,
                    secret=data,
                    mount_point=self._config.mount,
                )
            else:
                self._client.secrets.kv.v1.create_or_update_secret(
                    path=path,
                    secret=data,
                    mount_point=self._config.mount,
                )
            logger.info(f"Wrote secret to Vault at {self._config.mount}/{path}")
            return True
        except Exception as e:
            logger.error(f"Failed to write secret to Vault: {e}")
            return False

    def read_secret(self, path: str) -> Optional[Dict[str, Any]]:
        """Read a secret from the KV store."""
        if not self._connected:
            cached = self._offline_cache.get(path)
            if cached:
                return cached["data"]
            return None

        try:
            if self._config.kv_version == 2:
                response = self._client.secrets.kv.v2.read_secret_version(
                    path=path,
                    mount_point=self._config.mount,
                )
                return response["data"]["data"]
            else:
                response = self._client.secrets.kv.v1.read_secret(
                    path=path,
                    mount_point=self._config.mount,
                )
                return response["data"]
        except hvac.exceptions.InvalidPath:
            return None
        except Exception as e:
            logger.error(f"Failed to read secret from Vault: {e}")
            return None

    def delete_secret(self, path: str) -> bool:
        """Delete a secret from the KV store."""
        if not self._connected:
            self._offline_cache.pop(path, None)
            return True

        try:
            if self._config.kv_version == 2:
                self._client.secrets.kv.v2.delete_secret(
                    path=path,
                    mount_point=self._config.mount,
                )
            else:
                self._client.secrets.kv.v1.delete_secret(
                    path=path,
                    mount_point=self._config.mount,
                )
            return True
        except Exception as e:
            logger.error(f"Failed to delete secret from Vault: {e}")
            return False

    def list_secrets(self, prefix: str = "") -> List[str]:
        """List secrets under a given prefix."""
        if not self._connected:
            return list(self._offline_cache.keys())

        try:
            if self._config.kv_version == 2:
                response = self._client.secrets.kv.v2.list_secrets(
                    path=prefix,
                    mount_point=self._config.mount,
                )
            else:
                response = self._client.secrets.kv.v1.list_secret(
                    path=prefix,
                    mount_point=self._config.mount,
                )

            return response["data"]["keys"]
        except hvac.exceptions.InvalidPath:
            return []
        except Exception as e:
            logger.error(f"Failed to list secrets from Vault: {e}")
            return []

    # --- Transit Secret Engine ---

    def encrypt(self, key_name: str, plaintext: str) -> Optional[str]:
        """Encrypt plaintext using Vault Transit engine."""
        if not self._connected:
            # Offline: simple encryption
            import hashlib
            encrypted = hashlib.sha256(plaintext.encode()).hexdigest()
            self._offline_cache[f"transit/{key_name}"] = encrypted
            return f"offline-encrypted:{encrypted}"

        try:
            response = self._client.secrets.transit.encrypt_data(
                name=key_name,
                plaintext=plaintext.encode(),
            )
            return response["data"]["ciphertext"]
        except Exception as e:
            logger.error(f"Vault transit encrypt failed: {e}")
            return None

    def decrypt(self, key_name: str, ciphertext: str) -> Optional[str]:
        """Decrypt ciphertext using Vault Transit engine."""
        if not self._connected:
            return None

        try:
            response = self._client.secrets.transit.decrypt_data(
                name=key_name,
                ciphertext=ciphertext,
            )
            import base64
            return base64.b64decode(response["data"]["plaintext"]).decode()
        except Exception as e:
            logger.error(f"Vault transit decrypt failed: {e}")
            return None
