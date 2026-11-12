#!/usr/bin/env python
"""贾维斯一键启动器。

一条命令把「网关 + 控制台」拉起来，并等健康探针真正通过后再交还控制权。

    python scripts\\start_liuhao.py          # 单端口（默认）
    python scripts\\start_liuhao.py --backend-only
    python scripts\\start_liuhao.py --no-browser
    python scripts\\start_liuhao.py --vite   # vite 热更新开发模式
    python scripts\\start_liuhao.py --https  # 单端口 + 本地 HTTPS
    python scripts\\start_liuhao.py --diagnose  # 只生成诊断包 zip（服务不必在运行）

设计约束：
  * 不依赖 Docker（本机 Docker daemon 常常没起），走原生进程。
  * 本机有 HTTP 代理，所有本机探测必须显式绕代理，否则会误报不通。
  * 就绪判定走与桌面壳同一套启动门（``src/desktop/startup.py``）：网关在导入
    路由之后才绑定端口，绑定前浏览器只能拿到连接失败，因此**连接失败按
    「启动中」处理**，只有 ``/v1/startup`` 的 ready/degraded 才打开浏览器。
  * Ctrl+C 时把子进程一起带走，不留孤儿 uvicorn / vite。
  * --https 会在控制台之上叠加一个本地 TLS 反向代理（scripts/ops/https_proxy.py），
    用于让手机端 PWA 处于安全上下文、从而能「安装应用 / 添加到主屏幕」。
    证书为自签，需放进 deploy/cloud/certs/ 并由手机一次性信任（见交付说明）。
"""

from __future__ import annotations

import argparse
import atexit
import contextlib
import ipaddress
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CONSOLE_DIR = ROOT / "apps" / "console" / "console"

#: 以 ``python scripts/start_liuhao.py`` 方式运行时，``sys.path[0]`` 是 scripts/
#: 而不是仓库根，稍后的 ``import src.*``（启动门、日志配置）会 ModuleNotFoundError。
#: 安装版由 launch_installed.py 设置 PYTHONPATH；开发/自测路径在这里补上。
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.brand import BRAND_LOCKUP  # noqa: E402
from src.common.data_paths import resolve_store_path  # noqa: E402
from src.common.instance_lock import InstanceLock, InstanceLockBusy, instance_root  # noqa: E402
from src.common.leak_scan import OMITTED_MEMBERS_NAME  # noqa: E402

#: 唯一生效的日志配置。由 ``python -m src.gateway`` 子进程按 UTF-8 读取后
#: 以映射交给 uvicorn：10 MiB × 5 份轮转，INFO 进 logs/liuhao.log，ERROR
#: 另进 logs/error.log（见配置文件头注释）。
LOG_CONFIG = ROOT / "config" / "logging.yaml"
#: 启动器自己的日志目录（launcher.log / 就绪探针失败提示）。同样服从数据根：
#: ``LIUHAO_LOG_DIR`` > ``LIUHAO_DATA_DIR/logs`` > 仓库根 ``logs``。否则只设
#: ``LIUHAO_DATA_DIR`` 的隔离运行仍会在共享开发目录里留下运行时文件。
LOG_DIR = Path(
    resolve_store_path("logs", ROOT / "logs", env_var="LIUHAO_LOG_DIR")
)

#: 网关子进程的工作目录。开发态就是仓库根；安装版由 ``launch_installed``
#: 指向**数据目录**（程序目录只读），但 ``ROOT`` 必须继续指向程序目录：
#: ``-m src.gateway`` 的模块导入和 ``runtime/python.exe`` 都靠它。
GATEWAY_CWD = ROOT

#: 单端口模式下由网关服务的前端构建产物。
#: 权威实现在 src/gateway/console_static.py；这里重复声明只是为了让启动器
#: 能在**拉起进程之前**就给出可读的报错，而不是等健康探针超时。
CONSOLE_DIST_ENV = "LIUHAO_CONSOLE_DIST"
DEFAULT_CONSOLE_DIST = CONSOLE_DIR / "dist"

#: 登录凭据 / 人类身份登记表：`scripts/register_human_identity.py` 的默认落点。
#: 网关对 chat / dashboard / profile / knowledge 要求 Bearer 令牌，而内核在
#: 环境变量未设时**刻意不加载任何人**（fail-closed）—— 若不在这里补上，
#: 本机启动就是一个"没人能通过的登录墙"。详见 `_point_at_console_stores`。
IDENTITY_FILE_ENV = "LIUHAO_HUMAN_IDENTITIES_FILE"
AUTH_SECRETS_ENV = "LIUHAO_AUTH_SECRETS_FILE"
INTEGRITY_KEY_ENV = "LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY"

#: 默认只绑回环（127.0.0.1）：本机能用，局域网上的其它设备访问不到。
#: 手机/其它电脑要用时必须显式 ``--host 0.0.0.0``（或指定网卡地址），
#: 启动器会打印"已对外暴露"的醒目警告。
DEFAULT_API_HOST = "127.0.0.1"
DEFAULT_API_PORT = 8080
DEFAULT_UI_PORT = 5173

STARTUP_TIMEOUT_S = 120.0
POLL_INTERVAL_S = 0.5

_children: list[subprocess.Popen] = []


def _port_in_use(host: str, port: int) -> bool:
    """True when ``host:port`` is already bound by another process.

    必须与桌面壳用**同一套多地址探测**：只绑 ``127.0.0.1`` 会漏判监听
    ``0.0.0.0`` 的占用者（Windows 上通配与具体地址可以共存），于是启动器
    以为空闲、网关却在绑定阶段失败。判定口径统一到 ``port_is_free``。
    """
    from src.desktop.probe import port_is_free

    return not port_is_free(int(port), host=host or "127.0.0.1")


class PortUnavailable(RuntimeError):
    """配置端口被别的进程占用；不自动换端口，避免地址漂移。"""


def _describe_port_owner(port: int) -> str:
    """尽力查出谁在占用 ``port``（与托盘壳共用同一实现，含进程路径）。

    查不到时返回「查不到（可能需要管理员权限…）」，让用户分清「查不到」与
    「没人占用」；诊断信息是锦上添花，绝不能因为查不到就吞掉报错。
    """
    from src.desktop.probe import describe_port_owner

    return describe_port_owner(port)


def _log_launcher(message: str) -> None:
    """把启动器侧的关键事件写进 ``<数据目录>/logs/launcher.log``。

    与安装版共用 :class:`RotatingTextWriter`：10 MiB × 3 份、脱敏、跨进程
    文件锁。早期实现是直接 ``open(..., "a")``，长期运行只增不减。
    """
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        from src.logging_config import RotatingTextWriter

        RotatingTextWriter(LOG_DIR / "launcher.log").write(
            f"{datetime.now().isoformat(timespec='seconds')} {message}\n"
        )
    except OSError:
        pass


def _write_current_url(port: int, url: str) -> Path | None:
    """把本次实际地址写到 ``<数据目录>/config/current_url.txt``。

    只在安装版（``LIUHAO_DATA_DIR`` 已设置）落盘：开发态不往仓库里写文件。
    用户/支持人员据此确认"这一版到底在哪个地址上"，不必去翻窗口输出。
    """
    raw = (os.environ.get("LIUHAO_DATA_DIR") or "").strip()
    if not raw:
        return None
    target = Path(raw) / "config" / "current_url.txt"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            f"url={url}\n"
            f"port={port}\n"
            f"pid={os.getpid()}\n"
            f"started_at={datetime.now().isoformat(timespec='seconds')}\n",
            encoding="utf-8",
        )
    except OSError as exc:
        _log_launcher(f"[warn] 无法写入 {target}: {exc}")
        return None
    return target


def _require_available_port(host: str, requested: int, label: str, flag: str) -> int:
    """要求配置端口可用；被占用时明确报错，**绝不悄悄换端口**。

    原先的实现会自己 +1 往后找，结果每次启动地址都可能不同：书签、旧标签页、
    已安装的 PWA 全部指向一个已经不存在的 origin（现场：8124 实例退出后，
    用户手里留着一个连不上的旧地址）。端口冲突属于需要人知道的事，因此
    这里 fail-fast，并把占用者一起报出来。
    """
    if requested == 0:
        return requested  # 0 = 交给系统挑（测试/显式要求）
    if not _port_in_use(host, requested):
        return requested
    owner = _describe_port_owner(requested)
    detail = f"（占用者：{owner}）" if owner else ""
    suggested = requested + 1
    raise PortUnavailable(
        f"{label}端口 {requested} 已被占用{detail}。\n"
        f"       为避免地址变化导致书签 / 旧标签 / PWA 失联，启动器不会自动换端口。\n"
        f"       处理一：结束占用该端口的进程后，重新双击桌面图标。\n"
        f"       处理二：复制下面这条命令（含引号），改用 {suggested} 端口启动：\n"
        f'         "{ROOT / "LiuHao.bat"}" {flag} {suggested}'
    )


def _ask_port_choice(
    host: str, port: int, label: str, reason: str
) -> tuple[str, int | None]:
    """有图形桌面时弹窗二选一：关掉占用程序再试 / 换一个端口继续。

    返回 ``("retry", None)``、``("use", new_port)`` 或 ``("cancel", None)``。
    打不开窗口（无桌面 / Tk 缺失）时抛异常，由调用方退回命令行提示。
    """
    import tkinter as tk
    from tkinter import messagebox, simpledialog

    root = tk.Tk()
    root.title(f"贾维斯：{label}端口被占用")
    root.resizable(False, False)
    root.attributes("-topmost", True)
    choice = {"action": "cancel", "port": None}
    owner = _describe_port_owner(port)
    detail = f"（占用者：{owner}）" if owner else ""

    tk.Label(
        root,
        text=(
            f"{label}端口 {port} 已被占用{detail}。\n"
            "请选择：关掉占用程序后再试，或换一个空闲端口继续启动。"
        ),
        justify="left",
        anchor="w",
        font=("Microsoft YaHei UI", 10),
        wraplength=460,
    ).pack(fill="x", padx=16, pady=(16, 12))

    row = tk.Frame(root)
    row.pack(fill="x", padx=16, pady=(0, 16))

    def retry() -> None:
        choice["action"] = "retry"
        root.destroy()

    def use_other() -> None:
        suggestion = port + 1 if port < 65535 else 8080
        while True:
            value = simpledialog.askinteger(
                "换一个端口继续",
                "请输入新的服务端口（1–65535）：\n换端口后地址会变，请以新地址为准。",
                initialvalue=suggestion,
                parent=root,
                minvalue=1,
                maxvalue=65535,
            )
            if value is None:
                return  # 回到二选一窗口
            if not _port_in_use(host, value):
                choice["action"] = "use"
                choice["port"] = int(value)
                root.destroy()
                return
            other_owner = _describe_port_owner(value)
            messagebox.showwarning(
                "端口仍被占用",
                f"端口 {value} 也被占用"
                + (f"（占用者：{other_owner}）" if other_owner else "")
                + "，请换一个空闲端口。",
                parent=root,
            )

    # 只给两个选项；关闭窗口（标题栏 X）等同「稍后再处理」，不另设第三个按钮。
    tk.Button(row, text="关掉占用程序再试", width=18, command=retry).pack(side="left")
    tk.Button(row, text="换一个端口继续", width=18, command=use_other).pack(
        side="left", padx=(10, 0)
    )

    root.update_idletasks()
    width = root.winfo_width()
    height = root.winfo_height()
    x = max((root.winfo_screenwidth() - width) // 2, 0)
    y = max((root.winfo_screenheight() - height) // 3, 0)
    root.geometry(f"+{x}+{y}")
    root.mainloop()
    return choice["action"], choice["port"]


def _resolve_port_or_explain(
    host: str, requested: int, label: str, flag: str
) -> int | None:
    """确认端口可用；被占时优先弹窗二选一，无桌面则打印明确中文并返回 None。

    不自动顺延：换端口地址就变，书签 / 旧标签 / PWA 会指向连不上的地方。
    """
    port = requested
    while True:
        try:
            return _require_available_port(host, port, label, flag)
        except PortUnavailable as exc:
            try:
                action, new_port = _ask_port_choice(host, port, label, str(exc))
            except Exception:  # noqa: BLE001 - 无桌面会话，退回命令行提示
                print(f"[FAIL] {exc}", flush=True)
                _log_launcher(f"[FAIL] {exc}")
                return None
            if action == "retry":
                continue  # 用户说已经关掉了，重新检查同一端口
            if action == "use" and new_port:
                print(f"[port] 用户选择改用端口 {new_port}。", flush=True)
                _log_launcher(f"[port] 端口 {port} 被占用，用户改用 {new_port}")
                port = new_port
                continue
            print(f"[FAIL] {exc}", flush=True)
            _log_launcher(f"[FAIL] {exc}")
            return None


def _lan_addresses() -> list[str]:
    """返回本机所有非回环的 IPv4 地址，供控制台打印局域网访问 URL。

    手机连同一个 Wi-Fi 时用这些地址能直接访问；回环地址 127.0.0.1 只在本机
    有效，打印出来对手机用户没意义。
    """
    found: list[str] = []
    try:
        hostname = socket.gethostname()
        addrs = socket.getaddrinfo(hostname, None, socket.AF_INET)
    except OSError:
        addrs = []
    for info in addrs:
        ip = info[4][0]
        try:
            parsed = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if parsed.is_loopback or parsed.is_link_local or parsed.is_multicast:
            continue
        if ip not in found:
            found.append(ip)
    return found


def _venv_python() -> str:
    """优先用项目虚拟环境，退回当前解释器。"""
    if os.name == "nt":
        candidate = ROOT / ".venv" / "Scripts" / "python.exe"
    else:
        candidate = ROOT / ".venv" / "bin" / "python"
    if candidate.exists():
        return str(candidate)
    return sys.executable


def _node() -> str:
    found = shutil.which("node")
    if not found:
        raise SystemExit("未找到 node —— 控制台需要 Node.js，请先安装或加进 PATH。")
    return found


def _console_dist() -> Path | None:
    """已构建的控制台产物目录；没有就返回 None。"""
    raw = (os.environ.get(CONSOLE_DIST_ENV) or "").strip()
    candidate = Path(raw).expanduser() if raw else DEFAULT_CONSOLE_DIST
    if not (candidate / "index.html").is_file():
        return None
    return candidate


def _load_dotenv() -> None:
    """把仓库根的 `.env` 载入进程环境，且不覆盖已显式导出的变量。

    没有这一步时 `.env` 里的 SECRET_KEY / APP_ENV 对网关完全不可见：
    `/v1/production/preflight` 报 secret_key_missing，部署姿态被判为
    UNDETERMINABLE —— 服务看着起来了，实际是一个没配好的实例。
    `override=False` 保证运维显式导出的值永远优先。
    """
    env_file = ROOT / ".env"
    if not env_file.is_file():
        return
    try:
        from dotenv import load_dotenv
    except ImportError:  # requirements.txt 已声明 python-dotenv
        return
    load_dotenv(env_file, override=False)


def _point_at_console_stores() -> None:
    """当本机 config/ 里存在登记表/凭据时，把它们指给要拉起的网关。

    为什么需要这一步：注册脚本把两个文件写在 ``config/`` 下，但内核在
    ``LIUHAO_HUMAN_IDENTITIES_FILE`` 未设置时**刻意加载零个人类**（fail-closed），
    网关又对 chat/dashboard/profile/knowledge 要求令牌 —— 两者叠加的结果是
    一个"谁都进不去的登录墙"，而且没有任何报错说明原因。

    只 ``setdefault``：运维显式导出的路径永远优先；文件不存在则什么都不做，
    fail-closed 语义保持不变。
    """
    for env_name, path in (
        (IDENTITY_FILE_ENV, ROOT / "config" / "human_identities.json"),
        (AUTH_SECRETS_ENV, ROOT / "config" / "auth_secrets.json"),
    ):
        if not (os.environ.get(env_name) or "").strip() and path.is_file():
            os.environ[env_name] = str(path)


def _ensure_identity_integrity_key() -> bool:
    """无环境变量时把身份注册表签名密钥自举到数据目录。

    身份内核在 ``LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY`` 未设置时拒绝加载
    任何身份（fail-closed）。安装版由 ``launch_installed.prepare_environment``
    提前注入；这里覆盖直接运行本启动器（源码/自用版）的情形，保证「全新安装
    第一次启动」不会卡在无人可登录。密钥值不会打印，落盘权限见
    ``src/common/integrity_key``。
    """
    if (os.environ.get(INTEGRITY_KEY_ENV) or "").strip():
        return True
    from src.common.integrity_key import ensure_integrity_key, resolve_data_dir

    registry = ROOT / "config" / "human_identities.json"
    try:
        key = ensure_integrity_key(
            data_dir=resolve_data_dir(),
            registry_path=registry if registry.is_file() else None,
        )
    except OSError as exc:
        print(f"[FAIL] 无法生成/写入人类身份完整性密钥：{exc}", file=sys.stderr)
        print("        已停止启动；请确认数据目录可写，"
              f"或用 {os.environ.get('LIUHAO_DATA_DIR') or 'LIUHAO_DATA_DIR'} 指向可写目录。",
              file=sys.stderr)
        return False
    os.environ[INTEGRITY_KEY_ENV] = key
    return True


def _no_proxy_opener():
    """构造一个**不走代理**的 opener。

    本机 env 里常有活的 http_proxy，直接用 urlopen 打 127.0.0.1 会被代理拦掉，
    报 "upstream connect failed"，看起来像服务没起来——这是本机第一大坑。
    """
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _wait_http_ok(url: str, timeout: float, label: str, hint: str = "") -> bool:
    opener = _no_proxy_opener()
    deadline = time.monotonic() + timeout
    waited = 0.0
    while time.monotonic() < deadline:
        try:
            with opener.open(url, timeout=3) as resp:
                # 只认 200：3xx/4xx 说明端口上响应的不是我们要的页面
                # （例如别人的服务、代理的错误页），不能算"就绪"。
                if resp.status == 200:
                    print(f"  [ok] {label} 就绪（{waited:.1f}s，HTTP {resp.status}）")
                    return True
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(POLL_INTERVAL_S)
        waited += POLL_INTERVAL_S
    print(f"  [FAIL] {label} 在 {timeout:.0f}s 内没起来")
    if hint:
        print(f"         {hint}")
    return False


def _gate_log_hint() -> str:
    """启动失败时给用户看的日志路径（安装版落在数据目录，不是程序目录）。"""
    base = str(LOG_DIR).rstrip("/\\")
    return f"{os.path.join(base, 'liuhao.log')}、{os.path.join(base, 'error.log')}"


def _gate_diagnose_hint() -> str:
    """可照抄执行的诊断命令：走与托盘同一个入口，日志目录才不会被指错。"""
    entry = Path(__file__).resolve().parent / "liuhao_desktop.py"
    return f'"{_venv_python()}" "{entry}" diagnose --verify'


def _wait_gateway_ready(
    probe_base: str, proc: subprocess.Popen, label: str = "网关"
) -> dict | None:
    """等网关 ``GET /v1/startup`` 到 ready/degraded。

    与桌面壳共用 ``src/desktop/startup.py``：端口绑定前浏览器拿到的是**连接失败**，
    这里把它当 ``starting`` 继续等；只有 ``ready``/``degraded`` 才返回结果
    （调用方据此才允许打开浏览器）。``degraded`` 会把 ``background.errors``
    原样打出来，不吞成"启动失败"。

    超时或进程中途退出时返回 None，并打印终点提示（日志路径 + 诊断命令），
    不无限转圈。
    """
    from src.desktop.startup import wait_until_ready

    print(f"  [..] {label}启动中：连接失败按「启动中」处理，就绪后才打开浏览器")
    state = {"stage": "", "elapsed": -5.0}

    def on_progress(snapshot: dict) -> None:
        stage = snapshot.get("last_stage") or (
            "等待端口监听" if not snapshot.get("reachable") else "等待核心就绪"
        )
        elapsed = float(snapshot.get("elapsed") or 0.0)
        # 阶段变化立刻打印；同一阶段每 5 秒报一次进度，避免刷屏。
        if stage != state["stage"] or elapsed - state["elapsed"] >= 5.0:
            print(f"       {stage}（已 {elapsed:.1f}s）", flush=True)
            state["stage"] = stage
            state["elapsed"] = elapsed

    result = wait_until_ready(
        probe_base,
        timeout=STARTUP_TIMEOUT_S,
        log_dir=LOG_DIR,
        log_hint=_gate_log_hint(),
        diagnose_hint=_gate_diagnose_hint(),
        is_alive=lambda: proc.poll() is None,
        on_progress=on_progress,
        # 双击场景要尽快看见登录框：就绪探测从 250/500ms 收紧到 100/200ms。
        min_interval=0.10,
        max_interval=0.20,
    )
    if result.get("console_ok"):
        if result.get("state") == "degraded":
            print(f"  [warn] {result.get('detail')}", flush=True)
        else:
            print(f"  [ok] {label}就绪（{result.get('elapsed')}s）", flush=True)
        return result
    print(f"  [FAIL] {result.get('detail')}", flush=True)
    return None


def _headless() -> bool:
    """没有控制台（安装版 pythonw 启动）时为真。

    此时子进程必须加 ``CREATE_NO_WINDOW`` 并把输出写进 launcher.log：
    从 pythonw 拉起 console 子系统的 ``python.exe`` 会额外分配一个黑色窗口，
    而且子进程的任何 traceback 都会直接画到屏幕上。
    """
    return (os.environ.get("LIUHAO_HEADLESS") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


_CHILD_LOG_HANDLE = None


def _child_output():
    """无控制台模式下，子进程 stdout/stderr 的统一落点（launcher.log）。"""
    global _CHILD_LOG_HANDLE
    if _CHILD_LOG_HANDLE is None:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        _CHILD_LOG_HANDLE = open(  # noqa: SIM115 - 进程生命周期内复用同一个句柄
            LOG_DIR / "launcher.log", "a", encoding="utf-8", buffering=1
        )
    return _CHILD_LOG_HANDLE


def _spawn(cmd: list[str], cwd: Path, label: str, env: dict | None = None) -> subprocess.Popen:
    print(f"  [$] ({cwd.name}) {' '.join(cmd)}")
    extra: dict = {}
    if _headless():
        extra["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        extra["stdin"] = subprocess.DEVNULL
        handle = _child_output()
        if handle is not None:
            extra["stdout"] = handle
            extra["stderr"] = subprocess.STDOUT
    popen = subprocess.Popen(cmd, cwd=str(cwd), env=env, **extra)
    _children.append(popen)
    print(f"       {label} pid={popen.pid}")
    return popen


def _wait_https_ok(url: str, timeout: float, label: str) -> bool:
    """同 _wait_http_ok，但走 TLS 且容忍自签证书（不校验主机名/链）。"""
    import ssl

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=ctx)
    )
    deadline = time.monotonic() + timeout
    waited = 0.0
    while time.monotonic() < deadline:
        try:
            with opener.open(url, timeout=3) as resp:
                if 200 <= resp.status < 500:
                    print(f"  [ok] {label} 就绪（{waited:.1f}s，HTTPS {resp.status}）")
                    return True
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(POLL_INTERVAL_S)
        waited += POLL_INTERVAL_S
    print(f"  [FAIL] {label} 在 {timeout:.0f}s 内没起来，详见上方进程输出")
    return False


def _start_https_proxy(args: argparse.Namespace, single_port: bool) -> None:
    """在控制台之上叠加本地 TLS 反向代理（受管子进程，Ctrl+C 一起带走）。"""
    https_port = args.https_port
    backend_port = args.api_port if single_port else args.ui_port
    cert = os.environ.get("LIUHAO_TLS_CERT") or str(
        ROOT / "deploy" / "cloud" / "certs" / "liuhao-local.pem"
    )
    key = os.environ.get("LIUHAO_TLS_KEY") or str(
        ROOT / "deploy" / "cloud" / "certs" / "liuhao-local.key"
    )
    if not (os.path.isfile(cert) and os.path.isfile(key)):
        print(f"  [skip] HTTPS 代理未启动：缺少证书 {cert}")
        print("         用 openssl 生成自签证书（SAN 含本机局域网 IP）后重试。")
        return
    proxy_env = dict(os.environ)
    proxy_env["LIUHAO_PROXY_BACKEND_HOST"] = args.host
    proxy_env["LIUHAO_PROXY_BACKEND_PORT"] = str(backend_port)
    # 代理的监听地址跟随网关：默认同样只绑回环，不额外放大暴露面；
    # 只有用户显式 --host 对外时才跟着对外。
    proxy_env["LIUHAO_TLS_HOST"] = args.host
    proxy_env["LIUHAO_TLS_PORT"] = str(https_port)
    proxy_env["LIUHAO_TLS_CERT"] = cert
    proxy_env["LIUHAO_TLS_KEY"] = key
    https_proxy = ROOT / "scripts" / "ops" / "https_proxy.py"
    if not https_proxy.is_file():
        print(f"  [skip] HTTPS 代理未启动：找不到 {https_proxy}")
        return
    _spawn([_venv_python(), "-u", str(https_proxy)], ROOT, "https-proxy", proxy_env)
    wait_host = args.host if args.host not in ("0.0.0.0", "::") else "127.0.0.1"
    _wait_https_ok(f"https://{wait_host}:{https_port}/", 30.0, "HTTPS 代理")
    print(f"      手机端安装地址： https://<局域网IP>:{https_port}/")


def _shutdown_all() -> None:
    for proc in _children:
        if proc.poll() is None:
            proc.terminate()
    deadline = time.monotonic() + 5.0
    for proc in _children:
        try:
            proc.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            proc.kill()


def _is_loopback(host: str) -> bool:
    """绑定地址是否只覆盖本机回环。"""
    value = (host or "").strip()
    if value in ("127.0.0.1", "localhost", "::1", "[::1]"):
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def _warn_if_exposed(host: str, port: int) -> None:
    """显式对外绑定时打印醒目警告（默认 127.0.0.1 不会触发）。"""
    if _is_loopback(host):
        return
    print("!" * 62)
    print(f"!! 警告：网关已绑定 {host}:{port}，已对外暴露，局域网可访问。")
    print("!! 如非必要，请改用默认的 127.0.0.1（只本机可达）。")
    print("!" * 62)


#: 通配绑定：服务端用它监听所有网卡，但浏览器无法打开这种地址。
WILDCARD_HOSTS = ("0.0.0.0", "::", "[::]", "::0")


def _display_host(host: str) -> str:
    """控制台展示用的地址：通配绑定按回环展示，避免打印打不开的 URL。

    打印的 host 始终派生自实际生效的绑定配置（``args.host`` 同时就是传给网关
    ``HOST`` 的值），不允许在打印处硬编码字面量。
    """
    value = (host or "").strip()
    if value in WILDCARD_HOSTS:
        return "127.0.0.1"
    return value


def _probe_running_instance() -> tuple[str, dict] | None:
    """探测同一份数据目录是否已经有网关在跑。

    返回 ``(可读提示, 持有者元信息)``；空闲返回 ``None``。锁文件本身不可用
    （数据目录只读等）时返回 ``None``，交给网关在启动阶段给出 fail-closed 报错，
    避免启动器把「目录不可写」误报成「已在运行」。
    """
    lock = InstanceLock(instance_root(ROOT))
    holder = lock.probe()
    if holder is None:
        return None
    return InstanceLockBusy(holder).describe(), holder


def _tray_instance_key() -> str:
    """托盘窗口类名用的实例键，必须与 ``src.desktop.app`` 的 ``DATA_DIR`` 一致。

    安装版两者都是 ``LIUHAO_DATA_DIR``；源码直跑时桌面壳的 ``DATA_DIR`` 是
    ``<仓库>/data``（见 ``src/desktop/__init__.py``），而实例锁在仓库根，两者
    不能混用，否则找不到已驻留的托盘窗口。
    """
    override = (os.environ.get("LIUHAO_DATA_DIR") or "").strip()
    base = Path(override).expanduser().resolve() if override else ROOT / "data"
    return str(base)


def _instance_url_healthy(url: str) -> bool:
    """探测持有者公布的地址是否真能服务，用于区分「在跑」与「卡死」。"""
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(url)
    host = parts.hostname or "127.0.0.1"
    if host in ("0.0.0.0", "::", "::0"):
        host = "127.0.0.1"
    netloc = f"{host}:{parts.port}" if parts.port else host
    target = urlunsplit((parts.scheme or "http", netloc, "/v1/health", "", ""))
    try:
        from src.desktop.probe import http_json

        info = http_json(target, timeout=2.0)
        body = info.get("body")
        # 2xx 不等于"我们的服务活着"：端口上可能是别的程序。
        return (
            bool(info.get("ok"))
            and isinstance(body, dict)
            and body.get("service") == "liuhao-gateway"
        )
    except Exception:  # noqa: BLE001 - 探测失败即视为暂时无响应
        return False


def _show_instance_notice(text: str, *, warning: bool) -> None:
    """无控制台（快捷方式 pythonw）时用系统弹窗交付"一句人话"。

    有控制台时只打印：终端里跑测试/运维不该被模态框挡住。
    """
    if not _headless():
        return
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        if warning:
            messagebox.showwarning("贾维斯已在运行（服务无响应）", text, parent=root)
        else:
            messagebox.showinfo("贾维斯已在运行", text, parent=root)
        root.destroy()
    except Exception:  # noqa: BLE001 - 弹窗失败时至少日志里已经有一句人话
        pass


def _handoff_to_running_instance(holder: dict, *, no_browser: bool) -> int:
    """第二次双击：唤起已运行窗口 + 给一句人话，绝不起第二份服务。

    三种前置状态各自有明确说法（也正是验收要求的三态）：
      * 托盘已驻留 → ``request_show`` 让已有状态窗口到前台；
      * 服务在跑（无托盘）→ 打开它公布的控制台地址；
      * 服务卡死（锁在、``/v1/health`` 不通）→ 明确说"暂时没有响应"，并给出
        进程号与后续动作，而不是静默退出。

    这里不打印 ``InstanceLockBusy.describe()``：那句是"网关拒绝启动"的措辞
    （结尾"本次启动已退出"），与本分支"已为你切换/打开"直接矛盾。
    """
    url = str(holder.get("url") or "").strip().rstrip("/")
    pid = holder.get("pid")
    detail = "，".join(
        part for part in (f"pid={pid}" if pid else "", url) if part
    )
    print(f"[instance] 贾维斯已经在运行（{detail}）。" if detail else "[instance] 贾维斯已经在运行。", flush=True)
    try:
        from src.desktop.tray import request_show

        shown = request_show(_tray_instance_key(), timeout=0.0)
    except Exception:  # noqa: BLE001 - 没有 pywin32 时退回控制台/提示
        shown = False
    healthy = bool(url) and _instance_url_healthy(url)

    if shown:
        if healthy:
            summary = "贾维斯已经在运行，已为你切换到正在运行的状态窗口。"
        else:
            summary = (
                "贾维斯已经在运行，但服务暂时没有响应。\n\n"
                f"进程 pid={pid}。已为你切换到状态窗口，可在其中查看状态或重新启动服务；"
                "本次没有启动第二个服务。"
            )
    elif healthy:
        if no_browser:
            # --no-browser（运维/自测）下不能声称"已打开控制台"。
            summary = f"贾维斯已经在运行，控制台地址：{url}/"
        else:
            summary = f"贾维斯已经在运行，已为你打开控制台：{url}/"
            with contextlib.suppress(webbrowser.Error):
                webbrowser.open(_console_url_with_proof(url))
    elif url:
        summary = (
            "贾维斯已经在运行，但服务暂时没有响应。\n\n"
            f"进程 pid={pid}，地址 {url}。请打开任务栏通知区的贾维斯图标查看状态，"
            "或结束该进程后重试；本次没有启动第二个服务。"
        )
    else:
        summary = "贾维斯已经在运行。请使用任务栏通知区（托盘）的图标打开窗口或控制台。"
    print(f"[instance] {summary}")
    if _headless():
        # pythonw 下 print 进 launcher.log：把判断依据一并留档，便于远程排障。
        _log_launcher(
            f"[instance] 双开拦截：唤起窗口={shown} 服务可访问={healthy} "
            f"pid={pid or '-'} url={url or '-'}"
        )
    _show_instance_notice(summary, warning=not healthy)
    return 0


def _env_api_port() -> int:
    """默认端口：向导里选过（``LIUHAO_API_PORT``）就用那个，否则 8080。"""
    try:
        return int((os.environ.get("LIUHAO_API_PORT") or "").strip() or DEFAULT_API_PORT)
    except ValueError:
        return DEFAULT_API_PORT


def _run_diagnose() -> int:
    """一键诊断：网关没起来也能跑，只做离线采集，不依赖 HTTP。

    复用 ``src.desktop.diagnostics.create_bundle``：版本号、数据目录、端口、
    日志尾部一直都在包里；本版额外加入 ``startup_check.json``（端口可绑 /
    目录可写 / 各库打开状态 / 静态资源）、``bootstrap.log.txt``（数据与日志
    目录都不可写时的唯一证据）与 ``recent_events.txt``（最近 20 条 evt=）。
    """
    from src.desktop.diagnostics import create_bundle

    print("=" * 62)
    print(f"{BRAND_LOCKUP} — 一键诊断（不需要服务在运行）")
    print("=" * 62)
    try:
        omitted: list[dict[str, Any]] = []
        bundle = create_bundle(
            probe_network=False, ports=[_env_api_port()], omitted_out=omitted
        )
    except Exception as exc:  # noqa: BLE001 - 诊断失败也要给出可读原因
        print(f"[FAIL] 诊断包生成失败：{type(exc).__name__}: {exc}")
        return 1

    print(f"[OK] 诊断包已生成：{bundle}")
    if omitted:
        print(
            f"[WARN] 有 {len(omitted)} 个文件因含密钥未包含，清单见包内 "
            f"{OMITTED_MEMBERS_NAME}：" + "、".join(str(item["name"]) for item in omitted)
        )
    try:
        import zipfile

        with zipfile.ZipFile(bundle) as archive:
            names = archive.namelist()
            try:
                first_line = (
                    archive.read("SUMMARY.txt").decode("utf-8", "replace").splitlines()[0].strip()
                )
            except (KeyError, IndexError):
                first_line = ""
        if first_line:
            print(f"[结论] {first_line}")
        print(f"     包含 {len(names)} 个文件：{'、'.join(names)}")
    except Exception:  # noqa: BLE001 - 列不出清单不影响"包已生成"这一结论
        pass
    print("     先读包内的 SUMMARY.txt（第一行结论 + 3-5 条依据），再按需看 README.txt。")
    print("     请把这个 zip 发给技术支持：包内不含密钥，路径中的用户名已隐去。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="启动贾维斯：FastAPI 网关 + 控制台")
    parser.add_argument(
        "--host",
        default=DEFAULT_API_HOST,
        help=(
            f"绑定地址（默认 {DEFAULT_API_HOST}，只本机可达）。"
            "手机/其它电脑要访问时用 --host 0.0.0.0 直连（登录自带 5 次/300 秒锁定）；"
            "本版本不提供 8090 配对中继"
        ),
    )
    parser.add_argument("--api-port", type=int, default=_env_api_port())
    parser.add_argument("--ui-port", type=int, default=DEFAULT_UI_PORT)
    parser.add_argument("--backend-only", action="store_true", help="只起网关，不起控制台")
    parser.add_argument(
        "--vite",
        action="store_true",
        help="开发模式：用 vite 起控制台（热更新），默认是单端口直接服务 dist。",
    )
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument(
        "--https",
        action="store_true",
        help="叠加本地 HTTPS 反向代理（默认端口 8443），供手机端 PWA 安装。需自签证书。",
    )
    parser.add_argument("--https-port", type=int, default=8443, help="HTTPS 代理监听端口（默认 8443）")
    parser.add_argument(
        "--diagnose",
        action="store_true",
        help=(
            "只生成诊断包（zip）并打印路径，不启动任何服务。"
            "网关没起来时同样可用：离线检查数据/日志目录、端口、各库、"
            "驾驶舱静态资源，并收集 bootstrap.log 与最近 20 条 evt= 事件"
        ),
    )
    args = parser.parse_args()

    if args.backend_only and args.vite:
        parser.error("--backend-only 与 --vite 互斥（前者不含控制台）")

    from src.logging_config import ensure_utf8_streams

    ensure_utf8_streams()
    atexit.register(_shutdown_all)

    # 配置里的 handler 使用相对路径 ``logs/...``；先建目录再交给 uvicorn，
    # 否则首次写入时 handler 打不开文件、日志被静默丢弃。
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    # .env 里的 SECRET_KEY / APP_ENV 必须先可见，否则 preflight 与部署姿态都不成立。
    _load_dotenv()
    # 一键诊断：必须在拉起任何子进程、做任何身份自举之前返回；它不依赖 HTTP，
    # 也不写入业务数据（只做一次有界可写探测），因此"网关根本没起来"时也能跑。
    if args.diagnose:
        return _run_diagnose()
    # 没有密钥时身份行会被内核全部拒绝（= 无人可登录）。首次启动必须先自举。
    if not _ensure_identity_integrity_key():
        return 1
    # 网关带鉴权闸门；把本机登记表/凭据指过去，否则没人能登录。
    _point_at_console_stores()

    print("=" * 62)
    print(f"{BRAND_LOCKUP} — 启动")
    print("=" * 62)

    # 单实例前置检查：双击两次、托盘与命令行同时启动时，第二个入口在拉起
    # 任何子进程之前就停住，并告诉用户「已经在哪儿跑着」。
    # 真正的互斥由网关进程持有（见 src/common/instance_lock.py），这里的探测
    # 只负责把提示做早、做可读；即使探测与启动之间有竞态，网关也会兜住。
    busy = _probe_running_instance()
    if busy is not None:
        _message, holder = busy
        return _handoff_to_running_instance(holder, no_browser=args.no_browser)

    # 自用版默认单端口：网关既服务 API 又服务控制台 dist，对外只暴露一个端口，
    # 对"双击就用"的场景最友好。vite 开发模式（--vite）需要 Node + 热更新。
    single_port = not args.backend_only and not args.vite

    # 启动前先确认配置端口可用。绑定 0.0.0.0 时端口占用检查用 127.0.0.1 探测即可。
    # 顺序很重要：先确认「没有第二个贾维斯在跑」，再看端口，否则会出现
    # "端口被占" + "已经在运行" 两句自相矛盾的提示。
    probe_host = args.host if args.host not in ("0.0.0.0", "::") else "127.0.0.1"
    api_port = _resolve_port_or_explain(probe_host, args.api_port, "网关", "--api-port")
    if api_port is None:
        return 2
    args.api_port = api_port
    os.environ["LIUHAO_API_PORT"] = str(api_port)
    if args.vite:
        ui_port = _resolve_port_or_explain(
            probe_host, args.ui_port, "控制台(vite)", "--ui-port"
        )
        if ui_port is None:
            return 2
        args.ui_port = ui_port
    if args.https:
        https_port = _resolve_port_or_explain(
            probe_host, args.https_port, "HTTPS 代理", "--https-port"
        )
        if https_port is None:
            return 2
        args.https_port = https_port

    # 只有显式绑定非回环地址才算"对外"；默认 127.0.0.1 不告警。
    _warn_if_exposed(args.host, args.api_port)

    # 展示地址取自实际生效的绑定配置：通配绑定（0.0.0.0/::）换成回环，
    # 否则用户看到的 URL 打不开；真正监听地址仍由 args.host 决定。
    shown_host = _display_host(args.host)
    api_base = f"http://{shown_host}:{args.api_port}"
    ui_base = api_base if single_port else f"http://{shown_host}:{args.ui_port}"
    # Windows 下 urllib 无法 connect 到 0.0.0.0（WinError 10049），健康检查必须
    # 用回环地址；用户可见的地址仍由 _print_lan_urls 打 LAN IP。
    probe_api_base = f"http://{probe_host}:{args.api_port}"
    probe_ui_base = probe_api_base if single_port else f"http://{probe_host}:{args.ui_port}"

    print("[1/2] 网关（FastAPI / uvicorn）")
    # 不再把 ``--log-config`` 路径交给 uvicorn CLI：uvicorn 的 YAML 分支用
    # ``open()`` 不带 encoding，中文 Windows 下会按 cp936 解码 UTF-8 的
    # logging.yaml 并在导入 app 前崩溃。改由 ``src.gateway`` 入口按 UTF-8
    # 读成映射后交给 uvicorn（见 src/logging_config.py）。
    gateway_env = dict(os.environ)
    gateway_env["HOST"] = args.host
    gateway_env["PORT"] = str(args.api_port)
    # 直接关掉启动器窗口（不按 Ctrl+C）时，网关不能变成占着端口的孤儿进程。
    gateway_env["LIUHAO_PARENT_PID"] = str(os.getpid())
    # 启动器打开控制台时会带上桌面端一次性证明；把网关标记为可信本机入口，
    # 首次注册/取码/重置才会强制“回环 + 证明 + 同 Windows 用户”。
    gateway_env["LIUHAO_TRUSTED_LAUNCH"] = "1"
    if LOG_CONFIG.is_file():
        gateway_env["LIUHAO_LOG_CONFIG"] = str(LOG_CONFIG)
    python_path = os.pathsep.join(
        [str(ROOT), gateway_env.get("PYTHONPATH", "")]
    ).strip(os.pathsep)
    gateway_env["PYTHONPATH"] = python_path
    gateway = _spawn(
        [_venv_python(), "-u", "-m", "src.gateway"],
        GATEWAY_CWD,
        "gateway",
        env=gateway_env,
    )
    # 连接失败 = 还在启动（端口在路由导入后才绑定），必须等 /v1/startup 给结论。
    if _wait_gateway_ready(probe_api_base, gateway) is None:
        _shutdown_all()
        return 1
    # 端口探针可能被「已经在这个端口上的另一个贾维斯」满足（双击竞态时第二个
    # 启动器正是这种情况）。自己的子进程若已退出，就不能宣称启动成功，
    # 而要给出和网关入口同一句可读提示。
    if gateway.poll() is not None:
        busy = _probe_running_instance()
        if busy is not None:
            print(f"[instance] {busy[0]}", flush=True)
            return 0
        print(
            "[FAIL] 网关在报告就绪后立即退出，请查看日志："
            f"{_gate_log_hint()}",
            flush=True,
        )
        _shutdown_all()
        return 1

    if args.backend_only:
        print()
        print(f"网关已就绪：  {api_base}")
        print(f"API 文档：    {api_base}/docs")
        print("（--backend-only：控制台未启动）")
        _print_lan_urls(args.api_port, args.host)
        return _serve_forever()

    if single_port:
        dist = _console_dist()
        if dist is None:
            print("  [FAIL] 单端口模式需要控制台构建产物")
            print(f"         未找到 {os.environ.get(CONSOLE_DIST_ENV) or DEFAULT_CONSOLE_DIST}/index.html")
            print("         请先在 apps/console/console 下执行 npm run build")
            print("         或用 --vite 启动开发模式（需 Node.js）")
            _shutdown_all()
            return 1
        print("[2/2] 控制台由网关直接服务（单端口）")
        print(f"      源: {dist}")
        if not _wait_http_ok(
            probe_api_base + "/",
            STARTUP_TIMEOUT_S,
            "控制台（同端口）",
            hint=f"日志：{_gate_log_hint()}",
        ):
            _shutdown_all()
            return 1
    else:
        print("[2/2] 控制台（vite 开发模式）")
        vite_entry = CONSOLE_DIR / "node_modules" / "vite" / "bin" / "vite.js"
        if not vite_entry.exists():
            print(f"  [FAIL] 没找到 {vite_entry}")
            print("         请先在 apps/console/console 下执行 npm install")
            print("         或用默认单端口模式（网关直接服务已构建的 dist）")
            _shutdown_all()
            return 1
        _spawn(
            [
                _node(),
                str(vite_entry),
                "--host",
                args.host,
                "--port",
                str(args.ui_port),
            ],
            CONSOLE_DIR,
            "console",
        )
        if not _wait_http_ok(
            probe_ui_base + "/", STARTUP_TIMEOUT_S, "控制台", hint=f"日志：{_gate_log_hint()}"
        ):
            _shutdown_all()
            return 1
        _wait_http_ok(f"{probe_ui_base}/v1/health", 15.0, "控制台→网关代理")

    if args.https:
        _start_https_proxy(args, single_port)

    url_file = _write_current_url(args.api_port, ui_base)
    _log_launcher(f"[ok] 就绪 {ui_base}（api_port={args.api_port}）")

    print()
    print("=" * 62)
    print("贾维斯已就绪")
    print(f"  控制台    {ui_base}")
    print(f"  API       {api_base}     文档 {api_base}/docs")
    print(f"  健康探针  {api_base}/v1/health")
    print(f"  日志      {_gate_log_hint()}")
    if url_file is not None:
        print(f"  当前地址  {url_file}")
    _print_lan_urls(args.api_port, args.host, args.https_port if args.https else None)
    print()
    print("  本窗口只显示状态，不需要输入任何内容。")
    print("  关闭此窗口会停止本地服务（控制台将无法访问）；使用期间请保持打开，可最小化。")
    print("  Ctrl+C 同样会停止服务（子进程会被一起带走）")
    print("=" * 62)

    if not args.no_browser:
        with contextlib.suppress(webbrowser.Error):
            webbrowser.open(_console_url_with_proof(probe_ui_base))

    return _serve_forever()


def _console_url_with_proof(base: str) -> str:
    """把本机恢复码交接的一次性令牌放进 URL fragment。

    回环不等于“本人”：同机其他 Windows 账户也能访问 127.0.0.1。启动器与
    桌面壳一样，通过网关的 DACL 命名管道申请令牌，避免“忘记密码 → 本机
    生成恢复码”在共享电脑上被其他账户截走。
    """
    try:
        from src.common.local_proof_ipc import console_fragment

        separator = "&" if "#" in base else "#"
        return f"{base}{separator}{console_fragment()}"
    except Exception as exc:  # noqa: BLE001 - 证明不可用不应阻止打开控制台
        print(f"[recovery] 本机恢复证明通道不可用（{exc}）；")
        print("           控制台仍可打开，但“本机生成恢复码”会要求从桌面端重新进入。")
        return base


def _print_lan_urls(api_port: int, host: str, https_port: int | None = None) -> None:
    """在控制台打印局域网访问地址，方便手机手输（本产品没有二维码配对）。

    默认只绑回环时这些地址根本连不上，打印出来只会误导用户，所以直接跳过。
    """
    if _is_loopback(host):
        return
    lan_ips = _lan_addresses()
    if not lan_ips:
        return
    print()
    print("  局域网访问（手机连同一 Wi-Fi）：")
    for ip in lan_ips:
        print(f"    http://{ip}:{api_port}/")
        if https_port is not None:
            print(f"    https://{ip}:{https_port}/   (PWA，需信任自签证书)")


def _serve_forever() -> int:
    try:
        while _children:
            for proc in list(_children):
                if proc.poll() is not None:
                    print(f"  [!] 子进程 pid={proc.pid} 退出，code={proc.returncode}")
                    _children.remove(proc)
            if not _children:
                break
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\n收到 Ctrl+C，正在关闭…")
    finally:
        _shutdown_all()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
