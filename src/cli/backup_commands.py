"""Backup CLI commands for LiuHao AI OS.

Provides command-line interface for backup operations:
- backup create, backup list, backup info
- backup restore, backup verify
- backup prune, backup export

This module used to be an in-memory simulation: it seeded five fake backups,
reported a fixed 42 MB size, invented ``sha256:<random>`` checksums and a
``status: restored`` without touching a single file. The CLI is now a thin
adapter over :class:`src.backup.backup_manager.BackupManager`, so every command
operates on real archives on disk (byte-accurate, SQLite via ``VACUUM INTO``).

Archive metadata lives inside the archive, so a fresh CLI process can still
list, verify and restore backups written by an earlier process.
"""

from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import time
import zlib
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from src.backup.backup_manager import (
    BackupManager as _FileBackupManager,
    BackupMetadata,
    BackupSource as _FileBackupSource,
    BackupStatus as _FileBackupStatus,
    BackupType as _FileBackupType,
    RetentionPolicy,
    RetentionPolicyType,
)
from src.upgrade.backups import is_protected_state_file


class BackupType(str, Enum):
    """Types of backup strategies."""
    FULL = "full"
    INCREMENTAL = "incremental"
    DIFFERENTIAL = "differential"


class BackupStatus(str, Enum):
    """Status of a backup operation."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    VERIFIED = "verified"


class BackupSource(str, Enum):
    """Categories of data that can be backed up."""
    CONFIG = "config"
    MEMORY_DATA = "memory_data"
    KNOWLEDGE_BASE = "knowledge_base"
    USER_DATA = "user_data"


@dataclass
class BackupRecord:
    """A backup record (kept for API compatibility)."""
    backup_id: str
    backup_type: BackupType
    sources: List[str]
    status: BackupStatus = BackupStatus.COMPLETED
    size_bytes: int = 0
    checksum: str = ""
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    completed_at: Optional[str] = None
    encrypted: bool = False
    compressed: bool = True
    description: str = ""


def _default_data_dir() -> Path:
    env = os.environ.get("LIUHAO_DATA_DIR")
    if env:
        return Path(env)
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "LiuHao" / "data"
    return Path.home() / ".liuhao" / "data"


def _default_backup_dir(data_dir: Path) -> Path:
    env = os.environ.get("LIUHAO_BACKUP_DIR")
    if env:
        return Path(env)
    # Sibling of the data dir, never inside it: a backup directory nested in
    # the backed-up tree would be re-captured by the next backup.
    return data_dir.parent / "backups"


#: 加密归档：明文 gzip 会被口令 AES-256-GCM 封装进同一个 `.lhbak` 文件。
_ENCRYPTED_SUFFIX = ".lhbak"
_ENCRYPTED_FORMAT = "liuhao.cli-backup.encrypted/1"
_KDF_ITERATIONS = 200_000
_PASSWORD_ENV = "LIUHAO_BACKUP_PASSWORD"

#: ``is_protected_state_file`` 之外的 CLI 专属禁打包项：``app.env`` 里有
#: JWT/服务密钥，``model_registry.sqlite3`` 是 API Key 的密文派生数据。
_CLI_EXTRA_PROTECTED = frozenset({"app.env", "model_registry.sqlite3"})

#: 迁移不带的"原机运行痕迹"：日志与诊断包含旧机器的绝对路径/用户名，新机
#: 也会自动重建；锁文件带过来还可能挡住启动。数据本身不收这些。
_RUNTIME_EXCLUDED_PREFIXES = ("logs/", "diagnostics/")
_RUNTIME_EXCLUDED_SUFFIXES = (".log", ".lock", ".tmp", ".temp")

#: 备份源在**真实数据目录**里的落点。恢复时必须按这个布局铺回去，不能按
#: 源枚举名（``memory_data`` / ``user_data``）建目录，否则新机上多出一层
#: ``memory_data`` 目录，程序根本读不到库，等于"恢复了但登不进去"。
_LAYOUT_RELATIVE = {
    _FileBackupSource.CONFIG: "config",
    _FileBackupSource.MEMORY_DATA: "memory_store.db",
    _FileBackupSource.KNOWLEDGE_BASE: "data/knowledge_vectors.sqlite3",
    _FileBackupSource.USER_DATA: ".",
}

#: 出现任一即认为目标目录"不是干净的数据目录"，拒绝在其上做恢复。
#: 半覆盖的恢复比恢复失败更糟：用户会以为换机成功，实际数据是拼出来的。
_PRISTINE_BLOCKERS = (
    "config",
    "data",
    "workspace",
    "logs",
    "diagnostics",
    "memory_store.db",
    "conversation_store.db",
    "audit_store.db",
    "account_memory.sqlite3",
)


def _is_unsafe_for_plaintext(relpath: str) -> bool:
    """明文备份绝不允许打包的凭据/密钥文件。"""
    name = str(relpath or "").replace("\\", "/").rsplit("/", 1)[-1].lower()
    return is_protected_state_file(relpath) or name in _CLI_EXTRA_PROTECTED


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _derive_key(password: str, salt: bytes) -> bytes:
    return PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=_KDF_ITERATIONS,
    ).derive(password.encode("utf-8"))


def _seal_archive(raw: bytes, *, password: str, header: Dict[str, Any]) -> bytes:
    """用口令派生的 AES-256-GCM 密钥封装明文 Gzip 字节。"""
    salt = os.urandom(16)
    nonce = os.urandom(12)
    key = _derive_key(password, salt)
    payload = AESGCM(key).encrypt(nonce, raw, None)
    document = {
        "format": _ENCRYPTED_FORMAT,
        **header,
        "encrypted": True,
        "compressed": True,
        "cipher": "aes-256-gcm",
        "kdf": {
            "name": "pbkdf2-sha256",
            "iterations": _KDF_ITERATIONS,
            "salt": _b64(salt),
        },
        "nonce": _b64(nonce),
        "payload": _b64(payload),
        "payload_sha256": hashlib.sha256(raw).hexdigest(),
    }
    return json.dumps(document, indent=2, ensure_ascii=False).encode("utf-8")


def _open_archive(path: Path, *, password: str) -> bytes:
    """解开加密归档；口令错误/被篡改/被截断一律抛中文 ``ValueError``。

    ``.lhbak`` 的头部是明文 JSON、正文才是密文。文件被截断时可能在
    ``json.loads`` 就失败，也可能 base64 解码失败、或 AES-GCM 校验失败；
    三种情况都要给出可读原因，而不是把 ``JSONDecodeError`` 或
    ``InvalidTag`` 直接甩给用户。
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
        document = json.loads(text)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(
            "备份包已损坏或未完整复制（加密头部无法解析），无法恢复；"
            "现有数据未被改动。请回源机重新导出。"
        ) from exc
    if not isinstance(document, dict) or document.get("format") != _ENCRYPTED_FORMAT:
        raise ValueError("不是受支持的加密备份格式（不是本程序导出的 .lhbak 文件）")
    try:
        payload = base64.b64decode(document["payload"])
        salt = base64.b64decode(document["kdf"]["salt"])
        nonce = base64.b64decode(document["nonce"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            "备份包已损坏或未完整复制（加密数据缺失），无法恢复；"
            "现有数据未被改动。请回源机重新导出。"
        ) from exc
    key = _derive_key(password, salt)
    try:
        raw = AESGCM(key).decrypt(nonce, payload, None)
    except InvalidTag as exc:
        raise ValueError(
            "口令错误，或备份包已损坏/被篡改，无法解密；现有数据未被改动。"
        ) from exc
    expected = str(document.get("payload_sha256") or "")
    if expected and hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError(
            "备份包内容已损坏（哈希不符），无法恢复；现有数据未被改动。"
        )
    return raw


def _missing_parents(path: Path) -> List[Path]:
    """返回 ``path`` 及其尚未存在的祖先，最深的在前（用于回滚时逐层删）。"""
    missing: List[Path] = []
    current = path
    while not current.exists() and current != current.parent:
        missing.append(current)
        current = current.parent
    return missing


class _SanitisingBackupManager(_FileBackupManager):
    """CLI 用的真实备份管理器：明文归档跳过凭据/密钥文件。

    ``encryption_enabled=True`` 时保留全部文件——那时外层已经由口令
    AES-256-GCM 保护，不再有“明文密钥躺在磁盘上”的问题。
    """

    _include_protected: bool = False
    _excluded_protected_total: int = 0
    _runtime_excluded_total: int = 0
    _workspace_file_count: int = 0
    _workspace_bytes: int = 0

    @staticmethod
    def _is_runtime_artifact(rel: str) -> bool:
        """日志/诊断/锁与临时文件属于原机运行痕迹，不随迁移包走。"""
        value = str(rel or "").replace("\\", "/").lstrip("./")
        if value.startswith(_RUNTIME_EXCLUDED_PREFIXES):
            return True
        return value.lower().endswith(_RUNTIME_EXCLUDED_SUFFIXES)

    def _collect_source_data(self, source, source_path):
        data = super()._collect_source_data(source, source_path)
        if data.get("type") == "directory":
            original = data.get("files") or {}
            kept = {
                rel: info
                for rel, info in original.items()
                if not self._is_runtime_artifact(rel)
            }
            runtime_excluded = len(original) - len(kept)
            self._runtime_excluded_total += runtime_excluded
            for rel, info in kept.items():
                if str(rel).startswith("workspace/"):
                    self._workspace_file_count += 1
                    self._workspace_bytes += int(info.get("size_bytes") or 0)
            data["files"] = kept
            data["file_count"] = len(kept)
            data["size_bytes"] = sum(
                int(info.get("size_bytes") or 0) for info in kept.values()
            )
            data["excluded_runtime"] = runtime_excluded
        if self._include_protected or not data.get("exists"):
            return data
        if data.get("type") == "file":
            if _is_unsafe_for_plaintext(str(source_path)):
                self._excluded_protected_total += 1
                return {
                    "exists": False,
                    "type": "skipped_protected",
                    "path": str(source_path),
                    "size_bytes": 0,
                }
            return data
        original = data.get("files") or {}
        kept = {
            rel: info
            for rel, info in original.items()
            if not _is_unsafe_for_plaintext(rel)
        }
        excluded = len(original) - len(kept)
        self._excluded_protected_total += excluded
        data["files"] = kept
        data["file_count"] = len(kept)
        data["size_bytes"] = sum(
            int(info.get("size_bytes") or 0) for info in kept.values()
        )
        data["excluded_protected"] = excluded
        return data


class BackupManager:
    """CLI adapter over the real, byte-accurate backup manager.

    All state is read from the archives on disk; nothing is simulated.
    """

    def __init__(
        self,
        backup_dir: Optional[str] = None,
        source_paths: Optional[Dict[Any, Path]] = None,
        data_dir: Optional[str] = None,
    ) -> None:
        self._data_dir = Path(data_dir) if data_dir else _default_data_dir()
        self._backup_dir = (
            Path(backup_dir) if backup_dir else _default_backup_dir(self._data_dir)
        )
        self._source_paths = self._normalise_source_paths(source_paths)
        self._manager: Optional[_FileBackupManager] = None

    # ------------------------------------------------------------------
    # Real manager wiring
    # ------------------------------------------------------------------

    def _normalise_source_paths(
        self, source_paths: Optional[Dict[Any, Path]]
    ) -> Dict[_FileBackupSource, Path]:
        if source_paths:
            result: Dict[_FileBackupSource, Path] = {}
            for key, value in source_paths.items():
                source = key if isinstance(key, _FileBackupSource) else _FileBackupSource(key)
                result[source] = Path(value)
            return result
        return {
            _FileBackupSource.CONFIG: self._data_dir / "config",
            _FileBackupSource.MEMORY_DATA: self._data_dir / "memory_store.db",
            _FileBackupSource.KNOWLEDGE_BASE: (
                self._data_dir / "data" / "knowledge_vectors.sqlite3"
            ),
            _FileBackupSource.USER_DATA: self._data_dir,
        }

    def _get_manager(self) -> _FileBackupManager:
        if self._manager is None:
            self._manager = _SanitisingBackupManager(
                backup_dir=self._backup_dir,
                source_paths=self._source_paths,
            )
            self._load_archives(self._manager)
        return self._manager

    def _load_archives(self, manager: _FileBackupManager) -> None:
        """Register metadata of archives already on disk."""
        if not self._backup_dir.exists():
            return
        for path in sorted(self._backup_dir.glob("backup_*")):
            if path.suffix not in (".gz", ".tar", _ENCRYPTED_SUFFIX):
                continue
            meta = self._metadata_from_archive(path)
            if meta is not None:
                manager.backups[meta.backup_id] = meta

    @staticmethod
    def _metadata_from_archive(path: Path) -> Optional[BackupMetadata]:
        try:
            if path.suffix == _ENCRYPTED_SUFFIX:
                # 加密归档的头部是明文（只有 payload 是密文），因此无需口令
                # 也能列出/校验它的存在与大小。
                payload = json.loads(path.read_text(encoding="utf-8"))
                if payload.get("format") != _ENCRYPTED_FORMAT:
                    return None
            else:
                raw = path.read_bytes()
                if path.suffix == ".gz":
                    raw = gzip.decompress(raw)
                payload = json.loads(raw.decode("utf-8"))
            return BackupMetadata(
                backup_id=str(payload["backup_id"]),
                backup_type=_FileBackupType(payload["backup_type"]),
                sources=[_FileBackupSource(s) for s in payload.get("sources", [])],
                timestamp=float(payload["timestamp"]),
                size_bytes=path.stat().st_size,
                checksum=_sha256_file(path),
                compressed=bool(payload.get("compressed", True)),
                encrypted=bool(payload.get("encrypted", False)),
                status=_FileBackupStatus.COMPLETED,
                file_path=str(path),
                parent_backup_id=payload.get("parent_backup_id"),
                description=str(payload.get("description", "")),
            )
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            return None

    def _resolve_sources(self, names: Optional[List[str]]) -> List[_FileBackupSource]:
        if not names:
            return list(self._source_paths.keys())
        resolved: List[_FileBackupSource] = []
        for name in names:
            if name in {s.value for s in _FileBackupSource}:
                source = _FileBackupSource(name)
                if source not in resolved:
                    resolved.append(source)
                continue
            # Unknown label: treated as an explicit path under USER_DATA.
            self._source_paths[_FileBackupSource.USER_DATA] = Path(name)
            if _FileBackupSource.USER_DATA not in resolved:
                resolved.append(_FileBackupSource.USER_DATA)
        return resolved

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    def create_backup(
        self,
        backup_type: str = "full",
        sources: Optional[List[str]] = None,
        description: str = "",
        encrypted: bool = False,
        password: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a real backup archive on disk.

        明文归档默认**跳过**凭据/密钥文件（``auth_secrets.json``、身份库、
        ``integrity.key``、``model_config.key``、``app.env``、
        ``model_registry.sqlite3`` 等）。``encrypted=True`` 时这些文件会一并
        打包，但整包由口令派生的 AES-256-GCM 密钥加密。
        """
        if encrypted:
            password = (password or os.environ.get(_PASSWORD_ENV) or "").strip()
            if not password:
                raise ValueError(
                    "--encrypt 需要口令：请传 --password 或设置 "
                    f"{_PASSWORD_ENV}（拒绝退化为明文归档）。"
                    "口令一旦丢失，备份内容无法恢复，只能回源机重新导出。"
                )
        started = time.perf_counter()
        manager = self._get_manager()
        if isinstance(manager, _SanitisingBackupManager):
            manager._include_protected = bool(encrypted)
            manager._excluded_protected_total = 0
            manager._runtime_excluded_total = 0
            manager._workspace_file_count = 0
            manager._workspace_bytes = 0
        meta = manager.create_backup(
            backup_type=_FileBackupType(backup_type),
            sources=self._resolve_sources(sources),
            description=description,
            compress=True,
        )
        export_seconds = time.perf_counter() - started
        sealed = False
        encrypt_started = time.perf_counter()
        if encrypted:
            meta = self._seal_archive_on_disk(meta, password or "")
            sealed = True
        encrypt_seconds = time.perf_counter() - encrypt_started if encrypted else 0.0
        excluded = getattr(manager, "_excluded_protected_total", 0)
        runtime_excluded = getattr(manager, "_runtime_excluded_total", 0)
        workspace_files = getattr(manager, "_workspace_file_count", 0)
        workspace_bytes = getattr(manager, "_workspace_bytes", 0)
        return {
            "backup_id": meta.backup_id,
            "type": meta.backup_type.value,
            "sources": [s.value for s in meta.sources],
            "status": meta.status.value,
            "size_bytes": meta.size_bytes,
            "size_human": meta.size_human,
            "checksum": meta.checksum,
            "created_at": meta.datetime.isoformat(),
            "completed_at": meta.datetime.isoformat(),
            "encrypted": meta.encrypted,
            "compressed": meta.compressed,
            "sealed": sealed,
            "excluded_protected_files": excluded,
            # 迁移包不带日志/诊断（原机路径与用户名）；如实记账，别让用户以为
            # "整个目录都带走了"。
            "excluded_runtime_files": runtime_excluded,
            # 附件（用户上传的文件、生成的图表/文档）是随包走的：这里给出
            # 实测数量和字节，界面导出有 64MB/单文件上限，CLI 没有。
            "attachments": {
                "included": True,
                "files": workspace_files,
                "bytes": workspace_bytes,
                "note": (
                    "工作区里用户上传/生成的文件随包原样带走；"
                    "日志与诊断包不带（新机会重新生成）。"
                ),
            },
            "description": meta.description,
            "file_path": meta.file_path,
            # 实测耗时：让用户对"导出多久、加密多久"有预期，而不是猜。
            "export_seconds": round(export_seconds, 3),
            "encrypt_seconds": round(encrypt_seconds, 3),
            "total_seconds": round(export_seconds + encrypt_seconds, 3),
            "password_policy": (
                "口令不写入备份包，也不写日志；口令丢失后无法恢复内容，"
                "只能回源机重新导出（没有后门或万能口令）。"
                if encrypted
                else "未加密归档：任何人都能打开，不要放凭据/密钥；"
                     "它不用于换机迁移。"
            ),
        }

    def _seal_archive_on_disk(self, meta: BackupMetadata, password: str) -> BackupMetadata:
        """把明文 gzip 归档替换为 ``.lhbak`` 加密归档，明文文件随即删除。"""
        plain_path = Path(meta.file_path)
        raw = plain_path.read_bytes()
        header = {
            "backup_id": meta.backup_id,
            "backup_type": meta.backup_type.value,
            "sources": [s.value for s in meta.sources],
            "timestamp": meta.timestamp,
            "description": meta.description,
            "parent_backup_id": meta.parent_backup_id,
        }
        sealed_bytes = _seal_archive(raw, password=password, header=header)
        sealed_path = plain_path.parent / f"{meta.backup_id}{_ENCRYPTED_SUFFIX}"
        sealed_path.write_bytes(sealed_bytes)
        plain_path.unlink(missing_ok=True)
        meta.file_path = str(sealed_path)
        meta.size_bytes = sealed_path.stat().st_size
        meta.checksum = _sha256_file(sealed_path)
        meta.encrypted = True
        meta.compressed = True
        return meta

    def list_backups(
        self,
        backup_type: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """List backups found on disk."""
        manager = self._get_manager()
        backups = manager.list_backups(
            backup_type=_FileBackupType(backup_type) if backup_type else None,
            status=_FileBackupStatus(status) if status else None,
        )
        total = len(backups)
        backups = backups[offset: offset + limit]
        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "items": [
                {
                    "backup_id": b.backup_id,
                    "type": b.backup_type.value,
                    "status": b.status.value,
                    "sources": [s.value for s in b.sources],
                    "size_bytes": b.size_bytes,
                    "created_at": b.datetime.isoformat(),
                    "encrypted": b.encrypted,
                }
                for b in backups
            ],
        }

    def get_backup_info(self, backup_id: str) -> Dict[str, Any]:
        """Get detailed info about a specific backup."""
        meta = self._get_manager().get_backup(backup_id)
        if meta is None:
            raise ValueError(f"Backup not found: {backup_id}")
        return {
            "backup_id": meta.backup_id,
            "type": meta.backup_type.value,
            "sources": [s.value for s in meta.sources],
            "status": meta.status.value,
            "size_bytes": meta.size_bytes,
            "size_human": meta.size_human,
            "checksum": meta.checksum,
            "created_at": meta.datetime.isoformat(),
            "completed_at": meta.datetime.isoformat(),
            "encrypted": meta.encrypted,
            "compressed": meta.compressed,
            "description": meta.description,
            "file_path": meta.file_path,
        }

    def restore_backup(
        self,
        backup_id: str,
        sources: Optional[List[str]] = None,
        target: Optional[str] = None,
        password: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Restore a backup into a clean data directory (or back in place).

        换机恢复的三条硬约束：

        1. **先解密、再写盘**：口令错误、缺口令、包被截断都在写入前失败，
           目标目录保持原样；
        2. **按真实布局落盘**：``config/``、``memory_store.db``、
           ``data/knowledge_vectors.sqlite3``、工作区不能套一层源枚举目录；
        3. **先写暂存、后提交**：提交阶段失败会回滚本次新写入的文件，
           不留半恢复的数据目录。
        """
        started = time.perf_counter()
        manager = self._get_manager()
        meta = manager.get_backup(backup_id)
        if meta is None:
            fallback = self._archive_file_for(backup_id)
            if fallback is not None:
                raise ValueError(
                    "备份包已损坏或未完整复制（无法读取头部），无法恢复；"
                    "现有数据未被改动。请回源机重新导出。"
                )
            raise ValueError(f"Backup not found: {backup_id}")
        archive = Path(meta.file_path) if meta.file_path else None
        if archive is None or not archive.exists():
            raise ValueError(f"备份文件不存在：{meta.file_path or backup_id}")

        payload = self._read_archive_payload(meta, archive, password)
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ValueError(
                "备份包内容已损坏（缺少数据段），无法恢复；现有数据未被改动。"
            )

        # 包内源机根目录（USER_DATA 的绝对路径）；暂存阶段用它把执行日志/迁移
        # 标记 JSON 里写死的源机路径改到新数据目录。
        user_data = data.get("user_data")
        source_data_root = (
            str(user_data.get("path") or "") if isinstance(user_data, dict) else ""
        )

        selected = list(sources) if sources else [s.value for s in meta.sources]
        plan, skipped = self._plan_restore(data, selected, target)
        if not plan:
            raise ValueError(
                "备份包内没有可恢复的文件（源为空或被安全策略排除），"
                "请确认导出时是否用了加密归档。"
            )

        target_root: Optional[Path] = None
        if target:
            target_root = Path(target)
            if target_root.exists() and not target_root.is_dir():
                raise ValueError(f"恢复目标不是目录：{target_root}")
            self._require_pristine_target(target_root)
        else:
            target_root = None

        stage_root = Path(tempfile.mkdtemp(prefix="liuhao-restore-stage-"))
        created_files: List[Path] = []
        created_dirs: List[Path] = []
        existed: set[str] = set()
        path_rewrites = 0
        source_path_rewrites = 0
        try:
            staged = self._stage_restore(plan, stage_root)
            if target_root is not None:
                # 工件索引里存的是源机的绝对路径，必须在提交前改成新目录，
                # 否则新机上"文件都在"却会被判成缺失/打不开。
                path_rewrites = self._rewrite_staged_artifact_paths(staged, target_root)
                source_path_rewrites = self._rewrite_staged_source_paths(
                    staged, target_root, source_data_root
                )
            created_files, created_dirs, existed = self._commit_restore(staged)
        except ValueError:
            shutil.rmtree(stage_root, ignore_errors=True)
            raise
        except Exception as exc:  # noqa: BLE001 - 统一成可读失败，并回滚
            self._rollback_restore(created_files, created_dirs, existed)
            shutil.rmtree(stage_root, ignore_errors=True)
            raise ValueError(
                f"恢复过程中写入失败（{exc}）；本次新写入的文件已回滚，"
                "目标目录不会留下半恢复数据。"
            ) from exc
        shutil.rmtree(stage_root, ignore_errors=True)

        total_bytes = sum(size for _, _, size in plan)
        return {
            "backup_id": backup_id,
            "status": "restored",
            "restored_sources": [s.value for s in meta.sources if s.value in selected],
            "target": str(target_root) if target_root is not None else "original_location",
            "files_restored": len(plan),
            "bytes_restored": total_bytes,
            "skipped_sources": skipped,
            "encrypted": meta.encrypted,
            "restored_at": datetime.utcnow().isoformat(),
            "restore_seconds": round(time.perf_counter() - started, 3),
            # 工件索引里的绝对路径被改写到新数据目录的行数；0 表示没有该索引。
            "path_rewrites": path_rewrites,
            # 执行日志/迁移标记 JSON 文本里被改写的源机路径条数。
            "source_path_rewrites": source_path_rewrites,
        }

    # ------------------------------------------------------------------
    # 恢复辅助
    # ------------------------------------------------------------------

    def _archive_file_for(self, backup_id: str) -> Optional[Path]:
        """按 id 在备份目录里找归档（头部损坏时也能给出可读错误）。"""
        for suffix in (".tar.gz", ".tar", _ENCRYPTED_SUFFIX):
            candidate = self._backup_dir / f"{backup_id}{suffix}"
            if candidate.is_file():
                return candidate
        return None

    def _read_archive_payload(
        self,
        meta: BackupMetadata,
        archive: Path,
        password: Optional[str],
    ) -> Dict[str, Any]:
        if meta.encrypted:
            password = (password or os.environ.get(_PASSWORD_ENV) or "").strip()
            if not password:
                raise ValueError(
                    "该备份已加密：请用 --password 提供口令（或设置 "
                    f"{_PASSWORD_ENV}）。口令丢失后无法恢复，只能回源机重新导出；"
                    "程序没有后门或万能口令。"
                )
            compressed = _open_archive(archive, password=password)
        else:
            try:
                compressed = archive.read_bytes()
            except OSError as exc:
                raise ValueError(f"备份文件读取失败：{archive}（{exc}）") from exc
        try:
            raw = gzip.decompress(compressed)
        except (OSError, gzip.BadGzipFile, EOFError, zlib.error) as exc:
            raise ValueError(
                "备份包已损坏或未完整复制（压缩段无法解开），无法恢复；"
                "现有数据未被改动。"
            ) from exc
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(
                "备份包内容已损坏或被截断，无法恢复；现有数据未被改动。"
            ) from exc
        if not isinstance(payload, dict):
            raise ValueError("备份包内容格式无效，无法恢复；现有数据未被改动。")
        return payload

    @staticmethod
    def _require_pristine_target(target_root: Path) -> None:
        """换机恢复只接空目录：任何已有内容都拒绝，绝不拼接/覆盖。"""
        if not target_root.exists():
            return
        blockers = [
            name for name in _PRISTINE_BLOCKERS if (target_root / name).exists()
        ]
        if blockers:
            raise ValueError(
                "目标目录里已经有一整套数据（已含 "
                + "、".join(blockers)
                + "），拒绝恢复，避免覆盖成一份拼接数据。"
                "请换一个空目录；若确要原地恢复，请去掉 --target。"
            )
        try:
            entries = sorted(path.name for path in target_root.iterdir())
        except OSError as exc:
            raise ValueError(f"目标目录无法读取：{target_root}（{exc}）") from exc
        if entries:
            shown = "、".join(entries[:5])
            more = "" if len(entries) <= 5 else f" 等 {len(entries)} 项"
            raise ValueError(
                f"目标目录不是空目录（已有 {shown}{more}）。这个目录不干净，"
                "请换一个空目录再恢复；程序不会把新数据和旧文件合并。"
            )

    def _plan_restore(
        self,
        data: Dict[str, Any],
        selected: List[str],
        target: Optional[str],
    ) -> tuple[List[tuple[Path, Dict[str, Any], int]], List[str]]:
        """把归档条目映射成 (暂存相对路径, 条目, 字节数) 的写入计划。

        ``target`` 为空时按本机已配置的源路径原地恢复；有 ``target`` 时按
        真实数据布局铺到新目录（``config/``、``memory_store.db`` 等）。
        """
        plan: List[tuple[Path, Dict[str, Any], int]] = []
        seen: Dict[str, bool] = {}
        skipped: List[str] = []
        base = Path(target) if target else None

        for source_value in selected:
            source_data = data.get(source_value)
            if not isinstance(source_data, dict) or not source_data.get("exists"):
                skipped.append(source_value)
                continue
            try:
                source = _FileBackupSource(source_value)
            except ValueError:
                source = None
            layout = _LAYOUT_RELATIVE.get(source) if source is not None else None

            if source_data.get("type") == "file":
                entry = source_data
                size = int(entry.get("size_bytes") or 0)
                if base is not None:
                    rel = layout or Path(str(entry.get("path") or source_value)).name
                    final = base / rel
                else:
                    final = (
                        self._source_paths.get(source)
                        if source is not None
                        else None
                    ) or Path(str(entry.get("path") or source_value))
                self._append_plan(plan, seen, final, entry, size)
                continue

            if source_data.get("type") == "directory":
                files = source_data.get("files") or {}
                if base is not None:
                    root = base if layout in (None, ".") else base / str(layout)
                    if layout is None:
                        root = base / Path(str(source_data.get("path") or source_value)).name
                else:
                    root = (
                        self._source_paths.get(source)
                        if source is not None
                        else None
                    ) or Path(str(source_data.get("path") or source_value))
                for rel_path, entry in files.items():
                    if not isinstance(entry, dict):
                        continue
                    self._append_plan(
                        plan,
                        seen,
                        Path(root) / str(rel_path),
                        entry,
                        int(entry.get("size_bytes") or 0),
                    )
                continue

            skipped.append(source_value)

        return plan, skipped

    @staticmethod
    def _append_plan(
        plan: List[tuple[Path, Dict[str, Any], int]],
        seen: Dict[str, bool],
        final: Path,
        entry: Dict[str, Any],
        size: int,
    ) -> None:
        # CONFIG 与 USER_DATA 天然重叠（整目录里也含 config），同一落点只写一次。
        key = os.path.normcase(os.path.abspath(str(final)))
        if key in seen:
            return
        seen[key] = True
        plan.append((final, entry, size))

    @staticmethod
    def _rewrite_staged_artifact_paths(
        staged: List[tuple[Path, Path]], target_root: Path
    ) -> int:
        """把暂存工件索引里的源机绝对路径改到新数据目录。

        工件索引有两列绝对路径（``workspace`` 与 ``abs_path``）；新机不修的话，
        文件明明随包带过来了，验证/打开时仍会去源机的 ``D:\\LiuHaoData`` 找。
        用 ``path_key``（工作区相对路径）重算是最低风险的做法，工件 id 不变。
        """
        workspace = (target_root / "workspace").resolve()
        wanted = os.path.normcase(
            os.path.abspath(str(target_root / "workspace" / "liuhao_artifacts.db"))
        )
        db_path: Optional[Path] = None
        for stage_path, final in staged:
            if os.path.normcase(os.path.abspath(str(final))) == wanted:
                db_path = stage_path
                break
        if db_path is None:
            return 0
        try:
            from ..artifacts.index import rewrite_workspace_paths

            return rewrite_workspace_paths(
                root=str(workspace), db_path=str(db_path)
            )
        except (OSError, sqlite3.Error) as exc:
            raise ValueError(
                f"恢复后的工件索引无法修正路径（{exc}）；"
                "目标目录尚未写入，请换一个空目录重试。"
            ) from exc

    @staticmethod
    def _rewrite_staged_source_paths(
        staged: List[tuple[Path, Path]], target_root: Path, source_root: str
    ) -> int:
        """改写执行日志/迁移标记 JSON 里写死的源机绝对路径。

        这两个库把路径放进 JSON 文本（``journal_events.payload``、
        ``account_memory_migrations.details``），不能按列重算；按前缀替换到
        新数据目录，避免新机上报错信息/恢复提示仍指向旧盘。
        """
        if not source_root:
            return 0
        old = os.path.abspath(source_root)
        new = os.path.abspath(str(target_root))
        specs = {
            os.path.normcase(
                os.path.abspath(str(target_root / "workspace" / "liuhao_execution.journal"))
            ): ("journal_events", "payload"),
            os.path.normcase(
                os.path.abspath(str(target_root / "workspace" / "account_memory.sqlite3"))
            ): ("account_memory_migrations", "details"),
        }
        from ..backup.path_rewrite import rewrite_sqlite_json_column

        changed = 0
        for stage_path, final in staged:
            spec = specs.get(os.path.normcase(os.path.abspath(str(final))))
            if spec is None:
                continue
            try:
                changed += rewrite_sqlite_json_column(
                    str(stage_path), spec[0], spec[1], old, new
                )
            except (OSError, sqlite3.Error) as exc:
                raise ValueError(
                    f"恢复后的路径字段无法修正（{exc}）；"
                    "目标目录尚未写入，请换一个空目录重试。"
                ) from exc
        return changed

    def _stage_restore(
        self,
        plan: List[tuple[Path, Dict[str, Any], int]],
        stage_root: Path,
    ) -> List[tuple[Path, Path]]:
        """先把所有文件写到暂存区；任何解码失败都在提交前暴露。"""
        staged: List[tuple[Path, Path]] = []
        for index, (final, entry, _size) in enumerate(plan):
            stage_path = stage_root / f"{index:05d}" / final.name
            stage_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                _FileBackupManager._write_restored_file(
                    stage_path, entry, bool(entry.get("sqlite"))
                )
            except ValueError as exc:
                raise ValueError(
                    f"备份包内文件损坏（{final.name}）：{exc}；现有数据未被改动。"
                ) from exc
            staged.append((stage_path, final))
        return staged

    @staticmethod
    def _commit_restore(
        staged: List[tuple[Path, Path]],
    ) -> tuple[List[Path], List[Path], set[str]]:
        """把暂存文件搬到最终位置，记录新建文件/目录用于失败回滚。"""
        created_files: List[Path] = []
        created_dirs: List[Path] = []
        existed: set[str] = set()
        for stage_path, final in staged:
            created_dirs.extend(
                path
                for path in _missing_parents(final.parent)
                if path not in created_dirs
            )
            final.parent.mkdir(parents=True, exist_ok=True)
            key = os.path.normcase(os.path.abspath(str(final)))
            if final.exists():
                existed.add(key)
            else:
                created_files.append(final)
            shutil.copy2(stage_path, final)
        return created_files, created_dirs, existed

    @staticmethod
    def _rollback_restore(
        created_files: List[Path],
        created_dirs: List[Path],
        existed: set[str],
    ) -> None:
        for path in reversed(created_files):
            key = os.path.normcase(os.path.abspath(str(path)))
            if key in existed:
                continue
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        for path in reversed(created_dirs):
            try:
                path.rmdir()
            except OSError:
                pass

    def verify_backup(self, backup_id: str, password: Optional[str] = None) -> Dict[str, Any]:
        """Verify the archive checksum, size and readability."""
        manager = self._get_manager()
        meta = manager.get_backup(backup_id)
        if meta is None:
            raise ValueError(f"Backup not found: {backup_id}")
        path = Path(meta.file_path) if meta.file_path else None
        if path is None or not path.exists():
            return {
                "backup_id": backup_id,
                "verified": False,
                "checksum_match": False,
                "size_match": False,
                "error": "backup file missing",
            }
        actual_checksum = _sha256_file(path)
        checksum_match = actual_checksum == meta.checksum
        size_match = path.stat().st_size == meta.size_bytes
        decoded = _archive_reads(path)
        decrypted = False
        if decoded and meta.encrypted:
            password = (password or os.environ.get(_PASSWORD_ENV) or "").strip()
            if password:
                try:
                    _open_archive(path, password=password)
                    decrypted = True
                except (OSError, ValueError, json.JSONDecodeError):
                    decoded = False
        verified = checksum_match and size_match and decoded
        if verified:
            manager.verify_backup(backup_id)
        return {
            "backup_id": backup_id,
            "verified": verified,
            "checksum_match": checksum_match,
            "size_match": size_match,
            "encrypted": meta.encrypted,
            "decrypted": decrypted,
            "verified_at": datetime.utcnow().isoformat(),
        }

    def prune_backups(
        self,
        keep_last: int = 7,
        older_than_days: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Prune old backups according to the real retention policy."""
        manager = self._get_manager()
        if older_than_days:
            manager.retention_policy = RetentionPolicy(
                policy_type=RetentionPolicyType.BOTH,
                max_backups=keep_last,
                max_age_days=older_than_days,
                always_keep_full=True,
            )
        else:
            manager.retention_policy = RetentionPolicy(
                policy_type=RetentionPolicyType.COUNT_BASED,
                max_backups=keep_last,
                always_keep_full=True,
            )
        pruned = manager.prune_old_backups()
        return {
            "pruned": pruned,
            "remaining": len(manager.backups),
            "pruned_ids": [],
            "keep_last": keep_last,
        }

    def export_backup(
        self,
        backup_id: str,
        output_path: str,
    ) -> Dict[str, Any]:
        """Copy the real archive to ``output_path``."""
        meta = self._get_manager().get_backup(backup_id)
        if meta is None or not meta.file_path:
            raise ValueError(f"Backup not found: {backup_id}")
        source = Path(meta.file_path)
        if not source.exists():
            raise ValueError(f"Backup file missing: {source}")
        destination = Path(output_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        return {
            "backup_id": backup_id,
            "output_path": str(destination),
            "size_bytes": destination.stat().st_size,
            "exported_at": datetime.utcnow().isoformat(),
            "status": "exported",
        }


def _sha256_file(path: Path) -> str:
    sha256 = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(8192), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def _archive_reads(path: Path) -> bool:
    return BackupManager._metadata_from_archive(path) is not None


# =====================================================================
# CLI Parser
# =====================================================================


def register_backup_commands(
    subparsers: argparse._SubParsersAction,
) -> None:
    """Register backup subcommands under the given argparse subparser."""
    p_backup = subparsers.add_parser("backup", help="Backup and restore management")
    backup_sub = p_backup.add_subparsers(dest="backup_command", required=True)

    # backup create
    p_create = backup_sub.add_parser("create", help="Create a new backup")
    p_create.add_argument(
        "--type",
        default="full",
        choices=["full", "incremental", "differential"],
        help="Backup type",
    )
    p_create.add_argument(
        "--source",
        action="append",
        dest="sources",
        help="Source to include (repeatable)",
    )
    p_create.add_argument("--description", default="", help="Backup description")
    p_create.add_argument(
        "--encrypt",
        action="store_true",
        help=(
            "加密归档（必须给口令；加密包才包含身份库/主密钥等受保护文件）。"
            "口令丢失后内容无法恢复，只能回源机重新导出。"
        ),
    )
    p_create.add_argument(
        "--password",
        default=None,
        help=(
            f"加密口令（或设置 {_PASSWORD_ENV}）；仅配合 --encrypt。"
            "口令不写进备份包/日志，丢失后没有后门。"
        ),
    )

    # backup list
    p_list = backup_sub.add_parser("list", help="List backups")
    p_list.add_argument("--type", help="Filter by backup type")
    p_list.add_argument("--status", help="Filter by status")
    p_list.add_argument("--limit", type=int, default=20, help="Result limit")
    p_list.add_argument("--offset", type=int, default=0, help="Pagination offset")

    # backup info
    p_info = backup_sub.add_parser("info", help="Get backup details")
    p_info.add_argument("backup_id", help="Backup identifier")

    # backup restore
    p_restore = backup_sub.add_parser("restore", help="Restore from a backup")
    p_restore.add_argument("backup_id", help="Backup identifier")
    p_restore.add_argument(
        "--source",
        action="append",
        dest="sources",
        help="Specific source to restore (repeatable)",
    )
    p_restore.add_argument(
        "--target",
        help="换机恢复目标目录（必须是空目录；按真实数据布局铺开，可删掉参数原地恢复）",
    )
    p_restore.add_argument(
        "--password",
        default=None,
        help=(
            f"加密归档口令（或设置 {_PASSWORD_ENV}）。"
            "口令丢失则无法恢复（程序没有万能口令），只能回源机重新导出。"
        ),
    )

    # backup verify
    p_verify = backup_sub.add_parser("verify", help="Verify backup integrity")
    p_verify.add_argument("backup_id", help="Backup identifier")
    p_verify.add_argument(
        "--password",
        default=None,
        help=f"Password for an encrypted archive (or set {_PASSWORD_ENV})",
    )

    # backup prune
    p_prune = backup_sub.add_parser("prune", help="Prune old backups")
    p_prune.add_argument("--keep-last", type=int, default=7, help="Minimum backups to keep")
    p_prune.add_argument("--older-than-days", type=int, help="Remove backups older than N days")
    p_prune.add_argument("--force", action="store_true", help="Confirm prune operation")

    # backup export
    p_export = backup_sub.add_parser("export", help="Export a backup")
    p_export.add_argument("backup_id", help="Backup identifier")
    p_export.add_argument("--output", required=True, help="Output file path")


# =====================================================================
# Command Handler
# =====================================================================


def handle_backup_command(
    args: argparse.Namespace, manager: BackupManager
) -> int:
    """Dispatch a backup CLI command to the appropriate handler."""
    try:
        if args.backup_command == "create":
            result = manager.create_backup(
                backup_type=args.type,
                sources=args.sources,
                description=args.description,
                encrypted=args.encrypt,
                password=getattr(args, "password", None),
            )
        elif args.backup_command == "list":
            result = manager.list_backups(
                backup_type=getattr(args, "type", None),
                status=args.status,
                limit=args.limit,
                offset=args.offset,
            )
        elif args.backup_command == "info":
            result = manager.get_backup_info(args.backup_id)
        elif args.backup_command == "restore":
            result = manager.restore_backup(
                args.backup_id,
                sources=args.sources,
                target=args.target,
                password=getattr(args, "password", None),
            )
        elif args.backup_command == "verify":
            result = manager.verify_backup(
                args.backup_id, password=getattr(args, "password", None)
            )
        elif args.backup_command == "prune":
            if not args.force:
                print(json.dumps({"error": "Use --force to confirm backup prune"}, indent=2))
                return 1
            result = manager.prune_backups(
                keep_last=args.keep_last,
                older_than_days=args.older_than_days,
            )
        elif args.backup_command == "export":
            result = manager.export_backup(args.backup_id, args.output)
        else:
            print(f"Unknown backup command: {args.backup_command}")
            return 1

        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}, indent=2, ensure_ascii=False))
        return 1
    except NotImplementedError as exc:
        print(json.dumps({"error": str(exc)}, indent=2, ensure_ascii=False))
        return 1
