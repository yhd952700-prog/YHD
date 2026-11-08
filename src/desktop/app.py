"""桌面壳：状态窗口 + 系统托盘 + 网关子进程。

窗口关闭语义（明确，不留歧义）
------------------------------
  * 点标题栏关闭按钮 = **最小化到托盘**，网关继续运行。
  * 真正退出只有两条路：托盘菜单「退出」，或窗口里的「退出程序」。
  * 托盘菜单「停止服务」只停网关，窗口与托盘仍驻留，便于排障或重启。

线程模型：
  * tkinter 主循环：主线程（窗口与所有 UI 更新）。
  * 托盘 Win32 消息循环：``SystemTray`` 自己的线程。
  * 状态轮询：后台线程，只把结果放进 ``queue``，由 UI 线程消费。
  * 网关：独立子进程（``GatewayService``）。
  * 「停止服务」与「退出」都走 ``GatewayService.stop()``：先发 stdin 停止
    指令让 uvicorn 优雅收尾（最多 ``GRACEFUL_TIMEOUT`` 秒），超时才强杀；
    结果（优雅 / 超时强杀 / 未确认收尾）同时写日志并显示在窗口里。
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import queue
import threading
import tkinter as tk
import webbrowser
from logging.handlers import RotatingFileHandler
from tkinter import messagebox
from typing import Any

from . import CONSOLE_DIST, DATA_DIR, LOG_DIR, dpi
from .about import APP_NAME
from .diagnostics import format_bytes, probe_directory_writable, service_guidance
from .probe import describe_port_owner, http_json, port_is_free
from .service import DEFAULT_PORT, GRACEFUL_TIMEOUT, GatewayService
from .startup import STARTUP_TIMEOUT, unavailable_gate, wait_until_ready
from .tray import (
    ACTION_OPEN_CONSOLE,
    ACTION_OPEN_LOGS,
    ACTION_QUIT,
    ACTION_SHOW_STATUS,
    ACTION_STOP_SERVICE,
    ACTION_TOGGLE_STATUS,
    LABEL_OPEN_CONSOLE,
    LABEL_OPEN_LOGS,
    LABEL_QUIT,
    LABEL_STOP_SERVICE,
    STATE_ERROR,
    STATE_RUNNING,
    STATE_STOPPED,
    SystemTray,
    request_show,
)

logger = logging.getLogger(__name__)


def _configure_logging() -> None:
    """托盘壳自己的日志：10 MiB × 5 份轮转（与网关同一套参数）。

    挂在 ``src.desktop`` 包 logger 上，``app`` / ``service`` 等子模块都向它
    propagate —— 停止是优雅还是强杀，必须能在同一个文件里查到。

    formatter 用 :class:`RedactingFormatter`：桌面壳的日志会带出
    ``C:\\Users\\<用户>\\...`` 数据目录与环境变量名，落盘前统一折叠。
    """
    pkg_logger = logging.getLogger("src.desktop")
    if pkg_logger.handlers:
        return
    from ..logging_config import RedactingFormatter

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        LOG_DIR / "desktop.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    handler.setFormatter(
        RedactingFormatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    pkg_logger.addHandler(handler)
    pkg_logger.setLevel(logging.INFO)


#: 单实例互斥量的句柄。必须活到进程退出：句柄一关，命名互斥量就被内核回收，
#: 第二个实例会误以为自己是首实例。强杀进程时句柄随进程销毁，同样自动释放，
#: 因此不会出现"上次崩溃留下的锁让程序再也起不来"。
_SINGLE_INSTANCE_HANDLE: Any = None

_ERROR_ALREADY_EXISTS = 183

#: 端口被占用窗口的标题（与 docs/TROUBLESHOOTING-USER-20261108.md 的「有桌面时」一致）。
PORT_CONFLICT_TITLE = "鎏灏：网关端口被占用"
PORT_RETRY_LABEL = "关掉占用程序再试"
PORT_SWITCH_LABEL = "换一个端口继续"


def ask_port_choice(parent: tk.Misc, conflict: dict[str, Any]) -> tuple[str, int | None]:
    """端口被占用时给**两个**可点选项（不出现第三个按钮）。

    返回 ``("retry", None)``、``("use", new_port)`` 或 ``("cancel", None)``。
    关闭窗口等同「稍后再处理」：服务仍未启动，状态窗口里可以再次触发。
    """
    from tkinter import simpledialog

    port = int(conflict.get("port") or 0)
    owner = str(conflict.get("description") or describe_port_owner(port))
    result: dict[str, Any] = {"action": "cancel", "port": None}

    dialog = tk.Toplevel(parent)
    dialog.title(PORT_CONFLICT_TITLE)
    dialog.resizable(False, False)
    with contextlib.suppress(tk.TclError):
        dialog.transient(parent)
    with contextlib.suppress(tk.TclError):
        dialog.attributes("-topmost", True)

    tk.Label(
        dialog,
        text=(
            f"网关端口 {port} 已被占用（占用者：{owner}）。\n\n"
            "为避免地址变化，启动器不会自动换端口。请选一个：\n"
            "· 关掉占用程序再试：先关掉上面这个程序（或重启电脑），再点它，"
            "程序会原地继续启动。\n"
            "· 换一个端口继续：填一个空闲端口（例如 8081），校验空闲后直接继续启动，"
            "不用重开窗口。"
        ),
        justify="left",
        anchor="w",
        font=("Microsoft YaHei UI", 10),
        wraplength=480,
    ).pack(fill="x", padx=16, pady=(16, 12))

    row = tk.Frame(dialog)
    row.pack(fill="x", padx=16, pady=(0, 16))

    def retry() -> None:
        result["action"] = "retry"
        dialog.destroy()

    def use_other() -> None:
        suggestion = port + 1 if port < 65535 else 8080
        while True:
            value = simpledialog.askinteger(
                PORT_SWITCH_LABEL,
                "请输入新的服务端口（1–65535）：\n换端口后地址会变，请以窗口里显示的新地址为准。",
                initialvalue=suggestion,
                parent=dialog,
                minvalue=1,
                maxvalue=65535,
            )
            if value is None:
                return  # 回到二选一窗口，不丢用户已做的选择
            if port_is_free(int(value), host="127.0.0.1"):
                result["action"] = "use"
                result["port"] = int(value)
                dialog.destroy()
                return
            other = describe_port_owner(int(value))
            messagebox.showwarning(
                "端口仍被占用",
                f"端口 {value} 也被占用（占用者：{other}），请换一个空闲端口。",
                parent=dialog,
            )

    tk.Button(row, text=PORT_RETRY_LABEL, width=18, command=retry).pack(side="left")
    tk.Button(row, text=PORT_SWITCH_LABEL, width=18, command=use_other).pack(
        side="left", padx=(10, 0)
    )
    dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)

    dialog.update_idletasks()
    # 用请求尺寸而不是当前尺寸：文字按 wraplength 折行后，实际需要的高度
    # 往往还没反映到 winfo_height，直接用会把第二个选项裁掉。
    width = dialog.winfo_reqwidth()
    height = dialog.winfo_reqheight()
    x = max((dialog.winfo_screenwidth() - width) // 2, 0)
    y = max((dialog.winfo_screenheight() - height) // 3, 0)
    dialog.geometry(f"{width}x{height}+{x}+{y}")
    dialog.lift()
    dialog.focus_force()
    dialog.wait_window()
    return result["action"], result["port"]


def single_instance_name(data_dir: Any) -> str:
    """按数据目录派生命名互斥量：同一份数据只允许一个桌面壳。

    用数据目录而不是程序目录做键：测试/多实例并存时各自独立数据目录，互不
    抢占；同一份用户数据则严格单实例，避免两个壳同时启动/停止同一个网关。
    """
    digest = hashlib.sha1(str(data_dir).lower().encode("utf-8")).hexdigest()[:12]
    return f"Local\\LiuHaoDesktopShell_{digest}"


def acquire_single_instance(data_dir: Any) -> tuple[Any, bool]:
    """获取单实例互斥量，返回 ``(句柄, 是否已有实例在运行)``。

    非 Windows 返回 ``(None, False)``（不阻断）。获取失败按"没有互斥量"继续：
    网关自身的实例锁仍是第二道防线，不能因为拿不到互斥量就把程序拒之门外。
    """
    global _SINGLE_INSTANCE_HANDLE
    if os.name != "nt":
        return None, False
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        handle = kernel32.CreateMutexW(None, False, single_instance_name(data_dir))
        if not handle:
            raise OSError(ctypes.get_last_error(), "CreateMutexW 失败")
        already = ctypes.get_last_error() == _ERROR_ALREADY_EXISTS
        _SINGLE_INSTANCE_HANDLE = handle
        return handle, already
    except Exception as exc:  # noqa: BLE001 - 降级为"无互斥量"，不阻断启动
        logger.warning("单实例互斥量不可用（%s），退回由网关实例锁兜底", exc)
        return None, False


def _describe_stop(result: dict[str, Any]) -> str:
    """把停止判定翻译成用户能看懂的一句话（不夸大也不隐瞒）。"""
    if not result.get("stopped"):
        return "服务本来未运行"
    if result.get("forced"):
        return f"超时强杀（{result.get('elapsed')}s，已记录日志）"
    if result.get("inflight_cancelled"):
        return f"已停止，但在途任务因超时被取消（{result.get('elapsed')}s，详见日志）"
    if result.get("graceful"):
        return f"优雅停止（{result.get('elapsed')}s）"
    return f"进程已退出，但未确认优雅收尾（{result.get('elapsed')}s，详见日志）"


def _format_provider(health: dict[str, Any]) -> str:
    body = health.get("body") if health.get("ok") else None
    if not isinstance(body, dict):
        return "Provider：未知（健康接口不可达）"
    provider = body.get("ai_provider") or body.get("provider") or {}
    if isinstance(provider, dict):
        kind = provider.get("type") or provider.get("provider") or "未知"
        mode = provider.get("mode") or provider.get("status") or ""
        return f"Provider：{kind}" + (f"（{mode}）" if mode else "")
    return f"Provider：{provider}"


def _raise_foreground(widget_id: int) -> None:
    """把状态窗口真正提到前台（Windows）。

    ``focus_force`` 只在本进程已经持有前台权时有效；第二次双击是由另一个
    进程（启动器）发来的请求，Windows 的前台锁会把 ``SetForegroundWindow``
    直接忽略，窗口只显示在后面 —— 用户看到的是"点了没反应"。这里临时把
    自己的输入队列挂到当前前台线程，再置顶并设焦点；仍然失败就闪一下任务栏
    按钮，至少让用户看到它在哪。
    """
    try:
        import win32api
        import win32con
        import win32gui
        import win32process
    except Exception:  # noqa: BLE001 - 非 Windows 或缺少 pywin32
        return
    with contextlib.suppress(Exception):
        hwnd = win32gui.GetAncestor(int(widget_id), win32con.GA_ROOT)
        if not hwnd or win32gui.GetForegroundWindow() == hwnd:
            return
        foreground = win32gui.GetForegroundWindow()
        target_tid = (
            win32process.GetWindowThreadProcessId(foreground)[0] if foreground else 0
        )
        current_tid = win32api.GetCurrentThreadId()
        attached = False
        if target_tid and target_tid != current_tid:
            attached = bool(win32process.AttachThreadInput(current_tid, target_tid, True))
        try:
            win32gui.BringWindowToTop(hwnd)
            win32gui.SetForegroundWindow(hwnd)
        finally:
            if attached:
                win32process.AttachThreadInput(current_tid, target_tid, False)
        if win32gui.GetForegroundWindow() != hwnd:
            win32gui.FlashWindow(hwnd, True)


class DesktopApp:
    """装配状态窗口、托盘与网关服务。"""

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int | None = None,
        open_browser: bool = False,
        hidden: bool = False,
    ) -> None:
        self.service = GatewayService(host=host, port=port)
        self.open_browser = open_browser
        self.hidden = hidden
        #: 用户期望的端口；被占用时 ``GatewayService`` 默认拒绝启动并给出原因，
        #: 只有显式自动恢复模式（``allow_port_fallback``）才会换端口。
        self._requested_port = port if port is not None else DEFAULT_PORT
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._stop_event = threading.Event()
        self._closing = False
        self._stopping = False
        self._stop_result: dict[str, Any] | None = None
        self._stop_thread: threading.Thread | None = None
        self._boot_thread: threading.Thread | None = None
        self._health_thread: threading.Thread | None = None
        self._boot_pending = True
        self._boot_result: dict[str, Any] | None = None
        #: 启动失败时探测一次数据目录可写性，用来区分"权限问题"与其它原因。
        self._data_dir_writable: bool | None = None
        #: 端口占用二选一窗口是否已开着（幂等：重复的启动事件不叠窗）。
        self._port_dialog_open = False
        #: 本次启动是否因端口被占而失败（用于恢复成功后写一条 resolved 日志）。
        self._port_conflict_pending = False
        self._diagnostics_path: str | None = None
        self._diagnostics_busy = False
        #: 修复面板（懒加载；None 表示当前没开）。
        self._repair_dialog: Any = None
        self._tray: SystemTray | None = None
        self._root: tk.Tk | None = None
        self._labels: dict[str, tk.Label] = {}
        self._scroll: dpi.ScrollableFrame | None = None
        self._dpi_watcher: dpi.DpiWatcher | None = None
        self._wrapped_labels: list[tuple[tk.Label, int]] = []

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def run(self) -> int:
        _configure_logging()
        # 用户从修复面板改过数据目录：必须在起网关之前应用，否则这一次启动
        # 又会写回旧目录（构建好的环境变量优先，不会被覆盖）。
        try:
            from .repair import apply_saved_data_dir

            applied = apply_saved_data_dir()
            if applied.get("applied"):
                logger.info("已应用保存的数据目录：%s", applied.get("path"))
        except Exception:  # noqa: BLE001 - 配置读不出来不能挡住启动
            logger.warning("应用保存的数据目录失败，按默认目录启动", exc_info=True)
        # 必须在创建任何窗口之前声明 DPI 感知：晚一步 Windows 就已经把整个
        # 进程按 96 DPI 虚拟化了，界面会一直是拉伸后的模糊位图。
        dpi.enable_dpi_awareness()
        # 单实例判定必须在创建窗口/托盘/网关之前：否则第二次双击会多出一个
        # 状态窗口和第二个托盘图标，而且网关锁会让它显示"启动失败"——用户
        # 看到的是失败，真实结论却是"已经在运行"。
        _, already_running = acquire_single_instance(DATA_DIR)
        if already_running:
            self._handoff_to_running()
            return 0
        # 桌面壳的互斥量只挡"第二个桌面壳"，挡不住"命令行先起了网关、随后双击
        # 图标"：那种情况下外壳会去起第二份服务，最终显示"启动失败"，而真实
        # 结论是"已经在运行"。这里先探网关的实例锁，和行为与命令行入口一致：
        # 健康就打开控制台，无响应就给出可执行的人话，都不拉起第二份服务。
        if self._foreign_gateway_holds_data():
            self._handoff_to_running()
            return 0
        self._root = tk.Tk()
        dpi.apply_tk_scaling(self._root)
        self._root.title(APP_NAME)
        self._root.protocol("WM_DELETE_WINDOW", self._hide_to_tray)
        self._build_ui()
        self._apply_geometry(center=True)
        self._dpi_watcher = dpi.DpiWatcher(self._root, self._on_dpi_changed)
        self._dpi_watcher.start()

        self._tray = SystemTray(
            lambda action: self.events.put(("action", action)),
            tooltip=self._tooltip_text(),
            instance_key=str(DATA_DIR),
        )
        if not self._tray.start():
            logger.warning("系统托盘未启动：%s", self._tray.error)
            self._labels["tray"].config(text="托盘：不可用（" + (self._tray.error or "未知原因") + "）")
        else:
            self._labels["tray"].config(text="托盘：已启用（右键菜单可用）")

        # 窗口先显示"正在启动…"，网关启动与启动门放在后台线程：
        # 冷启动期间用户看到的是进度，而不是空白窗口，更不是浏览器的错误页。
        self._labels["service"].config(text="网关：正在启动…")
        self._labels["startup"].config(
            text=f"启动：等待网关端口就绪（最多 {STARTUP_TIMEOUT:.0f} 秒；连接失败按启动中处理）…"
        )
        self._labels["health"].config(text="健康：等待网关端口绑定")

        self._root.after(300, self._drain_events)
        if self.hidden:
            self._root.withdraw()
        self._boot_thread = threading.Thread(target=self._boot, name="liuhao-boot", daemon=True)
        self._boot_thread.start()

        self._root.mainloop()
        return 0 if (self._boot_result or {}).get("started") else 1

    def _boot(self) -> None:
        """后台启动网关并过启动门；结果经 ``events`` 交回 UI 线程。"""
        try:
            started = self.service.start()
        except Exception as exc:  # noqa: BLE001 - 启动异常不能把 UI 线程带下水
            logger.exception("网关启动异常")
            self._boot_result = {
                "started": False,
                "gate": unavailable_gate(f"启动异常：{type(exc).__name__}: {exc}", log_dir=LOG_DIR),
            }
            self.events.put(("boot", self._boot_result))
            return
        if self._closing:
            with contextlib.suppress(Exception):
                self.service.stop()
            return
        if started:
            gate = wait_until_ready(
                self.service.base_url or "",
                timeout=STARTUP_TIMEOUT,
                log_dir=LOG_DIR,
                is_alive=self.service.is_running,
                on_progress=self._on_startup_progress,
            )
        else:
            logger.error("网关启动失败：%s", self.service.last_error)
            gate = unavailable_gate(self.service.last_error, log_dir=LOG_DIR)
        self._boot_result = {"started": started, "gate": gate}
        self.events.put(("boot", self._boot_result))

    def _on_startup_progress(self, snapshot: dict[str, Any]) -> None:
        if not self._closing:
            self.events.put(("startup", snapshot))

    def _render_startup_progress(self, snapshot: dict[str, Any]) -> None:
        if not self._boot_pending or self._closing:
            return
        stage = snapshot.get("last_stage") or (
            "端口尚未监听" if not snapshot.get("reachable") else "等待核心就绪"
        )
        self._labels["startup"].config(
            text=f"启动：{stage}（已 {snapshot.get('elapsed', 0)}s；连接失败按启动中处理）"
        )

    def _apply_boot_result(self, result: dict[str, Any]) -> None:
        """把启动门结论写进界面与托盘（ready / degraded / 超时 / 失败）。"""
        gate = result.get("gate") or {}
        started = bool(result.get("started"))
        self._boot_pending = False
        logger.info("启动门结论：%s", gate)
        if self._stop_result is not None and not started:
            # 用户在启动过程中点了「停止」/「退出」：以停止结论为准，不要
            # 再弹"启动失败"把真实原因盖掉。
            self._labels["startup"].config(text="启动：启动过程被中断（见「最近停止」）")
            self._sync_tray_state(running=False)
            return
        self._labels["startup"].config(text=f"启动：{gate.get('detail') or '未完成'}")
        if not started:
            self._data_dir_writable = self._probe_data_dir_writable()
        else:
            self._data_dir_writable = None
        self._update_service_guidance(running=started)
        self._sync_tray_state(running=started)
        if self._tray is not None and not self._closing:
            self._tray.set_tooltip(self._tooltip_text())

        # 换过端口后地址会变：窗口上的「控制台」必须同步刷新，不能只写在日志里。
        if started:
            if "console" in self._labels:
                self._labels["console"].config(text=f"控制台：{self._console_url()}")
            if self._stop_result is None and "stop" in self._labels:
                # _begin_boot 写的是"正在重新启动"；启动成功后不能留在窗口里。
                self._labels["stop"].config(text="最近停止：—（本次尚未停止过）")
            if self._port_conflict_pending:
                self._port_conflict_pending = False
                self._log_port_event(
                    f"evt=port.resolved port={self.service.port} url={self.service.base_url}"
                )
        else:
            conflict = getattr(self.service, "port_conflict", None)
            if isinstance(conflict, dict) and conflict.get("port"):
                self._port_conflict_pending = True

        if gate.get("console_ok"):
            # 只在"能进门"之后才开始健康轮询，避免启动中把文案覆盖成"未探测"。
            # 重启时复用已有轮询线程，避免多个线程同时往队列里写状态。
            if self._health_thread is None or not self._health_thread.is_alive():
                self._health_thread = threading.Thread(
                    target=self._poll_health, name="liuhao-health", daemon=True
                )
                self._health_thread.start()
            if self.open_browser:
                webbrowser.open(self._console_url_with_proof())

        if self._tray is not None and not self._closing:
            state = gate.get("state")
            detail = str(gate.get("detail") or "")[:250]
            if state == "ready":
                self._tray.notify("鎏灏已就绪", f"服务运行在 {self._console_url()}")
            elif state == "degraded":
                self._tray.notify("鎏灏已启动（部分组件未就绪）", detail, warning=True)
            elif state == "timeout":
                self._tray.notify("鎏灏启动超时", detail, warning=True)
            else:
                self._tray.notify("鎏灏启动失败", detail, warning=True)

        if not started and not self._closing:
            conflict = getattr(self.service, "port_conflict", None)
            if isinstance(conflict, dict) and conflict.get("port"):
                self._prompt_port_conflict(conflict)

    def _probe_data_dir_writable(self) -> bool | None:
        """启动失败时探测数据目录：区分"权限问题"和"代码/端口问题"。"""
        try:
            from ..logging_config import resolve_data_dir

            return bool(probe_directory_writable(resolve_data_dir()).get("writable"))
        except Exception:  # noqa: BLE001 - 探测失败不能影响界面
            logger.warning("数据目录写入探测失败", exc_info=True)
            return None

    def _update_service_guidance(self, *, running: bool) -> None:
        """把状态翻译成"发生了什么 + 下一步做什么"写进窗口。"""
        guidance = service_guidance(
            running=running,
            last_error=self.service.last_error,
            port=self.service.port,
            requested_port=self._requested_port,
            stopped_by_user=self._stop_result is not None,
            data_dir_writable=self._data_dir_writable,
        )
        if "service" in self._labels:
            self._labels["service"].config(text=guidance["service"])
        if "next" in self._labels:
            self._labels["next"].config(text=f"下一步：{guidance['next']}")

    def _build_ui(self) -> None:
        assert self._root is not None
        outer = tk.Frame(self._root, padx=16, pady=12)
        outer.pack(fill=tk.BOTH, expand=True)

        tk.Label(outer, text=APP_NAME, font=("Microsoft YaHei UI", 14, "bold")).pack(anchor="w")

        # 内容区可滚动：高缩放或小屏把窗口钳制在工作区内时，底部按钮仍然
        # 固定可见，只有中间的说明文字滚动。
        self._scroll = dpi.ScrollableFrame(outer)
        self._scroll.frame.pack(fill=tk.BOTH, expand=True, pady=(4, 0))
        content = self._scroll.inner

        for key, label in (
            ("service", "网关："),
            ("next", "下一步："),
            ("startup", "启动："),
            ("health", "健康："),
            ("provider", "Provider："),
            ("console", "控制台："),
            ("data", "数据目录："),
            ("tray", "托盘："),
            ("stop", "最近停止："),
        ):
            row = tk.Label(content, text=label, anchor="w", justify=tk.LEFT, wraplength=460)
            row.pack(anchor="w", pady=1)
            self._labels[key] = row

        self._labels["data"].config(text=f"数据目录：{DATA_DIR}")
        self._labels["console"].config(text=f"控制台：{self._console_url()}")
        self._labels["stop"].config(text="最近停止：—（本次尚未停止过）")
        self._labels["next"].config(text="下一步：正在启动，通常几秒内完成…")

        tk.Label(
            content,
            text="遇到问题：生成诊断包（服务没起来时也能用）",
            font=("Microsoft YaHei UI", 10, "bold"),
            anchor="w",
        ).pack(anchor="w", pady=(10, 0))
        self._labels["diagnostics"] = tk.Label(
            content,
            text=(
                "诊断包：尚未生成。生成后可直接发给技术支持；"
                "里面不含 Key、恢复码，路径中的用户名已隐藏。"
            ),
            anchor="w",
            justify=tk.LEFT,
            wraplength=460,
        )
        self._labels["diagnostics"].pack(anchor="w", pady=1)
        self._labels["diagnostics_hint"] = tk.Label(
            content, text="", fg="#1b6e1b", anchor="w"
        )
        self._labels["diagnostics_hint"].pack(anchor="w")

        buttons = tk.Frame(outer, pady=10)
        buttons.pack(anchor="w")
        # 文案复用 ``tray.LABEL_*``：托盘菜单与窗口按钮是同一功能的两处入口，
        # 不允许各写各的说法（历史问题：「停止服务」vs「停止服务（仅停网关…）」）。
        primary = tk.Frame(buttons)
        primary.pack(anchor="w")
        tk.Button(primary, text=LABEL_OPEN_CONSOLE, command=self._open_console).pack(side=tk.LEFT, padx=(0, 8))
        tk.Button(primary, text=LABEL_OPEN_LOGS, command=self._open_logs).pack(side=tk.LEFT, padx=(0, 8))
        tk.Button(primary, text="重新启动服务", command=self._restart_service).pack(side=tk.LEFT, padx=(0, 8))
        tk.Button(primary, text="隐藏到托盘", command=self._hide_to_tray).pack(side=tk.LEFT)

        lifecycle = tk.Frame(buttons)
        lifecycle.pack(anchor="w", pady=(8, 0))
        tk.Button(lifecycle, text=LABEL_STOP_SERVICE, command=self._stop_service_and_show).pack(
            side=tk.LEFT, padx=(0, 8)
        )
        tk.Button(lifecycle, text=LABEL_QUIT, command=self._quit).pack(side=tk.LEFT)

        diagnostics_buttons = tk.Frame(buttons)
        diagnostics_buttons.pack(anchor="w", pady=(8, 0))
        self._diagnostics_button = tk.Button(
            diagnostics_buttons, text="生成诊断包", command=self._generate_diagnostics
        )
        self._diagnostics_button.pack(side=tk.LEFT, padx=(0, 8))
        self._copy_path_button = tk.Button(
            diagnostics_buttons,
            text="复制路径",
            state=tk.DISABLED,
            command=self._copy_diagnostics_path,
        )
        self._copy_path_button.pack(side=tk.LEFT, padx=(0, 8))
        # 诊断包回答"哪里坏了"，修复面板回答"点一下怎么好"：两个入口并排，
        # 且都不依赖网关是否存活。
        tk.Button(diagnostics_buttons, text="一键修复…", command=self._open_repair_dialog).pack(
            side=tk.LEFT
        )

        tk.Label(
            content,
            text="关闭窗口 = 最小化到托盘，网关继续运行。真正退出请用托盘菜单「退出」或上面的「退出程序」。",
            fg="#555555",
            wraplength=460,
            justify=tk.LEFT,
        ).pack(anchor="w", pady=(8, 0))
        if not (CONSOLE_DIST / "index.html").is_file():
            tk.Label(
                content,
                text=f"注意：未找到驾驶舱构建产物 {CONSOLE_DIST}，控制台页面会 404（API 仍可用）。",
                fg="#a33a00",
                wraplength=460,
                justify=tk.LEFT,
            ).pack(anchor="w", pady=(6, 0))

        # 按钮区优先占位：窗口被压到工作区高度时先压缩可滚动内容，不切按钮。
        self._scroll.frame.pack_forget()
        buttons.pack_forget()
        buttons.pack(side=tk.BOTTOM, fill=tk.X)
        self._scroll.frame.pack(fill=tk.BOTH, expand=True, pady=(4, 0))

        self._wrapped_labels = self._collect_wraplabels()

    def _collect_wraplabels(self) -> list[tuple[tk.Label, int]]:
        """记录所有自动换行标签的 96 DPI 基准宽度，供缩放时等比调整。"""
        assert self._root is not None
        collected: list[tuple[tk.Label, int]] = []
        stack: list[Any] = [self._root]
        while stack:
            widget = stack.pop()
            stack.extend(widget.winfo_children())
            try:
                base = int(widget.cget("wraplength"))
            except Exception:  # noqa: BLE001 - 控件没有 wraplength 选项
                continue
            if base > 0:
                collected.append((widget, base))
        return collected

    def _apply_wraplength(self, dpi_value: int) -> None:
        for widget, base in self._wrapped_labels:
            try:
                widget.configure(wraplength=dpi.scaled(base, dpi_value))
            except Exception:  # noqa: BLE001 - 控件可能已销毁
                continue

    def _apply_geometry(self, *, center: bool, dpi_value: int | None = None) -> None:
        """按内容自然尺寸 + 当前显示器工作区开窗（超高时内容区滚动）。"""
        if self._root is None:
            return
        try:
            current = int(dpi_value or dpi.window_dpi(self._root.winfo_id()))
            self._apply_wraplength(current)
            if self._scroll is not None:
                self._scroll.prepare()
            dpi.fit_window(
                self._root, min_width=460, min_height=320, dpi=current, center=center
            )
        except Exception:  # noqa: BLE001 - 几何计算失败不能挡住启动
            logger.warning("窗口自适应尺寸失败，退回默认尺寸", exc_info=True)
            self._root.geometry("500x360+120+120")

    def _on_dpi_changed(self, dpi_value: int) -> None:
        self._apply_wraplength(dpi_value)
        if self._root is not None:
            self._root.after_idle(
                lambda: self._apply_geometry(center=False, dpi_value=dpi_value)
            )

    # ------------------------------------------------------------------ #
    # 动作
    # ------------------------------------------------------------------ #
    def _console_url(self) -> str:
        return (self.service.base_url or "http://127.0.0.1:8080").rstrip("/") + "/"

    def _with_local_proof(self, url: str) -> str:
        """把本机恢复码交接的一次性令牌放进 URL fragment（不进日志/历史）。"""
        try:
            from ..common.local_proof_ipc import console_fragment

            separator = "&" if "#" in url else "#"
            return f"{url}{separator}{console_fragment()}"
        except Exception as exc:  # noqa: BLE001 - 打不开证明不应阻止打开控制台
            logger.warning(
                "本机恢复证明通道不可用；控制台仍可打开，但“忘记密码 → 本机生成恢复码”"
                "会要求从桌面端重新进入：%s",
                exc,
            )
            return url

    def _console_url_with_proof(self) -> str:
        return self._with_local_proof(self._console_url())

    def _tooltip_text(self) -> str:
        if self._boot_pending:
            return f"{APP_NAME} · 正在启动…"
        if self.service.is_running():
            return f"{APP_NAME} · 运行中 · {self._console_url()}"
        if self.service.last_error:
            return f"{APP_NAME} · 启动失败（详见桌面窗口）"
        return f"{APP_NAME} · 服务已停止"

    def _open_console(self) -> None:
        webbrowser.open(self._console_url_with_proof())

    def _handoff_to_running(self) -> None:
        """第二个桌面实例：唤起已运行的状态窗口 + 给用户一句人话。

        三个层次的降级，全部失败也不会静默退出：
        1. 请已运行的托盘窗口显示状态窗口（等价于点菜单「查看状态」）；
        2. 找不到窗口（例如上一实例正处在启动竞态里）→ 打开它已发布的控制台地址；
        3. 两者都没有 → 明确告诉用户去通知区找图标。

        与 ``scripts/start_liuhao.py`` 的双开分支保持同一套判断：服务活着 /
        服务卡死 / 完全没有发布地址，三种情况说的话不同，且都不启动第二个服务。
        ``--hidden``（开机自启重复拉起）只写日志：登录画面上不该弹模态框。
        """
        key = str(DATA_DIR)
        shown = request_show(key, timeout=0.0)
        url = self._published_console_url()
        healthy = bool(url) and self._service_healthy(url)
        if shown:
            if healthy:
                summary = "鎏灏已经在运行，已为你切换到正在运行的状态窗口。"
            else:
                summary = (
                    "鎏灏已经在运行，但服务暂时没有响应。\n\n"
                    "已为你切换到状态窗口，可在其中查看状态或重新启动服务；"
                    "本次没有启动第二个服务。"
                )
        elif healthy:
            summary = f"鎏灏已经在运行，已为你打开控制台：{url}"
            with contextlib.suppress(Exception):
                webbrowser.open(self._with_local_proof(url))
        elif url:
            pid = self._published_holder().get("pid")
            who = f"（pid={pid}）" if pid else ""
            summary = (
                f"鎏灏已经在运行，但服务暂时没有响应{who}。\n\n"
                "请在任务管理器结束该进程，然后重新打开鎏灏；"
                "本次没有启动第二个服务。"
            )
        else:
            summary = "鎏灏已经在运行。请使用任务栏通知区（托盘）的图标打开窗口或控制台。"
        logger.info(
            "已有鎏灏实例在运行：已唤起窗口=%s，服务可访问=%s，地址=%s",
            shown,
            healthy,
            url or "-",
        )
        print(f"[instance] {summary}", flush=True)
        if self.hidden:
            return
        with contextlib.suppress(Exception):
            if shown or healthy:
                messagebox.showinfo("鎏灏已在运行", summary)
            else:
                messagebox.showwarning("鎏灏已在运行（服务无响应）", summary)

    @staticmethod
    def _service_healthy(url: str) -> bool:
        """探测已发布地址的 ``/v1/health``，区分"在跑"与"卡死"。"""
        from urllib.parse import urlsplit, urlunsplit

        parts = urlsplit(url)
        host = parts.hostname or "127.0.0.1"
        if host in ("0.0.0.0", "::", "::0"):
            # 通配绑定无法被浏览器/探测器直接连接，按回环探测。
            host = "127.0.0.1"
        netloc = f"{host}:{parts.port}" if parts.port else host
        target = urlunsplit((parts.scheme or "http", netloc, "/v1/health", "", ""))
        info = http_json(target, timeout=2.0)
        body = info.get("body")
        # 只认鎏灏自己的网关：端口上可能是别的程序，2xx 不代表"我们的服务活着"。
        return bool(info.get("ok")) and isinstance(body, dict) and body.get("service") == "liuhao-gateway"

    def _published_holder(self) -> dict[str, Any]:
        """已运行网关公布的元信息（``run/instance.json``），没有就返回空 dict。

        路径必须与网关子进程写元信息的位置一致：``instance_root(服务的 root)``。
        源码直跑时桌面壳的数据目录是 ``<repo>/data``，而网关的实例根是
        ``<repo>``（子进程 cwd），写死 ``DATA_DIR`` 会永远读不到，把"服务正常"
        误报成"服务无响应"。
        """
        from ..common.instance_lock import instance_root

        meta = instance_root(self.service.root) / "run" / "instance.json"
        try:
            payload = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _published_console_url(self) -> str:
        url = str(self._published_holder().get("url") or "").strip()
        return url.rstrip("/") + "/" if url else ""

    def _foreign_gateway_holds_data(self) -> bool:
        """同一份数据目录是否已有网关在跑（本进程尚未启动网关）。

        用锁而不是元信息文件判定：文件可以被伪造，锁不能。探测会短暂持有锁
        随即释放，不会与真正的启动流程自锁。
        """
        from ..common.instance_lock import InstanceLock, instance_root

        try:
            return InstanceLock(instance_root(self.service.root)).probe() is not None
        except Exception:  # noqa: BLE001 - 探测失败按"没有"处理，交给启动流程报错
            logger.warning("网关实例锁探测失败", exc_info=True)
            return False

    def _open_logs(self) -> None:
        """打开日志目录（排障入口）：路径不存在就先建出来，打不开则给出路径。"""
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            if os.name == "nt":
                os.startfile(str(LOG_DIR))  # noqa: S606 - Windows 打开资源管理器
            else:
                webbrowser.open(LOG_DIR.as_uri())
        except OSError as exc:
            logger.warning("打开日志目录失败：%s", exc)
            with contextlib.suppress(Exception):
                messagebox.showinfo(
                    "无法自动打开日志目录",
                    f"日志目录：{LOG_DIR}\n请手动复制上面的路径打开。",
                )

    def _hide_to_tray(self) -> None:
        if self._root is None:
            return
        self._root.withdraw()
        if self._tray is not None:
            self._tray.set_tooltip(self._tooltip_text())

    def _show_window(self) -> None:
        if self._root is None:
            return
        self._root.deiconify()
        # deiconify 只是把"显示"排进队列；同一轮里立刻 lift/focus_force 会被
        # 当成对尚未映射窗口的请求而失效 —— 实测窗口会显示出来，但仍压在别的
        # 窗口后面。先跑一次 idle 让映射生效，再置顶。
        self._root.update_idletasks()
        self._root.lift()
        self._root.focus_force()
        if os.name == "nt":
            _raise_foreground(self._root.winfo_id())

    def _toggle_window(self) -> None:
        if self._root is None:
            return
        if self._root.state() == "normal":
            self._hide_to_tray()
        else:
            self._show_window()

    def _restart_service(self) -> None:
        """服务被杀 / 启动失败后，给用户一个当场可点的恢复动作。"""
        if self._closing or self._boot_pending or self._stopping:
            return
        if self.service.is_running():
            if "next" in self._labels:
                self._labels["next"].config(text="下一步：网关已经在运行，无需重新启动。")
            return
        logger.info("用户从桌面壳请求重新启动网关")
        self._begin_boot(note="正在重新启动")

    def _begin_boot(self, *, note: str) -> None:
        """在**当前窗口内**重新启动网关（不要求用户关掉窗口重开）。"""
        if self._closing or self._boot_pending or self._stopping:
            return
        self._boot_pending = True
        self._boot_result = None
        self._stop_result = None
        self._data_dir_writable = None
        if "service" in self._labels:
            self._labels["service"].config(text=f"网关：{note}…")
        if "startup" in self._labels:
            self._labels["startup"].config(text="启动：等待网关端口就绪…")
        if "next" in self._labels:
            self._labels["next"].config(text=f"下一步：{note}，通常几秒内完成…")
        if "stop" in self._labels:
            self._labels["stop"].config(text="最近停止：—（正在重新启动）")
        self._boot_thread = threading.Thread(target=self._boot, name="liuhao-boot", daemon=True)
        self._boot_thread.start()

    def _log_port_event(self, message: str) -> None:
        """端口冲突处理事件：同时写 ``desktop.log`` 与 ``launcher.log``。

        排障文档让用户先在 ``launcher.log`` 里搜「已被占用」；托盘壳若只写
        自己的日志，用户按文档找不到线索，所以两处都留。
        """
        logger.info(message)
        try:
            from datetime import datetime

            from ..logging_config import RotatingTextWriter

            LOG_DIR.mkdir(parents=True, exist_ok=True)
            RotatingTextWriter(str(LOG_DIR / "launcher.log")).write(
                f"{datetime.now().isoformat(timespec='seconds')} {message}\n"
            )
        except Exception:  # noqa: BLE001 - 日志写不进去不能挡住交互
            logger.warning("端口事件写入 launcher.log 失败", exc_info=True)

    def _prompt_port_conflict(self, conflict: dict[str, Any]) -> None:
        """端口被占用时弹二选一；两个动作都在当前窗口内原地恢复。"""
        if self._closing or self._root is None or self._port_dialog_open:
            return
        self._port_dialog_open = True
        self._log_port_event(
            f"evt=port.prompt port={conflict.get('port')} owner={conflict.get('description')}"
        )
        try:
            if self._root.state() == "withdrawn":
                self._show_window()
            action, new_port = ask_port_choice(self._root, conflict)
        except Exception:  # noqa: BLE001 - 窗口打不开时至少保留状态窗口里的说明
            logger.exception("端口占用二选一窗口打开失败")
            return
        finally:
            self._port_dialog_open = False

        if self._closing:
            return
        old_port = int(conflict.get("port") or 0)
        if action == "retry":
            self._log_port_event(f"evt=port.retry port={old_port} action=retry")
            self._begin_boot(note=f"正在重新检测端口 {old_port}")
            return
        if action == "use" and new_port:
            self._switch_port(int(new_port), old_port=old_port)
            return
        self._log_port_event(f"evt=port.deferred port={old_port} action=later")
        if "next" in self._labels:
            self._labels["next"].config(
                text=(
                    f"下一步：端口 {old_port} 仍被占用，服务未启动。准备好后点"
                    "「重新启动服务」，会再次给出「关掉占用程序再试」和「换一个端口继续」。"
                )
            )

    def _switch_port(self, new_port: int, *, old_port: int) -> None:
        """用户选了「换一个端口继续」：原地换端口并重启，不关窗口。"""
        if self.service.is_running():
            return
        self.service.port = new_port
        self._requested_port = new_port
        # 与首启向导同一口径：向导/托盘/重启都读这个变量，不能只改内存。
        os.environ["LIUHAO_API_PORT"] = str(new_port)
        self._log_port_event(f"evt=port.switch old={old_port} new={new_port} action=use")
        if "console" in self._labels:
            self._labels["console"].config(
                text=f"控制台：http://127.0.0.1:{new_port}/（正在启动…）"
            )
        self._begin_boot(note=f"正在改用端口 {new_port}")

    def _generate_diagnostics(self) -> None:
        """生成诊断包；完全离线，不依赖网关是否存活。"""
        if self._diagnostics_busy:
            return
        self._diagnostics_busy = True
        if "diagnostics" in self._labels:
            self._labels["diagnostics"].config(
                text="诊断包：正在收集信息（服务没起来也可以生成，通常几秒）…"
            )
        if "diagnostics_hint" in self._labels:
            self._labels["diagnostics_hint"].config(text="")
        with contextlib.suppress(Exception):
            self._diagnostics_button.config(state=tk.DISABLED)
        with contextlib.suppress(Exception):
            self._copy_path_button.config(state=tk.DISABLED)
        threading.Thread(
            target=self._diagnostics_worker, name="liuhao-diagnostics", daemon=True
        ).start()

    def _diagnostics_worker(self) -> None:
        try:
            from .diagnostics import create_bundle

            # 带上本应用实际使用的端口：包里"服务是否在运行"只对本服务负责，
            # 不被同一台机器上的其它网关带偏。
            ports = sorted({port for port in (self.service.port, self._requested_port) if port})
            omitted: list[dict[str, Any]] = []
            path = create_bundle(
                base_url=self.service.base_url, ports=ports or None, omitted_out=omitted
            )
            payload: dict[str, Any] = {
                "ok": True,
                "path": str(path),
                "size": path.stat().st_size,
                "omitted": [str(item.get("name")) for item in omitted],
            }
        except Exception as exc:  # noqa: BLE001 - 失败也要以中文回到界面
            logger.exception("生成诊断包失败")
            payload = {"ok": False, "message": f"{type(exc).__name__}: {exc}"}
        self.events.put(("diagnostics", payload))

    def _apply_diagnostics_result(self, payload: dict[str, Any]) -> None:
        self._diagnostics_busy = False
        with contextlib.suppress(Exception):
            self._diagnostics_button.config(state=tk.NORMAL)
        if payload.get("ok"):
            self._diagnostics_path = str(payload.get("path") or "")
            size = format_bytes(payload.get("size") or 0)
            omitted = [str(name) for name in payload.get("omitted") or []]
            if omitted:
                # 说清少了哪一类文件，别让用户只看到一个数字。
                logs = sum(1 for name in omitted if name.startswith("logs/"))
                others = len(omitted) - logs
                parts = []
                if logs:
                    parts.append(f"{logs} 个日志文件")
                if others:
                    parts.append(f"{others} 个诊断文件")
                note = (
                    f"其中 {'和 '.join(parts)}因含密钥未包含，"
                    "清单见包内 REDACTED-MEMBERS.txt。"
                )
            else:
                note = "里面不含 Key、恢复码，路径中的用户名已隐藏。"
            if "diagnostics" in self._labels:
                self._labels["diagnostics"].config(
                    text=(
                        f"诊断包：{self._diagnostics_path}（{size}）。"
                        f"可直接发给技术支持；{note}"
                    )
                )
            with contextlib.suppress(Exception):
                self._copy_path_button.config(state=tk.NORMAL)
            return
        if "diagnostics" in self._labels:
            self._labels["diagnostics"].config(
                text=(
                    f"诊断包：生成失败（{payload.get('message') or '未知原因'}）。"
                    f"若已按提示处理仍失败，请把 logs 目录（{LOG_DIR}）打包发给技术支持。"
                )
            )

    def _copy_diagnostics_path(self) -> None:
        if self._root is None or not self._diagnostics_path:
            return
        self._root.clipboard_clear()
        self._root.clipboard_append(self._diagnostics_path)
        self._root.update_idletasks()
        if "diagnostics_hint" in self._labels:
            self._labels["diagnostics_hint"].config(text="路径已复制到剪贴板。")

    def _open_repair_dialog(self) -> None:
        """打开修复面板：六类故障各一个可点动作（服务没起来时同样可用）。"""
        if self._root is None:
            return
        from .repair_ui import open_repair_dialog

        open_repair_dialog(self)

    def _open_url(self, url: str) -> None:
        webbrowser.open(url)

    def _on_data_dir_changed(self, path: str) -> None:
        """修复面板改过数据目录后同步窗口上的展示，避免两处说法不一致。"""
        if "data" in self._labels and path:
            self._labels["data"].config(text=f"数据目录：{path}（本次已切换，网关已按新目录重启）")

    def _stop_service_and_show(self) -> None:
        """「停止服务」的唯一实现：托盘菜单与窗口按钮共用，行为必须一致。"""
        self._stop_service()
        self._show_window()

    def _stop_service(self) -> None:
        """停止网关（UI 线程只负责发起，最多 10s 的等待放到后台线程）。"""
        if self._stopping:
            return
        if not self.service.is_running():
            self._refresh_status_text(running=False)
            if "stop" in self._labels:
                self._labels["stop"].config(text="最近停止：服务本来未运行")
            return
        self._stopping = True
        if "stop" in self._labels:
            self._labels["stop"].config(
                text=f"最近停止：正在优雅停止（最多 {GRACEFUL_TIMEOUT:.0f} 秒，超时才强杀）…"
            )
        logger.info("用户从桌面壳请求停止网关")
        self._stop_thread = threading.Thread(target=self._stop_worker, name="liuhao-stop", daemon=True)
        self._stop_thread.start()

    def _stop_worker(self) -> None:
        result = self.service.stop()
        # 先落结果再入队：用户点「停止」后立刻点「退出」时，退出路径能复用
        # 同一次停止结果，不会把"已优雅停止"覆盖成"服务本来未运行"。
        self._stop_result = result
        self.events.put(("stopped", result))

    def _apply_stop_result(self, result: dict[str, Any]) -> None:
        """展示并记录停止结果（优雅 / 超时强杀 / 未确认收尾）。"""
        self._stop_result = result
        logger.info("网关停止结果：%s", result)
        if "stop" in self._labels:
            self._labels["stop"].config(text=f"最近停止：{_describe_stop(result)}")
        self._refresh_status_text(running=False)
        if self._tray is not None and not self._closing:
            self._tray.set_tooltip(f"{APP_NAME} · 服务已停止（托盘仍在运行）")
            if result.get("forced"):
                self._tray.notify(
                    "服务已强制停止",
                    f"优雅停止超时 {GRACEFUL_TIMEOUT:.0f}s，已强制结束；详见 logs/desktop.log",
                    warning=True,
                )
            elif result.get("inflight_cancelled"):
                self._tray.notify(
                    "服务已停止（有任务被取消）",
                    "在途任务超过宽限时间被取消，已记录日志；详见 logs/desktop-gateway.log",
                    warning=True,
                )
            elif result.get("graceful"):
                self._tray.notify("服务已停止", f"已优雅退出（{result.get('elapsed')}s）")

    def _quit(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._stop_event.set()
        boot = self._boot_thread
        if boot is not None and boot.is_alive():
            # 启动线程若还在 start()/启动门里，给它一点时间退出；它自己会看到
            # _closing，并在 start() 返回后停掉刚拉起的网关（不留孤儿进程）。
            boot.join(timeout=2.0)
        if "stop" in self._labels:
            self._labels["stop"].config(
                text=f"最近停止：正在优雅退出（最多 {GRACEFUL_TIMEOUT:.0f} 秒）…"
            )
        if self._root is not None:
            with contextlib.suppress(Exception):
                self._root.update()
        # 若后台线程已在停止，等它完成并复用结果，避免二次 stop() 覆盖真实结论。
        thread = self._stop_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=GRACEFUL_TIMEOUT + 2)
        self._stopping = False
        result = self._stop_result if self._stop_result is not None else self.service.stop()
        self._apply_stop_result(result)
        if (result.get("forced") or result.get("inflight_cancelled")) and self._root is not None:
            with contextlib.suppress(Exception):
                if result.get("forced"):
                    title, body = (
                        "服务已强制停止",
                        f"服务未在 {GRACEFUL_TIMEOUT:.0f} 秒内自行退出，已强制结束。\n\n"
                        "这意味着可能有在途任务被中断，详情见：\n",
                    )
                else:
                    title, body = (
                        "服务已停止（有任务被取消）",
                        "退出时有在途任务超过宽限时间被取消。\n\n详情见：\n",
                    )
                messagebox.showwarning(
                    title,
                    body + f"{LOG_DIR}\\desktop.log\n{LOG_DIR}\\desktop-gateway.log",
                )
        if self._tray is not None:
            self._tray.stop()
        if self._dpi_watcher is not None:
            # 先还原窗口过程再销毁窗口：避免销毁过程中回调进已卸载的 Python 状态。
            self._dpi_watcher.stop()
        if self._root is not None:
            with contextlib.suppress(Exception):
                self._root.destroy()

    # ------------------------------------------------------------------ #
    # 状态轮询
    # ------------------------------------------------------------------ #
    def _poll_health(self) -> None:
        while not self._stop_event.is_set():
            base = self.service.base_url
            if not base or not self.service.is_running():
                self.events.put(("status", {"running": False, "health": None, "ready": None}))
            else:
                health = http_json(f"{base}/v1/health", timeout=1.0)
                ready = http_json(f"{base}/v1/ready", timeout=1.0)
                self.events.put(("status", {"running": True, "health": health, "ready": ready}))
            self._stop_event.wait(3.0)

    def _drain_events(self) -> None:
        if self._root is None:
            return
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "status":
                    self._render_status(payload)
                elif kind == "startup":
                    self._render_startup_progress(payload)
                elif kind == "boot":
                    self._apply_boot_result(payload)
                elif kind == "stopped":
                    self._stopping = False
                    self._apply_stop_result(payload)
                elif kind == "diagnostics":
                    self._apply_diagnostics_result(payload)
                elif kind == "action":
                    self._handle_action(str(payload))
        except queue.Empty:
            pass
        self._root.after(300, self._drain_events)

    def _handle_action(self, action: str) -> None:
        if action == ACTION_OPEN_CONSOLE:
            self._open_console()
        elif action == ACTION_SHOW_STATUS:
            self._show_window()
        elif action == ACTION_TOGGLE_STATUS:
            self._toggle_window()
        elif action == ACTION_STOP_SERVICE:
            self._stop_service_and_show()
        elif action == ACTION_OPEN_LOGS:
            self._open_logs()
        elif action == ACTION_QUIT:
            self._quit()

    def _render_status(self, payload: dict[str, Any]) -> None:
        health = payload.get("health")
        ready = payload.get("ready")
        running = bool(payload.get("running")) and isinstance(health, dict) and bool(health.get("ok"))
        self._refresh_status_text(running=running, health=health, ready=ready)
        if self._tray is not None and not self._closing:
            self._tray.set_tooltip(self._tooltip_text())

    def _sync_tray_state(self, *, running: bool) -> None:
        """托盘图标三态：运行中 / 有错误 / 已停止（用户一眼看得出）。"""
        if self._tray is None or self._stopping or self._closing:
            return
        if running:
            state = STATE_RUNNING
        elif self.service.is_running() or self.service.last_error:
            state = STATE_ERROR
        else:
            state = STATE_STOPPED
        self._tray.set_state(state)

    def _refresh_status_text(
        self,
        *,
        running: bool,
        health: dict[str, Any] | None = None,
        ready: dict[str, Any] | None = None,
    ) -> None:
        if "service" not in self._labels:
            return
        self._sync_tray_state(running=running)
        self._update_service_guidance(running=running)
        if isinstance(health, dict):
            status = health.get("status")
            self._labels["health"].config(
                text=f"健康：/v1/health={status if status is not None else '不可达'}"
                + (
                    f"，/v1/ready={ready.get('status') if isinstance(ready, dict) else '不可达'}"
                    if ready is not None
                    else ""
                )
            )
            self._labels["provider"].config(text=_format_provider(health))
        else:
            self._labels["health"].config(text="健康：未探测")
            self._labels["provider"].config(text="Provider：未知（健康接口不可达）")


__all__ = ["DesktopApp"]
