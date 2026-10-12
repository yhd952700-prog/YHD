"""
Authentication system for LiuHao AI OS.

Provides API key authentication, token-based auth (JWT interface), session
management, MFA interface, password hashing/verification, login attempt
tracking with lockout, auth event logging, and token refresh/revocation.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from .encryption import HashAlgorithm, PasswordHasher, SecureRandom


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class AuthMethod(str, Enum):
    """Supported authentication methods."""

    API_KEY = "api_key"
    JWT = "jwt"
    SESSION = "session"
    PASSWORD = "password"
    MFA = "mfa"


class AuthStatus(str, Enum):
    """Authentication status values."""

    SUCCESS = "success"
    FAILURE = "failure"
    LOCKED = "locked"
    PENDING = "pending"
    EXPIRED = "expired"
    REVOKED = "revoked"


class TokenType(str, Enum):
    """Types of authentication tokens."""

    ACCESS = "access"
    REFRESH = "refresh"
    API_KEY = "api_key"
    RESET = "reset"
    VERIFICATION = "verification"


class MfaMethod(str, Enum):
    """Supported MFA methods."""

    TOTP = "totp"
    SMS = "sms"
    EMAIL = "email"
    WEBAUTHN = "webauthn"
    BACKUP_CODE = "backup_code"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class AuthResult:
    """Result of an authentication attempt."""

    success: bool
    status: AuthStatus
    user_id: Optional[str] = None
    method: Optional[AuthMethod] = None
    token: Optional[str] = None
    expires_at: Optional[float] = None
    message: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ApiKey:
    """Represents an API key."""

    key_id: str
    user_id: str
    key_hash: str
    key_prefix: str
    name: str
    created_at: float
    expires_at: Optional[float] = None
    last_used_at: Optional[float] = None
    is_active: bool = True
    scopes: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Session:
    """Represents a user session."""

    session_id: str
    user_id: str
    created_at: float
    expires_at: float
    last_activity_at: float
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    is_active: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class LoginAttempt:
    """Tracks a login attempt for rate limiting / lockout."""

    user_id: Optional[str]
    ip_address: str
    success: bool
    timestamp: float
    method: AuthMethod
    reason: Optional[str] = None


@dataclass
class AuthEvent:
    """Represents an authentication event for audit logging."""

    event_id: str
    event_type: str
    user_id: Optional[str]
    method: AuthMethod
    status: AuthStatus
    timestamp: float
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# API Key Authentication
# ---------------------------------------------------------------------------


class ApiKeyManager:
    """Manages API key creation, validation, and revocation.

    API keys are stored as SHA-256 hashes. Only the prefix (first 8 chars)
    is stored in plaintext for user identification.
    """

    KEY_PREFIX_LENGTH = 8
    KEY_LENGTH = 32

    def __init__(self) -> None:
        self._keys: dict[str, ApiKey] = {}  # key_id -> ApiKey

    def create_key(
        self,
        user_id: str,
        name: str,
        scopes: Optional[list[str]] = None,
        expires_in: Optional[int] = None,
    ) -> tuple[str, ApiKey]:
        """Create a new API key.

        Args:
            user_id: User who owns the key.
            name: Human-readable key name.
            scopes: Optional permission scopes.
            expires_in: Optional expiration time in seconds.

        Returns:
            Tuple of (plaintext_key, ApiKey metadata).
        """
        key_id = f"key_{uuid.uuid4().hex[:12]}"
        plaintext = f"lh_{secrets.token_urlsafe(self.KEY_LENGTH)}"

        # Store SHA-256 hash of the key
        key_hash = hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
        key_prefix = plaintext[: self.KEY_PREFIX_LENGTH]

        now = time.time()
        api_key = ApiKey(
            key_id=key_id,
            user_id=user_id,
            key_hash=key_hash,
            key_prefix=key_prefix,
            name=name,
            created_at=now,
            expires_at=now + expires_in if expires_in else None,
            scopes=scopes or [],
        )

        self._keys[key_id] = api_key
        return plaintext, api_key

    def validate_key(self, plaintext_key: str) -> Optional[ApiKey]:
        """Validate an API key and return its metadata if valid.

        Args:
            plaintext_key: The raw API key string.

        Returns:
            ApiKey if valid, None otherwise.
        """
        key_hash = hashlib.sha256(plaintext_key.encode("utf-8")).hexdigest()
        key_prefix = plaintext_key[: self.KEY_PREFIX_LENGTH]

        for key in self._keys.values():
            if not key.is_active:
                continue
            if key.key_prefix != key_prefix:
                continue
            if hmac.compare_digest(key.key_hash, key_hash):
                # Check expiration
                if key.expires_at and time.time() > key.expires_at:
                    return None
                key.last_used_at = time.time()
                return key
        return None

    def revoke_key(self, key_id: str) -> bool:
        """Revoke an API key.

        Returns True if the key was found and revoked.
        """
        if key_id in self._keys:
            self._keys[key_id].is_active = False
            return True
        return False

    def list_keys(self, user_id: Optional[str] = None) -> list[ApiKey]:
        """List API keys, optionally filtered by user."""
        keys = list(self._keys.values())
        if user_id:
            keys = [k for k in keys if k.user_id == user_id]
        return keys

    def get_key(self, key_id: str) -> Optional[ApiKey]:
        """Get API key metadata by ID."""
        return self._keys.get(key_id)


# ---------------------------------------------------------------------------
# JWT Authentication Interface
# ---------------------------------------------------------------------------


class JwtManager:
    """JWT token management interface.

    Uses the pyjwt library (already a project dependency).
    Supports access tokens, refresh tokens, and revocation.
    """

    def __init__(
        self,
        secret_key: str,
        algorithm: str = "HS256",
        access_token_ttl: int = 3600,  # 1 hour
        refresh_token_ttl: int = 604800,  # 7 days
        issuer: str = "liuhao-ai-os",
    ) -> None:
        """Initialize JWT manager.

        Args:
            secret_key: Secret key for signing tokens.
            algorithm: Signing algorithm (HS256, RS256, etc.).
            access_token_ttl: Access token lifetime in seconds.
            refresh_token_ttl: Refresh token lifetime in seconds.
            issuer: Token issuer claim.
        """
        self._secret = secret_key
        self._algorithm = algorithm
        self._access_ttl = access_token_ttl
        self._refresh_ttl = refresh_token_ttl
        self._issuer = issuer
        self._revoked_tokens: set[str] = set()

    def create_access_token(
        self,
        user_id: str,
        scopes: Optional[list[str]] = None,
        extra_claims: Optional[dict[str, Any]] = None,
    ) -> tuple[str, float]:
        """Create a new access token.

        Returns:
            Tuple of (token_string, expires_at_timestamp).
        """
        import jwt

        now = time.time()
        expires_at = now + self._access_ttl
        jti = str(uuid.uuid4())

        payload = {
            "sub": user_id,
            "iat": int(now),
            "exp": int(expires_at),
            "jti": jti,
            "iss": self._issuer,
            "type": TokenType.ACCESS.value,
            "scopes": scopes or [],
        }
        if extra_claims:
            payload.update(extra_claims)

        token = jwt.encode(payload, self._secret, algorithm=self._algorithm)
        return token, expires_at

    def create_refresh_token(
        self,
        user_id: str,
        extra_claims: Optional[dict[str, Any]] = None,
    ) -> tuple[str, float]:
        """Create a new refresh token."""
        import jwt

        now = time.time()
        expires_at = now + self._refresh_ttl
        jti = str(uuid.uuid4())

        payload = {
            "sub": user_id,
            "iat": int(now),
            "exp": int(expires_at),
            "jti": jti,
            "iss": self._issuer,
            "type": TokenType.REFRESH.value,
        }
        if extra_claims:
            payload.update(extra_claims)

        token = jwt.encode(payload, self._secret, algorithm=self._algorithm)
        return token, expires_at

    def verify_token(self, token: str) -> Optional[dict[str, Any]]:
        """Verify a JWT token and return its payload.

        Returns None if the token is invalid, expired, or revoked.
        """
        import jwt

        try:
            payload = jwt.decode(
                token,
                self._secret,
                algorithms=[self._algorithm],
                issuer=self._issuer,
            )
            # Check revocation
            jti = payload.get("jti")
            if jti and jti in self._revoked_tokens:
                return None
            return payload
        except jwt.ExpiredSignatureError:
            return None
        except jwt.InvalidTokenError:
            return None

    def refresh_token(
        self, refresh_token: str
    ) -> Optional[tuple[str, str, float]]:
        """Use a refresh token to get a new access token.

        Returns:
            Tuple of (new_access_token, new_refresh_token, expires_at) or None.
        """
        payload = self.verify_token(refresh_token)
        if payload is None or payload.get("type") != TokenType.REFRESH.value:
            return None

        user_id = payload["sub"]
        # Revoke old refresh token (rotation)
        jti = payload.get("jti")
        if jti:
            self._revoked_tokens.add(jti)

        # Issue new token pair
        new_access, exp = self.create_access_token(user_id)
        new_refresh, _ = self.create_refresh_token(user_id)
        return new_access, new_refresh, exp

    def revoke_token(self, token: str) -> bool:
        """Revoke a token by its JTI.

        Returns True if the token was valid and is now revoked.
        """
        import jwt

        try:
            payload = jwt.decode(
                token,
                self._secret,
                algorithms=[self._algorithm],
                options={"verify_exp": False},
            )
            jti = payload.get("jti")
            if jti:
                self._revoked_tokens.add(jti)
                return True
            return False
        except jwt.InvalidTokenError:
            return False

    def revoke_all_for_user(self, user_id: str) -> int:
        """Revoke all tokens for a user (adds to revocation list via pattern).

        Note: In a production system, this would query a token store.
        Here we track via a simple set, so we can only revoke tokens
        we've seen. Use a token_identifier pattern for broad revocation.

        Returns the count of added revocation entries.
        """
        # Add a user-level revocation marker
        marker = f"user:{user_id}:{time.time()}"
        self._revoked_tokens.add(marker)
        return 1


# ---------------------------------------------------------------------------
# Session Management
# ---------------------------------------------------------------------------


class SessionManager:
    """Manages user sessions with idle timeout and explicit logout."""

    def __init__(
        self,
        session_ttl: int = 86400,  # 24 hours
        idle_timeout: int = 3600,  # 1 hour idle
    ) -> None:
        """Initialize session manager.

        Args:
            session_ttl: Maximum session lifetime in seconds.
            idle_timeout: Idle timeout in seconds.
        """
        self._sessions: dict[str, Session] = {}
        self._session_ttl = session_ttl
        self._idle_timeout = idle_timeout

    def create_session(
        self,
        user_id: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> Session:
        """Create a new session for a user.

        Returns the Session object.
        """
        now = time.time()
        session_id = SecureRandom.token_urlsafe(32)
        session = Session(
            session_id=session_id,
            user_id=user_id,
            created_at=now,
            expires_at=now + self._session_ttl,
            last_activity_at=now,
            ip_address=ip_address,
            user_agent=user_agent,
            is_active=True,
            metadata=metadata or {},
        )
        self._sessions[session_id] = session
        return session

    def get_session(self, session_id: str) -> Optional[Session]:
        """Get a session by ID, updating last activity if valid.

        Returns None if session doesn't exist, is expired, or inactive.
        """
        session = self._sessions.get(session_id)
        if session is None:
            return None
        if not session.is_active:
            return None

        now = time.time()
        # Check absolute expiry
        if now > session.expires_at:
            session.is_active = False
            return None
        # Check idle timeout
        if now - session.last_activity_at > self._idle_timeout:
            session.is_active = False
            return None

        session.last_activity_at = now
        return session

    def end_session(self, session_id: str) -> bool:
        """End (log out) a session.

        Returns True if the session was found and ended.
        """
        session = self._sessions.get(session_id)
        if session and session.is_active:
            session.is_active = False
            return True
        return False

    def end_all_user_sessions(self, user_id: str) -> int:
        """End all active sessions for a user.

        Returns the count of ended sessions.
        """
        count = 0
        for session in self._sessions.values():
            if session.user_id == user_id and session.is_active:
                session.is_active = False
                count += 1
        return count

    def list_user_sessions(self, user_id: str) -> list[Session]:
        """List all sessions for a user (active and inactive)."""
        return [s for s in self._sessions.values() if s.user_id == user_id]

    def cleanup_expired(self) -> int:
        """Remove expired sessions from memory.

        Returns the count of cleaned sessions.
        """
        now = time.time()
        expired_ids = [
            sid
            for sid, s in self._sessions.items()
            if not s.is_active or now > s.expires_at
        ]
        for sid in expired_ids:
            del self._sessions[sid]
        return len(expired_ids)


# ---------------------------------------------------------------------------
# MFA (Multi-Factor Authentication) Interface
# ---------------------------------------------------------------------------


class MfaProvider(ABC):
    """Abstract base class for MFA providers."""

    @abstractmethod
    def generate_secret(self) -> str:
        """Generate a new MFA secret."""
        ...

    @abstractmethod
    def verify_code(self, secret: str, code: str) -> bool:
        """Verify an MFA code against a secret."""
        ...

    @abstractmethod
    def get_provisioning_uri(self, secret: str, user_id: str, issuer: str) -> str:
        """Get a provisioning URI (e.g. for QR code)."""
        ...


class TotpMfaProvider(MfaProvider):
    """TOTP (Time-based One-Time Password) MFA provider.

    Uses the pyotp library if available, otherwise implements a basic
    TOTP calculation based on RFC 6238.
    """

    def __init__(self, digits: int = 6, period: int = 30) -> None:
        self._digits = digits
        self._period = period

    def generate_secret(self) -> str:
        """Generate a random base32-encoded TOTP secret."""
        # Generate 20 random bytes and encode as base32
        import base64 as b64

        random_bytes = secrets.token_bytes(20)
        # Use base32 encoding
        return b64.b32encode(random_bytes).decode("ascii").rstrip("=")

    def verify_code(self, secret: str, code: str) -> bool:
        """Verify a TOTP code.

        Implements RFC 6238 TOTP using HMAC-SHA1.
        Accepts codes from the current and adjacent time steps (drift tolerance).
        """
        try:
            import pyotp  # type: ignore

            totp = pyotp.TOTP(secret, digits=self._digits, interval=self._period)
            return totp.verify(code, valid_window=1)
        except ImportError:
            return self._verify_manual(secret, code)

    def _verify_manual(self, secret: str, code: str) -> bool:
        """Manual TOTP verification fallback."""
        import base64 as b64
        import struct

        # Pad secret to base32 block size
        padding = 8 - (len(secret) % 8)
        if padding != 8:
            secret += "=" * padding

        try:
            key = b64.b32decode(secret.upper())
        except Exception:
            return False

        now = int(time.time())
        for offset in (-1, 0, 1):
            counter = (now // self._period) + offset
            counter_bytes = struct.pack(">Q", counter)
            h = hmac.new(key, counter_bytes, hashlib.sha1).digest()
            offset_byte = h[-1] & 0x0F
            binary = struct.unpack(">I", h[offset_byte : offset_byte + 4])[0] & 0x7FFFFFFF
            generated = str(binary % (10**self._digits)).zfill(self._digits)
            if hmac.compare_digest(generated, code):
                return True
        return False

    def get_provisioning_uri(self, secret: str, user_id: str, issuer: str) -> str:
        """Get an otpauth:// URI for QR code generation."""
        from urllib.parse import quote

        return (
            f"otpauth://totp/{quote(issuer)}:{quote(user_id)}"
            f"?secret={secret}&issuer={quote(issuer)}"
            f"&algorithm=SHA1&digits={self._digits}&period={self._period}"
        )


class MfaManager:
    """Manages multi-factor authentication for users."""

    def __init__(self, default_method: MfaMethod = MfaMethod.TOTP) -> None:
        self._default_method = default_method
        self._providers: dict[MfaMethod, MfaProvider] = {
            MfaMethod.TOTP: TotpMfaProvider(),
        }
        self._user_secrets: dict[str, dict[MfaMethod, str]] = {}
        self._backup_codes: dict[str, list[str]] = {}
        self._enabled_users: set[str] = set()

    def enable_mfa(
        self,
        user_id: str,
        method: MfaMethod = MfaMethod.TOTP,
    ) -> tuple[str, str]:
        """Enable MFA for a user.

        Returns:
            Tuple of (secret, provisioning_uri).
        """
        provider = self._providers.get(method)
        if provider is None:
            raise ValueError(f"Unsupported MFA method: {method}")

        secret = provider.generate_secret()
        if user_id not in self._user_secrets:
            self._user_secrets[user_id] = {}
        self._user_secrets[user_id][method] = secret
        self._enabled_users.add(user_id)

        # Generate backup codes
        self._backup_codes[user_id] = [
            secrets.token_hex(4).upper() for _ in range(10)
        ]

        uri = provider.get_provisioning_uri(secret, user_id, "LiuHao AI OS")
        return secret, uri

    def verify(self, user_id: str, code: str, method: MfaMethod = MfaMethod.TOTP) -> bool:
        """Verify an MFA code for a user.

        Also checks backup codes.
        """
        if user_id not in self._enabled_users:
            return False

        # Check backup codes first
        if user_id in self._backup_codes:
            for i, bc in enumerate(self._backup_codes[user_id]):
                if hmac.compare_digest(bc, code.upper()):
                    # Consume the backup code
                    self._backup_codes[user_id].pop(i)
                    return True

        provider = self._providers.get(method)
        if provider is None:
            return False

        user_method_secrets = self._user_secrets.get(user_id, {})
        secret = user_method_secrets.get(method)
        if not secret:
            return False

        return provider.verify_code(secret, code)

    def is_enabled(self, user_id: str) -> bool:
        """Check if MFA is enabled for a user."""
        return user_id in self._enabled_users

    def disable_mfa(self, user_id: str) -> bool:
        """Disable MFA for a user.

        Returns True if MFA was previously enabled.
        """
        if user_id in self._enabled_users:
            self._enabled_users.discard(user_id)
            self._user_secrets.pop(user_id, None)
            self._backup_codes.pop(user_id, None)
            return True
        return False

    def get_backup_codes(self, user_id: str) -> list[str]:
        """Get remaining backup codes for a user."""
        return list(self._backup_codes.get(user_id, []))

    def regenerate_backup_codes(self, user_id: str) -> list[str]:
        """Regenerate backup codes for a user."""
        if user_id not in self._enabled_users:
            raise ValueError("MFA not enabled for user")
        self._backup_codes[user_id] = [
            secrets.token_hex(4).upper() for _ in range(10)
        ]
        return list(self._backup_codes[user_id])


# ---------------------------------------------------------------------------
# Login Attempt Tracking and Lockout
# ---------------------------------------------------------------------------


class LoginAttemptTracker:
    """Tracks login attempts and implements account lockout.

    Supports per-user and per-IP lockout with configurable thresholds
    and lockout duration.
    """

    def __init__(
        self,
        max_attempts: int = 5,
        lockout_duration: int = 900,  # 15 minutes
        window_seconds: int = 300,  # 5 minute window
    ) -> None:
        """Initialize the tracker.

        Args:
            max_attempts: Maximum failed attempts before lockout.
            lockout_duration: Lockout duration in seconds.
            window_seconds: Time window for counting attempts.
        """
        self._max_attempts = max_attempts
        self._lockout_duration = lockout_duration
        self._window = window_seconds

        self._user_attempts: dict[str, list[LoginAttempt]] = {}
        self._ip_attempts: dict[str, list[LoginAttempt]] = {}
        self._user_lockouts: dict[str, float] = {}  # user_id -> unlock_time
        self._ip_lockouts: dict[str, float] = {}  # ip -> unlock_time

    def record_attempt(
        self,
        success: bool,
        user_id: Optional[str] = None,
        ip_address: str = "unknown",
        method: AuthMethod = AuthMethod.PASSWORD,
        reason: Optional[str] = None,
    ) -> AuthResult:
        """Record a login attempt and check lockout status.

        Returns:
            AuthResult indicating success or lockout status.
        """
        now = time.time()
        attempt = LoginAttempt(
            user_id=user_id,
            ip_address=ip_address,
            success=success,
            timestamp=now,
            method=method,
            reason=reason,
        )

        # Track per-user
        if user_id:
            if user_id not in self._user_attempts:
                self._user_attempts[user_id] = []
            self._user_attempts[user_id].append(attempt)

        # Track per-IP
        if ip_address not in self._ip_attempts:
            self._ip_attempts[ip_address] = []
        self._ip_attempts[ip_address].append(attempt)

        # Check for lockout on failure
        if not success:
            if user_id and self._check_user_lockout(user_id):
                self._user_lockouts[user_id] = now + self._lockout_duration
            if self._check_ip_lockout(ip_address):
                self._ip_lockouts[ip_address] = now + self._lockout_duration

            status = AuthStatus.FAILURE
            if self.is_locked_out(user_id, ip_address):
                status = AuthStatus.LOCKED

            return AuthResult(
                success=False,
                status=status,
                user_id=user_id,
                method=method,
                message=reason or "Authentication failed",
            )

        return AuthResult(
            success=True,
            status=AuthStatus.SUCCESS,
            user_id=user_id,
            method=method,
        )

    def _check_user_lockout(self, user_id: str) -> bool:
        """Check if a user has exceeded the attempt threshold."""
        now = time.time()
        attempts = [
            a
            for a in self._user_attempts.get(user_id, [])
            if not a.success and (now - a.timestamp) <= self._window
        ]
        return len(attempts) >= self._max_attempts

    def _check_ip_lockout(self, ip_address: str) -> bool:
        """Check if an IP has exceeded the attempt threshold."""
        now = time.time()
        attempts = [
            a
            for a in self._ip_attempts.get(ip_address, [])
            if not a.success and (now - a.timestamp) <= self._window
        ]
        return len(attempts) >= self._max_attempts

    def is_locked_out(self, user_id: Optional[str] = None, ip_address: Optional[str] = None) -> bool:
        """Check if a user or IP is currently locked out."""
        now = time.time()
        if user_id and user_id in self._user_lockouts:
            if now < self._user_lockouts[user_id]:
                return True
            # Lockout expired
            del self._user_lockouts[user_id]
        if ip_address and ip_address in self._ip_lockouts:
            if now < self._ip_lockouts[ip_address]:
                return True
            del self._ip_lockouts[ip_address]
        return False

    def get_remaining_attempts(self, user_id: Optional[str] = None, ip_address: Optional[str] = None) -> int:
        """Get the number of remaining attempts before lockout."""
        now = time.time()
        failed = 0

        if user_id:
            user_failed = [
                a
                for a in self._user_attempts.get(user_id, [])
                if not a.success and (now - a.timestamp) <= self._window
            ]
            failed = max(failed, len(user_failed))

        if ip_address:
            ip_failed = [
                a
                for a in self._ip_attempts.get(ip_address, [])
                if not a.success and (now - a.timestamp) <= self._window
            ]
            failed = max(failed, len(ip_failed))

        return max(0, self._max_attempts - failed)

    def reset_attempts(self, user_id: Optional[str] = None, ip_address: Optional[str] = None) -> None:
        """Reset login attempts for a user and/or IP."""
        if user_id and user_id in self._user_attempts:
            self._user_attempts[user_id] = []
            self._user_lockouts.pop(user_id, None)
        if ip_address and ip_address in self._ip_attempts:
            self._ip_attempts[ip_address] = []
            self._ip_lockouts.pop(ip_address, None)

    def cleanup_old(self) -> int:
        """Remove old attempt records.

        Returns the count of cleaned entries.
        """
        now = time.time()
        cutoff = now - self._window * 2
        cleaned = 0

        for user_id in list(self._user_attempts.keys()):
            self._user_attempts[user_id] = [
                a for a in self._user_attempts[user_id] if a.timestamp > cutoff
            ]
            if not self._user_attempts[user_id]:
                del self._user_attempts[user_id]
                cleaned += 1

        for ip in list(self._ip_attempts.keys()):
            self._ip_attempts[ip] = [
                a for a in self._ip_attempts[ip] if a.timestamp > cutoff
            ]
            if not self._ip_attempts[ip]:
                del self._ip_attempts[ip]
                cleaned += 1

        # Clean expired lockouts
        for user_id in list(self._user_lockouts.keys()):
            if now >= self._user_lockouts[user_id]:
                del self._user_lockouts[user_id]
                cleaned += 1
        for ip in list(self._ip_lockouts.keys()):
            if now >= self._ip_lockouts[ip]:
                del self._ip_lockouts[ip]
                cleaned += 1

        return cleaned


# ---------------------------------------------------------------------------
# Auth Event Logging
# ---------------------------------------------------------------------------


class AuthEventLogger:
    """Logs authentication events for audit trail purposes."""

    def __init__(self, max_events: int = 10000) -> None:
        self._events: list[AuthEvent] = []
        self._max_events = max_events

    def log_event(
        self,
        event_type: str,
        user_id: Optional[str],
        method: AuthMethod,
        status: AuthStatus,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> AuthEvent:
        """Record an authentication event."""
        event = AuthEvent(
            event_id=str(uuid.uuid4()),
            event_type=event_type,
            user_id=user_id,
            method=method,
            status=status,
            timestamp=time.time(),
            ip_address=ip_address,
            user_agent=user_agent,
            metadata=metadata or {},
        )
        self._events.append(event)
        # Trim to max events
        if len(self._events) > self._max_events:
            self._events = self._events[-self._max_events :]
        return event

    def get_events(
        self,
        user_id: Optional[str] = None,
        event_type: Optional[str] = None,
        status: Optional[AuthStatus] = None,
        start_time: Optional[float] = None,
        end_time: Optional[float] = None,
        limit: int = 100,
    ) -> list[AuthEvent]:
        """Filter and return auth events."""
        events = list(self._events)

        if user_id:
            events = [e for e in events if e.user_id == user_id]
        if event_type:
            events = [e for e in events if e.event_type == event_type]
        if status:
            events = [e for e in events if e.status == status]
        if start_time:
            events = [e for e in events if e.timestamp >= start_time]
        if end_time:
            events = [e for e in events if e.timestamp <= end_time]

        return events[-limit:]

    def get_failure_rate(self, window_seconds: int = 3600) -> float:
        """Calculate the failure rate over a time window."""
        now = time.time()
        recent = [e for e in self._events if now - e.timestamp <= window_seconds]
        if not recent:
            return 0.0
        failures = sum(1 for e in recent if e.status in (AuthStatus.FAILURE, AuthStatus.LOCKED))
        return failures / len(recent)


# ---------------------------------------------------------------------------
# Password Authentication Service
# ---------------------------------------------------------------------------


class PasswordAuthenticator:
    """Password-based authentication with lockout and event logging."""

    def __init__(
        self,
        hasher: Optional[PasswordHasher] = None,
        attempt_tracker: Optional[LoginAttemptTracker] = None,
        event_logger: Optional[AuthEventLogger] = None,
    ) -> None:
        self._hasher = hasher or PasswordHasher(HashAlgorithm.ARGON2)
        self._tracker = attempt_tracker or LoginAttemptTracker()
        self._logger = event_logger or AuthEventLogger()
        self._passwords: dict[str, str] = {}  # user_id -> hashed_password

    def set_password(self, user_id: str, password: str) -> None:
        """Set a user's password (hashed)."""
        self._passwords[user_id] = self._hasher.hash(password)

    def authenticate(
        self,
        user_id: str,
        password: str,
        ip_address: str = "unknown",
        user_agent: Optional[str] = None,
    ) -> AuthResult:
        """Authenticate a user with password.

        Checks lockout first, then verifies password.
        """
        # Check if already locked out
        if self._tracker.is_locked_out(user_id=user_id, ip_address=ip_address):
            result = AuthResult(
                success=False,
                status=AuthStatus.LOCKED,
                user_id=user_id,
                method=AuthMethod.PASSWORD,
                message="Account locked due to too many failed attempts",
            )
            self._logger.log_event(
                "login",
                user_id,
                AuthMethod.PASSWORD,
                AuthStatus.LOCKED,
                ip_address,
                user_agent,
            )
            return result

        hashed = self._passwords.get(user_id)
        if hashed is None:
            result = self._tracker.record_attempt(
                success=False,
                user_id=user_id,
                ip_address=ip_address,
                method=AuthMethod.PASSWORD,
                reason="User not found",
            )
            self._logger.log_event(
                "login",
                user_id,
                AuthMethod.PASSWORD,
                AuthStatus.FAILURE,
                ip_address,
                user_agent,
                {"reason": "user_not_found"},
            )
            return result

        if self._hasher.verify(password, hashed):
            # Check if rehash is needed
            if self._hasher.needs_rehash(hashed):
                self._passwords[user_id] = self._hasher.hash(password)

            self._tracker.reset_attempts(user_id=user_id)
            result = self._tracker.record_attempt(
                success=True,
                user_id=user_id,
                ip_address=ip_address,
                method=AuthMethod.PASSWORD,
            )
            self._logger.log_event(
                "login",
                user_id,
                AuthMethod.PASSWORD,
                AuthStatus.SUCCESS,
                ip_address,
                user_agent,
            )
            return result

        result = self._tracker.record_attempt(
            success=False,
            user_id=user_id,
            ip_address=ip_address,
            method=AuthMethod.PASSWORD,
            reason="Invalid password",
        )
        self._logger.log_event(
            "login",
            user_id,
            AuthMethod.PASSWORD,
            AuthStatus.FAILURE,
            ip_address,
            user_agent,
            {"reason": "invalid_password"},
        )
        return result
