"""
Crypto Audit Logger for LiuHao AI OS

Provides tamper-evident logging of all cryptographic operations:
- encrypt/decrypt invocations
- sign/verify operations
- key generation, rotation, export

Audit events are recorded IN-MEMORY in `self._events` as a hash chain
(event_hash / prev_event_hash, SHA256). They are NOT written to any store and
are NOT backed by the authoritative AuditStore (src.kernels.audit), so there is
NO durable / authoritative chain of custody — all events are lost on process
restart. Do NOT treat this logger as audit-grade evidence. For durable,
tamper-evident audit (HC-01 runtime integrity currently UNVERIFIED), use
src.kernels.audit.
"""

from src.common.hash_chain import HASH_ALGORITHMS as _CHAIN_HASH_ALGORITHMS
import collections
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional, Dict, Any, List, ClassVar

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
    #: P0-8c: which algorithm produced ``event_hash``. The chain now says how
    #: to verify itself instead of relying on a hard-coded sha256. It is
    #: EXCLUDED from the canonical form, so adding it changed no existing hash.
    hash_alg: str = "sha256"
    #: D21 / HC-09 — envelope-only version LABEL / contract selector. It is a
    #: class constant (NOT a dataclass field), so it is excluded from the
    #: canonical form: the chain's hashing is unchanged. It confers NO durability
    #: or authority on the chain; it only labels which canonical contract produced
    #: the event.
    canon_version: ClassVar[str] = "CURRENT"

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["operation"] = self.operation.value
        return data

    def compute_hash(self) -> str:
        """Compute this event's hash using its DECLARED algorithm.

        P0-8c: the algorithm is dispatched from the shared registry on the
        event's own ``hash_alg`` declaration. An unknown declaration fails
        closed (raises) -- it is never silently recomputed with sha256.
        """
        # Canonicalize: sort keys, remove event_hash and prev_event_hash
        canonical = self.to_dict()
        canonical.pop("event_hash", None)
        canonical.pop("prev_event_hash", None)
        # The declaration is metadata about the hash, not part of its input.
        canonical.pop("hash_alg", None)
        raw = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
        fn = _CHAIN_HASH_ALGORITHMS.get(self.hash_alg)
        if fn is None:
            raise ValueError(
                f"unknown hash algorithm {self.hash_alg!r}; this build can "
                f"verify {sorted(_CHAIN_HASH_ALGORITHMS)}"
            )
        return fn(raw.encode())


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
        - IN-MEMORY ONLY: events are held in ``self._events`` and are NOT
          persisted to any store (this is NOT backed by AuditStore and there is
          no durable/authoritative historical evidence). They are lost on
          process restart, so do not treat this logger as audit-grade evidence.
        - Also logs to standard logging for real-time monitoring
    """

    def __init__(self, component_name: str = "crypto", hwm_path: Optional[str] = None):
        self._component = component_name
        # D19: bounded ring buffer -> old events are evicted (telemetry, not
        # evidence). See ``dropped_count`` / ``high_water_mark``.
        self._events: collections.deque = collections.deque(maxlen=10000)
        self._last_hash: Optional[str] = None
        self._event_counter = 0
        self._dropped_count = 0
        # HWM (high-water-mark) telemetry file. None => file writing DISABLED
        # (the default for direct construction; the production singleton sets a
        # real path). The file holds ONLY a monotonic counter and must NEVER
        # influence any trust decision.
        self._hwm_path = hwm_path if hwm_path is not None else os.environ.get("AUDIT_HWM_PATH")
        self._lock = threading.RLock()
        self._entries_ever_written = self._load_hwm()

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

        Thread-safety: the hash-chain linkage (prev_event_hash / event_hash /
        _last_hash), the ring buffer, and the HWM counter are all mutated under
        ``self._lock`` so concurrent callers from a thread pool cannot corrupt
        the chain or silently drop the eviction counter.
        """
        # Sanitize: never log secrets, keys, or full tokens
        safe_details = redact_secrets(details) if details else {}

        with self._lock:
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

            # D19: ring eviction -> if the buffer is already full, the append
            # below will silently drop the OLDEST event. Count it as dropped
            # (telemetry) so callers can detect the loss.
            if len(self._events) == self._events.maxlen:
                self._dropped_count += 1
            self._events.append(event)

            self._entries_ever_written += 1
            if self._hwm_path is not None:
                self._save_hwm()

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
            try:
                expected_hash = event.compute_hash()
            except ValueError as exc:
                # P0-8c: an event declaring an algorithm this build cannot
                # perform is UNVERIFIABLE -- fail closed, never assume sha256.
                logger.error("audit event %s is unverifiable: %s", event.event_id, exc)
                return False
            if event.event_hash != expected_hash:
                logger.error(
                    f"Audit event hash mismatch at {event.event_id}: "
                    f"expected={expected_hash}, got={event.event_hash}"
                )
                return False
        return True

    # ------------------------------------------------------------------
    # D19: high-water-mark (HWM) telemetry
    # ------------------------------------------------------------------
    def _load_hwm(self) -> int:
        """Load the monotonic ``entries_ever_written`` counter from disk.

        Fail-open: ANY error (missing file, unreadable, malformed, wrong type)
        returns 0. The HWM file holds ONLY a counter and must NEVER influence a
        trust decision, so it is safe to ignore bad content.
        """
        if not self._hwm_path:
            return 0
        try:
            with open(self._hwm_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                val = data.get("entries_ever_written")
                if isinstance(val, int) and val >= 0:
                    return val
            logger.warning(
                "HWM file %s has unexpected content; starting counter at 0",
                self._hwm_path,
            )
            return 0
        except FileNotFoundError:
            return 0
        except Exception as exc:  # noqa: BLE001 - fail-open on any HWM error
            logger.warning("Failed to load HWM file %s: %s", self._hwm_path, exc)
            return 0

    def _save_hwm(self) -> None:
        """Persist the ``entries_ever_written`` counter (telemetry only).

        Called under ``self._lock``. Fail-open: ANY error is logged and swallowed
        so a bad/locked file can NEVER break crypto logging. The file holds ONLY
        a counter and is NOT audit evidence.
        """
        if not self._hwm_path:
            return
        with self._lock:
            try:
                payload = {"entries_ever_written": self._entries_ever_written}
                tmp = f"{self._hwm_path}.tmp"
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh)
                try:
                    os.chmod(tmp, 0o600)
                except OSError:
                    pass
                os.replace(tmp, self._hwm_path)
                # Defensive: ensure the final file perms are 0600.
                try:
                    os.chmod(self._hwm_path, 0o600)
                except OSError:
                    pass
            except Exception as exc:  # noqa: BLE001 - fail-open on any HWM error
                logger.warning("Failed to save HWM file %s: %s", self._hwm_path, exc)

    @property
    def dropped_count(self) -> int:
        """Number of events evicted from the ring buffer (telemetry, not evidence)."""
        return self._dropped_count

    @property
    def high_water_mark(self) -> int:
        """Monotonic count of events ever written (survives restart via HWM file)."""
        return self._entries_ever_written

    def clear(self) -> None:
        """Clear the in-memory event buffer.

        This logger is memory-only (no persistence layer exists), so clearing
        the buffer discards all recorded crypto events permanently.

        NOTE: clear() does NOT reset ``high_water_mark`` (the monotonic
        ``entries_ever_written`` counter) — it is intentionally durable across
        clears so the telemetry survives. The HWM file is telemetry, NOT audit
        evidence, and must never be treated as authoritative.
        """
        with self._lock:
            self._events.clear()
            self._last_hash = None
            self._event_counter = 0
        # Intentionally NOT resetting ``self._entries_ever_written`` and NOT
        # incrementing ``self._dropped_count``.


# Module-level default instance
_default_logger: Optional[CryptoAuditLogger] = None


def get_crypto_audit_logger() -> CryptoAuditLogger:
    """Get singleton crypto audit logger.

    The production singleton is the ONLY construction that writes the HWM
    telemetry file: it resolves a real path from ``AUDIT_HWM_PATH`` or a default
    under the ``~/.liuhao`` data dir. Direct constructions keep ``hwm_path=None``
    (file writing disabled) unless they opt in explicitly.
    """
    global _default_logger
    if _default_logger is None:
        hwm_path = os.environ.get("AUDIT_HWM_PATH")
        if not hwm_path:
            data_dir = os.path.join(os.path.expanduser("~"), ".liuhao")
            try:
                os.makedirs(data_dir, exist_ok=True)
            except OSError:
                data_dir = None
            hwm_path = os.path.join(data_dir, "audit_hwm.json") if data_dir else None
        _default_logger = CryptoAuditLogger(hwm_path=hwm_path)
    return _default_logger
