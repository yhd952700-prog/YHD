#!/usr/bin/env python
"""
Backup Script for LiuHao AI OS

Performs automated backups of critical data with encryption and verification.

SCOPE BOUNDARY (U33): this is the operational *portable archive* tool -- encrypted
tar bundles of DB dumps / config, meant to be uploaded and restored as a unit. It
is NOT the in-application DR record-integrity library
(``src/sre/disaster/backup.py``), which verifies individual records. Different
layers, intentionally coexist. Currently has a CLI but no production caller wired.
"""

import os
import sys
import json
import base64
import shutil
import tarfile
import argparse
import logging
import tempfile
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Optional

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from config.production.config import get_config, ProductionConfig

logger = logging.getLogger(__name__)


def _reject_traversal(names: List[str], target_dir: Path) -> None:
    """Refuse member names that reach outside ``target_dir`` (pre-3.12 builds).

    ``extractall(filter="data")`` does this in the stdlib from 3.12 on; on an
    older interpreter the check has to be ours, because "this archive wants to
    write /etc/passwd" should never become a filesystem decision made by tar.
    """
    root = Path(target_dir).resolve()
    for name in names:
        candidate = Path(name.replace("\\", "/"))
        resolved = (
            candidate.resolve()
            if candidate.is_absolute()
            else (root / candidate).resolve()
        )
        if resolved != root and root not in resolved.parents:
            raise ValueError(f"refusing member path outside target: {name}")


class BackupManager:
    """Manages backup operations for LiuHao AI OS."""
    
    def __init__(self, config: ProductionConfig):
        self.config = config
        self.backup_config = config.backup
        self.backup_dir = Path(self.backup_config.storage_path)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        
        # Setup logging
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
    
    def create_backup(self, name: Optional[str] = None) -> Path:
        """
        Create a full backup of critical data.
        
        Args:
            name: Optional backup name (default: timestamp)
            
        Returns:
            Path to backup file
        """
        if name is None:
            name = f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        backup_path = self.backup_dir / f"{name}.tar.gz"
        # NOT /tmp: that path assumes POSIX and silently lands somewhere else
        # on Windows, where this project actually runs.
        temp_dir = Path(tempfile.mkdtemp(prefix="liuhao_backup_"))
        
        try:
            # Define paths to backup
            backup_paths = [
                ("data/security", "security"),
                ("data/observability", "observability"),
                ("data/plugins", "plugins"),
                ("data/storage", "storage"),
                ("config", "config"),
            ]
            
            # Copy data to temp directory
            for src_path, dest_name in backup_paths:
                src = Path(src_path)
                if src.exists():
                    dest = temp_dir / dest_name
                    if src.is_dir():
                        shutil.copytree(src, dest)
                    else:
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src, dest)
                    logger.info(f"Copied {src_path} -> {dest_name}")
            
            # Create metadata
            metadata = {
                "backup_name": name,
                "created_at": datetime.now().isoformat(),
                "version": "1.0.0",
                "config": {
                    "environment": "production",
                },
                "paths": [p[1] for p in backup_paths if Path(p[0]).exists()],
            }
            
            with open(temp_dir / "metadata.json", "w") as f:
                json.dump(metadata, f, indent=2)
            
            # Create compressed archive
            logger.info(f"Creating backup archive: {backup_path}")
            with tarfile.open(backup_path, "w:gz") as tar:
                tar.add(temp_dir, arcname=name)
            
            # Verify the ARCHIVE, then protect it. Verifying afterwards is a
            # trap: once the bytes are encrypted, tarfile cannot open them, so
            # every encrypted backup would report "verification failed" and
            # create_backup would raise -- i.e. encryption and verification
            # could never both be on.
            if self.backup_config.verify_after_backup:
                if not self._verify_backup(backup_path):
                    raise Exception("Backup verification failed")

            # Encrypt if enabled
            if self.backup_config.encryption:
                self._encrypt_backup(backup_path)

            logger.info(f"Backup completed: {backup_path}")
            return backup_path
            
        finally:
            # Cleanup temp directory
            shutil.rmtree(temp_dir, ignore_errors=True)
    
    def _encrypt_backup(self, backup_path: Path) -> None:
        """Encrypt backup file."""
        if not self.backup_config.encryption_key:
            logger.warning("No encryption key provided, skipping encryption")
            return
        
        from cryptography.fernet import Fernet
        key = self.backup_config.encryption_key.encode()
        if len(key) != 32:
            # Pad or hash to 32 bytes
            import hashlib
            key = hashlib.sha256(key).digest()
        
        fernet = Fernet(base64.urlsafe_b64encode(key))
        
        with open(backup_path, 'rb') as f:
            data = f.read()
        
        encrypted = fernet.encrypt(data)
        
        with open(backup_path, 'wb') as f:
            f.write(encrypted)
        
        logger.info(f"Backup encrypted: {backup_path}")
    
    def _verify_backup(self, backup_path: Path) -> bool:
        """Verify backup integrity."""
        try:
            with tarfile.open(backup_path, "r:gz") as tar:
                members = tar.getmembers()
                logger.info(f"Backup contains {len(members)} files")

                # A tarfile that opens is only *readable*. Reading every member
                # proves the payload survived compression; it is still not a
                # proof of authenticity -- that needs a keyed tag or a stored
                # digest, neither of which exists here yet (see the warning
                # below), so this verification deliberately claims only what it
                # actually checks.
                for member in members:
                    if member.isfile():
                        extracted = tar.extractfile(member)
                        if extracted is not None:
                            while extracted.read(1024 * 1024):
                                pass

                has_metadata = any(
                    m.name.endswith("metadata.json") for m in members
                )
                if not has_metadata:
                    logger.warning("Backup missing metadata.json")
                    return False

            if not self.backup_config.encryption:
                logger.warning(
                    "Backup integrity is UNVERIFIED beyond readability: with "
                    "encryption disabled nothing detects a bit-flipped or "
                    "hand-edited archive. Enable encryption, or store a digest "
                    "out-of-band, before treating these backups as restorable."
                )
            logger.info("Backup verification passed")
            return True
        except Exception as e:
            logger.error(f"Backup verification failed: {e}")
            return False
    
    def list_backups(self) -> List[dict]:
        """List all available backups."""
        backups = []
        for backup_file in self.backup_dir.glob("*.tar.gz*"):
            stat = backup_file.stat()
            backups.append({
                "name": backup_file.name,
                "path": str(backup_file),
                "size_mb": stat.st_size / (1024 * 1024),
                "created_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            })
        return sorted(backups, key=lambda x: x["created_at"], reverse=True)
    
    def restore_backup(self, backup_path: Path, target_dir: Optional[Path] = None) -> bool:
        """
        Restore from backup.
        
        Args:
            backup_path: Path to backup file
            target_dir: Target directory (default: current directory)
            
        Returns:
            True if successful
        """
        if target_dir is None:
            target_dir = Path(".")

        logger.info(f"Restoring from backup: {backup_path}")

        # Decrypt if needed
        temp_backup = backup_path
        if self.backup_config.encryption:
            temp_backup = self._decrypt_backup(backup_path)

        try:
            # Refuse to unpack an archive we cannot at least read end to end.
            # "It extracted" has always been the honest bar here; with
            # encryption off, nothing proves the bits are the bits we wrote.
            if self.backup_config.verify_after_backup:
                if not self._verify_backup(temp_backup):
                    logger.error(
                        "Refusing to restore %s: it does not pass verification, "
                        "so what it contains is unknown.", backup_path,
                    )
                    return False

            target_dir.mkdir(parents=True, exist_ok=True)
            with tarfile.open(temp_backup, "r:gz") as tar:
                # filter="data" is the whole point: without it a member named
                # ../../../../tmp/x (or an absolute path) writes OUTSIDE
                # target_dir, turning "restore this backup" into "let whoever
                # built the archive choose where we write".
                try:
                    tar.extractall(target_dir, filter="data")
                except TypeError:  # Python < 3.12 has no extraction filters
                    names = tar.getnames()
                    _reject_traversal(names, target_dir)
                    tar.extractall(target_dir)

            logger.info(f"Backup restored to: {target_dir}")
            return True
        except Exception as e:
            logger.error(f"Restore failed: {e}")
            return False
        finally:
            if temp_backup != backup_path:
                os.unlink(temp_backup)
    
    def _decrypt_backup(self, backup_path: Path) -> Path:
        """Decrypt backup file."""
        from cryptography.fernet import Fernet
        import base64
        
        key = self.backup_config.encryption_key.encode()
        if len(key) != 32:
            import hashlib
            key = hashlib.sha256(key).digest()
        
        fernet = Fernet(base64.urlsafe_b64encode(key))
        
        with open(backup_path, 'rb') as f:
            encrypted = f.read()
        
        decrypted = fernet.decrypt(encrypted)
        
        temp_path = Path(f"{backup_path}.decrypted")
        with open(temp_path, 'wb') as f:
            f.write(decrypted)
        
        return temp_path
    
    def cleanup_old_backups(self) -> int:
        """Remove backups older than retention period."""
        cutoff = datetime.now() - timedelta(days=self.backup_config.retention_days)
        removed = 0
        
        for backup_file in self.backup_dir.glob("*.tar.gz*"):
            mtime = datetime.fromtimestamp(backup_file.stat().st_mtime)
            if mtime < cutoff:
                backup_file.unlink()
                removed += 1
                logger.info(f"Removed old backup: {backup_file.name}")
        
        return removed


def main():
    parser = argparse.ArgumentParser(description="Backup management for LiuHao AI OS")
    parser.add_argument("action", choices=["create", "list", "restore", "cleanup"])
    parser.add_argument("--name", help="Backup name (for create)")
    parser.add_argument("--backup", help="Backup file path (for restore)")
    parser.add_argument("--target", help="Target directory (for restore)")
    parser.add_argument("--config", default="production", help="Config environment")
    
    args = parser.parse_args()
    
    config = get_config(args.config)
    manager = BackupManager(config)
    
    if args.action == "create":
        path = manager.create_backup(args.name)
        print(f"Backup created: {path}")
    
    elif args.action == "list":
        backups = manager.list_backups()
        for b in backups:
            print(f"{b['name']} - {b['size_mb']:.2f} MB - {b['created_at']}")
    
    elif args.action == "restore":
        if not args.backup:
            print("Error: --backup required for restore")
            sys.exit(1)
        target = Path(args.target) if args.target else None
        success = manager.restore_backup(Path(args.backup), target)
        sys.exit(0 if success else 1)
    
    elif args.action == "cleanup":
        removed = manager.cleanup_old_backups()
        print(f"Removed {removed} old backups")


if __name__ == "__main__":
    import base64
    main()