"""账号凭据包：导出编排与导入认领（gateway 层）。

职责边界
--------
* 身份内核的 :meth:`IdentityManager.build_backup_payload` 负责"身份 + 账号树"；
* 本模块把**凭据记录、模型配置密文、工作区产物**追加进同一份明文清单，再用
  :mod:`src.kernels.identity._backup` 的口令加密封装成可离线保存的 JSON；
* 导入负责口令校验、防重放（bundle_id 回执）、限次与审计，然后调用内核
  :meth:`IdentityManager.apply_backup_payload` 写回。

硬约束（``docs/account-hierarchy.md`` §6）
------------------------------------------
* 口令只来自请求体：**不落盘、不进日志、不进审计 details**；
* ``scope=all`` 仅根账号可用，成员越权一律 403 + 中性文案（真实原因只进审计）；
* 成员 ``scope=self`` 只导出本人：身份、本人凭据、本人 Key 密文、本人产物；
* 导出审计必须写 actor 与成员清单（文件数/字节数同理），不写口令；
* 导入先校验再落盘；失败次数按 ``bundle_id`` 计数，超过上限即锁定，避免把
  导入接口变成口令 oracle。
"""
from __future__ import annotations

import base64
import logging
import os
import sqlite3
import threading
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..kernels.identity import _accounts, _backup, get_identity_manager

logger = logging.getLogger(__name__)

#: 明文清单总量上限（加密前）；超过即 413，避免一次导出/导入打爆内存。
MAX_PAYLOAD_BYTES = 128 * 1024 * 1024
#: 单文件上限：超过的文件跳过并如实记入清单的 ``skipped``，不静默截断。
MAX_FILE_BYTES = 64 * 1024 * 1024
#: 同一个 ``bundle_id`` 允许的口令失败次数。
MAX_IMPORT_ATTEMPTS = 5

_ATTEMPTS_DB_ENV = "LIUHAO_ACCOUNT_BACKUP_DB"
_attempt_lock = threading.Lock()


# --------------------------------------------------------------------------- #
# 口令尝试计数（按 bundle_id，持久化；不依赖进程内存）
# --------------------------------------------------------------------------- #

def _attempts_db_path() -> str:
    explicit = (os.environ.get(_ATTEMPTS_DB_ENV) or "").strip()
    if explicit:
        return explicit
    root = (os.environ.get("LIUHAO_WORKSPACE_ROOT") or "").strip()
    if root:
        return os.path.join(root, "account_backup.sqlite3")
    return os.path.join("data", "account_backup.sqlite3")


def _attempt_connection() -> sqlite3.Connection:
    path = _attempts_db_path()
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.execute(
        "CREATE TABLE IF NOT EXISTS backup_attempts ("
        " bundle_id TEXT PRIMARY KEY,"
        " failures INTEGER NOT NULL DEFAULT 0,"
        " updated_at TEXT NOT NULL DEFAULT ''"
        ")"
    )
    connection.commit()
    return connection


def _attempts_remaining(bundle_id: str) -> int:
    with _attempt_lock, _attempt_connection() as connection:
        row = connection.execute(
            "SELECT failures FROM backup_attempts WHERE bundle_id = ?",
            (bundle_id,),
        ).fetchone()
    failures = int(row[0]) if row else 0
    return MAX_IMPORT_ATTEMPTS - failures


def _bump_attempt(bundle_id: str) -> int:
    """记一次失败并返回剩余次数（0 表示已锁定）。"""
    from src._time import utc_now

    with _attempt_lock, _attempt_connection() as connection:
        connection.execute(
            "INSERT INTO backup_attempts (bundle_id, failures, updated_at) "
            "VALUES (?, 1, ?) "
            "ON CONFLICT(bundle_id) DO UPDATE SET "
            " failures = failures + 1, updated_at = excluded.updated_at",
            (bundle_id, utc_now().isoformat()),
        )
        row = connection.execute(
            "SELECT failures FROM backup_attempts WHERE bundle_id = ?",
            (bundle_id,),
        ).fetchone()
    failures = int(row[0]) if row else MAX_IMPORT_ATTEMPTS
    return max(0, MAX_IMPORT_ATTEMPTS - failures)


def _clear_attempts(bundle_id: str) -> None:
    with _attempt_lock, _attempt_connection() as connection:
        connection.execute(
            "DELETE FROM backup_attempts WHERE bundle_id = ?", (bundle_id,)
        )


# --------------------------------------------------------------------------- #
# 辅助
# --------------------------------------------------------------------------- #

def _account_error(status: int, code: str, message: str) -> _accounts.AccountError:
    return _accounts.AccountError(status, code, message)


def _bundle_error(exc: Exception) -> _accounts.AccountError:
    """把 :class:`BundleError` 转成账号面统一错误；文案保持中性。"""
    if isinstance(exc, _backup.BundleError):
        return _account_error(exc.status_code, exc.code, exc.message)
    return _account_error(400, "BUNDLE_INVALID", "凭据包无效或口令错误")


def _now_iso() -> str:
    from src._time import utc_now

    return utc_now().isoformat()


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _secret_store():
    from .auth import resolve_secret_store

    return resolve_secret_store()


def _model_store():
    from ..ai.model_config import ModelConfigStore

    return ModelConfigStore()


def _artifact_owner_index() -> Dict[str, Any]:
    from .ai_management import AIStateManager

    return AIStateManager().artifact_owner_index()


def _normalise_rel(path: str) -> str:
    return str(path or "").replace("\\", "/").strip().strip("/")


# --------------------------------------------------------------------------- #
# 导出
# --------------------------------------------------------------------------- #

def _collect_files(
    *,
    principals: Sequence[str],
    include_public: bool,
) -> Tuple[List[Dict[str, Any]], int, List[str]]:
    """收集工作区产物；返回 ``(entries, total_bytes, skipped)``。

    成员导出时 ``include_public=False``：公共文件与别人的产物都不带走。
    """
    from src.ai.workspace import workspace_root

    root = workspace_root()
    index = _artifact_owner_index()
    allowed = set(principals)
    entries: List[Dict[str, Any]] = []
    skipped: List[str] = []
    total = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            name for name in dirnames
            if name not in (".git", "__pycache__", "node_modules")
        ]
        for name in filenames:
            absolute = os.path.join(dirpath, name)
            rel = _normalise_rel(os.path.relpath(absolute, root))
            owners = set(index.get(rel, set()))
            if not include_public:
                if not owners or not (owners & allowed):
                    continue
            try:
                stat = os.stat(absolute)
            except OSError:
                skipped.append(rel)
                continue
            if stat.st_size > MAX_FILE_BYTES:
                skipped.append(rel)
                continue
            if total + stat.st_size > MAX_PAYLOAD_BYTES:
                raise _account_error(
                    413, "ACCOUNT_BACKUP_TOO_LARGE", "备份内容过大，无法一次导出"
                )
            try:
                with open(absolute, "rb") as handle:
                    raw = handle.read()
            except OSError:
                skipped.append(rel)
                continue
            entries.append({"path": rel, "size": len(raw), "content": _b64(raw)})
            total += len(raw)
    return entries, total, skipped


def export_bundle(
    *,
    actor: str,
    scope: str,
    passphrase: str,
    subtree: Sequence[str],
    is_root: bool,
) -> Dict[str, Any]:
    """导出加密凭据包。返回前端契约 ``{bundle, scope, exported_at, members...}``。"""
    manager = get_identity_manager()
    wanted = str(scope or "").strip().lower()
    if wanted not in ("all", "self"):
        raise _account_error(400, "ACCOUNT_SCOPE_INVALID", "导出范围无效")
    if wanted == "all" and not is_root:
        # 可见但无权：403 + 中性文案；真实原因（越权导出整棵树）只进审计。
        manager.record_event(
            action="account.backup.export",
            actor=actor,
            target=actor,
            outcome="denied",
            details={"scope": "all", "error_code": "ACCOUNT_SCOPE_DENIED"},
        )
        raise _account_error(403, "ACCOUNT_SCOPE_DENIED", "无权执行该操作")

    identity_part = manager.build_backup_payload(actor, wanted)
    principals = [str(item.get("principal") or "") for item in identity_part["accounts"]]
    principals = [p for p in principals if p]
    include_public = wanted == "all"

    credentials_all = _secret_store().load()
    credentials = {
        principal: record
        for principal, record in credentials_all.items()
        if principal in set(principals)
    }

    model_store = _model_store()
    rows = model_store.export_encrypted_rows(owners=principals)
    if not include_public:
        # 空归属 = 账号体系上线前的历史模型，归根账号；成员不得带走。
        rows = [row for row in rows if str(row.get("owner_principal") or "")]

    key_material: Optional[str] = None
    if include_public:
        raw_key = model_store.export_key_material()
        if raw_key:
            key_material = _b64(raw_key)

    files, total_bytes, skipped = _collect_files(
        principals=principals, include_public=include_public
    )

    members = sorted(
        str(item.get("principal") or "") for item in identity_part["accounts"]
    )
    payload = {
        "format": "liuhao.account.backup.v1",
        "issued_at": _now_iso(),
        "scope": wanted,
        "manifest": {
            "members": members,
            "member_count": len(members),
            "files": len(files),
            "bytes": total_bytes,
            "models": len(rows),
            "credentials": len(credentials),
            "skipped": skipped,
        },
        "identity": identity_part,
        "credentials": credentials,
        "models": {"rows": rows, "key_material": key_material},
        "files": files,
    }

    # 审计写 actor 与成员清单；口令绝不出现在 details 里。
    manager.record_event(
        action="account.backup.export",
        actor=actor,
        target=actor,
        outcome="allowed",
        details={
            "scope": wanted,
            "members": members,
            "files": len(files),
            "bytes": total_bytes,
            "models": len(rows),
            "credentials": len(credentials),
        },
    )

    try:
        sealed = _backup.encrypt_bundle(payload, passphrase)
    except _backup.BundleError as exc:
        raise _bundle_error(exc) from exc

    return {
        "bundle": sealed,
        "scope": wanted,
        "exported_at": payload["issued_at"],
        "members": len(members),
        "manifest": payload["manifest"],
    }


# --------------------------------------------------------------------------- #
# 导入
# --------------------------------------------------------------------------- #

def _restore_files(entries: List[Dict[str, Any]]) -> Dict[str, int]:
    """把备份产物写回工作区；已存在的文件跳过（不覆盖现场数据）。"""
    from src.ai.workspace import WorkspaceViolation, resolve_in_workspace

    restored = 0
    skipped = 0
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        rel = _normalise_rel(str(entry.get("path") or ""))
        if not rel:
            continue
        try:
            absolute = resolve_in_workspace(rel)
        except WorkspaceViolation:
            skipped += 1
            continue
        if os.path.exists(absolute):
            skipped += 1
            continue
        try:
            raw = base64.b64decode(str(entry.get("content") or ""))
        except Exception:  # noqa: BLE001
            skipped += 1
            continue
        try:
            parent = os.path.dirname(absolute)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(absolute, "wb") as handle:
                handle.write(raw)
            restored += 1
        except OSError:
            skipped += 1
    return {"restored": restored, "skipped": skipped}


def import_bundle(*, bundle: Dict[str, Any], passphrase: str) -> Dict[str, Any]:
    """口令校验 → 防重放 → 限次 → 恢复 → 写回执与审计。"""
    manager = get_identity_manager()
    try:
        bundle_id = _backup.bundle_id_of(bundle)
    except _backup.BundleError as exc:
        raise _bundle_error(exc) from exc

    if manager.has_import_receipt(bundle_id):
        raise _account_error(
            409, "ACCOUNT_BACKUP_REPLAYED", "该备份包已被导入，出于安全原因不能重复使用"
        )
    if _attempts_remaining(bundle_id) <= 0:
        raise _account_error(
            429, "ACCOUNT_BACKUP_LOCKED", "尝试次数过多，请更换备份包或稍后再试"
        )
    # 认领只能在"还没有账号树"的全新实例上做。一旦本机已有根账号，任何
    # 成员（甚至匿名）都不能用备份包改写本机账号树 —— 否则子账号可借
    # ``/backup/import`` 绕过 root-only 边界。重放检查在前面，故已导入过的
    # 包仍返回 409 REPLAYED（这是更精确的答案）。
    if manager.account_root() is not None:
        raise _account_error(
            409, "ACCOUNT_ALREADY_INITIALIZED",
            "本机已存在账号树，备份包只能在全新实例上认领，不能用于覆盖现有账号",
        )

    try:
        payload = _backup.decrypt_bundle(bundle, passphrase)
    except _backup.BundleError as exc:
        remaining = _bump_attempt(bundle_id)
        manager.record_event(
            action="account.backup.import",
            actor="anonymous",
            target=bundle_id,
            outcome="denied",
            details={"error_code": exc.code, "attempts_remaining": remaining},
        )
        raise _bundle_error(exc) from exc

    identity_part = payload.get("identity")
    if not isinstance(identity_part, dict):
        _bump_attempt(bundle_id)
        raise _account_error(400, "BUNDLE_INVALID", "凭据包无效或口令错误")

    restored = manager.apply_backup_payload(identity_part)

    credentials = payload.get("credentials") or {}
    credential_count = _secret_store().import_credentials(credentials) if isinstance(
        credentials, dict
    ) else 0

    models = payload.get("models") or {}
    model_rows = models.get("rows") if isinstance(models, dict) else []
    model_count = _model_store().import_encrypted_rows(model_rows) if isinstance(
        model_rows, list
    ) else 0
    raw_key = models.get("key_material") if isinstance(models, dict) else None
    if raw_key:
        try:
            _model_store().import_key_material(base64.b64decode(str(raw_key)))
        except Exception:  # noqa: BLE001 - 主密钥缺失不阻断身份恢复
            logger.warning("could not restore model key material")

    files = payload.get("files") or []
    file_report = _restore_files(files if isinstance(files, list) else [])

    manager.record_import_receipt(bundle_id)
    _clear_attempts(bundle_id)

    members = sorted(
        str(item.get("principal") or "")
        for item in (identity_part.get("accounts") or [])
        if isinstance(item, dict)
    )
    manager.record_event(
        action="account.backup.imported",
        actor="anonymous",
        target=str(restored.get("root_principal") or ""),
        outcome="allowed",
        details={
            "bundle_id": bundle_id,
            "members": members,
            "accounts": restored.get("accounts", 0),
            "credentials": credential_count,
            "models": model_count,
            "files": file_report["restored"],
            "files_skipped": file_report["skipped"],
        },
    )

    return {
        "ok": True,
        "root_principal": restored.get("root_principal"),
        "accounts": restored.get("accounts", 0),
        "credentials": credential_count,
        "models": model_count,
        "files": file_report,
        "manifest": payload.get("manifest") or {},
    }
