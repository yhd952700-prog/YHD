#!/usr/bin/env python
"""鎏灏（LIUHAO X）一键启动器。

一条命令把「网关 + 驾驶舱」拉起来，并等健康探针真正通过后再交还控制权。

    .venv\\Scripts\\python.exe scripts\\start_liuhao.py          # 全栈
    .venv\\Scripts\\python.exe scripts\\start_liuhao.py --backend-only
    .venv\\Scripts\\python.exe scripts\\start_liuhao.py --no-browser

设计约束：
  * 不依赖 Docker（本机 Docker daemon 常常没起），走原生进程。
  * 本机有 HTTP 代理，所有本机探测必须显式绕代理，否则会误报不通。
  * Ctrl+C 时把子进程一起带走，不留孤儿 uvicorn / vite。
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


def _spawn(cmd: list[str], cwd: Path, label: str) -> subprocess.Popen:
    print(f"  [$] ({cwd.name}) {' '.join(cmd)}")
    popen = subprocess.Popen(cmd, cwd=str(cwd))
    _children.append(popen)
    print(f"       {label} pid={popen.pid}")
    return popen


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
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args()

    atexit.register(_shutdown_all)

    api_base = f"http://{args.host}:{args.api_port}"
    ui_base = f"http://{args.host}:{args.ui_port}"

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

    print("[2/2] 驾驶舱（vite）")
    vite_entry = CONSOLE_DIR / "node_modules" / "vite" / "bin" / "vite.js"
    if not vite_entry.exists():
        print(f"  [FAIL] 没找到 {vite_entry}")
        print("         请先在 apps/console/console 下执行 npm install")
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
