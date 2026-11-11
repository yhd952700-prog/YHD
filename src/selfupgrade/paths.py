"""路径常量与「绝不碰线上安装/数据」的硬边界。"""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(os.environ.get("LIUHAO_REPO_ROOT", r"D:\LiuHao-AI-OS"))
LOCK_DIR = REPO_ROOT / "release-lock"
STATE_ROOT = Path(os.environ.get("LIUHAO_SELFUPGRADE_STATE", r"D:\LiuHaoSelfUpgrade"))

LIVE_INSTALL_DIR = Path(r"D:\LiuHao")
LIVE_DATA_DIR = Path(r"D:\LiuHaoData")

# 允许自装的测试根目录。任何不在这些根下的路径都拒绝。
TEST_INSTALL_ROOTS = (Path(r"D:\LiuHaoTest"), Path(r"D:\LiuHaoSmoke"))
TEST_DATA_ROOTS = (Path(r"D:\LiuHaoTestData"), Path(r"D:\LiuHaoSmoke"))

DEFAULT_TEST_INSTALL = Path(r"D:\LiuHaoTest")
DEFAULT_TEST_DATA = Path(r"D:\LiuHaoTestData")
DEFAULT_TEST_PORT = 18080


class UnsafeTargetError(RuntimeError):
    """目标目录碰到线上安装/数据，直接拒绝。"""


def _norm(path: str | Path) -> Path:
    return Path(os.path.abspath(str(path)))


def is_under(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def assert_safe_test_target(install_dir: str | Path, data_dir: str | Path) -> tuple[Path, Path]:
    """只允许测试目录；线上目录及其子/父目录一律拒绝。"""
    install = _norm(install_dir)
    data = _norm(data_dir)

    for live, label in ((LIVE_INSTALL_DIR, r"D:\LiuHao"), (LIVE_DATA_DIR, r"D:\LiuHaoData")):
        live = _norm(live)
        for target, kind in ((install, "安装目录"), (data, "数据目录")):
            if target == live or is_under(target, live) or is_under(live, target):
                raise UnsafeTargetError(
                    f"{kind} {target} 与线上 {label} 重叠；自装只允许测试目录，已拒绝。"
                )

    if not any(is_under(install, _norm(root)) for root in TEST_INSTALL_ROOTS):
        raise UnsafeTargetError(
            f"安装目录 {install} 不在白名单 {[str(p) for p in TEST_INSTALL_ROOTS]} 内，已拒绝。"
        )
    if not any(is_under(data, _norm(root)) for root in TEST_DATA_ROOTS):
        raise UnsafeTargetError(
            f"数据目录 {data} 不在白名单 {[str(p) for p in TEST_DATA_ROOTS]} 内，已拒绝。"
        )
    return install, data


def state_dir() -> Path:
    return STATE_ROOT / "broker"


def evidence_dir() -> Path:
    return STATE_ROOT / "evidence"


def rescue_dir() -> Path:
    return STATE_ROOT / "rescue"
