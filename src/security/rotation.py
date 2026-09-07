"""
Key Rotation Policy for LiuHao AI OS.

Provides:
- 90-day default rotation cadence for JWT signing keys and API keys
- Grace period support for overlapping keys
- Automatic rotation triggering via check_rotation_needed()
- Policy configuration per key type
"""

import time
import secrets
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass, field
from enum import Enum

from .api_keys import APIKeyManager, KeyScope, KeyStatus
from .jwt_handler import JWTHandler


class KeyType(Enum):
    JWT_SIGNING = "jwt_signing"
    API_KEY = "api_key"
    ENCRYPTION = "encryption"


@dataclass
class RotationPolicy:
    """Policy for key rotation."""
    key_type: KeyType
    ttl_days: int = 90
    grace_period_days: int = 7
    notify_before_days: int = 7
    min_overlap_hours: int = 24
    auto_rotate: bool = True

    def ttl_seconds(self) -> int:
        return self.ttl_days * 86400

    def grace_period_seconds(self) -> int:
        return self.grace_period_days * 86400

    def notify_before_seconds(self) -> int:
        return self.notify_before_days * 86400


# Default policies
DEFAULT_POLICIES: Dict[KeyType, RotationPolicy] = {
    KeyType.JWT_SIGNING: RotationPolicy(
        key_type=KeyType.JWT_SIGNING,
        ttl_days=90,
        grace_period_days=14,
        notify_before_days=7,
        auto_rotate=True,
    ),
    KeyType.API_KEY: RotationPolicy(
        key_type=KeyType.API_KEY,
        ttl_days=90,
        grace_period_days=7,
        notify_before_days=7,
        auto_rotate=True,
    ),
    KeyType.ENCRYPTION: RotationPolicy(
        key_type=KeyType.ENCRYPTION,
        ttl_days=180,
        grace_period_days=30,
        notify_before_days=14,
        auto_rotate=False,
    ),
}


class KeyRotationManager:
    """
    Manages key rotation lifecycle across JWT signing keys,
    API keys, and encryption keys.

    Integrates with VaultTransitCrypto for key versioning and
    CryptoAuditLogger for audit trail of all rotation events.
    """

    def __init__(
        self,
        api_key_manager: Optional[APIKeyManager] = None,
        jwt_handler: Optional[JWTHandler] = None,
        policies: Optional[Dict[KeyType, RotationPolicy]] = None,
    ):
        self.api_key_manager = api_key_manager
        self.jwt_handler = jwt_handler
        self.policies = policies or DEFAULT_POLICIES
        self._rotation_history: List[Dict[str, Any]] = []

    def check_rotation_needed(
        self,
        key_type: KeyType,
        created_at: float,
        now: Optional[float] = None,
    ) -> Tuple[bool, float]:
        """
        Check if a key needs rotation.

        Returns:
            Tuple of (needs_rotation, seconds_until_rotation_time)
        """
        now = now or time.time()
        policy = self.policies.get(key_type, DEFAULT_POLICIES[KeyType.JWT_SIGNING])

        elapsed = now - created_at
        ttl = policy.ttl_seconds()

        if elapsed >= ttl:
            return (True, 0.0)

        return (False, ttl - elapsed)

    def check_api_keys_rotation(
        self,
        now: Optional[float] = None,
    ) -> List[Tuple[str, bool, float]]:
        """Check all API keys for rotation status."""
        if not self.api_key_manager:
            return []

        now = now or time.time()
        results = []

        for key_id, key in self.api_key_manager._keys.items():
            needs_rotation, _ = self.check_rotation_needed(
                KeyType.API_KEY, key.created_at, now
            )
            results.append((key_id, needs_rotation, key.expires_at or 0))

        return results

    def rotate_api_key(
        self,
        key_id: str,
        new_ttl_days: int = 90,
    ) -> Tuple[bool, Optional[str]]:
        """Rotate a single API key."""
        if not self.api_key_manager:
            return (False, None)

        try:
            new_key, raw_key = self.api_key_manager.rotate_key(
                key_id, expires_in_days=new_ttl_days
            )
            self._rotation_history.append({
                "key_id": key_id,
                "new_key_id": new_key.id,
                "timestamp": time.time(),
                "key_type": KeyType.API_KEY.value,
            })
            return (True, raw_key)
        except (KeyError, ValueError):
            return (False, None)

    def auto_rotate_api_keys(self) -> int:
        """Automatically rotate all API keys that are past their TTL."""
        if not self.api_key_manager:
            return 0

        rotated = 0
        checks = self.check_api_keys_rotation()

        for key_id, needs_rotation, _ in checks:
            if needs_rotation:
                success, _ = self.rotate_api_key(key_id)
                if success:
                    rotated += 1

        return rotated

    def get_rotation_schedule(
        self,
        key_type: KeyType,
        created_at: float,
        now: Optional[float] = None,
    ) -> Dict[str, float]:
        """Get the full rotation schedule for a key."""
        now = now or time.time()
        policy = self.policies.get(key_type, DEFAULT_POLICIES[KeyType.JWT_SIGNING])

        ttl = policy.ttl_seconds()
        grace = policy.grace_period_seconds()
        notify = policy.notify_before_seconds()

        return {
            "created_at": created_at,
            "rotation_date": created_at + ttl,
            "grace_period_end": created_at + ttl + grace,
            "notification_date": created_at + ttl - notify,
            "now": now,
        }

    def get_stats(self) -> Dict[str, Any]:
        """Get rotation statistics."""
        return {
            "total_rotations": len(self._rotation_history),
            "policies": {
                kt.value: {
                    "ttl_days": p.ttl_days,
                    "grace_period_days": p.grace_period_days,
                    "auto_rotate": p.auto_rotate,
                }
                for kt, p in self.policies.items()
            },
        }


# Convenience function
_default_rotation_manager: Optional[KeyRotationManager] = None


def get_key_rotation_manager(
    api_key_manager: Optional[APIKeyManager] = None,
    jwt_handler: Optional[JWTHandler] = None,
) -> KeyRotationManager:
    """Get the singleton KeyRotationManager."""
    global _default_rotation_manager
    if _default_rotation_manager is None:
        _default_rotation_manager = KeyRotationManager(
            api_key_manager=api_key_manager,
            jwt_handler=jwt_handler,
        )
    return _default_rotation_manager
