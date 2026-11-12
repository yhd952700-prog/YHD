"""
JWT Token Handler for LiuHao AI OS

Provides:
- JWT issuance and validation
- Access/refresh token patterns
- Token revocation (blocklist)
- Custom claims and scopes
- JWKS support for key rotation
"""

import os
import sqlite3
import threading
import secrets
import logging
import uuid
from typing import Optional, Dict, Any, List, Set, Union
from dataclasses import dataclass, field
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

from .encryption import (
    EncryptionManager,
    MachineBoundKeyError,
    get_encryption_manager,
    is_machine_bound_payload,
    unwrap_machine_bound,
    wrap_machine_bound,
)
from .secret_store import SecretBackendUnavailable
from .posture import is_production  # unified posture source of truth
from .._time import stable_now_epoch

# Defined after the guarded import above on purpose: pycodestyle tolerates a
# module-level import placed after a try/except import guard, but any *other*
# statement before it turns that import into an E402.
logger = logging.getLogger(__name__)

# Hard ceilings on token lifetimes. Callers may request a shorter TTL, but a
# token can never outlive these values -- otherwise a bad call site could mint
# an access token with `exp=9999999999` that survives every password reset.
MAX_ACCESS_TTL_SECONDS = 7 * 24 * 60 * 60
MAX_REFRESH_TTL_SECONDS = 30 * 24 * 60 * 60


def _record_revocation_check_failure() -> None:
    """Expose revocation-store failures as a metric without importing eagerly."""
    try:
        from ..observability.metrics import provider_errors_total

        provider_errors_total.labels(
            provider="security",
            model="jwt",
            error_type="revocation_check_failed",
        ).inc()
    except Exception:  # pragma: no cover - metrics must never break auth
        pass


# ``jwt`` is None when PyJWT is not installed (see the guarded import
# above): the class definition itself must not dereference it, or the
# whole module fails to import and every conftest fixture that touches
# ``src.security`` errors out. With PyJWT present the base is exactly
# ``jwt.InvalidTokenError``, as the docstring requires; without it the
# exception degrades to a plain Exception (no token can be verified in
# that environment anyway).
class IdentityStoreUnavailable(
    jwt.InvalidTokenError if jwt is not None else Exception
):
    """撤销库/身份库读不出来 —— **基础设施故障，不是令牌无效**。

    仍然继承 ``jwt.InvalidTokenError``：验证函数一旦读不出吊销状态就必须
    **拒绝**，而调用方普遍按 ``jwt.InvalidTokenError`` 这一族捕获"令牌不可用"。
    若这里不属该族，异常会从那些 ``except`` 旁边逃逸出去 —— 轻则 500，
    重则被某个"未捕获即放行"的分支当成没事，这正是 fail-closed 名不副实的
    来源。

    但它保持**独立类型**：本进程的网关层（``policy.require_bearer_payload``、
    ``rate_limiter`` 中间件）先按本类型捕获，映射成 503 + 中文可操作文案，
    而不是 401"密码/令牌无效"—— 那会把数据库故障误导成用户凭据问题。
    捕获顺序必须是"先本类型、后 ``InvalidTokenError``"。
    """


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
    iat: int = field(default_factory=lambda: int(stable_now_epoch()))  # Issued at
    nbf: int = 0                                # Not before
    jti: str = field(default_factory=lambda: str(uuid.uuid4()))  # JWT ID

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
            iat=data.get("iat", int(stable_now_epoch())),
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
        """Check if token is expired（用抗跳变的时钟，见 ``stable_now_epoch``）"""
        return stable_now_epoch() + leeway >= self.exp

    def is_valid_now(self, leeway: int = 0) -> bool:
        """Check if token is valid at current time"""
        now = stable_now_epoch() + leeway
        return self.nbf <= now < self.exp


#: Durable revocation blocklist location. Env-overridable so tests/containers
#: never touch the repo-root default.
JWT_REVOCATION_DB_ENV = "LIUHAO_JWT_REVOCATION_DB"


def _default_revocation_path() -> str:
    from src.common.data_paths import resolve_store_path

    repo_root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    return resolve_store_path(
        "liuhao_jwt_revocation.db",
        os.path.join(repo_root, ".liuhao_jwt_revocation.db"),
        workspace=True,
    )


class RevocationStore:
    """SQLite-backed JWT revocation blocklist with TTL cleanup.

    The previous blocklist was a ``set`` on the handler instance: a restart
    resurrected every revoked token, and a second worker (``server.workers`` is
    >1 in production) never saw the first worker's revocations. Both are
    security holes, not cache quirks. SQLite is stdlib-only and already the
    deployment's durable local store, so it is the natural home.

    Rows carry ``expires_at``: once the token would have expired anyway,
    validating it fails on ``exp`` and the row is dead weight, so expired rows
    are pruned on every write.
    """

    _SCHEMA = """
        CREATE TABLE IF NOT EXISTS revoked_tokens (
            jti TEXT NOT NULL,
            kind TEXT NOT NULL,
            subject TEXT,
            expires_at REAL NOT NULL,
            PRIMARY KEY (jti, kind)
        );
        CREATE INDEX IF NOT EXISTS idx_revoked_expires
            ON revoked_tokens(expires_at);
    """

    def __init__(self, path: Optional[str] = None):
        self.path = path or os.environ.get(JWT_REVOCATION_DB_ENV) or (
            _default_revocation_path()
        )
        self._lock = threading.Lock()
        #: 每条工作线程一条**长期只读连接**（``threading.local``）。复用连接
        #: 句柄能省掉每次请求的 open/close，但**绝不复用查询结果**：``contains``
        #: 每次都重新 SELECT，别的连接刚提交的吊销立即可见 —— 吊销生效延迟 = 0。
        #: **不得**给这里加 TTL / 缓存 / 快照，那会让已吊销的令牌继续通过。
        self._read_local = threading.local()
        #: 线程 ident -> 连接，供超时路径从**另一个线程** ``interrupt()``。
        self._read_registry: Dict[int, sqlite3.Connection] = {}
        self._registry_lock = threading.Lock()
        directory = os.path.dirname(os.path.abspath(self.path))
        if directory:
            os.makedirs(directory, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(self._SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=5.0)

    # ------------------------------------------------------------------
    # per-thread 长期只读连接（④）
    # ------------------------------------------------------------------
    def _read_conn(self) -> sqlite3.Connection:
        """返回**本线程**的只读连接；没有就建一条并登记。

        ``check_same_thread=False`` 只为了允许超时路径从另一个线程
        ``interrupt()/close()``；正常读写仍由创建它的线程独占使用。
        """
        conn = getattr(self._read_local, "conn", None)
        if conn is not None:
            return conn
        conn = sqlite3.connect(self.path, timeout=5.0, check_same_thread=False)
        self._read_local.conn = conn
        with self._registry_lock:
            self._read_registry[threading.get_ident()] = conn
        return conn

    def _drop_read_conn(self) -> None:
        """丢弃本线程的只读连接（出错或已被别的线程掐断时）。"""
        conn = getattr(self._read_local, "conn", None)
        self._read_local.conn = None
        if conn is None:
            return
        with self._registry_lock:
            self._read_registry.pop(threading.get_ident(), None)
        try:
            conn.close()
        except Exception:  # pragma: no cover - 已被关闭/被中断
            pass

    def interrupt_and_close(self, thread_ident: Optional[int]) -> bool:
        """从**另一个线程**掐断某条 lane 上正在执行的语句（④ 超时处置）。

        ``interrupt()`` 是 SQLite 官方支持的跨线程调用；``close()`` **不能**在这里
        调用：实测当目标连接正卡在 busy-wait 的语句上时，``close()`` 会一直等到
        该语句结束（持 EXCLUSIVE 锁时 6.3s），而本函数是在**事件循环线程**上执行
        的 —— 那等于把 D-1 的冻结搬到超时处置路径，2 秒预算变成 7 秒。
        这里只做 ``interrupt()`` + 从注册表摘掉；连接由那条 lane 线程在拿到
        ``sqlite3.Error`` 后自己 ``_drop_read_conn()``（它阻塞在自己的线程里）。
        返回是否拿到了连接句柄 —— 拿不到说明卡在 ``connect()`` 阶段（那里还没有
        conn 对象），只能弃置该 lane。
        """
        if thread_ident is None:
            return False
        with self._registry_lock:
            conn = self._read_registry.pop(int(thread_ident), None)
        if conn is None:
            return False
        try:
            conn.interrupt()
        except Exception:  # pragma: no cover - 尽力而为
            pass
        return True

    def probe(self, timeout: float = 1.0) -> bool:
        """降级自愈探针：用一条**临时**连接在 ``timeout`` 内读一行。

        只用于"撤销库恢复了吗"的判定，不参与请求路径。``SELECT 1 ... LIMIT 1``
        会真的去读表，因此能反映文件/表锁是否已经松开。
        """
        try:
            conn = sqlite3.connect(self.path, timeout=timeout)
        except Exception:  # pragma: no cover - 连不上就是没恢复
            return False
        try:
            conn.execute("SELECT 1 FROM revoked_tokens LIMIT 1").fetchone()
            return True
        except Exception:
            return False
        finally:
            try:
                conn.close()
            except Exception:  # pragma: no cover
                pass

    def add(
        self,
        jti: str,
        kind: str = "access",
        expires_at: Optional[float] = None,
        subject: Optional[str] = None,
    ) -> None:
        # The row must outlive the token, never the reverse: a missing/NaN
        # expiry would prune instantly and silently un-revoke the token.
        expiry = float(expires_at) if expires_at else stable_now_epoch() + 3600.0
        with self._lock, self._connect() as conn:
            conn.execute(
                "DELETE FROM revoked_tokens WHERE expires_at <= ?",
                (stable_now_epoch(),),
            )
            conn.execute(
                "INSERT OR REPLACE INTO revoked_tokens (jti, kind, subject, expires_at) "
                "VALUES (?, ?, ?, ?)",
                (str(jti), kind, subject, expiry),
            )

    def contains(self, jti: str, kind: str = "access") -> bool:
        if not jti:
            return False
        try:
            conn = self._read_conn()
            row = conn.execute(
                "SELECT 1 FROM revoked_tokens WHERE jti = ? AND kind = ? "
                "AND expires_at > ?",
                (str(jti), kind, stable_now_epoch()),
            ).fetchone()
        except sqlite3.Error as exc:
            # 读不出来 ≠ 没吊销：fail-closed，并把"基础设施故障"如实上报，
            # 由上层映射成 503（不是 401）。
            self._drop_read_conn()
            raise IdentityStoreUnavailable(
                f"revocation store read failed: {type(exc).__name__}"
            ) from exc
        return row is not None

    def purge(self) -> int:
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM revoked_tokens WHERE expires_at <= ?",
                (stable_now_epoch(),),
            )
            return int(cursor.rowcount or 0)

    def count(self, kind: Optional[str] = None) -> int:
        query = "SELECT COUNT(*) FROM revoked_tokens"
        params: tuple = ()
        if kind:
            query += " WHERE kind = ?"
            params = (kind,)
        with self._connect() as conn:
            row = conn.execute(query, params).fetchone()
        return int(row[0] if row else 0)


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

        # Token revocation blocklist: durable SQLite store (survives restart and
        # is shared across worker processes). In-memory sets used to lose
        # revocations on restart and leak them across workers.
        self._revocations = RevocationStore()

        # Subject -> set of JTIs minted for that subject. Maintained in
        # create_token so revoke_all_user_tokens can actually revoke a user's
        # tokens instead of silently doing nothing. In-memory only (see the
        # module note on per-process signing keys).
        self._subject_tokens: Dict[str, Set[str]] = {}

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
            content = path.read_text(encoding="utf-8")
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
        now = int(stable_now_epoch())

        if ttl is None:
            ttl = self.access_ttl if token_type == TokenType.ACCESS else self.refresh_ttl

        max_ttl = (
            MAX_ACCESS_TTL_SECONDS
            if token_type == TokenType.ACCESS
            else MAX_REFRESH_TTL_SECONDS
        )
        try:
            ttl = int(ttl)
        except (TypeError, ValueError):
            ttl = max_ttl
        if ttl <= 0:
            ttl = 1
        if ttl > max_ttl:
            logger.warning(
                "clamping %s token ttl for subject=%s from %ss to %ss",
                token_type.value, subject, ttl, max_ttl,
            )
            ttl = max_ttl

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

        # Track this JTI under its subject so revoke_all_user_tokens can find
        # it later. Append-only: individual revocations do not remove the JTI
        # here (harmless -- revoke_all re-adds to the blacklist idempotently).
        self._subject_tokens.setdefault(subject, set()).add(payload.jti)

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
        # Check revocation first. If the blocklist cannot be read we must fail
        # CLOSED: treating "unknown" as "not revoked" would let a revoked token
        # through whenever the revocation store is unavailable.
        try:
            unverified = jwt.decode(
                token,
                options={"verify_signature": False},
            )
            jti = unverified.get("jti")
            if jti and self._revocations.contains(jti, "access"):
                raise jwt.InvalidTokenError("Token has been revoked")
        except IdentityStoreUnavailable:
            # 基础设施故障必须原样上抛，让上层回 503；绝不能在这里改口说
            # "令牌无效"（那会引导用户去改密码，而问题在本机数据库）。
            # 必须排在 ``except jwt.InvalidTokenError`` **之前**：本类现在
            # 属于该族（保证按"令牌不可用"捕获的调用方也会拒绝），但这里
            # 要保留独立语义与失败指标。
            _record_revocation_check_failure()
            raise
        except jwt.InvalidTokenError:
            raise  # Re-raise revocation check result
        except Exception as exc:
            logger.error(
                "token revocation check failed; rejecting token (fail-closed): %s",
                exc,
                exc_info=True,
            )
            _record_revocation_check_failure()
            raise IdentityStoreUnavailable(
                "Token revocation status unavailable"
            ) from exc

        # Full validation。过期判定**不交给 PyJWT**：它内部直接读系统墙钟，
        # 用户调时间或从休眠恢复时会把一批有效令牌判成过期、全员踢下线。
        # 这里改成先验签名/iss/aud，再用抗跳变的 ``stable_now_epoch`` 比对 exp。
        payload_data = jwt.decode(
            token,
            self._get_verification_key(),
            algorithms=[self.algorithm],
            issuer=self.issuer,
            audience=self.audience if verify_aud else None,
            options={
                "verify_exp": False,
                "verify_iat": False,
                # nbf 也不交给 PyJWT：它用**系统墙钟**判断"尚未生效"。用户把
                # 时间往回拨超过 leeway 时，所有已签发令牌的 nbf 都会落到
                # "未来"，已登录会话立刻 401、新登录令牌永远进不来。
                "verify_nbf": False,
                "verify_iss": True,
                "verify_aud": verify_aud,
                "require_exp": True,
                "require_iat": True,
                "require_nbf": False,
            },
            leeway=self.leeway,
        )

        payload = TokenPayload.from_dict(payload_data)
        if verify_exp and payload.exp and stable_now_epoch() >= payload.exp + self.leeway:
            raise jwt.ExpiredSignatureError("Signature has expired")

        # nbf 用与 exp 同一套口径：抗跳变时钟 + leeway 的时钟偏差容忍。
        # 语义：``nbf`` 最多可以比当前稳定时间"快" ``leeway`` 秒；超过才算未来
        # 令牌。这样时间回拨不会把已登录用户锁在门外，也不会放宽真正的伪造。
        if payload.nbf and stable_now_epoch() + self.leeway < payload.nbf:
            raise jwt.InvalidTokenError(
                "Token is not yet valid (nbf is in the future)"
            )

        # Check token type
        if expected_type and payload.token_type != expected_type:
            raise jwt.InvalidTokenError(f"Expected token type {expected_type.value}, got {payload.token_type.value}")

        return payload

    def validate_access_token(self, token: str) -> TokenPayload:
        """Validate access token"""
        return self.validate_token(token, expected_type=TokenType.ACCESS)

    def decode_signature_only(
        self,
        token: str,
        verify_exp: bool = False,
    ) -> TokenPayload:
        """Verify the signature and standard claims but optionally ignore expiry.

        Used by ``/auth/logout``: revoking an already-expired token is
        harmless and idempotent, but we still refuse forged tokens. No
        revocation lookup is performed here.
        """
        payload_data = jwt.decode(
            token,
            self._get_verification_key(),
            algorithms=[self.algorithm],
            issuer=self.issuer,
            audience=self.audience,
            options={
                "verify_exp": False,
                "verify_iat": False,
                "verify_nbf": False,
                "verify_iss": True,
                "verify_aud": True,
                "require_exp": True,
                "require_iat": True,
                "require_nbf": False,
            },
            leeway=self.leeway,
        )
        payload = TokenPayload.from_dict(payload_data)
        if verify_exp and payload.exp and stable_now_epoch() >= payload.exp + self.leeway:
            raise jwt.ExpiredSignatureError("Signature has expired")
        if payload.nbf and stable_now_epoch() + self.leeway < payload.nbf:
            raise jwt.InvalidTokenError(
                "Token is not yet valid (nbf is in the future)"
            )
        return payload

    def validate_refresh_token(self, token: str) -> TokenPayload:
        """Validate refresh token"""
        payload = self.validate_token(token, expected_type=TokenType.REFRESH)

        # Check refresh token revocation
        if self._revocations.contains(payload.jti, "refresh"):
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
        """Revoke a token by recording its JTI in the durable blocklist."""
        try:
            unverified = jwt.decode(
                token,
                options={"verify_signature": False},
            )
            jti = unverified.get("jti")
            if jti:
                token_type = str(unverified.get("token_type") or "access").lower()
                kind = "refresh" if token_type.endswith("refresh") else "access"
                self._revocations.add(
                    jti,
                    kind=kind,
                    expires_at=unverified.get("exp"),
                    subject=unverified.get("sub"),
                )
                return True
        except Exception:
            pass
        return False

    def _revocation_ttl(self) -> float:
        """Conservative lifetime for a JTI revoked without a known ``exp``."""
        return stable_now_epoch() + max(
            3600.0, float(self.access_ttl or 0), float(self.refresh_ttl or 0)
        )

    def revoke_token_by_jti(self, jti: str) -> None:
        """Revoke an access token by JTI."""
        self._revocations.add(jti, kind="access", expires_at=self._revocation_ttl())

    def revoke_refresh_token(self, jti: str) -> None:
        """Revoke a refresh token by JTI."""
        self._revocations.add(jti, kind="refresh", expires_at=self._revocation_ttl())

    def revoke_all_user_tokens(self, subject: str) -> int:
        """Revoke every token currently minted for ``subject``.

        Works off the in-memory subject->JTI index maintained in
        :meth:`create_token`. Returns the number of JTIs revoked. A ``0`` here
        means the subject had no tracked tokens -- it is an honest "nothing to
        revoke", NOT a silent success pretending tokens were killed.

        Scope/limitations (honest, not hidden):
          * Revocations are durable (SQLite) and therefore survive restarts and
            are visible to every worker process sharing the store.
          * Only tokens minted *after* this handler instance started tracking
            are covered.
        """
        jtis = self._subject_tokens.pop(subject, set())
        expiry = self._revocation_ttl()
        for jti in jtis:
            self._revocations.add(
                jti, kind="access", expires_at=expiry, subject=subject
            )
            self._revocations.add(
                jti, kind="refresh", expires_at=expiry, subject=subject
            )
        return len(jtis)

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

#: Environment variables that pin the signing key to something durable.
#:
#: Without these a ``JWTHandler`` generates its keys in ``__init__``, which
#: means the key lives exactly as long as the process. That is fine for a
#: single-process dev run, and quietly wrong for a deployment:
#:
#:   * a restart invalidates every token -- every signed-in user is logged out
#:     for no reason they can see;
#:   * a deployment that runs more than one worker/replica mints tokens in one
#:     process and validates them in another, so a fresh login fails on the
#:     very next request. Login "works" and the console is still unusable.
#:
#: A key supplied through the environment makes every process share the same
#: one, which is the whole requirement. ``LIUHAO_JWT_SECRET`` covers the
#: symmetric case (HS256/HS512); the PEM pair covers the asymmetric one
#: (RS256/RS512), where only the public key is ever needed to verify.
JWT_SECRET_ENV = "LIUHAO_JWT_SECRET"

#: Names older deployments already use for the same secret. ``docker-compose
#: .yml`` sets ``JWT_SECRET`` and ``docker-compose.prod.yml`` sets
#: ``JWT_SECRET_KEY``; until now the code read neither, so those declarations
#: were silently ignored and every process still minted its own key. Honouring
#: them lets an existing deployment stop being broken by editing nothing.
JWT_SECRET_ENV_ALIASES = ("JWT_SECRET_KEY", "JWT_SECRET")

JWT_PRIVATE_KEY_ENV = "LIUHAO_JWT_PRIVATE_KEY"
JWT_PUBLIC_KEY_ENV = "LIUHAO_JWT_PUBLIC_KEY"
JWT_ALGORITHM_ENV = "LIUHAO_JWT_ALGORITHM"

#: JWT 签名密钥的 DPAPI 熵；与模型/身份密钥不可互换。
JWT_SECRET_DPAPI_PURPOSE = b"jwt-signing-secret-v1"

#: 允许显式指定 app.env 路径（安装器/测试用）。未设置时按数据目录发现。
APP_ENV_FILE_ENV = "LIUHAO_APP_ENV_FILE"

#: Values that are plainly not secrets. A *known* signing key is worse than a
#: random one -- anyone who has read this repository could mint tokens -- so a
#: placeholder is treated as "not configured" rather than trusted. Compared
#: case-insensitively.
_PLACEHOLDER_SECRETS = frozenset({
    "replace-me", "change-me", "changeme", "your-secret-key",
    "secret", "***", "xxx", "todo", "password",
})


class _UnreadableJwtSecret(Exception):
    """app.env 里的密钥带机器绑定版本头，但本机解不开。

    不向外抛 ``pywintypes.error``，由 :func:`get_jwt_handler` 转成中文告警 +
    本次进程临时密钥（不覆盖磁盘上的旧密文）。
    """

    def __init__(self, name: str, message: str) -> None:
        self.name = name
        super().__init__(message)


def _app_env_candidates() -> List[Path]:
    """可能承载 ``LIUHAO_JWT_SECRET`` 的 app.env 路径（去重、保持优先级）。"""
    explicit = (os.environ.get(APP_ENV_FILE_ENV) or "").strip()
    if explicit:
        return [Path(explicit)]
    candidates: List[Path] = []
    data_dir = (os.environ.get("LIUHAO_DATA_DIR") or "").strip()
    if data_dir:
        candidates.append(Path(data_dir) / "config" / "app.env")
    local = (os.environ.get("LOCALAPPDATA") or "").strip()
    if local:
        candidates.append(Path(local) / "LiuHao" / "data" / "config" / "app.env")
    repo_root = Path(__file__).resolve().parents[2]
    candidates.append(repo_root / "config" / "app.env")
    seen: Set[str] = set()
    unique: List[Path] = []
    for item in candidates:
        key = str(item)
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def _migrate_app_env_secret(name: str, plain: str) -> bool:
    """把 app.env 里的明文 JWT 密钥原子替换成 DPAPI 包裹形态。

    只改**值恰好等于**当前环境变量值的那一行，避免误改运维手工注入的
    其它配置；文件不存在或没匹配时不写盘（容器/测试用环境变量直连）。
    失败只告警，本次仍用明文密钥正常启动。
    """
    if os.name != "nt":
        return False
    for path in _app_env_candidates():
        if not path.is_file():
            continue
        try:
            original = path.read_text(encoding="utf-8")
        except OSError:
            continue
        newline = "\r\n" if "\r\n" in original else "\n"
        prefix = f"{name}="
        lines = original.splitlines(keepends=True)
        target = None
        for index, line in enumerate(lines):
            body = line.rstrip("\r\n")
            if not body.startswith(prefix):
                continue
            value = body[len(prefix):].strip().strip('"').strip("'")
            if value == plain:
                target = index
                break
        if target is None:
            continue
        tmp = None
        try:
            wrapped = wrap_machine_bound(
                plain.encode("utf-8"), purpose=JWT_SECRET_DPAPI_PURPOSE
            ).decode("ascii")
            lines[target] = prefix + wrapped + newline
            tmp = path.with_name(
                f".{path.name}.migrate-{os.getpid()}-{secrets.token_hex(4)}.tmp"
            )
            tmp.write_text("".join(lines), encoding="utf-8", newline="")
            try:
                from ..common.windows_acl import tighten_current_user_only

                tighten_current_user_only(tmp)
            except Exception:  # noqa: BLE001 - ACL 加固失败不阻断迁移
                pass
            os.replace(tmp, path)
            tmp = None
            logger.info("JWT 签名密钥已迁移为 DPAPI 机器绑定格式：%s", path)
            return True
        except Exception as exc:  # noqa: BLE001 - 迁移失败不得阻断启动
            logger.warning(
                "JWT 签名密钥自动迁移失败（%s）：%s；本次继续使用明文密钥运行，"
                "原文件未被覆盖。下次启动会再次尝试。",
                path,
                exc,
            )
            return False
        finally:
            if tmp is not None:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
    return False


def _configured_secret() -> Optional[tuple]:
    """Return ``(name, value)`` for the first usable secret in the environment.

    Skips empty values and known placeholders (loudly -- a placeholder that is
    quietly ignored is how the deployment got here). Returns ``None`` when no
    variable supplies a usable secret.

    值可以带 ``LIUHAO-DPAPI-1:`` 版本头（第一启动时由本函数把明文迁移成
    这种形态）。带版本头但本机解不开时抛 :class:`_UnreadableJwtSecret`，
    调用方据此给中文提示，而不是把底层 traceback 甩给用户。
    """
    for name in (JWT_SECRET_ENV, *JWT_SECRET_ENV_ALIASES):
        raw = os.environ.get(name, "").strip()
        if not raw:
            continue
        if is_machine_bound_payload(raw):
            try:
                secret_bytes, _mode = unwrap_machine_bound(
                    raw, purpose=JWT_SECRET_DPAPI_PURPOSE
                )
            except MachineBoundKeyError as exc:
                raise _UnreadableJwtSecret(name, str(exc)) from None
            secret = secret_bytes.decode("utf-8", "ignore")
        else:
            secret = raw
            _migrate_app_env_secret(name, raw)
        if secret.lower() in _PLACEHOLDER_SECRETS:
            logger.warning(
                "%s=%r is a placeholder, not a secret; ignoring it. A known "
                "signing key would let anyone forge tokens.", name, secret,
            )
            continue
        return name, secret
    return None


def _handler_from_env() -> Optional[JWTHandler]:
    """Build a handler from a signing key pinned in the environment.

    Returns ``None`` when nothing usable is configured, so the caller can fall
    back to per-process keys exactly as before. Raises when the configuration
    is self-contradictory (a symmetric secret paired with an asymmetric
    algorithm): that is a misconfiguration which would otherwise be discovered
    only as "every login fails", so it fails loudly and early instead.
    """
    configured = _configured_secret()
    private_key = os.environ.get(JWT_PRIVATE_KEY_ENV, "").strip()
    public_key = os.environ.get(JWT_PUBLIC_KEY_ENV, "").strip()
    algorithm = os.environ.get(JWT_ALGORITHM_ENV, "").strip()

    if private_key and public_key:
        return JWTHandler(algorithm=algorithm or "RS256",
                          private_key=private_key, public_key=public_key)
    if private_key or public_key:
        raise RuntimeError(
            f"{JWT_PRIVATE_KEY_ENV} and {JWT_PUBLIC_KEY_ENV} must be set together"
        )
    if configured:
        name, secret = configured
        if algorithm.startswith("RS"):
            raise RuntimeError(
                f"{name} is set but {JWT_ALGORITHM_ENV}={algorithm} is "
                f"asymmetric; provide {JWT_PRIVATE_KEY_ENV}/{JWT_PUBLIC_KEY_ENV} "
                "instead of a secret"
            )
        if name != JWT_SECRET_ENV:
            logger.warning(
                "using %s for the JWT signing key; prefer %s so the intent is "
                "explicit.", name, JWT_SECRET_ENV,
            )
        return JWTHandler(algorithm=algorithm or "HS256", secret_key=secret)
    return None


def get_jwt_handler() -> JWTHandler:
    """Get the process-wide default JWT handler.

    Prefers a signing key pinned in the environment (see the ``*_ENV``
    constants above); falls back to a per-process key and says so, because an
    ephemeral key silently drops sessions on restart and cannot be shared
    between workers.
    """
    global _default_handler
    if _default_handler is None:
        unreadable: Optional[_UnreadableJwtSecret] = None
        try:
            handler = _handler_from_env()
        except _UnreadableJwtSecret as exc:
            unreadable = exc
            handler = None
        if unreadable is not None:
            # 换机器/换 Windows 用户：app.env 里的密文本机解不开。这里用
            # 本次进程的临时随机密钥继续启动（不写盘、不覆盖旧密文），并把
            # 恢复步骤用中文说清楚；绝不能静默生成新密钥覆盖旧值。
            logger.warning(
                "本机密钥无法在此机器解密，请用账号备份包恢复或重新生成。\n"
                "       变量：%s\n"
                "       可执行步骤：\n"
                "         1) 回到原电脑，在「设置 → 账号与备份」导出带口令的账号备份包；\n"
                "         2) 在新电脑导入该备份包后重启；\n"
                "         3) 若原电脑不可用，请重新注册管理员账号，系统会用新电脑的密钥重新签发令牌。\n"
                "       注意：本次不会覆盖 app.env 里的旧密文，但所有旧登录令牌已失效，需要重新登录。",
                unreadable.name,
            )
            handler = JWTHandler()
        if handler is None:
            if is_production():
                # Fail-CLOSED: an ephemeral, per-process signing key silently
                # drops every session on restart and cannot be shared across
                # workers. Refuse autonomous execution rather than degrade
                # security by minting tokens with a throwaway key.
                raise SecretBackendUnavailable(
                    f"no durable JWT signing key configured (set {JWT_SECRET_ENV} "
                    "or the RSA key env vars). Refusing to run in production "
                    "with an ephemeral, per-process key."
                )
            logger.warning(
                "no %s in the environment: signing keys are generated for this "
                "process only, so tokens do not survive a restart and are not "
                "shared across workers. Set %s for a durable deployment.",
                JWT_SECRET_ENV, JWT_SECRET_ENV,
            )
            handler = JWTHandler()
        _default_handler = handler
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
