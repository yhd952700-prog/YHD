"""Windows 开机自启登记（仅 HKCU，不需要管理员）。

约束与实际行为：
  * 只写 ``HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run``，
    **绝不**触碰 ``HKLM`` —— 因此不需要管理员权限，也不会影响其他用户。
  * 默认**关闭**；必须由用户在设置里显式开启（``enable``）。
  * 提供"自启但不自动打开浏览器"的默认行为（``open_browser=False``）；
    需要开机直接弹浏览器时才传 ``open_browser=True``。
  * 写入的命令使用 ``pythonw.exe``（无控制台窗口），并带 ``--autostart``，
    由 ``scripts/liuhao_desktop.py`` 装配桌面壳。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from . import REPO_ROOT

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "LiuHaoAIOS"
DESKTOP_ENTRY = Path("scripts") / "liuhao_desktop.py"
PYTHONW = Path(".venv") / "Scripts" / "pythonw.exe"


def supported() -> bool:
    """当前平台是否支持 HKCU Run 登记。"""
    return os.name == "nt"


def _pythonw(root: Path) -> str:
    candidate = root / PYTHONW
    if candidate.is_file():
        return str(candidate)
    return "pythonw.exe" if os.name == "nt" else "python"


def build_command(root: Path | None = None, *, open_browser: bool = False) -> str:
    """构造写入注册表的完整命令行（Windows 参数引用规则）。"""
    base = Path(root or REPO_ROOT)
    args = [str(_pythonw(base)), str(base / DESKTOP_ENTRY), "--autostart"]
    if open_browser:
        args.append("--open-browser")
    return subprocess.list2cmdline(args)


def open_browser_enabled(command: str) -> bool:
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
    open_browser: bool = False,
    value_name: str = VALUE_NAME,
) -> dict[str, Any]:
    """开启自启（默认"自启但不打开浏览器"）。返回写入后的读取结果。"""
    if not supported():
        raise RuntimeError("开机自启仅支持 Windows（HKCU Run）")
    import winreg

    command = build_command(root, open_browser=open_browser)
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, value_name, 0, winreg.REG_SZ, command)
    result = read(value_name)
    result["command"] = command
    return result


def disable(value_name: str = VALUE_NAME) -> dict[str, Any]:
    """关闭自启；不存在时视为已关闭（幂等）。"""
    if not supported():
        return {"supported": False, "enabled": False, "value_name": value_name}
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, value_name)
    except FileNotFoundError:
        pass
    return read(value_name)


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
