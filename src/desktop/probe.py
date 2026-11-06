"""桌面层共用的本机探测工具（HTTP / 端口 / 局域网地址）。

全部走标准库；本机常有 HTTP 代理，因此所有回环探测都显式绕开代理 ——
这是 ``scripts/start_liuhao.py`` 里同一类坑的复用，否则 127.0.0.1 会被代理
拦成 "upstream connect failed"，看起来像服务没起来。
"""

from __future__ import annotations

import ipaddress
import json
import os
import socket
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


def port_is_free(port: int, host: str = "127.0.0.1") -> bool:
    """``host:port`` 当前是否可绑定（可绑定即未被占用）。

    探测方式按平台对齐真实的监听行为：POSIX 上先关服务端连接（HTTP
    keep-alive 收尾就是这样）会让内核保留 TIME_WAIT，此时**不带
    ``SO_REUSEADDR``** 的 bind 返回 ``EADDRINUSE``，而 uvicorn/asyncio
    自己就是带这个选项监听的 —— 实际能立刻重启。这里跟着服务端加同一个
    选项，避免把 TIME_WAIT 误报成"被占用"（否则重启会平白换端口）。

    Windows 不加：那边 ``SO_REUSEADDR`` 允许绑定到别人**正在监听**的端口，
    会把真占用误判成空闲。
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.5)
    if os.name != "nt":
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
