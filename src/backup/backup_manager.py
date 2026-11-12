"""Backup and restore system for LiuHao AI OS.

Provides comprehensive backup management including:
- Multiple backup types: full, incremental, differential
- Multiple backup sources: config, memory data, knowledge base, user data
- Manual and periodic scheduling
- Backup metadata (timestamp, size, checksum, type, sources)
- Gzip compression support
- Restore capability with integrity verification
- Selective and full restore
- Backup retention policy (count-based and time-based)
- Backup statistics and reporting

Payload encoding is BYTE-ACCURATE: every file is stored as base64 of its
bytes, never as decoded UTF-8 text. The previous text encoding silently
mangled SQLite databases (and their ``-wal``), binary attachments and any
non-UTF-8 file: the backup "succeeded", the damage only surfaced at restore
time. Silent corruption is worse than a failed backup.

SQLite files are not copied byte-for-byte: they are snapshotted with
``VACUUM INTO`` (``src/kernels/audit/durability.py``), which is
transaction-consistent even with an uncheckpointed WAL. Their ``-wal`` /
``-shm`` / ``-journal`` sidecars are deliberately excluded from the archive
and removed on restore, because replaying a stale WAL against a restored
main file corrupts it.
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import json
import os
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from src.kernels.audit.durability import vacuum_into


# =====================================================================
# Byte-accurate file helpers
# =====================================================================

_SQLITE_MAGIC = b"SQLite format 3\x00"
_SQLITE_SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")


def derive_backup_key(
    passphrase: str,
    salt: bytes,
    iterations: int = 600_000,
) -> bytes:
    """从口令派生 32 字节备份密钥（PBKDF2-HMAC-SHA256）。

    调用方负责保管 ``salt``（与备份文件放在一起即可，它不是密钥）；
    同一口令 + 同一 salt 才能解开。迭代次数 600k 是 OWASP 对 PBKDF2-HMAC-SHA256
    的建议值。``cryptography`` 是 pyproject 已声明的依赖，不引入新依赖。
    """
    if not passphrase:
        raise ValueError("derive_backup_key requires a non-empty passphrase")
    if not salt:
        raise ValueError("derive_backup_key requires a non-empty salt")
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    return PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=iterations,
    ).derive(passphrase.encode("utf-8"))


_ENCRYPTED_MAGIC = b"LHBAK1"
_ENCRYPTED_NONCE_LEN = 12


def encrypt_backup_bytes(plaintext: bytes, key: bytes) -> bytes:
    """AES-256-GCM 加密一段备份字节流。

    格式：``b"LHBAK1" + 12 字节随机 nonce + 密文（含 16 字节 GCM tag）``。
    先压缩再加密（调用方负责顺序）；解密失败一律报 ``ValueError``，
    不把 ``InvalidTag`` 的细节透给调用方之外的日志。
    """
    if len(key) != 32:
        raise ValueError("backup encryption key must be 32 bytes (AES-256)")
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = os.urandom(_ENCRYPTED_NONCE_LEN)
    return _ENCRYPTED_MAGIC + nonce + AESGCM(key).encrypt(nonce, plaintext, None)


def decrypt_backup_bytes(blob: bytes, key: bytes) -> bytes:
    """``encrypt_backup_bytes`` 的逆操作。"""
    if len(key) != 32:
        raise ValueError("backup encryption key must be 32 bytes (AES-256)")
    if len(blob) < len(_ENCRYPTED_MAGIC) + _ENCRYPTED_NONCE_LEN + 16:
        raise ValueError("not an encrypted backup blob (too short or bad magic)")
    if blob[: len(_ENCRYPTED_MAGIC)] != _ENCRYPTED_MAGIC:
        raise ValueError("not an encrypted backup blob (bad magic)")
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = blob[len(_ENCRYPTED_MAGIC) : len(_ENCRYPTED_MAGIC) + _ENCRYPTED_NONCE_LEN]
    try:
        return AESGCM(key).decrypt(nonce, blob[len(_ENCRYPTED_MAGIC) + _ENCRYPTED_NONCE_LEN :], None)
    except InvalidTag as exc:
        raise ValueError(
            "cannot decrypt backup: wrong key or corrupted archive"
        ) from exc


def _is_sqlite_file(path: Path) -> bool:
    """Detect SQLite by its on-disk header, not by extension."""
    try:
        with open(path, "rb") as fh:
            return fh.read(16) == _SQLITE_MAGIC
    except OSError:
        return False


def _is_sqlite_sidecar(path: Path) -> bool:
    """True iff ``path`` is a ``-wal``/``-shm``/``-journal`` of a SQLite DB."""
    for suffix in _SQLITE_SIDECAR_SUFFIXES:
        if path.name.endswith(suffix):
            base = path.with_name(path.name[: -len(suffix)])
            return base.exists() and _is_sqlite_file(base)
    return False


def _read_sqlite_snapshot(path: Path) -> bytes:
    """Return a transaction-consistent copy of a SQLite DB as bytes."""
    with tempfile.TemporaryDirectory(prefix="liuhao_backup_snapshot_") as tmp:
        dst = Path(tmp) / "snapshot.db"
        vacuum_into(str(path), str(dst))
        return dst.read_bytes()


# =====================================================================
# Machine-bound key material inside migration archives
# =====================================================================
#
# 换机迁移包必须能跨机器恢复，而 DPAPI 密文只有原机/原用户能解。因此：
#
# * **导出**：把 ``model_config.key``、``integrity.key`` 与 ``app.env`` 里的
#   JWT 密钥解包成原始材料再封进口令加密包——包里不再出现 ``LIUHAO-DPAPI-1:``
#   密文；
# * **恢复**：用目标机 DPAPI 重新包裹，落盘形态与首次启动生成的一致；
# * 只有确实解包过的条目才打 ``machine_bound`` 标记，历史明文/旧归档不受影响。

#: app.env 里可能承载 JWT 签名密钥的变量名（与 jwt_handler 保持同源）。
_JWT_SECRET_ENV_NAMES_FALLBACK = (
    "LIUHAO_JWT_SECRET", "JWT_SECRET_KEY", "JWT_SECRET",
)

_RESTORE_DPAPI_UNAVAILABLE_TEXT = (
    "恢复失败：目标机无法使用 Windows DPAPI，密钥不能重新绑定本机。"
    "本次未写入任何文件，已有文件未被覆盖。"
    "请在 Windows 上以当前用户运行，或先修复/安装 pywin32 后再恢复。"
)


def _dpapi_purpose_for(basename: str) -> Optional[bytes]:
    if basename == "model_config.key":
        from src.ai.model_config import KEY_DPAPI_PURPOSE

        return KEY_DPAPI_PURPOSE
    if basename == "integrity.key":
        from src.common.integrity_key import INTEGRITY_DPAPI_PURPOSE

        return INTEGRITY_DPAPI_PURPOSE
    return None


def _jwt_dpapi_purpose() -> bytes:
    from src.security.jwt_handler import JWT_SECRET_DPAPI_PURPOSE

    return JWT_SECRET_DPAPI_PURPOSE


def _jwt_secret_env_names() -> Tuple[str, ...]:
    try:
        from src.security.jwt_handler import JWT_SECRET_ENV, JWT_SECRET_ENV_ALIASES

        return (JWT_SECRET_ENV, *JWT_SECRET_ENV_ALIASES)
    except Exception:  # noqa: BLE001 - jwt 未安装时仍按已知变量名处理
        return _JWT_SECRET_ENV_NAMES_FALLBACK


def _env_secret_names() -> Tuple[str, ...]:
    """app.env 里需要目标机 DPAPI 重绑的变量：JWT 密钥 + 会话密钥。"""
    try:
        from src.security.app_env_secrets import SECRET_KEY_ENV

        return (*_jwt_secret_env_names(), SECRET_KEY_ENV)
    except Exception:  # noqa: BLE001 - 模块缺失时仍按已知变量名处理
        return (*_jwt_secret_env_names(), "SECRET_KEY")


def _env_secret_purpose(name: str) -> bytes:
    """按变量名选 DPAPI 熵：不同用途的密钥密文不可互换。"""
    try:
        from src.security.app_env_secrets import (
            SECRET_KEY_DPAPI_PURPOSE,
            SECRET_KEY_ENV,
        )

        if str(name) == SECRET_KEY_ENV:
            return SECRET_KEY_DPAPI_PURPOSE
    except Exception:  # noqa: BLE001
        if str(name) == "SECRET_KEY":
            return b"secret-key-v1"
    return _jwt_dpapi_purpose()


def _env_value_parts(body: str) -> Tuple[str, str]:
    name, sep, value = body.partition("=")
    return name.strip(), (value.strip().strip('"').strip("'") if sep else "")


def _extract_env_secrets(raw: bytes) -> Tuple[bytes, Optional[Dict[str, Any]]]:
    """把 app.env 里的密钥密文还原成明文；返回 ``(内容, 标记)``。

    覆盖 JWT 签名密钥与 ``SECRET_KEY``：两者都用各自用途的 DPAPI 熵包裹，
    导出时都必须解成原始材料，否则换机恢复后目标机解不开。
    """
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw, None
    names = _env_secret_names()
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines(keepends=True)
    extracted: List[str] = []
    from src.security.encryption import is_machine_bound_payload, unwrap_machine_bound

    for index, line in enumerate(lines):
        body = line.rstrip("\r\n")
        name, value = _env_value_parts(body)
        if name not in names:
            continue
        if not is_machine_bound_payload(value):
            continue
        try:
            plain, _mode = unwrap_machine_bound(
                value.encode("utf-8"), purpose=_env_secret_purpose(name)
            )
        except Exception as exc:  # noqa: BLE001 - MachineBoundKeyError/损坏
            raise ValueError(
                f"导出失败：app.env 里的 {name} 无法在本机解包，"
                "无法生成可跨机器恢复的备份包。本次未写出任何归档；"
                "请先在原机修复/迁移密钥后再导出，或改用「账号备份」导出。\n"
                f"{exc}"
            ) from None
        lines[index] = f"{name}={plain.decode('utf-8', 'ignore')}{newline}"
        extracted.append(name)
    if not extracted:
        return raw, None
    return "".join(lines).encode("utf-8"), {"env": extracted}


def extract_machine_bound_material(
    basename: str, raw: bytes
) -> Tuple[bytes, Optional[Dict[str, Any]]]:
    """导出：把本机密钥密文还原成原始材料。

    返回 ``(内容, 标记)``；``标记`` 为 ``None`` 表示该文件不含机器绑定密钥
    （历史明文/普通文件原样保留）。
    """
    from src.security.encryption import (
        MachineBoundKeyError,
        is_machine_bound_payload,
        unwrap_machine_bound,
    )

    purpose = _dpapi_purpose_for(basename)
    if purpose is not None:
        if not is_machine_bound_payload(raw):
            return raw, None
        try:
            plain, _mode = unwrap_machine_bound(raw, purpose=purpose)
        except MachineBoundKeyError as exc:
            raise ValueError(
                f"导出失败：{basename} 里的密钥无法在本机解包，"
                "无法生成可跨机器恢复的备份包。本次未写出任何归档；"
                "请先在原机修复/迁移密钥后再导出，或改用「账号备份」导出。\n"
                f"{exc}"
            ) from None
        return plain, {"file": basename}
    if basename == "app.env":
        return _extract_env_secrets(raw)
    return raw, None


def restore_machine_bound_material(
    basename: str, raw: bytes, marker: Dict[str, Any]
) -> bytes:
    """恢复：按标记用目标机 DPAPI 重新包裹；不可用时抛中文 ``ValueError``。"""
    if not marker:
        return raw
    from src.security.encryption import (
        MachineBoundKeyError,
        is_machine_bound_payload,
        wrap_machine_bound,
    )

    file_name = str(marker.get("file") or "")
    if file_name:
        if is_machine_bound_payload(raw):
            return raw  # 旧归档已经包裹过，不重复包裹
        purpose = _dpapi_purpose_for(file_name) or _dpapi_purpose_for(basename)
        if purpose is None:
            return raw
        try:
            return wrap_machine_bound(raw, purpose=purpose)
        except MachineBoundKeyError:
            raise ValueError(_RESTORE_DPAPI_UNAVAILABLE_TEXT) from None

    env_names = marker.get("env")
    if not isinstance(env_names, list) or not env_names:
        return raw
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw
    wanted = set(str(name) for name in env_names)
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines(keepends=True)
    rewrapped = False
    for index, line in enumerate(lines):
        body = line.rstrip("\r\n")
        name, value = _env_value_parts(body)
        if name not in wanted or is_machine_bound_payload(value):
            continue
        try:
            wrapped = wrap_machine_bound(
                value.encode("utf-8"), purpose=_env_secret_purpose(name)
            ).decode("ascii")
        except MachineBoundKeyError:
            raise ValueError(_RESTORE_DPAPI_UNAVAILABLE_TEXT) from None
        lines[index] = f"{name}={wrapped}{newline}"
        rewrapped = True
    return "".join(lines).encode("utf-8") if rewrapped else raw


# =====================================================================
# Enums & Data Classes
# =====================================================================


class BackupType(str, Enum):
    """Types of backup strategies."""

    FULL = "full"
    INCREMENTAL = "incremental"
    DIFFERENTIAL = "differential"


class BackupSource(str, Enum):
    """Categories of data that can be backed up."""

    CONFIG = "config"
    MEMORY_DATA = "memory_data"
    KNOWLEDGE_BASE = "knowledge_base"
    USER_DATA = "user_data"


class BackupStatus(str, Enum):
    """Status of a backup operation."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    VERIFIED = "verified"


class RestoreStatus(str, Enum):
    """Status of a restore operation."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    VERIFIED = "verified"


class RetentionPolicyType(str, Enum):
    """Types of retention policies."""

    COUNT_BASED = "count_based"
    TIME_BASED = "time_based"
    BOTH = "both"


@dataclass
class RetentionPolicy:
    """Backup retention policy configuration.

    Attributes:
        policy_type: How retention is determined.
        max_backups: Maximum number of backups to keep (count-based).
        max_age_days: Maximum age in days (time-based).
        always_keep_full: Whether to always keep at least one full backup.
    """

    policy_type: RetentionPolicyType = RetentionPolicyType.COUNT_BASED
    max_backups: int = 10
    max_age_days: int = 30
    always_keep_full: bool = True


@dataclass
class BackupMetadata:
    """Metadata for a single backup.

    Attributes:
        backup_id: Unique backup identifier.
        backup_type: Type of backup.
        sources: List of data sources included.
        timestamp: Unix timestamp of when the backup was created.
        size_bytes: Total size of the backup in bytes.
        checksum: SHA-256 checksum of the backup data.
        compressed: Whether compression was applied.
        encrypted: Whether encryption was applied.
        status: Current backup status.
        file_path: Path to the backup file on disk.
        parent_backup_id: ID of parent backup (for incremental/differential).
        description: Human-readable description.
        metadata: Additional arbitrary metadata.
    """

    backup_id: str
    backup_type: BackupType
    sources: List[BackupSource]
    timestamp: float
    size_bytes: int = 0
    checksum: str = ""
    compressed: bool = False
    encrypted: bool = False
    status: BackupStatus = BackupStatus.PENDING
    file_path: Optional[str] = None
    parent_backup_id: Optional[str] = None
    description: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def datetime(self) -> datetime:
        """Return the timestamp as a datetime object."""
        return datetime.fromtimestamp(self.timestamp)

    @property
    def size_human(self) -> str:
        """Return human-readable size string."""
        size = self.size_bytes
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if size < 1024.0:
                return f"{size:.2f} {unit}"
            size /= 1024.0
        return f"{size:.2f} PB"

    def to_dict(self) -> Dict[str, Any]:
        """Convert to a serialisable dictionary."""
        return {
            "backup_id": self.backup_id,
            "backup_type": self.backup_type.value,
            "sources": [s.value for s in self.sources],
            "timestamp": self.timestamp,
            "size_bytes": self.size_bytes,
            "checksum": self.checksum,
            "compressed": self.compressed,
            "encrypted": self.encrypted,
            "status": self.status.value,
            "file_path": self.file_path,
            "parent_backup_id": self.parent_backup_id,
            "description": self.description,
            "metadata": self.metadata,
        }


@dataclass
class RestoreResult:
    """Result of a restore operation."""

    success: bool
    backup_id: str
    restored_sources: List[BackupSource] = field(default_factory=list)
    total_files_restored: int = 0
    total_bytes_restored: int = 0
    error_message: Optional[str] = None
    duration_seconds: float = 0.0
    status: RestoreStatus = RestoreStatus.COMPLETED

    def to_dict(self) -> Dict[str, Any]:
        """Convert to a serialisable dictionary."""
        return {
            "success": self.success,
            "backup_id": self.backup_id,
            "restored_sources": [s.value for s in self.restored_sources],
            "total_files_restored": self.total_files_restored,
            "total_bytes_restored": self.total_bytes_restored,
            "error_message": self.error_message,
            "duration_seconds": self.duration_seconds,
            "status": self.status.value,
        }


@dataclass
class BackupStatistics:
    """Aggregate backup statistics."""

    total_backups: int = 0
    total_size_bytes: int = 0
    full_backups: int = 0
    incremental_backups: int = 0
    differential_backups: int = 0
    successful_backups: int = 0
    failed_backups: int = 0
    oldest_backup_timestamp: Optional[float] = None
    newest_backup_timestamp: Optional[float] = None

    @property
    def total_size_human(self) -> str:
        """Return human-readable total size."""
        size = self.total_size_bytes
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if size < 1024.0:
                return f"{size:.2f} {unit}"
            size /= 1024.0
        return f"{size:.2f} PB"

    def to_dict(self) -> Dict[str, Any]:
        """Convert to a serialisable dictionary."""
        return {
            "total_backups": self.total_backups,
            "total_size_bytes": self.total_size_bytes,
            "total_size_human": self.total_size_human,
            "full_backups": self.full_backups,
            "incremental_backups": self.incremental_backups,
            "differential_backups": self.differential_backups,
            "successful_backups": self.successful_backups,
            "failed_backups": self.failed_backups,
            "oldest_backup_timestamp": self.oldest_backup_timestamp,
            "newest_backup_timestamp": self.newest_backup_timestamp,
        }


# =====================================================================
# BackupManager
# =====================================================================


@dataclass
class BackupManager:
    """Backup and restore manager for LiuHao AI OS.

    Handles creating, managing, verifying, and restoring backups of
    various data sources.  Supports compression, encryption flags,
    retention policies, and both full and selective restores.

    Attributes:
        backup_dir: Directory where backup files are stored.
        source_paths: Mapping of backup source types to their filesystem paths.
        backups: Registry of all known backups (by ID).
        retention_policy: Policy for pruning old backups.
        compression_level: Gzip compression level (1-9).
        encryption_enabled: ``True`` 时归档经 AES-256-GCM 加密后落盘
            （先 gzip 再加密）。必须同时提供 ``encryption_key``（32 字节），
            否则 ``create_backup`` 拒绝写入；只有 ``encryption_key`` 而未开启
            亦拒绝，避免"带了密钥却写了明文"的误解。密钥由调用方保管，
            可用 ``derive_backup_key(passphrase, salt)`` 从口令派生。
    """

    backup_dir: Path
    source_paths: Dict[BackupSource, Path] = field(default_factory=dict)
    backups: Dict[str, BackupMetadata] = field(default_factory=dict)
    retention_policy: RetentionPolicy = field(default_factory=RetentionPolicy)
    compression_level: int = 6
    encryption_enabled: bool = False
    encryption_key: Optional[bytes] = None

    def _require_encryption_key(self) -> bytes:
        """加密备份的密钥门禁：缺密钥 / 错长度一律拒绝，绝不写明文。"""
        if self.encryption_key is None:
            raise ValueError(
                "encryption_enabled=True requires encryption_key (32 bytes): "
                "pass encryption_key=derive_backup_key(passphrase, salt), "
                "or use scripts/ops/backup.py for passphrase-based archives."
            )
        if len(self.encryption_key) != 32:
            raise ValueError("encryption_key must be 32 bytes (AES-256)")
        return self.encryption_key

    def _decrypt_blob(self, raw: bytes, meta: "BackupMetadata") -> bytes:
        """按元数据把归档字节流还原成压缩/明文 JSON（加密时先解密）。"""
        if not meta.encrypted:
            return raw
        key = self._require_encryption_key()
        return decrypt_backup_bytes(raw, key)

    def __post_init__(self) -> None:
        self.backup_dir = Path(self.backup_dir)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        # Ensure source paths are Path objects
        self.source_paths = {
            k: Path(v) for k, v in self.source_paths.items()
        }

    # ------------------------------------------------------------------
    # Backup creation
    # ------------------------------------------------------------------

    def create_backup(
        self,
        backup_type: BackupType = BackupType.FULL,
        sources: Optional[List[BackupSource]] = None,
        description: str = "",
        compress: bool = True,
        parent_backup_id: Optional[str] = None,
    ) -> BackupMetadata:
        """Create a new backup.

        Args:
            backup_type: Type of backup to create.
            sources: Sources to include (defaults to all configured sources).
            description: Optional description.
            compress: Whether to compress the backup with gzip.
            parent_backup_id: Parent backup ID (for incremental/differential).

        Returns:
            ``BackupMetadata`` for the created backup.
        """
        if sources is None:
            sources = list(self.source_paths.keys())

        # 加密是真实落盘的 AES-256-GCM，不是只改元数据的 flag：缺密钥或
        # 密钥长度不对时直接拒绝，绝不写出"标称已加密的明文"。
        encryption_key: Optional[bytes] = None
        if self.encryption_enabled:
            encryption_key = self._require_encryption_key()
        elif self.encryption_key is not None:
            raise ValueError(
                "encryption_key is set but encryption_enabled=False: refusing to "
                "write a plaintext archive while a key is configured."
            )

        backup_id = f"backup_{int(time.time())}_{uuid.uuid4().hex[:8]}"
        timestamp = time.time()

        meta = BackupMetadata(
            backup_id=backup_id,
            backup_type=backup_type,
            sources=sources,
            timestamp=timestamp,
            compressed=compress,
            encrypted=self.encryption_enabled,
            status=BackupStatus.IN_PROGRESS,
            description=description,
            parent_backup_id=parent_backup_id,
        )

        backup_path = self.backup_dir / backup_id
        if self.encryption_enabled:
            backup_path = backup_path.with_suffix(".enc")
        elif compress:
            backup_path = backup_path.with_suffix(".tar.gz")
        else:
            backup_path = backup_path.with_suffix(".tar")

        meta.file_path = str(backup_path)
        self.backups[backup_id] = meta

        try:
            # Create the backup data
            backup_data: Dict[str, Any] = {
                "backup_id": backup_id,
                "backup_type": backup_type.value,
                "sources": [s.value for s in sources],
                "timestamp": timestamp,
                "description": description,
                "parent_backup_id": parent_backup_id,
                "data": {},
            }

            total_size = 0
            for source in sources:
                source_path = self.source_paths.get(source)
                if source_path is None or not source_path.exists():
                    backup_data["data"][source.value] = {
                        "exists": False,
                        "content": None,
                    }
                    continue

                source_data = self._collect_source_data(source, source_path)
                backup_data["data"][source.value] = source_data
                total_size += source_data.get("size_bytes", 0)

            # Serialise and write: gzip first, then encrypt (encrypt-then-
            # compress would leak plaintext length structure and defeat gzip).
            json_bytes = json.dumps(backup_data, indent=2, default=str).encode("utf-8")

            if compress:
                payload = gzip.compress(json_bytes, compresslevel=self.compression_level)
            else:
                payload = json_bytes
            if encryption_key is not None:
                payload = encrypt_backup_bytes(payload, encryption_key)
            backup_path.write_bytes(payload)
            meta.size_bytes = len(payload)

            # Compute checksum from the written file
            meta.checksum = self._compute_checksum(backup_path)
            meta.status = BackupStatus.COMPLETED

            # Apply retention policy
            self._apply_retention_policy()

            return meta

        except Exception as exc:
            meta.status = BackupStatus.FAILED
            meta.metadata["error"] = str(exc)
            raise

    def _collect_source_data(
        self,
        source: BackupSource,
        source_path: Path,
    ) -> Dict[str, Any]:
        """Collect data from a backup source.

        Files are captured as bytes (base64 in the JSON envelope). SQLite
        databases are captured through ``VACUUM INTO`` so the archive always
        holds one consistent snapshot instead of a main file plus a WAL that
        may or may not belong to it.
        """
        if source_path.is_file():
            file_info = self._read_source_file(source_path)
            return {
                "exists": True,
                "type": "file",
                "path": str(source_path),
                **file_info,
            }
        elif source_path.is_dir():
            files: Dict[str, Any] = {}
            total_size = 0
            for fpath in sorted(source_path.rglob("*")):
                if not fpath.is_file():
                    continue
                # Sidecars of a SQLite DB are captured as part of the
                # VACUUM INTO snapshot of the main file, not separately:
                # archiving a -wal byte-for-byte would let a later restore
                # replay it against a different main file and corrupt it.
                if _is_sqlite_sidecar(fpath):
                    continue
                rel = fpath.relative_to(source_path).as_posix()
                file_info = self._read_source_file(fpath)
                files[rel] = file_info
                total_size += file_info["size_bytes"]
            return {
                "exists": True,
                "type": "directory",
                "path": str(source_path),
                "files": files,
                "file_count": len(files),
                "size_bytes": total_size,
            }
        else:
            return {
                "exists": False,
                "type": "unknown",
                "path": str(source_path),
                "size_bytes": 0,
            }

    def _read_source_file(self, path: Path) -> Dict[str, Any]:
        """Read one file byte-accurately (SQLite via VACUUM INTO)."""
        is_sqlite = _is_sqlite_file(path)
        raw = _read_sqlite_snapshot(path) if is_sqlite else path.read_bytes()
        return {
            "encoding": "base64",
            "content_b64": base64.b64encode(raw).decode("ascii"),
            "size_bytes": len(raw),
            "modified": path.stat().st_mtime,
            "sqlite": is_sqlite,
        }

    def _compute_checksum(self, filepath: Path) -> str:
        """Compute SHA-256 checksum of a file."""
        sha256 = hashlib.sha256()
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256.update(chunk)
        return sha256.hexdigest()

    # ------------------------------------------------------------------
    # Backup listing & lookup
    # ------------------------------------------------------------------

    def list_backups(
        self,
        backup_type: Optional[BackupType] = None,
        status: Optional[BackupStatus] = None,
        limit: Optional[int] = None,
    ) -> List[BackupMetadata]:
        """List backups, optionally filtered.

        Args:
            backup_type: Optional filter by backup type.
            status: Optional filter by status.
            limit: Maximum number of results (newest first).

        Returns:
            List of ``BackupMetadata`` objects, newest first.
        """
        results = list(self.backups.values())

        if backup_type is not None:
            results = [b for b in results if b.backup_type == backup_type]
        if status is not None:
            results = [b for b in results if b.status == status]

        results.sort(key=lambda b: b.timestamp, reverse=True)

        if limit is not None:
            results = results[:limit]

        return results

    def get_backup(self, backup_id: str) -> Optional[BackupMetadata]:
        """Get a backup by ID."""
        return self.backups.get(backup_id)

    def latest_backup(
        self,
        backup_type: Optional[BackupType] = None,
    ) -> Optional[BackupMetadata]:
        """Get the most recent (successful) backup."""
        backups = self.list_backups(
            backup_type=backup_type,
            status=BackupStatus.COMPLETED,
            limit=1,
        )
        return backups[0] if backups else None

    # ------------------------------------------------------------------
    # Verification
    # ------------------------------------------------------------------

    def verify_backup(self, backup_id: str) -> bool:
        """Verify the integrity of a backup.

        Checks that:
        - The backup file exists
        - The stored checksum matches the actual file checksum
        - The file can be decompressed/decoded

        Args:
            backup_id: ID of the backup to verify.

        Returns:
            True if verification passes, False otherwise.
        """
        meta = self.backups.get(backup_id)
        if meta is None or meta.file_path is None:
            return False

        filepath = Path(meta.file_path)
        if not filepath.exists():
            return False

        # Check checksum
        actual_checksum = self._compute_checksum(filepath)
        if actual_checksum != meta.checksum:
            return False

        # Try to read/decompress (decrypt first when the archive is encrypted).
        try:
            raw = filepath.read_bytes()
            raw = self._decrypt_blob(raw, meta)
            if meta.compressed:
                gzip.decompress(raw)
            else:
                json.loads(raw.decode("utf-8"))
        except Exception:
            return False

        meta.status = BackupStatus.VERIFIED
        return True

    # ------------------------------------------------------------------
    # Restore
    # ------------------------------------------------------------------

    def restore_backup(
        self,
        backup_id: str,
        sources: Optional[List[BackupSource]] = None,
        target_dir: Optional[Path] = None,
    ) -> RestoreResult:
        """Restore from a backup.

        Args:
            backup_id: ID of the backup to restore from.
            sources: Specific sources to restore (None = all).
            target_dir: Optional alternate target directory (None = original).

        Returns:
            A ``RestoreResult`` describing the outcome.
        """
        start_time = time.time()
        meta = self.backups.get(backup_id)

        if meta is None:
            return RestoreResult(
                success=False,
                backup_id=backup_id,
                error_message=f"Backup '{backup_id}' not found",
                status=RestoreStatus.FAILED,
            )

        if meta.file_path is None:
            return RestoreResult(
                success=False,
                backup_id=backup_id,
                error_message="Backup file path not set",
                status=RestoreStatus.FAILED,
            )

        filepath = Path(meta.file_path)
        if not filepath.exists():
            return RestoreResult(
                success=False,
                backup_id=backup_id,
                error_message=f"Backup file not found at {filepath}",
                status=RestoreStatus.FAILED,
            )

        try:
            # Read, decrypt (when encrypted), then decompress.
            raw = filepath.read_bytes()
            try:
                raw = self._decrypt_blob(raw, meta)
            except ValueError as exc:
                return RestoreResult(
                    success=False,
                    backup_id=backup_id,
                    error_message=str(exc),
                    duration_seconds=time.time() - start_time,
                    status=RestoreStatus.FAILED,
                )
            if meta.compressed:
                raw = gzip.decompress(raw)

            backup_data = json.loads(raw.decode("utf-8"))

            sources_to_restore = sources if sources else meta.sources
            restored_sources: List[BackupSource] = []
            total_files = 0
            total_bytes = 0

            # 先完成全部密钥重包，再写任何文件：目标机 DPAPI 不可用时在这里
            # 失败，目标目录保持原样（不覆盖已有文件）。
            for source in sources_to_restore:
                source_data = backup_data.get("data", {}).get(source.value)
                if isinstance(source_data, dict) and source_data.get("exists"):
                    self._rewrap_source_entries(source_data)

            for source in sources_to_restore:
                source_val = source.value
                if source_val not in backup_data.get("data", {}):
                    continue

                source_data = backup_data["data"][source_val]
                if not source_data.get("exists"):
                    continue

                # Determine target path
                if target_dir is not None:
                    target = Path(target_dir) / source_val
                else:
                    source_path = self.source_paths.get(source)
                    if source_path is None:
                        continue
                    target = source_path

                target.parent.mkdir(parents=True, exist_ok=True)

                if source_data.get("type") == "file":
                    restored = self._write_restored_file(
                        target, source_data, source_data.get("sqlite", False)
                    )
                    total_files += 1
                    total_bytes += restored
                elif source_data.get("type") == "directory":
                    files = source_data.get("files", {})
                    for rel_path, file_info in files.items():
                        file_target = target / rel_path
                        restored = self._write_restored_file(
                            file_target, file_info, file_info.get("sqlite", False)
                        )
                        total_files += 1
                        total_bytes += restored

                restored_sources.append(source)

            return RestoreResult(
                success=True,
                backup_id=backup_id,
                restored_sources=restored_sources,
                total_files_restored=total_files,
                total_bytes_restored=total_bytes,
                duration_seconds=time.time() - start_time,
                status=RestoreStatus.COMPLETED,
            )

        except Exception as exc:
            return RestoreResult(
                success=False,
                backup_id=backup_id,
                error_message=str(exc),
                duration_seconds=time.time() - start_time,
                status=RestoreStatus.FAILED,
            )

    @classmethod
    def _rewrap_source_entries(cls, source_data: Dict[str, Any]) -> None:
        """把归档里带 ``machine_bound`` 标记的条目重新包成本机形态。"""
        if source_data.get("type") == "file":
            name = Path(str(source_data.get("path") or "")).name
            cls._restore_entry_machine_bound(name, source_data)
            return
        for rel, entry in (source_data.get("files") or {}).items():
            if isinstance(entry, dict):
                cls._restore_entry_machine_bound(Path(str(rel)).name, entry)

    @staticmethod
    def _restore_entry_machine_bound(
        basename: str, entry: Dict[str, Any]
    ) -> Dict[str, Any]:
        """返回重新包裹后的条目；没有标记时原样返回。"""
        marker = entry.get("machine_bound")
        if not isinstance(marker, dict) or not marker:
            return entry
        raw = BackupManager._decode_file_entry(entry)
        wrapped = restore_machine_bound_material(basename, raw, marker)
        updated = dict(entry)
        updated.pop("content", None)
        updated["content_b64"] = base64.b64encode(wrapped).decode("ascii")
        updated["size_bytes"] = len(wrapped)
        return updated

    @staticmethod
    def _decode_file_entry(entry: Dict[str, Any]) -> bytes:
        """Decode one archived file entry without losing any byte.

        Supports the legacy text format (``content``) so archives written
        before this fix still restore; new archives always carry
        ``content_b64``.
        """
        if "content_b64" in entry and entry["content_b64"] is not None:
            return base64.b64decode(entry["content_b64"])
        if "content" in entry:
            return str(entry.get("content") or "").encode("utf-8")
        raise ValueError(
            "backup entry carries neither content_b64 nor content; refusing "
            "to restore a file whose bytes are unknown"
        )

    @staticmethod
    def _write_restored_file(
        target: Path,
        entry: Dict[str, Any],
        is_sqlite: bool,
    ) -> int:
        """Write restored bytes atomically and return the byte count.

        ``os.replace`` means a restore interrupted halfway leaves the old file
        intact rather than a truncated one. For SQLite, stale sidecars are
        removed *after* the main file is in place: a leftover ``-wal`` from the
        pre-restore DB would be replayed against the restored file and corrupt
        it, which is exactly the failure a backup is supposed to undo.
        """
        raw = BackupManager._decode_file_entry(entry)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".restore-tmp")
        try:
            with open(tmp, "wb") as fh:
                fh.write(raw)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, target)
        finally:
            if tmp.exists():
                tmp.unlink(missing_ok=True)
        if is_sqlite:
            for suffix in _SQLITE_SIDECAR_SUFFIXES:
                sidecar = target.with_name(target.name + suffix)
                if sidecar.exists():
                    sidecar.unlink(missing_ok=True)
        return len(raw)

    # ------------------------------------------------------------------
    # Retention policy
    # ------------------------------------------------------------------

    def _apply_retention_policy(self) -> int:
        """Apply the retention policy, deleting old backups.

        Returns:
            Number of backups deleted.
        """
        policy = self.retention_policy
        deleted = 0

        # Get all completed backups sorted by timestamp (oldest first)
        completed = [
            b for b in self.backups.values()
            if b.status in (BackupStatus.COMPLETED, BackupStatus.VERIFIED)
        ]
        completed.sort(key=lambda b: b.timestamp)

        backups_to_remove: Set[str] = set()

        # Count-based retention
        if policy.policy_type in (RetentionPolicyType.COUNT_BASED,
                                  RetentionPolicyType.BOTH):
            if len(completed) > policy.max_backups:
                excess = len(completed) - policy.max_backups
                for b in completed[:excess]:
                    backups_to_remove.add(b.backup_id)

        # Time-based retention
        if policy.policy_type in (RetentionPolicyType.TIME_BASED,
                                  RetentionPolicyType.BOTH):
            cutoff = time.time() - (policy.max_age_days * 86400)
            for b in completed:
                if b.timestamp < cutoff:
                    backups_to_remove.add(b.backup_id)

        # Always keep at least one full backup if configured
        if policy.always_keep_full:
            full_backups = [b for b in completed if b.backup_type == BackupType.FULL]
            if full_backups:
                newest_full = max(full_backups, key=lambda b: b.timestamp)
                backups_to_remove.discard(newest_full.backup_id)

        # Actually delete
        for backup_id in backups_to_remove:
            self._delete_backup(backup_id)
            deleted += 1

        return deleted

    def _delete_backup(self, backup_id: str) -> bool:
        """Delete a backup and its file.  Returns False if not found."""
        meta = self.backups.get(backup_id)
        if meta is None:
            return False

        if meta.file_path:
            filepath = Path(meta.file_path)
            if filepath.exists():
                filepath.unlink()

        del self.backups[backup_id]
        return True

    def prune_old_backups(self) -> int:
        """Manually trigger retention policy application.

        Returns:
            Number of backups deleted.
        """
        return self._apply_retention_policy()

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def get_statistics(self) -> BackupStatistics:
        """Compute aggregate backup statistics.

        Returns:
            A ``BackupStatistics`` object with summary data.
        """
        stats = BackupStatistics(total_backups=len(self.backups))

        completed_times: List[float] = []

        for meta in self.backups.values():
            stats.total_size_bytes += meta.size_bytes

            if meta.backup_type == BackupType.FULL:
                stats.full_backups += 1
            elif meta.backup_type == BackupType.INCREMENTAL:
                stats.incremental_backups += 1
            elif meta.backup_type == BackupType.DIFFERENTIAL:
                stats.differential_backups += 1

            if meta.status in (BackupStatus.COMPLETED, BackupStatus.VERIFIED):
                stats.successful_backups += 1
                completed_times.append(meta.timestamp)
            elif meta.status == BackupStatus.FAILED:
                stats.failed_backups += 1

        if completed_times:
            stats.oldest_backup_timestamp = min(completed_times)
            stats.newest_backup_timestamp = max(completed_times)

        return stats

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def generate_report(self) -> Dict[str, Any]:
        """Generate a comprehensive backup report.

        Returns:
            Dictionary with backup statistics and recent backup info.
        """
        stats = self.get_statistics()
        recent = self.list_backups(limit=5)

        return {
            "statistics": stats.to_dict(),
            "retention_policy": {
                "type": self.retention_policy.policy_type.value,
                "max_backups": self.retention_policy.max_backups,
                "max_age_days": self.retention_policy.max_age_days,
                "always_keep_full": self.retention_policy.always_keep_full,
            },
            "compression_level": self.compression_level,
            "encryption_enabled": self.encryption_enabled,
            "backup_dir": str(self.backup_dir),
            "recent_backups": [b.to_dict() for b in recent],
            "sources_configured": [s.value for s in self.source_paths.keys()],
        }
