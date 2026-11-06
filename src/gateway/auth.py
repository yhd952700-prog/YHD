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

from .policy import extract_bearer_token, require_bearer_payload

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

    def __init__(self, status_code: int, detail: Any):
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

    def import_credentials(self, entries: Dict[str, Dict[str, Any]]) -> int:
        """备份恢复入口：合并（同 principal 覆盖）凭据记录，返回写入条数。"""
        incoming = {
            str(k): v for k, v in (entries or {}).items() if isinstance(v, dict)
        }
        if not incoming:
            return 0
        current = self.load()
        current.update(incoming)
        return len(incoming) if self._write(current) else 0

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

    Resolution order matters: a token's ``sub`` is the *identity id*
    (``human:<name>`` / ``human:<hex>``) when minted by
    ``scripts/issue_console_token.py``, but the *principal string* (``liuhao``)
    when minted by ``POST /v1/auth/login``. Looking up only by principal made
    every script-minted human token report ``still_human: false`` with null
    scope/permissions -- the token was valid, the identity was simply looked up
    in the wrong key space. Accept both, exactly as ``_sovereignty``'s
    ``_validated_principal`` already does.
    """
    from ..kernels.identity import get_identity_manager, is_human_identity

    manager = get_identity_manager()
    identity = manager.get_identity(principal) or manager.get_identity_by_principal(
        principal
    )
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
        node = _account_node(pid)
        if node is not None and node.status != "active":
            # 停用/删除的账号：登录阶段就拒绝，并给出稳定业务码与中性文案
            # （不暴露"已被谁停用/为什么"）。
            raise AuthError(
                403, {"code": "ACCOUNT_UNAVAILABLE", "message": "该账号当前不可用"}
            )
        raise AuthError(
            403,
            {"code": "AUTH_NOT_HUMAN", "message": "该主体不是可登录的账号"},
        )

    permissions = sorted(identity.permissions)
    ttl = _ttl_seconds()
    scope = _effective_scope(identity)
    token, payload = _issue_token(pid, identity, client_value, permissions, ttl, scope)
    _clear_failures(throttle_key)
    _audit(
        "login_success",
        pid,
        {"client": client_value, "scope": scope, "jti": payload.jti},
    )
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": ttl,
        "expires_at": payload.exp,
        "principal": pid,
        "display_name": (identity.metadata or {}).get("display_name"),
        "scope": scope,
        "permissions": permissions,
        "client": client_value,
        **_account_position(pid),
    }


def _effective_scope(identity) -> str:
    """Return the scope a *token* may carry for this identity.

    **定稿（docs/account-hierarchy.md §2.4）**：账号树内的 scope 由 **depth**
    派生 —— 根 L0、子 L1、孙 L2……封顶 L7。这样成员的 clearance 与"能管理
    自己直接下级"一致，而不是被一律压成 L7。

    没有账号节点的老部署保持原语义：显式 ``owner`` 保留存库 scope；
    ``admin/administrator`` → L1；其余 → L7（最低权限；登录仍可用）。
    派生失败或 depth 越界时 fail-closed 到 L7，绝不默认提升。
    """
    depth_scope = _depth_scope(identity)
    if depth_scope is not None:
        return depth_scope
    permissions = {str(p).lower() for p in (identity.permissions or [])}
    if "owner" in permissions:
        return identity.scope.value
    if permissions & {"admin", "administrator"}:
        return "L1"
    return "L7"


def _depth_scope(identity) -> Optional[str]:
    """账号树内按 depth 派生 clearance；树外 / 异常返回 ``None``（走旧口径）。"""
    principal = str(getattr(identity, "principal", "") or "")
    if not principal:
        return None
    try:
        from ..kernels.identity import get_identity_manager

        node = get_identity_manager().get_account(principal)
    except Exception:  # noqa: BLE001 - 派生失败不得阻断登录
        return None
    if node is None:
        return None
    try:
        depth = int(node.depth)
    except (TypeError, ValueError):
        return "L7"
    if depth < 0:
        return "L7"
    return f"L{min(depth, 7)}"


def _issue_token(principal, identity, client, permissions, ttl, scope):
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
            "scope": scope,
            # 账号树 epoch：权限/停用/Key 归属变化会 +1，网关在每个请求上比对，
            # 已签发凭据因此"下一个请求即失效"，不需要等重新登录。
            "account_epoch": _account_epoch(principal),
        },
        device_id=client,
    )


def _account_node(principal: str):
    """账号节点（不存在时 ``None``）；账号内核异常不得影响认证。"""
    try:
        from ..kernels.identity import get_identity_manager

        return get_identity_manager().get_account(principal)
    except Exception:  # noqa: BLE001 - 账号内核不可用按老部署处理
        return None


def _account_epoch(principal: str) -> int:
    """Read the account security epoch, 0 for principals outside the tree.

    0 is also what legacy tokens (and standalone peers) carry, so an old
    deployment sees no behaviour change until an account actually exists.
    """
    try:
        from ..kernels.identity import get_identity_manager

        node = get_identity_manager().get_account(principal)
        return int(node.security_epoch) if node is not None else 0
    except Exception:  # noqa: BLE001 - auth must not fail because of the tree
        return 0


def _account_position(principal: str) -> Dict[str, Any]:
    """本人在账号树中的位置（``parent_account_id`` / ``path`` / ``depth``）。

    只返回**本人**的位置：前端做能力裁剪需要它，但它不构成"看别人"的通道。
    没有账号节点的老部署按独立根处理（与 ``account_context`` 的兼容路径一致）。
    """
    fallback = {
        "parent_account_id": None,
        "path": f"/{principal}" if principal else "",
        "depth": 0,
    }
    try:
        from ..kernels.identity import _accounts, get_identity_manager

        manager = get_identity_manager()
        node = manager.get_account(principal)
        if node is None:
            return fallback
        return {
            "parent_account_id": node.parent_id,
            "path": _accounts.account_path(manager.account_nodes(), principal)
            or fallback["path"],
            "depth": int(node.depth),
        }
    except Exception:  # noqa: BLE001 - 位置信息不得阻断登录/自述
        return fallback


def issue_token_for(principal: str, client: str = "web") -> Dict[str, Any]:
    """Mint a token for an already-authenticated principal.

    Used by activation / recovery flows: the caller has just proven possession
    of a one-time code, so no credential record is required yet.
    """
    identity = _human_identity(principal)
    if identity is None:
        raise AuthError(403, "该主体不是可登录的人类身份（需 metadata.kind=human 且状态 ACTIVE）")
    permissions = sorted(identity.permissions)
    ttl = _ttl_seconds()
    scope = _effective_scope(identity)
    token, payload = _issue_token(principal, identity, client, permissions, ttl, scope)
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": ttl,
        "expires_at": payload.exp,
        "principal": principal,
        "display_name": (identity.metadata or {}).get("display_name"),
        "scope": scope,
        "permissions": permissions,
        "client": client,
        **_account_position(principal),
    }


def rotate_credential(principal: str, secret: str) -> bool:
    """Set/replace a human credential (activation, reset, recovery)."""
    return resolve_secret_store().set_credential(principal, secret)


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
    token_metadata = getattr(payload, "metadata", None)
    token_metadata = token_metadata if isinstance(token_metadata, dict) else {}
    # 口径统一：``/auth/login`` 返回的是**签进令牌**的 scope，``/auth/me`` 也读
    # 同一份令牌声明（而不是身份库里另一个 clearance 字段），否则同一主体会
    # 出现"登录 L7、自述 L3"的两套答案。令牌缺失该声明时才按当前规则派生。
    effective_scope = token_metadata.get("scope")
    if not effective_scope and identity is not None:
        effective_scope = _effective_scope(identity)
    return {
        "principal": principal,
        "display_name": metadata.get("display_name"),
        "scope": effective_scope,
        "permissions": sorted(identity.permissions) if identity is not None else [],
        "status": identity.status.value if identity is not None else None,
        "registered_at": metadata.get("registered_at"),
        # A token can outlive its subject's human-ness (an operator could
        # deactivate the identity). Say so instead of pretending.
        "still_human": identity is not None,
        "expires_at": getattr(payload, "exp", None),
        "client": (getattr(payload, "metadata", None) or {}).get("client"),
        **_account_position(principal),
    }


@router.post("/auth/logout", response_model=Dict[str, Any])
def auth_logout(
    x_liuhao_token: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
) -> Dict[str, Any]:
    """Revoke the presented token. Idempotent: no token is still ``logged_out``.

    Reads the token through the same two headers as the gate (see
    ``policy.TOKEN_HEADER``): revoking the *gateway's* token instead of the
    caller's would report a logout while leaving the real session live.
    """
    token = extract_bearer_token(x_liuhao_token, authorization)
    if not token:
        return {"logged_out": True, "revoked": False}
    try:
        from ..security import get_jwt_handler

        handler = get_jwt_handler()
        # Verify the signature before touching the blocklist. Revoking an
        # expired token is still fine (verify_exp=False), but accepting
        # arbitrary garbage would let an unauthenticated caller grow the
        # revocation table without bound.
        try:
            payload = handler.decode_signature_only(token, verify_exp=False)
        except Exception as exc:  # noqa: BLE001 - any failure means "forged"
            logger.warning("logout rejected unverifiable token: %s", exc)
            raise HTTPException(status_code=401, detail="invalid bearer token")
        principal = str(getattr(payload, "sub", "") or "")
        revoked = bool(handler.revoke_token(token))
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error("logout revoke failed: %s", exc)
        revoked = False
        principal = ""
    _audit("logout", principal, {"revoked": revoked})
    return {"logged_out": True, "revoked": revoked}


@router.get("/auth/config", response_model=Dict[str, Any])
def auth_config() -> Dict[str, Any]:
    """What the login page needs to describe the current auth state.

    Public by necessity. It therefore reports *counts and booleans* about the
    credential store -- never principals, salts or hashes, and never anything
    echoed back from the caller's own request headers.
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

    root_exists = False
    try:
        root_exists = get_identity_manager().account_root() is not None
    except Exception as exc:  # noqa: BLE001 - 状态不明按"已初始化"处理，不放开注册
        logger.warning("could not read account root: %s", exc)
        root_exists = True

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
        # 登录页据此决定是否展示"首次初始化"入口：已存在主账号时自助注册根
        # 一律关闭（后端也会 409 拒绝并写审计），前端不得自行猜测。
        # 扁平字段是驾驶舱 `AuthConfig` 契约（已上线），嵌套字段是文档 §2.5 契约；
        # 两个都返回，避免任一消费方读到 undefined 后自己猜。
        "root_account_exists": root_exists,
        "registration_open": not root_exists,
        "root": {"exists": root_exists},
        "registration": {"open": not root_exists},
        "recovery": {"available": root_exists},
        "migration": {"allowed": not root_exists},
        "client_modes": list(CLIENT_MODES),
        "token": {"ttl_seconds": _ttl_seconds()},
        "message": message,
    }
