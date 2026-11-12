#!/usr/bin/env python3
"""电脑端的「手机访问」开关与管理工具。

默认关闭：只有在这台电脑上显式执行 ``python scripts/mobile_access.py start``
（或启动器 ``--mobile-access``）时，才监听局域网端口。

常用命令::

    python scripts/mobile_access.py start      # 开启，并打印配对码 / 配对链接
    python scripts/mobile_access.py status     # 当前配对码、剩余时间、设备数
    python scripts/mobile_access.py devices    # 哪些手机连过、最近一次连接、IP
    python scripts/mobile_access.py pair       # 重新生成一次性配对码
    python scripts/mobile_access.py kick <id>  # 踢掉一台
    python scripts/mobile_access.py kick-all   # 一键踢掉全部（并换新配对码）
    python scripts/mobile_access.py stop       # 关闭，立刻断开所有手机

管理调用只走 ``127.0.0.1`` 的管理面，令牌存放在数据目录，手机拿不到。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parents[1]
RELAY_SCRIPT = ROOT / "scripts" / "ops" / "lan_relay.py"
RELAY_DIR_REL = Path("mobile_access")
ADMIN_TOKEN_FILE = "admin.token"
STATE_FILE = "relay.json"
LOG_FILE = "relay.log"


def _data_dir() -> Path:
    raw = (os.environ.get("LIUHAO_DATA_DIR") or "").strip()
    if raw:
        return Path(raw).expanduser()
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home())
        return base / "LiuHao" / "data"
    base = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
    return base / "liuhao"


def _relay_dir() -> Path:
    path = _data_dir() / RELAY_DIR_REL
    path.mkdir(parents=True, exist_ok=True)
    return path


def _admin_token() -> str:
    path = _relay_dir() / ADMIN_TOKEN_FILE
    if not path.is_file():
        return ""
    return (path.read_text(encoding="utf-8") or "").strip()


def _read_state() -> Optional[Dict[str, Any]]:
    path = _relay_dir() / STATE_FILE
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (FileNotFoundError, OSError, ValueError):
        return None


def _request(method: str, path: str, *, timeout: float = 12.0) -> Dict[str, Any]:
    state = _read_state()
    if not state:
        raise SystemExit("手机访问没有开启。先运行：python scripts/mobile_access.py start")
    admin_port = int(state.get("admin_port") or 8091)
    token = _admin_token()
    if not token:
        raise SystemExit("缺少管理令牌。请重新运行 start。")
    request = urllib.request.Request(
        f"http://127.0.0.1:{admin_port}{path}",
        method=method,
        headers={"Authorization": f"Bearer {token}"},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise SystemExit(f"管理面返回 {exc.code}：{detail}")
    except (urllib.error.URLError, OSError):
        raise SystemExit("手机访问没有在运行（或已退出）。先运行 start。")
    try:
        payload = json.loads(raw)
    except ValueError:
        raise SystemExit("管理面返回了无法解析的内容。")
    return payload if isinstance(payload, dict) else {"data": payload}


def _is_running() -> bool:
    try:
        _request("GET", "/status", timeout=2.0)
        return True
    except SystemExit:
        return False


def _print_status(status: Dict[str, Any]) -> None:
    print(f"  配对码    {status.get('pair_code')}（剩余 {status.get('pair_expires_in')} 秒，一次有效）")
    urls = status.get("lan_urls") or []
    print(f"  手机地址  {'、'.join(urls) if urls else '（未探测到局域网地址）'}")
    for url in status.get("pair_urls") or []:
        print(f"  一步配对  {url}")
    print(f"  转发到    http://{status.get('backend')}")
    print(f"  已连设备  {status.get('device_count')} 台")


def _start(args: argparse.Namespace) -> int:
    if _is_running():
        print("手机访问已经在运行。")
        _print_status(_request("GET", "/status"))
        return 0
    if not RELAY_SCRIPT.is_file():
        print(f"[FAIL] 找不到中继脚本：{RELAY_SCRIPT}")
        return 1

    log_path = _relay_dir() / LOG_FILE
    log_handle = open(log_path, "a", encoding="utf-8")
    log_handle.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} start ===\n")
    log_handle.flush()

    command = [
        sys.executable,
        "-u",
        str(RELAY_SCRIPT),
        "--lan-host",
        args.lan_host,
        "--lan-port",
        str(args.lan_port),
        "--admin-port",
        str(args.admin_port),
        "--backend-host",
        args.backend_host,
        "--backend-port",
        str(args.backend_port),
    ]
    extra: Dict[str, Any] = {}
    if os.name == "nt":
        # CREATE_BREAKAWAY_FROM_JOB：从启动它的终端/任务对象中脱离，否则一旦终端退出，
        # 中继会跟着被杀掉（开关看起来"开了"却连不上）。DETACHED_PROCESS 让它没有
        # 控制台窗口，CREATE_NO_WINDOW 兜住不弹窗。
        breakaway = 0x01000000
        extra["creationflags"] = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
            | breakaway
        )
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(ROOT), env.get("PYTHONPATH", "")]).strip(
        os.pathsep
    )
    try:
        process = subprocess.Popen(
            command,
            cwd=str(ROOT),
            env=env,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            **extra,
        )
    except OSError:
        # 少数环境不允许脱离任务对象（例如已在受限 Job 里）；退回普通分离方式。
        extra.pop("creationflags", None)
        if os.name == "nt":
            extra["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
                subprocess, "DETACHED_PROCESS", 0
            )
        process = subprocess.Popen(
            command,
            cwd=str(ROOT),
            env=env,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            **extra,
        )

    # 等管理面可用；中继端口绑定很快，但不要无限等。
    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline:
        if process.poll() is not None:
            print(f"[FAIL] 中继启动失败，日志：{log_path}")
            return 1
        if _is_running():
            print("手机访问已开启（只在这台电脑上显式开启时才监听局域网）。")
            _print_status(_request("GET", "/status"))
            print("  关闭      python scripts/mobile_access.py stop")
            return 0
        time.sleep(0.3)

    print(f"[FAIL] 中继在 8 秒内没有就绪，日志：{log_path}")
    return 1


def _devices() -> int:
    payload = _request("GET", "/devices")
    devices = payload.get("devices") or []
    if not devices:
        print("还没有手机配对过。")
        return 0
    print("  ID                设备            最近连接              IP")
    for item in devices:
        last = item.get("last_seen_at")
        when = (
            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(last)))
            if isinstance(last, (int, float))
            else "—"
        )
        print(
            f"  {str(item.get('id')):<16}  {str(item.get('name') or '—'):<12}  "
            f"{when:<19}  {item.get('ip') or '—'}"
        )
    return 0


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="贾维斯手机访问开关（电脑端）")
    parser.add_argument(
        "command",
        nargs="?",
        default="status",
        choices=["start", "stop", "status", "pair", "devices", "kick", "kick-all"],
    )
    parser.add_argument("device_id", nargs="?", help="kick 需要设备 ID（见 devices）")
    parser.add_argument("--lan-host", default="0.0.0.0")
    parser.add_argument("--lan-port", type=int, default=8090)
    parser.add_argument("--admin-port", type=int, default=8091)
    parser.add_argument("--backend-host", default="127.0.0.1")
    parser.add_argument("--backend-port", type=int, default=8080)
    args = parser.parse_args(argv)

    if args.command == "start":
        return _start(args)
    if args.command == "stop":
        _request("POST", "/shutdown")
        print("手机访问已关闭，所有手机会话已断开。")
        return 0
    if args.command == "status":
        _print_status(_request("GET", "/status"))
        return 0
    if args.command == "pair":
        payload = _request("POST", "/pair/new")
        print(f"新的配对码：{payload.get('pair_code')}（剩余 {payload.get('pair_expires_in')} 秒，一次有效）")
        return 0
    if args.command == "devices":
        return _devices()
    if args.command == "kick":
        if not args.device_id:
            print("用法：python scripts/mobile_access.py kick <设备 ID>")
            return 2
        _request("POST", f"/devices/{args.device_id}/revoke")
        print("已踢掉该设备；它下一次请求会回到配对页。")
        return 0
    if args.command == "kick-all":
        payload = _request("POST", "/devices/revoke-all")
        print(f"已踢掉 {payload.get('revoked', 0)} 台设备，配对码已更新。")
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
