#!/usr/bin/env python
"""鎏灏（LIUHAO X）一键启动器。

一条命令把「网关 + 驾驶舱」拉起来，并等健康探针真正通过后再交还控制权。

    python scripts\\start_liuhao.py          # 单端口（默认）
    python scripts\\start_liuhao.py --backend-only
    python scripts\\start_liuhao.py --no-browser
    python scripts\\start_liuhao.py --vite   # vite 热更新开发模式
    python scripts\\start_liuhao.py --https  # 单端口 + 本地 HTTPS

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
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONSOLE_DIR = ROOT / "apps" / "console" / "console"

#: 以 ``python scripts/start_liuhao.py`` 方式运行时，``sys.path[0]`` 是 scripts/
#: 而不是仓库根，稍后的 ``import src.*``（启动门、日志配置）会 ModuleNotFoundError。
#: 安装版由 launch_installed.py 设置 PYTHONPATH；开发/自测路径在这里补上。
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.common.instance_lock import InstanceLockBusy, InstanceLock, instance_root  # noqa: E402

#: 唯一生效的日志配置。由 ``python -m src.gateway`` 子进程按 UTF-8 读取后
#: 以映射交给 uvicorn：10 MiB × 5 份轮转，INFO 进 logs/liuhao.log，ERROR
#: 另进 logs/error.log（见配置文件头注释）。
LOG_CONFIG = ROOT / "config" / "logging.yaml"
LOG_DIR = ROOT / "logs"

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

#: 自动换端口时往上最多扫多少个端口。
PORT_SCAN_LIMIT = 100

_children: list[subprocess.Popen] = []


def _port_in_use(host: str, port: int) -> bool:
    """True when ``host:port`` is already bound by another process."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.5)
    try:
        sock.bind((host, port))
    except OSError:
        return True
    finally:
        sock.close()
    return False


def _ensure_free_port(host: str, requested: int, label: str) -> int:
    """Return ``requested`` if free, else the next free port above it.

    双击启动最常见的坑是"上次的进程没退干净、8080 被占着"，用户看到 uvicorn
    报一句 ``address already in use`` 就懵了。这里在拉起前就把端口让出来，
    并打印换到了哪个端口，避免用户再去查进程。
    """
    if not _port_in_use(host, requested):
        return requested
    for candidate in range(requested + 1, requested + 1 + PORT_SCAN_LIMIT):
        if not _port_in_use(host, candidate):
            print(
                f"  [warn] {label} 端口 {requested} 被占用，已自动改用 {candidate}"
            )
            return candidate
    raise SystemExit(
        f"找不到可用端口：在 {requested}..{requested + PORT_SCAN_LIMIT} 之间都被占用"
    )


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
        raise SystemExit("未找到 node —— 驾驶舱需要 Node.js，请先安装或加进 PATH。")
    return found


def _console_dist() -> Path | None:
    """已构建的驾驶舱产物目录；没有就返回 None。"""
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
                if 200 <= resp.status < 500:
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
    )
    if result.get("console_ok"):
        if result.get("state") == "degraded":
            print(f"  [warn] {result.get('detail')}", flush=True)
        else:
            print(f"  [ok] {label}就绪（{result.get('elapsed')}s）", flush=True)
        return result
    print(f"  [FAIL] {result.get('detail')}", flush=True)
    return None


def _spawn(cmd: list[str], cwd: Path, label: str, env: dict | None = None) -> subprocess.Popen:
    print(f"  [$] ({cwd.name}) {' '.join(cmd)}")
    popen = subprocess.Popen(cmd, cwd=str(cwd), env=env)
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


def main() -> int:
    parser = argparse.ArgumentParser(description="启动鎏灏：FastAPI 网关 + 驾驶舱")
    parser.add_argument(
        "--host",
        default=DEFAULT_API_HOST,
        help=(
            f"绑定地址（默认 {DEFAULT_API_HOST}，只本机可达）。"
            "需要手机/其它电脑访问时显式传 0.0.0.0 或指定网卡地址"
        ),
    )
    parser.add_argument("--api-port", type=int, default=DEFAULT_API_PORT)
    parser.add_argument("--ui-port", type=int, default=DEFAULT_UI_PORT)
    parser.add_argument("--backend-only", action="store_true", help="只起网关，不起驾驶舱")
    parser.add_argument(
        "--vite",
        action="store_true",
        help="开发模式：用 vite 起驾驶舱（热更新），默认是单端口直接服务 dist。",
    )
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument(
        "--https",
        action="store_true",
        help="叠加本地 HTTPS 反向代理（默认端口 8443），供手机端 PWA 安装。需自签证书。",
    )
    parser.add_argument("--https-port", type=int, default=8443, help="HTTPS 代理监听端口（默认 8443）")
    args = parser.parse_args()

    if args.backend_only and args.vite:
        parser.error("--backend-only 与 --vite 互斥（前者不含驾驶舱）")

    from src.logging_config import ensure_utf8_streams

    ensure_utf8_streams()
    atexit.register(_shutdown_all)

    # 配置里的 handler 使用相对路径 ``logs/...``；先建目录再交给 uvicorn，
    # 否则首次写入时 handler 打不开文件、日志被静默丢弃。
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    # .env 里的 SECRET_KEY / APP_ENV 必须先可见，否则 preflight 与部署姿态都不成立。
    _load_dotenv()
    # 没有密钥时身份行会被内核全部拒绝（= 无人可登录）。首次启动必须先自举。
    if not _ensure_identity_integrity_key():
        return 1
    # 网关带鉴权闸门；把本机登记表/凭据指过去，否则没人能登录。
    _point_at_console_stores()

    print("=" * 62)
    print("鎏灏 LIUHAO X — 启动")
    print("=" * 62)

    # 单实例前置检查：双击两次、托盘与命令行同时启动时，第二个入口在拉起
    # 任何子进程之前就停住，并告诉用户「已经在哪儿跑着」。
    # 真正的互斥由网关进程持有（见 src/common/instance_lock.py），这里的探测
    # 只负责把提示做早、做可读；即使探测与启动之间有竞态，网关也会兜住。
    busy = _probe_running_instance()
    if busy is not None:
        message, holder = busy
        print(f"[instance] {message}")
        url = (holder.get("url") or "").strip()
        if url and not args.no_browser:
            with contextlib.suppress(webbrowser.Error):
                webbrowser.open(url)
        return 0

    # 自用版默认单端口：网关既服务 API 又服务驾驶舱 dist，对外只暴露一个端口，
    # 对"双击就用"的场景最友好。vite 开发模式（--vite）需要 Node + 热更新。
    single_port = not args.backend_only and not args.vite

    # 启动前先把端口让出来。绑定 0.0.0.0 时端口占用检查用 127.0.0.1 探测即可。
    # 顺序很重要：先确认「没有第二个鎏灏在跑」，再看端口，否则会出现
    # "已自动改用 18081" + "已经在运行" 两句自相矛盾的提示。
    probe_host = args.host if args.host not in ("0.0.0.0", "::") else "127.0.0.1"
    args.api_port = _ensure_free_port(probe_host, args.api_port, "网关")
    if args.vite:
        args.ui_port = _ensure_free_port(probe_host, args.ui_port, "驾驶舱(vite)")
    if args.https:
        args.https_port = _ensure_free_port(probe_host, args.https_port, "HTTPS 代理")

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
    if LOG_CONFIG.is_file():
        gateway_env["LIUHAO_LOG_CONFIG"] = str(LOG_CONFIG)
    python_path = os.pathsep.join(
        [str(ROOT), gateway_env.get("PYTHONPATH", "")]
    ).strip(os.pathsep)
    gateway_env["PYTHONPATH"] = python_path
    gateway = _spawn(
        [_venv_python(), "-u", "-m", "src.gateway"],
        ROOT,
        "gateway",
        env=gateway_env,
    )
    # 连接失败 = 还在启动（端口在路由导入后才绑定），必须等 /v1/startup 给结论。
    if _wait_gateway_ready(probe_api_base, gateway) is None:
        _shutdown_all()
        return 1
    # 端口探针可能被「已经在这个端口上的另一个鎏灏」满足（双击竞态时第二个
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
        print("（--backend-only：驾驶舱未启动）")
        _print_lan_urls(args.api_port, args.host)
        return _serve_forever()

    if single_port:
        dist = _console_dist()
        if dist is None:
            print("  [FAIL] 单端口模式需要驾驶舱构建产物")
            print(f"         未找到 {os.environ.get(CONSOLE_DIST_ENV) or DEFAULT_CONSOLE_DIST}/index.html")
            print("         请先在 apps/console/console 下执行 npm run build")
            print("         或用 --vite 启动开发模式（需 Node.js）")
            _shutdown_all()
            return 1
        print("[2/2] 驾驶舱由网关直接服务（单端口）")
        print(f"      源: {dist}")
        if not _wait_http_ok(
            probe_api_base + "/",
            STARTUP_TIMEOUT_S,
            "驾驶舱（同端口）",
            hint=f"日志：{_gate_log_hint()}",
        ):
            _shutdown_all()
            return 1
    else:
        print("[2/2] 驾驶舱（vite 开发模式）")
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
            probe_ui_base + "/", STARTUP_TIMEOUT_S, "驾驶舱", hint=f"日志：{_gate_log_hint()}"
        ):
            _shutdown_all()
            return 1
        _wait_http_ok(f"{probe_ui_base}/v1/health", 15.0, "驾驶舱→网关代理")

    if args.https:
        _start_https_proxy(args, single_port)

    print()
    print("=" * 62)
    print("鎏灏已就绪")
    print(f"  驾驶舱    {ui_base}")
    print(f"  API       {api_base}     文档 {api_base}/docs")
    print(f"  健康探针  {api_base}/v1/health")
    _print_lan_urls(args.api_port, args.host, args.https_port if args.https else None)
    print("  Ctrl+C 停止（子进程会被一起带走）")
    print("=" * 62)

    if not args.no_browser:
        with contextlib.suppress(webbrowser.Error):
            webbrowser.open(probe_ui_base)

    return _serve_forever()


def _print_lan_urls(api_port: int, host: str, https_port: int | None = None) -> None:
    """在控制台打印局域网访问地址，方便手机扫码/手输。

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
