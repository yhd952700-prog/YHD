"""Windows 侧只读观察器（pywin32 窗口 API）。

硬约束：

* **只看不做**：没有 ``SendInput`` / ``mouse_event`` / 截图 / 跨进程内存读取；
* **读不到就如实报**：进程名、可执行路径、窗口矩形、控件树任何一项取不到，
  都留空并在 ``*_error`` / ``elements_reason`` 里说明，绝不编造坐标；
* **不读控件文本**：控件文本可能是用户输入或聊天内容，超出只读窗口结构的范围；
* **可注入 backend**：真实 pywin32 调用藏在 ``PyWin32Backend`` 后面，测试可
  注入假窗口数据，CI 无桌面环境时真实冒烟测试直接跳过。
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional, Protocol, Sequence, Tuple

from .contracts import (
    AutomationError,
    ElementInfo,
    Observation,
    TargetScope,
    WindowInfo,
)
from .observe import (
    DEFAULT_MAX_ELEMENTS,
    DEFAULT_MAX_WINDOWS,
    ELEMENTS_UNSUPPORTED_REASON,
)

Rect = Tuple[int, int, int, int]

#: 判断"全屏"时允许的像素误差（某些应用窗口矩形会比显示器大 1px）。
_FULLSCREEN_TOLERANCE = 2


class Win32Api(Protocol):
    """真实窗口 API 的最小接口；测试可注入同形状的假实现。"""

    available: bool
    unavailable_reason: str

    def enum_windows(self) -> Sequence[int]:
        ...

    def foreground_window(self) -> int:
        ...

    def window_title(self, handle: int) -> str:
        ...

    def window_pid(self, handle: int) -> int:
        ...

    def window_rect(self, handle: int) -> Rect:
        ...

    def monitor_rect(self, handle: int) -> Optional[Rect]:
        ...

    def is_visible(self, handle: int) -> bool:
        ...

    def is_minimized(self, handle: int) -> bool:
        ...

    def enum_children(self, handle: int) -> Sequence[int]:
        ...

    def class_name(self, handle: int) -> str:
        ...

    def control_id(self, handle: int) -> Optional[int]:
        ...

    def is_enabled(self, handle: int) -> bool:
        ...

    def process_info(self, pid: int) -> Tuple[str, str, str]:
        ...


class PyWin32Backend:
    """pywin32 + psutil 的真实实现。

    模块导入失败时不抛异常，而是标记 ``available=False``；观察器会据此明确
    报错，而不是返回一个空窗口列表假装"没有窗口"。
    """

    def __init__(self) -> None:
        self.available = False
        self.unavailable_reason = ""
        self._win32api: Any = None
        self._win32con: Any = None
        self._win32gui: Any = None
        self._win32process: Any = None
        self._psutil: Any = None
        try:
            import win32api
            import win32con
            import win32gui
            import win32process
        except Exception as exc:  # noqa: BLE001 - 缺依赖要如实上报
            self.unavailable_reason = f"pywin32_unavailable:{type(exc).__name__}"
            return
        self._win32api = win32api
        self._win32con = win32con
        self._win32gui = win32gui
        self._win32process = win32process
        try:
            import psutil
        except Exception:  # noqa: BLE001 - 没有 psutil 仍可读窗口结构
            self._psutil = None
        else:
            self._psutil = psutil
        self.available = True

    # ---------------------------------------------------------------- 窗口
    def enum_windows(self) -> Sequence[int]:
        handles: List[int] = []

        def _collect(handle: int, _extra: Any) -> bool:
            handles.append(int(handle))
            return True

        self._win32gui.EnumWindows(_collect, None)
        return handles

    def foreground_window(self) -> int:
        return int(self._win32gui.GetForegroundWindow() or 0)

    def window_title(self, handle: int) -> str:
        return str(self._win32gui.GetWindowText(int(handle)) or "")

    def window_pid(self, handle: int) -> int:
        return int(self._win32process.GetWindowThreadProcessId(int(handle))[1])

    def window_rect(self, handle: int) -> Rect:
        return tuple(int(part) for part in self._win32gui.GetWindowRect(int(handle)))

    def monitor_rect(self, handle: int) -> Optional[Rect]:
        try:
            monitor = self._win32api.MonitorFromWindow(
                int(handle), self._win32con.MONITOR_DEFAULTTOPRIMARY
            )
            info = self._win32api.GetMonitorInfo(monitor)
        except Exception:  # noqa: BLE001 - 取不到就返回 None，由上层标注
            return None
        rect = info.get("Monitor") or info.get("Work")
        if not rect:
            return None
        return tuple(int(part) for part in rect)

    def is_visible(self, handle: int) -> bool:
        return bool(self._win32gui.IsWindowVisible(int(handle)))

    def is_minimized(self, handle: int) -> bool:
        return bool(self._win32gui.IsIconic(int(handle)))

    # ---------------------------------------------------------------- 元素
    def enum_children(self, handle: int) -> Sequence[int]:
        handles: List[int] = []

        def _collect(child: int, _extra: Any) -> bool:
            handles.append(int(child))
            return True

        self._win32gui.EnumChildWindows(int(handle), _collect, None)
        return handles

    def class_name(self, handle: int) -> str:
        return str(self._win32gui.GetClassName(int(handle)) or "")

    def control_id(self, handle: int) -> Optional[int]:
        try:
            return int(self._win32gui.GetDlgCtrlID(int(handle)))
        except Exception:  # noqa: BLE001 - 非对话框控件可能没有 ID
            return None

    def is_enabled(self, handle: int) -> bool:
        return bool(self._win32gui.IsWindowEnabled(int(handle)))

    # ---------------------------------------------------------------- 进程
    def process_info(self, pid: int) -> Tuple[str, str, str]:
        """返回 ``(进程名, 可执行路径, 错误说明)``，失败时错误说明非空。"""

        if self._psutil is None:
            return "", "", "psutil_unavailable"
        try:
            process = self._psutil.Process(int(pid))
            name = str(process.name() or "")
        except Exception as exc:  # noqa: BLE001 - 无权限/进程已退出
            return "", "", f"process_unavailable:{type(exc).__name__}"
        try:
            executable = str(process.exe() or "")
            return name, executable, ""
        except Exception as exc:  # noqa: BLE001 - 路径单独失败仍保留进程名
            return name, "", f"executable_unavailable:{type(exc).__name__}"


class WindowsObserver:
    """把 ``Win32Api`` 的结果整理成 :class:`Observation`。"""

    backend = "windows"

    def __init__(
        self,
        *,
        backend: Optional[Win32Api] = None,
        max_windows: int = DEFAULT_MAX_WINDOWS,
        max_elements: int = DEFAULT_MAX_ELEMENTS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._api = backend or PyWin32Backend()
        self._max_windows = int(max_windows)
        self._max_elements = int(max_elements)
        self._clock = clock

    def observe(
        self,
        scope: TargetScope = TargetScope.WINDOW,
        *,
        include_elements: bool = True,
    ) -> Observation:
        if not bool(getattr(self._api, "available", True)):
            raise AutomationError(
                "observation_backend_unavailable",
                str(getattr(self._api, "unavailable_reason", "") or "窗口 API 不可用"),
            )
        try:
            foreground_handle = int(self._api.foreground_window() or 0)
        except Exception:  # noqa: BLE001 - 前台窗口读不到不阻断窗口清单
            foreground_handle = 0

        read_errors = [0]
        truncated = False
        if scope is TargetScope.SCREEN:
            # 只有明确要求"全屏窗口清单"时才枚举所有顶层窗口；
            # 默认的前台观察不碰其它窗口，避免每次轮询都扫一遍桌面。
            try:
                handles = list(self._api.enum_windows())
            except Exception as exc:  # noqa: BLE001 - 枚举失败必须上报
                raise AutomationError(
                    "observation_failed",
                    f"枚举窗口失败: {type(exc).__name__}",
                ) from exc
            candidates = self._candidates(handles, foreground_handle, read_errors)
            if len(candidates) > self._max_windows:
                candidates = candidates[: self._max_windows]
                truncated = True
        else:
            # 没有前台窗口时不能拿"第一个可见窗口"冒充前台。
            candidates = [foreground_handle] if foreground_handle else []

        windows = [
            self._build_window(handle, foreground_handle, read_errors)
            for handle in candidates
        ]
        foreground = windows[0] if (foreground_handle and windows) else None

        elements: Tuple[ElementInfo, ...] = ()
        supported = False
        reason = ""
        if not include_elements:
            reason = "调用方未请求元素"
        elif foreground is None:
            reason = "没有前台窗口，无法读取元素"
        else:
            elements, supported, reason, elements_truncated = self._elements(
                foreground.handle, read_errors
            )
            truncated = truncated or elements_truncated
            foreground = _with_element_state(foreground, supported, len(elements))
            windows[0] = foreground

        summary: Dict[str, Any] = {
            "platform": "windows",
            "scope": scope.value,
            "window_count": len(windows),
            "element_count": len(elements),
            "window_read_errors": read_errors[0],
        }

        return Observation(
            focus=(
                (foreground.process_name or foreground.title) if foreground else None
            ),
            scope=scope,
            captured_at=self._clock(),
            summary=summary,
            backend=self.backend,
            foreground=foreground,
            windows=tuple(windows),
            elements=tuple(elements),
            elements_supported=supported,
            elements_reason=reason,
            truncated=truncated,
        )

    # ---------------------------------------------------------------- helpers
    def _candidates(
        self,
        handles: Sequence[int],
        foreground_handle: int,
        read_errors: List[int],
    ) -> List[int]:
        """挑选可见、有标题的顶层窗口；前台窗口永远排第一。"""

        seen: List[int] = []

        def _append(handle: int) -> None:
            if handle and handle not in seen:
                seen.append(handle)

        if foreground_handle:
            _append(foreground_handle)
        for handle in handles:
            handle = int(handle)
            if handle == foreground_handle:
                continue
            visible = bool(
                self._safe(self._api.is_visible, handle, False, read_errors)
            )
            if not visible:
                continue
            title = str(self._safe(self._api.window_title, handle, "", read_errors))
            if not title.strip():
                # 空标题多半是隐藏的工具窗口，跳过以免清单变成噪声。
                continue
            _append(handle)
        return seen

    def _build_window(
        self,
        handle: int,
        foreground_handle: int,
        read_errors: List[int],
    ) -> WindowInfo:
        title = str(self._safe(self._api.window_title, handle, "", read_errors))
        try:
            pid = int(self._api.window_pid(handle))
        except Exception:  # noqa: BLE001 - 读不到 PID 就留空
            pid = None
            read_errors[0] += 1

        process_name = ""
        executable_path = ""
        process_error = "" if pid is not None else "pid_unavailable"
        if pid is not None:
            try:
                process_name, executable_path, process_error = self._api.process_info(
                    pid
                )
            except Exception as exc:  # noqa: BLE001 - 注入实现异常也要如实报
                process_error = f"process_unavailable:{type(exc).__name__}"

        visible = bool(self._safe(self._api.is_visible, handle, True, read_errors))
        minimized = bool(
            self._safe(self._api.is_minimized, handle, False, read_errors)
        )
        bounds: Optional[Rect] = None
        try:
            bounds = tuple(
                int(part) for part in self._api.window_rect(handle)
            )
        except Exception:  # noqa: BLE001 - 坐标取不到就不给
            read_errors[0] += 1

        monitor: Optional[Rect] = None
        try:
            raw_monitor = self._api.monitor_rect(handle)
            if raw_monitor is not None:
                monitor = tuple(int(part) for part in raw_monitor)
        except Exception:  # noqa: BLE001
            read_errors[0] += 1

        fullscreen = False
        fullscreen_error = ""
        if bounds is None:
            fullscreen_error = "bounds_unavailable"
        elif monitor is None:
            fullscreen_error = "monitor_rect_unavailable"
        else:
            fullscreen = _covers(bounds, monitor) and visible and not minimized

        return WindowInfo(
            handle=int(handle),
            title=title,
            process_name=process_name,
            executable_path=executable_path,
            pid=pid,
            foreground=int(handle) == int(foreground_handle or 0),
            fullscreen=fullscreen,
            visible=visible,
            minimized=minimized,
            bounds=bounds,
            elements_supported=False,
            element_count=0,
            process_error=process_error,
            fullscreen_error=fullscreen_error,
        )

    def _elements(
        self,
        handle: int,
        read_errors: List[int],
    ) -> Tuple[Tuple[ElementInfo, ...], bool, str, bool]:
        """返回 ``(元素, 是否支持, 原因, 是否截断)``。

        只有枚举成功才算"支持元素级读取"；零子控件也按下支持来处理并给出
        明确原因，绝不凭空造元素。
        """

        try:
            children = list(self._api.enum_children(handle))
        except Exception as exc:  # noqa: BLE001 - 读不到就明确报不支持
            read_errors[0] += 1
            return (), False, f"{ELEMENTS_UNSUPPORTED_REASON}：{type(exc).__name__}", False

        if not children:
            return (), False, f"{ELEMENTS_UNSUPPORTED_REASON}（未枚举到子控件）", False

        supported = True
        elements: List[ElementInfo] = []
        truncated = False
        for child in children:
            child = int(child)
            visible = bool(
                self._safe(self._api.is_visible, child, False, read_errors)
            )
            if not visible:
                continue
            if len(elements) >= self._max_elements:
                truncated = True
                break
            class_name = str(
                self._safe(self._api.class_name, child, "", read_errors)
            )
            control_id: Optional[int] = None
            try:
                raw_id = self._api.control_id(child)
                control_id = int(raw_id) if raw_id is not None else None
            except Exception:  # noqa: BLE001 - 没有控件 ID 不算失败
                control_id = None
            enabled = bool(
                self._safe(self._api.is_enabled, child, False, read_errors)
            )
            rect: Optional[Rect] = None
            try:
                rect = tuple(
                    int(part) for part in self._api.window_rect(child)
                )
            except Exception:  # noqa: BLE001 - 单个元素坐标读不到就留空
                read_errors[0] += 1
            elements.append(
                ElementInfo(
                    handle=child,
                    class_name=class_name,
                    control_id=control_id,
                    enabled=enabled,
                    visible=visible,
                    bounds=rect,
                )
            )

        reason = "" if elements else "该窗口没有可见子控件"
        return tuple(elements), supported, reason, truncated

    def _safe(
        self,
        fn: Callable[[int], Any],
        handle: int,
        default: Any,
        read_errors: List[int],
    ) -> Any:
        try:
            return fn(handle)
        except Exception:  # noqa: BLE001 - 单个窗口读失败不影响整体观察
            read_errors[0] += 1
            return default


def _covers(bounds: Rect, monitor: Rect) -> bool:
    left, top, right, bottom = bounds
    m_left, m_top, m_right, m_bottom = monitor
    return (
        left <= m_left + _FULLSCREEN_TOLERANCE
        and top <= m_top + _FULLSCREEN_TOLERANCE
        and right >= m_right - _FULLSCREEN_TOLERANCE
        and bottom >= m_bottom - _FULLSCREEN_TOLERANCE
    )


def _with_element_state(
    window: WindowInfo, supported: bool, element_count: int
) -> WindowInfo:
    from dataclasses import replace

    return replace(
        window,
        elements_supported=supported,
        element_count=int(element_count),
    )


__all__ = ["PyWin32Backend", "Win32Api", "WindowsObserver"]
