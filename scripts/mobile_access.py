#!/usr/bin/env python3
"""电脑端的「手机访问」开关与管理工具。

默认关闭：只有在这台电脑上显式执行 ``python scripts/mobile_access.py start``
（或启动器 ``--mobile-access``）时，才监听局域网端口。

常用命令::

    python scripts/mobile_access.py start      # 开启，并打印配对码 / 配对链接
    python scripts/mobile_access.py start --open-firewall
                                               # 同时按实际端口精确放行防火墙
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
import ctypes
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
#: 安装版网关可能不在 8080（端口冲突时桌面壳会换端口），开启中继前先读实例。
INSTANCE_FILE = Path("run") / "instance.json"
DESKTOP_PORT_FILE = Path("config") / "desktop_port.json"


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


def _detect_backend_port() -> int:
    """找当前网关端口：运行实例优先，其次桌面壳持久化的端口，最后 8080。"""
    data = _data_dir()
    for relative in (INSTANCE_FILE, DESKTOP_PORT_FILE):
        try:
            payload = json.loads((data / relative).read_text(encoding="utf-8"))
            port = int(payload.get("port") or 0)
        except (OSError, ValueError, TypeError):
            continue
        if 1 <= port <= 65535:
            return port
    return 8080


def _firewall_rule_name(port: int) -> str:
    # 用 ASCII 规则名：netsh / 本地化 Windows 下不会因控制台代码页变成乱码。
    return f"LiuHao Mobile Access (TCP {port})"


def _is_admin() -> bool:
    if os.name != "nt":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _powershell(command: str, *, timeout: float = 20.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _firewall_has_rule(port: int) -> bool:
    if os.name != "nt":
        return False
    name = _firewall_rule_name(port)
    query = (
        f"$r = Get-NetFirewallRule -DisplayName '{name}' -ErrorAction SilentlyContinue "
        "| Select-Object -First 1; "
        "if ($r -and $r.Enabled -eq 'True' -and $r.Action -eq 'Allow' "
        "-and $r.Direction -eq 'Inbound' -and ([string]$r.Profile) -match 'Private') { "
        "$p = $r | Get-NetFirewallPortFilter; $a = $r | Get-NetFirewallAddressFilter; "
        f"if ($p.Protocol -eq 'TCP' -and [string]$p.LocalPort -eq '{int(port)}' "
        "-and ([string]$a.RemoteAddress) -match 'LocalSubnet') { 'FOUND' } }"
    )
    try:
        result = _powershell(query)
    except (OSError, subprocess.SubprocessError):
        return False
    return "FOUND" in (result.stdout or "")


def _open_firewall(port: int) -> bool:
    """按实际监听端口精确放行：只 TCP、只专用网络、只本子网，绝不整段放开。"""
    if os.name != "nt":
        print("  [skip] 非 Windows 平台不需要设置防火墙。")
        return True
    name = _firewall_rule_name(port)
    if _firewall_has_rule(port):
        print(f"  [ok] 防火墙已有精确放行规则：TCP {port}（专用网络 / 本子网）。")
        return True
    command = (
        f"New-NetFirewallRule -DisplayName '{name}' -Direction Inbound "
        f"-Protocol TCP -LocalPort {int(port)} -Action Allow "
        "-Profile Private -RemoteAddress LocalSubnet"
    )
    if not _is_admin():
        print(
            f"  [UAC] 只新增一条入站规则：TCP {port} / 专用网络 / 本子网；"
            "不会放开其它端口。"
        )
        parameters = f'-NoProfile -ExecutionPolicy Bypass -Command "{command}"'
        try:
            rc = ctypes.windll.shell32.ShellExecuteW(
                None, "runas", "powershell.exe", parameters, None, 1
            )
        except Exception:
            rc = 0
        if rc <= 32:
            print("  [WARN] 未获得管理员授权，防火墙未改动。")
            print(f"         可手动执行：{command}")
            return False
        deadline = time.time() + 30.0
        while time.time() < deadline:
            if _firewall_has_rule(port):
                print(f"  [ok] 已放行防火墙：TCP {port}（仅专用网络、仅本子网）。")
                return True
            time.sleep(0.5)
        print("  [WARN] 未确认规则已生效，请用 Get-NetFirewallRule 检查。")
        print(f"         手动命令：{command}")
        return False
    try:
        result = _powershell(command + " | Out-Null", timeout=30.0)
        detail = ((result.stderr or "") + (result.stdout or "")).strip()
    except (OSError, subprocess.SubprocessError) as exc:
        result = None
        detail = str(exc)
    if result is not None and result.returncode == 0:
        print(f"  [ok] 已放行防火墙：TCP {port}（仅专用网络、仅本子网）。")
        return True
    print("  [WARN] 未能自动添加防火墙规则（通常需要管理员权限）。")
    print("         可右键「以管理员身份运行」PowerShell，只执行下面这一条：")
    print(f"         {command}")
    if detail:
        print(f"         原因：{detail.splitlines()[-1]}")
    return False


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

    backend_port = int(args.backend_port or _detect_backend_port())
    print("即将开启局域网手机访问：")
    print(f"  监听 {args.lan_host}:{args.lan_port}（只有中继对外；网关仍只绑 127.0.0.1:{backend_port}）")
    print("  只有配对成功（一次性配对码，10 分钟有效）的设备能进入；请勿在公共 Wi-Fi 开启。")
    print("  关闭：运行 stop，或开始菜单「手机访问（关闭）」。")
    if args.open_firewall:
        _open_firewall(args.lan_port)
    else:
        print(
            f"  提示：手机连不上时，多是 Windows 防火墙未放行；"
            f"可加 --open-firewall 只放行 TCP {args.lan_port}。"
        )

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
        str(backend_port),
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
    parser = argparse.ArgumentParser(description="鎏灏手机访问开关（电脑端）")
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
    parser.add_argument(
        "--backend-port",
        type=int,
        default=None,
        help="回环网关端口；默认自动读取运行实例，读不到再用 8080",
    )
    parser.add_argument(
        "--open-firewall",
        action="store_true",
        help="开启时精确放行防火墙：仅 TCP <lan-port>、仅专用网络、仅本子网（需管理员）",
    )
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
