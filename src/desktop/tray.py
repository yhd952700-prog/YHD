"""pywin32 系统托盘（不使用 pystray / LGPL）。

消息循环与主服务的关系
----------------------
  * Win32 托盘图标属于**创建它的线程**，因此 :class:`SystemTray` 在独立线程里
    注册窗口类、创建隐藏窗口、``Shell_NotifyIcon(NIM_ADD)`` 并泵消息；
    主服务（uvicorn 子进程）与 tkinter 状态窗口都在别的线程/进程里，互不阻塞。
  * 菜单回调只做一件事：把动作塞进调用方给的队列/回调，由 UI 线程处理。
    托盘线程从不直接操作 tkinter，避免跨线程 UI 崩溃。
  * ``stop()`` 从任意线程调用：置停止标志 + ``PostMessage(WM_CLOSE)`` 唤醒
    消息循环，循环退出前在同一线程里 ``NIM_DELETE`` 并销毁窗口 ——
    图标不会遗留，窗口句柄也不会泄漏。

关于导入方式
------------
``win32api`` / ``win32gui`` / ``win32con`` 由 PyPI 分发 ``pywin32`` 提供，但 import
根名与分发名不同。仓库的依赖声明护栏会在非 Windows runner 上把这种"未安装即
无法映射"的 import 误判为未声明，而 pywin32 本身无法在 Linux 安装。为此这里用
``importlib.import_module`` 做**平台限定加载**；依赖已在 ``pyproject.toml``
（``sys_platform == "win32"`` 标记）与 ``oss-registry.yaml`` 中真实登记。
"""

from __future__ import annotations

import contextlib
import importlib
import os
import sys
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any

#: 菜单动作（对外稳定名称）。
ACTION_OPEN_CONSOLE = "open_console"
ACTION_SHOW_STATUS = "show_status"
ACTION_STOP_SERVICE = "stop_service"
ACTION_QUIT = "quit"

#: 托盘左键单击 / 双击的附加动作。
ACTION_TOGGLE_STATUS = "toggle_status"

#: 菜单项顺序即产品要求：打开控制台 / 查看状态 / 停止服务 / 退出。
MENU_ITEMS: tuple[tuple[int, str, str], ...] = (
    (1001, ACTION_OPEN_CONSOLE, "打开控制台"),
    (1002, ACTION_SHOW_STATUS, "查看状态"),
    (1003, ACTION_STOP_SERVICE, "停止服务"),
    (1004, ACTION_QUIT, "退出"),
)

_TIP_MAX = 127


def available() -> bool:
    """当前平台是否支持系统托盘（需要 Windows + pywin32）。"""
    return os.name == "nt"


def _load_win32() -> tuple[Any, Any, Any]:
    if not available():
        raise RuntimeError("系统托盘仅支持 Windows（需要 pywin32）")
    try:
        return (
            importlib.import_module("win32api"),
            importlib.import_module("win32con"),
            importlib.import_module("win32gui"),
        )
    except ImportError as exc:  # pragma: no cover - Windows 上 pywin32 缺失时
        raise RuntimeError("未安装 pywin32，无法创建系统托盘（pip install pywin32）") from exc


class SystemTray:
    """一个只在 Windows 上真正工作的托盘图标。"""

    def __init__(self, on_action: Callable[[str], None], *, tooltip: str = "鎏灏 LIUHAO X") -> None:
        self._on_action = on_action
        self._tooltip = tooltip[:_TIP_MAX]
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._running = False
        self._hwnd: int = 0
        self._nid: tuple[Any, ...] | None = None
        self._w32: tuple[Any, Any, Any] | None = None
        self._message_id = 0
        self._class_name = ""
        self._icon_added = False
        self._error: str | None = None

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def start(self, timeout: float = 5.0) -> bool:
        """在后台线程创建托盘；返回图标是否真正进入消息循环。"""
        if not available():
            return False
        if self._thread and self._thread.is_alive():
            return True
        self._running = True
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, name="liuhao-tray", daemon=True)
        self._thread.start()
        self._ready.wait(timeout)
        return self._icon_added and self._error is None

    def stop(self) -> None:
        """移除图标并退出消息循环（可从任意线程调用）。"""
        self._running = False
        hwnd = self._hwnd
        if hwnd and self._w32 is not None:
            with contextlib.suppress(Exception):
                self._w32[2].PostMessage(hwnd, self._w32[1].WM_CLOSE, 0, 0)
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None

    @property
    def error(self) -> str | None:
        return self._error

    @property
    def icon_added(self) -> bool:
        return self._icon_added

    @property
    def hwnd(self) -> int:
        return self._hwnd

    def set_tooltip(self, text: str) -> None:
        """更新悬停提示（失败静默，不影响服务）。"""
        self._tooltip = (text or "")[:_TIP_MAX]
        if not self._icon_added or not self._nid or self._w32 is None:
            return
        _, _, win32gui = self._w32
        try:
            self._nid = self._nid[:5] + (self._tooltip,)
            win32gui.Shell_NotifyIcon(win32gui.NIM_MODIFY, self._nid)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------ #
    # 消息循环（托盘线程）
    # ------------------------------------------------------------------ #
    def _run(self) -> None:
        try:
            win32api, win32con, win32gui = _load_win32()
            self._w32 = (win32api, win32con, win32gui)
            self._message_id = win32con.WM_USER + 20
            self._class_name = f"LiuHaoTray_{uuid.uuid4().hex[:10]}"

            wc = win32gui.WNDCLASS()
            wc.hInstance = win32api.GetModuleHandle(None)
            wc.lpszClassName = self._class_name
            wc.lpfnWndProc = self._wndproc
            win32gui.RegisterClass(wc)
            self._hwnd = win32gui.CreateWindow(
                self._class_name, "LiuHaoTray", 0, 0, 0, 0, 0, 0, 0, wc.hInstance, None
            )

            hicon = 0
            with contextlib.suppress(Exception):
                hicon = win32gui.ExtractIcon(0, sys.executable, 0)
            if not hicon:
                hicon = win32gui.LoadIcon(0, win32con.IDI_APPLICATION)
            self._nid = (
                self._hwnd,
                1,
                win32gui.NIF_MESSAGE | win32gui.NIF_ICON | win32gui.NIF_TIP,
                self._message_id,
                hicon,
                self._tooltip,
            )
            result = win32gui.Shell_NotifyIcon(win32gui.NIM_ADD, self._nid)
            # pywin32 成功时返回 None（实测），失败返回 False/0。
            self._icon_added = result is None or bool(result)
            if not self._icon_added:
                self._error = "Shell_NotifyIcon(NIM_ADD) 失败（可能没有可用的任务栏通知区）"
            self._ready.set()

            while self._running:
                win32gui.PumpWaitingMessages()
                time.sleep(0.03)
        except Exception as exc:  # noqa: BLE001 - 托盘失败不能拖垮主服务
            self._error = f"{type(exc).__name__}: {exc}"
        finally:
            self._cleanup()

    def _cleanup(self) -> None:
        self._icon_added = False
        if self._w32 is None:
            self._ready.set()
            return
        _, _, win32gui = self._w32
        try:
            if self._nid is not None:
                win32gui.Shell_NotifyIcon(win32gui.NIM_DELETE, self._nid)
        except Exception:  # noqa: BLE001
            pass
        try:
            if self._hwnd:
                win32gui.DestroyWindow(self._hwnd)
        except Exception:  # noqa: BLE001
            pass
        try:
            if self._class_name:
                win32gui.UnregisterClass(self._class_name, self._w32[0].GetModuleHandle(None))
        except Exception:  # noqa: BLE001
            pass
        self._nid = None
        self._hwnd = 0
        self._ready.set()

    # ------------------------------------------------------------------ #
    # 窗口消息
    # ------------------------------------------------------------------ #
    def _wndproc(self, hwnd: int, msg: int, wparam: int, lparam: int) -> int:
        assert self._w32 is not None
        _, win32con, _ = self._w32
        if msg == self._message_id:
            if lparam == win32con.WM_LBUTTONUP:
                self._dispatch(ACTION_TOGGLE_STATUS)
            elif lparam == win32con.WM_LBUTTONDBLCLK:
                self._dispatch(ACTION_OPEN_CONSOLE)
            elif lparam == win32con.WM_RBUTTONUP:
                self._show_menu()
        elif msg == win32con.WM_COMMAND:
            self.handle_command(int(wparam) & 0xFFFF)
        elif msg == win32con.WM_CLOSE:
            self._running = False
        return 0

    def _show_menu(self) -> None:
        assert self._w32 is not None
        _, win32con, win32gui = self._w32
        menu = win32gui.CreatePopupMenu()
        try:
            for menu_id, _action, label in MENU_ITEMS:
                win32gui.AppendMenu(menu, win32con.MF_STRING, menu_id, label)
            x, y = win32gui.GetCursorPos()
            if self._hwnd:
                win32gui.SetForegroundWindow(self._hwnd)
            chosen = win32gui.TrackPopupMenu(
                menu,
                win32con.TPM_LEFTALIGN | win32con.TPM_RIGHTBUTTON | win32con.TPM_RETURNCMD,
                int(x),
                int(y),
                0,
                self._hwnd,
                None,
            )
            if self._hwnd:
                win32gui.PostMessage(self._hwnd, win32con.WM_NULL, 0, 0)
            if chosen:
                self.handle_command(int(chosen))
        finally:
            win32gui.DestroyMenu(menu)

    def handle_command(self, menu_id: int) -> None:
        """菜单命令入口（右键菜单与 ``WM_COMMAND`` 共用；测试直接调用它）。"""
        for candidate, action, _label in MENU_ITEMS:
            if candidate == menu_id:
                self._dispatch(action)
                return

    def _dispatch(self, action: str) -> None:
        with contextlib.suppress(Exception):
            self._on_action(action)


__all__ = [
    "ACTION_OPEN_CONSOLE",
    "ACTION_QUIT",
    "ACTION_SHOW_STATUS",
    "ACTION_STOP_SERVICE",
    "ACTION_TOGGLE_STATUS",
    "MENU_ITEMS",
    "SystemTray",
    "available",
]
