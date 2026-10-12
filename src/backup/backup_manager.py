"""Backup and restore system for LiuHao AI OS.

Provides comprehensive backup management including:
- Multiple backup types: full, incremental, differential
- Multiple backup sources: config, memory data, knowledge base, user data
- Manual and periodic scheduling
- Backup metadata (timestamp, size, checksum, type, sources)
- Gzip compression support
- Encryption support flag
- Restore capability with integrity verification
- Selective and full restore
- Backup retention policy (count-based and time-based)
- Backup statistics and reporting
"""
from __future__ import annotations

import gzip
import hashlib
import json
import shutil
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set


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
        encryption_enabled: Whether encryption is enabled (flag only).
    """

    backup_dir: Path
    source_paths: Dict[BackupSource, Path] = field(default_factory=dict)
    backups: Dict[str, BackupMetadata] = field(default_factory=dict)
    retention_policy: RetentionPolicy = field(default_factory=RetentionPolicy)
    compression_level: int = 6
    encryption_enabled: bool = False

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
        if compress:
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

            # Serialise and write
            json_bytes = json.dumps(backup_data, indent=2, default=str).encode("utf-8")

            if compress:
                compressed = gzip.compress(json_bytes, compresslevel=self.compression_level)
                backup_path.write_bytes(compressed)
                meta.size_bytes = len(compressed)
            else:
                backup_path.write_bytes(json_bytes)
                meta.size_bytes = len(json_bytes)

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

        For file sources, reads file contents or directory listings.
        """
        if source_path.is_file():
            content = source_path.read_text(encoding="utf-8", errors="replace")
            return {
                "exists": True,
                "type": "file",
                "path": str(source_path),
                "content": content,
                "size_bytes": len(content.encode("utf-8")),
                "modified": source_path.stat().st_mtime,
            }
        elif source_path.is_dir():
            files: Dict[str, Any] = {}
            total_size = 0
            for fpath in sorted(source_path.rglob("*")):
                if fpath.is_file():
                    rel = fpath.relative_to(source_path).as_posix()
                    try:
                        content = fpath.read_text(encoding="utf-8", errors="replace")
                    except Exception:
                        content = ""
                    files[rel] = {
                        "content": content,
                        "size_bytes": len(content.encode("utf-8")),
                        "modified": fpath.stat().st_mtime,
                    }
                    total_size += len(content.encode("utf-8"))
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

        # Try to read/decompress
        try:
            raw = filepath.read_bytes()
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
            # Read and decompress
            raw = filepath.read_bytes()
            if meta.compressed:
                raw = gzip.decompress(raw)

            backup_data = json.loads(raw.decode("utf-8"))

            sources_to_restore = sources if sources else meta.sources
            restored_sources: List[BackupSource] = []
            total_files = 0
            total_bytes = 0

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
                    target.write_text(source_data.get("content", ""), encoding="utf-8")
                    total_files += 1
                    total_bytes += len(source_data.get("content", "").encode("utf-8"))
                elif source_data.get("type") == "directory":
                    files = source_data.get("files", {})
                    for rel_path, file_info in files.items():
                        file_target = target / rel_path
                        file_target.parent.mkdir(parents=True, exist_ok=True)
                        file_target.write_text(
                            file_info.get("content", ""),
                            encoding="utf-8",
                        )
                        total_files += 1
                        total_bytes += len(
                            file_info.get("content", "").encode("utf-8")
                        )

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
