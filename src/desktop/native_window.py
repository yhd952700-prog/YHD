"""原生窗口：把控制台装进 Edge WebView2，不出现浏览器外壳，也不出现控制台。

为什么是 pywebview + WebView2
-----------------------------
  * 控制台是 React 单页应用，用系统 WebView2 渲染，等于"桌面窗口里的内嵌
    浏览器"，没有地址栏 / 标签页 / 书签栏；
  * 不使用 CEF / Qt：那会把安装包再放大几十到上百 MB，而 WebView2 Runtime
    在 Win10/11 上通常已随 Edge 安装；
  * 窗口创建/加载/显示都经 pywebview 的 ``Invoke`` 调度，可从后台线程调用，
    主线程只负责 ``webview.start()`` 的 GUI 消息循环。

WebView2 是硬前提
-----------------
pywebview 的 WinForms 后端在检测不到 WebView2 时会**静默回退到 mshtml/IE**，
现代 React 控制台在 IE 内核里只会白屏。所以这里先自己探测 WebView2，探测
不到就完全不启动 webview，由 :func:`ask_missing_webview2` 给出中文说明和
一条明确出路（去装 WebView2，装好重开），绝不白屏、绝不静默退出。
"""

from __future__ import annotations

import contextlib
import logging
import os
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: 桌面壳身份标记：前端读 ``navigator.userAgent`` 里的这一段就能确认"我在
#: 桌面原生窗口里"，不需要窗口尺寸，也不依赖任何查询参数（刷新、深链、
#: 重定向都不会丢）。
#:
#: 选 User-Agent 而不是注入请求头：UA 由 WebView 进程统一附加到**所有**请求
#: （页面、静态资源、接口），且页面 JS 可直接读取；请求头只能覆盖同源 fetch，
#: 首屏导航与静态资源拿不到，前端还得让服务端把头部回显出来才能识别自己。
DESKTOP_UA_TOKEN = "LiuHaoDesktopShell/1.0"
#: 保留 Windows + WebView2 的常规 UA 外形，只在末尾追加身份标记。
DESKTOP_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0 "
    f"{DESKTOP_UA_TOKEN}"
)

#: 原生窗口的最小尺寸：再小会把侧边栏与登录卡压到不可用。
MIN_WINDOW_SIZE = (960, 640)

#: WebView2 Runtime 的 EdgeUpdate 客户端 GUID（微软公开的检测方式）。
WEBVIEW2_CLIENT_GUID = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
#: 1.6 MB 左右的联网引导安装器（Evergreen Bootstrapper）。
WEBVIEW2_BOOTSTRAPPER_URL = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"

_WEBVIEW2_KEY = rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_CLIENT_GUID}"


def webview2_version() -> str | None:
    """返回本机 WebView2 Runtime 版本号；没有则返回 ``None``。

    同时查 HKLM 64/32 位视图、HKCU（按用户安装）与常见安装目录 —— 只看注册表
    会漏掉"Edge 自带 WebView2 但未写 EdgeUpdate 键"的机器。
    """
    if os.name != "nt":
        return None
    try:
        import winreg

        for hive, view in (
            (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_64KEY),
            (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_32KEY),
            (winreg.HKEY_CURRENT_USER, 0),
        ):
            try:
                with winreg.OpenKey(hive, _WEBVIEW2_KEY, 0, winreg.KEY_READ | view) as key:
                    version, _ = winreg.QueryValueEx(key, "pv")
                    if str(version).strip():
                        return str(version).strip()
            except OSError:
                continue
    except Exception:  # noqa: BLE001 - 探测失败按"没有"处理，由调用方给提示
        logger.warning("读取 WebView2 注册表失败", exc_info=True)

    for base in (
        os.environ.get("PROGRAMFILES(X86)"),
        os.environ.get("PROGRAMFILES"),
    ):
        if not base:
            continue
        application = Path(base) / "Microsoft" / "EdgeWebView" / "Application"
        if not application.is_dir():
            continue
        versions = [entry.name for entry in application.iterdir() if entry.is_dir()]
        if versions:
            return sorted(versions)[-1]
    return None


def _page(title: str, body: str) -> str:
    """所有本地页面共用一套深色品牌样式（不依赖任何外部资源）。"""
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  html, body {{ height: 100%; margin: 0; }}
  body {{
    display: flex; align-items: center; justify-content: center;
    background: radial-gradient(1200px 700px at 20% 0%, #1d2a3a 0%, #0d1117 55%, #080b10 100%);
    color: #e6edf3; font-family: "Microsoft YaHei UI", "Segoe UI", system-ui, sans-serif;
  }}
  main {{ width: min(760px, 88vw); text-align: center; padding: 32px; }}
  .brand {{ font-size: 26px; letter-spacing: .06em; font-weight: 700; }}
  .brand span {{ color: #58a6ff; font-weight: 400; font-size: 18px; margin-left: 10px; }}
  h1 {{ font-size: 21px; font-weight: 600; margin: 22px 0 10px; }}
  p {{ line-height: 1.75; color: #9fb0c3; font-size: 14px; margin: 8px 0; }}
  .reason {{ color: #c9d6e2; background: #121a24; border: 1px solid #21303f;
    border-radius: 10px; padding: 14px 16px; text-align: left; word-break: break-word; }}
  .spinner {{
    width: 34px; height: 34px; margin: 30px auto 6px;
    border: 3px solid #24384c; border-top-color: #58a6ff; border-radius: 50%;
    animation: spin 1s linear infinite;
  }}
  @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
  .actions {{ display: flex; gap: 12px; justify-content: center; flex-wrap: wrap; margin-top: 22px; }}
  button {{
    appearance: none; border: 0; border-radius: 9px; padding: 11px 20px;
    font-size: 14px; font-family: inherit; cursor: pointer;
    background: #2f81f7; color: #fff;
  }}
  button:hover {{ background: #4b94ff; }}
  button.ghost {{ background: #1b2530; color: #cfdae6; border: 1px solid #2b3a4b; }}
  button.ghost:hover {{ background: #23303f; }}
  .hint {{ margin-top: 20px; font-size: 12.5px; color: #7d8fa3; }}
  input {{
    width: 150px; padding: 10px 12px; border-radius: 8px;
    border: 1px solid #2b3a4b; background: #0e151d; color: #e6edf3; font-size: 14px;
  }}
  .row {{ display: flex; gap: 10px; justify-content: center; align-items: center; margin-top: 20px; flex-wrap: wrap; }}
</style></head>
<body><main>{body}</main>
<script>
  function bridge () {{ return (window.pywebview && window.pywebview.api) || null; }}
  function act (name, arg) {{
    var api = bridge();
    if (api && typeof api[name] === 'function') {{ api[name](arg); return true; }}
    return false;
  }}
  function retry () {{ if (!act('retry')) location.reload(); }}
</script>
</body></html>"""


def splash_page() -> str:
    return _page(
        "鎏灏",
        """
  <div class="brand">LIUHAO<span>鎏灏</span></div>
  <div class="spinner"></div>
  <h1>正在启动鎏灏…</h1>
  <p>正在准备本地服务，首次启动通常需要几秒。</p>
  <p class="hint">启动完成后会自动进入登录页，无需手动刷新。</p>
""",
    )


def error_page(title: str, reason: str) -> str:
    safe = (
        reason.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        or "服务没有在预期时间内就绪。"
    )
    return _page(
        "鎏灏",
        f"""
  <div class="brand">LIUHAO<span>鎏灏</span></div>
  <h1>{title}</h1>
  <p class="reason">{safe}</p>
  <p>鎏灏正在等本地服务（网关）就绪：端口要能连上，核心组件也要加载完成。
     首次启动一般需要 10–30 秒，最长约 60 秒。</p>
  <div class="actions">
    <button onclick="retry()">重试检查</button>
  </div>
  <p class="hint">重试只重新检查本地服务，不会重启它，也不需要关掉这个窗口。</p>
""",
    )


def conflict_page(port: int, owner: str) -> str:
    safe_owner = (
        owner.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        or "未知"
    )
    suggestion = port + 1 if port < 65535 else 8080
    return _page(
        "鎏灏：网关端口被占用",
        f"""
  <div class="brand">LIUHAO<span>鎏灏</span></div>
  <h1>网关端口 {port} 已被占用</h1>
  <p class="reason">占用者：{safe_owner}</p>
  <p>为避免地址变化，鎏灏不会自动换端口。请选一个继续：</p>
  <div class="actions">
    <button onclick="retry()">关掉占用程序再试</button>
    <button class="ghost" onclick="usePort()">换一个端口继续</button>
  </div>
  <div class="row">
    <input id="port" type="number" min="1" max="65535" value="{suggestion}">
    <span class="hint" style="margin:0">校验空闲后直接继续启动</span>
  </div>
  <p class="hint" id="msg"></p>
<script>
  function usePort () {{
    var input = document.getElementById('port');
    var value = parseInt(input && input.value, 10);
    var msg = document.getElementById('msg');
    if (!value || value < 1 || value > 65535) {{
      if (msg) msg.textContent = '请输入 1–65535 之间的端口号。';
      return;
    }}
    if (!act('use_port', value) && msg) msg.textContent = '窗口还在准备中，请稍后重试。';
  }}
</script>
""",
    )


class NativeWindow:
    """pywebview 窗口的薄封装：创建、加载、显示/隐藏、销毁。

    关闭按钮默认只隐藏到托盘：``events.closing`` 的处理器返回 ``False`` 表示
    "取消关闭"；只有 :meth:`destroy` 会把 ``_quitting`` 置真，让关闭真正放行。
    """

    def __init__(
        self,
        *,
        title: str,
        storage_dir: Path | str,
        js_api: Any,
        width: int = 1280,
        height: int = 860,
        hidden: bool = False,
    ) -> None:
        self._title = title
        self._storage_dir = Path(storage_dir)
        self._js_api = js_api
        self._width = width
        self._height = height
        self._hidden = hidden
        self._window: Any = None
        self._webview: Any = None
        self._quitting = False
        self._visible = False
        self._ready = threading.Event()
        self._loaded_url: str | None = None

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def create(self) -> None:
        import webview

        self._webview = webview
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        self._window = webview.create_window(
            self._title,
            html=splash_page(),
            width=self._width,
            height=self._height,
            min_size=MIN_WINDOW_SIZE,
            hidden=self._hidden,
            js_api=self._js_api,
            background_color="#0d1117",
            text_select=False,
            confirm_close=False,
        )
        self._window.events.closing += self._on_closing
        self._window.events.shown += self._on_shown
        self._visible = not self._hidden

    def start(self) -> None:
        """阻塞运行 GUI 消息循环，直到窗口被 :meth:`destroy`。"""
        if self._webview is None or self._window is None:
            raise RuntimeError("NativeWindow 未创建")
        self._webview.start(
            func=self._on_gui_ready,
            gui="edgechromium",
            private_mode=False,
            storage_path=str(self._storage_dir),
            # 身份标记随 WebView 发出，前端据此走桌面端形态；不靠窗口宽度猜。
            user_agent=DESKTOP_USER_AGENT,
        )

    def _on_gui_ready(self) -> None:
        self._ready.set()
        logger.info("原生窗口已就绪（WebView2）")

    def _on_shown(self) -> None:
        self._visible = True

    def _on_closing(self) -> bool:
        """返回 True 允许关闭，返回 False 取消关闭（隐藏到托盘）。"""
        if self._quitting:
            return True
        self._visible = False
        with contextlib.suppress(Exception):
            self._window.hide()
        logger.info("原生窗口已关闭到托盘（进程继续运行）")
        return False

    # ------------------------------------------------------------------ #
    # 页面与可见性
    # ------------------------------------------------------------------ #
    def load_console(self, url: str, *, show: bool = True) -> None:
        if self._window is None:
            return
        self._loaded_url = url
        logger.info("原生窗口加载控制台：%s", self._masked_url(url))
        self._window.load_url(url)
        if show:
            self.show()

    def retry_splash(self, *, show: bool = True) -> None:
        if self._window is None:
            return
        self._window.load_html(splash_page())
        if show:
            self.show()

    def show_error(self, title: str, reason: str, *, show: bool = True) -> None:
        if self._window is None:
            return
        self._window.load_html(error_page(title, reason))
        if show:
            self.show()

    def show_conflict(self, port: int, owner: str, *, show: bool = True) -> None:
        if self._window is None:
            return
        self._window.load_html(conflict_page(port, owner))
        if show:
            self.show()

    def show(self) -> None:
        if self._window is None:
            return
        self._window.show()
        self._visible = True
        with contextlib.suppress(Exception):
            self._window.restore()

    def hide(self) -> None:
        if self._window is None:
            return
        self._window.hide()
        self._visible = False

    def destroy(self) -> None:
        self._quitting = True
        if self._window is not None:
            with contextlib.suppress(Exception):
                self._window.destroy()

    @property
    def visible(self) -> bool:
        return self._visible

    @property
    def loaded_url(self) -> str | None:
        return self._loaded_url

    @staticmethod
    def _masked_url(url: str) -> str:
        """日志里不带一次性的本机恢复令牌（fragment 可能含恢复码）。"""
        return url.split("#", 1)[0]


def ask_missing_webview2() -> str:
    """WebView2 缺失时的中文提示；返回 ``install`` / ``cancel``。

    不提供"用浏览器打开"的自降级入口：桌面端就是桌面端，缺显示组件时只给
    安装出路，装好后重新打开即可。用 tkinter 是因为这时 pywebview 本身不可用；
    它只出现在缺组件的机器上，不参与正常启动路径。tkinter 不可用时退回
    MessageBoxW（是=去安装）。
    """
    title = "鎏灏：缺少显示组件"
    message = (
        "这台电脑缺少鎏灏桌面窗口所需的组件（WebView2）。\n\n"
        "· 去安装 WebView2：打开微软官方下载页（约 1.6 MB 的联网安装器），"
        "装完后重新打开鎏灏即可。\n"
        "· 稍后再说：先关闭这个提示，装好 WebView2 后再重新打开鎏灏。"
    )
    try:
        import tkinter as tk

        root = tk.Tk()
        root.title(title)
        root.resizable(False, False)
        with contextlib.suppress(Exception):  # 无桌面会话时忽略置顶失败
            root.attributes("-topmost", True)
        choice = {"value": "cancel"}

        tk.Label(
            root,
            text=message,
            justify="left",
            anchor="w",
            wraplength=460,
            font=("Microsoft YaHei UI", 10),
        ).pack(padx=18, pady=(18, 12))

        row = tk.Frame(root)
        row.pack(pady=(0, 18))

        def choose(value: str) -> None:
            choice["value"] = value
            root.destroy()

        tk.Button(row, text="去安装 WebView2", width=16, command=lambda: choose("install")).pack(
            side="left", padx=(0, 10)
        )
        tk.Button(row, text="稍后再说", width=16, command=lambda: choose("cancel")).pack(side="left")
        root.protocol("WM_DELETE_WINDOW", lambda: choose("cancel"))
        root.update_idletasks()
        root.mainloop()
        return choice["value"]
    except Exception:  # noqa: BLE001 - 没有 tkinter 时用系统对话框兜底
        logger.warning("tkinter 不可用，改用系统对话框提示 WebView2 缺失", exc_info=True)
        return _messagebox_webview2()


def _messagebox_webview2() -> str:
    if os.name != "nt":
        print("[webview2] 缺少 WebView2，无法创建桌面窗口", flush=True)
        return "cancel"
    try:
        import ctypes

        yes = ctypes.windll.user32.MessageBoxW(
            None,
            "这台电脑缺少鎏灏桌面窗口所需的组件（WebView2）。\n\n"
            "点「是」打开微软官方下载页（约 1.6 MB）；装好后重新打开鎏灏。\n"
            "点「否」先关闭。",
            "鎏灏：缺少显示组件",
            0x00000004 | 0x00000040,  # MB_YESNO | MB_ICONINFORMATION
        )
        return "install" if yes == 6 else "cancel"
    except Exception:  # noqa: BLE001
        return "cancel"


def show_install_help() -> None:
    """点击「去安装」后的第二步说明（下载页不是安装器，必须讲清楚）。"""
    if os.name != "nt":
        return
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(
            None,
            "浏览器已打开 WebView2 下载页。\n\n"
            "请下载并运行 Evergreen 安装器，安装完成后重新打开鎏灏。\n\n"
            "如果安装失败或这台电脑无法联网，请先解决网络/安装问题；鎏灏桌面窗口"
            "依赖 WebView2，装好后即可正常打开。",
            "鎏灏：安装 WebView2",
            0x00000040,  # MB_ICONINFORMATION
        )
    except Exception:  # noqa: BLE001
        pass


__all__ = [
    "DESKTOP_UA_TOKEN",
    "DESKTOP_USER_AGENT",
    "MIN_WINDOW_SIZE",
    "WEBVIEW2_BOOTSTRAPPER_URL",
    "NativeWindow",
    "ask_missing_webview2",
    "conflict_page",
    "error_page",
    "show_install_help",
    "splash_page",
    "webview2_version",
]
