"""Pre-upgrade data snapshots must be retrievable *after* the upgrade."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

from src.upgrade import (
    LocalDirectorySource,
    RollbackError,
    SnapshotError,
    UpgradeConfig,
    UpgradeManager,
)
from src.upgrade.dataversion import DATA_SCHEMA_VERSION
from src.upgrade.filemanifest import build_file_manifest_for_zip


def _make_app(root: Path, *, version: str = "0.1.0") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "liuhao-ai-os"\nversion = "{version}"\n',
        encoding="utf-8",
    )
    (root / "src").mkdir(exist_ok=True)
    (root / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "src" / "gateway").mkdir(exist_ok=True)
    (root / "src" / "gateway" / "main.py").write_text(
        "from fastapi import FastAPI\n\n"
        "app = FastAPI()\n\n"
        "@app.get('/health')\n"
        "def health():\n"
        "    return {'ok': True}\n",
        encoding="utf-8",
    )
    (root / "data").mkdir(exist_ok=True)
    conn = sqlite3.connect(root / "data" / "app.db")
    conn.execute("CREATE TABLE t(v TEXT)")
    conn.execute("INSERT INTO t VALUES ('keep')")
    conn.commit()
    conn.close()
    (root / "data" / "state.json").write_text('{"ok": true}', encoding="utf-8")
    return root


def _release(release_dir: Path, *, version: str = "0.2.0") -> dict:
    release_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "pyproject.toml": f'[project]\nname = "liuhao-ai-os"\nversion = "{version}"\n',
        "src/app.py": "VALUE = 2\n",
    }
    artifact = f"liuhao-{version}.zip"
    zip_path = release_dir / artifact
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    release = {
        "version": version,
        "artifact": artifact,
        "sha256": hashlib.sha256(zip_path.read_bytes()).hexdigest(),
        "size": zip_path.stat().st_size,
        "files": build_file_manifest_for_zip(zip_path),
        "data_schema_version": DATA_SCHEMA_VERSION,
    }
    (release_dir / "LIUHAO_RELEASE.json").write_text(
        json.dumps({"schema": "liuhao.release-manifest/1", "releases": [release]}),
        encoding="utf-8",
    )
    return release


def _rows(db: Path) -> list[str]:
    conn = sqlite3.connect(db)
    try:
        return [row[0] for row in conn.execute("SELECT v FROM t").fetchall()]
    finally:
        conn.close()


def _manager(app_root: Path, release_dir: Path) -> UpgradeManager:
    # 显式指定数据目录：conftest 的 LIUHAO_DATA_DIR 指向 pytest tmp，
    # 跟随它会让备份/状态落在断言路径之外。
    return UpgradeManager(
        UpgradeConfig(
            app_root=app_root,
            data_dir=app_root / "data",
            source=LocalDirectorySource(release_dir),
        )
    )


def test_exported_snapshot_contains_pre_upgrade_data(tmp_path: Path) -> None:
    app_root = _make_app(tmp_path / "app")
    release_dir = tmp_path / "releases"
    _release(release_dir)
    manager = _manager(app_root, release_dir)
    try:
        check = manager.check()
        assert check.release is not None
        download = manager.download(check.release)
        result = manager.apply(download.path, check.release)
        assert result.self_check_passed is True

        # Data written *after* the upgrade is the whole point of the snapshot:
        # the user noticed something wrong and wants the pre-upgrade bytes back.
        conn = sqlite3.connect(app_root / "data" / "app.db")
        conn.execute("INSERT INTO t VALUES ('added-after-upgrade')")
        conn.commit()
        conn.close()

        exported = manager.export_snapshot(tmp_path / "exported")
        exported_db = Path(exported["destination"]) / "data" / "app.db"
        assert _rows(exported_db) == ["keep"]
        assert exported["backup_id"] == result.backup_id

        snapshot = json.loads(Path(exported["manifest_path"]).read_text(encoding="utf-8"))
        assert snapshot["schema"] == "liuhao.snapshot-export/1"
        assert snapshot["backup_id"] == result.backup_id
        assert snapshot["from_version"] == "0.1.0"
        assert snapshot["to_version"] == "0.2.0"
        assert snapshot["file_count"] == exported["file_count"] == 2
        assert snapshot["sqlite_count"] == 1
        entry = next(item for item in snapshot["entries"] if item["relpath"] == "app.db")
        actual_sha = hashlib.sha256(exported_db.read_bytes()).hexdigest()
        assert entry["sha256"] == actual_sha

        # The live database still has the post-upgrade row: exporting is read-only.
        assert _rows(app_root / "data" / "app.db") == ["keep", "added-after-upgrade"]
    finally:
        manager.close()


def test_list_backups_shows_snapshots_newest_first(tmp_path: Path) -> None:
    app_root = _make_app(tmp_path / "app")
    release_dir = tmp_path / "releases"
    _release(release_dir)
    manager = _manager(app_root, release_dir)
    try:
        check = manager.check()
        assert check.release is not None
        download = manager.download(check.release)
        result = manager.apply(download.path, check.release)

        backups = manager.list_backups(limit=10)
        assert [item["backup_id"] for item in backups] == [result.backup_id]
        assert backups[0]["has_data_snapshot"] is True
        assert backups[0]["key_tables"]
        assert backups[0]["data_sqlite_count"] == 1
    finally:
        manager.close()


def test_export_refuses_to_overwrite_an_existing_directory(tmp_path: Path) -> None:
    app_root = _make_app(tmp_path / "app")
    release_dir = tmp_path / "releases"
    _release(release_dir)
    manager = _manager(app_root, release_dir)
    try:
        check = manager.check()
        assert check.release is not None
        download = manager.download(check.release)
        manager.apply(download.path, check.release)

        destination = tmp_path / "exported"
        manager.export_snapshot(destination)
        with pytest.raises(SnapshotError) as exc:
            manager.export_snapshot(destination)
        assert "已存在" in str(exc.value)
        # The first export is untouched.
        assert (destination / "snapshot.json").is_file()
    finally:
        manager.close()


def test_export_without_any_backup_reports_clearly(tmp_path: Path) -> None:
    app_root = _make_app(tmp_path / "app")
    manager = _manager(app_root, tmp_path / "no-releases")
    try:
        assert manager.list_backups() == []
        with pytest.raises(RollbackError) as exc:
            manager.export_snapshot(tmp_path / "exported")
        assert "没有可回滚" in str(exc.value)
        assert not (tmp_path / "exported").exists()
    finally:
        manager.close()


def test_export_can_target_a_specific_backup(tmp_path: Path) -> None:
    app_root = _make_app(tmp_path / "app")
    release_dir = tmp_path / "releases"
    _release(release_dir, version="0.2.0")
    manager = _manager(app_root, release_dir)
    try:
        check = manager.check()
        assert check.release is not None
        download = manager.download(check.release)
        result = manager.apply(download.path, check.release)

        exported = manager.export_snapshot(
            tmp_path / "exported", backup_id=result.backup_id
        )
        assert exported["backup_id"] == result.backup_id
        assert (Path(exported["destination"]) / "data" / "app.db").is_file()
    finally:
        manager.close()
