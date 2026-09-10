"""Security Hardening for LiuHao AI OS — Workstream H

Per Definition Lock constraints and Hermes override #16/#19.
Implements Vault Transit integration, RBAC/ABAC hardening for the Model Gateway
and broader system security.

Spec items: Vault Transit hash-chain logging, RBAC policy propagation,
ABAC rule evaluation, capability-based access control.

This package re-exports the public API surface consumed by the test suite and
downstream modules. Import order matters to avoid circular imports:
dependencies (encryption / vault_client) are imported before dependents
(api_keys / jwt_handler / rbac / audit_logger / vault_crypto).
"""

from __future__ import annotations

# Dependency-free base modules first.
from .encryption import EncryptionManager, get_encryption_manager
from .api_keys import APIKeyManager, KeyScope, KeyStatus, get_api_key_manager
from .jwt_handler import JWTHandler, TokenType, get_jwt_handler
from .rbac import (
    RBACManager,
    Role,
    Permission,
    PermissionAction,
    ResourceType,
    RoleStatus,
    get_rbac_manager,
)
from .audit_logger import (
    CryptoAuditLogger,
    CryptoAuditEvent,
    CryptoOperation,
)
from .vault_crypto import VaultTransitCrypto

__all__ = [
    # Encryption
    "EncryptionManager",
    "get_encryption_manager",
    # API keys
    "APIKeyManager",
    "KeyScope",
    "KeyStatus",
    "get_api_key_manager",
    # JWT
    "JWTHandler",
    "TokenType",
    "get_jwt_handler",
    # RBAC
    "RBACManager",
    "Role",
    "Permission",
    "PermissionAction",
    "ResourceType",
    "RoleStatus",
    "get_rbac_manager",
    # Crypto audit
    "CryptoAuditLogger",
    "CryptoAuditEvent",
    "CryptoOperation",
    # Vault transit crypto
    "VaultTransitCrypto",
]
