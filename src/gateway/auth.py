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

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import secrets as _secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from src.common.atomic_write import (
    atomic_write_text,
    exclusive_file_lock,
    read_text_with_retry,
)

from .account_context import AccountContext, require_account_principal
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

#: Refresh-token lifetime in seconds.
AUTH_REFRESH_TTL_ENV = "LIUHAO_AUTH_REFRESH_TTL_SECONDS"

#: 0.4.6 统一口径（见 docs/jwt-token-policy.md）：access 15 分钟 +
#: refresh 7 天轮换，与 ``src/security/jwt_handler.py`` 的默认值一致。
#: 旧的网关 12 小时单令牌退役：12 小时无刷新、无轮换，令牌泄露后有效
#: 期太长；15 分钟 access + 可吊销、可轮换的 refresh 是更短的暴露窗口。
#: 已签发的旧 12 小时令牌按其自身 exp 继续有效到过期，不会被提前作废。
DEFAULT_TTL_SECONDS = 15 * 60
DEFAULT_REFRESH_TTL_SECONDS = 7 * 24 * 3600
MIN_TTL_SECONDS = 60
MAX_TTL_SECONDS = 7 * 24 * 3600
MIN_REFRESH_TTL_SECONDS = 3600
MAX_REFRESH_TTL_SECONDS = 30 * 24 * 3600

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

#: 登录请求级预算（秒），环境变量 ``LIUHAO_LOGIN_TIMEOUT_SECONDS`` 可覆盖。
#: 凭据校验（PBKDF2 600k 次 + SQLite 读 + 审计写）是同步阻塞工作，FastAPI
#: 把它放在线程池里跑；满载（CPU 打满/线程池排队）时没有预算，登录请求会
#: 无限挂起、界面一直转圈，只能重启（P0，见 RELEASE-RUNBOOK.md §8 已知
#: 问题 5）。30 秒是"平时一次登录远不到 1 秒，满载时也不该让人无限等"
#: 的折中：超时后回 504「登录超时，请重试」，用户可重试，而不是重启。
#: 注意：``asyncio.wait_for`` 取消的是等待，不是底下的线程 —— 超时后那
#: 次校验仍可能在后台跑完（令牌已签发、审计已写），重试会再签一个新令牌；
#: 旧令牌有效但客户端不知道它存在，与"超时后重试"的常规语义一致。
LOGIN_TIMEOUT_ENV = "LIUHAO_LOGIN_TIMEOUT_SECONDS"
DEFAULT_LOGIN_TIMEOUT_SECONDS = 30.0
MIN_LOGIN_TIMEOUT_SECONDS = 1.0
MAX_LOGIN_TIMEOUT_SECONDS = 300.0


def login_timeout_seconds() -> float:
    """读取登录请求级预算；非法/越界值退回默认 30 秒（每次调用都重读）。"""
    raw = (os.environ.get(LOGIN_TIMEOUT_ENV) or "").strip()
    if raw:
        try:
            value = float(raw)
        except (TypeError, ValueError):
            value = 0.0
        if value > 0:
            return max(
                MIN_LOGIN_TIMEOUT_SECONDS,
                min(MAX_LOGIN_TIMEOUT_SECONDS, value),
            )
    return DEFAULT_LOGIN_TIMEOUT_SECONDS

# Brute-force throttle. Counters live in SQLite (``login_throttle.sqlite3``,
# next to the audit DB but a separate file), so a restart does **not** reset
# the 5-in-300s window -- an attacker can no longer clear a lockout by
# crashing/restarting the server.
#
# 检测面（2026-11-13 核实）：旧 ``audit_trail.py`` 删掉后，暴力登录的“看见”
# 并没有丢——每次失败登录都写主线审计（``_audit("login_denied", ...)`` →
# ``src/kernels/audit``，action=login、结果 denied），在「操作记录」页按
# 操作类型 login + 结果 denied 就能筛出谁在撞密码；防锁（5 次/300 秒）与
# Owner 解锁（``POST /auth/throttle/unlock``）都在。所以不重做检测器，
# 只在这里写清去向，免得后人以为能力丢了又造一套。
#
# Timestamps are wall-clock epoch seconds: ``time.monotonic()`` is meaningless
# across restarts. A system-clock rollback can only shorten the window, and
# only for someone able to move the machine clock.
#
# If the throttle DB is ever unusable (permissions, disk full), the counter
# falls back to the in-process dict so login itself never breaks -- the
# fallback is logged, and the window then behaves like the old in-memory one
# until the DB is writable again.
MAX_FAILURES = 5
FAILURE_WINDOW_SECONDS = 300

#: Explicit override for the throttle DB (tests / operators).
THROTTLE_DB_ENV = "LIUHAO_LOGIN_THROTTLE_DB"

_failures: Dict[str, List[float]] = {}
_failures_lock = threading.Lock()


def _throttle_db_path() -> Path:
    """Resolve ``login_throttle.sqlite3`` next to the audit DB.

    Priority: ``LIUHAO_LOGIN_THROTTLE_DB`` env, then the data dir
    (``LIUHAO_DATA_DIR``), then the shared store-path fallback.
    """
    explicit = (os.environ.get(THROTTLE_DB_ENV) or "").strip()
    if explicit:
        return Path(explicit)
    from src.common.data_paths import data_root, resolve_store_path

    root = data_root()
    if root is not None:
        return root / "login_throttle.sqlite3"
    return Path(
        resolve_store_path(
            "login_throttle.sqlite3",
            Path.home() / ".liuhao" / "login_throttle.sqlite3",
            env_var=THROTTLE_DB_ENV,
        )
    )


def _throttle_connect() -> sqlite3.Connection:
    path = _throttle_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=5.0, check_same_thread=False)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS login_failures ("
        " principal TEXT NOT NULL, failed_at REAL NOT NULL)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_login_failures_principal"
        " ON login_failures (principal, failed_at)"
    )
    conn.commit()
    return conn


class AuthError(Exception):
    """A login failure carrying the HTTP status the endpoint should report."""

    def __init__(self, status_code: int, detail: Any, headers: Optional[Dict[str, str]] = None):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.headers = headers


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
        self._restrict_permissions()

    def _restrict_permissions(self) -> None:
        """创建/覆盖后立即把凭据库收紧为“仅当前用户 + 去继承”。

        为什么必须在写入之后就做，而不是等启动器事后补：``atomic_write_text``
        是“同目录临时文件 + 原子替换”，新文件的 ACE 继承自父目录。安装副本的
        ``%LOCALAPPDATA%\\LiuHao\\data\\config`` 会继承 ``CodexSandboxUsers`` /
        ``Administrators``，凭据库（口令 PBKDF2 材料）因此对同机其它沙箱用户
        可读——0.1.1 装出来的 ``auth_secrets.json`` 正是这个状态。

        收紧失败只告警、不打断注册/改密（可用性优先），但告警必须带路径，
        不允许静默放过。
        """
        from ..common.windows_acl import (
            acl_is_current_user_only,
            tighten_current_user_only,
        )

        path = Path(self.path)
        try:
            tightened = tighten_current_user_only(path)
            protected = acl_is_current_user_only(path)
        except Exception:  # noqa: BLE001 - ACL 是加固步骤，不能反过来阻断登录
            logger.warning("凭据库 ACL 收紧异常：%s", path, exc_info=True)
            return
        if not (tightened and protected):
            logger.warning(
                "凭据库 ACL 未达「仅当前用户」：%s（tightened=%s protected=%s）；"
                "请检查 %s 的继承权限",
                path, tightened, protected, path.parent,
            )

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

    def export_credentials(self, principals: Optional[list] = None) -> Dict[str, Dict[str, Any]]:
        """备份导出：按主体过滤后的凭据记录（PBKDF2 哈希，不含明文口令）。

        迁移认领后必须能"用原密码登录"（N17）；口令哈希只会进入口令加密的
        凭据包，不写盘、不落日志、不进审计。``principals=None`` 表示全量。
        """
        entries = self._read_entries(strict=False)
        if principals is None:
            return dict(entries)
        allowed = {str(item) for item in principals if str(item)}
        return {key: value for key, value in entries.items() if key in allowed}

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
    """Locate the credential file.

    0.4.6 起默认路径迁到数据目录（``%LOCALAPPDATA%\\LiuHao\\data\\config\\
    auth_secrets.json``，Linux 下为 ``$LIUHAO_DATA_DIR/config/auth_secrets.json``），
    不再默认落在仓库 ``config/`` 里。优先级：

    1. 显式环境变量 ``LIUHAO_AUTH_SECRETS_FILE``；
    2. 数据目录新位置 ``config/auth_secrets.json``（存在即用）；
    3. 数据目录旧位置 ``auth_secrets.json``（0.4.6 之前已迁过去的部署，
       继续用，不静默换地方）；
    4. 数据目录新位置（不存在时作为新默认，并把仓库旧文件**复制**过去，
       原文件保留不删，复制失败也不阻断登录）；
    5. 未设数据目录时保持仓库 ``config/auth_secrets.json``（开发模式原样）。
    """
    from src.common.data_paths import data_root, resolve_store_path

    explicit = (os.environ.get(AUTH_SECRETS_FILE_ENV) or "").strip()
    if explicit:
        return SecretStore(explicit)
    root = data_root()
    if root is not None:
        new_path = root / "config" / "auth_secrets.json"
        old_data_path = root / "auth_secrets.json"
        if new_path.is_file():
            return SecretStore(str(new_path))
        if old_data_path.is_file():
            return SecretStore(str(old_data_path))
        if DEFAULT_SECRETS_PATH.is_file():
            # 一次性迁移：仓库旧文件复制到数据目录新位置，原文件保留。
            try:
                import shutil

                new_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(DEFAULT_SECRETS_PATH, new_path)
                logger.info(
                    "凭据文件已从仓库 config/ 迁移到数据目录：%s（原文件保留）",
                    new_path,
                )
            except OSError as exc:  # noqa: BLE001 - 迁移失败用旧位置继续登录
                logger.warning("凭据文件迁移到数据目录失败：%s；继续使用仓库旧文件", exc)
                return SecretStore(str(DEFAULT_SECRETS_PATH))
        return SecretStore(str(new_path))
    return SecretStore(
        resolve_store_path(
            "auth_secrets.json",
            DEFAULT_SECRETS_PATH,
            env_var=AUTH_SECRETS_FILE_ENV,
        )
    )


# ---------------------------------------------------------------------------
# Throttle
# ---------------------------------------------------------------------------


def _recent_failures(key: str) -> List[float]:
    """Failure timestamps inside the sliding window (wall-clock seconds).

    SQLite is the source of truth; the in-memory dict is only the fallback
    when the DB is unusable, so a restart keeps the count.
    """
    cutoff = time.time() - FAILURE_WINDOW_SECONDS
    try:
        conn = _throttle_connect()
        try:
            conn.execute(
                "DELETE FROM login_failures WHERE failed_at < ?", (cutoff,)
            )
            conn.commit()
            rows = conn.execute(
                "SELECT failed_at FROM login_failures"
                " WHERE principal = ? ORDER BY failed_at",
                (key,),
            ).fetchall()
            return [float(r[0]) for r in rows]
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 - throttle DB must never break login
        logger.warning("登录节流库不可用，退回内存计数：%s", exc)
        return [stamp for stamp in _failures.get(key, []) if stamp >= cutoff]


def _assert_not_throttled(key: str) -> None:
    with _failures_lock:
        recent = _recent_failures(key)
        _failures[key] = recent
        if len(recent) >= MAX_FAILURES:
            wait = FAILURE_WINDOW_SECONDS - (time.time() - recent[0])
            retry_after = max(1, int(wait))
            _audit(
                "login_throttled",
                key,
                {"reason": "too_many_failures", "failures": len(recent)},
                event_type="LOGIN_THROTTLED",
            )
            raise AuthError(
                429,
                f"登录失败次数过多，请在 {retry_after} 秒后重试",
                headers={
                    "Retry-After": str(retry_after),
                    "X-RateLimit-Limit": str(MAX_FAILURES),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(retry_after),
                },
            )


def _register_failure(key: str) -> None:
    with _failures_lock:
        now = time.time()
        try:
            conn = _throttle_connect()
            try:
                conn.execute(
                    "INSERT INTO login_failures (principal, failed_at) VALUES (?, ?)",
                    (key, now),
                )
                conn.execute(
                    "DELETE FROM login_failures WHERE failed_at < ?",
                    (now - FAILURE_WINDOW_SECONDS,),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001 - throttle DB must never break login
            logger.warning("登录节流库不可用，退回内存计数：%s", exc)
            _failures[key] = _recent_failures(key) + [now]
        else:
            _failures[key] = _recent_failures(key)


def _clear_failures(key: str) -> None:
    with _failures_lock:
        try:
            conn = _throttle_connect()
            try:
                conn.execute(
                    "DELETE FROM login_failures WHERE principal = ?", (key,)
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001 - throttle DB must never break login
            logger.warning("登录节流库不可用，退回内存计数：%s", exc)
        _failures.pop(key, None)


def reset_throttle() -> None:
    """Drop all failure counters (tests / operator unblock)."""
    with _failures_lock:
        try:
            conn = _throttle_connect()
            try:
                conn.execute("DELETE FROM login_failures")
                conn.commit()
            finally:
                conn.close()
        except Exception:  # noqa: BLE001 - best effort; memory fallback still clears
            pass
        _failures.clear()


def throttle_failure_count(principal: str) -> int:
    """Failures inside the window for one principal (0 when the DB is down)."""
    return len(_recent_failures(str(principal or "").strip().lower()))


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


#: 审计写入失败时对调用方的可读提示（与恢复端点同一口径）：操作照常完成，
#: 但必须让用户知道"本次没有留下审计记录"。只追加到成功响应，不改既有契约。
AUDIT_NOT_WRITTEN_MESSAGE = (
    "审计未写入：本次操作已完成，但审计记录未能落盘；请联系管理员检查审计库"
    "（数据目录写权限、磁盘空间或 AUDIT_DB_PATH 配置）。"
)


def audit_failure_snapshot() -> int:
    """记录进程级审计失败累计值，用于判断"本次请求内是否发生写失败"。

    返回 ``-1`` 表示计数不可用；此时不声称"审计未写入"，避免误报。
    """
    try:
        from ..kernels.audit import get_audit_failure_count

        return int(get_audit_failure_count())
    except Exception:  # noqa: BLE001 - 取不到计数就不声称失败
        return -1


def audit_notice_after(snapshot: int, operation: str) -> Dict[str, Any]:
    """若本次请求期间审计写入失败，返回可追加到成功响应的字段。

    语义与恢复端点一致：**不阻断操作**，只让结果"可见"——成功响应多出
    ``audit_written=false`` 与一段中文 ``audit_warning``，并留一行明确日志。
    """
    try:
        from ..kernels.audit import get_audit_failure_count

        current = int(get_audit_failure_count())
    except Exception:  # noqa: BLE001
        return {}
    if snapshot < 0 or current <= snapshot:
        return {}
    logger.warning("审计未写入：%s 已完成但未留下审计记录", operation)
    return {
        "audit_written": False,
        "audit_warning": AUDIT_NOT_WRITTEN_MESSAGE,
    }


def _audit(
    outcome: str,
    principal: str,
    details: Dict[str, Any],
    *,
    event_type: Optional[str] = None,
) -> bool:
    """Record a login outcome. Audit failure must never break authentication.

    ``details`` must never carry secret material -- callers only ever pass a
    reason code and the client class.

    ``event_type`` overrides the default ACCESS_ALLOWED/ACCESS_DENIED mapping
    (e.g. ``LOGIN_THROTTLED`` / ``LOGIN_THROTTLE_UNLOCKED``); unknown names
    fall back to the default mapping instead of dropping the record.

    返回是否真正写入审计：登录成功但审计写失败时，调用方据此在响应里
    显式告知“审计未写入”，同时照常发放令牌（不阻断登录）。
    """
    try:
        from ..kernels.audit import AuditEventType, AuditScope, log_event

        allowed = outcome == "login_success"
        # 给事件一个明确的动作名：界面要回答"谁在什么时候登录了"，
        # 只靠 access_allowed/access_denied 只能说"有人访问过"。
        audit_details = dict(details)
        audit_details.setdefault("action", "login")
        # 登录的“对象”就是本人：补上 target，四个字段（谁/何时/对什么/结果）
        # 字面齐全，不必让读者从 principal_id 反推。
        audit_details.setdefault("target", principal or "anonymous")
        resolved_type = None
        if event_type:
            resolved_type = getattr(AuditEventType, event_type, None)
            if resolved_type is None:
                logger.warning("未知审计事件类型 %s，退回默认映射", event_type)
        if resolved_type is None:
            resolved_type = (
                AuditEventType.ACCESS_ALLOWED if allowed else AuditEventType.ACCESS_DENIED
            )
        log_event(
            event_type=resolved_type,
            principal_id=principal or "anonymous",
            scope=AuditScope.L0,
            outcome=outcome,
            details=audit_details,
        )
        return True
    except Exception as exc:  # noqa: BLE001 - defensive, and never fatal
        logger.warning("auth audit failed (%s): %s", outcome, exc)
        return False


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


def _refresh_ttl_seconds() -> int:
    raw = os.environ.get(AUTH_REFRESH_TTL_ENV)
    if not raw:
        return DEFAULT_REFRESH_TTL_SECONDS
    try:
        value = int(float(raw))
    except (TypeError, ValueError):
        logger.warning(
            "%s=%r is not a number -- using %d",
            AUTH_REFRESH_TTL_ENV, raw, DEFAULT_REFRESH_TTL_SECONDS,
        )
        return DEFAULT_REFRESH_TTL_SECONDS
    return max(MIN_REFRESH_TTL_SECONDS, min(MAX_REFRESH_TTL_SECONDS, value))


_HUMAN_ID_PREFIX = "human:"


def _normalize_principal(principal: str) -> str:
    """把令牌 ``sub`` 归一成账号树 / 身份库使用的裸主体名。

    同一个主体有两种写法：``POST /v1/auth/login`` 签裸名（``liuhao``），
    ``scripts/issue_console_token.py`` 签身份 id（``human:liuhao``，即
    ``IdentityManager`` 的 ``identity_id`` 生成规则）。兼容逻辑只此一份，
    身份查询与账号树查询都调用它，避免两处各写一遍再漂移。
    """
    pid = str(principal or "").strip()
    if pid.startswith(_HUMAN_ID_PREFIX) and len(pid) > len(_HUMAN_ID_PREFIX):
        return pid[len(_HUMAN_ID_PREFIX):]
    return pid


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
        _normalize_principal(principal)
    )
    if identity is None or not is_human_identity(identity):
        return None
    return identity


def authenticate(
    principal: str,
    secret: str,
    *,
    client: str = "web",
    totp_code: Optional[str] = None,
) -> Dict[str, Any]:
    """Verify a credential and mint an access token.

    Raises :class:`AuthError` (``status_code`` 400 / 401 / 403 / 429) on any
    failure. Kept free of HTTP so it is unit-testable directly.

    ``totp_code`` 是双重验证的第二步（验证器 6 位验证码或一张备用码）：
    账号开启了 TOTP 时，没有它回 401 ``TOTP_REQUIRED``（前端据此弹出
    第二步输入框），错码回 401 ``TOTP_INVALID``；备用码通过即消费一张。
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

    node = _account_node(pid)
    if node is not None and node.status != "active":
        # 待批准 / 停用 / 删除的账号：登录阶段就拒绝（与上面非人类身份分支
        # 同口径、同业务码、同中性文案）。之前这里只查了“没有人类身份”的
        # 分支，自助注册账号有人类身份就直接绕过去了——2026-11-14 新规矩
        # （注册先等批准）要求这里也拦住。
        _audit(
            "login_denied",
            pid,
            {"reason": "account_not_active", "client": client_value},
        )
        raise AuthError(
            403, {"code": "ACCOUNT_UNAVAILABLE", "message": "该账号当前不可用"}
        )

    permissions = sorted(identity.permissions)
    scope = _effective_scope(identity)

    # 双重验证第二步（见 src/gateway/totp.py）：密码通过后才查 TOTP，
    # 避免用"要不要验证码"反推账号是否存在。
    second_factor: Optional[str] = None
    backup_remaining: Optional[int] = None
    try:
        from .totp import (
            TotpError,
            TotpStoreError,
            is_totp_enabled,
            verify_login_second_factor,
        )
    except Exception as exc:  # noqa: BLE001 - TOTP 模块坏了不能当作"没开验证"
        logger.error("TOTP 模块加载失败，登录拒绝：%s", exc)
        raise AuthError(503, "服务暂时不可用，请稍后重试") from exc
    try:
        totp_enabled = is_totp_enabled(pid)
    except TotpStoreError as exc:
        raise AuthError(503, "服务暂时不可用，请稍后重试") from exc
    if totp_enabled:
        code = (totp_code or "").strip()
        if not code:
            _register_failure(throttle_key)
            _audit(
                "login_denied",
                pid,
                {"reason": "totp_required", "client": client_value},
            )
            raise AuthError(
                401,
                {
                    "code": "TOTP_REQUIRED",
                    "message": "该账号开启了双重验证，请输入验证器上的 6 位验证码，也可以用备用码代替。",
                    "totp_required": True,
                },
            )
        try:
            second_factor, backup_remaining = verify_login_second_factor(pid, code)
        except TotpStoreError as exc:
            raise AuthError(503, "服务暂时不可用，请稍后重试") from exc
        except TotpError:
            _register_failure(throttle_key)
            _audit(
                "login_denied",
                pid,
                {"reason": "totp_invalid", "client": client_value},
            )
            raise AuthError(
                401,
                {"code": "TOTP_INVALID", "message": "验证码不正确或已使用过，请重新输入"},
            ) from None

    pair = _issue_token_pair(pid, identity, client_value, permissions, scope)
    _clear_failures(throttle_key)
    login_details: Dict[str, Any] = {
        "client": client_value,
        "scope": scope,
        "jti": pair["access_jti"],
    }
    if second_factor:
        login_details["second_factor"] = second_factor
        if backup_remaining is not None:
            login_details["backup_codes_remaining"] = backup_remaining
    written = _audit(
        "login_success",
        pid,
        login_details,
    )
    response = {
        **pair,
        "principal": pid,
        "display_name": (identity.metadata or {}).get("display_name"),
        "scope": scope,
        "permissions": permissions,
        "client": client_value,
        # 旧版曾把恢复码以明文落盘：迁移后提示用户在登录状态下换新（旧码
        # 若在明文窗口期被读取过，只有重新生成才能止损）。
        "recovery_rotate_recommended": _recovery_rotate_recommended(),
        **_account_is_root(pid),
    }
    if second_factor:
        response["second_factor"] = second_factor
        if backup_remaining is not None:
            response["backup_codes_remaining"] = backup_remaining
    if not written:
        # 登录照常成功（令牌已签发）；审计写失败只追加可读提示，不改契约。
        logger.warning("审计未写入：登录已完成但未留下审计记录 principal=%s", pid)
        response["audit_written"] = False
        response["audit_warning"] = AUDIT_NOT_WRITTEN_MESSAGE
    return response


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


def _issue_token_pair(principal, identity, client, permissions, scope) -> Dict[str, Any]:
    """签发 access + refresh 令牌对（0.4.6 统一口径）。

    access 15 分钟（``_ttl_seconds()``），refresh 7 天（``_refresh_ttl_seconds()``）。
    refresh 令牌上同样带 roles/permissions/scope/account_epoch，刷新时可
    凭 epoch 发现权限变更并拒绝续期，而不是拿着旧权限一直续下去。
    """
    from ..security.jwt_handler import TokenType, get_jwt_handler

    handler = get_jwt_handler()
    metadata = {
        "kind": "human",
        "client": client,
        "scope": scope,
        # 账号树 epoch：权限/停用/Key 归属变化会 +1，刷新时比对，旧 refresh
        # 因此"下一次刷新即失效"，不需要等重新登录。
        "account_epoch": _account_epoch(principal),
    }
    access_token, access_payload = handler.create_token(
        subject=principal,
        token_type=TokenType.ACCESS,
        scopes=["console"],
        roles=sorted(identity.permissions),
        permissions=permissions,
        ttl=_ttl_seconds(),
        metadata=dict(metadata),
        device_id=client,
    )
    refresh_token, refresh_payload = handler.create_token(
        subject=principal,
        token_type=TokenType.REFRESH,
        scopes=["console"],
        roles=sorted(identity.permissions),
        permissions=permissions,
        ttl=_refresh_ttl_seconds(),
        metadata=dict(metadata),
        device_id=client,
    )
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "expires_in": access_payload.exp - access_payload.iat,
        "expires_at": access_payload.exp,
        "refresh_expires_in": refresh_payload.exp - refresh_payload.iat,
        "refresh_expires_at": refresh_payload.exp,
        "access_jti": access_payload.jti,
        "refresh_jti": refresh_payload.jti,
        "token_policy": "access_15m_refresh_7d",
    }


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


def _account_is_root(principal: str) -> Dict[str, Any]:
    """本人是否为账号树根，以及能否自助管理自己的恢复码。

    只回答"是不是根"和"能不能自助补发恢复码"这两个布尔值：前端做入口裁剪
    够用，但不返回 ``parent_account_id`` / ``path`` / ``depth`` —— 子账号
    不应从任何接口得知自己是被谁创建的、自己在第几层。

    ``can_rotate_recovery`` 与内核 ``_can_hold_recovery`` 同口径：根账号、
    以及登录页自助注册的账号（没有管理员可代发激活码）为真；管理员创建的
    成员为假（由管理员重置密码）。

    只有**完全没有账号树**的老部署 / 单机令牌才按独立根处理。账号树一旦
    存在，树外主体的令牌（例如内部工具直接签出的服务/测试令牌、或账号树
    被回滚后残留的旧令牌）一律按非根回答：``is_root`` 是控制台渲染主账号
    入口的开关，fail-open 会让这类令牌被当成 boss 渲染。
    """
    try:
        from ..kernels.identity import get_identity_manager

        manager = get_identity_manager()
        # 账号树 key 是裸主体名；令牌 sub 可能是 human:<name>（脚本签发），
        # 归一化后再查，否则根账号会被误报成树外主体。
        node = manager.get_account(_normalize_principal(principal))
        tree_exists = bool(manager.account_nodes())
    except Exception:  # noqa: BLE001 - 位置信息不得阻断登录/自述，且须 fail-closed
        return {"is_root": False, "is_owner": False, "can_rotate_recovery": False}
    if node is not None:
        return {
            "is_root": bool(node.is_root),
            # Owner 即账号树根：与 is_root 同值，只是用产品语言命名，
            # 供控制台按 Owner 裁剪入口（授权判定仍在后端，不靠该字段）。
            "is_owner": bool(node.is_root),
            "can_rotate_recovery": bool(
                node.is_root
                or (node.source == "self_service" and node.is_active)
            ),
        }
    standalone = not tree_exists
    return {
        "is_root": standalone,
        "is_owner": standalone,
        "can_rotate_recovery": standalone,
    }


def issue_token_for(principal: str, client: str = "web") -> Dict[str, Any]:
    """Mint a token for an already-authenticated principal.

    Used by activation / recovery flows: the caller has just proven possession
    of a one-time code, so no credential record is required yet.
    """
    identity = _human_identity(principal)
    if identity is None:
        raise AuthError(403, "该账号不是可登录的账号")
    permissions = sorted(identity.permissions)
    scope = _effective_scope(identity)
    pair = _issue_token_pair(principal, identity, client, permissions, scope)
    return {
        **pair,
        "principal": principal,
        "display_name": (identity.metadata or {}).get("display_name"),
        "scope": scope,
        "permissions": permissions,
        "client": client,
        **_account_is_root(principal),
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
    totp_code: Optional[str] = Field(
        default=None,
        max_length=64,
        description="双重验证第二步：验证器 6 位验证码或一张备用码；未开启双重验证的账号忽略",
    )


@router.post("/auth/login", response_model=Dict[str, Any])
async def auth_login(req: LoginRequest) -> Dict[str, Any]:
    """Exchange ``principal`` + ``secret`` for a bearer <redacted>

    ``authenticate()`` 是同步阻塞调用（PBKDF2 + SQLite + 审计写），这里用
    ``asyncio.to_thread`` 把它移出事件循环，并用请求级预算
    （``login_timeout_seconds()``，默认 30 秒）包住等待：满载时线程池排队
    再久也不会无限挂起，超时回 504「登录超时，请重试」（可重试的错误，
    不是让用户重启）。``AuthError`` 原样转 HTTPException。
    """
    timeout = login_timeout_seconds()
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(
                authenticate,
                req.principal,
                req.secret,
                client=req.client,
                totp_code=req.totp_code,
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "evt=auth.login.timeout principal=%s timeout_seconds=%s",
            str(req.principal or "")[:32],
            timeout,
        )
        raise HTTPException(
            status_code=504,
            detail={"code": "LOGIN_TIMEOUT", "message": "登录超时，请重试"},
        ) from None
    except AuthError as exc:
        raise HTTPException(
            status_code=exc.status_code, detail=exc.detail, headers=exc.headers
        ) from exc


class ThrottleUnlockRequest(BaseModel):
    principal: str = Field(..., min_length=1, max_length=128, description="要解除登录限制的账号")


def _require_owner(
    context: AccountContext = Depends(require_account_principal),
) -> AccountContext:
    """Owner 门禁（延迟导入 ``owner``，避免 auth -> owner -> accounts -> auth
    的顶层循环导入；判定逻辑与 ``owner.require_owner`` 完全同一份）。"""
    from .owner import require_owner

    return require_owner(context)


@router.post("/auth/throttle/unlock", response_model=Dict[str, Any])
def auth_throttle_unlock(
    req: ThrottleUnlockRequest,
    context: AccountContext = Depends(_require_owner),
) -> Dict[str, Any]:
    """Owner 专属：解除某账号的登录节流（只清该账号）。

    不走密码校验，所以不受节流本身影响；只清请求里那一个账号的计数，
    不清全站。解锁写审计 ``throttle.unlocked``；审计写失败时操作照常
    完成，响应里带 ``audit_written=false`` + ``audit_warning``。
    """
    pid = str(req.principal or "").strip()
    had = throttle_failure_count(pid)
    _clear_failures(pid.lower())
    written = _audit(
        "throttle_unlocked",
        pid,
        {
            "action": "throttle.unlocked",
            "target": pid,
            "unlocked_by": getattr(context, "principal", ""),
            "cleared_failures": had,
        },
        event_type="LOGIN_THROTTLE_UNLOCKED",
    )
    response: Dict[str, Any] = {
        "principal": pid,
        "unlocked": True,
        "cleared_failures": had,
    }
    if not written:
        logger.warning("审计未写入：节流解锁已完成但未留下审计记录 principal=%s", pid)
        response["audit_written"] = False
        response["audit_warning"] = AUDIT_NOT_WRITTEN_MESSAGE
    return response


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
        **_account_is_root(principal),
    }


class RefreshRequest(BaseModel):
    refresh_token: str = Field(..., min_length=10, description="7 天有效的刷新令牌")


@router.post("/auth/refresh", response_model=Dict[str, Any])
def auth_refresh(req: RefreshRequest) -> Dict[str, Any]:
    """用 refresh 令牌换一对新令牌（旧 refresh 轮换作废）。

    0.4.6 统一口径的一部分：access 只有 15 分钟，前端凭 refresh 在后台
    续期，用户一天不用反复输密码。刷新时重查账号树 epoch 与 human 身份：
    权限变了、账号停用了，旧 refresh 续不出来，必须重新登录。
    """
    from ..security import get_jwt_handler

    handler = get_jwt_handler()
    try:
        payload = handler.validate_refresh_token(req.refresh_token)
    except Exception as exc:  # noqa: BLE001 - 刷新令牌无效/过期/已吊销都一样回 401
        logger.warning("refresh rejected: %s", type(exc).__name__)
        raise HTTPException(status_code=401, detail="登录信息已失效，请重新登录") from exc
    principal = str(getattr(payload, "sub", "") or "")
    identity = _human_identity(principal)
    if identity is None:
        raise HTTPException(status_code=403, detail="该账号不是可登录的账号")
    metadata = getattr(payload, "metadata", None) or {}
    if int(metadata.get("account_epoch", 0) or 0) != _account_epoch(principal):
        handler.revoke_token(req.refresh_token)
        raise HTTPException(
            status_code=401, detail="账号权限已变更，请重新登录"
        )
    pair = handler.refresh_access_token(req.refresh_token, rotate=True)
    _audit("token_refreshed", principal, {"client": metadata.get("client", "web")})
    return {
        **pair,
        "principal": principal,
        "token_policy": "access_15m_refresh_7d",
    }


@router.post("/auth/logout", response_model=Dict[str, Any])
def auth_logout(
    body: Optional[Dict[str, Any]] = Body(default=None),
    x_liuhao_token: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
) -> Dict[str, Any]:
    """Revoke the presented token. Idempotent: no token is still ``logged_out``.

    Reads the token through the same two headers as the gate (see
    ``policy.TOKEN_HEADER``): revoking the *gateway's* token instead of the
    caller's would report a logout while leaving the real session live.

    请求体里若带了 ``refresh_token``，一并吊销（否则登出后 refresh 还能
    再换出新 access，等于没登出）。没带时只吊销 access（兼容老客户端）。
    """
    token = extract_bearer_token(x_liuhao_token, authorization)
    refresh_token: Optional[str] = None
    if isinstance(body, dict):
        raw = body.get("refresh_token")
        refresh_token = str(raw).strip() if raw else None
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
        refresh_revoked = False
        if refresh_token:
            try:
                refresh_revoked = bool(handler.revoke_token(refresh_token))
            except Exception:  # noqa: BLE001 - refresh 吊销失败不掩盖主结果
                refresh_revoked = False
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error("logout revoke failed: %s", exc)
        revoked = False
        refresh_revoked = False
        principal = ""
    _audit("logout", principal, {"revoked": revoked, "refresh_revoked": refresh_revoked})
    return {"logged_out": True, "revoked": revoked, "refresh_revoked": refresh_revoked}


@router.post("/auth/logout-all", response_model=Dict[str, Any])
def auth_logout_all(
    body: Optional[Dict[str, Any]] = Body(default=None),
    x_liuhao_token: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
) -> Dict[str, Any]:
    """一键踢掉其他所有登录：吊销本账号除当前这次登录外的全部令牌。

    当前 access 令牌（与请求体里带的 refresh 令牌，如果有的话）不受影响，
    继续能用；别的电脑/手机上的登录会被吊销，下次请求即 401。
    只覆盖本服务启动后签发的令牌（追踪索引只记这些），响应里如实说明
    这个范围，不假装覆盖了全部历史令牌。
    """
    token = extract_bearer_token(x_liuhao_token, authorization)
    if not token:
        raise HTTPException(status_code=401, detail="登录信息已失效，请重新登录")
    refresh_token: Optional[str] = None
    if isinstance(body, dict):
        raw = body.get("refresh_token")
        refresh_token = str(raw).strip() if raw else None
    try:
        from ..security import get_jwt_handler

        handler = get_jwt_handler()
        try:
            payload = handler.decode_signature_only(token, verify_exp=False)
        except Exception as exc:  # noqa: BLE001 - 验不过的令牌不能当"当前登录"
            logger.warning("logout-all rejected unverifiable token: %s", exc)
            raise HTTPException(status_code=401, detail="登录信息已失效，请重新登录")
        principal = str(getattr(payload, "sub", "") or "")
        keep = {str(getattr(payload, "jti", "") or "")}
        if refresh_token:
            try:
                refresh_payload = handler.decode_signature_only(
                    refresh_token, verify_exp=False
                )
                if str(getattr(refresh_payload, "sub", "") or "") == principal:
                    keep.add(str(getattr(refresh_payload, "jti", "") or ""))
            except Exception:  # noqa: BLE001 - 坏的 refresh 不阻断主操作
                pass
        keep.discard("")
        revoked = handler.revoke_all_user_tokens_except(principal, keep)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error("logout-all revoke failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="踢掉其他登录失败：吊销服务暂时不可用，其他登录还在，请稍后重试",
        ) from exc
    _audit("logout_all", principal, {"revoked": revoked})
    return {
        "logged_out_others": True,
        "revoked": revoked,
        "kept_current": True,
        "scope_note": "仅覆盖本服务启动后签发的令牌；更早签发的令牌不在追踪范围内。",
    }


def _recovery_rotate_recommended() -> bool:
    """旧明文恢复码迁移后，是否应提示用户登录后重新生成。"""
    try:
        from .recovery_handoff import rotate_recommended

        return rotate_recommended()
    except Exception:  # noqa: BLE001 - 提示失败绝不影响登录/配置
        return False


def _request_is_local(request: Request) -> bool:
    """请求是否来自本机回环（已配对的手机中继也在此列）。"""
    try:
        from .accounts import request_is_local

        return bool(request_is_local(request))
    except Exception:  # noqa: BLE001 - 判定失败按"非本机"处理，宁严不宽
        return False


def _remote_recovery_enabled() -> bool:
    """远程恢复码重置是否被显式开启。"""
    try:
        from .recovery_handoff import remote_recovery_enabled

        return bool(remote_recovery_enabled())
    except Exception:  # noqa: BLE001
        return False


@router.get("/auth/config", response_model=Dict[str, Any])
def auth_config(request: Request) -> Dict[str, Any]:
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
            # 本请求是否来自本机（浏览器/已配对的手机中继）：决定登录页能否
            # 直接展示“在本机生成恢复码”。手机直连局域网时为 false。
            "local": _request_is_local(request),
            # 远程重置是否被运维显式开启（LIUHAO_ALLOW_REMOTE_RECOVERY）。
            # 登录页据此给出**真实可用**的自助路径，而不是承诺做不到的事。
            "remote_allowed": _remote_recovery_enabled(),
        },
        "migration": {"allowed": not root_exists},
        "client_modes": list(CLIENT_MODES),
        "token": {"ttl_seconds": _ttl_seconds()},
        "message": message,
    }
