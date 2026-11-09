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
#: 「在浏览器中打开」：网页模式的唯一桌面入口（默认启动路径不会自动打开浏览器）。
ACTION_OPEN_BROWSER = "open_browser"
ACTION_SHOW_STATUS = "show_status"
#: 「重新启动服务」：网关死掉后不必"退出再重开"，原地重启；启动中置灰防连点。
ACTION_RESTART_SERVICE = "restart_service"
ACTION_STOP_SERVICE = "stop_service"
ACTION_QUIT = "quit"

#: 托盘左键单击 / 双击的附加动作。
ACTION_TOGGLE_STATUS = "toggle_status"

#: 「打开日志目录」：排障时不必让用户自己去找数据目录。
ACTION_OPEN_LOGS = "open_logs"

#: 第二个桌面实例唤起已有窗口用的注册消息（跨进程，按类名找隐藏窗口）。
SHOW_MESSAGE_NAME = "LiuHao.ShowStatusWindow"

#: Explorer 重启后由 Shell 向所有顶层窗口广播的消息名。
#: 图标属于 Explorer 的通知区，外壳重启会把它一起清掉；不处理这条消息，
#: 用户看到的就是"程序还在、图标没了"，只能去任务管理器找进程。
TASKBAR_CREATED_NAME = "TaskbarCreated"

#: 图标没加成功时的重试定时器（在托盘线程的隐藏窗口上）。
_ADD_TIMER_ID = 1
_RETRY_INITIAL_MS = 1000
_RETRY_MAX_MS = 30000

#: 托盘窗口类名前缀；完整类名由 :func:`tray_class_name` 按实例键派生。
TRAY_CLASS_PREFIX = "LiuHaoTray"

#: 菜单文案的唯一定义处。状态窗口的按钮直接复用这些常量：同一个功能在托盘
#: 与界面里必须写同一句话，改一处两边同时生效，不会再出现"停服务"和"停止
#: 服务（仅停网关）"两种说法。
LABEL_OPEN_CONSOLE = "打开鎏灏窗口"
LABEL_OPEN_BROWSER = "在浏览器中打开"
LABEL_SHOW_STATUS = "查看状态"
LABEL_RESTART_SERVICE = "重新启动服务"
LABEL_OPEN_LOGS = "打开日志目录"
LABEL_STOP_SERVICE = "停止服务（仅停网关，托盘保留）"
LABEL_QUIT = "退出（停止网关并关闭托盘）"

#: 菜单项顺序即产品要求：打开窗口 / 查看状态 / 在浏览器中打开 / 打开日志目录 /
#: 停止服务 / 退出。「停止服务」只停网关（托盘与窗口仍在）；「退出」才会停止
#: 网关并移除托盘 —— 标签里写清楚，避免用户把"停止"当成"退出"。
#: 「在浏览器中打开」是网页模式的入口；默认启动不会自动打开浏览器。
MENU_ITEMS: tuple[tuple[int, str, str], ...] = (
    (1001, ACTION_OPEN_CONSOLE, LABEL_OPEN_CONSOLE),
    (1002, ACTION_SHOW_STATUS, LABEL_SHOW_STATUS),
    (1007, ACTION_RESTART_SERVICE, LABEL_RESTART_SERVICE),
    (1006, ACTION_OPEN_BROWSER, LABEL_OPEN_BROWSER),
    (1005, ACTION_OPEN_LOGS, LABEL_OPEN_LOGS),
    (1003, ACTION_STOP_SERVICE, LABEL_STOP_SERVICE),
    (1004, ACTION_QUIT, LABEL_QUIT),
)


def menu_items_for(*, include_browser: bool) -> tuple[tuple[int, str, str], ...]:
    """按窗口形态裁剪菜单项。

    原生窗口不出现「在浏览器中打开」——桌面端不做浏览器自降级；只有浏览器
    模式（``--web``）才保留，因为它本身就是网页入口。
    """
    if include_browser:
        return MENU_ITEMS
    return tuple(item for item in MENU_ITEMS if item[1] != ACTION_OPEN_BROWSER)


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
#: 托盘图标要覆盖的尺寸：100/125/150/200% 分别对应 16/20/24/32，再补
#: 任务管理器、Alt+Tab 等场景会用到的大图。Windows 从 ICO 里挑最合适的一档，
#: 缺档时只能拿 16×16 放大，显示发虚。
_ICON_SIZES: tuple[int, ...] = (16, 20, 24, 32, 48, 64, 256)


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


def broadcast_taskbar_created(timeout_ms: int = 5000) -> bool:
    """模拟 Explorer 重启：向所有顶层窗口广播 ``TaskbarCreated``。

    真实 Explorer 重启时发的就是这条消息，所以验收自动化可以走同一条路径，
    不必真的杀掉桌面外壳。返回是否送达（``SendMessageTimeout`` 的返回值）；
    非 Windows 或无桌面会话时返回 False。
    """
    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
        user32.RegisterWindowMessageW.restype = wintypes.UINT
        user32.SendMessageTimeoutW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
            wintypes.UINT,
            wintypes.UINT,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        user32.SendMessageTimeoutW.restype = ctypes.c_size_t
        message = user32.RegisterWindowMessageW(TASKBAR_CREATED_NAME)
        if not message:
            return False
        result = ctypes.c_size_t(0)
        sent = user32.SendMessageTimeoutW(
            wintypes.HWND(0xFFFF),  # HWND_BROADCAST
            message,
            0,
            0,
            0x0002,  # SMTO_ABORTIFHUNG
            int(timeout_ms),
            ctypes.byref(result),
        )
        return bool(sent)
    except Exception:  # noqa: BLE001 - 验收工具不能因为广播失败抛异常
        return False


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


def _small_icon_size() -> int:
    """通知区小图标的实际像素尺寸（``SM_CXSMICON``：100%/150%/200% → 16/24/32）。

    进程已声明 Per-Monitor DPI 感知，这里拿到的是当前显示器的真实尺寸，正好
    对应多尺寸 ICO 里的一档。拿不到时返回 0，由调用方退回 ``LR_DEFAULTSIZE``。
    """
    if os.name != "nt":
        return 0
    try:
        import ctypes

        value = int(ctypes.windll.user32.GetSystemMetrics(49))  # SM_CXSMICON
        return value if value > 0 else 0
    except Exception:  # noqa: BLE001 - 只影响图标清晰度，不能阻断托盘
        return 0


def _icon_image(size: int, rgb: tuple[int, int, int]) -> bytes:
    """单个尺寸的 32bpp DIB（BITMAPINFOHEADER + XOR 位图 + AND 掩码）。"""
    pixels = bytearray()
    for y in range(size - 1, -1, -1):  # ICO 的 XOR 位图自下而上
        for x in range(size):
            dx = x - (size - 1) / 2
            dy = y - (size - 1) / 2
            inside = dx * dx + dy * dy <= (size * 0.44) ** 2
            alpha = 255 if inside else 0
            pixels += bytes((rgb[2], rgb[1], rgb[0], alpha))
    # 1bpp AND 掩码：每行按 4 字节对齐（48/64/256 下不能直接用 4*size）。
    mask_row = ((size + 31) // 32) * 4
    mask = b"\x00" * (mask_row * size)
    bitmap_header = struct.pack(
        "<IiiHHIIiiII",
        40, size, size * 2, 1, 32, 0, len(pixels) + len(mask), 0, 0, 0, 0,
    )
    return bitmap_header + bytes(pixels) + mask


def write_state_icon(
    path: Path,
    rgb: tuple[int, int, int],
    sizes: tuple[int, ...] = _ICON_SIZES,
) -> Path:
    """手写一个 32bpp 圆点 ICO（绿=运行中 / 红=有错误 / 灰=已停止）。

    为什么不用 Pillow / 图片资源：托盘只依赖 pywin32，仓库不引入新依赖，也不
    想把二进制图标塞进仓库。ICO 是公开格式，这里按规范拼 ICONDIR + 多尺寸
    32bpp DIB，Windows 的 ``LoadImage`` 可直接读取（含 alpha 通道，圆外透明）。
    多尺寸是必须的：只有 16×16 时，150%/200% 缩放会被系统放大成模糊色块。
    """
    images = [(size, _icon_image(size, rgb)) for size in sizes]
    offset = 6 + 16 * len(images)
    directory = struct.pack("<HHH", 0, 1, len(images))
    entries = bytearray()
    body = bytearray()
    for size, image in images:
        dimension = 0 if size >= 256 else size  # ICO 用 0 表示 256
        entries += struct.pack(
            "<BBBBHHII", dimension, dimension, 0, 0, 1, 32, len(image), offset
        )
        offset += len(image)
        body += image
    path.write_bytes(directory + bytes(entries) + bytes(body))
    return path


class SystemTray:
    """一个只在 Windows 上真正工作的托盘图标。"""

    def __init__(
        self,
        on_action: Callable[[str], None],
        *,
        tooltip: str = BRAND_LOCKUP,
        instance_key: str | None = None,
        menu_items: tuple[tuple[int, str, str], ...] | None = None,
    ) -> None:
        self._on_action = on_action
        self._menu_items = MENU_ITEMS if menu_items is None else tuple(menu_items)
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
        self._taskbar_created_id = 0
        self._class_name = ""
        self._icon_added = False
        self._icon_adds = 0
        self._taskbar_recoveries = 0
        self._retry_delay_ms = _RETRY_INITIAL_MS
        self._ready_deadline = 0.0
        self._error: str | None = None
        self.last_notify_error: str | None = None
        self._state = ""
        self._icon_cache: dict[str, Any] = {}
        #: 置灰动作（启动/停止进行中时「重新启动服务」不可点）；从 UI 线程写、
        #: 托盘线程读，用短锁保护。
        self._disabled_actions: set[str] = set()
        self._menu_lock = threading.Lock()

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
        self._ready_deadline = time.monotonic() + max(0.0, timeout)
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
    def icon_adds(self) -> int:
        """``NIM_ADD`` 成功次数（首次注册 + Explorer 重启后的重建）。"""
        return self._icon_adds

    @property
    def taskbar_recoveries(self) -> int:
        """收到 ``TaskbarCreated`` 广播的次数（验收脚本据此判断是否走了恢复分支）。"""
        return self._taskbar_recoveries

    @property
    def menu_items(self) -> tuple[tuple[int, str, str], ...]:
        """右键菜单内容（重建后仍读这一份，验收断言项数用）。"""
        return self._menu_items

    def icon_present(self) -> bool:
        """Shell 是否仍持有本图标：``NIM_MODIFY`` 成功即说明通知区里有它。

        验收脚本用它判断"广播后图标真的回来了"，而不是只看内部布尔标志。
        """
        if self._w32 is None or self._nid is None or not self._icon_added:
            return False
        _, _, win32gui = self._w32
        try:
            result = win32gui.Shell_NotifyIcon(win32gui.NIM_MODIFY, self._nid)
        except Exception:  # noqa: BLE001
            return False
        return result is None or bool(result)

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
            # 明确要通知区的小图标尺寸（SM_CXSMICON，随 DPI 是 16/24/32…），
            # 让 Windows 从多尺寸 ICO 里挑**正好匹配**的那一档；用
            # LR_DEFAULTSIZE 会固定拿 SM_CXICON（32）再由外壳缩到 16/24，
            # 150% 下就还是糊的。
            size = _small_icon_size()
            hicon = win32gui.LoadImage(
                0,
                str(path),
                win32con.IMAGE_ICON,
                size,
                size,
                win32con.LR_LOADFROMFILE
                | (0 if size else win32con.LR_DEFAULTSIZE),
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
    # 图标注册与重建（托盘线程）
    # ------------------------------------------------------------------ #
    def _add_icon(self) -> bool:
        """把当前 ``_nid`` 注册进通知区；成功返回 True。

        先 ``NIM_DELETE`` 再 ``NIM_ADD``：Explorer 重启后旧注册已随外壳消失，
        DELETE 会失败（无害）；而手工广播 / 外壳仍在时，DELETE 保证不会因为
        重复 ADD 出现第二个图标。提示文字与图标句柄都在 ``_nid`` 里，ADD
        成功后若有状态色再 ``NIM_MODIFY`` 一次覆盖回去。
        """
        if self._w32 is None or self._nid is None:
            return False
        _, _, win32gui = self._w32
        with contextlib.suppress(Exception):
            win32gui.Shell_NotifyIcon(win32gui.NIM_DELETE, self._nid)
        try:
            result = win32gui.Shell_NotifyIcon(win32gui.NIM_ADD, self._nid)
        except Exception as exc:  # noqa: BLE001 - 重试由定时器接管
            self._error = f"Shell_NotifyIcon(NIM_ADD) 异常：{type(exc).__name__}: {exc}"
            self._icon_added = False
            return False
        # pywin32 成功时返回 None（实测），失败返回 False/0。
        self._icon_added = result is None or bool(result)
        if self._icon_added:
            self._icon_adds += 1
            self._retry_delay_ms = _RETRY_INITIAL_MS
            if self._state:
                self.set_state(self._state)
        return self._icon_added

    def _schedule_icon_retry(self) -> None:
        """用隐藏窗口的定时器安排下一次注册尝试（退避到 30s 封顶）。

        之所以不用单独线程：``Shell_NotifyIcon`` 与图标句柄都属于本线程的
        窗口，定时器回调也在同一线程，不会出现跨线程句柄失效。
        """
        if not self._running or self._hwnd == 0 or self._w32 is None:
            self._ready.set()
            return
        _, _, win32gui = self._w32
        try:
            win32gui.SetTimer(self._hwnd, _ADD_TIMER_ID, self._retry_delay_ms, None)
        except Exception:  # noqa: BLE001 - 定时器都起不来就别让 start() 干等
            self._ready.set()
            return
        self._retry_delay_ms = min(self._retry_delay_ms * 2, _RETRY_MAX_MS)

    def _retry_icon(self) -> None:
        """定时器回调：任务栏还没准备好就继续退避重试，直到成功。"""
        if not self._running:
            return
        if self._add_icon():
            self._error = None
            self._ready.set()
            return
        if time.monotonic() >= self._ready_deadline:
            # 首次启动已经等够了：如实告诉调用方"图标没加上"，但重试不停止 ——
            # 开机自启时任务栏可能比本进程晚十几秒才可用。
            self._ready.set()
        self._schedule_icon_retry()

    def _on_taskbar_created(self) -> None:
        """收到 ``TaskbarCreated``：Explorer 重启后把图标重新注册回去。

        窗口、菜单（每次右键现建）与消息循环都还在，只有通知区图标是新外壳
        里的空白，所以这里只需重建图标 / 提示文字 / 状态色。
        """
        if not self._running or self._nid is None:
            return
        self._taskbar_recoveries += 1
        if self._add_icon():
            self._error = None
            return
        self._error = "Explorer 重启后 NIM_ADD 失败（任务栏可能尚未就绪，正在重试）"
        self._schedule_icon_retry()

    # ------------------------------------------------------------------ #
    # 消息循环（托盘线程）
    # ------------------------------------------------------------------ #
    def _run(self) -> None:
        try:
            win32api, win32con, win32gui = _load_win32()
            self._w32 = (win32api, win32con, win32gui)
            self._message_id = win32con.WM_USER + 20
            self._show_message_id = win32gui.RegisterWindowMessage(SHOW_MESSAGE_NAME)
            # 必须是 RegisterWindowMessage：TaskbarCreated 是系统广播的注册消息，
            # 数值随会话变化，写死常量会永远收不到。
            self._taskbar_created_id = win32gui.RegisterWindowMessage(TASKBAR_CREATED_NAME)
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
            if self._add_icon():
                self._ready.set()
            else:
                # 开机自启时本进程常比任务栏先起来：一次失败不代表永远没有
                # 图标，交给定时器退避重试；start() 到点会如实返回当前状态。
                self._error = "Shell_NotifyIcon(NIM_ADD) 失败（任务栏可能尚未就绪，正在重试）"
                self._schedule_icon_retry()

            while self._running:
                win32gui.PumpWaitingMessages()
                if not self._ready.is_set() and time.monotonic() >= self._ready_deadline:
                    self._ready.set()
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
                win32gui.KillTimer(self._hwnd, _ADD_TIMER_ID)
        except Exception:  # noqa: BLE001 - 窗口销毁时系统会一并清掉定时器
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
        elif self._taskbar_created_id and msg == self._taskbar_created_id:
            # Explorer 重启（或手工广播）：通知区已被清空，必须重新注册图标。
            self._on_taskbar_created()
        elif msg == win32con.WM_TIMER and int(wparam) == _ADD_TIMER_ID:
            self._retry_icon()
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
            with self._menu_lock:
                disabled = set(self._disabled_actions)
            for menu_id, action, label in self._menu_items:
                flags = win32con.MF_STRING
                if action in disabled:
                    flags |= win32con.MF_GRAYED
                win32gui.AppendMenu(menu, flags, menu_id, label)
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

    def set_disabled_actions(self, actions: set[str] | frozenset[str] | None) -> None:
        """设置右键菜单里要置灰的动作（启动/停止进行中防连点）。"""
        with self._menu_lock:
            self._disabled_actions = set(actions or ())

    def handle_command(self, menu_id: int) -> None:
        """菜单命令入口（右键菜单与 ``WM_COMMAND`` 共用；测试直接调用它）。"""
        for candidate, action, _label in self._menu_items:
            if candidate == menu_id:
                self._dispatch(action)
                return

    def _dispatch(self, action: str) -> None:
        with contextlib.suppress(Exception):
            self._on_action(action)


__all__ = [
    "ACTION_OPEN_BROWSER",
    "ACTION_OPEN_CONSOLE",
    "ACTION_OPEN_LOGS",
    "ACTION_QUIT",
    "ACTION_RESTART_SERVICE",
    "ACTION_SHOW_STATUS",
    "ACTION_STOP_SERVICE",
    "ACTION_TOGGLE_STATUS",
    "LABEL_OPEN_BROWSER",
    "LABEL_OPEN_CONSOLE",
    "LABEL_OPEN_LOGS",
    "LABEL_QUIT",
    "LABEL_RESTART_SERVICE",
    "LABEL_SHOW_STATUS",
    "LABEL_STOP_SERVICE",
    "MENU_ITEMS",
    "menu_items_for",
    "SHOW_MESSAGE_NAME",
    "STATE_COLORS",
    "STATE_ERROR",
    "STATE_RUNNING",
    "STATE_STOPPED",
    "SystemTray",
    "TASKBAR_CREATED_NAME",
    "available",
    "broadcast_taskbar_created",
    "find_tray_window",
    "request_show",
    "tray_class_name",
    "write_state_icon",
]
