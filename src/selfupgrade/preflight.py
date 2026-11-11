"""自升级预检：锁、安装包、磁盘、目标目录。"""

from __future__ import annotations

import shutil
from pathlib import Path

from . import paths
from .manifest import verify_manifest


def preflight(repo: Path, installer: Path, install_dir: Path, data_dir: Path) -> list[str]:
    problems: list[str] = []
    try:
        paths.assert_safe_test_target(install_dir, data_dir)
    except paths.UnsafeTargetError as exc:
        problems.append(str(exc))

    installer = Path(installer)
    if not installer.is_file():
        problems.append(f"安装包不存在：{installer}")

    verify = verify_manifest(Path(repo), paths.LOCK_DIR)
    if not verify.ok:
        problems.extend(f"release-lock 校验失败：{p}" for p in verify.problems)

    try:
        free = shutil.disk_usage(str(install_dir.anchor)).free
        if free < 2 * 1024**3:
            problems.append(f"安装盘剩余空间不足 2GB：{free / 1024**3:.1f}GB")
    except OSError:
        pass
    return problems
