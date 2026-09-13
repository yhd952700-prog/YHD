#!/usr/bin/env python
"""鎏灏（LIUHAO X）一键启动器。

一条命令把「网关 + 驾驶舱」拉起来，并等健康探针真正通过后再交还控制权。

    .venv\\Scripts\\python.exe scripts\\start_liuhao.py          # 全栈
    .venv\\Scripts\\python.exe scripts\\start_liuhao.py --backend-only
    .venv\\Scripts\\python.exe scripts\\start_liuhao.py --no-browser
    .venv\\Scripts\\python.exe scripts\\start_liuhao.py --single-port --https   # 单端口 + 本地 HTTPS

设计约束：
  * 不依赖 Docker（本机 Docker daemon 常常没起），走原生进程。
  * 本机有 HTTP 代理，所有本机探测必须显式绕代理，否则会误报不通。
  * Ctrl+C 时把子进程一起带走，不留孤儿 uvicorn / vite。
  * --https 会在控制台之上叠加一个本地 TLS 反向代理（scripts/ops/https_proxy.py），
    用于让手机端 PWA 处于安全上下文、从而能「安装应用 / 添加到主屏幕」。
    证书为自签，需放进 deploy/cloud/certs/ 并由手机一次性信任（见交付说明）。
"""

from __future__ import annotations

import argparse
import atexit
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONSOLE_DIR = ROOT / "apps" / "console" / "console"

#: 单端口模式下由网关服务的前端构建产物。
#: 权威实现在 src/gateway/console_static.py；这里重复声明只是为了让启动器
#: 能在**拉起进程之前**就给出可读的报错，而不是等健康探针超时。
CONSOLE_DIST_ENV = "LIUHAO_CONSOLE_DIST"
DEFAULT_CONSOLE_DIST = CONSOLE_DIR / "dist"

DEFAULT_API_HOST = "127.0.0.1"
DEFAULT_API_PORT = 8080
DEFAULT_UI_PORT = 5173

STARTUP_TIMEOUT_S = 120.0
POLL_INTERVAL_S = 0.5

_children: list[subprocess.Popen] = []


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


def _no_proxy_opener():
    """构造一个**不走代理**的 opener。

    本机 env 里常有活的 http_proxy，直接用 urlopen 打 127.0.0.1 会被代理拦掉，
    报 "upstream connect failed"，看起来像服务没起来——这是本机第一大坑。
    """
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _wait_http_ok(url: str, timeout: float, label: str) -> bool:
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
    print(f"  [FAIL] {label} 在 {timeout:.0f}s 内没起来，详见上方进程输出")
    return False


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


def _start_https_proxy(args: argparse.Namespace) -> None:
    """在控制台之上叠加本地 TLS 反向代理（受管子进程，Ctrl+C 一起带走）。"""
    https_port = args.https_port
    backend_port = args.api_port if args.single_port else args.ui_port
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
    proxy_env["LIUHAO_TLS_PORT"] = str(https_port)
    proxy_env["LIUHAO_TLS_CERT"] = cert
    proxy_env["LIUHAO_TLS_KEY"] = key
    https_proxy = ROOT / "scripts" / "ops" / "https_proxy.py"
    if not https_proxy.is_file():
        print(f"  [skip] HTTPS 代理未启动：找不到 {https_proxy}")
        return
    _spawn([_venv_python(), "-u", str(https_proxy)], ROOT, "https-proxy", proxy_env)
    _wait_https_ok(f"https://{args.host}:{https_port}/", 30.0, "HTTPS 代理")
    print(f"      手机端安装地址： https://{args.host}:{https_port}/")


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


def main() -> int:
    parser = argparse.ArgumentParser(description="启动鎏灏：FastAPI 网关 + 驾驶舱")
    parser.add_argument("--host", default=DEFAULT_API_HOST, help=f"绑定地址（默认 {DEFAULT_API_HOST}）")
    parser.add_argument("--api-port", type=int, default=DEFAULT_API_PORT)
    parser.add_argument("--ui-port", type=int, default=DEFAULT_UI_PORT)
    parser.add_argument("--backend-only", action="store_true", help="只起网关")
    parser.add_argument(
        "--single-port",
        action="store_true",
        help="单端口模式：只起网关，由网关直接服务驾驶舱构建产物（dist）。"
             "适用于容器 / 只开放一个端口的部署。",
    )
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument(
        "--https",
        action="store_true",
        help="叠加本地 HTTPS 反向代理（默认端口 8443），供手机端 PWA 安装。需自签证书。",
    )
    parser.add_argument("--https-port", type=int, default=8443, help="HTTPS 代理监听端口（默认 8443）")
    args = parser.parse_args()

    if args.backend_only and args.single_port:
        parser.error("--backend-only 与 --single-port 互斥（前者不含驾驶舱）")

    atexit.register(_shutdown_all)

    api_base = f"http://{args.host}:{args.api_port}"
    ui_base = api_base if args.single_port else f"http://{args.host}:{args.ui_port}"

    print("=" * 62)
    print("鎏灏 LIUHAO X — 启动")
    print("=" * 62)

    print("[1/2] 网关（FastAPI / uvicorn）")
    _spawn(
        [
            _venv_python(),
            "-u",
            "-m",
            "uvicorn",
            "src.gateway.main:app",
            "--host",
            args.host,
            "--port",
            str(args.api_port),
        ],
        ROOT,
        "gateway",
    )
    if not _wait_http_ok(f"{api_base}/v1/health", STARTUP_TIMEOUT_S, "网关"):
        _shutdown_all()
        return 1

    if args.backend_only:
        print()
        print(f"网关已就绪：  {api_base}")
        print(f"API 文档：    {api_base}/docs")
        print("（--backend-only：驾驶舱未启动）")
        return _serve_forever()

    if args.single_port:
        dist = _console_dist()
        if dist is None:
            print("  [FAIL] 单端口模式需要驾驶舱构建产物")
            print(f"         未找到 {os.environ.get(CONSOLE_DIST_ENV) or DEFAULT_CONSOLE_DIST}/index.html")
            print("         请先在 apps/console/console 下执行 npm run build")
            _shutdown_all()
            return 1
        print("[2/2] 驾驶舱由网关直接服务（单端口）")
        print(f"      源: {dist}")
        if not _wait_http_ok(api_base + "/", STARTUP_TIMEOUT_S, "驾驶舱（同端口）"):
            _shutdown_all()
            return 1
    else:
        print("[2/2] 驾驶舱（vite）")
        vite_entry = CONSOLE_DIR / "node_modules" / "vite" / "bin" / "vite.js"
        if not vite_entry.exists():
            print(f"  [FAIL] 没找到 {vite_entry}")
            print("         请先在 apps/console/console 下执行 npm install")
            print("         或用 --single-port 让网关直接服务已构建的 dist")
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
        if not _wait_http_ok(ui_base + "/", STARTUP_TIMEOUT_S, "驾驶舱"):
            _shutdown_all()
            return 1
        _wait_http_ok(f"{ui_base}/v1/health", 15.0, "驾驶舱→网关代理")

    if args.https:
        _start_https_proxy(args)

    print()
    print("=" * 62)
    print("鎏灏已就绪")
    print(f"  驾驶舱    {ui_base}")
    print(f"  API       {api_base}     文档 {api_base}/docs")
    print(f"  健康探针  {api_base}/v1/health")
    print("  Ctrl+C 停止（子进程会被一起带走）")
    print("=" * 62)

    if not args.no_browser:
        try:
            webbrowser.open(ui_base)
        except webbrowser.Error:
            pass

    return _serve_forever()


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
