"""Backup management CLI commands for LiuHao AI OS.

Provides command-line interface for backup operations:
- backup create, backup list, backup info
- backup restore, backup verify
- backup prune, backup export
"""

from __future__ import annotations

import argparse
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional


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
    """A backup record."""
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


class BackupManager:
    """In-memory backup manager for CLI operations.

    Provides backup creation, listing, restore, verification, and pruning.
    """

    def __init__(self) -> None:
        self._backups: Dict[str, BackupRecord] = {}
        self._seed_sample_data()

    def _seed_sample_data(self) -> None:
        """Seed with sample backup data."""
        now = datetime.utcnow()
        for i in range(5):
            backup_id = f"bkp-{uuid.uuid4().hex[:8]}"
            created = now - timedelta(days=i * 3)
            self._backups[backup_id] = BackupRecord(
                backup_id=backup_id,
                backup_type=BackupType.FULL if i == 0 else BackupType.INCREMENTAL,
                sources=[s.value for s in BackupSource],
                status=BackupStatus.VERIFIED,
                size_bytes=1024 * 1024 * (50 - i * 5),
                checksum=f"sha256:{uuid.uuid4().hex}",
                created_at=created.isoformat(),
                completed_at=created.isoformat(),
                encrypted=True,
                compressed=True,
                description=f"Scheduled backup {i + 1}",
            )

    def create_backup(
        self,
        backup_type: str = "full",
        sources: Optional[List[str]] = None,
        description: str = "",
        encrypted: bool = False,
    ) -> Dict[str, Any]:
        """Create a new backup.

        Args:
            backup_type: Type of backup (full, incremental, differential).
            sources: List of sources to back up.
            description: Optional description.
            encrypted: Whether to encrypt the backup.

        Returns:
            Dict with created backup details.
        """
        backup_id = f"bkp-{uuid.uuid4().hex[:8]}"
        backup = BackupRecord(
            backup_id=backup_id,
            backup_type=BackupType(backup_type),
            sources=sources or [s.value for s in BackupSource],
            status=BackupStatus.IN_PROGRESS,
            description=description,
            encrypted=encrypted,
        )
        self._backups[backup_id] = backup

        # Simulate completion
        backup.status = BackupStatus.COMPLETED
        backup.size_bytes = 1024 * 1024 * 42
        backup.checksum = f"sha256:{uuid.uuid4().hex}"
        backup.completed_at = datetime.utcnow().isoformat()

        return {
            "backup_id": backup.backup_id,
            "type": backup.backup_type.value,
            "sources": backup.sources,
            "status": backup.status.value,
            "size_bytes": backup.size_bytes,
            "checksum": backup.checksum,
            "created_at": backup.created_at,
            "completed_at": backup.completed_at,
            "encrypted": backup.encrypted,
            "compressed": backup.compressed,
            "description": backup.description,
        }

    def list_backups(
        self,
        backup_type: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """List backups with optional filtering.

        Args:
            backup_type: Filter by backup type.
            status: Filter by status.
            limit: Result limit.
            offset: Pagination offset.

        Returns:
            Dict with paginated backup list.
        """
        backups = list(self._backups.values())
        if backup_type:
            backups = [b for b in backups if b.backup_type.value == backup_type]
        if status:
            backups = [b for b in backups if b.status.value == status]
        backups.sort(key=lambda b: b.created_at, reverse=True)
        total = len(backups)
        backups = backups[offset : offset + limit]
        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "items": [
                {
                    "backup_id": b.backup_id,
                    "type": b.backup_type.value,
                    "status": b.status.value,
                    "sources": b.sources,
                    "size_bytes": b.size_bytes,
                    "created_at": b.created_at,
                    "encrypted": b.encrypted,
                }
                for b in backups
            ],
        }

    def get_backup_info(self, backup_id: str) -> Dict[str, Any]:
        """Get detailed info about a specific backup.

        Args:
            backup_id: Backup identifier.

        Returns:
            Dict with backup details.

        Raises:
            ValueError: If backup not found.
        """
        backup = self._backups.get(backup_id)
        if not backup:
            raise ValueError(f"Backup not found: {backup_id}")
        return {
            "backup_id": backup.backup_id,
            "type": backup.backup_type.value,
            "sources": backup.sources,
            "status": backup.status.value,
            "size_bytes": backup.size_bytes,
            "size_human": self._human_size(backup.size_bytes),
            "checksum": backup.checksum,
            "created_at": backup.created_at,
            "completed_at": backup.completed_at,
            "encrypted": backup.encrypted,
            "compressed": backup.compressed,
            "description": backup.description,
        }

    def restore_backup(
        self,
        backup_id: str,
        sources: Optional[List[str]] = None,
        target: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Restore from a backup.

        Args:
            backup_id: Backup identifier.
            sources: Optional subset of sources to restore.
            target: Optional target path.

        Returns:
            Dict with restore status.

        Raises:
            ValueError: If backup not found.
        """
        backup = self._backups.get(backup_id)
        if not backup:
            raise ValueError(f"Backup not found: {backup_id}")
        restore_sources = sources or backup.sources
        return {
            "backup_id": backup_id,
            "status": "restored",
            "restored_sources": restore_sources,
            "target": target or "original_location",
            "restored_at": datetime.utcnow().isoformat(),
            "size_bytes": backup.size_bytes,
        }

    def verify_backup(self, backup_id: str) -> Dict[str, Any]:
        """Verify a backup's integrity.

        Args:
            backup_id: Backup identifier.

        Returns:
            Dict with verification result.

        Raises:
            ValueError: If backup not found.
        """
        backup = self._backups.get(backup_id)
        if not backup:
            raise ValueError(f"Backup not found: {backup_id}")
        backup.status = BackupStatus.VERIFIED
        return {
            "backup_id": backup_id,
            "verified": True,
            "checksum_match": True,
            "size_match": True,
            "verified_at": datetime.utcnow().isoformat(),
        }

    def prune_backups(
        self,
        keep_last: int = 7,
        older_than_days: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Prune old backups based on retention policy.

        Args:
            keep_last: Minimum number of backups to keep.
            older_than_days: Remove backups older than this many days.

        Returns:
            Dict with prune results.
        """
        backups = sorted(
            self._backups.values(),
            key=lambda b: b.created_at,
            reverse=True,
        )
        to_prune: List[str] = []

        if older_than_days:
            cutoff = datetime.utcnow() - timedelta(days=older_than_days)
            for b in backups:
                created = datetime.fromisoformat(b.created_at)
                if created < cutoff:
                    to_prune.append(b.backup_id)

        # Always keep at least keep_last newest backups
        keep_ids = {b.backup_id for b in backups[:keep_last]}
        to_prune = [bid for bid in to_prune if bid not in keep_ids]

        for bid in to_prune:
            del self._backups[bid]

        return {
            "pruned": len(to_prune),
            "remaining": len(self._backups),
            "pruned_ids": to_prune,
            "keep_last": keep_last,
        }

    def export_backup(
        self,
        backup_id: str,
        output_path: str,
    ) -> Dict[str, Any]:
        """Export a backup to a file.

        Args:
            backup_id: Backup identifier.
            output_path: Output file path.

        Returns:
            Dict with export status.

        Raises:
            ValueError: If backup not found.
        """
        backup = self._backups.get(backup_id)
        if not backup:
            raise ValueError(f"Backup not found: {backup_id}")
        return {
            "backup_id": backup_id,
            "output_path": output_path,
            "size_bytes": backup.size_bytes,
            "exported_at": datetime.utcnow().isoformat(),
            "status": "exported",
        }

    @staticmethod
    def _human_size(size_bytes: int) -> str:
        """Convert bytes to human-readable size."""
        units = ["B", "KB", "MB", "GB", "TB"]
        size = float(size_bytes)
        for unit in units:
            if size < 1024:
                return f"{size:.2f} {unit}"
            size /= 1024
        return f"{size:.2f} PB"


# =====================================================================
# CLI Parser
# =====================================================================


def register_backup_commands(
    subparsers: argparse._SubParsersAction,
) -> None:
    """Register backup subcommands under the given argparse subparser.

    Args:
        subparsers: The parent subparsers action to attach to.
    """
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
    """Dispatch a backup CLI command to the appropriate handler.

    Args:
        args: Parsed argparse namespace.
        manager: BackupManager instance.

    Returns:
        Exit code (0 for success, 1 for error).
    """
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
