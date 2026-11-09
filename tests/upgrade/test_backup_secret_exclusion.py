"""C'：主密钥/管理令牌不进备份；恢复不覆盖本机文件；跨机恢复明确失败。"""
from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from src.upgrade.backups import backup_data, restore_data, verify_backup
from src.upgrade.errors import BackupCorrupt, RollbackError


def _make_data_dir(tmp_path: Path) -> tuple[Path, Path]:
    app_root = tmp_path / "app"
    data_dir = app_root / "data"
    (data_dir / "mobile_access").mkdir(parents=True)
    (data_dir / "master.key").write_bytes(b"k" * 44)
    (data_dir / "mobile_access" / "admin.token").write_bytes(b"t" * 43)
    (data_dir / "notes.txt").write_text("keep", encoding="utf-8")
    return app_root, data_dir


def _write_code_backup(backup_dir: Path) -> None:
    """``verify_backup`` 要求 code.zip + code-backup.json，这里造最小可用的一份。"""
    zip_path = backup_dir / "code.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("main.py", "print('x')\n")
    (backup_dir / "code-backup.json").write_text(
        json.dumps(
            {
                "schema": "liuhao.code-backup/1",
                "file_count": 1,
                "sha256": hashlib.sha256(zip_path.read_bytes()).hexdigest(),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_backup_excludes_master_key_and_admin_token(tmp_path: Path) -> None:
    app_root, data_dir = _make_data_dir(tmp_path)
    dest = tmp_path / "backup"

    backup = backup_data(app_root, data_dir, [], dest)

    # 1) 产物里没有这两个文件（物理文件 + 清单条目都查）
    assert not (dest / "data" / "master.key").exists()
    assert not (dest / "data" / "mobile_access" / "admin.token").exists()
    assert not (dest / "data" / "mobile_access").exists()

    manifest = json.loads((dest / "data-backup.json").read_text(encoding="utf-8"))
    relpaths = {entry["relpath"] for entry in manifest["entries"]}
    assert relpaths == {"notes.txt"}
    assert manifest["excluded_protected"] == 2
    assert manifest["secrets_excluded"] is True
    assert manifest["master_key_present"] is True
    assert backup.file_count == 1


def test_same_machine_restore_keeps_existing_secrets(tmp_path: Path) -> None:
    app_root, data_dir = _make_data_dir(tmp_path)
    dest = tmp_path / "backup"
    backup_data(app_root, data_dir, [], dest)

    master_before = (data_dir / "master.key").read_bytes()
    token_path = data_dir / "mobile_access" / "admin.token"
    token_before = token_path.read_bytes()
    (data_dir / "notes.txt").write_text("changed-later", encoding="utf-8")

    restored = restore_data(app_root, data_dir, dest)

    assert restored == 1
    assert (data_dir / "notes.txt").read_text(encoding="utf-8") == "keep"
    # 2) 磁盘上已有的两份一个字节都没动
    assert (data_dir / "master.key").read_bytes() == master_before
    assert token_path.read_bytes() == token_before


def test_cross_machine_restore_refuses_before_copying(tmp_path: Path) -> None:
    app_root, data_dir = _make_data_dir(tmp_path)
    dest = tmp_path / "backup"
    backup_data(app_root, data_dir, [], dest)

    new_root = tmp_path / "another-machine"
    new_data = new_root / "data"
    new_data.mkdir(parents=True)

    with pytest.raises(RollbackError) as excinfo:
        restore_data(new_root, new_data, dest)

    message = str(excinfo.value)
    assert "跨机恢复缺少主密钥" in message
    assert "master.key" in message
    assert "LIUHAO_RESTORE_ALLOW_NEW_KEY=1" in message
    # 3) 预检在复制之前：普通文件也一个没落盘
    assert not (new_data / "notes.txt").exists()


def test_cross_machine_restore_allows_explicit_reinitialize(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_root, data_dir = _make_data_dir(tmp_path)
    dest = tmp_path / "backup"
    backup_data(app_root, data_dir, [], dest)

    new_root = tmp_path / "another-machine"
    new_data = new_root / "data"
    new_data.mkdir(parents=True)
    monkeypatch.setenv("LIUHAO_RESTORE_ALLOW_NEW_KEY", "1")

    restored = restore_data(new_root, new_data, dest)

    assert restored == 1
    assert (new_data / "notes.txt").read_text(encoding="utf-8") == "keep"
    # 即便显式放行，也不会凭空写出密钥文件
    assert not (new_data / "master.key").exists()
    assert not (new_data / "mobile_access" / "admin.token").exists()


def test_legacy_backup_with_secrets_verifies_and_is_skipped(tmp_path: Path) -> None:
    """补丁前生成的备份必然含这两个文件：不能因此让历史回滚点失效。"""
    app_root, data_dir = _make_data_dir(tmp_path)
    legacy = tmp_path / "legacy-backup"
    (legacy / "data" / "mobile_access").mkdir(parents=True)
    (legacy / "data" / "master.key").write_bytes(b"OLD" * 15)
    (legacy / "data" / "mobile_access" / "admin.token").write_bytes(b"old-token")
    (legacy / "data" / "notes.txt").write_text("keep", encoding="utf-8")

    entries = []
    for rel in ("master.key", "mobile_access/admin.token", "notes.txt"):
        payload = (legacy / "data" / rel).read_bytes()
        entries.append(
            {
                "kind": "data",
                "relpath": rel,
                "sqlite": False,
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    (legacy / "data-backup.json").write_text(
        json.dumps(
            {"schema": "liuhao.data-backup/1", "file_count": 3, "entries": entries}
        ),
        encoding="utf-8",
    )
    _write_code_backup(legacy)

    verify_backup(legacy)  # 旧备份容忍，不抛

    sentinel = b"LIVE-KEY-MUST-SURVIVE"
    (data_dir / "master.key").write_bytes(sentinel)
    token_path = data_dir / "mobile_access" / "admin.token"
    token_before = token_path.read_bytes()
    (data_dir / "notes.txt").write_text("changed", encoding="utf-8")

    restored = restore_data(app_root, data_dir, legacy)

    assert restored == 1
    assert (data_dir / "master.key").read_bytes() == sentinel
    assert token_path.read_bytes() == token_before
    assert (data_dir / "notes.txt").read_text(encoding="utf-8") == "keep"


def test_new_backup_carrying_a_secret_is_rejected(tmp_path: Path) -> None:
    app_root, data_dir = _make_data_dir(tmp_path)
    dest = tmp_path / "backup"
    backup_data(app_root, data_dir, [], dest)
    _write_code_backup(dest)
    (dest / "data" / "master.key").write_bytes(b"smuggled")

    with pytest.raises(BackupCorrupt) as excinfo:
        verify_backup(dest)

    assert "受保护文件" in str(excinfo.value)


def test_restore_is_not_blocked_when_source_had_no_master_key(tmp_path: Path) -> None:
    """从没有主密钥的安装做备份，恢复不该被跨机预检误伤。"""
    app_root = tmp_path / "app"
    data_dir = app_root / "data"
    data_dir.mkdir(parents=True)
    (data_dir / "notes.txt").write_text("keep", encoding="utf-8")
    dest = tmp_path / "backup"
    backup_data(app_root, data_dir, [], dest)

    new_root = tmp_path / "another-machine"
    new_data = new_root / "data"
    new_data.mkdir(parents=True)

    assert restore_data(new_root, new_data, dest) == 1
    assert (new_data / "notes.txt").read_text(encoding="utf-8") == "keep"


def test_manager_refuses_before_replacing_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """回滚编排里，跨机预检必须发生在 ``restore_code`` 之前。

    否则会先换掉旧代码、再在数据阶段失败，留下「旧代码 + 缺主密钥」的半完成
    状态。这里用哨兵证明 ``restore_code`` 未被调用。
    """
    from src.upgrade import manager as manager_mod

    app_root, data_dir = _make_data_dir(tmp_path)
    backup_id = "b-cross"
    dest = tmp_path / "backups" / backup_id
    backup_data(app_root, data_dir, [], dest)
    _write_code_backup(dest)
    (dest / "backup.json").write_text(
        json.dumps(
            {
                "schema": "liuhao.upgrade-backup/1",
                "from_version": "0.1.2",
                "to_version": "0.1.3",
            }
        ),
        encoding="utf-8",
    )

    other_root = tmp_path / "another-machine"
    other_data = other_root / "data"
    other_data.mkdir(parents=True)
    (other_root / "VERSION").write_text("0.1.3\n", encoding="utf-8")

    mgr = manager_mod.UpgradeManager(
        manager_mod.UpgradeConfig(
            app_root=other_root,
            data_dir=other_data,
            backup_root=tmp_path / "backups",
            state_dir=tmp_path / "state",
        )
    )
    called = {"restore_code": False}
    monkeypatch.setattr(
        manager_mod,
        "restore_code",
        lambda *a, **k: called.__setitem__("restore_code", True),
    )

    with pytest.raises(RollbackError) as excinfo:
        mgr._restore_backup(backup_id)

    assert "跨机恢复缺少主密钥" in str(excinfo.value)
    assert called["restore_code"] is False
    assert (other_root / "VERSION").read_text(encoding="utf-8").strip() == "0.1.3"
