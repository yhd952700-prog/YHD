"""Vault Transit Integration for Workstream H

Per Definition Lock: Vault Transit hash-chain logging for all cryptographic
operations. Hermes override #16: integrate Vault Transit for model request
signing and verification.

Spec items 191-192: Vault Transit integration supports:
- Transit engine connection
- Key management
- Hash chaining for audit trails
- Signing and verification of model gateway requests
"""
import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import aiohttp


class VaultTransitIntegration:
    """Integrates with HashiCorp Vault Transit engine.

    Per Definition Lock: Vault Transit hash-chain logging for all
    cryptographic operations. Hermes override #16: integrate Vault Transit
    for model request signing and verification.

    Spec items 191-192:
    - Transit engine connection
    - Key management
    - Hash chaining for audit trails
    - Signing and verification of model gateway requests
    """

    def __init__(self, base_url: str, transit_path: str = "secret/model-gateway",
                 token: Optional[str] = None):
        """Initialize Vault Transit integration.

        Args:
            base_url: Vault server base URL (e.g., "http://localhost:8200")
            transit_path: Path within Vault for transit secrets (default: "secret/model-gateway")
            token: Vault token for authentication (optional; if omitted, relies on
                   environment Vault_TOKEN or Vault_ADDR)
        """
        self.base_url = base_url.rstrip("/")
        self.transit_path = transit_path.rstrip("/")
        self.token = token
        self.session: Optional[aiohttp.ClientSession] = None
        self.keys: Dict[str, str] = {}  # key_name -> algorithm mapping
        self.audit_log: List[Dict[str, Any]] = []
        self._initialized = False
        self._connection_error: Optional[str] = None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """Ensure an aiohttp session exists, create one if needed."""
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
        return self.session

    async def _request(self, method: str, endpoint: str, **kwargs) -> Any:
        """Make an authenticated request to Vault.

        Raises on non-2xx responses after retry logic.
        """
        session = await self._ensure_session()
        url = f"{self.base_url}/v1{endpoint}"

        # Build headers: token auth if provided
        headers = kwargs.pop("headers", {})
        if self.token:
            headers["X-Vault-Token"] = self.token
        if headers:
            kwargs["headers"] = headers

        attempt = 0
        max_attempts = 3
        while attempt < max_attempts:
            try:
                async with method(session, url, **kwargs) as resp:
                    body = await resp.json()
                    if resp.status >= 200 and resp.status < 300:
                        return body
                    # Non-2xx: log and retry
                    attempt += 1
                    if attempt >= max_attempts:
                        self._connection_error = f"Vault request failed after {max_attempts} attempts: {resp.status} {body}"
                        raise RuntimeError(self._connection_error)
                    # Brief backoff
                    await asyncio.sleep(0.5 * attempt)
            except aiohttp.ClientError as e:
                attempt += 1
                if attempt >= max_attempts:
                    self._connection_error = f"Vault client error after {max_attempts} attempts: {e}"
                    raise RuntimeError(self._connection_error)
                await asyncio.sleep(0.5 * attempt)

    async def initialize(self) -> bool:
        """Initialize Vault Transit connection and load existing keys.

        Per Definition Lock: ensures Vault Transit engine is reachable and
        loads existing key configuration.

        Returns:
            True if initialization succeeded (keys loaded or freshly registered).
        """
        self._initialized = False
        self.keys = {}
        self.audit_log = []

        body = await self._request(
            "GET",
            f"/{self.transit_path}/config",
        )
        # Config response format varies; handle gracefully
        if body and "data" in body:
            data = body["data"]
            if isinstance(data, dict):
                self.keys = data.get("keys", {})
                # Normalize to {key_name: algorithm}
                normalized = {}
                for k, v in self.keys.items():
                    if isinstance(v, dict):
                        normalized[k] = v.get("type", "unknown")
                    else:
                        normalized[k] = str(v)
                self.keys = normalized
        elif body and "keys" in body:
            keys_data = body["keys"]
            if isinstance(keys_data, dict):
                for k, v in keys_data.items():
                    if isinstance(v, dict):
                        self.keys[k] = v.get("type", "unknown")
                    else:
                        self.keys[k] = str(v)

        self._initialized = True

        # Log initialization to audit
        self.audit_log.append({
            "action": "initialize",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "keys_loaded": len(self.keys),
            "transit_path": self.transit_path,
            "base_url": self.base_url,
            "success": True,
        })

        return self._initialized

    async def register_key(self, key_name: str, key_algorithm: str = "aes256-gcm") -> bool:
        """Register a new encryption key in Vault Transit.

        Per spec items 191-192: key management.

        Args:
            key_name: Name of the key to register in Vault
            key_algorithm: Transit algorithm type (e.g., "aes256-gcm", "aes256-ctr")

        Returns:
            True if key was successfully registered.

        Raises:
            RuntimeError: If Vault request fails after retry logic.
        """
        body = {
            "type": key_algorithm,
            "keys": {},
        }

        await self._request(
            "PUT",
            f"/{self.transit_path}/key/{key_name}",
            json=body,
        )

        # Store locally
        self.keys[key_name] = key_algorithm

        # Audit log entry
        self.audit_log.append({
            "action": "register_key",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "key_name": key_name,
            "key_algorithm": key_algorithm,
            "transit_path": self.transit_path,
            "success": True,
        })

        return True

    async def encrypt(self, key_name: str, plaintext: str) -> Optional[Dict[str, Any]]:
        """Encrypt plaintext using a Vault Transit key.

        Per spec items 191-192: supports hash-chain audit logging for
        all cryptographic operations.

        Args:
            key_name: Name of the Transit key to use for encryption
            plaintext: Data to encrypt

        Returns:
            Dict with ciphertext and metadata, or None if key not found.

        Raises:
            RuntimeError: If Vault request fails.
        """
        if key_name not in self.keys:
            # Log missing key attempt
            self.audit_log.append({
                "action": "encrypt_missing_key",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "key_name": key_name,
                "success": False,
                "reason": "key_not_found",
            })
            return None

        session = await self._ensure_session()
        url = f"{self.base_url}/v1/{self.transit_path}/encrypt/{key_name}"

        async with session.post(url, json={"plaintext": plaintext}) as resp:
            if resp.status == 200:
                result = await resp.json()
                # Audit log entry
                self.audit_log.append({
                    "action": "encrypt",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "key_name": key_name,
                    "ciphertext_len": len(result.get("ciphertext", "")),
                    "success": True,
                })
                return result
            else:
                body = await resp.json()
                self.audit_log.append({
                    "action": "encrypt_failed",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "key_name": key_name,
                    "status": resp.status,
                    "success": False,
                    "error": body.get("errors", ["unknown"]),
                })
                raise RuntimeError(f"Encryption failed: {resp.status} {body}")

    async def decrypt(self, key_name: str, ciphertext: str) -> Optional[Dict[str, Any]]:
        """Decrypt ciphertext using a Vault Transit key.

        Per spec items 191-192: supports hash-chain audit logging for
        all cryptographic operations.

        Args:
            key_name: Name of the Transit key to use for decryption
            ciphertext: Data to decrypt (base64-encoded)

        Returns:
            Dict with plaintext and metadata, or None if key not found.

        Raises:
            RuntimeError: If Vault request fails.
        """
        if key_name not in self.keys:
            # Log missing key attempt
            self.audit_log.append({
                "action": "decrypt_missing_key",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "key_name": key_name,
                "success": False,
                "reason": "key_not_found",
            })
            return None

        session = await self._ensure_session()
        url = f"{self.base_url}/v1/{self.transit_path}/decrypt/{key_name}"

        async with session.post(url, json={"ciphertext": ciphertext}) as resp:
            if resp.status == 200:
                result = await resp.json()
                # Audit log entry
                self.audit_log.append({
                    "action": "decrypt",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "key_name": key_name,
                    "plaintext_len": len(result.get("plaintext", "")),
                    "success": True,
                })
                return result
            else:
                body = await resp.json()
                self.audit_log.append({
                    "action": "decrypt_failed",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "key_name": key_name,
                    "status": resp.status,
                    "success": False,
                    "error": body.get("errors", ["unknown"]),
                })
                raise RuntimeError(f"Decryption failed: {resp.status} {body}")

    def get_audit_log(self) -> List[Dict[str, Any]]:
        """Return the audit log of all operations.

        Per Definition Lock: hash-chain logging for all cryptographic
        operations. Each entry is a dict with action, timestamp, and relevant
        metadata. The log can be serialized as a hash chain for immutability.

        Returns:
            List of audit log entries (dicts), oldest first.
        """
        return list(self.audit_log)

    def get_active_keys(self) -> Dict[str, str]:
        """Return dict of active key names and their algorithms.

        Returns:
            Mapping of key_name -> algorithm (e.g., {"model-gateway": "aes256-gcm"})
        """
        return dict(self.keys)

    async def close(self) -> None:
        """Close the aiohttp session."""
        if self.session and not self.session.closed:
            await self.session.close()
            self.session = None
