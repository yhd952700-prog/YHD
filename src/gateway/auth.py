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
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from src.common.atomic_write import (
    atomic_write_text,
    exclusive_file_lock,
    read_text_with_retry,
)

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


class CredentialStoreError(RuntimeError):
    """凭据库写入事务失败。

    调用方必须把这条异常当成"本次修改没有落盘"，不能像以前那样拿到
    ``False`` 后继续宣称注册/恢复成功 —— 那正是"多账号注册后互相覆盖、
    甚至把文件写坏"的来源。
    """


#: 同进程内所有 ``SecretStore`` 实例共享的写锁。实例级锁挡不住"每个脚本/请求
#: new 一个 store"的真实路径；Windows ``msvcrt`` 的 region lock 又是进程级的，
#: 同进程内二次加锁会直接失败，所以进程内必须先串行。
_PROCESS_WRITE_LOCK = threading.Lock()


class SecretStore:
    """Read/write access to the credential file.

    Deliberately **not** cached: rotating a credential takes effect on the
    next login, and a stale in-memory copy of password material is exactly the
    kind of state worth not having.

    写入事务：进程锁 → OS 文件锁 → 锁内**重读磁盘** → 合并本次修改 → 唯一
    临时文件 + fsync + 重试 replace。任何一步失败都抛
    :class:`CredentialStoreError`，绝不把"没写进去"吞成 ``True``。
    """


    def __init__(self, path: str):
        self.path = str(path or "")

    # ---- reading -------------------------------------------------------

    def available(self) -> bool:
        return bool(self.path) and os.path.isfile(self.path)

    def _read_entries(self, *, strict: bool) -> Dict[str, Dict[str, Any]]:
        """读回 ``{principal: record}``。

        ``strict=False``（登录读路径）遇到任何问题返回 ``{}``（fail-closed）；
        ``strict=True``（写事务内）必须抛错 —— 把损坏文件当成空库再整份覆盖，
        会让所有账号的凭据一起消失。
        """
        if not self.available():
            return {}
        try:
            document = json.loads(read_text_with_retry(self.path))
        except Exception as exc:  # noqa: BLE001 - config problem, not a crash
            if strict:
                raise CredentialStoreError(
                    f"读取凭据库失败（{self.path}）：{type(exc).__name__}: {exc}；"
                    "为避免用空快照覆盖已有凭据，本次写入已拒绝"
                ) from exc
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
            if strict:
                raise CredentialStoreError(
                    f"凭据库结构不合法（{self.path}）：期望 JSON 对象，"
                    f"实际 {type(document).__name__}；本次写入已拒绝"
                )
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

    def load(self) -> Dict[str, Dict[str, Any]]:
        """Return ``{principal: record}``. Any problem yields ``{}`` (fail-closed)."""
        return self._read_entries(strict=False)

    def credential_for(self, principal: str) -> Optional[Dict[str, Any]]:
        return self.load().get(principal)

    # ---- writing (used by scripts/register_human_identity.py) ----------

    @contextmanager
    def _write_transaction(self) -> Iterator[None]:
        """进程锁 → OS 文件锁；整个「读全量 → 改 → 覆盖写」都在锁内。"""
        with _PROCESS_WRITE_LOCK:
            with exclusive_file_lock(f"{self.path}.lock", description="凭据库"):
                yield

    def _write(self, entries: Dict[str, Dict[str, Any]]) -> None:
        """原子替换文件；失败抛错，绝不留下半截凭据库。"""
        document = {"version": 1, "credentials": entries}
        text = json.dumps(
            document, ensure_ascii=False, indent=2, sort_keys=True
        ) + "\n"
        try:
            atomic_write_text(self.path, text)
        except Exception as exc:  # noqa: BLE001
            logger.error("could not write %r: %s", self.path, exc)
            raise CredentialStoreError(
                f"凭据库写入失败（{self.path}）：{type(exc).__name__}: {exc}；"
                "已重试仍无法原子替换，本次修改未落盘"
            ) from exc

    def set_credential(
        self, principal: str, secret: str, *, iterations: int = PBKDF2_ITERATIONS
    ) -> bool:
        pid = str(principal or "").strip()
        if not pid or not secret or not self.path:
            return False
        # PBKDF2 很慢：在锁外算好，锁内只做短事务。
        record = hash_credential(secret, iterations=iterations)
        with self._write_transaction():
            entries = self._read_entries(strict=True)
            entries[pid] = record
            self._write(entries)
        return True

    def import_credentials(self, entries: Dict[str, Dict[str, Any]]) -> int:
        """备份恢复入口：合并（同 principal 覆盖）凭据记录，返回写入条数。"""
        incoming = {
            str(k): v for k, v in (entries or {}).items() if isinstance(v, dict)
        }
        if not incoming or not self.path:
            return 0
        with self._write_transaction():
            current = self._read_entries(strict=True)
            current.update(incoming)
            self._write(current)
        return len(incoming)

    def remove_credential(self, principal: str) -> bool:
        if not self.path:
            return False
        with self._write_transaction():
            entries = self._read_entries(strict=True)
            if principal not in entries:
                return False
            entries.pop(principal)
            self._write(entries)
        return True


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
        # 给事件一个明确的动作名：界面要回答"谁在什么时候登录了"，
        # 只靠 access_allowed/access_denied 只能说"有人访问过"。
        audit_details = dict(details)
        audit_details.setdefault("action", "login")
        log_event(
            event_type=(
                AuditEventType.ACCESS_ALLOWED if allowed else AuditEventType.ACCESS_DENIED
            ),
            principal_id=principal or "anonymous",
            scope=AuditScope.L0,
            outcome=outcome,
            details=audit_details,
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
        raise AuthError(400, "账号和密码均不能为空")

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
        # Same message whether the account is unknown or the password is wrong:
        # a different message would let callers probe which accounts exist.
        raise AuthError(401, "账号或密码不正确")

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
            {"code": "AUTH_NOT_HUMAN", "message": "该账号不是可登录的账号"},
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
        # 旧版曾把恢复码以明文落盘：迁移后提示用户在登录状态下换新（旧码
        # 若在明文窗口期被读取过，只有重新生成才能止损）。
        "recovery_rotate_recommended": _recovery_rotate_recommended(),
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
        raise AuthError(403, "该账号不是可登录的账号")
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
            raise HTTPException(status_code=401, detail="登录信息已失效，请重新登录")
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


def _recovery_rotate_recommended() -> bool:
    """旧明文恢复码迁移后，是否应提示用户登录后重新生成。"""
    try:
        from .recovery_handoff import rotate_recommended

        return rotate_recommended()
    except Exception:  # noqa: BLE001 - 提示失败绝不影响登录/配置
        return False


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
    root_principal: Optional[str] = None
    recovery_configured = False
    try:
        root_node = get_identity_manager().account_root()
        if root_node is not None:
            root_exists = True
            root_principal = root_node.principal
            recovery_configured = bool(root_node.recovery_hash)
    except Exception as exc:  # noqa: BLE001 - 状态不明按"已初始化"处理，不放开注册
        logger.warning("could not read account root: %s", exc)
        root_exists = True

    # P0-1：根已落盘但**没有凭据**是"初始化停在半路"，不是"已完成"。
    # 旧实现只看到根存在就报 initialized，用户再注册 409、登录 401，永久锁死。
    # 只有**确证**根缺少凭据时才标记为可恢复；读状态失败一律按已完成处理，
    # 避免用异常把注册入口重新打开。
    initialization_incomplete = (
        root_principal is not None and root_principal not in credentials
    )
    registration_open = (not root_exists) or initialization_incomplete

    eligible = [h for h in humans if h.principal in credentials]
    secret_configured = bool(credentials)
    if initialization_incomplete:
        # 匿名可读的 /auth/config 不能回显根账号名：共享电脑上同机其他账户
        # 可据此补全注册并接管。这里只给中性描述，名称由用户自己回忆。
        message = (
            "初始化未完成：初始账号已创建，但还没有设置登录密码。"
            "请使用首次注册时填写的名称重新完成初始化（密码可以重新设置）。"
        )
    elif not secret_configured:
        message = (
            "还没有设置登录密码，目前任何人都无法登录。"
            "请为需要登录的账号设置密码。"
        )
    elif not eligible:
        message = "还没有可以登录的账号 —— 请为需要登录的账号设置密码。"
    else:
        message = f"已登记 {len(humans)} 个账号，其中 {len(eligible)} 个可以登录。"

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
        "registration_open": registration_open,
        "root": {"exists": root_exists},
        "registration": {"open": registration_open},
        # 初始化状态：pending（还没有账号）/ complete / incomplete（可恢复）。
        # 前端用 ``recoverable`` 决定是展示"首次初始化"还是"补全初始化"。
        "initialization": {
            "state": (
                "incomplete"
                if initialization_incomplete
                else ("complete" if root_exists else "pending")
            ),
            "recoverable": initialization_incomplete,
            # 匿名响应永不回显根账号名（避免同机其他账户接管半初始化实例）。
            "principal": None,
        },
        # 恢复码是否**真的**可用：只看"根存在"会在 0.1.0 升级上来的老数据
        # （recovery_hash 为空串）上撒谎 —— 界面会展示"用恢复码重置"，用户却
        # 永远拿到无效码。self_service 表示"没有恢复码、但可在本机自助补发"。
        "recovery": {
            "available": root_exists and recovery_configured,
            "self_service": root_exists and not recovery_configured,
            # 旧明文交接文件迁移过且用户还没换新：登录后应提示重新生成。
            "rotate_recommended": _recovery_rotate_recommended(),
        },
        "migration": {"allowed": not root_exists},
        "client_modes": list(CLIENT_MODES),
        "token": {"ttl_seconds": _ttl_seconds()},
        "message": message,
    }
