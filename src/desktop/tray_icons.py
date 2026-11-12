"""托盘状态图标解析：优先用外部资产，缺省退回手写圆点占位图。

换图接口（资产线只需把文件放到任一处，无需改代码）
----------------------------------------------------
1. 环境变量 ``LIUHAO_TRAY_ICON_DIR`` 指向的目录；
2. ``<DATA_DIR>/desktop/tray-icons``；
3. 包内 ``src/desktop/assets/tray``。

文件名约定：``<state>.ico`` 或 ``tray-<state>.ico``，尺寸建议做成多档
（16/20/24/32/48/64/256）的 ICO，Windows 会按 DPI 自动挑最合适的一档。

状态位（与 ``tray.STATE_*`` 一致）：``idle`` / ``working`` / ``waiting`` /
``error`` / ``stopped``。找不到资产时调用 :func:`placeholder` 生成圆点 ICO，
保证托盘在资产到位前也能用，且不同状态仍有颜色区分。
"""

from __future__ import annotations

import os
from pathlib import Path

#: 外部资产目录的环境变量名（资产线可据此注入测试/正式图标）。
ICON_DIR_ENV = "LIUHAO_TRAY_ICON_DIR"
#: 全部状态（与托盘状态位保持同一份清单）。
STATES: tuple[str, ...] = ("idle", "working", "waiting", "error", "stopped")


def candidate_dirs(data_dir: Path | str) -> tuple[Path, ...]:
    """按优先级返回可能的图标目录（不保证存在）。"""
    dirs: list[Path] = []
    override = (os.environ.get(ICON_DIR_ENV) or "").strip()
    if override:
        dirs.append(Path(override).expanduser())
    dirs.append(Path(data_dir) / "desktop" / "tray-icons")
    dirs.append(Path(__file__).resolve().parent / "assets" / "tray")
    return tuple(dirs)


def asset_for(state: str, data_dir: Path | str) -> Path | None:
    """查找状态对应的外部 ICO；没有返回 ``None``。"""
    for directory in candidate_dirs(data_dir):
        for name in (f"{state}.ico", f"tray-{state}.ico"):
            candidate = directory / name
            if candidate.is_file():
                return candidate
    return None


def placeholder(state: str, rgb: tuple[int, int, int], data_dir: Path | str) -> Path:
    """生成/复用圆点占位 ICO（只含颜色，不含任何用户数据）。"""
    from .tray import write_state_icon

    target = Path(data_dir) / "desktop" / "tray-icons" / f"placeholder-{state}.ico"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.is_file():
        write_state_icon(target, rgb)
    return target


def resolve(state: str, rgb: tuple[int, int, int], data_dir: Path | str) -> Path:
    """返回该状态最终使用的图标路径：外部资产优先，否则占位圆点。"""
    return asset_for(state, data_dir) or placeholder(state, rgb, data_dir)


__all__ = [
    "ICON_DIR_ENV",
    "STATES",
    "asset_for",
    "candidate_dirs",
    "placeholder",
    "resolve",
]
