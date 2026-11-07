"""Real apply / automatic rollback / explicit rollback drills.

Each test asserts bytes on disk and rows in SQLite, not just return values.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

import src.upgrade.manager as manager_module
from src.upgrade import (
    ApplyError,
    LocalDirectorySource,
    PreflightError,
    UpgradeConfig,
    UpgradeManager,
    read_current_version,
)


def _setup_app(root: Path, version: str = "0.1.0") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "liuhao-ai-os"\nversion = "{version}"\n',
        encoding="utf-8",
    )
    (root / "src").mkdir(exist_ok=True)
    (root / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    # Real ASGI service entry point: the post-install self-check actually
    # starts `src.gateway.main:app` on a temporary port, so a stub object
    # would not exercise the same path production uses.
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


def _release(
    release_dir: Path,
    *,
    version: str = "0.2.0",
    files: dict[str, str] | None = None,
) -> dict:
    release_dir.mkdir(parents=True, exist_ok=True)
    files = files or {
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
    }
    manifest = {"schema": "liuhao.release-manifest/1", "releases": [release]}
    (release_dir / "release-manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    return release


def _manager(app_root: Path, release_dir: Path) -> UpgradeManager:
    return UpgradeManager(
        UpgradeConfig(app_root=app_root, source=LocalDirectorySource(release_dir))
    )


def _rows(db: Path) -> list[str]:
    conn = sqlite3.connect(db)
    try:
        return [row[0] for row in conn.execute("SELECT v FROM t").fetchall()]
    finally:
        conn.close()


def test_apply_backs_up_code_and_data_then_rolls_back(tmp_path: Path) -> None:
    app_root = _setup_app(tmp_path / "app")
    release_dir = tmp_path / "releases"
    _release(release_dir)
    manager = _manager(app_root, release_dir)
    try:
        check = manager.check()
        assert check.release is not None
        download = manager.download(check.release)
        result = manager.apply(download.path, check.release)
        assert result.from_version == "0.1.0"
        assert result.to_version == "0.2.0"

        assert read_current_version(app_root)[0] == "0.2.0"
        assert (app_root / "src" / "app.py").read_text(encoding="utf-8") == "VALUE = 2\n"
        assert _rows(app_root / "data" / "app.db") == ["keep"]
        assert json.loads((app_root / "data" / "state.json").read_text(encoding="utf-8")) == {
            "ok": True
        }

        backup_dir = app_root / "data" / "upgrade-backups" / result.backup_id
        assert (backup_dir / "code.zip").is_file()
        assert (backup_dir / "data" / "app.db").is_file()
        assert (backup_dir / "backup.json").is_file()
        backup_meta = json.loads((backup_dir / "backup.json").read_text(encoding="utf-8"))
        assert backup_meta["from_version"] == "0.1.0"
        assert backup_meta["to_version"] == "0.2.0"
        assert backup_meta["data_sqlite_count"] == 1

        history = manager.history(limit=50)
        assert any(
            item["kind"] == "apply" and item["status"] == "completed"
            for item in history
        )
        assert manager.status()["phase"] == "completed"

        # Data written after the upgrade must disappear after rollback because
        # rollback restores the pre-upgrade snapshot.
        conn = sqlite3.connect(app_root / "data" / "app.db")
        conn.execute("INSERT INTO t VALUES ('added-after-upgrade')")
        conn.commit()
        conn.close()

        rolled = manager.rollback()
        assert rolled.from_version == "0.2.0"
        assert rolled.to_version == "0.1.0"
        assert read_current_version(app_root)[0] == "0.1.0"
        assert (app_root / "src" / "app.py").read_text(encoding="utf-8") == "VALUE = 1\n"
        assert _rows(app_root / "data" / "app.db") == ["keep"]
        assert manager.status()["phase"] == "rolled_back"
        assert any(
            item["kind"] == "rollback" and item["status"] == "rolled_back"
            for item in manager.history()
        )
    finally:
        manager.close()


def test_failure_after_install_triggers_automatic_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_root = _setup_app(tmp_path / "app")
    release_dir = tmp_path / "releases"
    _release(release_dir)
    manager = _manager(app_root, release_dir)
    real_extract = manager_module.extract_zip_members

    def failing_extract(archive, members, target_root):
        installed = real_extract(archive, members, target_root)
        if Path(target_root).resolve() == app_root.resolve():
            raise RuntimeError("injected failure after installation")
        return installed

    monkeypatch.setattr(manager_module, "extract_zip_members", failing_extract)
    try:
        check = manager.check()
        assert check.release is not None
        download = manager.download(check.release)
        with pytest.raises(ApplyError) as exc:
            manager.apply(download.path, check.release)
        assert "已自动回滚" in str(exc.value)

        assert read_current_version(app_root)[0] == "0.1.0"
        assert (app_root / "src" / "app.py").read_text(encoding="utf-8") == "VALUE = 1\n"
        assert _rows(app_root / "data" / "app.db") == ["keep"]
        record = [
            item
            for item in manager.history()
            if item["kind"] == "apply" and item["status"] == "failed_rolled_back"
        ]
        assert record, manager.history()
        assert manager.status()["phase"] == "rolled_back"
    finally:
        manager.close()


def test_corrupt_data_aborts_before_any_mutation(tmp_path: Path) -> None:
    app_root = _setup_app(tmp_path / "app")
    release_dir = tmp_path / "releases"
    _release(release_dir)
    manager = _manager(app_root, release_dir)
    try:
        check = manager.check()
        assert check.release is not None
        download = manager.download(check.release)
        (app_root / "data" / "app.db").write_bytes(b"SQLite format 3\x00corrupted")
        with pytest.raises(PreflightError) as exc:
            manager.apply(download.path, check.release)
        assert "数据校验未通过" in str(exc.value)
        assert read_current_version(app_root)[0] == "0.1.0"
        assert not any(
            (path / "backup.json").is_file()
            for path in (app_root / "data" / "upgrade-backups").glob("*")
        )
        assert manager.status()["phase"] == "failed"
        assert any(
            item["kind"] == "preflight" and item["status"] == "failed"
            for item in manager.history()
        )
    finally:
        manager.close()


def test_release_cannot_overwrite_data(tmp_path: Path) -> None:
    app_root = _setup_app(tmp_path / "app")
    before = (app_root / "data" / "app.db").read_bytes()
    release_dir = tmp_path / "releases"
    _release(
        release_dir,
        files={
            "pyproject.toml": '[project]\nname = "liuhao-ai-os"\nversion = "0.2.0"\n',
            "data/app.db": "not a database",
        },
    )
    manager = _manager(app_root, release_dir)
    try:
        check = manager.check()
        assert check.release is not None
        download = manager.download(check.release)
        with pytest.raises(ApplyError) as exc:
            manager.apply(download.path, check.release)
        assert "data" in str(exc.value)
        assert read_current_version(app_root)[0] == "0.1.0"
        assert (app_root / "data" / "app.db").read_bytes() == before
        assert manager.status()["phase"] == "failed"
    finally:
        manager.close()


def test_package_without_version_is_rejected(tmp_path: Path) -> None:
    app_root = _setup_app(tmp_path / "app")
    release_dir = tmp_path / "releases"
    _release(
        release_dir,
        files={"src/app.py": "VALUE = 9\n"},
    )
    manager = _manager(app_root, release_dir)
    try:
        check = manager.check()
        assert check.release is not None
        download = manager.download(check.release)
        with pytest.raises(ApplyError) as exc:
            manager.apply(download.path, check.release)
        assert "无法确定版本" in str(exc.value)
        assert read_current_version(app_root)[0] == "0.1.0"
        assert not any(
            (path / "backup.json").is_file()
            for path in (app_root / "data" / "upgrade-backups").glob("*")
        )
    finally:
        manager.close()


def test_artifact_tampered_after_download_is_rejected(tmp_path: Path) -> None:
    app_root = _setup_app(tmp_path / "app")
    release_dir = tmp_path / "releases"
    _release(release_dir)
    manager = _manager(app_root, release_dir)
    try:
        check = manager.check()
        assert check.release is not None
        download = manager.download(check.release)
        artifact = Path(download.path)
        artifact.write_bytes(artifact.read_bytes() + b"tampered")
        with pytest.raises(ApplyError) as exc:
            manager.apply(artifact, check.release)
        assert "SHA256" in str(exc.value)
        assert read_current_version(app_root)[0] == "0.1.0"
    finally:
        manager.close()


def test_rollback_without_record_reports_failure_not_stuck(tmp_path: Path) -> None:
    """A failed rollback must end at ``failed``, never hang at ``rolling_back``."""
    from src.upgrade import RollbackError

    app_root = _setup_app(tmp_path / "app")
    manager = _manager(app_root, tmp_path / "no-releases")
    try:
        with pytest.raises(RollbackError) as exc:
            manager.rollback()
        assert "没有可回滚" in str(exc.value)
        status = manager.status()
        assert status["phase"] == "failed", status
        assert manager.history(kind="rollback")[0]["status"] == "failed"
        assert read_current_version(app_root)[0] == "0.1.0"
    finally:
        manager.close()

