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
import gzip
import hashlib
import json
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.backup.backup_manager import (
    BackupManager as _FileBackupManager,
    BackupMetadata,
    BackupSource as _FileBackupSource,
    BackupStatus as _FileBackupStatus,
    BackupType as _FileBackupType,
    RetentionPolicy,
    RetentionPolicyType,
)


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
            _FileBackupSource.CONFIG: self._data_dir.parent / "config",
            _FileBackupSource.MEMORY_DATA: self._data_dir / "memory",
            _FileBackupSource.KNOWLEDGE_BASE: self._data_dir / "knowledge",
            _FileBackupSource.USER_DATA: self._data_dir,
        }

    def _get_manager(self) -> _FileBackupManager:
        if self._manager is None:
            self._manager = _FileBackupManager(
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
            if path.suffix not in (".gz", ".tar"):
                continue
            meta = self._metadata_from_archive(path)
            if meta is not None:
                manager.backups[meta.backup_id] = meta

    @staticmethod
    def _metadata_from_archive(path: Path) -> Optional[BackupMetadata]:
        try:
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
                compressed=path.suffix == ".gz",
                encrypted=False,
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
    ) -> Dict[str, Any]:
        """Create a real backup archive on disk."""
        if encrypted:
            raise NotImplementedError(
                "encrypted backups are not implemented: this manager writes "
                "plaintext archives. Re-run without --encrypt."
            )
        manager = self._get_manager()
        meta = manager.create_backup(
            backup_type=_FileBackupType(backup_type),
            sources=self._resolve_sources(sources),
            description=description,
            compress=True,
        )
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
    ) -> Dict[str, Any]:
        """Restore from a backup archive (really writes the files)."""
        manager = self._get_manager()
        if manager.get_backup(backup_id) is None:
            raise ValueError(f"Backup not found: {backup_id}")
        restore_sources = (
            [_FileBackupSource(s) for s in sources] if sources else None
        )
        result = manager.restore_backup(
            backup_id,
            sources=restore_sources,
            target_dir=Path(target) if target else None,
        )
        if not result.success:
            raise ValueError(f"Restore failed: {result.error_message}")
        return {
            "backup_id": backup_id,
            "status": "restored",
            "restored_sources": [s.value for s in result.restored_sources],
            "target": target or "original_location",
            "files_restored": result.total_files_restored,
            "bytes_restored": result.total_bytes_restored,
            "restored_at": datetime.utcnow().isoformat(),
        }

    def verify_backup(self, backup_id: str) -> Dict[str, Any]:
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
        verified = checksum_match and size_match and decoded
        if verified:
            manager.verify_backup(backup_id)
        return {
            "backup_id": backup_id,
            "verified": verified,
            "checksum_match": checksum_match,
            "size_match": size_match,
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
    p_create.add_argument("--encrypt", action="store_true", help="Encrypt the backup")

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
    p_restore.add_argument("--target", help="Restore target path")

    # backup verify
    p_verify = backup_sub.add_parser("verify", help="Verify backup integrity")
    p_verify.add_argument("backup_id", help="Backup identifier")

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
                args.backup_id, sources=args.sources, target=args.target
            )
        elif args.backup_command == "verify":
            result = manager.verify_backup(args.backup_id)
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
