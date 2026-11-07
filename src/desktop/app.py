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

from . import CONSOLE_DIST, DATA_DIR, LOG_DIR
from .about import APP_NAME
from .probe import http_json
from .service import GRACEFUL_TIMEOUT, GatewayService
from .startup import STARTUP_TIMEOUT, unavailable_gate, wait_until_ready
from .tray import (
    ACTION_OPEN_CONSOLE,
    ACTION_OPEN_LOGS,
    ACTION_QUIT,
    ACTION_SHOW_STATUS,
    ACTION_STOP_SERVICE,
    ACTION_TOGGLE_STATUS,
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
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._stop_event = threading.Event()
        self._closing = False
        self._stopping = False
        self._stop_result: dict[str, Any] | None = None
        self._stop_thread: threading.Thread | None = None
        self._boot_thread: threading.Thread | None = None
        self._boot_pending = True
        self._boot_result: dict[str, Any] | None = None
        self._tray: SystemTray | None = None
        self._root: tk.Tk | None = None
        self._labels: dict[str, tk.Label] = {}

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def run(self) -> int:
        _configure_logging()
        # 单实例判定必须在创建窗口/托盘/网关之前：否则第二次双击会多出一个
        # 状态窗口和第二个托盘图标，而且网关锁会让它显示"启动失败"——用户
        # 看到的是失败，真实结论却是"已经在运行"。
        _, already_running = acquire_single_instance(DATA_DIR)
        if already_running:
            self._handoff_to_running()
            return 0
        self._root = tk.Tk()
        self._root.title(APP_NAME)
        self._root.geometry("500x360+120+120")
        self._root.protocol("WM_DELETE_WINDOW", self._hide_to_tray)
        self._build_ui()

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
        if started:
            self._labels["service"].config(text=f"网关：运行中（127.0.0.1:{self.service.port}）")
        else:
            self._labels["service"].config(text=f"网关：启动失败（{self.service.last_error or '未知原因'}）")
        self._sync_tray_state(running=started)
        if self._tray is not None and not self._closing:
            self._tray.set_tooltip(self._tooltip_text())

        if gate.get("console_ok"):
            # 只在"能进门"之后才开始健康轮询，避免启动中把文案覆盖成"未探测"。
            threading.Thread(target=self._poll_health, name="liuhao-health", daemon=True).start()
            if self.open_browser:
                webbrowser.open(self._console_url_with_proof())

        if self._tray is None or self._closing:
            return
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

    def _build_ui(self) -> None:
        assert self._root is not None
        frame = tk.Frame(self._root, padx=16, pady=12)
        frame.pack(fill=tk.BOTH, expand=True)

        tk.Label(frame, text=APP_NAME, font=("Microsoft YaHei UI", 14, "bold")).pack(anchor="w")
        for key, label in (
            ("service", "网关："),
            ("startup", "启动："),
            ("health", "健康："),
            ("provider", "Provider："),
            ("console", "控制台："),
            ("data", "数据目录："),
            ("tray", "托盘："),
            ("stop", "最近停止："),
        ):
            row = tk.Label(frame, text=label, anchor="w", justify=tk.LEFT, wraplength=460)
            row.pack(anchor="w", pady=1)
            self._labels[key] = row

        self._labels["data"].config(text=f"数据目录：{DATA_DIR}")
        self._labels["console"].config(text=f"控制台：{self._console_url()}")
        self._labels["stop"].config(text="最近停止：—（本次尚未停止过）")

        buttons = tk.Frame(frame, pady=10)
        buttons.pack(anchor="w")
        tk.Button(buttons, text="打开控制台", command=self._open_console).pack(side=tk.LEFT, padx=(0, 8))
        tk.Button(buttons, text="隐藏到托盘", command=self._hide_to_tray).pack(side=tk.LEFT, padx=(0, 8))
        tk.Button(buttons, text="停止服务", command=self._stop_service).pack(side=tk.LEFT, padx=(0, 8))
        tk.Button(buttons, text="退出程序", command=self._quit).pack(side=tk.LEFT)

        tk.Label(
            frame,
            text="关闭窗口 = 最小化到托盘，网关继续运行。真正退出请用托盘菜单「退出」或上面的「退出程序」。",
            fg="#555555",
            wraplength=460,
            justify=tk.LEFT,
        ).pack(anchor="w", pady=(8, 0))
        if not (CONSOLE_DIST / "index.html").is_file():
            tk.Label(
                frame,
                text=f"注意：未找到驾驶舱构建产物 {CONSOLE_DIST}，控制台页面会 404（API 仍可用）。",
                fg="#a33a00",
                wraplength=460,
                justify=tk.LEFT,
            ).pack(anchor="w", pady=(6, 0))

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
            return "鎏灏 · 正在启动…"
        if self.service.is_running():
            return f"鎏灏 · 运行中 · {self._console_url()}"
        if self.service.last_error:
            return "鎏灏 · 启动失败（详见桌面窗口）"
        return "鎏灏 · 服务已停止"

    def _open_console(self) -> None:
        webbrowser.open(self._console_url_with_proof())

    def _handoff_to_running(self) -> None:
        """第二个桌面实例：唤起已运行的状态窗口，而不是再起一套网关。

        三个层次的降级，全部失败也不会静默退出：
        1. 请已运行的托盘窗口显示状态窗口（等价于点菜单「查看状态」）；
        2. 找不到窗口（例如上一实例正处在启动竞态里）→ 打开它已发布的控制台地址；
        3. 两者都没有 → 弹一句人话，告诉用户去通知区找图标。
        """
        if request_show(str(DATA_DIR)):
            logger.info("已有鎏灏桌面实例在运行：已唤起其状态窗口，本进程直接退出")
            return
        url = self._published_console_url()
        if url:
            logger.info("已有鎏灏实例在运行，但未能唤起窗口：打开控制台 %s", url)
            webbrowser.open(self._with_local_proof(url))
            return
        logger.info("已有鎏灏实例在运行，但既找不到窗口也没有已发布地址")
        with contextlib.suppress(Exception):
            messagebox.showinfo(
                "鎏灏已在运行",
                "鎏灏已经在运行。请使用任务栏通知区（托盘）的图标打开窗口或控制台。",
            )

    def _published_console_url(self) -> str:
        """读取已运行实例发布在数据目录里的控制台地址（没有就返回空串）。"""
        try:
            payload = json.loads((DATA_DIR / "run" / "instance.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return ""
        url = str(payload.get("url") or "").strip()
        return url.rstrip("/") + "/" if url else ""

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
        self._root.lift()
        self._root.focus_force()

    def _toggle_window(self) -> None:
        if self._root is None:
            return
        if self._root.state() == "normal":
            self._hide_to_tray()
        else:
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
            self._tray.set_tooltip("鎏灏 · 服务已停止（托盘仍在运行）")
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
            self._stop_service()
            self._show_window()
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
        if running:
            self._labels["service"].config(text=f"网关：运行中（127.0.0.1:{self.service.port}）")
        elif self.service.last_error:
            self._labels["service"].config(text=f"网关：启动失败（{self.service.last_error}）")
        else:
            self._labels["service"].config(text="网关：已停止")
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
