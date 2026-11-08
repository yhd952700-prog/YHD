"""CLI behaviour the distribution flow depends on.

Two real defects are covered here:

* ``--allow-same-or-downgrade`` used to be accepted by argparse but never
  reached ``check()``: the same version was reported ``up_to_date`` and
  ``upgrade`` exited 0 without installing anything;
* the default data directory was ``<app-root>/data`` even for an installed
  tree, whose real data lives in ``%LOCALAPPDATA%\\LiuHao\\data`` — the CLI
  then checked an empty database and "data survives the upgrade" was false.

The success-path tests turn the post-install service probe off (that path is
covered by ``test_upgrade_selfcheck.py``); apply/backup/preflight still run
for real against a throwaway app tree.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

from src.upgrade import cli
from src.upgrade.cli import _build_parser, _manager, main
from src.upgrade.dataversion import DATA_SCHEMA_VERSION
from src.upgrade.filemanifest import build_file_manifest_for_zip
from src.upgrade.manager import (
    UpgradeConfig,
    UpgradeManager,
    default_data_dir,
    resolve_data_dir,
)
from src.upgrade.models import CheckStatus
from src.upgrade.sources import LocalDirectorySource
from src.upgrade.versioning import read_current_version


def _setup_app(
    root: Path,
    version: str = "0.1.0",
    *,
    value: str = "VALUE = 1\n",
    data_dir: Path | None = None,
    installed: bool = False,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "liuhao-ai-os"\nversion = "{version}"\n',
        encoding="utf-8",
    )
    (root / "src").mkdir(exist_ok=True)
    (root / "src" / "app.py").write_text(value, encoding="utf-8")
    (root / "src" / "gateway").mkdir(exist_ok=True)
    (root / "src" / "gateway" / "main.py").write_text(
        "from fastapi import FastAPI\n\n"
        "app = FastAPI()\n\n"
        "@app.get('/health')\n"
        "def health():\n"
        "    return {'ok': True}\n",
        encoding="utf-8",
    )
    if installed:
        # Installer marker (installer/liuhao.iss installs the interpreter here).
        (root / "runtime").mkdir(exist_ok=True)
        (root / "runtime" / "python.exe").write_text("", encoding="utf-8")
    target = Path(data_dir) if data_dir else root / "data"
    target.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target / "app.db")
    conn.execute("CREATE TABLE t(v TEXT)")
    conn.execute("INSERT INTO t VALUES ('keep')")
    conn.commit()
    conn.close()
    (target / "state.json").write_text('{"ok": true}', encoding="utf-8")
    return root


def _release(release_dir: Path, version: str = "0.2.0", *, value: str = "VALUE = 2\n") -> dict:
    release_dir.mkdir(parents=True, exist_ok=True)
    artifact = f"liuhao-{version}.zip"
    zip_path = release_dir / artifact
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "pyproject.toml",
            f'[project]\nname = "liuhao-ai-os"\nversion = "{version}"\n',
        )
        archive.writestr("src/app.py", value)
    release = {
        "version": version,
        "artifact": artifact,
        "sha256": hashlib.sha256(zip_path.read_bytes()).hexdigest(),
        "size": zip_path.stat().st_size,
        "files": build_file_manifest_for_zip(zip_path),
        "data_schema_version": DATA_SCHEMA_VERSION,
    }
    (release_dir / "release-manifest.json").write_text(
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


def _run(*argv: object, fast_selfcheck: bool = True) -> int:
    """Run the CLI in-process; optionally skip only the service probe."""
    if fast_selfcheck:
        real_manager = cli.UpgradeManager

        def manager_without_service_probe(config):
            config.self_check = dataclasses.replace(
                config.self_check, check_service=False
            )
            return real_manager(config)

        cli.UpgradeManager = manager_without_service_probe
    try:
        return main([str(item) for item in argv])
    finally:
        if fast_selfcheck:
            cli.UpgradeManager = real_manager


def test_upgrade_help_lists_the_flag_and_renders_paths(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Guard both the option name and argparse's ``%``-formatting in help."""
    with pytest.raises(SystemExit) as exc:
        main(["upgrade", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "--allow-same-or-downgrade" in out
    assert "%LOCALAPPDATA%\\LiuHao\\data" in out


# ---------------------------------------------------------------------------
# --allow-same-or-downgrade must actually take effect
# ---------------------------------------------------------------------------
def test_same_version_reinstall_requires_the_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # conftest 会把 LIUHAO_DATA_DIR 指到 pytest tmp；这里断言的是
    # “备份落在 app-root/data”，必须让 CLI 走默认解析而不是环境变量。
    monkeypatch.delenv("LIUHAO_DATA_DIR", raising=False)
    app_root = _setup_app(tmp_path / "app", "0.1.0", value="VALUE = 1\n")
    release_dir = tmp_path / "releases"
    _release(release_dir, "0.1.0", value="VALUE = 2\n")
    release = LocalDirectorySource(release_dir)
    manager = UpgradeManager(
        UpgradeConfig(app_root=app_root, data_dir=app_root / "data", source=release)
    )
    try:
        without_flag = manager.check()
        assert without_flag.status is CheckStatus.UP_TO_DATE
        assert without_flag.message == "已是最新版本 0.1.0"
    finally:
        manager.close()

    # Without the flag the CLI must not install anything (it exits 0 as "no
    # update", but the bytes stay as they were).
    assert _run("upgrade", "--app-root", app_root, "--release-dir", release_dir) == 0
    assert (app_root / "src" / "app.py").read_text(encoding="utf-8") == "VALUE = 1\n"

    # With the flag the same version is a real install target (repair a
    # damaged installation by reinstalling the same release).
    assert (
        _run(
            "upgrade",
            "--app-root",
            app_root,
            "--release-dir",
            release_dir,
            "--allow-same-or-downgrade",
        )
        == 0
    )
    assert read_current_version(app_root)[0] == "0.1.0"
    assert (app_root / "src" / "app.py").read_text(encoding="utf-8") == "VALUE = 2\n"
    assert _rows(app_root / "data" / "app.db") == ["keep"]
    assert list((app_root / "data" / "upgrade-backups").glob("*/backup.json"))


def test_same_version_check_explains_the_override(tmp_path: Path) -> None:
    app_root = _setup_app(tmp_path / "app", "0.1.0")
    release_dir = tmp_path / "releases"
    _release(release_dir, "0.1.0")
    manager = UpgradeManager(
        UpgradeConfig(
            app_root=app_root,
            data_dir=app_root / "data",
            source=LocalDirectorySource(release_dir),
            allow_same_or_downgrade=True,
        )
    )
    try:
        check = manager.check()
        assert check.status is CheckStatus.UPDATE_AVAILABLE
        assert check.release is not None and check.release.version == "0.1.0"
        assert "与当前版本相同" in check.message
        assert "允许重装/降级" in check.message
    finally:
        manager.close()


def test_downgrade_runs_only_with_the_flag(tmp_path: Path) -> None:
    app_root = _setup_app(tmp_path / "app", "0.2.0", value="VALUE = 3\n")
    release_dir = tmp_path / "releases"
    _release(release_dir, "0.1.0", value="VALUE = 2\n")
    manager = UpgradeManager(
        UpgradeConfig(app_root=app_root, data_dir=app_root / "data", source=LocalDirectorySource(release_dir))
    )
    try:
        check = manager.check()
        assert check.status is CheckStatus.UP_TO_DATE
        assert "高于发布清单中的最高版本" in check.message
    finally:
        manager.close()

    assert _run("upgrade", "--app-root", app_root, "--release-dir", release_dir) == 0
    assert read_current_version(app_root)[0] == "0.2.0"
    assert (app_root / "src" / "app.py").read_text(encoding="utf-8") == "VALUE = 3\n"
    assert not list((app_root / "data" / "upgrade-backups").glob("*/backup.json"))

    assert (
        _run(
            "upgrade",
            "--app-root",
            app_root,
            "--release-dir",
            release_dir,
            "--allow-same-or-downgrade",
        )
        == 0
    )
    assert read_current_version(app_root)[0] == "0.1.0"
    assert (app_root / "src" / "app.py").read_text(encoding="utf-8") == "VALUE = 2\n"
    assert _rows(app_root / "data" / "app.db") == ["keep"]


def test_check_json_is_machine_parseable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    app_root = _setup_app(tmp_path / "app", "0.1.0")
    release_dir = tmp_path / "releases"
    _release(release_dir, "0.1.0")
    assert (
        _run(
            "check",
            "--app-root",
            app_root,
            "--release-dir",
            release_dir,
            "--json",
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "up_to_date"


# ---------------------------------------------------------------------------
# data directory must follow the installer's real rules
# ---------------------------------------------------------------------------
def test_portable_tree_keeps_data_under_app_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("LIUHAO_DATA_DIR", raising=False)
    app_root = _setup_app(tmp_path / "app", "0.1.0")
    assert default_data_dir(app_root) == (app_root / "data").resolve()
    assert UpgradeConfig(app_root=app_root).data_dir == (app_root / "data").resolve()


def test_installed_tree_uses_localappdata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("LIUHAO_DATA_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    app_root = _setup_app(tmp_path / "app", "0.1.0", installed=True)
    expected = (tmp_path / "localappdata" / "LiuHao" / "data").resolve()
    assert default_data_dir(app_root) == expected
    assert UpgradeConfig(app_root=app_root).data_dir == expected


def test_data_dir_override_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    app_root = _setup_app(tmp_path / "app", "0.1.0", installed=True)

    env_dir = tmp_path / "env-data"
    monkeypatch.setenv("LIUHAO_DATA_DIR", str(env_dir))
    assert resolve_data_dir(app_root) == env_dir.resolve()

    explicit = tmp_path / "explicit-data"
    assert resolve_data_dir(app_root, explicit) == explicit.resolve()
    assert (
        UpgradeConfig(app_root=app_root, data_dir=explicit).data_dir
        == explicit.resolve()
    )


def test_cli_manager_uses_installer_rules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("LIUHAO_DATA_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    app_root = _setup_app(tmp_path / "app", "0.1.0", installed=True)
    expected = (tmp_path / "localappdata" / "LiuHao" / "data").resolve()

    args = _build_parser().parse_args(["status", "--app-root", str(app_root)])
    manager = _manager(args)
    try:
        assert manager.config.app_root == app_root.resolve()
        assert manager.config.data_dir == expected
    finally:
        manager.close()

    explicit = tmp_path / "explicit-data"
    args = _build_parser().parse_args(
        ["status", "--app-root", str(app_root), "--data-dir", str(explicit)]
    )
    manager = _manager(args)
    try:
        assert manager.config.data_dir == explicit.resolve()
    finally:
        manager.close()


def _program_files(root: Path, data_dir: Path) -> set[str]:
    """程序目录里除数据目录以外的文件（运行期残留就是这些）。"""
    files: set[str] = set()
    data_resolved = data_dir.resolve()
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            path.resolve().relative_to(data_resolved)
        except ValueError:
            files.add(path.relative_to(root).as_posix())
    return files


def test_cli_check_leaves_no_new_files_in_the_program_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """CLI ``check`` 的运行期状态必须落在数据目录，程序目录新增文件数为 0。

    旧实现把 ``upgrade-state``（last-check.json / status.json /
    upgrade-ledger.db）写进程序目录，卸载后残留。
    """
    monkeypatch.delenv("LIUHAO_DATA_DIR", raising=False)
    monkeypatch.delenv("LIUHAO_UPGRADE_STATE_DIR", raising=False)
    app_root = _setup_app(tmp_path / "app", "0.1.0")
    release_dir = tmp_path / "releases"
    _release(release_dir, "0.1.0")
    data_dir = app_root / "data"
    before = _program_files(app_root, data_dir)

    assert (
        _run(
            "check",
            "--app-root",
            app_root,
            "--release-dir",
            release_dir,
            "--json",
        )
        == 0
    )
    capsys.readouterr()
    after = _program_files(app_root, data_dir)

    assert after == before, f"程序目录出现新增运行期文件：{sorted(after - before)}"
    assert not (app_root / "upgrade-state").exists()
    assert (data_dir / "upgrade-state" / "last-check.json").is_file()
    assert (data_dir / "upgrade-state" / "upgrade-ledger.db").is_file()


def test_legacy_state_dir_in_program_directory_is_migrated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """旧版本已在程序目录写下的 ``upgrade-state`` 要能读、能迁移、不报错。"""
    monkeypatch.delenv("LIUHAO_DATA_DIR", raising=False)
    monkeypatch.delenv("LIUHAO_UPGRADE_STATE_DIR", raising=False)
    app_root = _setup_app(tmp_path / "app", "0.1.0")
    data_dir = app_root / "data"
    legacy = app_root / "upgrade-state"
    legacy.mkdir()
    (legacy / "last-check.json").write_text(
        json.dumps({"status": "up_to_date", "message": "legacy", "checked_at": "2026-01-01"}),
        encoding="utf-8",
    )

    manager = UpgradeManager(UpgradeConfig(app_root=app_root, data_dir=data_dir))
    try:
        state = manager.last_check()
        assert state["status"] == "up_to_date"
        assert not legacy.exists()
        assert (data_dir / "upgrade-state" / "last-check.json").is_file()
    finally:
        manager.close()


def test_cli_apply_preflights_the_installed_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A corrupt DB in %LOCALAPPDATA% must stop the CLI before any mutation.

    If the CLI still looked at ``<app-root>/data`` (which does not exist in
    an installed tree), preflight would pass silently and "data survived"
    would be a lie.
    """
    monkeypatch.delenv("LIUHAO_DATA_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    real_data = tmp_path / "localappdata" / "LiuHao" / "data"
    app_root = _setup_app(
        tmp_path / "app", "0.1.0", installed=True, data_dir=real_data
    )
    (real_data / "app.db").write_bytes(b"SQLite format 3\x00corrupted")
    release_dir = tmp_path / "releases"
    _release(release_dir, "0.2.0")

    assert _run("upgrade", "--app-root", app_root, "--release-dir", release_dir) == 1
    stderr = capsys.readouterr().err
    assert str(real_data / "app.db") in stderr
    assert "数据校验未通过" in stderr
    assert (app_root / "src" / "app.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert not list((app_root / "data" / "upgrade-backups").glob("*/backup.json"))
    assert not (app_root / "data").exists()


def test_api_manager_uses_the_same_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.upgrade.update_check import build_manager_from_env

    monkeypatch.delenv("LIUHAO_DATA_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    app_root = _setup_app(
        tmp_path / "app",
        "0.1.0",
        installed=True,
        data_dir=tmp_path / "localappdata" / "LiuHao" / "data",
    )
    release_dir = tmp_path / "releases"
    _release(release_dir, "0.2.0")
    monkeypatch.setenv("LIUHAO_APP_ROOT", str(app_root))
    monkeypatch.setenv("LIUHAO_UPGRADE_RELEASE_DIR", str(release_dir))
    monkeypatch.setenv("LIUHAO_UPGRADE_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("LIUHAO_UPGRADE_BACKUP_ROOT", str(tmp_path / "backups"))

    manager = build_manager_from_env()
    try:
        expected = (tmp_path / "localappdata" / "LiuHao" / "data").resolve()
        assert manager.config.app_root == app_root.resolve()
        assert manager.config.data_dir == expected
        assert manager.data_paths()[0] == expected
    finally:
        manager.close()

    env_dir = tmp_path / "env-data"
    monkeypatch.setenv("LIUHAO_DATA_DIR", str(env_dir))
    manager = build_manager_from_env()
    try:
        assert manager.config.data_dir == env_dir.resolve()
    finally:
        manager.close()
