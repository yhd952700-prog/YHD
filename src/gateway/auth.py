"""Console authentication: exchange a credential for a bearer token.

Why this is a separate module, with a separate secret store
-----------------------------------------------------------
The Identity Kernel is the single authority on *who is human*
(:func:`src.kernels.identity.is_human_identity`: ``metadata["kind"] ==
"human"`` **and** status ``ACTIVE``). This module does not duplicate that
authority -- it *asks* the kernel. What it adds is the transport concern the
console needs and the kernel must not grow: turning a presented credential
into a short-lived signed token, and taking it away again.

Secrets therefore live in their own store rather than in the identity store
for two concrete reasons:

* the kernel's persistence layer records *registration facts* (who, which
  scope, registered since when). Putting password material there would make
  every identity consumer a holder of secrets;
* it keeps the identity store's on-disk schema unchanged, so no existing
  deployment needs a migration.

Endpoints
---------
``POST /v1/auth/login``    credential -> access token
``GET  /v1/auth/me``       token -> the identity behind it
``POST /v1/auth/logout``   revoke the presented token (idempotent)
``GET  /v1/auth/config``   what the login page needs to explain itself

The same token then unlocks the Policy endpoints, because
``require_human_principal`` resolves the principal from ``Authorization:
Bearer`` -- and this module issues tokens it accepts.

Fail-closed (OD-010)
--------------------
With no secret store, or no credential for a principal, **login is
impossible**. ``GET /v1/auth/config`` reports that state plainly so the login
page can explain it instead of failing opaquely. Booting the server never
creates a store, and no code path ever falls back to a machine identity.

Three clients, one session
--------------------------
The console ships as desktop / web / mobile presentations of one app. That is
a *presentation* difference, not an authentication one: all three call the
same endpoint and carry the same bearer token. ``LoginRequest.client`` records
which one signed in, so a token in the audit log is attributable to a device
class.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import secrets as _secrets
import threading
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from .policy import require_bearer_payload

router = APIRouter(prefix="/v1", tags=["auth"])
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

#: Path to the credential file. Unset (or missing) means nobody can log in.
AUTH_SECRETS_FILE_ENV = "LIUHAO_AUTH_SECRETS_FILE"

#: Access-token lifetime in seconds.
AUTH_TTL_ENV = "LIUHAO_AUTH_TTL_SECONDS"

#: 12h: long enough that a CEO is not re-typing a password all day, short
#: enough that a leaked token expires before it is worth much.
DEFAULT_TTL_SECONDS = 12 * 3600
MIN_TTL_SECONDS = 60
MAX_TTL_SECONDS = 7 * 24 * 3600

PBKDF2_ALGO = "pbkdf2_hmac_sha256"

#: Matches ``src.security.encryption``'s PBKDF2 default. The cost is paid once
#: per login, not per request.
PBKDF2_ITERATIONS = 600_000
SALT_BYTES = 16

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SECRETS_PATH = _REPO_ROOT / "config" / "auth_secrets.json"

#: Client classes the console ships. Recorded on the token, never used to
#: grant or deny -- all three are equally authenticated.
CLIENT_MODES = ("desktop", "web", "mobile")

# Brute-force throttle. In-process, so it is per-replica: the same scope as
# ``IdentityManager``'s in-memory table. Documented rather than silently
# assumed -- a multi-replica deployment needs a shared counter.
MAX_FAILURES = 5
FAILURE_WINDOW_SECONDS = 300

_failures: Dict[str, List[float]] = {}
_failures_lock = threading.Lock()


class AuthError(Exception):
    """A login failure carrying the HTTP status the endpoint should report."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


# ---------------------------------------------------------------------------
# Credential hashing (stdlib only -- the kernel line stays dependency-free)
# ---------------------------------------------------------------------------


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"))


def hash_credential(
    secret: str,
    *,
    salt: Optional[bytes] = None,
    iterations: int = PBKDF2_ITERATIONS,
) -> Dict[str, Any]:
    """Derive a storable credential record. Never returns the plaintext."""
    if not secret:
        raise ValueError("secret must not be empty")
    if iterations <= 0:
        raise ValueError("iterations must be positive")
    salt = salt if salt is not None else _secrets.token_bytes(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), salt, iterations)
    return {
        "algo": PBKDF2_ALGO,
        "iterations": iterations,
        "salt": _b64(salt),
        "hash": _b64(digest),
    }


def verify_credential(secret: str, record: Optional[Dict[str, Any]]) -> bool:
    """Constant-time credential check. Any malformed record is a refusal."""
    if not isinstance(record, dict):
        return False
    try:
        if record.get("algo") != PBKDF2_ALGO:
            return False
        iterations = int(record.get("iterations") or 0)
        if iterations <= 0:
            return False
        salt = _unb64(str(record.get("salt") or ""))
        expected = _unb64(str(record.get("hash") or ""))
        if not salt or not expected:
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", (secret or "").encode("utf-8"), salt, iterations
        )
    except Exception:  # noqa: BLE001 - a broken record is a denial, not a crash
        return False
    return hmac.compare_digest(digest, expected)


#: Burned when the principal has no credential, so an unknown principal costs
#: the same wall-clock time as a known one. Without this, response latency
#: enumerates which principals exist.
_DUMMY_SALT = b"liuhao-auth-timing-pad"


def _burn_equivalent_time(secret: str) -> bool:
    hashlib.pbkdf2_hmac(
        "sha256", (secret or "").encode("utf-8"), _DUMMY_SALT, PBKDF2_ITERATIONS
    )
    return False


def describe_credential(record: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Safe-to-display view of a credential. Never leaks salt or hash."""
    if not isinstance(record, dict) or not record:
        return {"configured": False}
    return {
        "configured": True,
        "algo": record.get("algo"),
        "iterations": record.get("iterations"),
    }


# ---------------------------------------------------------------------------
# Secret store
# ---------------------------------------------------------------------------


class SecretStore:
    """Read/write access to the credential file.

    Deliberately **not** cached: rotating a credential takes effect on the
    next login, and a stale in-memory copy of password material is exactly the
    kind of state worth not having.
    """

    def __init__(self, path: str):
        self.path = str(path or "")

    # ---- reading -------------------------------------------------------

    def available(self) -> bool:
        return bool(self.path) and os.path.isfile(self.path)

    def load(self) -> Dict[str, Dict[str, Any]]:
        """Return ``{principal: record}``. Any problem yields ``{}`` (fail-closed)."""
        if not self.available():
            return {}
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                document = json.load(handle)
        except Exception as exc:  # noqa: BLE001 - config problem, not a crash
            logger.error(
                "could not read %s (%r): %s -- NOBODY can log in",
                AUTH_SECRETS_FILE_ENV, self.path, exc,
            )
            return {}

        # Accept {"credentials": {...}} (canonical) or a bare mapping (handy
        # for one-line operator edits). Anything else is a refusal.
        if isinstance(document, dict) and isinstance(document.get("credentials"), dict):
            entries = document["credentials"]
        elif isinstance(document, dict):
            entries = document
        else:
            logger.error(
                "%s (%r) must be an object -- got %s",
                AUTH_SECRETS_FILE_ENV, self.path, type(document).__name__,
            )
            return {}

        return {
            str(principal): record
            for principal, record in entries.items()
            if isinstance(record, dict)
        }

    def credential_for(self, principal: str) -> Optional[Dict[str, Any]]:
        return self.load().get(principal)

    # ---- writing (used by scripts/register_human_identity.py) ----------

    def _write(self, entries: Dict[str, Dict[str, Any]]) -> bool:
        """Atomically replace the file; never leave a half-written store."""
        if not self.path:
            return False
        try:
            path = Path(self.path)
            path.parent.mkdir(parents=True, exist_ok=True)
            document = {"version": 1, "credentials": entries}
            tmp = path.with_suffix(path.suffix + ".tmp")
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(document, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
            os.replace(tmp, path)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.error("could not write %r: %s", self.path, exc)
            return False

    def set_credential(
        self, principal: str, secret: str, *, iterations: int = PBKDF2_ITERATIONS
    ) -> bool:
        pid = str(principal or "").strip()
        if not pid or not secret:
            return False
        entries = self.load()
        entries[pid] = hash_credential(secret, iterations=iterations)
        return self._write(entries)

    def remove_credential(self, principal: str) -> bool:
        entries = self.load()
        if principal not in entries:
            return False
        entries.pop(principal)
        return self._write(entries)


def resolve_secret_store() -> SecretStore:
    """Locate the credential file: explicit env var, else the config default."""
    return SecretStore(os.environ.get(AUTH_SECRETS_FILE_ENV) or str(DEFAULT_SECRETS_PATH))


# ---------------------------------------------------------------------------
# Throttle
# ---------------------------------------------------------------------------


def _recent_failures(key: str) -> List[float]:
    cutoff = time.monotonic() - FAILURE_WINDOW_SECONDS
    return [stamp for stamp in _failures.get(key, []) if stamp >= cutoff]


def _assert_not_throttled(key: str) -> None:
    with _failures_lock:
        recent = _recent_failures(key)
        _failures[key] = recent
        if len(recent) >= MAX_FAILURES:
            wait = FAILURE_WINDOW_SECONDS - (time.monotonic() - recent[0])
            raise AuthError(
                429,
                f"登录失败次数过多，请在 {max(1, int(wait))} 秒后重试",
            )


def _register_failure(key: str) -> None:
    with _failures_lock:
        _failures[key] = _recent_failures(key) + [time.monotonic()]


def _clear_failures(key: str) -> None:
    with _failures_lock:
        _failures.pop(key, None)


def reset_throttle() -> None:
    """Drop all failure counters (tests / operator unblock)."""
    with _failures_lock:
        _failures.clear()


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


def _audit(outcome: str, principal: str, details: Dict[str, Any]) -> None:
    """Record a login outcome. Audit failure must never break authentication.

    ``details`` must never carry secret material -- callers only ever pass a
    reason code and the client class.
    """
    try:
        from ..kernels.audit import AuditEventType, AuditScope, log_event

        allowed = outcome == "login_success"
        log_event(
            event_type=(
                AuditEventType.ACCESS_ALLOWED if allowed else AuditEventType.ACCESS_DENIED
            ),
            principal_id=principal or "anonymous",
            scope=AuditScope.L0,
            outcome=outcome,
            details=details,
        )
    except Exception as exc:  # noqa: BLE001 - defensive, and never fatal
        logger.warning("auth audit failed (%s): %s", outcome, exc)


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


def _ttl_seconds() -> int:
    raw = os.environ.get(AUTH_TTL_ENV)
    if not raw:
        return DEFAULT_TTL_SECONDS
    try:
        value = int(float(raw))
    except (TypeError, ValueError):
        logger.warning("%s=%r is not a number -- using %d", AUTH_TTL_ENV, raw, DEFAULT_TTL_SECONDS)
        return DEFAULT_TTL_SECONDS
    return max(MIN_TTL_SECONDS, min(MAX_TTL_SECONDS, value))


def _human_identity(principal: str):
    """Ask the Identity Kernel whether this principal is a login-eligible human.

    The kernel owns the answer; this module only relays it. Returns ``None``
    when the principal is unknown, not human, or not ACTIVE.
    """
    from ..kernels.identity import get_identity_manager, is_human_identity

    manager = get_identity_manager()
    identity = manager.get_identity_by_principal(principal)
    if identity is None or not is_human_identity(identity):
        return None
    return identity


def authenticate(principal: str, secret: str, *, client: str = "web") -> Dict[str, Any]:
    """Verify a credential and mint an access token.

    Raises :class:`AuthError` (``status_code`` 400 / 401 / 403 / 429) on any
    failure. Kept free of HTTP so it is unit-testable directly.
    """
    pid = str(principal or "").strip()
    if not pid or not secret:
        raise AuthError(400, "principal 与 secret 均不能为空")

    client_value = str(client or "web").strip().lower()
    if client_value not in CLIENT_MODES:
        client_value = "web"

    throttle_key = pid.lower()
    _assert_not_throttled(throttle_key)

    record = resolve_secret_store().credential_for(pid)
    ok = verify_credential(secret, record) if record else _burn_equivalent_time(secret)
    if not ok:
        _register_failure(throttle_key)
        _audit("login_denied", pid, {"reason": "bad_credential", "client": client_value})
        # Same message whether the principal is unknown or the secret is wrong:
        # a different message would turn this endpoint into a principal oracle.
        raise AuthError(401, "凭据无效")

    identity = _human_identity(pid)
    if identity is None:
        _register_failure(throttle_key)
        _audit(
            "login_denied",
            pid,
            {"reason": "not_a_human_identity", "client": client_value},
        )
        raise AuthError(403, "该主体不是可登录的人类身份（需 metadata.kind=human 且状态 ACTIVE）")

    permissions = sorted(identity.permissions)
    ttl = _ttl_seconds()
    token, payload = _issue_token(pid, identity, client_value, permissions, ttl)
    _clear_failures(throttle_key)
    _audit(
        "login_success",
        pid,
        {"client": client_value, "scope": identity.scope.value, "jti": payload.jti},
    )
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": ttl,
        "expires_at": payload.exp,
        "principal": pid,
        "display_name": (identity.metadata or {}).get("display_name"),
        "scope": identity.scope.value,
        "permissions": permissions,
        "client": client_value,
    }


def _issue_token(principal, identity, client, permissions, ttl):
    from ..security.jwt_handler import create_access_token

    return create_access_token(
        subject=principal,
        scopes=["console"],
        roles=sorted(identity.permissions),
        permissions=permissions,
        ttl=ttl,
        metadata={
            "kind": "human",
            "client": client,
            "scope": identity.scope.value,
        },
        device_id=client,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    principal: str = Field(..., min_length=1, max_length=128, description="登录主体")
    secret: str = Field(..., min_length=1, max_length=512, description="凭据")
    client: str = Field("web", max_length=16, description="desktop | web | mobile")


@router.post("/auth/login", response_model=Dict[str, Any])
def auth_login(req: LoginRequest) -> Dict[str, Any]:
    """Exchange ``principal`` + ``secret`` for a bearer token."""
    try:
        return authenticate(req.principal, req.secret, client=req.client)
    except AuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)


@router.get("/auth/me", response_model=Dict[str, Any])
def auth_me(payload=Depends(require_bearer_payload)) -> Dict[str, Any]:
    """Describe the identity a valid token belongs to."""
    principal = str(getattr(payload, "sub", "") or "")
    identity = _human_identity(principal)
    metadata = (identity.metadata or {}) if identity is not None else {}
    return {
        "principal": principal,
        "display_name": metadata.get("display_name"),
        "scope": identity.scope.value if identity is not None else None,
        "permissions": sorted(identity.permissions) if identity is not None else [],
        "status": identity.status.value if identity is not None else None,
        "registered_at": metadata.get("registered_at"),
        # A token can outlive its subject's human-ness (an operator could
        # deactivate the identity). Say so instead of pretending.
        "still_human": identity is not None,
        "expires_at": getattr(payload, "exp", None),
        "client": (getattr(payload, "metadata", None) or {}).get("client"),
    }


@router.post("/auth/logout", response_model=Dict[str, Any])
def auth_logout(authorization: Optional[str] = Header(None)) -> Dict[str, Any]:
    """Revoke the presented token. Idempotent: no token is still ``logged_out``."""
    if not authorization or not authorization.lower().startswith("bearer "):
        return {"logged_out": True, "revoked": False}
    token = authorization.split(" ", 1)[1].strip()
    if not token:
        return {"logged_out": True, "revoked": False}
    try:
        from ..security import get_jwt_handler

        handler = get_jwt_handler()
        principal = ""
        try:
            principal = str(getattr(handler.validate_access_token(token), "sub", "") or "")
        except Exception:  # noqa: BLE001 - already-invalid token: still a logout
            principal = ""
        revoked = bool(handler.revoke_token(token))
    except Exception as exc:  # noqa: BLE001
        logger.warning("logout revoke failed: %s", exc)
        revoked = False
    _audit("logout", principal, {"revoked": revoked})
    return {"logged_out": True, "revoked": revoked}


@router.get("/auth/config", response_model=Dict[str, Any])
def auth_config() -> Dict[str, Any]:
    """What the login page needs to describe the current auth state.

    Public by necessity. It therefore reports *counts and booleans* about the
    credential store -- never principals, salts or hashes.
    """
    from ..kernels.identity import get_identity_manager, is_human_identity

    store = resolve_secret_store()
    credentials = store.load()

    try:
        humans = [
            identity
            for identity in get_identity_manager().list_identities()
            if is_human_identity(identity)
        ]
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not enumerate human identities: %s", exc)
        humans = []

    eligible = [h for h in humans if h.principal in credentials]
    secret_configured = bool(credentials)
    if not secret_configured:
        message = (
            "尚未配置登录凭据：任何人都无法登录（fail-closed，OD-010）。"
            "用 scripts/register_human_identity.py --password 设置。"
        )
    elif not eligible:
        message = (
            f"已登记 {len(humans)} 个人类身份，但没有一个设置了凭据 —— "
            "请为要登录的主体设置密码。"
        )
    else:
        message = f"已登记 {len(humans)} 个人类身份，其中 {len(eligible)} 个可登录。"

    return {
        "auth_required": True,
        "methods": ["password"],
        # Honest: no third-party login is wired up. Reporting these as
        # available would make the login page lie.
        "oauth": {"wechat": False, "sms": False},
        "secret_store": {
            "configured": secret_configured,
            "source": os.path.basename(store.path) if store.path else None,
        },
        "registered_humans": len(humans),
        "login_eligible_humans": len(eligible),
        "client_modes": list(CLIENT_MODES),
        "token": {"ttl_seconds": _ttl_seconds()},
        "message": message,
    }
