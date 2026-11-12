"""Windows 高 DPI 适配：进程感知 + tkinter 缩放 + 窗口自适应尺寸。

为什么需要独立一层
------------------
Windows 会把**没有声明 DPI 感知**的进程按 96 DPI 渲染，再整屏位图拉伸。
125% / 150% / 200% 下所有文字都发虚；把窗口拖到另一块 DPI 不同的显示器时
还会被重新采样、尺寸跳变。tkinter 既不替我们声明感知级别，也不会在换屏后
重算缩放。

本模块只做显示层，不碰任何业务或文案：

1. :func:`enable_dpi_awareness` —— 在创建任何窗口**之前**声明
   Per-Monitor V2（Win10 1703+），并逐级回退到 Per-Monitor / System。
2. :class:`DpiWatcher` —— 跟踪窗口所在显示器的 DPI。收到
   ``WM_DPICHANGED``（另配低频轮询兜底）时重设 ``tk scaling`` 并通知调用方
   重新排布，避免跨屏拖动后变形。
3. :func:`fit_window` / :class:`ScrollableFrame` —— 按内容自然尺寸开窗，
   并钳制在当前显示器工作区内；放不下时内容区滚动，底部按钮始终在屏内。

注意：``tk scaling`` 的单位是"每磅像素数"，96 DPI 对应 ``96 / 72``。只对
以磅为单位声明的字体生效（本仓库的 UI 全部是磅值字体）。
"""

from __future__ import annotations

import ctypes
import os
import tkinter as tk
from ctypes import wintypes
from typing import Any, Callable

#: ``GetProcessDpiAwareness`` 的返回值。
DPI_UNAWARE = 0
DPI_SYSTEM_AWARE = 1
DPI_PER_MONITOR_AWARE = 2

#: ``DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2`` 的伪句柄值。
_PER_MONITOR_AWARE_V2 = -4

#: 窗口消息：窗口被移到 DPI 不同的显示器（wParam 高字 = 新 DPI）。
WM_DPICHANGED = 0x02E0

#: 窗口消息：显示分辨率变化（含远程桌面连接/断开）。
#: 此时 DPI 往往**不变**，却可能出现窗口整体落在新工作区之外。
WM_DISPLAYCHANGE = 0x007E

#: 窗口消息：系统设置变化（显示器增删、缩放与分辨率调整会随之广播）。
WM_SETTINGCHANGE = 0x001A

#: ``SetWindowLongPtr(GWL_WNDPROC)``。
_GWL_WNDPROC = -4

_MONITOR_DEFAULTTONEAREST = 2
_LOGPIXELSX = 88


class _RECT(ctypes.Structure):
    _fields_ = (
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    )


class _MONITORINFO(ctypes.Structure):
    _fields_ = (
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", _RECT),
        ("rcWork", _RECT),
        ("dwFlags", wintypes.DWORD),
    )


_user32_cache: Any = None
_shcore_cache: Any = None


def _user32() -> Any:
    global _user32_cache
    if os.name != "nt":
        return None
    if _user32_cache is None:
        _user32_cache = ctypes.WinDLL("user32", use_last_error=True)
    return _user32_cache


def _shcore() -> Any:
    global _shcore_cache
    if os.name != "nt":
        return None
    if _shcore_cache is None:
        _shcore_cache = ctypes.WinDLL("shcore", use_last_error=True)
    return _shcore_cache


def _call(func: Any, *args: Any, argtypes: tuple[Any, ...], restype: Any) -> Any:
    """给 ctypes 函数补上签名再调用（缺签名在 64 位下会把 HWND 截断）。"""
    func.argtypes = list(argtypes)
    func.restype = restype
    return func(*args)


# ---------------------------------------------------------------------------
# 进程 DPI 感知
# ---------------------------------------------------------------------------


def process_dpi_awareness() -> int | None:
    """读取当前进程的 DPI 感知级别；非 Windows 或接口不可用时返回 ``None``。"""
    if os.name != "nt":
        return None
    try:
        value = ctypes.c_int()
        result = _call(
            _shcore().GetProcessDpiAwareness,
            None,
            ctypes.byref(value),
            argtypes=(wintypes.HANDLE, ctypes.POINTER(ctypes.c_int)),
            restype=ctypes.c_long,
        )
        if result == 0:
            return int(value.value)
    except Exception:  # noqa: BLE001 - 仅用于诊断，失败就当作未知
        pass
    return None


def enable_dpi_awareness() -> str:
    """在创建窗口之前声明 DPI 感知，返回实际生效的级别名称。

    顺序：Per-Monitor V2 → Per-Monitor → System。已经由外部（清单/宿主）
    设置过时，新调用会失败但不报错——此时以 :func:`process_dpi_awareness`
    读到的实际值为准。
    """
    if os.name != "nt":
        return "unsupported"
    user32 = _user32()
    try:
        setter = getattr(user32, "SetProcessDpiAwarenessContext", None)
        if setter is not None and _call(
            setter,
            ctypes.c_void_p(_PER_MONITOR_AWARE_V2),
            argtypes=(ctypes.c_void_p,),
            restype=wintypes.BOOL,
        ):
            return "per-monitor-v2"
    except Exception:  # noqa: BLE001 - Win10 1703 以下没有这个入口
        pass
    try:
        shcore = _shcore()
        setter = getattr(shcore, "SetProcessDpiAwareness", None)
        if setter is not None and _call(
            setter,
            DPI_PER_MONITOR_AWARE,
            argtypes=(ctypes.c_int,),
            restype=ctypes.c_long,
        ) == 0:
            return "per-monitor"
    except Exception:  # noqa: BLE001 - Win8.1 以下没有 shcore
        pass
    try:
        setter = getattr(user32, "SetProcessDPIAware", None)
        if setter is not None and _call(
            setter,
            argtypes=(),
            restype=wintypes.BOOL,
        ):
            return "system"
    except Exception:  # noqa: BLE001
        pass

    level = process_dpi_awareness()
    return {
        DPI_PER_MONITOR_AWARE: "per-monitor",
        DPI_SYSTEM_AWARE: "system",
        DPI_UNAWARE: "unaware",
    }.get(level, "unknown")


def system_dpi() -> int:
    """主显示器 DPI（拿不到时按 96 处理）。"""
    user32 = _user32()
    if user32 is None:
        return 96
    try:
        getter = getattr(user32, "GetDpiForSystem", None)
        if getter is not None:
            value = int(_call(getter, argtypes=(), restype=wintypes.UINT))
            if value:
                return value
    except Exception:  # noqa: BLE001
        pass
    try:
        hdc = _call(user32.GetDC, None, argtypes=(wintypes.HWND,), restype=wintypes.HDC)
        if hdc:
            gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
            try:
                value = int(
                    _call(
                        gdi32.GetDeviceCaps,
                        hdc,
                        _LOGPIXELSX,
                        argtypes=(wintypes.HDC, ctypes.c_int),
                        restype=ctypes.c_int,
                    )
                )
            finally:
                _call(
                    user32.ReleaseDC,
                    None,
                    hdc,
                    argtypes=(wintypes.HWND, wintypes.HDC),
                    restype=ctypes.c_int,
                )
            if value:
                return value
    except Exception:  # noqa: BLE001
        pass
    return 96


def window_dpi(hwnd: int | None = None) -> int:
    """窗口所在显示器的 DPI；没有窗口时退回系统 DPI。"""
    user32 = _user32()
    if user32 is not None and hwnd:
        try:
            getter = getattr(user32, "GetDpiForWindow", None)
            if getter is not None:
                value = int(
                    _call(getter, wintypes.HWND(int(hwnd)), argtypes=(wintypes.HWND,), restype=wintypes.UINT)
                )
                if value:
                    return value
        except Exception:  # noqa: BLE001 - Win10 1607 以下没有该接口
            pass
    return system_dpi()


def apply_tk_scaling(root: Any, dpi: int | None = None) -> int:
    """把 Tk 的"每磅像素数"设成实际 DPI，返回采用的 DPI。

    必须在创建控件**之前**调用一次；换屏后由 :class:`DpiWatcher` 再调用。
    """
    value = int(dpi or window_dpi(root.winfo_id()))
    root.tk.call("tk", "scaling", float(value) / 72.0)
    return value


def scaled(value: float, dpi: int) -> int:
    """把 96 DPI 下的逻辑像素换算成当前 DPI 的物理像素。"""
    return int(round(float(value) * int(dpi) / 96.0))


# ---------------------------------------------------------------------------
# 尺寸 / 位置
# ---------------------------------------------------------------------------


def work_area(hwnd: int) -> tuple[int, int, int, int]:
    """窗口所在显示器的可用工作区 ``(left, top, right, bottom)``（物理像素）。"""
    user32 = _user32()
    if user32 is not None:
        try:
            monitor = _call(
                user32.MonitorFromWindow,
                wintypes.HWND(int(hwnd)),
                _MONITOR_DEFAULTTONEAREST,
                argtypes=(wintypes.HWND, wintypes.DWORD),
                restype=wintypes.HANDLE,
            )
            info = _MONITORINFO()
            info.cbSize = ctypes.sizeof(_MONITORINFO)
            if monitor and _call(
                user32.GetMonitorInfoW,
                monitor,
                ctypes.byref(info),
                argtypes=(wintypes.HANDLE, ctypes.POINTER(_MONITORINFO)),
                restype=wintypes.BOOL,
            ):
                rect = info.rcWork
                return (rect.left, rect.top, rect.right, rect.bottom)
        except Exception:  # noqa: BLE001
            pass
    width = int(user32.GetSystemMetrics(0)) if user32 is not None else 1024
    height = int(user32.GetSystemMetrics(1)) if user32 is not None else 768
    return (0, 0, width, height)


def _frame_hwnd(root: Any) -> int:
    """Tk 顶层窗口的**外框**句柄（带标题栏的那个，``winfo_id`` 只是客户区）。"""
    user32 = _user32()
    if user32 is None or os.name != "nt":
        return 0
    try:
        hwnd = int(root.winfo_id())
        parent = int(
            _call(
                user32.GetParent,
                wintypes.HWND(hwnd),
                argtypes=(wintypes.HWND,),
                restype=wintypes.HWND,
            )
            or 0
        )
        return parent or hwnd
    except Exception:  # noqa: BLE001
        return 0


def _frame_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    """外框矩形 ``(left, top, right, bottom)``；拿不到返回 ``None``。"""
    user32 = _user32()
    if user32 is None or not hwnd:
        return None
    try:
        rect = _RECT()
        if _call(
            user32.GetWindowRect,
            wintypes.HWND(hwnd),
            ctypes.byref(rect),
            argtypes=(wintypes.HWND, ctypes.POINTER(_RECT)),
            restype=wintypes.BOOL,
        ):
            return (rect.left, rect.top, rect.right, rect.bottom)
    except Exception:  # noqa: BLE001
        pass
    return None


def _move_frame(hwnd: int, x: int, y: int) -> None:
    """只移动外框，不改尺寸（Tk 的 ``+x+y`` 在带装饰窗口上容易差一个标题栏）。"""
    user32 = _user32()
    if user32 is None or not hwnd:
        return
    try:
        _call(
            user32.SetWindowPos,
            wintypes.HWND(hwnd),
            None,
            int(x),
            int(y),
            0,
            0,
            0x0001 | 0x0004 | 0x0010,  # NOSIZE | NOZORDER | NOACTIVATE
            argtypes=(wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT),
            restype=wintypes.BOOL,
        )
    except Exception:  # noqa: BLE001
        pass


def fit_window(
    root: Any,
    *,
    min_width: int = 0,
    min_height: int = 0,
    margin: int | None = None,
    dpi: int | None = None,
    center: bool = True,
) -> tuple[int, int, int, int]:
    """按内容自然尺寸开窗/改窗，并保证**整窗外框**落在当前显示器工作区内。

    返回最终的 ``(x, y, width, height)``（外框，物理像素）。

    * ``center=True``：首次开窗，居中显示；
    * ``center=False``：换 DPI 或切换向导步骤时保持用户当前位置，只钳制边界。
    * ``margin`` 为 0 表示贴边允许；默认按 DPI 缩放 24 逻辑像素。

    Tk 的 ``geometry("WxH+X+Y")`` 在 Windows 上把 ``+X+Y`` 当作外框位置，
    但尺寸只算客户区；直接用客户区高度钳制会让窗口底边多出标题栏那一截。
    所以这里先定客户区尺寸，再按真实外框矩形定位。
    """
    root.update_idletasks()
    current_dpi = int(dpi or window_dpi(root.winfo_id()))
    gap = scaled(24, current_dpi) if margin is None else int(margin)

    # 先解除上一次留下的 minsize 下限。否则从小屏 / 高缩放切到大屏时没问题，
    # 但反向切换（1920×1080@200% → 1366×768）会被旧下限卡住：Tk 拒绝把窗口
    # 缩到比 minsize 还小，窗口就比工作区高一截、底边永远在屏幕外。
    # 真正的下限在函数末尾按本次客户区尺寸重新设置。
    root.minsize(1, 1)

    req_width = max(1, int(root.winfo_reqwidth()))
    req_height = max(1, int(root.winfo_reqheight()))
    left, top, right, bottom = work_area(root.winfo_id())
    available_width = max(1, right - left - 2 * gap)
    available_height = max(1, bottom - top - 2 * gap)

    width = min(req_width, available_width)
    height = min(req_height, available_height)
    client_width, client_height = width, height
    root.geometry(f"{width}x{height}")
    root.update_idletasks()

    frame = _frame_hwnd(root)
    outer = _frame_rect(frame) if frame else None
    if outer is None:
        # 拿不到外框（非 Windows / 句柄异常）：退回客户区近似。
        if center:
            x = left + max(gap, (right - left - width) // 2)
            y = top + max(gap, (bottom - top - height) // 2)
        else:
            x = int(root.winfo_rootx())
            y = int(root.winfo_rooty())
            x = min(max(x, left + gap), max(left + gap, right - gap - width))
            y = min(max(y, top + gap), max(top + gap, bottom - gap - height))
        root.geometry(f"{width}x{height}+{x}+{y}")
    else:
        outer_width = outer[2] - outer[0]
        outer_height = outer[3] - outer[1]
        if outer_width > available_width or outer_height > available_height:
            width = max(1, width - max(0, outer_width - available_width))
            height = max(1, height - max(0, outer_height - available_height))
            client_width, client_height = width, height
            root.geometry(f"{width}x{height}")
            root.update_idletasks()
            outer = _frame_rect(frame) or outer
            outer_width = outer[2] - outer[0]
            outer_height = outer[3] - outer[1]
        if center:
            x = left + max(gap, (right - left - outer_width) // 2)
            y = top + max(gap, (bottom - top - outer_height) // 2)
        else:
            x, y = outer[0], outer[1]
            x = min(max(x, left + gap), max(left + gap, right - gap - outer_width))
            y = min(max(y, top + gap), max(top + gap, bottom - gap - outer_height))
        _move_frame(frame, x, y)
        width, height = outer_width, outer_height

    # 允许用户继续缩小（内容会滚动），但不能小到看不见底部按钮。
    # minsize 用的是**客户区**尺寸，不能拿外框尺寸回填，否则标题栏那一截
    # 会被当成"不可缩小"的硬下限，窗口就再也压不回工作区内。
    root.minsize(
        min(client_width, max(1, scaled(min_width or 420, current_dpi))),
        min(client_height, max(1, scaled(min_height or 300, current_dpi))),
    )
    return (x, y, width, height)


# ---------------------------------------------------------------------------
# 可滚动内容区
# ---------------------------------------------------------------------------


class ScrollableFrame:
    """内容可滚动的容器；外框尺寸由父容器决定，底部按钮区不受影响。

    用法：``scroll = ScrollableFrame(root); scroll.frame.pack(...)``，
    再把内容放进 ``scroll.inner``。内容变化后调用 :meth:`prepare` 让窗口的
    "自然尺寸"反映最新内容（供 :func:`fit_window` 计算开窗大小）。
    """

    def __init__(self, parent: Any) -> None:
        self.frame = tk.Frame(parent)
        self.canvas = tk.Canvas(self.frame, highlightthickness=0, borderwidth=0)
        self.scrollbar = tk.Scrollbar(self.frame, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self._on_scroll)
        self.inner = tk.Frame(self.canvas)
        self._window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self._scrollbar_visible = True

        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")

        self.inner.bind("<Configure>", self._on_inner_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.frame.bind("<Enter>", self._bind_wheel)
        self.frame.bind("<Leave>", self._unbind_wheel)

    # -- 尺寸 ---------------------------------------------------------------
    def prepare(self) -> None:
        """用当前内容的自然尺寸更新画布，并回到顶部。"""
        self.inner.update_idletasks()
        self.canvas.configure(
            width=max(1, int(self.inner.winfo_reqwidth())),
            height=max(1, int(self.inner.winfo_reqheight())),
        )
        self.canvas.yview_moveto(0.0)

    def _on_inner_configure(self, _event: Any = None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_scroll(self, first: Any, last: Any) -> None:
        """内容装得下时收起滚动条，避免高 DPI 下多出一条无意义的竖条。"""
        first_value, last_value = float(first), float(last)
        needed = first_value > 0.0 or last_value < 1.0
        if needed and not self._scrollbar_visible:
            self.scrollbar.pack(side="right", fill="y")
            self._scrollbar_visible = True
        elif not needed and self._scrollbar_visible:
            self.scrollbar.pack_forget()
            self._scrollbar_visible = False
        self.scrollbar.set(first_value, last_value)

    def _on_canvas_configure(self, event: Any) -> None:
        # 内容宽度跟随可视区域，避免长文本被横向裁掉。
        self.canvas.itemconfigure(self._window, width=max(1, int(event.width)))

    # -- 滚轮 ---------------------------------------------------------------
    def _bind_wheel(self, _event: Any = None) -> None:
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)

    def _unbind_wheel(self, _event: Any = None) -> None:
        # 指针移到内容子控件上也会触发 frame 的 <Leave>，此时不能解绑，
        # 否则鼠标停在说明文字上时滚轮失效。
        if self._pointer_inside():
            return
        self.canvas.unbind_all("<MouseWheel>")

    def _pointer_inside(self) -> bool:
        try:
            x, y = self.frame.winfo_pointerxy()
            widget = self.frame.winfo_containing(x, y)
        except Exception:  # noqa: BLE001 - 窗口正在销毁
            return False
        while widget is not None:
            if widget is self.frame:
                return True
            widget = getattr(widget, "master", None)
        return False

    def _on_wheel(self, event: Any) -> None:
        first, last = self.canvas.yview()
        if first <= 0.0 and last >= 1.0:
            return  # 内容没超出，不拦截滚轮
        delta = -1 if int(getattr(event, "delta", 0)) > 0 else 1
        self.canvas.yview_scroll(delta, "units")


# ---------------------------------------------------------------------------
# WM_DPICHANGED
# ---------------------------------------------------------------------------


class FontScaler:
    """DPI 变化后重建控件字体，让"磅值字体"真正跟着缩放。

    Tk 只在**创建字体**时把磅值换算成像素；窗口已经存在时改 ``tk scaling``
    不会让已有字体变大（实测：``tk scaling`` 从 1.33 改到 2.67，标签高度不变）。
    把窗口拖到另一块 DPI 更高的显示器上，如果不重建字体，界面会保持原来的
    物理尺寸、字反而显得更小。这里按当前缩放把每个控件用到的字体重新创建
    一遍，并持有引用（Python 对象被回收时 Tcl 字体也会被删掉）。
    """

    def __init__(self, root: Any) -> None:
        self._root = root
        self._fonts: list[Any] = []

    def refresh(self) -> int:
        """重建树里所有控件的字体，返回实际替换的控件数。"""
        try:
            import tkinter.font as tkfont
        except Exception:  # noqa: BLE001 - 极端裁剪环境没有 tkinter.font
            return 0
        cache: dict[str, Any] = {}
        replaced = 0
        stack: list[Any] = [self._root]
        while stack:
            widget = stack.pop()
            try:
                stack.extend(widget.winfo_children())
                spec = str(widget.cget("font"))
            except Exception:  # noqa: BLE001 - 控件不支持 font 或已销毁
                continue
            font = cache.get(spec)
            if font is None:
                try:
                    source = tkfont.Font(root=self._root, font=spec)
                    actual = source.actual()
                    font = tkfont.Font(
                        root=self._root,
                        family=actual["family"],
                        size=actual["size"],
                        weight=actual["weight"],
                        slant=actual["slant"],
                        underline=actual["underline"],
                        overstrike=actual["overstrike"],
                    )
                except Exception:  # noqa: BLE001 - 解析不了的字体保持原样
                    continue
                cache[spec] = font
            try:
                widget.configure(font=font)
                replaced += 1
            except Exception:  # noqa: BLE001
                continue
        self._fonts = list(cache.values())
        return replaced


class DpiWatcher:
    """窗口换显示器时重算缩放并回调，保持窗口不变形。

    ``WM_DPICHANGED`` 是权威信号，但 tkinter 不暴露它：这里用 ctypes 给顶层
    窗口挂一个**只观察**的窗口过程，其余消息原样转交。同时保留低频轮询兜底
    （某些 Tk 版本/主题下顶层窗口过程拿不到该消息，但 ``GetDpiForWindow``
    仍会变化）。两条路径都汇总到 :meth:`_handle`，并用"DPI 未变就忽略"去重。
    """

    def __init__(
        self,
        root: Any,
        on_change: Callable[[int], None],
        *,
        poll_ms: int = 500,
    ) -> None:
        self._root = root
        self._on_change = on_change
        self._poll_ms = max(100, int(poll_ms))
        self._dpi = 96
        self._work: tuple[int, int, int, int] | None = None
        self._stopped = False
        self._fonts = FontScaler(root)
        self._user32: Any = None
        self._frame = 0
        self._old_wndproc: Any = None
        self._wndproc: Any = None

    def start(self) -> int:
        self._dpi = apply_tk_scaling(self._root)
        self._work = self._read_work_area()
        self._install_wndproc()
        self._schedule()
        return self._dpi

    def stop(self) -> None:
        self._stopped = True
        self._restore_wndproc()

    # -- 内部 ---------------------------------------------------------------
    def _schedule(self) -> None:
        if self._stopped:
            return
        try:
            self._root.after(self._poll_ms, self._poll)
        except Exception:  # noqa: BLE001 - 窗口已销毁
            self._stopped = True

    def _poll(self) -> None:
        if self._stopped:
            return
        try:
            current = window_dpi(self._root.winfo_id())
        except Exception:  # noqa: BLE001
            current = self._dpi
        if current != self._dpi:
            self._handle(current)
        else:
            # DPI 没变不代表位置还有效：分辨率切换、远程桌面断开后工作区会缩小，
            # 窗口可能整体落在新工作区之外（"卡在屏幕外拖不回来"）。这里独立检查。
            self._check_work_area()
        self._schedule()

    def _read_work_area(self) -> tuple[int, int, int, int] | None:
        try:
            return tuple(int(v) for v in work_area(self._root.winfo_id()))  # type: ignore[return-value]
        except Exception:  # noqa: BLE001 - 窗口已销毁
            return None

    def _check_work_area(self) -> None:
        """工作区变化（分辨率 / 远程桌面 / 显示器增删）时重新排布窗口。

        与 DPI 变化走同一个回调，调用方用 ``center=False`` 重新钳制即可把
        窗口拉回屏内，不需要额外的接口。
        """
        if self._stopped:
            return
        current = self._read_work_area()
        if current is None or current == self._work:
            return
        self._work = current
        try:
            self._on_change(self._dpi)
        except Exception:  # noqa: BLE001
            pass

    def _handle(self, dpi: int) -> None:
        dpi = int(dpi)
        if self._stopped or dpi <= 0 or dpi == self._dpi:
            return
        self._dpi = dpi
        # 换显示器时工作区通常也一起变，记下新值避免紧接着再触发一次重排。
        self._work = self._read_work_area() or self._work
        try:
            apply_tk_scaling(self._root, dpi)
        except Exception:  # noqa: BLE001 - 缩放失败不影响回调
            pass
        try:
            # 已存在的控件不会因为 tk scaling 变化而变大，必须重建字体。
            self._fonts.refresh()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._on_change(dpi)
        except Exception:  # noqa: BLE001
            pass

    def _install_wndproc(self) -> None:
        if os.name != "nt":
            return
        try:
            user32 = _user32()
            hwnd = int(self._root.winfo_id())
            frame = int(
                _call(
                    user32.GetParent,
                    wintypes.HWND(hwnd),
                    argtypes=(wintypes.HWND,),
                    restype=wintypes.HWND,
                )
                or 0
            )
            frame = frame or hwnd
            proc_type = ctypes.WINFUNCTYPE(
                ctypes.c_ssize_t,
                wintypes.HWND,
                wintypes.UINT,
                wintypes.WPARAM,
                wintypes.LPARAM,
            )
            proc = proc_type(self._on_message)
            setter = getattr(user32, "SetWindowLongPtrW", None) or getattr(
                user32, "SetWindowLongW", None
            )
            if setter is None:
                return
            old = _call(
                setter,
                wintypes.HWND(frame),
                _GWL_WNDPROC,
                ctypes.cast(proc, ctypes.c_void_p),
                argtypes=(wintypes.HWND, ctypes.c_int, ctypes.c_void_p),
                restype=ctypes.c_void_p,
            )
            self._user32 = user32
            self._frame = frame
            self._wndproc = proc
            self._old_wndproc = old
        except Exception:  # noqa: BLE001 - 挂钩失败就只靠轮询
            self._user32 = None
            self._frame = 0
            self._wndproc = None
            self._old_wndproc = None

    def _restore_wndproc(self) -> None:
        if not self._user32 or not self._frame or not self._old_wndproc:
            self._wndproc = None
            self._old_wndproc = None
            self._frame = 0
            return
        try:
            setter = getattr(self._user32, "SetWindowLongPtrW", None) or getattr(
                self._user32, "SetWindowLongW", None
            )
            if setter is not None:
                _call(
                    setter,
                    wintypes.HWND(self._frame),
                    _GWL_WNDPROC,
                    ctypes.c_void_p(self._old_wndproc),
                    argtypes=(wintypes.HWND, ctypes.c_int, ctypes.c_void_p),
                    restype=ctypes.c_void_p,
                )
        except Exception:  # noqa: BLE001 - 窗口可能已销毁
            pass
        finally:
            self._wndproc = None
            self._old_wndproc = None
            self._frame = 0
            self._user32 = None

    def _on_message(self, hwnd: int, msg: int, wparam: int, lparam: int) -> int:
        try:
            if msg == WM_DPICHANGED:
                new_dpi = int(wparam) & 0xFFFF
                if new_dpi > 0:
                    self._root.after_idle(lambda: self._handle(new_dpi))
            elif msg in (WM_DISPLAYCHANGE, WM_SETTINGCHANGE):
                # 分辨率 / 远程桌面切换：DPI 可能一点没变，但工作区变了。
                self._root.after_idle(self._check_work_area)
        except Exception:  # noqa: BLE001 - 绝不让异常穿回 Windows 消息循环
            pass
        if self._old_wndproc:
            try:
                return int(
                    _call(
                        self._user32.CallWindowProcW,
                        ctypes.c_void_p(self._old_wndproc),
                        wintypes.HWND(hwnd),
                        wintypes.UINT(msg),
                        wintypes.WPARAM(wparam),
                        wintypes.LPARAM(lparam),
                        argtypes=(
                            ctypes.c_void_p,
                            wintypes.HWND,
                            wintypes.UINT,
                            wintypes.WPARAM,
                            wintypes.LPARAM,
                        ),
                        restype=ctypes.c_ssize_t,
                    )
                )
            except Exception:  # noqa: BLE001
                pass
        return 0


__all__ = [
    "DPI_PER_MONITOR_AWARE",
    "DPI_SYSTEM_AWARE",
    "DPI_UNAWARE",
    "DpiWatcher",
    "FontScaler",
    "ScrollableFrame",
    "apply_tk_scaling",
    "enable_dpi_awareness",
    "fit_window",
    "process_dpi_awareness",
    "scaled",
    "system_dpi",
    "window_dpi",
    "work_area",
]
