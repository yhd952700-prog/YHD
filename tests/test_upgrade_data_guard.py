"""Data preflight and backup correctness (SQLite/WAL/JSON)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from src.upgrade import backups
from src.upgrade.backups import backup_data, restore_data
from src.upgrade.dataguard import is_sqlite_file, verify_data_paths


def _make_app(tmp_path: Path) -> Path:
    root = tmp_path / "app"
    root.mkdir()
    (root / "data").mkdir()
    return root


def test_verify_detects_invalid_json(tmp_path: Path) -> None:
    root = _make_app(tmp_path)
    (root / "data" / "broken.json").write_text("{not json", encoding="utf-8")
    report = verify_data_paths([root / "data"])
    assert report.ok is False
    assert report.json_checked == 1
    assert any(issue.kind == "json" for issue in report.issues)


def test_verify_detects_corrupted_sqlite(tmp_path: Path) -> None:
    root = _make_app(tmp_path)
    (root / "data" / "bad.db").write_bytes(b"SQLite format 3\x00garbage")
    report = verify_data_paths([root / "data"])
    assert report.ok is False
    assert report.sqlite_checked == 1
    assert any(issue.kind == "sqlite" for issue in report.issues)


def test_wal_snapshot_contains_committed_data_and_has_no_sidecar(tmp_path: Path) -> None:
    root = _make_app(tmp_path)
    db = root / "data" / "wal.db"
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE t(v TEXT)")
    conn.execute("INSERT INTO t VALUES ('committed')")
    conn.commit()
    conn.close()

    backup = backup_data(root, root / "data", [], tmp_path / "backup")
    snapshot = tmp_path / "backup" / "data" / "wal.db"
    assert snapshot.is_file()
    assert not (tmp_path / "backup" / "data" / "wal.db-wal").exists()
    conn = sqlite3.connect(snapshot)
    try:
        rows = [row[0] for row in conn.execute("SELECT v FROM t").fetchall()]
    finally:
        conn.close()
    assert rows == ["committed"]
    assert backup.sqlite_count == 1
    assert backup.file_count == 1
    assert backup.manifest_path.is_file()


def test_restore_removes_stale_sidecars_and_restores_bytes(tmp_path: Path) -> None:
    root = _make_app(tmp_path)
    db = root / "data" / "restore.db"
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE t(v TEXT)")
    conn.execute("INSERT INTO t VALUES ('original')")
    conn.commit()
    conn.close()

    backup = backup_data(root, root / "data", [], tmp_path / "backup")
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM t")
    conn.execute("INSERT INTO t VALUES ('changed-later')")
    conn.commit()
    conn.close()
    (db.with_name("restore.db-wal")).write_bytes(b"stale-wal")
    restore_data(root, root / "data", tmp_path / "backup")
    assert not db.with_name("restore.db-wal").exists()
    assert not db.with_name("restore.db-shm").exists()
    conn = sqlite3.connect(db)
    try:
        rows = [row[0] for row in conn.execute("SELECT v FROM t").fetchall()]
    finally:
        conn.close()
    assert rows == ["original"]
    assert backup.file_count == 1


def test_restore_uses_sqlite_backup_when_a_running_service_holds_the_database(
    tmp_path: Path, monkeypatch,
) -> None:
    """Windows refuses ``os.replace`` while a WAL DB is open (WinError 5).

    The upgrade runs against a live service, so rollback must restore the
    content *through* SQLite instead of replacing the file. The simulation is
    deterministic and cross-platform: replacement is forced to fail exactly
    the way Windows does, while the live connection stays open.
    """
    root = _make_app(tmp_path)
    db = root / "data" / "live.db"
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE t(v TEXT)")
    conn.execute("INSERT INTO t VALUES ('original')")
    conn.commit()
    conn.close()

    backup_data(root, root / "data", [], tmp_path / "backup")

    live = sqlite3.connect(db)
    live.execute("PRAGMA journal_mode=WAL").fetchone()
    live.execute("DELETE FROM t")
    live.execute("INSERT INTO t VALUES ('changed-by-running-service')")
    live.commit()

    def _refuse_replace(source: Path, target: Path) -> None:
        raise PermissionError(5, "拒绝访问", str(target))

    monkeypatch.setattr(backups, "atomic_copy_path", _refuse_replace)
    restore_data(root, root / "data", tmp_path / "backup")

    rows = live.execute("SELECT v FROM t").fetchall()
    live.close()
    assert rows == [("original",)]


def test_extra_state_files_are_backed_up_and_restored(tmp_path: Path) -> None:
    root = _make_app(tmp_path)
    (root / "data" / "a.json").write_text('{"v": 1}', encoding="utf-8")
    extra = root / "audit_store.db"
    conn = sqlite3.connect(extra)
    conn.execute("CREATE TABLE events(v TEXT)")
    conn.execute("INSERT INTO events VALUES ('e1')")
    conn.commit()
    conn.close()

    backup = backup_data(root, root / "data", [extra], tmp_path / "backup")
    assert backup.sqlite_count == 1
    manifest = json.loads(backup.manifest_path.read_text(encoding="utf-8"))
    kinds = {entry["kind"] for entry in manifest["entries"]}
    assert kinds == {"data", "extra"}

    extra.write_bytes(b"broken")
    restore_data(root, root / "data", tmp_path / "backup")
    conn = sqlite3.connect(extra)
    try:
        rows = [row[0] for row in conn.execute("SELECT v FROM events").fetchall()]
    finally:
        conn.close()
    assert rows == ["e1"]


def test_sqlite_is_detected_by_header_not_extension(tmp_path: Path) -> None:
    root = _make_app(tmp_path)
    db = root / "data" / "state.bin"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE t(v TEXT)")
    conn.commit()
    conn.close()
    assert is_sqlite_file(db) is True
    report = verify_data_paths([root / "data"])
    assert report.ok is True
    assert report.sqlite_checked == 1
    assert report.files_checked == 1
