"""Windows 开机自启登记（仅 HKCU，不需要管理员）。

约束与实际行为：
  * 只写 ``HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run``，
    **绝不**触碰 ``HKLM`` —— 因此不需要管理员权限，也不会影响其他用户。
  * 默认**关闭**；必须由用户在设置里显式开启（``enable``）。
  * 自启不打扰用户：只驻留托盘、不弹窗口、不自动开浏览器；入口的
    ``--open-browser`` 是历史参数，不再写入（``read`` 仍能识别旧注册项）。
  * 写入的命令使用 ``pythonw.exe``（无控制台窗口）。解释器按布局解析：
    开发目录用虚拟环境里的 ``pythonw.exe``；安装目录用
    ``runtime\\pythonw.exe``（安装包没有虚拟环境）；都没有时退回当前解释器
    同目录的 pythonw。
  * 入口优先 ``scripts/liuhao_desktop.py``（带 ``--autostart``，起托盘，走
    启动门）；安装包载荷已收录该脚本，所以安装版自启同样是托盘链路。
    只有该脚本确实不存在（旧载荷）时才退回 ``scripts/launch_installed.py``
    （控制台启动器，安装版首次运行向导也由它负责）。
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, List

from . import REPO_ROOT

logger = logging.getLogger(__name__)

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "LiuHaoAIOS"
DESKTOP_ENTRY = Path("scripts") / "liuhao_desktop.py"
INSTALLED_ENTRY = Path("scripts") / "launch_installed.py"
PYTHONW = Path(".venv") / "Scripts" / "pythonw.exe"
INSTALLED_PYTHONW = Path("runtime") / "pythonw.exe"


def supported() -> bool:
    """当前平台是否支持 HKCU Run 登记。"""
    return os.name == "nt"


def _pythonw(root: Path) -> str:
    for candidate in (root / PYTHONW, root / INSTALLED_PYTHONW):
        if candidate.is_file():
            return str(candidate)
    current = Path(sys.executable)
    sibling = current.with_name("pythonw.exe")
    if os.name == "nt" and sibling.is_file():
        return str(sibling)
    return "pythonw.exe" if os.name == "nt" else "python"


def _entry(root: Path) -> Path:
    desktop = root / DESKTOP_ENTRY
    if desktop.is_file():
        return desktop
    installed = root / INSTALLED_ENTRY
    if installed.is_file():
        return installed
    raise RuntimeError(
        "找不到桌面入口：源码目录需要 scripts/liuhao_desktop.py，"
        "安装目录需要 scripts/launch_installed.py；请重装或从完整目录登记自启。"
    )


def build_command(root: Path | None = None) -> str:
    """构造写入注册表的完整命令行（Windows 参数引用规则）。

    自启恒为「静默驻留托盘、原生窗口保持隐藏」：桌面入口只写 ``--autostart``，
    安装入口只写 ``--no-browser``。``--open-browser`` 不再写入 —— 入口已不据它
    改变行为，留着只会让人误以为存在「网页模式自启」。
    """
    base = Path(root or REPO_ROOT)
    entry = _entry(base)
    args = [str(_pythonw(base)), str(entry)]
    if entry.name == DESKTOP_ENTRY.name:
        args.append("--autostart")
    else:
        # 安装入口默认只启动鎏灏窗口；自启场景保持不打扰用户。
        args.append("--no-browser")
    return subprocess.list2cmdline(args)


def open_browser_enabled(command: str) -> bool:
    """旧注册项是否带 ``--open-browser``（只读识别；新命令不再写入）。"""
    return "--open-browser" in (command or "")


def read(value_name: str = VALUE_NAME) -> dict[str, Any]:
    """读取当前登记（只读，不需要管理员）。"""
    if not supported():
        return {"supported": False, "enabled": False, "command": None, "value_name": value_name, "run_key": RUN_KEY}
    import winreg  # 仅 Windows 存在

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            command, _kind = winreg.QueryValueEx(key, value_name)
    except FileNotFoundError:
        return {"supported": True, "enabled": False, "command": None, "value_name": value_name, "run_key": RUN_KEY}
    except OSError as exc:
        return {
            "supported": True,
            "enabled": False,
            "command": None,
            "value_name": value_name,
            "run_key": RUN_KEY,
            "error": f"{type(exc).__name__}: {exc}",
        }
    return {
        "supported": True,
        "enabled": True,
        "command": command,
        "open_browser": open_browser_enabled(command),
        "value_name": value_name,
        "run_key": RUN_KEY,
    }


def enable(
    root: Path | None = None,
    *,
    value_name: str = VALUE_NAME,
) -> dict[str, Any]:
    """开启自启（静默驻留托盘，不打开浏览器）。返回写入后的读取结果。"""
    if not supported():
        raise RuntimeError("开机自启仅支持 Windows（HKCU Run）")
    import winreg

    command = build_command(root)
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, value_name, 0, winreg.REG_SZ, command)
    result = read(value_name)
    result["command"] = command
    return result


def disable(
    value_name: str = VALUE_NAME,
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    """关闭自启；不存在时视为已关闭（幂等）。

    ``root`` 非空时，**除默认值名外**还会删掉 HKCU Run 里所有「命令行指向该安装
    根」的值：旧版安装器/手工登记可能留下别的值名（或便携版留下的绝对路径），
    只删默认值名会让卸载后开机仍拉起一份旧副本。
    """
    if not supported():
        return {"supported": False, "enabled": False, "value_name": value_name}
    import winreg

    removed: List[str] = []
    try:
        # 必须同时要 KEY_QUERY_VALUE：只给 KEY_SET_VALUE 时 EnumValue 会报
        # 「拒绝访问」，按安装根批量清理会静默失效（旧注册项留在机器上）。
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            RUN_KEY,
            0,
            winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE,
        ) as key:
            try:
                winreg.DeleteValue(key, value_name)
                removed.append(value_name)
            except FileNotFoundError:
                pass
            if root is not None:
                removed.extend(_delete_values_pointing_to(key, winreg, Path(root)))
    except FileNotFoundError:
        pass
    result = read(value_name)
    result["removed_values"] = removed
    return result


def _delete_values_pointing_to(key: Any, winreg: Any, root: Path) -> List[str]:
    """删掉命令里含安装根绝对路径的 Run 值，返回被删值名。"""
    needle = str(root).replace("/", "\\").lower()
    removed: List[str] = []
    index = 0
    while True:
        try:
            name, value, _kind = winreg.EnumValue(key, index)
        except OSError as exc:
            # WinError 259 = 没有更多数据，是正常读完；其余错误（拒绝访问、
            # 句柄失效等）表示枚举提前中断，后面的旧注册项会漏删。
            if getattr(exc, "winerror", None) != 259:
                logger.warning(
                    "枚举开机自启项在第 %d 项中断（%s）：批量清理可能漏删指向 %s 的旧注册项",
                    index,
                    exc,
                    root,
                )
            break
        if isinstance(value, str) and needle and needle in value.lower():
            try:
                winreg.DeleteValue(key, name)
                removed.append(name)
                continue  # 删掉后同下标的项会前移，别自增
            except OSError as exc:
                logger.warning(
                    "删除开机自启项 %s 失败（%s）：该值仍指向 %s，卸载后可能仍会自启旧副本",
                    name,
                    exc,
                    root,
                )
        index += 1
    return removed


__all__ = [
    "RUN_KEY",
    "VALUE_NAME",
    "build_command",
    "disable",
    "enable",
    "open_browser_enabled",
    "read",
    "supported",
]
