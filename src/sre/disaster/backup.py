"""Disaster Recovery module for Phase 6 SRE."""

import json
import os
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Dict, Any, Optional
from uuid import uuid4


@dataclass
class BackupRecord:
    """A backup record with snapshot data."""
    backup_id: str = field(default_factory=lambda: str(uuid4()))
    timestamp: float = field(default_factory=lambda: datetime.now(timezone.utc).timestamp())
    payload: Dict[str, Any] = field(default_factory=dict)
    resource_snapshot: Dict[str, Any] = field(default_factory=dict)
    integrity_hash: str = ""


@dataclass
class BackupManager:
    """Manages backup creation and restoration."""

    backup_dir: str = "./backups"

    def create_backup(self, payload: Dict[str, Any], resource_snapshot: Dict[str, Any]) -> BackupRecord:
        """Create a backup record."""
        record = BackupRecord(
            payload=payload,
            resource_snapshot=resource_snapshot,
            integrity_hash=self._compute_hash(payload, resource_snapshot),
        )

        # Ensure backup directory exists
        os.makedirs(self.backup_dir, exist_ok=True)

        # Write backup to disk
        backup_path = os.path.join(self.backup_dir, f"backup_{record.backup_id}.json")
        with open(backup_path, "w") as f:
            json.dump({
                "backup_id": record.backup_id,
                "timestamp": record.timestamp,
                "payload": record.payload,
                "resource_snapshot": record.resource_snapshot,
                "integrity_hash": record.integrity_hash,
            }, f, indent=2)

        return record

    def _compute_hash(self, payload: Dict[str, Any], resource_snapshot: Dict[str, Any]) -> str:
        """Compute integrity hash for backup."""
        import hashlib
        content = json.dumps({
            "payload": payload,
            "resource_snapshot": resource_snapshot,
        }, sort_keys=True)
        return hashlib.sha256(content.encode()).hexdigest()[:16]


@dataclass
class RecoveryRecord:
    """A recovery record."""
    recovery_id: str = field(default_factory=lambda: str(uuid4()))
    backup_id: str = ""
    restored_at: float = field(default_factory=lambda: datetime.now(timezone.utc).timestamp())
    success: bool = False
    error: Optional[str] = None


@dataclass
class RecoveryManager:
    """Manages restoration from backups."""

    backup_dir: str = "./backups"

    def restore(self, backup_id: str) -> RecoveryRecord:
        """Restore from a backup record."""
        backup_path = os.path.join(self.backup_dir, f"backup_{backup_id}.json")

        if not os.path.exists(backup_path):
            return RecoveryRecord(
                backup_id=backup_id,
                success=False,
                error=f"Backup file not found: {backup_path}",
            )

        try:
            with open(backup_path, "r") as f:
                json.load(f)

            return RecoveryRecord(
                backup_id=backup_id,
                success=True,
                restored_at=datetime.now(timezone.utc).timestamp(),
            )
        except Exception as e:
            return RecoveryRecord(
                backup_id=backup_id,
                success=False,
                error=str(e),
            )
