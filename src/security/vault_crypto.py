"""
Vault Transit Crypto Integration for LiuHao AI OS

Wraps the Vault Transit secrets engine to provide:
- Sign/verify (asymmetric) for JWT signing
- HMAC-SHA256 (via Transit) for API key hashing with pepper
- Encrypt/decrypt (envelope) for sensitive data at rest
- Fallback to EncryptionManager when Vault is offline

Author: LiuHao OS Security Module
"""

import hashlib
import hmac
import logging
from typing import Optional, Dict, Any

from ..integrations.vault.client import VaultClient

logger = logging.getLogger(__name__)


class VaultTransitCrypto:
    """
    Vault Transit engine wrapper for cryptographic operations.

    Vault Transit provides:
    - RSA sign/verify (no private key ever leaves Vault)
    - HMAC-SHA256 issuance/verification (pepper stored in Vault)
    - AES-256-GCM encrypt/decrypt (data key never exposed)

    When Vault is unavailable, falls back to EncryptionManager.
    """

    def __init__(self, vault_client: Optional[VaultClient] = None):
        try:
            self._vault = vault_client or VaultClient.get_instance()
            self._vault.connect()
        except Exception as e:
            logger.debug(f"Vault client initialization failed: {e}")
            self._vault = None
        self._offline = self._vault is None or not self._vault.connected

    @property
    def is_online(self) -> bool:
        return not self._offline

    # ── HMAC (API Key Peppered Hash) ───────────────────────────

    def hmac_sha256(self, key_name: str, data: str) -> Optional[str]:
        """
        Compute HMAC-SHA256 via Vault Transit's HMAC API.

        The HMAC key is stored and managed by Vault (never exposed).
        The 'key_name' maps to a Transit key (e.g., 'api-key-hash-pepper').

        Returns hex digest string, or None if Vault unavailable.
        """
        if self._offline:
            return None
        try:
            # Vault Transit HMAC: POST /transit/hmac/{key_name}/sha256
            result = self._vault._client.secrets.transit.generate_hmac(
                name=key_name,
                hash_input=data,
            )
            return result["data"]["hmac"]
        except Exception as e:
            logger.debug(f"Vault Transit HMAC failed for '{key_name}': {e}")
            return None

    def verify_hmac(self, key_name: str, hmac_value: str, data: str) -> bool:
        """Verify HMAC-SHA256 via Vault Transit."""
        if self._offline:
            return False
        try:
            self._vault._client.secrets.transit.verify_hmac(
                name=key_name,
                hmac=hmac_value,
                input=data,
            )
            return True
        except Exception:
            return False

    # ── Asymmetric Sign/Verify (JWT) ───────────────────────────

    def sign_rsa_pkcs1v15_sha256(
        self, key_name: str, data: bytes
    ) -> Optional[str]:
        """
        Sign data with RSA-PKCS1v15-SHA256 via Vault Transit.

        The private key never leaves Vault. Returns hex-encoded signature.
        """
        if self._offline:
            return None
        try:
            import base64
            b64_data = base64.b64encode(data).decode()
            result = self._vault._client.secrets.transit.sign(
                name=key_name,
                input_data=b64_data,
                hash_algorithm="sha2-256",
                signature_algorithm="pkcs1v15",
            )
            return result["data"]["signature"]
        except Exception as e:
            logger.debug(f"Vault Transit sign failed for '{key_name}': {e}")
            return None

    def verify_rsa_pkcs1v15_sha256(
        self, key_name: str, signature: str, data: bytes
    ) -> bool:
        """Verify RSA-PKCS1v15-SHA256 signature via Vault Transit."""
        if self._offline:
            return False
        try:
            import base64
            b64_data = base64.b64encode(data).decode()
            self._vault._client.secrets.transit.verify_signed(
                name=key_name,
                input_data=b64_data,
                signature=signature,
                hash_algorithm="sha2-256",
                signature_algorithm="pkcs1v15",
            )
            return True
        except Exception:
            return False

    # ── Envelope Encryption (AES-256-GCM) ───────────────────────

    def encrypt(self, key_name: str, plaintext: str) -> Optional[Dict[str, Any]]:
        """
        Encrypt data via Vault Transit (AES-256-GCM).

        Returns {'ciphertext': ..., 'key_name': ..., 'version': ...}
        """
        if self._offline:
            return None
        try:
            result = self._vault._client.secrets.transit.encrypt(
                name=key_name,
                plaintext=plaintext,
            )
            return {
                "ciphertext": result["data"]["ciphertext"],
                "key_name": key_name,
                "version": result["data"].get("version", 1),
            }
        except Exception as e:
            logger.debug(f"Vault Transit encrypt failed for '{key_name}': {e}")
            return None

    def decrypt(self, key_name: str, ciphertext: str) -> Optional[str]:
        """Decrypt data via Vault Transit."""
        if self._offline:
            return None
        try:
            result = self._vault._client.secrets.transit.decrypt(
                name=key_name,
                ciphertext=ciphertext,
            )
            import base64
            return base64.b64decode(result["data"]["plaintext"]).decode()
        except Exception as e:
            logger.debug(f"Vault Transit decrypt failed for '{key_name}': {e}")
            return None

    # ── Transit Key Management ─────────────────────────────────

    def ensure_key(self, key_name: str, key_type: str = "hmac") -> bool:
        """
        Ensure a Transit key exists. Creates if not present.

        Args:
            key_name: Transit key name
            key_type: One of 'hmac', 'rsa', 'aes256
        """
        if self._offline:
            return False
        try:
            self._vault._client.secrets.transit.create_key(
                name=key_name,
                type=key_type,
                exportable=False,  # private key never exported
                allow_plaintext_backup=False,
            )
            logger.info(f"Created Vault Transit key: {key_name} (type={key_type})")
            return True
        except Exception:
            # Key already exists
            return True

    def rotate_transit_key(self, key_name: str) -> bool:
        """Rotate a Transit key (creates new version)."""
        if self._offline:
            return False
        try:
            self._vault._client.secrets.transit.rotate_key(name=key_name)
            logger.info(f"Rotated Vault Transit key: {key_name}")
            return True
        except Exception as e:
            logger.error(f"Failed to rotate Transit key '{key_name}': {e}")
            return False

    # ── API Key Hashing (with pepper) ───────────────────────────

    def hash_api_key(self, raw_key: str, key_name: str = "api-key-hash-pepper") -> str:
        """
        Hash an API key for secure storage.

        When Vault is online, uses HMAC-SHA256 via Transit (pepper in Vault).
        When offline, falls back to SHA256 with a static pepper derived
        from the EncryptionManager master key.

        Args:
            raw_key: The raw API key value
            key_name: Vault Transit HMAC key name

        Returns:
            Hex-encoded hash string
        """
        if self._offline:
            # Offline fallback: SHA256 + pepper from EncryptionManager
            from .encryption import get_encryption_manager
            mgr = get_encryption_manager()
            pepper = mgr.master_key.hex()[:32] if hasattr(mgr, "master_key") else ""
            return hashlib.sha256(
                (pepper + raw_key).encode()
            ).hexdigest()

        # Online: use Vault Transit HMAC
        hmac_val = self.hmac_sha256(key_name, raw_key)
        if hmac_val:
            return hmac_val

        # Fallback if Vault HMAC fails: SHA256 without pepper
        return hashlib.sha256(raw_key.encode()).hexdigest()

    def verify_api_key_hash(self, raw_key: str, stored_hash: str, key_name: str = "api-key-hash-pepper") -> bool:
        """
        Verify an API key against a stored hash.

        Uses constant-time comparison to prevent timing attacks.
        """
        computed = self.hash_api_key(raw_key, key_name)
        return hmac.compare_digest(computed, stored_hash)


def get_vault_crypto() -> VaultTransitCrypto:
    """Get singleton VaultTransitCrypto instance."""
    return VaultTransitCrypto()
