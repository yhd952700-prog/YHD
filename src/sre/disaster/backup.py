"""Disaster Recovery module for Phase 6 SRE.

Why restore() verifies instead of trusting the file
---------------------------------------------------
``create_backup`` writes an ``integrity_hash`` next to the payload, and the old
``restore`` read the file, threw the parsed content away and reported
``success=True`` -- so **any** file with that name "restored" successfully:
a bit-rotted one, a hand-truncated one, and one edited by whoever already had
write access to the backup directory. A recovery path that cannot fail is not a
recovery path; it is a backup you discover is empty during the incident.

Restore therefore recomputes the hash from what it actually read and refuses on
a mismatch, and it hands the recovered data back instead of discarding it.
"""

import json
import os
from datetime import datetime, timezone
from dataclasses import dataclass, field
from hmac import compare_digest
from typing import Any, Dict, Optional
from uuid import uuid4


@dataclass
class BackupRecord:
    """A backup record with snapshot data."""
    backup_id: str = field(default_factory=lambda: str(uuid4()))
    timestamp: float = field(default_factory=lambda: datetime.now(timezone.utc).timestamp())
    payload: Dict[str, Any] = field(default_factory=dict)
    resource_snapshot: Dict[str, Any] = field(default_factory=dict)
    integrity_hash: str = ""


def compute_integrity_hash(
    payload: Any, resource_snapshot: Any
) -> str:
    """Authenticate the two halves of a backup with SHA-256.

    One function for both directions on purpose: a writer and a verifier that
    compute this separately eventually disagree, and then every restore fails
    (or, worse, every restore is skipped).
    """
    import hashlib
    content = json.dumps({
        "payload": payload,
        "resource_snapshot": resource_snapshot,
    }, sort_keys=True)
    return hashlib.sha256(content.encode()).hexdigest()[:16]


@dataclass
class BackupManager:

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
        """Compute integrity hash for backup (see :func:`compute_integrity_hash`)."""
        return compute_integrity_hash(payload, resource_snapshot)


@dataclass
class RecoveryRecord:
    """A recovery record.

    ``success`` must mean *the data came back and it was the data we wrote*.
    When it is True, ``payload`` / ``resource_snapshot`` hold what was restored;
    when it is False, ``error`` says why and those stay empty -- "restored
    nothing, successfully" is the failure mode this shape exists to prevent.
    """
    recovery_id: str = field(default_factory=lambda: str(uuid4()))
    backup_id: str = ""
    restored_at: float = field(default_factory=lambda: datetime.now(timezone.utc).timestamp())
    success: bool = False
    error: Optional[str] = None
    payload: Optional[Dict[str, Any]] = None
    resource_snapshot: Optional[Dict[str, Any]] = None


@dataclass
class RecoveryManager:
    """Manages restoration from backups."""

    backup_dir: str = "./backups"

    def restore(self, backup_id: str) -> RecoveryRecord:
        """Restore from a backup record, verifying it on the way in."""
        backup_path = os.path.join(self.backup_dir, f"backup_{backup_id}.json")

        if not os.path.exists(backup_path):
            return RecoveryRecord(
                backup_id=backup_id,
                success=False,
                error=f"Backup file not found: {backup_path}",
            )

        try:
            with open(backup_path, "r") as f:
                data = json.load(f)
        except Exception as e:
            return RecoveryRecord(
                backup_id=backup_id,
                success=False,
                error=f"Backup file unreadable ({type(e).__name__}): {e}",
            )

        if not isinstance(data, dict):
            return RecoveryRecord(
                backup_id=backup_id,
                success=False,
                error=(
                    "Backup file is not a JSON object "
                    f"(got {type(data).__name__}); refusing to guess what it was"
                ),
            )

        payload = data.get("payload")
        snapshot = data.get("resource_snapshot")
        declared = str(data.get("integrity_hash") or "")
        expected = compute_integrity_hash(payload, snapshot)

        if not declared:
            return RecoveryRecord(
                backup_id=backup_id,
                success=False,
                error=(
                    "Backup carries no integrity hash, so it cannot be shown to "
                    "be the data that was written; refusing to restore it"
                ),
            )
        if not compare_digest(declared, expected):
            return RecoveryRecord(
                backup_id=backup_id,
                success=False,
                error=(
                    "Backup failed integrity verification (hash mismatch) -- it "
                    "was modified after it was written. Refusing to restore it; "
                    "find an earlier backup and treat this as an incident."
                ),
            )

        return RecoveryRecord(
            backup_id=backup_id,
            success=True,
            restored_at=datetime.now(timezone.utc).timestamp(),
            payload=payload if isinstance(payload, dict) else {},
            resource_snapshot=snapshot if isinstance(snapshot, dict) else {},
        )
