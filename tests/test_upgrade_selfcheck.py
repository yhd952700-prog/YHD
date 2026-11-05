"""Post-install self-check: service, databases, key tables, and auto-rollback."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

from src.upgrade import ApplyError, LocalDirectorySource, UpgradeConfig, UpgradeManager
from src.upgrade.dataguard import capture_table_inventory
from src.upgrade.selfcheck import SelfCheckConfig, run_self_check


def _make_app(root: Path, *, version: str = "0.1.0", healthy_service: bool = True) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "liuhao-ai-os"\nversion = "{version}"\n',
        encoding="utf-8",
    )
    (root / "src").mkdir(exist_ok=True)
    (root / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "src" / "gateway").mkdir(exist_ok=True)
    (root / "src" / "gateway" / "main.py").write_text(
        "class _App:\n    routes = [object()]\n\napp = _App()\n"
        if healthy_service
        else "raise RuntimeError('release is broken')\n",
        encoding="utf-8",
    )
    (root / "data").mkdir(exist_ok=True)
    conn = sqlite3.connect(root / "data" / "app.db")
    conn.execute("CREATE TABLE t(v TEXT)")
    conn.execute("INSERT INTO t VALUES ('keep')")
    conn.commit()
    conn.close()
    return root


def _release(release_dir: Path, *, version: str = "0.2.0", broken_service: bool = False) -> dict:
    release_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "pyproject.toml": f'[project]\nname = "liuhao-ai-os"\nversion = "{version}"\n',
        "src/app.py": "VALUE = 2\n",
    }
    if broken_service:
        files["src/gateway/main.py"] = "raise RuntimeError('release is broken')\n"
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


def test_healthy_app_passes_service_database_and_table_checks(tmp_path: Path) -> None:
    app_root = _make_app(tmp_path / "app")
    data_dir = app_root / "data"
    expected = capture_table_inventory([data_dir])

    report = run_self_check(app_root, [data_dir], expected_tables=expected)

    assert report.ok, report.to_dict()
    assert report.service_checked and report.service_ok
    assert report.service_routes >= 1
    assert report.databases_checked == 1
    assert report.tables_checked == 1
    assert report.issues == []


def test_broken_service_module_fails_the_check(tmp_path: Path) -> None:
    app_root = _make_app(tmp_path / "app", healthy_service=False)

    report = run_self_check(app_root, [app_root / "data"])

    assert report.ok is False
    assert report.service_checked and not report.service_ok
    assert [issue.kind for issue in report.issues] == ["service"]
    assert "release is broken" in report.issues[0].detail


def test_missing_key_table_fails_the_check(tmp_path: Path) -> None:
    app_root = _make_app(tmp_path / "app")
    data_dir = app_root / "data"
    expected = capture_table_inventory([data_dir])
    conn = sqlite3.connect(data_dir / "app.db")
    conn.execute("DROP TABLE t")
    conn.commit()
    conn.close()

    report = run_self_check(
        app_root,
        [data_dir],
        expected_tables=expected,
        config=SelfCheckConfig(check_service=False),
    )

    assert report.ok is False
    assert any(issue.kind == "table" and "关键表缺失" in issue.detail for issue in report.issues)


def test_unreadable_database_fails_the_check(tmp_path: Path) -> None:
    app_root = _make_app(tmp_path / "app")
    db = app_root / "data" / "app.db"
    db.write_bytes(b"SQLite format 3\x00" + b"not really a database")

    report = run_self_check(
        app_root,
        [app_root / "data"],
        config=SelfCheckConfig(check_service=False),
    )

    assert report.ok is False
    assert any(issue.kind == "sqlite" and "数据库无法打开" in issue.detail for issue in report.issues)


def test_self_check_failure_rolls_back_automatically(tmp_path: Path) -> None:
    app_root = _make_app(tmp_path / "app")
    release_dir = tmp_path / "releases"
    _release(release_dir, broken_service=True)
    manager = UpgradeManager(
        UpgradeConfig(app_root=app_root, source=LocalDirectorySource(release_dir))
    )
    try:
        check = manager.check()
        assert check.release is not None
        download = manager.download(check.release)

        with pytest.raises(ApplyError) as exc:
            manager.apply(download.path, check.release)
        assert "自检" in str(exc.value)
        assert "已自动回滚" in str(exc.value)

        # Version, code bytes and the pre-upgrade database all come back.
        assert (app_root / "pyproject.toml").read_text(encoding="utf-8").find("0.1.0") >= 0
        assert (app_root / "src" / "app.py").read_text(encoding="utf-8") == "VALUE = 1\n"
        assert "routes" in (app_root / "src" / "gateway" / "main.py").read_text(encoding="utf-8")
        assert _rows(app_root / "data" / "app.db") == ["keep"]

        assert manager.status()["phase"] == "rolled_back"
        history = manager.history(limit=50)
        assert any(
            item["kind"] == "selfcheck" and item["status"] == "failed" for item in history
        )
        assert any(
            item["kind"] == "apply" and item["status"] == "failed_rolled_back"
            for item in history
        )
    finally:
        manager.close()


def test_application_records_a_passed_self_check(tmp_path: Path) -> None:
    app_root = _make_app(tmp_path / "app")
    release_dir = tmp_path / "releases"
    _release(release_dir)
    manager = UpgradeManager(
        UpgradeConfig(app_root=app_root, source=LocalDirectorySource(release_dir))
    )
    try:
        check = manager.check()
        assert check.release is not None
        download = manager.download(check.release)
        result = manager.apply(download.path, check.release)

        assert result.self_check_passed is True
        assert result.self_check_issues == 0
        assert manager.status()["phase"] == "completed"
        assert any(
            item["kind"] == "selfcheck" and item["status"] == "passed"
            for item in manager.history()
        )
        assert _rows(app_root / "data" / "app.db") == ["keep"]
    finally:
        manager.close()
