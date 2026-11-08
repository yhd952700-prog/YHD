"""桌面层共用的本机探测工具（HTTP / 端口 / 局域网地址）。

全部走标准库；本机常有 HTTP 代理，因此所有回环探测都显式绕开代理 ——
这是 ``scripts/start_liuhao.py`` 里同一类坑的复用，否则 127.0.0.1 会被代理
拦成 "upstream connect failed"，看起来像服务没起来。
"""

from __future__ import annotations

import errno
import ipaddress
import json
import os
import socket
import subprocess
import urllib.error
import urllib.request
from typing import Any

DEFAULT_PORTS = (8080, 5173, 8443)


def _opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http_json(url: str, timeout: float = 3.0) -> dict[str, Any]:
    """GET ``url`` 并解析 JSON，返回统一结构（不抛异常）。

    结构：``{"ok": bool, "status": int | None, "body": Any, "error": str | None}``。
    ``ok`` 表示拿到了 2xx/3xx/4xx 响应；连接失败时为 False 并带 ``error``。
    """
    result: dict[str, Any] = {"ok": False, "status": None, "body": None, "error": None}
    try:
        with _opener().open(url, timeout=timeout) as resp:
            result["status"] = resp.status
            raw = resp.read()
            try:
                result["body"] = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                result["body"] = raw.decode("utf-8", "replace")[:2000]
            result["ok"] = 200 <= resp.status < 400
    except urllib.error.HTTPError as exc:  # 4xx/5xx 也是真实响应，保留状态码与正文
        result["status"] = exc.code
        result["error"] = f"HTTP {exc.code}"
        try:
            body = exc.read().decode("utf-8", "replace")
            result["body"] = json.loads(body) if body.strip().startswith(("{", "[")) else body[:2000]
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            result["body"] = None
    except (urllib.error.URLError, OSError, TimeoutError, ValueError) as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


#: Windows 的 WSA 错误码（Python 的 ``errno`` 在 Windows 上未必映射成 EADDRINUSE）。
_WSA_ADDRINUSE = 10048
_WSA_ACCES = 10013
_WSA_ADDRNOTAVAIL = 10049
_WSA_AFNOSUPPORT = 10047


def _windows_probe_targets(port: int, host: str) -> list[tuple[tuple, int, bool | None]]:
    """Windows 上要分别尝试的绑定地址。

    通配与具体地址在 Windows 上允许共存，只探一个会漏判：别人监听
    ``0.0.0.0:端口`` 时绑 ``127.0.0.1:端口`` 会成功，反之亦然。IPv6 通配
    用 ``V6ONLY=1``，避免与 IPv4 监听互相影响。
    """
    targets: list[tuple[tuple, int, bool | None]] = [(("", port), socket.AF_INET, None)]
    address = (host or "").strip()
    if address and address not in ("0.0.0.0", "::"):
        try:
            family = (
                socket.AF_INET6
                if ipaddress.ip_address(address).version == 6
                else socket.AF_INET
            )
        except ValueError:  # 主机名：按 IPv4 解析
            family = socket.AF_INET
        targets.append(((address, port), family, None))
    targets.append((("::", port), socket.AF_INET6, True))
    targets.append((("::1", port), socket.AF_INET6, None))
    return targets


def _bind_status(address: tuple, family: int, v6only: bool | None = None) -> str:
    """尝试绑定，返回 ``free`` / ``busy`` / ``denied`` / ``error: …``。

    ``unavailable`` 表示该地址族在当前机器不可用（例如禁用了 IPv6），可以跳过；
    其余任何失败都**不能**当成可用 —— 权限不足（保留端口）同样意味着网关自己
    也绑不上，必须让上层换端口或如实报错。
    """
    try:
        sock = socket.socket(family, socket.SOCK_STREAM)
    except OSError:
        return "unavailable"
    sock.settimeout(0.5)
    try:
        if v6only is not None:
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1 if v6only else 0)
        sock.bind(address)
    except OSError as exc:
        code = getattr(exc, "winerror", None) or exc.errno
        if code in (errno.EADDRINUSE, _WSA_ADDRINUSE):
            return "busy"
        if code in (errno.EACCES, errno.EPERM, _WSA_ACCES):
            return "denied"
        if family == socket.AF_INET6 and code in (
            errno.EADDRNOTAVAIL,
            errno.EAFNOSUPPORT,
            _WSA_ADDRNOTAVAIL,
            _WSA_AFNOSUPPORT,
        ):
            # IPv6 栈被禁用：这一路探测没有意义，跳过，不代表端口被占用。
            return "unavailable"
        return f"error: {exc}"
    finally:
        sock.close()
    return "free"


def port_is_free(port: int, host: str = "127.0.0.1") -> bool:
    """``host:port`` 当前是否可绑定（可绑定即未被占用）。

    探测方式按平台对齐真实的监听行为：POSIX 上先关服务端连接（HTTP
    keep-alive 收尾就是这样）会让内核保留 TIME_WAIT，此时**不带
    ``SO_REUSEADDR``** 的 bind 返回 ``EADDRINUSE``，而 uvicorn/asyncio
    自己就是带这个选项监听的 —— 实际能立刻重启。这里跟着服务端加同一个
    选项，避免把 TIME_WAIT 误报成"被占用"（否则重启会平白换端口）。

    Windows 不加 ``SO_REUSEADDR``：那边它允许绑定到别人**正在监听**的端口，
    会把真占用误判成空闲。而且通配地址与具体地址可以共存（实测只有绑到同一
    地址才报 winerror 10048），只探一个都会漏判：

    * 别人监听 ``0.0.0.0:端口`` 时，绑 ``127.0.0.1:端口`` 依然成功；
    * 别人监听 ``127.0.0.1:端口`` 时，绑 ``0.0.0.0:端口`` 依然成功。

    所以这里把 IPv4 通配、调用方地址、IPv6 通配（``V6ONLY=1``）与 ``::1``
    各探一次，**任一失败即视为不可用**。权限不足 / 保留端口同样返回 False：
    网关自己也会绑不上，不能报成"空闲"再让它起不来。
    """
    if os.name == "nt":
        for address, family, v6only in _windows_probe_targets(port, host):
            if _bind_status(address, family, v6only) in ("free", "unavailable"):
                continue
            return False
        return True

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.5)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((host, port))
    except OSError:
        return False
    finally:
        sock.close()
    return True


def pick_port(preferred: int = 8080, count: int = 100) -> int:
    """返回 ``preferred`` 起的第一个空闲端口；全被占用时抛 ``RuntimeError``。"""
    for candidate in range(preferred, preferred + count):
        if port_is_free(candidate):
            return candidate
    raise RuntimeError(f"{preferred}..{preferred + count - 1} 全部被占用，找不到空闲端口")


#: 占用者查不到时的统一说法。用户需要能分清「查不到」和「没人占用」，
#: 不能给一个空白让人以为程序没查。
PORT_OWNER_UNKNOWN = "查不到（可能需要管理员权限才能看到占用该端口的程序）"


def listener_pids(port: int) -> list[int]:
    """返回正在监听 ``port`` 的进程号（Windows 用 ``netstat -ano``）。

    非 Windows 或命令不可用时返回空列表：占用者信息只是排障辅助，
    绝不能因此影响端口判定本身。
    """
    if os.name != "nt":
        return []
    try:
        listing = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    needle = f":{port}"
    pids: list[int] = []
    for line in (listing.stdout or "").splitlines():
        parts = line.split()
        # Proto  Local Address  Foreign Address  State  PID
        if len(parts) != 5 or parts[0].upper() not in {"TCP", "TCPV6"}:
            continue
        if not parts[1].endswith(needle) or parts[3].upper() not in {"LISTENING", "LISTEN"}:
            continue
        try:
            pid = int(parts[4])
        except ValueError:
            continue
        if pid not in pids:
            pids.append(pid)
    return pids


def _process_name_and_path(pid: int) -> tuple[str, str]:
    """尽力取进程名与可执行文件路径；拿不到就返回空串（不抛异常）。"""
    name = ""
    exe = ""
    try:
        import psutil  # requirements.txt 已声明
    except Exception:  # noqa: BLE001 - 缺少 psutil 时退回 tasklist
        psutil = None
    if psutil is not None:
        try:
            proc = psutil.Process(pid)
            try:
                name = str(proc.name() or "")
            except Exception:  # noqa: BLE001 - 权限不足
                name = ""
            try:
                exe = str(proc.exe() or "")
            except Exception:  # noqa: BLE001 - 权限不足 / 进程已退出
                exe = ""
        except Exception:  # noqa: BLE001 - 进程不存在
            pass
    if not name:
        try:
            detail = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
            )
            cells = (detail.stdout or "").strip().strip('"').split('","')
            if cells and cells[0]:
                name = cells[0]
        except (OSError, subprocess.SubprocessError):
            pass
    return name, exe


def port_owner(port: int) -> dict[str, Any]:
    """返回端口占用者信息：``{"pids": [...], "description": str}``。

    ``description`` 形如 ``PID 1234，python.exe，C:\\...\\python.exe``；多个
    占用者用「；」分隔。查不到时给 :data:`PORT_OWNER_UNKNOWN`，不返回空串。
    """
    labels: list[str] = []
    pids = listener_pids(port)
    for pid in pids:
        name, exe = _process_name_and_path(pid)
        parts = [f"PID {pid}"]
        if name:
            parts.append(name)
        if exe:
            parts.append(exe)
        labels.append("，".join(parts))
    return {
        "pids": pids,
        "description": "；".join(labels) if labels else PORT_OWNER_UNKNOWN,
    }


def describe_port_owner(port: int) -> str:
    """只要一句占用者描述（查不到时说明查不到）。"""
    return str(port_owner(port)["description"])


def discover_service(ports: tuple[int, ...] | range = range(8080, 8090), timeout: float = 0.8) -> str | None:
    """探测本机正在运行的网关，返回 ``http://127.0.0.1:<port>`` 或 None。"""
    for port in ports:
        info = http_json(f"http://127.0.0.1:{port}/v1/health", timeout=timeout)
        if info["ok"] and isinstance(info["body"], dict) and info["body"].get("service") == "liuhao-gateway":
            return f"http://127.0.0.1:{port}"
    return None


def lan_addresses() -> list[str]:
    """本机所有非回环、非链路本地的 IPv4 地址（手机同 Wi-Fi 时使用）。"""
    found: list[str] = []
    try:
        addrs = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
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
