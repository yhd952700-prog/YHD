"""
Security module for LiuHao AI OS.

Provides comprehensive security capabilities including:
- Encryption and key management (encryption.py)
- Authentication and session management (authentication.py)
- Authorization and RBAC (authorization.py)
- Input sanitization and validation (input_sanitizer.py)
- Rate limiting (rate_limit.py)
- Security audit trail (audit_trail.py)

Legacy public API (jwt_handler / rbac / api_keys / audit_logger / vault_crypto
and the EncryptionManager compatibility layer in encryption.py) is re-exported
here as well: the gateway, the health probe and the existing test suite import
these names from the package root. Where the new modules define a name that
already exists in the legacy surface (``TokenType``, ``Role``, ``Permission``,
``ResourceType``, ``RoleStatus``), the LEGACY definition wins the package-level
name; the new-style definitions remain importable from their submodules
(e.g. ``src.security.authentication.TokenType``).

Import order matters to avoid circular imports: dependency-free base modules
(encryption) are imported before dependents (jwt_handler / rbac / vault_crypto).
"""

from __future__ import annotations

# --- Encryption (base module; also hosts the legacy EncryptionManager) ---
from .encryption import (
    AesGcmCipher,
    DataAtRestEncryption,
    EncryptedData,
    EncryptionConfig,
    EncryptionManager,
    EncryptionMode,
    HashAlgorithm,
    HashUtils,
    HmacUtils,
    InMemoryKeyManager,
    KeyDerivation,
    KeyManager,
    KeyMaterial,
    KeyType,
    PasswordHasher,
    RsaCipher,
    SecureRandom,
    decrypt_data,
    encrypt_data,
    generate_key,
    get_encryption_manager,
)

# --- New-style modules (depend only on .encryption or nothing) ---
# NOTE: authentication.TokenType is intentionally NOT re-exported here; the
# package-level ``TokenType`` is the JWT one from jwt_handler (see below).
from .authentication import (
    ApiKey,
    ApiKeyManager,
    AuthEvent,
    AuthEventLogger,
    AuthMethod,
    AuthResult,
    AuthStatus,
    JwtManager,
    LoginAttempt,
    LoginAttemptTracker,
    MfaManager,
    MfaMethod,
    MfaProvider,
    PasswordAuthenticator,
    Session,
    SessionManager,
    TotpMfaProvider,
)
# NOTE: authorization.{Permission, ResourceType, Role, RoleStatus} are
# intentionally NOT re-exported here; those package-level names come from the
# legacy rbac module (see below).
from .authorization import (
    AbacPolicy,
    Action,
    PermissionEffect,
    PermissionRegistry,
    PolicyEvaluationResult,
    RbacManager,
    SimpleAbacPolicy,
    UserRoleAssignment,
    create_default_rbac,
)
from .input_sanitizer import (
    CommandInjectionGuard,
    FileUploadCheckResult,
    FileUploadSecurity,
    InputValidator,
    PathTraversalGuard,
    SanitizationLevel,
    SqlInjectionGuard,
    ValidationResult,
    ValidationStatus,
    XssGuard,
)
from .rate_limit import (
    FixedWindowLimiter,
    MemoryRateLimitBackend,
    RateLimitBackend,
    RateLimitConfig,
    RateLimitHeaders,
    RateLimitManager,
    RateLimitResult,
    RateLimitStatus,
    RateLimitStrategy,
    RateLimitTier,
    SlidingWindowLimiter,
    TieredRateLimits,
    TokenBucketLimiter,
)
from .audit_trail import (
    AuditEvent,
    AuditEventType,
    AuditLogStatus,
    AuditRetentionPolicy,
    AuditSeverity,
    AuditStore,
    AuditTrailManager,
    ComplianceReport,
    HashChainAuditor,
    InMemoryAuditStore,
    SuspiciousActivityDetector,
)

# --- Legacy surface consumed by the gateway / health probe / test suite ---
from .api_keys import (
    APIKeyManager,
    KeyScope,
    KeyStatus,
    get_api_key_manager,
)
from .jwt_handler import (
    JWTHandler,
    TokenType,
    get_jwt_handler,
    JWT_SECRET_ENV,
    JWT_SECRET_ENV_ALIASES,
    JWT_PRIVATE_KEY_ENV,
    JWT_PUBLIC_KEY_ENV,
    JWT_ALGORITHM_ENV,
)
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
    "AesGcmCipher",
    "DataAtRestEncryption",
    "EncryptedData",
    "EncryptionConfig",
    "EncryptionManager",
    "EncryptionMode",
    "HashAlgorithm",
    "HashUtils",
    "HmacUtils",
    "InMemoryKeyManager",
    "KeyDerivation",
    "KeyManager",
    "KeyMaterial",
    "KeyType",
    "PasswordHasher",
    "RsaCipher",
    "SecureRandom",
    "decrypt_data",
    "encrypt_data",
    "generate_key",
    "get_encryption_manager",
    # Authentication (new-style)
    "ApiKey",
    "ApiKeyManager",
    "AuthEvent",
    "AuthEventLogger",
    "AuthMethod",
    "AuthResult",
    "AuthStatus",
    "JwtManager",
    "LoginAttempt",
    "LoginAttemptTracker",
    "MfaManager",
    "MfaMethod",
    "MfaProvider",
    "PasswordAuthenticator",
    "Session",
    "SessionManager",
    "TotpMfaProvider",
    # Authorization (new-style)
    "AbacPolicy",
    "Action",
    "PermissionEffect",
    "PermissionRegistry",
    "PolicyEvaluationResult",
    "RbacManager",
    "SimpleAbacPolicy",
    "UserRoleAssignment",
    "create_default_rbac",
    # Input sanitization
    "CommandInjectionGuard",
    "FileUploadCheckResult",
    "FileUploadSecurity",
    "InputValidator",
    "PathTraversalGuard",
    "SanitizationLevel",
    "SqlInjectionGuard",
    "ValidationResult",
    "ValidationStatus",
    "XssGuard",
    # Rate limiting
    "FixedWindowLimiter",
    "MemoryRateLimitBackend",
    "RateLimitBackend",
    "RateLimitConfig",
    "RateLimitHeaders",
    "RateLimitManager",
    "RateLimitResult",
    "RateLimitStatus",
    "RateLimitStrategy",
    "RateLimitTier",
    "SlidingWindowLimiter",
    "TieredRateLimits",
    "TokenBucketLimiter",
    # Audit trail (new-style)
    "AuditEvent",
    "AuditEventType",
    "AuditLogStatus",
    "AuditRetentionPolicy",
    "AuditSeverity",
    "AuditStore",
    "AuditTrailManager",
    "ComplianceReport",
    "HashChainAuditor",
    "InMemoryAuditStore",
    "SuspiciousActivityDetector",
    # API keys (legacy)
    "APIKeyManager",
    "KeyScope",
    "KeyStatus",
    "get_api_key_manager",
    # JWT (legacy; TokenType here is the JWT one)
    "JWTHandler",
    "TokenType",
    "get_jwt_handler",
    "JWT_SECRET_ENV",
    "JWT_SECRET_ENV_ALIASES",
    "JWT_PRIVATE_KEY_ENV",
    "JWT_PUBLIC_KEY_ENV",
    "JWT_ALGORITHM_ENV",
    # RBAC (legacy)
    "RBACManager",
    "Role",
    "Permission",
    "PermissionAction",
    "ResourceType",
    "RoleStatus",
    "get_rbac_manager",
    # Crypto audit (legacy)
    "CryptoAuditLogger",
    "CryptoAuditEvent",
    "CryptoOperation",
    # Vault transit crypto (legacy)
    "VaultTransitCrypto",
]
