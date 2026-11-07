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
import json
import logging
import os
import sqlite3
import tempfile
import threading
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..kernels.identity import _accounts, _backup, get_identity_manager

#: 备份与登录令牌的边界：导出只读，不轮换本机签名密钥。
_TOKEN_POLICY: Dict[str, Any] = {
    "scope": "instance",
    "export_revokes_tokens": False,
    "source_tokens_stay_valid_on_source": True,
    "source_tokens_valid_on_target": False,
    "reason": "登录令牌绑定本实例的签名密钥；导出不轮换密钥，也不把令牌带到别的实例。",
}

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
# 记忆库 / 会话库快照
# --------------------------------------------------------------------------- #

#: 两个库都在工作区之外，必须单独纳入备份；列清单是"能带走什么"的唯一定义。
_MEMORY_TABLES: Dict[str, Tuple[str, ...]] = {
    "account_memory_entries": ("id", "owner", "text", "source", "tags", "created_at"),
    "account_memory_custody": ("owner", "enabled", "updated_at", "updated_by"),
}
_CONVERSATION_TABLES: Dict[str, Tuple[str, ...]] = {
    "conversation_turns": ("seq", "principal_id", "turn", "role", "content", "created_at"),
}
#: 0.1.0 的内核记忆 / 画像库（跨机导入后老记忆必须仍然可读）。
_KERNEL_MEMORY_TABLES: Dict[str, Tuple[str, ...]] = {
    "memory_entries": (
        "key_hash", "entry_id", "key", "value", "tier", "scope", "created_at",
        "expires_at", "tags", "correlation_id", "access_count",
        "last_accessed", "provenance",
    ),
}
_PERSONAL_CONTEXT_TABLES: Dict[str, Tuple[str, ...]] = {
    "profile_preferences": (
        "principal_id", "key", "value_json", "confidence", "source", "updated_at",
    ),
    "profile_facts": ("principal_id", "key", "value_json", "source", "updated_at"),
    "profile_lists": ("principal_id", "kind", "item"),
    "profile_relationships": ("principal_id", "who", "relation"),
    "profile_meta": ("principal_id", "key", "value_json"),
}


def _kernel_memory_db_path() -> str:
    from ..kernels.memory.store import DEFAULT_DB_PATH

    return os.environ.get("MEMORY_DB_PATH") or DEFAULT_DB_PATH


def _personal_context_db_path() -> str:
    from ..ai.personal_context import DEFAULT_DB_PATH

    return os.environ.get("PERSONAL_CONTEXT_DB_PATH") or DEFAULT_DB_PATH


def _kernel_row_owner(row: Dict[str, Any], allowed: Sequence[str]) -> Optional[str]:
    """内核记忆行没有账号列：键里带账号就归该账号，否则归根（仅整树导出）。"""
    key = str(row.get("key") or "")
    for owner in allowed:
        if owner and (f"liuhao-{owner}" in key or f"{owner}:" in key or f"user-{owner}" in key):
            return owner
    return None


def _profile_row_owner(row: Dict[str, Any], allowed: Sequence[str]) -> Optional[str]:
    raw = str(row.get("principal_id") or "")
    stripped = raw[len("user-"):] if raw.startswith("user-") else raw
    if stripped in allowed:
        return stripped
    return None


def _snapshot_tables(
    db_path: str, tables: Dict[str, Tuple[str, ...]]
) -> Dict[str, List[Dict[str, Any]]]:
    """用 SQLite backup API 取一致快照，再逐表读成行。

    绝不直接拷 ``-wal`` 文件：正在写入的库直接复制会得到撕裂快照；``backup``
    在源库上加共享锁并复制到临时库，读的是同一事务视图。
    """
    empty = {name: [] for name in tables}
    if not db_path or not os.path.exists(db_path):
        return empty
    with tempfile.TemporaryDirectory() as tmp:
        snapshot = os.path.join(tmp, "snapshot.sqlite3")
        source = sqlite3.connect(db_path, timeout=10)
        try:
            target = sqlite3.connect(snapshot)
            try:
                source.backup(target)
            finally:
                target.close()
        finally:
            source.close()
        connection = sqlite3.connect(snapshot)
        connection.row_factory = sqlite3.Row
        try:
            result: Dict[str, List[Dict[str, Any]]] = {}
            for name, columns in tables.items():
                try:
                    rows = connection.execute(
                        f"SELECT {','.join(columns)} FROM {name}"
                    ).fetchall()
                except sqlite3.Error:
                    rows = []
                result[name] = [dict(row) for row in rows]
            return result
        finally:
            connection.close()


def _conversation_owner(principal_id: str, allowed: Sequence[str]) -> Optional[str]:
    """把会话行主体映射回账号。

    网关的会话主体是 ``liuhao-{账号}-{会话名}``（见 ``chat.read_history``），
    不是裸账号名；直接按账号名过滤会把真实对话全部漏掉。
    """
    if principal_id in allowed:
        return principal_id
    prefix = "liuhao-"
    if not principal_id.startswith(prefix):
        return None
    rest = principal_id[len(prefix):]
    for owner in allowed:
        if rest.startswith(owner + "-"):
            return owner
    return None


def _collect_stores(
    principals: Sequence[str], include_public: bool = False
) -> Dict[str, Any]:
    """收集记忆库与会话库中**归属这些账号**的行（含来源清单）。

    先把 0.1.0 的 ``memory_store.db`` / ``personal_context.db`` 幂等映射进
    账号记忆表，再按账号过滤快照；内核库本身也原样带进备份，保证"迁移后
    统一"与"导入后仍可重读"两条路径都成立。
    """
    from ..ai.conversation_store import get_conversation_store
    from . import account_memory
    from .account_memory import db_path as memory_db_path

    allowed = set(principals)
    # 迁移失败会让导出显式失败（而不是静默少带一段记忆）；源库保持原样，可重试。
    migration = account_memory.ensure_legacy_memory_migrated()
    memory_rows = _snapshot_tables(memory_db_path(), _MEMORY_TABLES)
    entries = [
        row for row in memory_rows.get("account_memory_entries", [])
        if str(row.get("owner") or "") in allowed
    ]
    custody = [
        row for row in memory_rows.get("account_memory_custody", [])
        if str(row.get("owner") or "") in allowed
    ]

    conversation_rows = _snapshot_tables(
        get_conversation_store().db_path, _CONVERSATION_TABLES
    )
    turns = [
        row for row in conversation_rows.get("conversation_turns", [])
        if _conversation_owner(str(row.get("principal_id") or ""), principals)
    ]
    conversation_owners = sorted(
        {
            owner
            for row in turns
            if (owner := _conversation_owner(
                str(row.get("principal_id") or ""), principals
            ))
        }
    )

    kernel_rows = _snapshot_tables(
        _kernel_memory_db_path(), _KERNEL_MEMORY_TABLES
    ).get("memory_entries", [])
    if not include_public:
        kernel_rows = [
            row for row in kernel_rows
            if _kernel_row_owner(row, principals)
        ]
    profile_rows = _snapshot_tables(
        _personal_context_db_path(), _PERSONAL_CONTEXT_TABLES
    )
    profile_counts: Dict[str, int] = {}
    for table, rows in profile_rows.items():
        if not include_public:
            rows = [row for row in rows if _profile_row_owner(row, principals)]
            profile_rows[table] = rows
        profile_counts[table] = len(rows)

    return {
        "payload": {
            "memory": {"entries": entries, "custody": custody},
            "conversations": {"turns": turns},
            "kernel_memory": {"entries": kernel_rows},
            "personal_context": profile_rows,
        },
        "manifest": {
            "account_memory": {
                "db": "account_memory.sqlite3",
                "entries": len(entries),
                "custody": len(custody),
                "owners": sorted({str(row.get("owner") or "") for row in entries}),
            },
            "conversation": {
                "db": "conversation_store.db",
                "turns": len(turns),
                "principals": conversation_owners,
            },
            "kernel_memory": {
                "db": "memory_store.db",
                "entries": len(kernel_rows),
                "migrated": migration.get("entries", 0),
            },
            "personal_context": {
                "db": "personal_context.db",
                "tables": profile_counts,
            },
        },
    }


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
    stores = _collect_stores(principals, include_public)
    store_bytes = len(
        json.dumps(stores["payload"], ensure_ascii=False).encode("utf-8")
    )
    if total_bytes + store_bytes > MAX_PAYLOAD_BYTES:
        raise _account_error(
            413, "ACCOUNT_BACKUP_TOO_LARGE", "备份内容过大，无法一次导出"
        )
    total_bytes += store_bytes
    memory_manifest = stores["manifest"]["account_memory"]
    conversation_manifest = stores["manifest"]["conversation"]
    kernel_manifest = stores["manifest"]["kernel_memory"]
    profile_manifest = stores["manifest"]["personal_context"]

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
            # 带上哪些库、各多少条：导入方据此核对，用户侧也能一眼看懂。
            "stores": {
                "account_memory": memory_manifest,
                "conversation": conversation_manifest,
                "kernel_memory": kernel_manifest,
                "personal_context": profile_manifest,
            },
        },
        "identity": identity_part,
        "credentials": credentials,
        "models": {"rows": rows, "key_material": key_material},
        "files": files,
        "memory": stores["payload"]["memory"],
        "conversations": stores["payload"]["conversations"],
        "kernel_memory": stores["payload"]["kernel_memory"],
        "personal_context": stores["payload"]["personal_context"],
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
            "memory": memory_manifest["entries"],
            "conversations": conversation_manifest["turns"],
            "kernel_memory": kernel_manifest["entries"],
            "personal_context": profile_manifest["tables"],
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
        "token_policy": dict(_TOKEN_POLICY),
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


def _restore_memory(memory: Any) -> Dict[str, int]:
    """把记忆行写回本机记忆库：按主键合并，**已存在的行永不覆盖**。

    * ``account_memory_entries`` 以 ``id`` 为主键 → ``INSERT OR IGNORE``；
    * ``account_memory_custody`` 以 ``owner`` 为主键 → 已存在即保留现场值；
    * 坏行（缺 id/owner/text）计数为 ``skipped``，不猜、不补默认值。
    """
    from . import account_memory

    report = {"imported": 0, "skipped": 0, "custody_imported": 0, "custody_skipped": 0}
    if not isinstance(memory, dict):
        return report
    entries = memory.get("entries") or []
    custody = memory.get("custody") or []
    with account_memory._connect() as connection:  # noqa: SLF001 - 同包共享库连接
        for row in entries if isinstance(entries, list) else []:
            if not isinstance(row, dict):
                report["skipped"] += 1
                continue
            values = (
                str(row.get("id") or ""),
                str(row.get("owner") or ""),
                str(row.get("text") or ""),
                str(row.get("source") or ""),
                str(row.get("tags") or "[]"),
                str(row.get("created_at") or ""),
            )
            if not values[0] or not values[1] or not values[2]:
                report["skipped"] += 1
                continue
            cursor = connection.execute(
                "INSERT OR IGNORE INTO account_memory_entries "
                "(id, owner, text, source, tags, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                values,
            )
            if cursor.rowcount:
                report["imported"] += 1
            else:
                report["skipped"] += 1
        for row in custody if isinstance(custody, list) else []:
            if not isinstance(row, dict) or not str(row.get("owner") or ""):
                report["custody_skipped"] += 1
                continue
            cursor = connection.execute(
                "INSERT OR IGNORE INTO account_memory_custody "
                "(owner, enabled, updated_at, updated_by) VALUES (?, ?, ?, ?)",
                (
                    str(row.get("owner") or ""),
                    1 if row.get("enabled") else 0,
                    str(row.get("updated_at") or ""),
                    str(row.get("updated_by") or ""),
                ),
            )
            if cursor.rowcount:
                report["custody_imported"] += 1
            else:
                report["custody_skipped"] += 1
        connection.commit()
    return report


def _restore_conversations(conversations: Any) -> Dict[str, int]:
    """把对话追加回本机会话库：同内容同轮次视为已存在，不重复写入。

    ``seq`` 由本机自增，不携带源实例行号（避免与现场数据主键冲突）。
    """
    from ..ai.conversation_store import get_conversation_store

    report = {"imported": 0, "skipped": 0}
    if not isinstance(conversations, dict):
        return report
    turns = conversations.get("turns") or []
    store = get_conversation_store()
    connection = sqlite3.connect(store.db_path, timeout=10)
    try:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS conversation_turns ("
            " seq INTEGER PRIMARY KEY AUTOINCREMENT,"
            " principal_id TEXT NOT NULL,"
            " turn INTEGER NOT NULL,"
            " role TEXT NOT NULL,"
            " content TEXT NOT NULL,"
            " created_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))"
            ")"
        )
        for row in turns if isinstance(turns, list) else []:
            if not isinstance(row, dict):
                report["skipped"] += 1
                continue
            principal_id = str(row.get("principal_id") or "")
            role = str(row.get("role") or "")
            content = str(row.get("content") or "")
            if not principal_id or not role:
                report["skipped"] += 1
                continue
            existing = connection.execute(
                "SELECT 1 FROM conversation_turns WHERE principal_id = ? "
                "AND turn = ? AND role = ? AND content = ? LIMIT 1",
                (principal_id, int(row.get("turn") or 0), role, content),
            ).fetchone()
            if existing is not None:
                report["skipped"] += 1
                continue
            connection.execute(
                "INSERT INTO conversation_turns "
                "(principal_id, turn, role, content, created_at) VALUES (?, ?, ?, ?, ?)",
                (
                    principal_id,
                    int(row.get("turn") or 0),
                    role,
                    content,
                    float(row.get("created_at") or 0) or 0,
                ),
            )
            report["imported"] += 1
        connection.commit()
    finally:
        connection.close()
    return report


def _restore_kernel_memory(kernel_memory: Any) -> Dict[str, int]:
    """把 0.1.0 内核记忆行写回本机 ``memory_store.db``（按 ``key_hash`` 去重）。

    导入完成后调用方会再跑一次 ``ensure_legacy_memory_migrated(force=True)``，
    把这些行映射进账号记忆表 —— 这样"跨机导入后老记忆可见"不依赖导出端
    是否已完成迁移。
    """
    report = {"imported": 0, "skipped": 0}
    if not isinstance(kernel_memory, dict):
        return report
    rows = kernel_memory.get("entries") or []
    if not isinstance(rows, list):
        return report
    path = _kernel_memory_db_path()
    connection = sqlite3.connect(path, timeout=10)
    try:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS memory_entries (
                key_hash TEXT PRIMARY KEY, entry_id TEXT NOT NULL,
                key TEXT NOT NULL, value TEXT NOT NULL,
                tier TEXT NOT NULL, scope TEXT NOT NULL,
                created_at TEXT NOT NULL, expires_at TEXT,
                tags TEXT, correlation_id TEXT NOT NULL,
                access_count INTEGER NOT NULL DEFAULT 0,
                last_accessed TEXT, provenance TEXT
            );
            """
        )
        for row in rows:
            if not isinstance(row, dict):
                report["skipped"] += 1
                continue
            key_hash = str(row.get("key_hash") or "")
            entry_id = str(row.get("entry_id") or "")
            key = str(row.get("key") or "")
            value = str(row.get("value") or "")
            if not key_hash or not entry_id or not key or not value:
                report["skipped"] += 1
                continue
            cursor = connection.execute(
                "INSERT OR IGNORE INTO memory_entries "
                "(key_hash, entry_id, key, value, tier, scope, created_at, "
                " expires_at, tags, correlation_id, access_count, last_accessed,"
                " provenance) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    key_hash, entry_id, key, value,
                    str(row.get("tier") or "PERSISTENT"),
                    str(row.get("scope") or "L1"),
                    str(row.get("created_at") or ""),
                    row.get("expires_at"),
                    row.get("tags"),
                    str(row.get("correlation_id") or ""),
                    int(row.get("access_count") or 0),
                    row.get("last_accessed"),
                    row.get("provenance"),
                ),
            )
            if cursor.rowcount:
                report["imported"] += 1
            else:
                report["skipped"] += 1
        connection.commit()
    finally:
        connection.close()
    return report


def _restore_personal_context(personal_context: Any) -> Dict[str, int]:
    """把画像库行写回本机 ``personal_context.db``（按各表主键去重，不覆盖现场）。"""
    report = {"imported": 0, "skipped": 0}
    if not isinstance(personal_context, dict):
        return report
    path = _personal_context_db_path()
    connection = sqlite3.connect(path, timeout=10)
    try:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS profile_preferences (
                principal_id TEXT NOT NULL, key TEXT NOT NULL,
                value_json TEXT NOT NULL, confidence REAL NOT NULL DEFAULT 0.5,
                source TEXT NOT NULL DEFAULT 'explicit', updated_at REAL NOT NULL,
                PRIMARY KEY (principal_id, key)
            );
            CREATE TABLE IF NOT EXISTS profile_facts (
                principal_id TEXT NOT NULL, key TEXT NOT NULL,
                value_json TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'explicit',
                updated_at REAL NOT NULL, PRIMARY KEY (principal_id, key)
            );
            CREATE TABLE IF NOT EXISTS profile_lists (
                principal_id TEXT NOT NULL, kind TEXT NOT NULL, item TEXT NOT NULL,
                PRIMARY KEY (principal_id, kind, item)
            );
            CREATE TABLE IF NOT EXISTS profile_relationships (
                principal_id TEXT NOT NULL, who TEXT NOT NULL, relation TEXT NOT NULL,
                PRIMARY KEY (principal_id, who)
            );
            CREATE TABLE IF NOT EXISTS profile_meta (
                principal_id TEXT NOT NULL, key TEXT NOT NULL,
                value_json TEXT NOT NULL, PRIMARY KEY (principal_id, key)
            );
            """
        )
        for table, columns in _PERSONAL_CONTEXT_TABLES.items():
            rows = personal_context.get(table) or []
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, dict):
                    report["skipped"] += 1
                    continue
                values = [row.get(column) for column in columns]
                if not str(values[0] or ""):
                    report["skipped"] += 1
                    continue
                placeholders = ",".join("?" for _ in columns)
                cursor = connection.execute(
                    f"INSERT OR IGNORE INTO {table} ({','.join(columns)}) "
                    f"VALUES ({placeholders})",
                    values,
                )
                if cursor.rowcount:
                    report["imported"] += 1
                else:
                    report["skipped"] += 1
        connection.commit()
    finally:
        connection.close()
    return report


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
            "本机已完成初始化，备份包只能在全新实例上导入，不能覆盖现有数据",
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

    # 记忆库 / 会话库在**工作区之外**，必须落回本机对应路径；合并策略：
    # 记忆按主键去重、对话按（主体/轮次/角色/正文）去重，绝不覆盖现场数据。
    memory_report = _restore_memory(payload.get("memory"))
    conversation_report = _restore_conversations(payload.get("conversations"))
    kernel_report = _restore_kernel_memory(payload.get("kernel_memory"))
    profile_report = _restore_personal_context(payload.get("personal_context"))
    # 把刚恢复的内核记忆立即映射进账号记忆表：记忆页/检索/导出不再 200+空。
    migration_report: Dict[str, Any] = {}
    try:
        from . import account_memory

        migration_report = account_memory.ensure_legacy_memory_migrated(force=True)
    except Exception:  # noqa: BLE001 - 已在场账号数据不受影响，下次访问可重试
        logger.warning("legacy memory migration after import failed", exc_info=True)

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
            "memory": memory_report["imported"],
            "memory_skipped": memory_report["skipped"],
            "conversations": conversation_report["imported"],
            "conversations_skipped": conversation_report["skipped"],
            "kernel_memory": kernel_report["imported"],
            "kernel_memory_skipped": kernel_report["skipped"],
            "personal_context": profile_report["imported"],
            "memory_migrated": migration_report.get("entries", 0),
        },
    )

    return {
        "ok": True,
        "root_principal": restored.get("root_principal"),
        "accounts": restored.get("accounts", 0),
        "credentials": credential_count,
        "models": model_count,
        "files": file_report,
        "memory": memory_report,
        "conversations": conversation_report,
        "kernel_memory": kernel_report,
        "personal_context": profile_report,
        "memory_migrated": migration_report.get("entries", 0),
        "manifest": payload.get("manifest") or {},
        "token_policy": dict(_TOKEN_POLICY),
        "notice": (
            "导入完成；本机使用自己的登录签名密钥，其他实例已签发的令牌不能在本机使用。"
            "已有数据按主键合并，不会被覆盖。"
        ),
    }
