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
"""

from __future__ import annotations

import logging
import queue
import threading
import tkinter as tk
import webbrowser
from logging.handlers import RotatingFileHandler
from typing import Any

from . import CONSOLE_DIST, DATA_DIR, LOG_DIR
from .probe import http_json
from .service import GatewayService
from .tray import (
    ACTION_OPEN_CONSOLE,
    ACTION_QUIT,
    ACTION_SHOW_STATUS,
    ACTION_STOP_SERVICE,
    ACTION_TOGGLE_STATUS,
    SystemTray,
)

logger = logging.getLogger(__name__)


def _configure_logging() -> None:
    """托盘壳自己的日志：10 MiB × 5 份轮转（与网关同一套参数）。

    只挂在本模块 logger 上；网关是独立子进程，走 ``config/logging.yaml``。
    """
    if logger.handlers:
        return
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        LOG_DIR / "desktop.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


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
        host: str = "0.0.0.0",
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
        self._tray: SystemTray | None = None
        self._root: tk.Tk | None = None
        self._labels: dict[str, tk.Label] = {}

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def run(self) -> int:
        _configure_logging()
        started = self.service.start()
        if not started:
            logger.error("网关启动失败：%s", self.service.last_error)

        self._root = tk.Tk()
        self._root.title("鎏灏 LIUHAO X")
        self._root.geometry("500x320+120+120")
        self._root.protocol("WM_DELETE_WINDOW", self._hide_to_tray)
        self._build_ui()

        self._tray = SystemTray(
            lambda action: self.events.put(("action", action)),
            tooltip=self._tooltip_text(),
        )
        if not self._tray.start():
            logger.warning("系统托盘未启动：%s", self._tray.error)
            self._labels["tray"].config(text="托盘：不可用（" + (self._tray.error or "未知原因") + "）")
        else:
            self._labels["tray"].config(text="托盘：已启用（右键菜单可用）")

        self._refresh_status_text(running=started)
        threading.Thread(target=self._poll_health, name="liuhao-health", daemon=True).start()
        self._root.after(300, self._drain_events)

        if self.hidden:
            self._root.withdraw()
        if self.open_browser and started:
            webbrowser.open(self._console_url())

        self._root.mainloop()
        return 0 if started else 1

    def _build_ui(self) -> None:
        assert self._root is not None
        frame = tk.Frame(self._root, padx=16, pady=12)
        frame.pack(fill=tk.BOTH, expand=True)

        tk.Label(frame, text="鎏灏 LIUHAO X", font=("Microsoft YaHei UI", 14, "bold")).pack(anchor="w")
        for key, label in (
            ("service", "网关："),
            ("health", "健康："),
            ("provider", "Provider："),
            ("console", "控制台："),
            ("data", "数据目录："),
            ("tray", "托盘："),
        ):
            row = tk.Label(frame, text=label, anchor="w", justify=tk.LEFT, wraplength=460)
            row.pack(anchor="w", pady=1)
            self._labels[key] = row

        self._labels["data"].config(text=f"数据目录：{DATA_DIR}")
        self._labels["console"].config(text=f"控制台：{self._console_url()}")

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

    def _tooltip_text(self) -> str:
        if self.service.is_running():
            return f"鎏灏 · 运行中 · {self._console_url()}"
        return "鎏灏 · 服务已停止"

    def _open_console(self) -> None:
        webbrowser.open(self._console_url())

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
        if self.service.is_running():
            self.service.stop()
            logger.info("用户从桌面壳停止网关")
        self._refresh_status_text(running=False)
        if self._tray is not None:
            self._tray.set_tooltip("鎏灏 · 服务已停止（托盘仍在运行）")

    def _quit(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._stop_event.set()
        if self._tray is not None:
            self._tray.stop()
        self.service.stop()
        if self._root is not None:
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
        elif action == ACTION_QUIT:
            self._quit()

    def _render_status(self, payload: dict[str, Any]) -> None:
        health = payload.get("health")
        ready = payload.get("ready")
        running = bool(payload.get("running")) and isinstance(health, dict) and bool(health.get("ok"))
        self._refresh_status_text(running=running, health=health, ready=ready)
        if self._tray is not None and not self._closing:
            self._tray.set_tooltip(self._tooltip_text())

    def _refresh_status_text(
        self,
        *,
        running: bool,
        health: dict[str, Any] | None = None,
        ready: dict[str, Any] | None = None,
    ) -> None:
        if "service" not in self._labels:
            return
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
