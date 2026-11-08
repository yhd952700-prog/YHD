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
import errno
import hashlib
import json
import logging
import os
import sqlite3
import tempfile
import threading
from pathlib import Path
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
#: 备份导出默认只允许本机来源；远程导出必须显式打开并写启动日志。
REMOTE_EXPORT_ENV = "LIUHAO_ALLOW_REMOTE_BACKUP_EXPORT"
_attempt_lock = threading.Lock()


def remote_export_enabled() -> bool:
    """远程备份导出开关（默认关闭）；只认显式的 1/true/yes。"""
    return (os.environ.get(REMOTE_EXPORT_ENV) or "").strip().lower() in (
        "1", "true", "yes", "on",
    )


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


def _collect_employees(
    principals: Sequence[str],
    include_public: bool,
    *,
    legacy_owner: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """导出员工整实体（agent 池/任务/计数器）；成员导出只带本人员工。"""
    from ..ai.employee_store import EmployeeStore

    owners = None if include_public else list(principals)
    try:
        exported = EmployeeStore().export_employees(owners)
    except Exception:  # noqa: BLE001 - 员工库缺失不应阻断身份与记忆导出
        logger.warning("could not export employees", exc_info=True)
        return []
    if include_public:
        return exported
    allowed = set(principals)
    legacy = legacy_owner if legacy_owner in allowed else None
    filtered: List[Dict[str, Any]] = []
    for record in exported:
        owner = str(record.get("owner_account_id") or "")
        # 空归属 = 0.1.0 历史员工，只有根账号本人导出时才视为自己的；
        # 否则成员 self 导出会把根账号的员工一起带走。
        if owner in allowed or (not owner and legacy is not None):
            filtered.append(record)
    return filtered


def _normalise_rel(path: str) -> str:
    return str(path or "").replace("\\", "/").strip().strip("/")


def _is_disk_full(exc: BaseException) -> bool:
    """ENOSPC / Windows ERROR_DISK_FULL：必须中止导入，不能当作"跳过"。"""
    return (
        getattr(exc, "errno", None) == errno.ENOSPC
        or getattr(exc, "winerror", None) in (39, 112)
    )


def _is_storage_full(exc: BaseException) -> bool:
    if _is_disk_full(exc):
        return True
    return "disk is full" in str(exc).strip().lower()


# --------------------------------------------------------------------------- #
# 模型 Key 的跨机保险箱
# --------------------------------------------------------------------------- #
#
# ``api_key_enc`` 绑定本机主密钥，原样搬到新机只会是 ``needs_reentry``。要让
# 用户「换机后原来的 Key 还能用」，Key 必须以某种形式随包走；但明文 API Key
# 绝不入包，所以这里做双层封装：
#
#   * 随机 32 字节数据密钥（DEK）用 AES-GCM 加密 Key 清单；
#   * DEK 再用「备份口令 → PBKDF2」派生的 KEK 包裹。
#
# 包内只有两段密文：口令泄露才会解开 DEK，主密钥始终不出机。导入时口令已由
# ``decrypt_bundle`` 验过，这里只负责解封，再用本机主密钥重新加密落库。

_MODEL_VAULT_FORMAT = "liuhao.model.keyvault.v1"
_MODEL_VAULT_AAD = _MODEL_VAULT_FORMAT.encode("utf-8")


def _seal_model_secrets(
    model_store: Any, rows: Sequence[Dict[str, Any]], passphrase: str
) -> Tuple[Dict[str, Any], int]:
    """把 Key 清单封成保险箱；返回 ``(保险箱, Key 条数)``。失败按未携带处理。"""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    try:
        secrets = model_store.export_plaintext_secrets(list(rows))
    except Exception:  # noqa: BLE001 - 单行不可解密不阻断整包导出
        logger.warning("could not export model credentials for portability", exc_info=True)
        return {}, 0
    if not secrets:
        return {}, 0
    raw = json.dumps(secrets, ensure_ascii=False).encode("utf-8")
    dek = os.urandom(32)
    nonce = os.urandom(12)
    ciphertext = AESGCM(dek).encrypt(nonce, raw, _MODEL_VAULT_AAD)
    salt = os.urandom(16)
    kek = _backup._derive(passphrase, salt)
    wrap_nonce = os.urandom(12)
    wrapped = AESGCM(kek).encrypt(wrap_nonce, dek, _MODEL_VAULT_AAD)
    vault = {
        "format": _MODEL_VAULT_FORMAT,
        "kdf": {
            "algo": _backup.KDF_ALGO,
            "iterations": _backup.KDF_ITERATIONS,
            "salt": _b64(salt),
        },
        "wrap_nonce": _b64(wrap_nonce),
        "wrapped_key": _b64(wrapped),
        "nonce": _b64(nonce),
        "ciphertext": _b64(ciphertext),
    }
    return vault, len(secrets)


def _open_model_secrets(vault: Any, passphrase: str) -> Dict[str, str]:
    """导入端解封：口令 → KEK → DEK → Key 清单。任何失败都按"未携带"处理。"""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if not isinstance(vault, dict) or vault.get("format") != _MODEL_VAULT_FORMAT:
        return {}
    try:
        kdf = vault.get("kdf") or {}
        salt = base64.b64decode(str(kdf.get("salt") or ""))
        kek = _backup._derive(str(passphrase or "").strip(), salt)
        dek = AESGCM(kek).decrypt(
            base64.b64decode(str(vault.get("wrap_nonce") or "")),
            base64.b64decode(str(vault.get("wrapped_key") or "")),
            _MODEL_VAULT_AAD,
        )
        raw = AESGCM(dek).decrypt(
            base64.b64decode(str(vault.get("nonce") or "")),
            base64.b64decode(str(vault.get("ciphertext") or "")),
            _MODEL_VAULT_AAD,
        )
        secrets = json.loads(raw.decode("utf-8"))
    except Exception:  # noqa: BLE001 - 旧包没有该字段；损坏按未携带处理
        logger.warning("could not open model key vault", exc_info=True)
        return {}
    if not isinstance(secrets, dict):
        return {}
    return {
        str(key): str(value)
        for key, value in secrets.items()
        if key and isinstance(value, str) and value
    }


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
#: 知识库向量与内容哈希同样在工作区之外；不带上就等于"换机后知识库全空"。
_KNOWLEDGE_TABLES: Dict[str, Tuple[str, ...]] = {
    "knowledge_vectors": ("doc_id", "embedding", "metadata", "updated_at"),
    "knowledge_document_hashes": ("content_hash", "document_id", "created_at"),
}
#: 工作区根下的内部状态文件：由结构化恢复负责（或刻意不迁移），
#: 不能让它们作为"普通产物"被原样拷贝，否则 SQLite 会带着撕裂的 -wal 快照。
_WORKSPACE_STATE_FILES = frozenset({
    "liuhao_employees.json",
    "liuhao_goals.json",
    "account_memory.sqlite3",
    "account_backup.sqlite3",
})


def _knowledge_db_path() -> str:
    from ..knowledge.vector_store import default_db_path

    return os.environ.get("LIUHAO_KNOWLEDGE_DB") or str(default_db_path())


def _goals_store_path() -> str:
    from .ai_management import AIStateManager

    return AIStateManager._default_goals_path()


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


def _read_json_store(path: str, *, label: str) -> Dict[str, Any]:
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        raise _account_error(
            500, "ACCOUNT_BACKUP_STORE_UNREADABLE",
            f"{label}读取失败，备份已中止（不会漏带数据）",
        ) from exc
    if not isinstance(data, dict):
        raise _account_error(
            500, "ACCOUNT_BACKUP_STORE_UNREADABLE",
            f"{label}结构不合法，备份已中止（不会漏带数据）",
        )
    return data


def _collect_goals(
    principals: Sequence[str],
    include_public: bool,
    *,
    legacy_owner: Optional[str] = None,
) -> Dict[str, Any]:
    """目标（``liuhao_goals.json``）：按 ``owner_account_id`` 过滤；空归属视同根账号。"""
    allowed = set(principals)
    legacy = legacy_owner if legacy_owner in allowed else None
    goals_doc = _read_json_store(_goals_store_path(), label="目标库")
    selected_goals = {}
    for goal_id, record in goals_doc.items():
        if not isinstance(record, dict):
            continue
        owner = str(record.get("owner_account_id") or "")
        if include_public or owner in allowed or (not owner and legacy is not None):
            selected_goals[goal_id] = record
    return {
        "payload": selected_goals,
        "manifest": {"count": len(selected_goals)},
    }


def _collect_knowledge(
    principals: Sequence[str], include_public: bool
) -> Dict[str, Any]:
    """知识库向量/哈希：整树带走全部；成员只带 ``owner_account_id`` 命中的文档。"""
    allowed = set(principals)
    rows = _snapshot_tables(_knowledge_db_path(), _KNOWLEDGE_TABLES)
    vectors: List[Dict[str, Any]] = []
    document_ids = set()
    for row in rows.get("knowledge_vectors", []):
        metadata: Dict[str, Any] = {}
        raw_metadata = row.get("metadata")
        if isinstance(raw_metadata, str) and raw_metadata:
            try:
                parsed = json.loads(raw_metadata)
                if isinstance(parsed, dict):
                    metadata = parsed
            except ValueError:
                metadata = {}
        owner = str(metadata.get("owner_account_id") or "")
        if not include_public and owner not in allowed:
            continue
        vectors.append(row)
        document_id = str(metadata.get("document_id") or "")
        if not document_id:
            from ..knowledge.vector_store import _document_id_of

            document_id = _document_id_of(str(row.get("doc_id") or ""), metadata)
        if document_id:
            document_ids.add(document_id)
    hashes = [
        row for row in rows.get("knowledge_document_hashes", [])
        if str(row.get("document_id") or "") in document_ids
    ]
    digest = hashlib.sha256(
        json.dumps(vectors, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return {
        "payload": {"vectors": vectors, "hashes": hashes},
        "manifest": {
            "documents": len(document_ids),
            "chunks": len(vectors),
            "digest": digest,
        },
    }


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
    from src.backup.backup_manager import (
        _is_sqlite_file,
        _is_sqlite_sidecar,
        _read_sqlite_snapshot,
    )

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
            if rel in _WORKSPACE_STATE_FILES:
                # 员工/目标有结构化分区；记忆/备份尝试计数由各自恢复逻辑负责。
                continue
            if _is_sqlite_sidecar(Path(absolute)):
                # -wal/-shm 是**本机**的连接状态，随包走会把旧页面带到新机，
                # 覆盖掉对主库的修正；主库本身用 VACUUM INTO 取一致快照。
                continue
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
                if _is_sqlite_file(Path(absolute)):
                    # 运行中的 SQLite 不能按字节直拷：主库可能还没 checkpoint，
                    # 把它拷走等于一份撕裂/偏旧的快照。VACUUM INTO 拿一致副本。
                    raw = _read_sqlite_snapshot(Path(absolute))
                else:
                    with open(absolute, "rb") as handle:
                        raw = handle.read()
            except (OSError, sqlite3.Error):
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
    remote_source: bool = False,
) -> Dict[str, Any]:
    """导出加密凭据包。返回前端契约 ``{bundle, scope, exported_at, members...}``。

    ``scope=all`` 带整棵子树的模型 Key 行，但**只以口令保险箱密文形式**入包：
    明文 API Key 永不出现，模型主密钥也不出机（导入端用本机主密钥重新加密）。
    这与"主账号能把整棵树带走"的产品承诺一致，也让换机后成员 Key 开箱即用；
    ``scope=self`` 仍只带本人 Key。
    """
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
    legacy_owner = next(
        (
            str(item.get("principal") or "")
            for item in identity_part["accounts"]
            if not item.get("parent_principal")
        ),
        None,
    )

    # 凭据记录（PBKDF2 哈希，不是明文口令）只进入口令加密的包，并按 scope
    # 限定主体。没有它，导入成功后的新机没人能用原密码登录（N17）。
    # 仍不落地 auth_secrets.json，也不进日志/审计。
    credentials = _secret_store().export_credentials(principals)

    model_store = _model_store()
    # 整树导出带全部成员 Key 行；空归属是 0.1.0 的历史模型，视同根账号所有
    # （``export_encrypted_rows`` 会保留空归属行）。成员导出仍只带本人。
    rows = model_store.export_encrypted_rows(owners=principals)
    if not include_public:
        rows = [row for row in rows if str(row.get("owner_principal") or "")]
    # 成员 Key 只以保险箱密文随包：换机后同一口令解封即可用，包里没有明文。
    member_key_owners = {
        str(row.get("owner_principal") or "")
        for row in rows
        if str(row.get("owner_principal") or "") not in ("", actor)
    }
    member_keys_included = bool(member_key_owners)
    # 主密钥不再随包离机：它能解开**所有** api_key_enc，一旦随整包泄露，
    # 上面的行过滤就形同虚设。回执据此如实标注。
    key_material: Optional[str] = None
    # 明文 API Key 永不入包。整包已有一层口令 AES-GCM；随包的 Key 清单再套
    # 一层「随机 DEK + 口令派生 KEK」保险箱，换机后由同一口令解封、用本机
    # 主密钥重新加密，从而"原来的 Key 还能用"，包内不出现任何明文 Key。
    model_vault, model_key_count = _seal_model_secrets(model_store, rows, passphrase)

    files, total_bytes, skipped = _collect_files(
        principals=principals, include_public=include_public
    )
    stores = _collect_stores(principals, include_public)
    employees = _collect_employees(
        principals, include_public, legacy_owner=legacy_owner
    )
    goals = _collect_goals(
        principals, include_public, legacy_owner=legacy_owner
    )
    knowledge = _collect_knowledge(principals, include_public)
    store_bytes = (
        len(json.dumps(stores["payload"], ensure_ascii=False).encode("utf-8"))
        + len(json.dumps(goals["payload"], ensure_ascii=False).encode("utf-8"))
        + len(json.dumps(knowledge["payload"], ensure_ascii=False).encode("utf-8"))
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
    # 包内的执行日志/迁移标记把路径写进了 JSON 文本；记下源机根目录，导入端
    # 才能像 CLI 一样把 "D:\\LiuHaoData" 类的绝对路径改到新机。这些信息在
    # 口令加密信封内，包外看不到。
    from src.ai.workspace import workspace_root

    source_workspace = workspace_root()
    source_paths = {
        "workspace": source_workspace,
        "data_dir": os.environ.get("LIUHAO_DATA_DIR")
        or os.path.dirname(source_workspace),
    }
    payload = {
        "format": "liuhao.account.backup.v1",
        "issued_at": _now_iso(),
        "scope": wanted,
        "manifest": {
            "members": members,
            "member_count": len(members),
            "files": len(files),
            "bytes": total_bytes,
            "employees": len(employees),
            "goals": goals["manifest"]["count"],
            "knowledge": knowledge["manifest"],
            "models": len(rows),
            "credentials": len(credentials),
            "skipped": skipped,
            # 回执如实说明「带了哪些 Key」：主密钥始终不出机；成员 Key 若存在，
            # 随口令保险箱密文携带（换机可直接使用），包内没有明文。
            "key_scope": "subtree" if include_public else "owner-only",
            "member_keys_included": member_keys_included,
            "key_material_included": False,
            "model_keys_exported": model_key_count,
            # 带上哪些库、各多少条：导入方据此核对，用户侧也能一眼看懂。
            "stores": {
                "account_memory": memory_manifest,
                "conversation": conversation_manifest,
                "kernel_memory": kernel_manifest,
                "personal_context": profile_manifest,
            },
        },
        "source": source_paths,
        "identity": identity_part,
        "credentials": credentials,
        "models": {"rows": rows, "key_material": key_material, "key_vault": model_vault},
        "files": files,
        "employees": employees,
        "goals": goals["payload"],
        "knowledge": knowledge["payload"],
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
            "employees": len(employees),
            "goals": goals["manifest"]["count"],
            "knowledge_documents": knowledge["manifest"]["documents"],
            "models": len(rows),
            "credentials": len(credentials),
            "key_material_included": False,
            "member_keys_included": member_keys_included,
            "issued_at": payload["issued_at"],
            "remote_source": bool(remote_source),
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
        "member_keys_included": member_keys_included,
        "key_material_included": False,
        "notice": (
            (
                (
                    "本包携带整棵子树的账号、员工、记忆与会话；模型 Key 随口令加密"
                    "保险箱密文携带（包内没有明文 API Key），成员 Key 可直接使用。"
                    if member_keys_included
                    else "本包携带整棵子树的账号、员工、记忆与会话；模型 Key 只带"
                    "导出者本人的（口令加密保险箱密文，包内没有明文 API Key），"
                    "本次没有成员 Key 需要携带。"
                )
                if include_public
                else "本包只携带你本人的模型 Key（口令加密保险箱密文，无明文）。"
            )
            + (
                f"工作区里的用户上传/生成文件随包带走（本次 {len(files)} 个"
                + (
                    f"；另有 {len(skipped)} 个文件因超过单文件 64MB 上限未随包，"
                    "已在清单 skipped 中列明"
                    if skipped
                    else ""
                )
                + "）；日志与诊断包不随包（新机会重新生成）。"
            )
            + "模型主密钥不随包离机；登录凭据只以 PBKDF2 哈希随口令加密包迁移。"
            "口令丢失将无法打开备份。"
            "审计记录与审计链校验密钥都不随包：新机的审计从头记录；"
            "若你把原机审计库单独带到新机，新机没有对应密钥，审计链只能做结构检查、"
            "无法用密钥证明内容未被改写。想保留可验证性，请在原机导出审计记录归档，"
            "并保留原机数据目录里的审计链密钥文件（audit_chain.key）。"
        ),
    }


# --------------------------------------------------------------------------- #
# 导入
# --------------------------------------------------------------------------- #

def _restore_employees(payload: Any) -> Dict[str, int]:
    """把员工整实体写回本机员工库；同名员工已存在则跳过。"""
    if not isinstance(payload, list) or not payload:
        return {"restored": 0, "skipped": 0}
    try:
        from ..ai.employee_store import EmployeeStore

        return EmployeeStore().import_employees(payload)
    except (OSError, sqlite3.Error) as exc:
        # 磁盘满必须中止整次导入（身份树尚未认领，清盘后可重试）。
        if _is_storage_full(exc):
            raise
        logger.warning("could not restore employees", exc_info=True)
        return {"restored": 0, "skipped": len(payload)}
    except Exception:  # noqa: BLE001 - 员工库损坏不应阻断身份/记忆恢复
        logger.warning("could not restore employees", exc_info=True)
        return {"restored": 0, "skipped": len(payload)}


def _restore_goals(payload: Any) -> Dict[str, int]:
    """把目标写回 ``liuhao_goals.json``：按 goal_id 合并，已存在的不覆盖。"""
    report = {"restored": 0, "skipped": 0}
    if not isinstance(payload, dict) or not payload:
        return report
    path = _goals_store_path()
    existing = _read_json_store(path, label="目标库")
    changed = False
    for goal_id, record in payload.items():
        if not isinstance(record, dict) or not str(goal_id or ""):
            report["skipped"] += 1
            continue
        if str(goal_id) in existing:
            report["skipped"] += 1
            continue
        existing[str(goal_id)] = record
        report["restored"] += 1
        changed = True
    if changed:
        from src.common.atomic_write import atomic_write_bytes

        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        atomic_write_bytes(
            path, json.dumps(existing, ensure_ascii=False).encode("utf-8")
        )
    return report


def _restore_knowledge(payload: Any) -> Dict[str, int]:
    """把知识库向量与内容哈希写回本机库；主键已存在则跳过。

    恢复后丢弃当前进程缓存的 KnowledgeDocumentService，下一次检索会重新
    从 SQLite 载入（含 FTS 重建），不需要重启网关。
    """
    report = {"chunks": 0, "chunks_skipped": 0, "hashes": 0, "hashes_skipped": 0}
    if not isinstance(payload, dict):
        return report
    vectors = payload.get("vectors") or []
    hashes = payload.get("hashes") or []
    if not isinstance(vectors, list) or not isinstance(hashes, list):
        return report
    path = _knowledge_db_path()
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    try:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS knowledge_vectors (
                doc_id TEXT PRIMARY KEY,
                embedding TEXT NOT NULL,
                metadata TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS knowledge_document_hashes (
                content_hash TEXT PRIMARY KEY,
                document_id TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            """
        )
        for row in vectors:
            if not isinstance(row, dict):
                report["chunks_skipped"] += 1
                continue
            doc_id = str(row.get("doc_id") or "")
            embedding = row.get("embedding")
            if not doc_id or embedding in (None, ""):
                report["chunks_skipped"] += 1
                continue
            if not isinstance(embedding, str):
                embedding = json.dumps(embedding)
            metadata = row.get("metadata")
            if not isinstance(metadata, str):
                metadata = json.dumps(metadata or {})
            cursor = connection.execute(
                "INSERT OR IGNORE INTO knowledge_vectors "
                "(doc_id, embedding, metadata, updated_at) VALUES (?, ?, ?, ?)",
                (
                    doc_id,
                    embedding,
                    metadata,
                    str(row.get("updated_at") or ""),
                ),
            )
            if cursor.rowcount:
                report["chunks"] += 1
            else:
                report["chunks_skipped"] += 1
        for row in hashes:
            if not isinstance(row, dict):
                report["hashes_skipped"] += 1
                continue
            content_hash = str(row.get("content_hash") or "")
            document_id = str(row.get("document_id") or "")
            if not content_hash or not document_id:
                report["hashes_skipped"] += 1
                continue
            cursor = connection.execute(
                "INSERT OR IGNORE INTO knowledge_document_hashes "
                "(content_hash, document_id, created_at) VALUES (?, ?, ?)",
                (
                    content_hash,
                    document_id,
                    str(row.get("created_at") or ""),
                ),
            )
            if cursor.rowcount:
                report["hashes"] += 1
            else:
                report["hashes_skipped"] += 1
        connection.commit()
    finally:
        connection.close()
    if report["chunks"] or report["hashes"]:
        from ..knowledge.documents import reset_knowledge_service

        reset_knowledge_service()
    return report


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
            from src.common.atomic_write import atomic_write_bytes

            # 原子落盘：磁盘满时不会留下半截文件，重试也不会误判"已存在"。
            atomic_write_bytes(absolute, raw)
            restored += 1
        except OSError as exc:
            if _is_disk_full(exc):
                raise
            skipped += 1
    return {"restored": restored, "skipped": skipped}


def _rewrite_restored_source_paths(source: Any) -> int:
    """把执行日志/迁移标记里的源机绝对路径改到本机。

    工件索引已经按列结构化改写；这两个库把路径放在 JSON 文本里，只能解析后
    逐字符串替换。``source`` 是导出时记下的源机根目录（在口令信封内）。
    """
    if not isinstance(source, dict):
        return 0
    from src.ai.workspace import workspace_root
    from src.backup.path_rewrite import rewrite_sqlite_json_column

    old_workspace = str(source.get("workspace") or "")
    old_data = str(source.get("data_dir") or "")
    new_workspace = workspace_root()
    new_data = os.environ.get("LIUHAO_DATA_DIR") or os.path.dirname(new_workspace)
    changed = 0
    try:
        if old_workspace:
            changed += rewrite_sqlite_json_column(
                os.path.join(new_workspace, "liuhao_execution.journal"),
                "journal_events",
                "payload",
                old_workspace,
                new_workspace,
            )
        if old_data:
            changed += rewrite_sqlite_json_column(
                os.path.join(new_workspace, "account_memory.sqlite3"),
                "account_memory_migrations",
                "details",
                old_data,
                new_data,
            )
    except (OSError, sqlite3.Error):
        logger.warning("could not rewrite JSON path fields after import", exc_info=True)
    return changed


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

    # 先恢复"可幂等合并"的数据，最后才认领账号树。磁盘满/存储不可用时，
    # 本机仍保持"未初始化"，清出空间后可以用同一个包重试；不会出现
    # "根账号已建好、数据只导了一半、重试又被 409 挡住"的死局。
    credential_count = 0
    model_count = 0
    secret_count = 0
    file_report = {"restored": 0, "skipped": 0}
    employee_report = {"restored": 0, "skipped": 0}
    goal_report = {"restored": 0, "skipped": 0}
    knowledge_report = {
        "chunks": 0, "chunks_skipped": 0, "hashes": 0, "hashes_skipped": 0,
    }
    memory_report = {"imported": 0, "skipped": 0}
    conversation_report = {"imported": 0, "skipped": 0}
    kernel_report = {"imported": 0, "skipped": 0}
    profile_report = {"imported": 0, "skipped": 0}
    try:
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
        # 包内只带口令包裹的 Key 保险箱；旧包（明文字段 ``secrets``）仍兼容。
        model_secrets: Dict[str, str] = {}
        if isinstance(models, dict):
            model_secrets = _open_model_secrets(models.get("key_vault"), passphrase)
            legacy_secrets = models.get("secrets")
            if not model_secrets and isinstance(legacy_secrets, dict):
                model_secrets = {
                    str(key): str(value)
                    for key, value in legacy_secrets.items()
                    if key and isinstance(value, str) and value
                }
        secret_count = (
            _model_store().import_plaintext_secrets(model_secrets) if model_secrets else 0
        )

        files = payload.get("files") or []
        file_report = _restore_files(files if isinstance(files, list) else [])
        # JSON 文本里的源机路径（执行日志、迁移标记）先改到本机；旧包没有
        # ``source`` 段时跳过，不影响导入。
        file_report["source_path_rewrites"] = _rewrite_restored_source_paths(
            payload.get("source")
        )
        # 包里的工件索引仍带源机绝对路径；重算到本机工作区，否则导入后
        # "文件都在"却会被 verify() 判成 missing（换盘/换用户名必踩）。
        try:
            from ..artifacts.index import rewrite_workspace_paths

            file_report["path_rewrites"] = rewrite_workspace_paths()
        except Exception:  # noqa: BLE001 - 索引缺失/损坏不阻断文件恢复
            logger.warning("could not rewrite artifact paths after import", exc_info=True)
        employee_report = _restore_employees(payload.get("employees"))
        goal_report = _restore_goals(payload.get("goals"))
        knowledge_report = _restore_knowledge(payload.get("knowledge"))

        # 记忆库 / 会话库在**工作区之外**，必须落回本机对应路径；合并策略：
        # 记忆按主键去重、对话按（主体/轮次/角色/正文）去重，绝不覆盖现场数据。
        memory_report = _restore_memory(payload.get("memory"))
        conversation_report = _restore_conversations(payload.get("conversations"))
        kernel_report = _restore_kernel_memory(payload.get("kernel_memory"))
        profile_report = _restore_personal_context(payload.get("personal_context"))

        restored = manager.apply_backup_payload(identity_part)
    except (OSError, sqlite3.Error) as exc:
        if _is_storage_full(exc):
            manager.record_event(
                action="account.backup.import",
                actor="anonymous",
                target=bundle_id,
                outcome="denied",
                details={"error_code": "ACCOUNT_BACKUP_DISK_FULL"},
            )
            raise _account_error(
                507, "ACCOUNT_BACKUP_DISK_FULL",
                "目标机存储空间不足，导入已中止；清理磁盘后可重试同一备份包",
            ) from exc
        raise

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
            "model_keys_restored": secret_count,
            "files": file_report["restored"],
            "files_skipped": file_report["skipped"],
            "files_path_rewrites": file_report.get("path_rewrites", 0),
            "employees": employee_report["restored"],
            "employees_skipped": employee_report["skipped"],
            "goals": goal_report["restored"],
            "goals_skipped": goal_report["skipped"],
            "knowledge_chunks": knowledge_report["chunks"],
            "knowledge_chunks_skipped": knowledge_report["chunks_skipped"],
            "knowledge_hashes": knowledge_report["hashes"],
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
        "model_keys_restored": secret_count,
        "files": file_report,
        "employees": employee_report,
        "goals": goal_report,
        "knowledge": knowledge_report,
        "memory": memory_report,
        "conversations": conversation_report,
        "kernel_memory": kernel_report,
        "personal_context": profile_report,
        "memory_migrated": migration_report.get("entries", 0),
        "manifest": payload.get("manifest") or {},
        "token_policy": dict(_TOKEN_POLICY),
        "notice": (
            "导入完成；本机使用自己的登录签名密钥，其他实例已签发的令牌不能在本机使用。"
            "账号树与成员数据已恢复；员工、目标、知识库、记忆按主键去重，不会产生副本。"
            "本机已有账号时不会再接受另一个备份包（避免覆盖现有数据）。"
        ),
    }
