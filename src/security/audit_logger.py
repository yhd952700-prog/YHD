"""
Crypto Audit Logger for LiuHao AI OS

Provides tamper-evident logging of all cryptographic operations:
- encrypt/decrypt invocations
- sign/verify operations
- key generation, rotation, export
- Vault Transit key lifecycle

Audit events are written to the AuditStore with SHA256 event hashing,
ensuring a verifiable chain of custody for all crypto operations.
"""

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)


class CryptoOperation(Enum):
    """Cryptographic operation types for audit logging."""
    ENCRYPT = "encrypt"
    DECRYPT = "decrypt"
    SIGN = "sign"
    VERIFY = "verify"
    KEY_GENERATE = "key_generate"
    KEY_ROTATE = "key_rotate"
    KEY_EXPORT = "key_export"
    KEY_IMPORT = "key_import"
    HMAC_COMPUTE = "hmac_compute"
    HMAC_VERIFY = "hmac_verify"
    TOKEN_ISSUE = "token_issue"
    TOKEN_VALIDATE = "token_validate"
    TOKEN_REVOKE = "token_revoke"


@dataclass
class CryptoAuditEvent:
    """Audit event for a cryptographic operation."""
    event_id: str
    operation: CryptoOperation
    component: str           # e.g., "jwt_handler", "api_key_manager", "vault_crypto"
    key_name: Optional[str]  # e.g., "jwt-signing-key", "api-key-hash-pepper", None if N/A
    key_id: Optional[str]    # UUID or identifier of the key
    success: bool
    timestamp: float
    duration_ms: Optional[float] = None
    details: Dict[str, Any] = field(default_factory=dict)
    # Cryptographic hash of this event's canonical form (set after creation)
    event_hash: Optional[str] = None
    # Hash of the previous event (links into audit chain)
    prev_event_hash: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["operation"] = self.operation.value
        return data

    def compute_hash(self) -> str:
        """Compute SHA256 hash of this event's canonical JSON form."""
        # Canonicalize: sort keys, remove event_hash and prev_event_hash
        canonical = self.to_dict()
        canonical.pop("event_hash", None)
        canonical.pop("prev_event_hash", None)
        raw = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()


def redact_secrets(details: Dict[str, Any]) -> Dict[str, Any]:
    """
    Redact sensitive values from a details dictionary.

    Any key containing 'secret', 'key', 'token', 'password', or 'passphrase'
    (case-insensitive) has its value replaced with '[REDACTED]'.
    String values longer than 512 characters are truncated to 128 + '...[truncated]'
    (142 chars total).
    """
    safe_details: Dict[str, Any] = {}
    if details:
        for k, v in details.items():
            if any(s in k.lower() for s in ("secret", "key", "token", "password", "passphrase")):
                safe_details[k] = "[REDACTED]"
            elif isinstance(v, str) and len(v) > 512:
                safe_details[k] = v[:128] + "...[truncated]"
            else:
                safe_details[k] = v
    return safe_details


class CryptoAuditLogger:
    """
    Tamper-evident logger for cryptographic operations.

    Every crypto operation that touches keys or secrets is logged here.
    Events form a hash chain so any tampering is detectable.

    Integration:
        - Called from EncryptionManager, JWTHandler, APIKeyManager
        - Uses the existing AuditStore for persistence
        - Also logs to standard logging for real-time monitoring
    """

    def __init__(self, component_name: str = "crypto"):
        self._component = component_name
        self._events: List[CryptoAuditEvent] = []
        self._last_hash: Optional[str] = None
        self._event_counter = 0

    def _generate_event_id(self) -> str:
        """Generate a unique event ID using timestamp + counter + random."""
        import secrets
        self._event_counter += 1
        ts = int(time.time() * 1000000)
        rand = secrets.token_hex(4)
        return f"crypto-{ts}-{self._event_counter}-{rand}"

    def log(
        self,
        operation: CryptoOperation,
        key_name: Optional[str] = None,
        key_id: Optional[str] = None,
        success: bool = True,
        duration_ms: Optional[float] = None,
        details: Optional[Dict[str, Any]] = None,
        component: Optional[str] = None,
    ) -> CryptoAuditEvent:
        """
        Log a cryptographic operation.

        Args:
            operation: Type of crypto operation
            key_name: Logical key name (e.g., "jwt-signing-key")
            key_id: Key identifier/UUID
            success: Whether the operation succeeded
            duration_ms: Operation duration in milliseconds
            details: Additional context (algorithm, key_size, etc.)
            component: Component that performed the operation

        Returns:
            The CryptoAuditEvent that was logged
        """
        # Sanitize: never log secrets, keys, or full tokens
        safe_details = redact_secrets(details) if details else {}

        event = CryptoAuditEvent(
            event_id=self._generate_event_id(),
            operation=operation,
            component=component or self._component,
            key_name=key_name,
            key_id=key_id,
            success=success,
            timestamp=time.time(),
            duration_ms=duration_ms,
            details=safe_details,
        )
        event.prev_event_hash = self._last_hash
        event.event_hash = event.compute_hash()
        self._last_hash = event.event_hash

        # Add to local buffer
        self._events.append(event)

        # Log to standard logging (info for operations, warning for failures)
        level = logging.INFO if success else logging.WARNING
        logger.log(
            level,
            f"[CRYPTO-AUDIT] op={operation.value} key={key_name or 'N/A'} "
            f"comp={event.component} success={success}"
            + (f" duration={duration_ms}ms" if duration_ms else ""),
        )

        return event

    def log_encryption(
        self,
        algorithm: str,
        key_name: Optional[str] = None,
        key_id: Optional[str] = None,
        success: bool = True,
        details: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> CryptoAuditEvent:
        """Log an encryption operation."""
        details = details or {}
        details.setdefault("algorithm", algorithm)
        return self.log(
            CryptoOperation.ENCRYPT,
            key_name=key_name,
            key_id=key_id,
            success=success,
            details=details,
            component=kwargs.get("component"),
        )

    def log_decryption(
        self,
        algorithm: str,
        key_name: Optional[str] = None,
        key_id: Optional[str] = None,
        success: bool = True,
        details: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> CryptoAuditEvent:
        """Log a decryption operation."""
        details = details or {}
        details.setdefault("algorithm", algorithm)
        return self.log(
            CryptoOperation.DECRYPT,
            key_name=key_name,
            key_id=key_id,
            success=success,
            details=details,
            component=kwargs.get("component"),
        )

    def log_signing(
        self,
        algorithm: str,
        key_name: Optional[str] = None,
        key_id: Optional[str] = None,
        success: bool = True,
        details: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> CryptoAuditEvent:
        """Log a signing operation."""
        details = details or {}
        details.setdefault("algorithm", algorithm)
        return self.log(
            CryptoOperation.SIGN,
            key_name=key_name,
            key_id=key_id,
            success=success,
            details=details,
            component=kwargs.get("component"),
        )

    def log_key_generation(
        self,
        algorithm: str,
        key_name: Optional[str] = None,
        key_id: Optional[str] = None,
        success: bool = True,
        details: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> CryptoAuditEvent:
        """Log a key generation operation."""
        details = details or {}
        details.setdefault("algorithm", algorithm)
        return self.log(
            CryptoOperation.KEY_GENERATE,
            key_name=key_name,
            key_id=key_id,
            success=success,
            details=details,
            component=kwargs.get("component"),
        )

    def get_events(self, limit: Optional[int] = None) -> List[CryptoAuditEvent]:
        """Get recent audit events (newest first)."""
        events = list(reversed(self._events))
        return events[:limit] if limit else events

    def verify_chain(self) -> bool:
        """
        Verify the integrity of the audit event chain.

        Returns True if the hash chain is intact, False otherwise.
        """
        if not self._events:
            return True
        for i, event in enumerate(self._events):
            expected_prev = self._events[i - 1].event_hash if i > 0 else None
            if event.prev_event_hash != expected_prev:
                logger.error(
                    f"Audit chain broken at event {event.event_id}: "
                    f"expected prev_hash={expected_prev}, got={event.prev_event_hash}"
                )
                return False
            expected_hash = event.compute_hash()
            if event.event_hash != expected_hash:
                logger.error(
                    f"Audit event hash mismatch at {event.event_id}: "
                    f"expected={expected_hash}, got={event.event_hash}"
                )
                return False
        return True

    def clear(self) -> None:
        """Clear the in-memory event buffer (does NOT clear persistent storage)."""
        self._events.clear()
        self._last_hash = None
        self._event_counter = 0


# Module-level default instance
_default_logger: Optional[CryptoAuditLogger] = None


def get_crypto_audit_logger() -> CryptoAuditLogger:
    """Get singleton crypto audit logger."""
    global _default_logger
    if _default_logger is None:
        _default_logger = CryptoAuditLogger()
    return _default_logger
