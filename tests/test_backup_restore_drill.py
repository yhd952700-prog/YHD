"""Real backup -> destroy -> restore drills.

These are not "the method returned a dict" tests. Each one proves the bytes
that come back are the bytes that went in, and the audit drill proves the
restored evidence store still verifies its own hash chain.

Regression targets:
- ``_collect_source_data`` used to read every file as UTF-8 text, so any
  non-text payload (SQLite, WAL, binary attachment) was silently corrupted
  while the backup still reported success.
- SQLite is captured with ``VACUUM INTO``; a byte copy of the main file
  without its uncheckpointed ``-wal`` loses committed events.
- A stale ``-wal`` left next to a restored main file would be replayed and
  corrupt it, so restore must remove the sidecars.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.backup.backup_manager import (
    BackupManager,
    BackupSource,
    BackupStatus,
    RestoreStatus,
)
from src.kernels.audit import AuditEventType, AuditScope, AuditStore


def _log_events(store: AuditStore, count: int) -> None:
    for i in range(count):
        store.log_event(
            event_type=AuditEventType.STATE_CHANGE,
            principal_id="test-liuhao",
            scope=AuditScope.L1,
            outcome="allow",
            details={"sequence": i},
        )


def _close_store(store: AuditStore) -> None:
    """Release the SQLite handle so the file can be deleted on Windows."""
    store._conn.close()


def _delete_with_sidecars(path: Path) -> None:
    for candidate in (
        path,
        path.with_name(path.name + "-wal"),
        path.with_name(path.name + "-shm"),
        path.with_name(path.name + "-journal"),
    ):
        if candidate.exists():
            candidate.unlink()


def test_binary_payload_round_trips_byte_for_byte(tmp_path: Path) -> None:
    """Non-UTF-8 bytes must survive; the old text path mangled them."""
    source = tmp_path / "attachment.bin"
    blob = bytes(range(256)) * 4 + b"\x00\xff\xfe\x80\x81"
    source.write_bytes(blob)

    manager = BackupManager(
        backup_dir=tmp_path / "backups",
        source_paths={BackupSource.USER_DATA: source},
    )
    meta = manager.create_backup(compress=True)
    assert meta.status == BackupStatus.COMPLETED

    source.write_bytes(b"clobbered")
    result = manager.restore_backup(meta.backup_id)

    assert result.success is True, result.error_message
    assert result.status == RestoreStatus.COMPLETED
    assert source.read_bytes() == blob


def test_sqlite_audit_db_delete_and_restore_keeps_hash_chain(tmp_path: Path) -> None:
    """The disaster drill: backup, destroy the DB, restore, verify the chain."""
    db_path = tmp_path / "audit_store.db"
    store = AuditStore(db_path=str(db_path))
    _log_events(store, 6)
    ok, count = store.verify_integrity()
    assert (ok, count) == (True, 6)

    manager = BackupManager(
        backup_dir=tmp_path / "backups",
        source_paths={BackupSource.MEMORY_DATA: db_path},
    )
    meta = manager.create_backup()
    assert meta.status == BackupStatus.COMPLETED
    assert meta.compressed is True

    # The event log lives in the WAL; closing the handle is what a crashed
    # process looks like. The backup must already contain those 6 events.
    _close_store(store)
    _delete_with_sidecars(db_path)
    assert not db_path.exists()

    result = manager.restore_backup(meta.backup_id)
    assert result.success is True, result.error_message
    assert db_path.exists()

    restored = AuditStore(db_path=str(db_path))
    try:
        restored_ok, restored_count = restored.verify_integrity()
    finally:
        _close_store(restored)

    assert (restored_ok, restored_count) == (True, 6)


def test_restored_sqlite_removes_stale_wal_sidecar(tmp_path: Path) -> None:
    """A leftover WAL from the pre-restore DB must not survive the restore."""
    db_path = tmp_path / "audit_store.db"
    store = AuditStore(db_path=str(db_path))
    _log_events(store, 3)
    _close_store(store)

    manager = BackupManager(
        backup_dir=tmp_path / "backups",
        source_paths={BackupSource.MEMORY_DATA: db_path},
    )
    meta = manager.create_backup()

    wal = db_path.with_name(db_path.name + "-wal")
    # The last connection close checkpoints and removes the real WAL, so plant
    # a stale one explicitly: this is the artifact that would be replayed against
    # restored bytes if restore left it behind.
    wal.write_bytes(b"stale-wal-bytes-that-do-not-belong-to-the-restored-db")
    assert wal.exists()

    result = manager.restore_backup(meta.backup_id)
    assert result.success is True, result.error_message
    assert not wal.exists(), "restore left a stale WAL next to the restored DB"

    restored = AuditStore(db_path=str(db_path))
    try:
        restored_ok, restored_count = restored.verify_integrity()
    finally:
        _close_store(restored)
    assert (restored_ok, restored_count) == (True, 3)


def test_directory_source_excludes_sqlite_sidecars(tmp_path: Path) -> None:
    """Directory backups snapshot the DB once and never archive its WAL."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_path = data_dir / "audit_store.db"
    store = AuditStore(db_path=str(db_path))
    _log_events(store, 2)

    blob = bytes(range(256)) + b"\x00\xff"
    (data_dir / "blob.bin").write_bytes(blob)

    manager = BackupManager(
        backup_dir=tmp_path / "backups",
        source_paths={BackupSource.USER_DATA: data_dir},
    )
    meta = manager.create_backup(compress=False)

    archive = Path(meta.file_path).read_bytes()
    import json

    payload = json.loads(archive.decode("utf-8"))
    files = payload["data"]["user_data"]["files"]
    assert "audit_store.db" in files
    assert files["audit_store.db"]["sqlite"] is True
    assert "audit_store.db-wal" not in files
    assert "blob.bin" in files

    _close_store(store)
    target = tmp_path / "restored"
    result = manager.restore_backup(meta.backup_id, target_dir=target)
    assert result.success is True, result.error_message
    assert (target / "user_data" / "blob.bin").read_bytes() == blob

    restored = AuditStore(db_path=str(target / "user_data" / "audit_store.db"))
    try:
        restored_ok, restored_count = restored.verify_integrity()
    finally:
        _close_store(restored)
    assert (restored_ok, restored_count) == (True, 2)


def test_encryption_flag_is_not_a_silent_noop(tmp_path: Path) -> None:
    """``encryption_enabled=True`` must fail, not label plaintext as encrypted."""
    source = tmp_path / "file.txt"
    source.write_text("plain")
    manager = BackupManager(
        backup_dir=tmp_path / "backups",
        source_paths={BackupSource.CONFIG: source},
        encryption_enabled=True,
    )
    with pytest.raises(NotImplementedError, match="encryption_enabled"):
        manager.create_backup()
