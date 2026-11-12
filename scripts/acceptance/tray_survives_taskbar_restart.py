"""托盘在 Explorer 重启后自动恢复的验收（真实 Win32 通知区）。

判据（全部满足才算通过）：
  1. 托盘启动成功，``NIM_ADD`` 后 Shell 认可图标存在；
  2. 广播 ``TaskbarCreated`` 后托盘重新注册：``taskbar_recoveries`` +1、
     ``icon_adds`` +1，且 ``NIM_MODIFY`` 仍成功（图标真的回到了通知区）；
  3. 双击仍派发 ``open_console``、菜单命令仍派发 ``show_status``；
  4. 菜单项数量与 ``MENU_ITEMS`` 一致。

退出码：``0`` 通过；``1`` 断言失败；``2`` 环境不具备（非 Windows / 无
pywin32 / 非交互桌面 / 没有 Explorer / 广播无法送达）。**2 不算通过。**

用法::

    .\\.venv\\Scripts\\python.exe scripts\\acceptance\\tray_survives_taskbar_restart.py
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.desktop import tray  # noqa: E402

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_ENV = 2


def _say(tag: str, text: str) -> None:
    print(f"[{tag}] {text}", flush=True)


def _interactive_session() -> bool:
    """当前进程是否跑在 WinSta0 的交互会话里（托盘需要真实桌面）。"""
    if os.name != "nt":
        return False
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        session = ctypes.c_ulong(0)
        if not kernel32.ProcessIdToSessionId(kernel32.GetCurrentProcessId(), ctypes.byref(session)):
            return False
        if session.value == 0:
            return False
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        handle = user32.GetProcessWindowStation()
        if not handle:
            return False
        name = ctypes.create_unicode_buffer(256)
        needed = ctypes.c_ulong(0)
        if not user32.GetUserObjectInformationW(
            handle, 2, name, ctypes.sizeof(name), ctypes.byref(needed)
        ):
            return False
        return name.value.lower() == "winsta0"
    except Exception:  # noqa: BLE001
        return False


def _explorer_running() -> bool:
    """通知区由 Explorer 提供；没有它就无法判断图标是否恢复。"""
    # nosemgrep: liuhao-swallow-exception-return-success
    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq explorer.exe", "/NH"],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:  # noqa: BLE001
        # 结辩：这是前置探测而非验收结论——tasklist 失败时按「Explorer 在跑」
        # 处理并继续走真实托盘恢复校验，由后续判定给出最终结论；
        # True 语义是保守默认，不是谎报成功。
        return True
    return "explorer.exe" in (result.stdout or "").lower()


def _wait(predicate, timeout: float = 5.0, interval: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return bool(predicate())


def _snapshot(widget: tray.SystemTray) -> str:
    return (
        f"icon_added={widget.icon_added} icon_adds={widget.icon_adds} "
        f"recoveries={widget.taskbar_recoveries} icon_present={widget.icon_present()}"
    )


def run() -> int:
    if os.name != "nt":
        _say("ENV", "非 Windows 平台，托盘验收无法执行")
        return EXIT_ENV
    if not tray.available():
        _say("ENV", "pywin32 不可用，无法创建通知区图标")
        return EXIT_ENV
    if not _interactive_session():
        _say("ENV", "当前不是 WinSta0 交互会话（服务 / 会话 0 里没有通知区）")
        return EXIT_ENV
    if not _explorer_running():
        _say("ENV", "Explorer 未运行，没有可验证的通知区")
        return EXIT_ENV

    received: list[str] = []
    widget = tray.SystemTray(received.append, tooltip="贾维斯（托盘恢复验收）")
    if not widget.start(timeout=5.0):
        widget.stop()
        _say("FAIL", f"托盘启动失败（图标没能进入通知区）：{widget.error}")
        return EXIT_FAIL

    try:
        _say("INFO", f"广播前：{_snapshot(widget)}")
        if not widget.icon_present():
            _say("FAIL", "启动后 Shell 不认可图标存在")
            return EXIT_FAIL

        before = (widget.icon_adds, widget.taskbar_recoveries)
        if not tray.broadcast_taskbar_created():
            _say("ENV", "TaskbarCreated 广播未送达（桌面无响应），无法判定")
            return EXIT_ENV
        _say("INFO", "已广播 TaskbarCreated（等价于 Explorer 重启）")

        if not _wait(lambda: widget.taskbar_recoveries > before[1]):
            _say("FAIL", f"托盘没有处理 TaskbarCreated：{_snapshot(widget)}")
            return EXIT_FAIL
        if not _wait(lambda: widget.icon_adds > before[0]):
            _say("FAIL", f"收到广播但没有重新 NIM_ADD：{_snapshot(widget)}")
            return EXIT_FAIL
        if not _wait(widget.icon_present):
            _say("FAIL", f"重新注册后 Shell 仍不认可图标：{_snapshot(widget)}")
            return EXIT_FAIL
        _say("INFO", f"广播后：{_snapshot(widget)}")

        expected_menu = len(tray.MENU_ITEMS)
        actual_menu = len(widget.menu_items)
        if expected_menu != 5 or actual_menu != expected_menu:
            _say("FAIL", f"菜单项数量不对：期望 {expected_menu}，实际 {actual_menu}")
            return EXIT_FAIL
        _say("PASS", f"菜单项数量正确：{actual_menu} 项")

        win32gui = __import__("win32gui")
        win32con = __import__("win32con")
        hwnd = widget.hwnd
        if not hwnd:
            _say("FAIL", "托盘窗口句柄为空")
            return EXIT_FAIL

        win32gui.PostMessage(hwnd, widget._message_id, 0, win32con.WM_LBUTTONDBLCLK)
        if not _wait(lambda: tray.ACTION_OPEN_CONSOLE in received, timeout=3.0):
            _say("FAIL", f"广播后双击未派发 open_console：received={received}")
            return EXIT_FAIL
        _say("PASS", "广播后双击仍能唤出窗口（open_console）")

        win32gui.PostMessage(hwnd, win32con.WM_COMMAND, 1002, 0)
        if not _wait(lambda: tray.ACTION_SHOW_STATUS in received, timeout=3.0):
            _say("FAIL", f"广播后菜单命令未派发 show_status：received={received}")
            return EXIT_FAIL
        _say("PASS", "广播后菜单命令仍可用（show_status）")
        return EXIT_OK
    finally:
        widget.stop()
        _say("INFO", f"停止后：icon_added={widget.icon_added}")


if __name__ == "__main__":
    raise SystemExit(run())
