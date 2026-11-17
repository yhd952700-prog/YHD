"""
Security module for LiuHao AI OS.

Provides comprehensive security capabilities including:
- Encryption and key management (encryption.py)
- Authentication and session management (authentication.py)
- Authorization and RBAC (authorization.py)
- Input sanitization and validation (input_sanitizer.py)
- Rate limiting (rate_limit.py)
- Audit: the only authoritative audit is ``src/kernels/audit``
  (SQLite + hash chain); the legacy ``audit_trail.py`` /
  ``audit_logger.py`` helpers are no longer re-exported from this
  package root since 2026-11-12 (the files remain for submodule
  importers such as ``src.security.abac``; they are not evidence-grade).

Legacy public API (jwt_handler / api_keys / vault_crypto
and the EncryptionManager compatibility layer in encryption.py) is re-exported
here as well: the gateway, the health probe and the existing test suite import
these names from the package root. Where the new modules define a name that
already exists in the legacy surface (``TokenType``, ``Role``, ``Permission``,
``ResourceType``, ``RoleStatus``), the LEGACY definition wins the package-level
name; the new-style definitions remain importable from their submodules
(e.g. ``src.security.authentication.TokenType``).

Import order matters to avoid circular imports: dependency-free base modules
(encryption) are imported before dependents (jwt_handler / vault_crypto).
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
# intentionally NOT re-exported here.
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

# --- Legacy surface consumed by the gateway / health probe / test suite ---
from .api_keys import (
    APIKeyManager,
    KeyScope,
    KeyStatus,
    get_api_key_manager,
)
from .jwt_handler import (
    IdentityStoreUnavailable,
    JWTHandler,
    TokenType,
    get_jwt_handler,
    JWT_SECRET_ENV,
    JWT_SECRET_ENV_ALIASES,
    JWT_PRIVATE_KEY_ENV,
    JWT_PUBLIC_KEY_ENV,
    JWT_ALGORITHM_ENV,
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
    # API keys (legacy)
    "APIKeyManager",
    "KeyScope",
    "KeyStatus",
    "get_api_key_manager",
    # JWT (legacy; TokenType here is the JWT one)
    "IdentityStoreUnavailable",
    "JWTHandler",
    "TokenType",
    "get_jwt_handler",
    "JWT_SECRET_ENV",
    "JWT_SECRET_ENV_ALIASES",
    "JWT_PRIVATE_KEY_ENV",
    "JWT_PUBLIC_KEY_ENV",
    "JWT_ALGORITHM_ENV",
    # Vault transit crypto (legacy)
    "VaultTransitCrypto",
]
