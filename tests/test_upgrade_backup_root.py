"""升级备份根的四态语义：显式跨盘、不可写、同盘、未配置。

这组用例锁定 ``LIUHAO_UPGRADE_BACKUP_ROOT`` 的行为：
- 显式配置的备份根只在该根内探针/落盘，失败时明确拒绝，不静默改道数据目录；
- 未配置时按“未启用跨盘备份”处理，并在状态里给出中文警告；
- 同盘配置照常可用，但状态必须标出磁盘损坏风险；
- 备份盘空间不足时在写任何真实文件前拒绝。
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from src.upgrade.manager import (
    BACKUP_DIRNAME,
    DiskSpaceError,
    UpgradeConfig,
    UpgradeManager,
)


def _app(root: Path, version: str = "0.1.0") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "liuhao-ai-os"\nversion = "{version}"\n',
        encoding="utf-8",
    )
    (root / "src").mkdir(exist_ok=True)
    (root / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "data").mkdir(exist_ok=True)
    (root / "data" / "state.json").write_text('{"ok": true}', encoding="utf-8")
    return root


def _manager(tmp_path: Path, *, backup_root: str | Path | None) -> UpgradeManager:
    app = _app(tmp_path / "app")
    config = UpgradeConfig(
        app_root=app,
        data_dir=tmp_path / "data",
        state_dir=tmp_path / "state",
        backup_root=backup_root,
    )
    return UpgradeManager(config)


def test_configured_cross_disk_backup_root(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("src.upgrade.manager.same_volume", lambda *_: False)
    root = tmp_path / "backup"
    manager = _manager(tmp_path, backup_root=root)
    summary = manager.backup_summary()

    assert summary["source"] == "configured"
    assert summary["available"] is True
    assert summary["same_volume"] is False
    assert summary["cross_disk"] is True
    assert summary["label"] == "跨盘备份已启用"
    assert summary["warning"] == ""
    assert Path(summary["root"]) == root.resolve()
    # 探针/落盘都在显式根内，没有跑到数据目录。
    assert root.is_dir()
    assert not (tmp_path / "data" / BACKUP_DIRNAME).exists()


def test_configured_unwritable_never_falls_back(tmp_path: Path) -> None:
    blocked = tmp_path / "blocked-file"
    blocked.write_text("not a directory", encoding="utf-8")
    manager = _manager(tmp_path, backup_root=blocked)
    summary = manager.backup_summary()

    assert summary["source"] == "configured"
    assert summary["available"] is False
    assert summary["cross_disk"] is False
    assert summary["label"] == "备份盘不可写，备份未启用"
    assert "不可写" in summary["error"] and "备份未启用" in summary["error"]
    # 关键：失败时不改道数据目录，备份根仍是用户配置的那个路径。
    assert Path(summary["root"]) == blocked.resolve()
    assert not (tmp_path / "data" / BACKUP_DIRNAME).exists()

    with pytest.raises(DiskSpaceError) as exc:
        manager._check_disk_space(0)
    assert "不可写" in str(exc.value) and "备份未启用" in str(exc.value)


def test_configured_same_volume_warns(tmp_path: Path) -> None:
    root = tmp_path / "same-volume-backup"
    manager = _manager(tmp_path, backup_root=root)
    summary = manager.backup_summary()

    assert summary["available"] is True
    assert summary["same_volume"] is True
    assert summary["cross_disk"] is False
    assert summary["label"] == "备份与数据在同一磁盘"
    assert "备份与数据在同一磁盘，磁盘损坏时无法恢复" in summary["warning"]


def test_unconfigured_marks_not_cross_disk(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("LIUHAO_UPGRADE_BACKUP_ROOT", raising=False)
    manager = _manager(tmp_path, backup_root=None)
    summary = manager.backup_summary()

    assert summary["source"] in {"data_dir", "portable"}
    assert summary["configured"] is False
    assert summary["same_volume"] is True
    assert summary["cross_disk"] is False
    assert "未启用跨盘备份" in summary["warning"]
    assert Path(summary["root"]) == (tmp_path / "data" / BACKUP_DIRNAME).resolve()
    assert Path(summary["root"]).is_dir()


def test_backup_disk_space_insufficient_rejected(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "backup"
    manager = _manager(tmp_path, backup_root=root)

    original_disk_usage = shutil.disk_usage

    def fake_disk_usage(path: str):
        return original_disk_usage(str(tmp_path))._replace(free=1024)

    monkeypatch.setattr("src.upgrade.manager.shutil.disk_usage", fake_disk_usage)
    with pytest.raises(DiskSpaceError) as exc:
        manager._check_disk_space(0)
    message = str(exc.value)
    assert "磁盘空间不足" in message
    assert "备份未启用" in message and "尚未修改任何文件" in message
