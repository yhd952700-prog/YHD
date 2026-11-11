"""回滚：重装 known-good 安装包；重装失败则落救援启动器。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import paths
from .install import install_and_smoke, stop_installed
from .manifest import sha256_file
from .models import SmokeResult


def rollback_to_known_good(
    repo: Path,
    known_good: dict[str, Any],
    install_dir: Path,
    data_dir: Path,
    port: int,
    evidence_root: Path,
) -> SmokeResult:
    install_dir, data_dir = paths.assert_safe_test_target(install_dir, data_dir)
    installer = Path(str(known_good.get("installer", "")))
    expected_sha = str(known_good.get("sha256", ""))
    if not installer.is_file():
        return SmokeResult(False, 2, False, f"known-good 安装包不存在：{installer}")
    actual_sha = sha256_file(installer)
    if expected_sha and actual_sha != expected_sha:
        return SmokeResult(
            False,
            1,
            False,
            f"known-good 安装包哈希不符（期望 {expected_sha[:12]}…，实际 {actual_sha[:12]}…）",
        )

    # 先停掉失败的实例，避免文件占用导致覆盖安装失败。
    stop_installed(install_dir)
    return install_and_smoke(
        repo,
        installer,
        install_dir,
        data_dir,
        port,
        Path(evidence_root) / "rollback",
    )
