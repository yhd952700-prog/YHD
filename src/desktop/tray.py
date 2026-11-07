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
import hashlib
import importlib
import os
import struct
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..brand import BRAND_LOCKUP

#: 菜单动作（对外稳定名称）。
ACTION_OPEN_CONSOLE = "open_console"
ACTION_SHOW_STATUS = "show_status"
ACTION_STOP_SERVICE = "stop_service"
ACTION_QUIT = "quit"

#: 托盘左键单击 / 双击的附加动作。
ACTION_TOGGLE_STATUS = "toggle_status"

#: 「打开日志目录」：排障时不必让用户自己去找数据目录。
ACTION_OPEN_LOGS = "open_logs"

#: 第二个桌面实例唤起已有窗口用的注册消息（跨进程，按类名找隐藏窗口）。
SHOW_MESSAGE_NAME = "LiuHao.ShowStatusWindow"

#: 托盘窗口类名前缀；完整类名由 :func:`tray_class_name` 按实例键派生。
TRAY_CLASS_PREFIX = "LiuHaoTray"

#: 菜单项顺序即产品要求：打开控制台 / 查看状态 / 打开日志目录 / 停止服务 / 退出。
#: 「停止服务」只停网关（托盘与窗口仍在）；「退出」才会停止网关并移除托盘 ——
#: 标签里写清楚，避免用户把"停止"当成"退出"。
MENU_ITEMS: tuple[tuple[int, str, str], ...] = (
    (1001, ACTION_OPEN_CONSOLE, "打开控制台"),
    (1002, ACTION_SHOW_STATUS, "查看状态"),
    (1005, ACTION_OPEN_LOGS, "打开日志目录"),
    (1003, ACTION_STOP_SERVICE, "停止服务（仅停网关，托盘保留）"),
    (1004, ACTION_QUIT, "退出（停止网关并关闭托盘）"),
)

#: 图标状态：运行中（绿）/ 有错误（红）/ 已停止（灰）。
STATE_RUNNING = "running"
STATE_ERROR = "error"
STATE_STOPPED = "stopped"
STATE_COLORS: dict[str, tuple[int, int, int]] = {
    STATE_RUNNING: (46, 160, 67),
    STATE_ERROR: (218, 54, 51),
    STATE_STOPPED: (120, 120, 120),
}

_TIP_MAX = 127
_ICON_SIZE = 16


def available() -> bool:
    """当前平台是否支持系统托盘（需要 Windows + pywin32）。"""
    return os.name == "nt"


def tray_class_name(instance_key: str) -> str:
    """按实例键派生**稳定**的托盘窗口类名。

    第二个桌面实例要靠它 ``FindWindow`` 找到已运行的那个托盘窗口。随机类名
    （旧实现）只能保证"不冲突"，做不到"找得到"，于是第二次双击只能报失败。
    ``instance_key`` 取数据目录：同一份数据 = 同一个实例，不同数据目录互不干扰。
    """
    digest = hashlib.sha1(str(instance_key).lower().encode("utf-8")).hexdigest()[:12]
    return f"{TRAY_CLASS_PREFIX}_{digest}"


def find_tray_window(instance_key: str) -> int:
    """查找已运行的托盘隐藏窗口；找不到返回 0。"""
    if not available():
        return 0
    try:
        _, _, win32gui = _load_win32()
        return int(win32gui.FindWindow(tray_class_name(instance_key), None) or 0)
    except Exception:  # noqa: BLE001 - 只在 Windows 且 pywin32 可用时有意义
        return 0


def request_show(instance_key: str, timeout: float = 5.0) -> bool:
    """请求已运行的托盘实例显示状态窗口；返回是否送达。

    ``timeout`` 用于覆盖启动竞态：两个实例几乎同时起来时，赢家可能还没来得及
    创建窗口，输家短暂等待后再放弃（放弃前由调用方退回"打开控制台"）。
    """
    if not available():
        return False
    try:
        _, _, win32gui = _load_win32()
        message = win32gui.RegisterWindowMessage(SHOW_MESSAGE_NAME)
    except Exception:  # noqa: BLE001
        return False
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        hwnd = find_tray_window(instance_key)
        if hwnd:
            try:
                win32gui.PostMessage(hwnd, message, 0, 0)
                return True
            except Exception:  # noqa: BLE001 - 窗口刚好退出
                return False
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)


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


def state_icon_path(state: str) -> Path:
    """状态图标文件落点（临时目录，跨进程复用；只含颜色，不含任何用户数据）。"""
    base = Path(tempfile.gettempdir()) / "liuhao-tray-icons"
    base.mkdir(parents=True, exist_ok=True)
    return base / f"liuhao-{state}.ico"


def write_state_icon(path: Path, rgb: tuple[int, int, int], size: int = _ICON_SIZE) -> Path:
    """手写一个 32bpp 圆点 ICO（绿=运行中 / 红=有错误 / 灰=已停止）。

    为什么不用 Pillow / 图片资源：托盘只依赖 pywin32，仓库不引入新依赖，也不
    想把二进制图标塞进仓库。ICO 是公开格式，这里按规范拼 ICONDIR + 32bpp
    DIB，Windows 的 ``LoadImage`` 可直接读取（含 alpha 通道，圆外透明）。
    """
    pixels = bytearray()
    for y in range(size - 1, -1, -1):  # ICO 的 XOR 位图自下而上
        for x in range(size):
            dx = x - (size - 1) / 2
            dy = y - (size - 1) / 2
            inside = dx * dx + dy * dy <= (size * 0.44) ** 2
            alpha = 255 if inside else 0
            pixels += bytes((rgb[2], rgb[1], rgb[0], alpha))
    mask = b"\x00" * (4 * size)  # 1bpp AND 掩码，全 0；透明由 alpha 决定
    bitmap_header = struct.pack(
        "<IiiHHIIiiII",
        40, size, size * 2, 1, 32, 0, len(pixels) + len(mask), 0, 0, 0, 0,
    )
    directory = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack(
        "<BBBBHHII", size, size, 0, 0, 1, 32, 40 + len(pixels) + len(mask), 22
    )
    path.write_bytes(directory + entry + bitmap_header + bytes(pixels) + mask)
    return path


class SystemTray:
    """一个只在 Windows 上真正工作的托盘图标。"""

    def __init__(
        self,
        on_action: Callable[[str], None],
        *,
        tooltip: str = BRAND_LOCKUP,
        instance_key: str | None = None,
    ) -> None:
        self._on_action = on_action
        self._tooltip = tooltip[:_TIP_MAX]
        #: 稳定的实例键：第二个桌面实例据此找到本窗口并请求显示状态窗口。
        self._instance_key = instance_key or ""
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._running = False
        self._hwnd: int = 0
        self._nid: tuple[Any, ...] | None = None
        self._w32: tuple[Any, Any, Any] | None = None
        self._message_id = 0
        self._show_message_id = 0
        self._class_name = ""
        self._icon_added = False
        self._error: str | None = None
        self.last_notify_error: str | None = None
        self._state = ""
        self._icon_cache: dict[str, Any] = {}

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

    def notify(self, title: str, message: str, *, timeout: float = 8.0, warning: bool = False) -> bool:
        """弹一次托盘气泡通知，返回是否成功提交给 Shell。

        只用于状态切换（服务已启动 / 已停止 / 已强制停止），不做业务通知。
        **是否真的显示由 Windows 通知设置决定**（专注助手、通知开关、通知
        历史策略都会吞掉气泡），代码层只能保证把 NIF_INFO 交给 Explorer。
        """
        if not self._icon_added or self._nid is None or self._w32 is None:
            return False
        _, _, win32gui = self._w32
        info_flags = win32gui.NIIF_WARNING if warning else win32gui.NIIF_INFO
        timeout_ms = int(max(1.0, timeout) * 1000)
        # pywin32 的 PyNOTIFYICONDATA 元组顺序（官方文档）：
        # (hWnd, ID, Flags, CallbackMessage, hIcon, Tip, Info, Timeout, InfoTitle, InfoFlags)
        nid = (
            self._nid[0],
            self._nid[1],
            self._nid[2] | win32gui.NIF_INFO,
            self._nid[3],
            self._nid[4],
            self._nid[5],
            message[:255],
            timeout_ms,
            title[:63],
            info_flags,
        )
        try:
            result = win32gui.Shell_NotifyIcon(win32gui.NIM_MODIFY, nid)
        except Exception as exc:  # noqa: BLE001
            self.last_notify_error = f"{type(exc).__name__}: {exc}"
            return False
        return result is None or bool(result)

    @property
    def state(self) -> str:
        return self._state

    def set_state(self, state: str) -> bool:
        """切换图标颜色：运行中（绿）/ 有错误（红）/ 已停止（灰）。

        失败（图标未添加、图标文件不可读）时只返回 False，不抛异常 ——
        状态图标是**辅助信息**，不能因为它把服务或消息循环拖垮。
        """
        if state not in STATE_COLORS:
            return False
        self._state = state
        if not self._icon_added or self._nid is None or self._w32 is None:
            return False
        _, win32con, win32gui = self._w32
        hicon = self._ensure_icon(win32gui, win32con, state)
        if not hicon:
            return False
        self._nid = self._nid[:4] + (hicon,) + self._nid[5:]
        try:
            result = win32gui.Shell_NotifyIcon(win32gui.NIM_MODIFY, self._nid)
        except Exception:  # noqa: BLE001
            return False
        return result is None or bool(result)

    def _ensure_icon(self, win32gui: Any, win32con: Any, state: str) -> Any:
        cached = self._icon_cache.get(state)
        if cached:
            return cached
        hicon = 0
        try:
            path = write_state_icon(state_icon_path(state), STATE_COLORS[state])
            hicon = win32gui.LoadImage(
                0, str(path), win32con.IMAGE_ICON, 0, 0,
                win32con.LR_LOADFROMFILE | win32con.LR_DEFAULTSIZE,
            )
        except Exception:  # noqa: BLE001 - 退回系统内置图标
            hicon = 0
        if not hicon:
            stock_name = {
                STATE_RUNNING: "IDI_APPLICATION",
                STATE_ERROR: "IDI_ERROR",
                STATE_STOPPED: "IDI_INFORMATION",
            }[state]
            hicon = win32gui.LoadIcon(0, getattr(win32con, stock_name, win32con.IDI_APPLICATION))
        if hicon:
            self._icon_cache[state] = hicon
        return hicon

    # ------------------------------------------------------------------ #
    # 消息循环（托盘线程）
    # ------------------------------------------------------------------ #
    def _run(self) -> None:
        try:
            win32api, win32con, win32gui = _load_win32()
            self._w32 = (win32api, win32con, win32gui)
            self._message_id = win32con.WM_USER + 20
            self._show_message_id = win32gui.RegisterWindowMessage(SHOW_MESSAGE_NAME)
            self._class_name = (
                tray_class_name(self._instance_key)
                if self._instance_key
                else f"LiuHaoTray_{uuid.uuid4().hex[:10]}"
            )

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
        elif self._show_message_id and msg == self._show_message_id:
            # 第二个桌面实例发来的「显示状态窗口」请求（等价于点菜单「查看状态」）。
            self._dispatch(ACTION_SHOW_STATUS)
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
    "ACTION_OPEN_LOGS",
    "ACTION_QUIT",
    "ACTION_SHOW_STATUS",
    "ACTION_STOP_SERVICE",
    "ACTION_TOGGLE_STATUS",
    "MENU_ITEMS",
    "SHOW_MESSAGE_NAME",
    "STATE_COLORS",
    "STATE_ERROR",
    "STATE_RUNNING",
    "STATE_STOPPED",
    "SystemTray",
    "available",
    "find_tray_window",
    "request_show",
    "tray_class_name",
    "write_state_icon",
]
