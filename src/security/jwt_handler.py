"""
JWT Token Handler for LiuHao AI OS

Provides:
- JWT issuance and validation
- Access/refresh token patterns
- Token revocation (blocklist)
- Custom claims and scopes
- JWKS support for key rotation
"""

import time
import secrets
import json
import uuid
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Set, Union
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path

try:
    import jwt
    from jwt import PyJWK, PyJWKSet
    from jwt.algorithms import RSAAlgorithm
    JWT_AVAILABLE = True
except ImportError:
    JWT_AVAILABLE = False
    jwt = None
    PyJWK = None
    PyJWKSet = None
    RSAAlgorithm = None

from .encryption import EncryptionManager, get_encryption_manager


class TokenType(Enum):
    """Token type enumeration"""
    ACCESS = "access"
    REFRESH = "refresh"
    API_KEY = "api_key"
    INVITATION = "invitation"
    RESET_PASSWORD = "reset_password"
    EMAIL_VERIFICATION = "email_verification"
    MFA = "mfa"


@dataclass
class TokenPayload:
    """JWT Token payload structure"""
    # Standard claims
    sub: str                                    # Subject (user ID)
    iss: str = "liuhao-ai-os"                   # Issuer
    aud: Union[str, List[str]] = "liuhao-api"   # Audience
    exp: int = 0                                # Expiration timestamp
    iat: int = field(default_factory=lambda: int(time.time()))  # Issued at
    nbf: int = 0                                # Not before
    jti: str = field(default_factory=lambda: str(uuid.uuid4())) # JWT ID
    
    # Custom claims
    token_type: TokenType = TokenType.ACCESS
    scopes: List[str] = field(default_factory=list)
    roles: List[str] = field(default_factory=list)
    permissions: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    # Device/session info
    device_id: Optional[str] = None
    session_id: Optional[str] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JWT encoding"""
        data = {
            "sub": self.sub,
            "iss": self.iss,
            "aud": self.aud,
            "exp": self.exp,
            "iat": self.iat,
            "nbf": self.nbf,
            "jti": self.jti,
            "token_type": self.token_type.value,
            "scopes": self.scopes,
            "roles": self.roles,
            "permissions": self.permissions,
            "metadata": self.metadata,
        }
        
        if self.device_id:
            data["device_id"] = self.device_id
        if self.session_id:
            data["session_id"] = self.session_id
        if self.ip_address:
            data["ip_address"] = self.ip_address
        if self.user_agent:
            data["user_agent"] = self.user_agent
            
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TokenPayload":
        """Create from decoded JWT payload"""
        return cls(
            sub=data.get("sub", ""),
            iss=data.get("iss", "liuhao-ai-os"),
            aud=data.get("aud", "liuhao-api"),
            exp=data.get("exp", 0),
            iat=data.get("iat", int(time.time())),
            nbf=data.get("nbf", 0),
            jti=data.get("jti", str(uuid.uuid4())),
            token_type=TokenType(data.get("token_type", "access")),
            scopes=data.get("scopes", []),
            roles=data.get("roles", []),
            permissions=data.get("permissions", []),
            metadata=data.get("metadata", {}),
            device_id=data.get("device_id"),
            session_id=data.get("session_id"),
            ip_address=data.get("ip_address"),
            user_agent=data.get("user_agent"),
        )

    def is_expired(self, leeway: int = 0) -> bool:
        """Check if token is expired"""
        return time.time() + leeway >= self.exp

    def is_valid_now(self, leeway: int = 0) -> bool:
        """Check if token is valid at current time"""
        now = time.time() + leeway
        return self.nbf <= now < self.exp


class JWTHandler:
    """
    JWT Token handler with support for:
    - RS256/RS512 asymmetric signing (recommended for production)
    - HS256 symmetric signing (simpler, for development)
    - Token revocation via blocklist
    - Key rotation via JWKS
    - Refresh token rotation
    """

    DEFAULT_ALGORITHM = "RS256"
    DEFAULT_ACCESS_TTL = 900      # 15 minutes
    DEFAULT_REFRESH_TTL = 604800  # 7 days
    DEFAULT_ISSUER = "liuhao-ai-os"
    DEFAULT_AUDIENCE = "liuhao-api"

    def __init__(
        self,
        algorithm: str = DEFAULT_ALGORITHM,
        private_key: Optional[Union[str, bytes]] = None,
        public_key: Optional[Union[str, bytes]] = None,
        secret_key: Optional[Union[str, bytes]] = None,
        issuer: str = DEFAULT_ISSUER,
        audience: Union[str, List[str]] = DEFAULT_AUDIENCE,
        access_ttl: int = DEFAULT_ACCESS_TTL,
        refresh_ttl: int = DEFAULT_REFRESH_TTL,
        leeway: int = 30,
        encryption: Optional[EncryptionManager] = None,
        key_file: Optional[str] = None,
    ):
        """
        Initialize JWT handler.
        
        Args:
            algorithm: Signing algorithm (RS256, RS512, HS256, HS512)
            private_key: Private key for signing (PEM format)
            public_key: Public key for verification (PEM format)
            secret_key: Secret key for HS256
            issuer: Token issuer
            audience: Token audience
            access_ttl: Access token TTL in seconds
            refresh_ttl: Refresh token TTL in seconds
            leeway: Clock skew leeway in seconds
            encryption: EncryptionManager for key storage
            key_file: Path to key file (for auto-loading)
        """
        if not JWT_AVAILABLE:
            raise RuntimeError(
                "PyJWT library required. Install: pip install pyjwt[crypto]"
            )

        self.algorithm = algorithm
        self.issuer = issuer
        self.audience = audience
        self.access_ttl = access_ttl
        self.refresh_ttl = refresh_ttl
        self.leeway = leeway
        self.encryption = encryption or get_encryption_manager()
        
        # Token revocation blocklist (in-memory, use Redis for distributed)
        self._revoked_tokens: Set[str] = set()
        self._revoked_refresh_tokens: Set[str] = set()

        # Load or generate keys
        self._load_keys(private_key, public_key, secret_key, key_file)

    def _load_keys(
        self,
        private_key: Optional[Union[str, bytes]],
        public_key: Optional[Union[str, bytes]],
        secret_key: Optional[Union[str, bytes]],
        key_file: Optional[str],
    ) -> None:
        """Load or generate signing keys"""
        is_rsa = self.algorithm.startswith("RS")
        is_hs = self.algorithm.startswith("HS")

        if key_file:
            # Load from file
            self._load_keys_from_file(key_file)
        elif private_key and public_key:
            # Use provided keys
            self._private_key = private_key if isinstance(private_key, bytes) else private_key.encode()
            self._public_key = public_key if isinstance(public_key, bytes) else public_key.encode()
        elif secret_key and is_hs:
            # Use symmetric key
            self._secret_key = secret_key if isinstance(secret_key, bytes) else secret_key.encode()
            self._private_key = self._secret_key
            self._public_key = self._secret_key
        elif is_rsa:
            # Generate RSA key pair
            self._generate_rsa_keys()
        else:
            # Generate HMAC secret
            self._secret_key = secrets.token_bytes(32)
            self._private_key = self._secret_key
            self._public_key = self._secret_key

    def _load_keys_from_file(self, path: str) -> None:
        """Load keys from PEM files"""
        path = Path(path)
        if path.suffix == ".pem":
            # Single file with both keys
            content = path.read_text()
            if "PRIVATE KEY" in content:
                self._private_key = content.encode()
            if "PUBLIC KEY" in content:
                self._public_key = content.encode()
        else:
            # Directory with separate files
            private_path = path / "private.pem"
            public_path = path / "public.pem"
            if private_path.exists():
                self._private_key = private_path.read_bytes()
            if public_path.exists():
                self._public_key = public_path.read_bytes()

    def _generate_rsa_keys(self, key_size: int = 2048) -> None:
        """Generate RSA key pair"""
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.primitives import serialization

        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=key_size,
        )
        
        self._private_key = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        
        self._public_key = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )

    def save_keys(self, path: str) -> None:
        """Save keys to files"""
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        
        if self._private_key:
            (path / "private.pem").write_bytes(self._private_key)
        if self._public_key:
            (path / "public.pem").write_bytes(self._public_key)

    def get_jwks(self) -> Dict[str, Any]:
        """Get JSON Web Key Set for public key distribution"""
        if self.algorithm.startswith("RS"):
            from jwt import PyJWK
            jwk = PyJWK.from_pem(self._public_key)
            return {"keys": [jwk.to_dict()]}
        else:
            # For HS256, return key ID only (symmetric key not exposed)
            return {"keys": [{"kty": "oct", "kid": "default", "alg": self.algorithm}]}

    def _get_signing_key(self) -> Union[str, bytes]:
        """Get key for signing"""
        if self.algorithm.startswith("RS"):
            return self._private_key
        return self._secret_key

    def _get_verification_key(self) -> Union[str, bytes]:
        """Get key for verification"""
        if self.algorithm.startswith("RS"):
            return self._public_key
        return self._secret_key

    def create_token(
        self,
        subject: str,
        token_type: TokenType = TokenType.ACCESS,
        scopes: Optional[List[str]] = None,
        roles: Optional[List[str]] = None,
        permissions: Optional[List[str]] = None,
        ttl: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
        device_id: Optional[str] = None,
        session_id: Optional[str] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> tuple[str, TokenPayload]:
        """
        Create a JWT token.
        
        Returns:
            Tuple of (encoded JWT string, TokenPayload)
        """
        now = int(time.time())
        
        if ttl is None:
            ttl = self.access_ttl if token_type == TokenType.ACCESS else self.refresh_ttl
            
        payload = TokenPayload(
            sub=subject,
            iss=self.issuer,
            aud=self.audience,
            exp=now + ttl,
            iat=now,
            nbf=now,
            jti=str(uuid.uuid4()),
            token_type=token_type,
            scopes=scopes or [],
            roles=roles or [],
            permissions=permissions or [],
            metadata=metadata or {},
            device_id=device_id,
            session_id=session_id,
            ip_address=ip_address,
            user_agent=user_agent,
        )

        # Encode token
        token = jwt.encode(
            payload.to_dict(),
            self._get_signing_key(),
            algorithm=self.algorithm,
        )

        # PyJWT >= 2.0 returns string
        if isinstance(token, bytes):
            token = token.decode()

        return token, payload

    def create_token_pair(
        self,
        subject: str,
        scopes: Optional[List[str]] = None,
        roles: Optional[List[str]] = None,
        permissions: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        device_id: Optional[str] = None,
        session_id: Optional[str] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create access + refresh token pair.
        
        Returns:
            Dict with access_token, refresh_token, expires_in, token_type
        """
        access_token, access_payload = self.create_token(
            subject=subject,
            token_type=TokenType.ACCESS,
            scopes=scopes,
            roles=roles,
            permissions=permissions,
            ttl=self.access_ttl,
            metadata=metadata,
            device_id=device_id,
            session_id=session_id,
            ip_address=ip_address,
            user_agent=user_agent,
        )

        refresh_token, refresh_payload = self.create_token(
            subject=subject,
            token_type=TokenType.REFRESH,
            scopes=scopes,
            ttl=self.refresh_ttl,
            metadata={"access_jti": access_payload.jti},
            device_id=device_id,
            session_id=session_id,
            ip_address=ip_address,
            user_agent=user_agent,
        )

        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "Bearer",
            "expires_in": self.access_ttl,
            "refresh_expires_in": self.refresh_ttl,
            "scope": " ".join(scopes) if scopes else "",
        }

    def validate_token(
        self,
        token: str,
        expected_type: Optional[TokenType] = None,
        verify_exp: bool = True,
        verify_aud: bool = True,
    ) -> TokenPayload:
        """
        Validate and decode a JWT token.
        
        Raises:
            jwt.ExpiredSignatureError: Token expired
            jwt.InvalidTokenError: Token invalid
            jwt.InvalidAudienceError: Audience mismatch
        """
        # Check revocation
        try:
            unverified = jwt.decode(
                token,
                options={"verify_signature": False},
            )
            jti = unverified.get("jti")
            if jti and jti in self._revoked_tokens:
                raise jwt.InvalidTokenError("Token has been revoked")
        except jwt.InvalidTokenError:
            raise  # Re-raise revocation check result
        except Exception:
            pass  # Will be caught by full validation

        # Full validation
        payload_data = jwt.decode(
            token,
            self._get_verification_key(),
            algorithms=[self.algorithm],
            issuer=self.issuer,
            audience=self.audience if verify_aud else None,
            options={
                "verify_exp": verify_exp,
                "verify_iss": True,
                "verify_aud": verify_aud,
                "require_exp": True,
                "require_iat": True,
                "require_nbf": False,
            },
            leeway=self.leeway,
        )

        payload = TokenPayload.from_dict(payload_data)

        # Check token type
        if expected_type and payload.token_type != expected_type:
            raise jwt.InvalidTokenError(f"Expected token type {expected_type.value}, got {payload.token_type.value}")

        return payload

    def validate_access_token(self, token: str) -> TokenPayload:
        """Validate access token"""
        return self.validate_token(token, expected_type=TokenType.ACCESS)

    def validate_refresh_token(self, token: str) -> TokenPayload:
        """Validate refresh token"""
        payload = self.validate_token(token, expected_type=TokenType.REFRESH)
        
        # Check refresh token revocation
        if payload.jti in self._revoked_refresh_tokens:
            raise jwt.InvalidTokenError("Refresh token has been revoked")
            
        return payload

    def refresh_access_token(
        self,
        refresh_token: str,
        rotate: bool = True,
    ) -> Dict[str, Any]:
        """
        Use refresh token to get new access token.
        
        Args:
            refresh_token: Refresh token string
            rotate: If True, issue new refresh token and revoke old one
            
        Returns:
            New token pair dict
        """
        # Validate refresh token
        payload = self.validate_refresh_token(refresh_token)

        # Revoke old refresh token if rotating
        if rotate:
            self.revoke_refresh_token(payload.jti)

        # Create new token pair
        return self.create_token_pair(
            subject=payload.sub,
            scopes=payload.scopes,
            roles=payload.roles,
            permissions=payload.permissions,
            metadata=payload.metadata,
            device_id=payload.device_id,
            session_id=payload.session_id,
            ip_address=payload.ip_address,
            user_agent=payload.user_agent,
        )

    def revoke_token(self, token: str) -> bool:
        """Revoke a token by adding to blocklist"""
        try:
            unverified = jwt.decode(
                token,
                options={"verify_signature": False},
            )
            jti = unverified.get("jti")
            if jti:
                self._revoked_tokens.add(jti)
                return True
        except Exception:
            pass
        return False

    def revoke_token_by_jti(self, jti: str) -> None:
        """Revoke token by JTI"""
        self._revoked_tokens.add(jti)

    def revoke_refresh_token(self, jti: str) -> None:
        """Revoke refresh token by JTI"""
        self._revoked_refresh_tokens.add(jti)

    def revoke_all_user_tokens(self, subject: str) -> int:
        """Revoke all tokens for a user (requires token introspection storage)"""
        # This would require a token store - placeholder for now
        return 0

    def introspect_token(self, token: str) -> Dict[str, Any]:
        """Introspect token (RFC 7662)"""
        try:
            payload = self.validate_token(token, verify_exp=False)
            return {
                "active": True,
                "scope": " ".join(payload.scopes),
                "client_id": payload.metadata.get("client_id"),
                "username": payload.sub,
                "token_type": payload.token_type.value,
                "exp": payload.exp,
                "iat": payload.iat,
                "nbf": payload.nbf,
                "sub": payload.sub,
                "aud": payload.aud,
                "iss": payload.iss,
                "jti": payload.jti,
            }
        except Exception:
            return {"active": False}

    def get_token_info(self, token: str) -> Optional[Dict[str, Any]]:
        """Get token info without full validation (for debugging)"""
        try:
            return jwt.decode(
                token,
                options={"verify_signature": False},
            )
        except Exception:
            return None


# Convenience functions
_default_handler: Optional[JWTHandler] = None


def get_jwt_handler() -> JWTHandler:
    """Get default JWT handler"""
    global _default_handler
    if _default_handler is None:
        _default_handler = JWTHandler()
    return _default_handler


def create_access_token(
    subject: str,
    scopes: Optional[List[str]] = None,
    **kwargs
) -> tuple[str, TokenPayload]:
    """Create access token"""
    return get_jwt_handler().create_token(
        subject=subject,
        token_type=TokenType.ACCESS,
        scopes=scopes,
        **kwargs
    )


def create_refresh_token(
    subject: str,
    scopes: Optional[List[str]] = None,
    **kwargs
) -> tuple[str, TokenPayload]:
    """Create refresh token"""
    return get_jwt_handler().create_token(
        subject=subject,
        token_type=TokenType.REFRESH,
        scopes=scopes,
        **kwargs
    )


def validate_token(token: str, **kwargs) -> TokenPayload:
    """Validate token"""
    return get_jwt_handler().validate_token(token, **kwargs)