"""Disaster drills for the self-upgrade path.

Each scenario is executed for real against a throwaway app tree:

1. the applying process is killed mid file replacement, then a fresh start
   must resolve the interrupted state and roll back;
2. a disk-full condition is simulated at the real write call and must abort
   before any live file is touched;
3. a backup whose SHA256 no longer matches must be refused, with the current
   installation left intact;
4. a missing old-version package must produce a "re-download X" instruction
   and must not block a later upgrade.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import types
import zipfile
from pathlib import Path

import pytest

import src.upgrade.archive as archive_module
import src.upgrade.backups as backups_module
import src.upgrade.manager as manager_module
from src.upgrade import (
    ApplyError,
    BackupCorrupt,
    BackupNotFound,
    DiskSpaceError,
    LocalDirectorySource,
    RollbackError,
    UpgradeConfig,
    UpgradeManager,
    read_current_version,
)
from src.upgrade.backups import backup_code, restore_code
from src.upgrade.dataversion import DATA_SCHEMA_VERSION
from src.upgrade.filemanifest import build_file_manifest_for_zip


def _setup_app(root: Path, version: str = "0.1.0") -> Path:
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
        "files": build_file_manifest_for_zip(zip_path),
        "data_schema_version": DATA_SCHEMA_VERSION,
    }
    manifest = {"schema": "liuhao.release-manifest/1", "releases": [release]}
    (release_dir / "release-manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    return release


def _manager(app_root: Path, release_dir: Path) -> UpgradeManager:
    # 显式指定数据目录：conftest 会把 LIUHAO_DATA_DIR 指到 pytest 的 tmp，
    # 若跟随环境变量，备份与状态会落在用例断言之外的目录。
    return UpgradeManager(
        UpgradeConfig(
            app_root=app_root,
            data_dir=app_root / "data",
            source=LocalDirectorySource(release_dir),
        )
    )


def _rows(db: Path) -> list[str]:
    conn = sqlite3.connect(db)
    try:
        return [row[0] for row in conn.execute("SELECT v FROM t").fetchall()]
    finally:
        conn.close()


def _snapshot(app_root: Path) -> tuple[bytes, list[str], bytes]:
    return (
        (app_root / "pyproject.toml").read_bytes(),
        _rows(app_root / "data" / "app.db"),
        (app_root / "src" / "app.py").read_bytes(),
    )


def _ledger_rows(app_root: Path) -> list[tuple]:
    conn = sqlite3.connect(app_root / "data" / "upgrade-state" / "upgrade-ledger.db")
    try:
        return conn.execute(
            "SELECT kind, status, backup_id, from_version, reason "
            "FROM upgrade_records ORDER BY rowid"
        ).fetchall()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 1. process killed during file replacement
# ---------------------------------------------------------------------------
_CHILD_SCRIPT = r'''
import os
import sys
from pathlib import Path

import src.upgrade.manager as manager_module
from src.upgrade import LocalDirectorySource, UpgradeConfig, UpgradeManager

app_root = Path(sys.argv[1])
release_dir = Path(sys.argv[2])
manager = UpgradeManager(
    UpgradeConfig(
        app_root=app_root,
        data_dir=app_root / "data",
        source=LocalDirectorySource(release_dir),
    )
)
check = manager.check()
assert check.release is not None, check.message
download = manager.download(check.release)

real_extract = manager_module.extract_zip_members

def kill_during_install(archive, members, target_root):
    if Path(target_root).resolve() == app_root.resolve():
        # Real replacement of the first two real files, then an abrupt process
        # death: no finally block, no rollback, exactly like a power loss.
        real_extract(archive, members[:2], target_root)
        os._exit(23)
    return real_extract(archive, members, target_root)

manager_module.extract_zip_members = kill_during_install
manager.apply(download.path, check.release)
'''


def test_kill_during_file_replacement_is_recovered_on_restart(tmp_path: Path) -> None:
    app_root = _setup_app(tmp_path / "app")
    release_dir = tmp_path / "releases"
    _release(release_dir, version="0.2.0")

    script = tmp_path / "kill_child.py"
    script.write_text(_CHILD_SCRIPT, encoding="utf-8")
    repo_root = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        [sys.executable, str(script), str(app_root), str(release_dir)],
        cwd=str(repo_root),
        env={**os.environ, "PYTHONPATH": str(repo_root)},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 23, proc.stderr

    # The interrupted process must leave a decidable status and a ledger row,
    # not an empty directory or "unknown".
    status = json.loads(
        (app_root / "data" / "upgrade-state" / "status.json").read_text(encoding="utf-8")
    )
    assert status["phase"] == "applying"
    assert status["backup_id"]
    rows = _ledger_rows(app_root)
    assert any(
        row[0] == "apply" and row[1] == "in_progress" and row[2]
        for row in rows
    )

    # A fresh start is the real restart: __init__ must resolve the interruption.
    manager = _manager(app_root, release_dir)
    try:
        assert read_current_version(app_root)[0] == "0.1.0"
        assert (app_root / "src" / "app.py").read_text(encoding="utf-8") == "VALUE = 1\n"
        assert _rows(app_root / "data" / "app.db") == ["keep"]
        recovered = manager.status()
        assert recovered["phase"] == "rolled_back"
        assert "中断" in recovered["message"]
        assert "已自动恢复" in recovered["message"]
        history = manager.history()
        assert any(
            item["kind"] == "apply" and item["status"] == "interrupted_rolled_back"
            for item in history
        )
        assert any(
            item["kind"] == "rollback" and item["status"] == "rolled_back"
            for item in history
        )
    finally:
        manager.close()


# ---------------------------------------------------------------------------
# 2. disk space exhaustion
# ---------------------------------------------------------------------------
def test_disk_space_preflight_aborts_before_any_file_is_touched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_root = _setup_app(tmp_path / "app")
    release_dir = tmp_path / "releases"
    _release(release_dir, version="0.2.0")
    before = _snapshot(app_root)
    backups_before = list((app_root / "data" / "upgrade-backups").glob("*/backup.json"))

    fake_usage = types.SimpleNamespace(
        total=2 * 1024 * 1024, used=2 * 1024 * 1024 - 1024 * 1024, free=1024 * 1024
    )
    monkeypatch.setattr(
        manager_module.shutil, "disk_usage", lambda path: fake_usage
    )

    manager = _manager(app_root, release_dir)
    try:
        check = manager.check()
        assert check.release is not None
        download = manager.download(check.release)
        with pytest.raises(ApplyError) as excinfo:
            manager.apply(download.path, check.release)
        message = str(excinfo.value)
        assert "磁盘空间不足" in message
        assert "尚未修改任何文件" in message
        assert "当前版本 0.1.0 仍完整" in message
        assert _snapshot(app_root) == before
        assert list((app_root / "data" / "upgrade-backups").glob("*/backup.json")) == backups_before
        state = manager.status()
        assert state["phase"] == "failed"
        assert "磁盘空间不足" in state["message"]
        assert any(
            item["kind"] == "apply"
            and item["status"] == "failed"
            and "磁盘空间不足" in item["reason"]
            for item in manager.history()
        )
    finally:
        manager.close()


def test_enospc_during_staging_write_leaves_no_half_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_root = _setup_app(tmp_path / "app")
    release_dir = tmp_path / "releases"
    _release(release_dir, version="0.2.0")
    before = _snapshot(app_root)

    def fake_copy(source, target):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(archive_module, "_atomic_stream_copy", fake_copy)

    manager = _manager(app_root, release_dir)
    try:
        check = manager.check()
        assert check.release is not None
        download = manager.download(check.release)
        with pytest.raises(ApplyError) as excinfo:
            manager.apply(download.path, check.release)
        message = str(excinfo.value)
        assert "磁盘空间不足" in message
        assert "尚未修改任何文件" in message
        assert _snapshot(app_root) == before
        assert not list((app_root / "data" / "upgrade-backups").glob("*/backup.json"))
        assert manager.status()["phase"] == "failed"
    finally:
        manager.close()


# ---------------------------------------------------------------------------
# 3. corrupted backup
# ---------------------------------------------------------------------------
def test_corrupt_backup_is_refused_and_current_version_stays_intact(
    tmp_path: Path,
) -> None:
    app_root = _setup_app(tmp_path / "app")
    release_dir = tmp_path / "releases"
    _release(release_dir, version="0.2.0")

    manager = _manager(app_root, release_dir)
    try:
        check = manager.check()
        download = manager.download(check.release)
        result = manager.apply(download.path, check.release)
        assert read_current_version(app_root)[0] == "0.2.0"
        backup_dir = app_root / "data" / "upgrade-backups" / result.backup_id
        before = _snapshot(app_root)

        # Corrupt the old-version package after the SHA256 was recorded.
        with open(backup_dir / "code.zip", "ab") as handle:
            handle.write(b"tampered")

        with pytest.raises(RollbackError) as excinfo:
            manager.rollback(backup_id=result.backup_id)
        message = str(excinfo.value)
        assert "备份不可用" in message
        assert "当前版本 0.2.0 仍完整" in message
        assert "未做任何修改" in message

        # Nothing was restored, not even partially.
        assert read_current_version(app_root)[0] == "0.2.0"
        assert _snapshot(app_root) == before
        assert not (backup_dir / "rollback-trash").exists()
        state = manager.status()
        assert state["phase"] == "failed"
        assert "备份不可用" in state["message"]
        history = manager.history(kind="rollback")
        assert history and history[0]["status"] == "failed"
        assert "备份不可用" in history[0]["reason"]
        with pytest.raises(BackupCorrupt):
            manager._restore_backup(result.backup_id)
    finally:
        manager.close()


def test_corrupt_data_snapshot_is_also_refused_before_restore(
    tmp_path: Path,
) -> None:
    app_root = _setup_app(tmp_path / "app")
    release_dir = tmp_path / "releases"
    _release(release_dir, version="0.2.0")

    manager = _manager(app_root, release_dir)
    try:
        check = manager.check()
        download = manager.download(check.release)
        result = manager.apply(download.path, check.release)
        backup_dir = app_root / "data" / "upgrade-backups" / result.backup_id
        before = _snapshot(app_root)
        with open(backup_dir / "data" / "app.db", "ab") as handle:
            handle.write(b"tampered")

        with pytest.raises(RollbackError) as excinfo:
            manager.rollback(backup_id=result.backup_id)
        assert "备份不可用" in str(excinfo.value)
        assert _snapshot(app_root) == before
        assert read_current_version(app_root)[0] == "0.2.0"
    finally:
        manager.close()


# ---------------------------------------------------------------------------
# 4. missing old-version package
# ---------------------------------------------------------------------------
def test_missing_old_package_tells_user_to_redownload_and_does_not_block(
    tmp_path: Path,
) -> None:
    app_root = _setup_app(tmp_path / "app")
    release_dir = tmp_path / "releases"
    _release(release_dir, version="0.2.0")

    manager = _manager(app_root, release_dir)
    try:
        check = manager.check()
        download = manager.download(check.release)
        result = manager.apply(download.path, check.release)
        backup_dir = app_root / "data" / "upgrade-backups" / result.backup_id
        # The old version's package inside the backup is gone (cleaned disk,
        # antivirus quarantine, interrupted copy...).
        (backup_dir / "code.zip").unlink()
        before = _snapshot(app_root)

        with pytest.raises(RollbackError) as excinfo:
            manager.rollback(backup_id=result.backup_id)
        message = str(excinfo.value)
        assert "无法回滚" in message
        assert "需要重新下载 0.1.0 版本安装包" in message
        assert "当前版本 0.2.0 仍可正常使用" in message
        assert "--allow-same-or-downgrade" in message
        assert _snapshot(app_root) == before
        state = manager.status()
        assert state["phase"] == "failed"
        assert "需要重新下载 0.1.0 版本安装包" in state["message"]
        history = manager.history(kind="rollback")
        assert history and history[0]["status"] == "failed"
        with pytest.raises(BackupNotFound):
            manager._restore_backup(result.backup_id)

        # Not stuck: a newer release still installs normally.
        _release(release_dir, version="0.3.0")
        check = manager.check()
        assert check.release is not None and check.release.version == "0.3.0"
        download = manager.download(check.release)
        manager.apply(download.path, check.release)
        assert read_current_version(app_root)[0] == "0.3.0"
        assert manager.status()["phase"] == "completed"
    finally:
        manager.close()


def test_cli_accepts_downgrade_recovery_flag(tmp_path: Path) -> None:
    from src.upgrade.cli import _build_parser

    args = _build_parser().parse_args(
        [
            "upgrade",
            "--app-root",
            str(tmp_path / "app"),
            "--release-dir",
            str(tmp_path / "releases"),
            "--allow-same-or-downgrade",
        ]
    )
    assert args.allow_same_or_downgrade is True


# ---------------------------------------------------------------------------
# 5. code restore fails midway: no half-unpacked tree is ever left behind
# ---------------------------------------------------------------------------
def _code_tree(app_root: Path) -> dict[str, bytes]:
    tree = {
        path.relative_to(app_root).as_posix(): path.read_bytes()
        for path in sorted((app_root / "src").rglob("*"))
        if path.is_file()
    }
    tree["pyproject.toml"] = (app_root / "pyproject.toml").read_bytes()
    return tree


def _fail_on_second_member(monkeypatch: pytest.MonkeyPatch) -> None:
    """第二次写文件时模拟“目标文件被占用”（真实占句柄的 PermissionError）。"""
    real_copy = backups_module._atomic_stream_copy
    calls = {"n": 0}

    def failing_copy(source, target):
        calls["n"] += 1
        if calls["n"] == 2:
            raise PermissionError(13, "拒绝访问：目标文件正被占用", str(target))
        return real_copy(source, target)

    monkeypatch.setattr(backups_module, "_atomic_stream_copy", failing_copy)


def test_restore_code_failure_restores_the_complete_pre_rollback_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """解压到被占用文件中止：先清半成品，再把 trash 放回，不留残缺树。"""
    app_root = _setup_app(tmp_path / "app")
    backup_dir = tmp_path / "code-backup"
    backup_code(app_root, ("src", "pyproject.toml"), backup_dir)

    # 模拟“已经升到 0.2.0 的当前树”：内容改了、多了文件、少了文件。
    (app_root / "pyproject.toml").write_text(
        '[project]\nname = "liuhao-ai-os"\nversion = "0.2.0"\n', encoding="utf-8"
    )
    (app_root / "src" / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
    (app_root / "src" / "only_in_new.py").write_text("NEW = 1\n", encoding="utf-8")
    new_tree = _code_tree(app_root)

    _fail_on_second_member(monkeypatch)
    with pytest.raises(RollbackError) as excinfo:
        restore_code(app_root, backup_dir, trash_root=backup_dir / "rollback-trash")

    assert "需人工介入" not in str(excinfo.value)
    # 回滚失败 = 回到“回滚前”的完整树，而不是解压了一半的新旧混合树。
    assert _code_tree(app_root) == new_tree
    assert (app_root / "src" / "app.py").read_text(encoding="utf-8") == "VALUE = 2\n"
    # 回收区已整体放回，没有残留（只留下空目录）。
    leftovers = [
        item
        for bucket in (backup_dir / "rollback-trash").iterdir()
        for item in bucket.iterdir()
    ]
    assert leftovers == []


def test_restore_code_reports_manual_intervention_with_residue_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """半成品清不掉、旧版本放不回时：必须显式报需人工介入并给出具体路径。"""
    app_root = _setup_app(tmp_path / "app")
    backup_dir = tmp_path / "code-backup"
    backup_code(app_root, ("src", "pyproject.toml"), backup_dir)
    (app_root / "src" / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
    (app_root / "src" / "only_in_new.py").write_text("NEW = 1\n", encoding="utf-8")

    _fail_on_second_member(monkeypatch)

    def refuse_delete(path):
        raise PermissionError(13, "文件被占用，无法删除", str(path))

    monkeypatch.setattr(backups_module, "_remove_path", refuse_delete)
    with pytest.raises(RollbackError) as excinfo:
        restore_code(app_root, backup_dir, trash_root=backup_dir / "rollback-trash")

    message = str(excinfo.value)
    assert "需人工介入" in message
    assert "半成品目录清理失败" in message
    # 残留位置必须具体到路径：半成品目录、回收区、旧版本代码包。
    assert str(app_root / "src") in message
    trash_dirs = list((backup_dir / "rollback-trash").iterdir())
    assert len(trash_dirs) == 1
    assert str(trash_dirs[0]) in message
    assert str(backup_dir / "code.zip") in message
    # 回收区里是“回滚前”的完整树，可按报错路径手工放回；
    # 旧版本本身仍完整保存在 code.zip 里。
    assert (trash_dirs[0] / "src" / "app.py").read_text(encoding="utf-8") == "VALUE = 2\n"


def test_restore_code_falls_back_to_in_place_when_roots_cannot_be_moved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """移不走（文件被占用）时原地恢复：补回缺失文件、清掉新版本残留。"""
    app_root = _setup_app(tmp_path / "app")
    backup_dir = tmp_path / "code-backup"
    backup_code(app_root, ("src", "pyproject.toml"), backup_dir)
    old_tree = _code_tree(app_root)

    # 升级中断后的真实状态：内容被改、旧文件被删、新文件已写入。
    (app_root / "src" / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
    (app_root / "src" / "gateway" / "main.py").unlink()
    (app_root / "src" / "only_in_new.py").write_text("NEW = 1\n", encoding="utf-8")

    def refuse_rename(source, destination):
        raise PermissionError(13, "另一个程序正在使用此文件", str(source))

    written: list[str] = []
    real_copy = backups_module._atomic_stream_copy

    def record_copy(source, target):
        written.append(Path(target).name)
        return real_copy(source, target)

    monkeypatch.setattr(backups_module.os, "rename", refuse_rename)
    monkeypatch.setattr(backups_module, "_atomic_stream_copy", record_copy)
    restored = restore_code(app_root, backup_dir, trash_root=backup_dir / "rollback-trash")

    assert restored == 2
    assert _code_tree(app_root) == old_tree
    # 内容已经一致的成员一律不写：被占用的句柄不能被“为了写一遍”撞上。
    assert "pyproject.toml" not in written
    assert {"app.py", "main.py"} <= set(written)
