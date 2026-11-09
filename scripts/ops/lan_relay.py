#!/usr/bin/env python3
"""局域网手机中继（手机端唯一的局域网入口）。

为什么要这一层
--------------
网关默认只绑 ``127.0.0.1``，手机在同一 Wi-Fi 下连不上；而直接把网关改成
``0.0.0.0`` 会同时放开 ``/docs``、``/v1/ready`` 与完整健康探针，且手机同源
写请求仍会被网关的 Origin 守卫 403。

本进程把「局域网暴露面」收在一个独立端口里：

* 只转发到回环网关（``127.0.0.1:<backend_port>``），网关监听地址一个字节都不改；
* 转发时把 ``Origin`` / ``Host`` 改写成回环地址，命中网关既有的回环白名单；
* 在**这一层**关掉开发接口（``/docs``、``/redoc``、``/openapi.json``、``/v1/ready``），
  并把 ``/v1/health`` 裁成 ``status`` + ``timestamp``；
* 未配对设备只能看到配对页，看不到任何业务接口；
* 设备凭证、最近连接、单踢/全踢、登录失败冷却都在本进程内完成。

默认关闭：只有电脑端显式运行本脚本（或启动器 ``--mobile-access``）才会监听
局域网。进程退出即断开所有手机会话。

管理面
------
另起一个只绑 ``127.0.0.1`` 的管理端口，供 ``scripts/mobile_access.py`` 使用；
管理接口要求 ``Authorization: Bearer <数据目录/mobile_access/admin.token>``。
手机永远拿不到这个令牌。
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import secrets
import signal
import socket
import sys
import threading
import time
import traceback
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[2]

#: 中继写入的数据（设备表、管理令牌、运行状态）。开发态落在平台数据目录，
#: 不往仓库里写文件。
DATA_DIR_ENV = "LIUHAO_DATA_DIR"
RELAY_DIR_REL = Path("mobile_access")
DEVICE_FILE = "devices.json"
ADMIN_TOKEN_FILE = "admin.token"
STATE_FILE = "relay.json"

#: 配对码有效期与长度。一次性：配对成功即作废。
PAIR_TTL_SECONDS = 600
PAIR_CODE_DIGITS = 6

#: 登录失败冷却：同一设备 + 来源 IP，窗口内 5 次失败 → 冷却 15 分钟。
FAIL_WINDOW_SECONDS = 300
FAIL_MAX = 5
FAIL_COOLDOWN_SECONDS = 900

#: 配对页 / 管理面的最小触控尺寸。
MIN_TOUCH_PX = 44

_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
    "origin",
    "referer",
    "cookie",
    "content-length",
}

#: 对局域网一律不存在的开发接口。
_BLOCKED_PREFIXES = ("/docs", "/redoc", "/openapi.json")
_BLOCKED_EXACT = {"/v1/ready"}

COOKIE_NAME = "liuhao_relay_device"


def _data_dir() -> Path:
    raw = (os.environ.get(DATA_DIR_ENV) or "").strip()
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


def _write_private(path: Path, content: str) -> None:
    """写文件并把权限收紧到当前用户（Windows 下 os.chmod 语义有限，尽力而为）。

    Windows 上 ``os.replace`` 偶尔会被杀毒/索引器短暂占用（WinError 5），
    这里重试几次再退化为直接覆盖 —— 让"记录最近连接时间"这种后台写
    不至于把一次正常的页面请求变成 500。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    for attempt in range(6):
        try:
            os.replace(tmp, path)
            break
        except PermissionError:
            if attempt == 5:
                path.write_text(content, encoding="utf-8")
                try:
                    tmp.unlink()
                except OSError:
                    pass
            else:
                time.sleep(0.05 * (attempt + 1))
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _admin_token() -> str:
    path = _relay_dir() / ADMIN_TOKEN_FILE
    if path.is_file():
        value = (path.read_text(encoding="utf-8") or "").strip()
        if value:
            return value
    value = secrets.token_urlsafe(32)
    _write_private(path, value)
    return value


def _lan_ipv4_addresses() -> List[str]:
    """枚举本机可用于局域网访问的 IPv4（跳过回环/链路本地/虚拟网卡噪声）。"""
    found: List[str] = []
    try:
        host = socket.gethostname()
        for item in socket.gethostbyname_ex(host)[2]:
            if item and not item.startswith("127.") and item not in found:
                found.append(item)
    except OSError:
        pass
    if not found:
        # 不依赖默认路由（UDP connect 不发包），只取出口网卡地址。
        try:
            probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            probe.connect(("8.8.8.8", 80))
            candidate = probe.getsockname()[0]
            probe.close()
            if candidate and not candidate.startswith("127."):
                found.append(candidate)
        except OSError:
            pass
    return [item for item in found if not item.startswith("169.254.")]


def _device_name(user_agent: str) -> str:
    ua = user_agent or ""
    if "iPhone" in ua:
        return "iPhone"
    if "iPad" in ua:
        return "iPad"
    if "Android" in ua:
        match = re.search(r"Android[^;)]*", ua)
        return "Android 手机" if not match else "Android 手机"
    if "Macintosh" in ua:
        return "Mac 浏览器"
    if "Windows" in ua:
        return "Windows 浏览器"
    return "手机浏览器"


class DeviceStore:
    """设备表：设备名 / 最近连接 / IP / 已签发的网关令牌。"""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.RLock()
        self._devices: Dict[str, Dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                for key, value in raw.items():
                    if isinstance(value, dict) and value.get("secret"):
                        self._devices[str(key)] = value
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            # 设备表损坏不能拖垮中继；保留空表并让用户重新配对。
            self._devices = {}

    def _save(self) -> None:
        payload = json.dumps(self._devices, ensure_ascii=False, indent=2, sort_keys=True)
        _write_private(self._path, payload)

    def add(self, *, name: str, ip: str) -> Dict[str, Any]:
        with self._lock:
            device_id = secrets.token_hex(8)
            record = {
                "id": device_id,
                "secret": secrets.token_urlsafe(32),
                "name": name,
                "ip": ip,
                "paired_at": time.time(),
                "last_seen_at": time.time(),
                "tokens": [],
            }
            self._devices[device_id] = record
            self._save()
            return dict(record)

    def identify(self, device_id: str, secret: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            record = self._devices.get(device_id)
            if not record:
                return None
            if not secrets.compare_digest(str(record.get("secret") or ""), secret):
                return None
            return dict(record)

    def touch(self, device_id: str, ip: str) -> None:
        with self._lock:
            record = self._devices.get(device_id)
            if not record:
                return
            now = time.time()
            moved = bool(ip) and ip != record.get("ip")
            # 一次页面加载会打十几个请求；每 15 秒落一次盘足够"最近连接时间"用，
            # 也避免高频 os.replace 与杀毒/索引器抢文件。
            if not moved and now - float(record.get("last_seen_at") or 0) < 15:
                return
            record["last_seen_at"] = now
            if ip:
                record["ip"] = ip
            self._save()

    def remember_token(self, device_id: str, token: str) -> None:
        if not token:
            return
        with self._lock:
            record = self._devices.get(device_id)
            if not record:
                return
            tokens = [str(item) for item in (record.get("tokens") or [])]
            if token in tokens:
                tokens.remove(token)
            tokens.append(token)
            record["tokens"] = tokens[-10:]
            self._save()

    def list(self) -> List[Dict[str, Any]]:
        with self._lock:
            items = []
            for record in self._devices.values():
                items.append(
                    {
                        "id": record.get("id"),
                        "name": record.get("name"),
                        "ip": record.get("ip"),
                        "paired_at": record.get("paired_at"),
                        "last_seen_at": record.get("last_seen_at"),
                        "tokens": len(record.get("tokens") or []),
                    }
                )
            return sorted(items, key=lambda item: item.get("last_seen_at") or 0, reverse=True)

    def pop(self, device_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            record = self._devices.pop(device_id, None)
            if record is not None:
                self._save()
            return dict(record) if record else None

    def clear(self) -> List[Dict[str, Any]]:
        with self._lock:
            records = [dict(item) for item in self._devices.values()]
            self._devices = {}
            self._save()
            return records


class LoginGuard:
    """按 设备 + 来源 IP 的登录失败计数与冷却（与网关的按账号限流并存）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._failures: Dict[str, deque] = {}
        self._blocked_until: Dict[str, float] = {}

    @staticmethod
    def _key(device_id: str, ip: str) -> str:
        return f"{device_id}|{ip}"

    def blocked_for(self, device_id: str, ip: str) -> int:
        key = self._key(device_id, ip)
        with self._lock:
            until = self._blocked_until.get(key, 0.0)
            if until <= time.time():
                self._blocked_until.pop(key, None)
                return 0
            return int(until - time.time()) + 1

    def register_failure(self, device_id: str, ip: str) -> None:
        key = self._key(device_id, ip)
        now = time.time()
        with self._lock:
            recent = self._failures.setdefault(key, deque())
            while recent and now - recent[0] > FAIL_WINDOW_SECONDS:
                recent.popleft()
            recent.append(now)
            if len(recent) >= FAIL_MAX:
                self._blocked_until[key] = now + FAIL_COOLDOWN_SECONDS
                recent.clear()

    def clear(self, device_id: str, ip: str) -> None:
        key = self._key(device_id, ip)
        with self._lock:
            self._failures.pop(key, None)
            self._blocked_until.pop(key, None)


class RelayState:
    def __init__(
        self,
        *,
        backend_host: str,
        backend_port: int,
        lan_host: str,
        lan_port: int,
        admin_port: int,
        devices: DeviceStore,
        admin_token: str,
    ) -> None:
        self.backend_host = backend_host
        self.backend_port = backend_port
        self.lan_host = lan_host
        self.lan_port = lan_port
        self.admin_port = admin_port
        self.devices = devices
        self.admin_token = admin_token
        self.guard = LoginGuard()
        # 可重入：rotate_pair_code() 持锁时会再调用 pair_snapshot()，普通 Lock 会自锁死。
        self._lock = threading.RLock()
        # 局域网地址只在启动时探测一次：DNS / 网卡枚举在某些网络下会阻塞十几秒，
        # 放进请求路径会让管理面的 /status 直接超时。
        self._lan_urls = _lan_ipv4_addresses()
        self.pair_code = self._new_code()
        self.pair_expires_at = time.time() + PAIR_TTL_SECONDS
        self.started_at = time.time()
        self._server: Optional[ThreadingHTTPServer] = None

    def _new_code(self) -> str:
        # 不以 0 开头，避免被某些输入法当成空值吞掉。
        first = secrets.choice("123456789")
        rest = "".join(secrets.choice("0123456789") for _ in range(PAIR_CODE_DIGITS - 1))
        return first + rest

    def rotate_pair_code(self) -> Dict[str, Any]:
        with self._lock:
            self.pair_code = self._new_code()
            self.pair_expires_at = time.time() + PAIR_TTL_SECONDS
            return self.pair_snapshot()

    def pair_snapshot(self) -> Dict[str, Any]:
        with self._lock:
            remaining = max(0, int(self.pair_expires_at - time.time()))
            return {
                "pair_code": self.pair_code,
                "pair_expires_in": remaining,
            }

    def consume_code(self, code: str) -> bool:
        with self._lock:
            if time.time() > self.pair_expires_at:
                return False
            if not code or not secrets.compare_digest(str(code).strip(), self.pair_code):
                return False
            # 一次性：无论后续绑定是否成功，这个码都不再可用。
            self.pair_code = self._new_code()
            self.pair_expires_at = time.time() + PAIR_TTL_SECONDS
            return True

    def lan_urls(self) -> List[str]:
        return [f"http://{ip}:{self.lan_port}" for ip in self._lan_urls]

    def status(self) -> Dict[str, Any]:
        snapshot = self.pair_snapshot()
        return {
            **snapshot,
            "lan_urls": self.lan_urls(),
            "pair_urls": [
                f"{base}/__relay/pair?code={snapshot['pair_code']}" for base in self.lan_urls()
            ],
            "backend": f"{self.backend_host}:{self.backend_port}",
            "device_count": len(self.devices.list()),
            "started_at": self.started_at,
            "lan_port": self.lan_port,
            "admin_port": self.admin_port,
        }


# ---------------------------------------------------------------------------
# 管理面（只绑 127.0.0.1）
# ---------------------------------------------------------------------------


class AdminHandler(BaseHTTPRequestHandler):
    server_version = "LiuHaoRelayAdmin/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:  # 静默
        pass

    @property
    def relay(self) -> RelayState:
        return self.server.relay  # type: ignore[attr-defined]

    def _authorized(self) -> bool:
        header = self.headers.get("authorization", "")
        supplied = header[7:].strip() if header.lower().startswith("bearer ") else ""
        return bool(supplied) and secrets.compare_digest(supplied, self.relay.admin_token)

    def _json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle(self) -> None:
        if not self._authorized():
            self._json(403, {"detail": "需要本机管理令牌"})
            return
        parsed = urlsplit(self.path)
        path = parsed.path.rstrip("/") or "/"
        method = self.command.upper()

        if path == "/status" and method == "GET":
            self._json(200, self.relay.status())
            return
        if path == "/pair/new" and method == "POST":
            self._json(200, self.relay.rotate_pair_code())
            return
        if path == "/devices" and method == "GET":
            self._json(200, {"devices": self.relay.devices.list()})
            return
        if path == "/devices/revoke-all" and method == "POST":
            records = self.relay.devices.clear()
            for record in records:
                _revoke_tokens(self.relay, record)
            self.relay.rotate_pair_code()
            self._json(200, {"revoked": len(records)})
            return
        match = re.fullmatch(r"/devices/([0-9a-f]+)/revoke", path)
        if match and method == "POST":
            record = self.relay.devices.pop(match.group(1))
            if record is None:
                self._json(404, {"detail": "设备不存在"})
                return
            _revoke_tokens(self.relay, record)
            self._json(200, {"revoked": 1, "id": record.get("id")})
            return
        if path == "/shutdown" and method == "POST":
            self._json(200, {"stopping": True})
            threading.Thread(target=_hard_stop, args=(self.relay,), daemon=True).start()
            return
        self._json(404, {"detail": "未知的管理操作"})

    do_GET = do_POST = _handle


def _revoke_tokens(relay: RelayState, record: Dict[str, Any]) -> None:
    """在网关侧撤销该设备拿到的每一个令牌；中继删记录后手机立即回登录页。"""
    for token in record.get("tokens") or []:
        try:
            connection = http.client.HTTPConnection(
                relay.backend_host, relay.backend_port, timeout=3
            )
            connection.request(
                "POST",
                "/v1/auth/logout",
                headers={
                    "Host": f"127.0.0.1:{relay.backend_port}",
                    "Origin": f"http://127.0.0.1:{relay.backend_port}",
                    "X-Liuhao-Token": str(token),
                },
            )
            connection.getresponse().read()
            connection.close()
        except OSError:
            # 单个令牌撤销失败不阻断其它设备；记录是否清掉由调用方决定。
            pass


# ---------------------------------------------------------------------------
# 局域网面
# ---------------------------------------------------------------------------


_PAIR_PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover, interactive-widget=resizes-content" />
<title>连接鎏灏</title>
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; height: 100%; overflow-x: hidden; }}
  body {{
    display: grid; place-items: safe center; padding: 20px;
    padding-bottom: calc(20px + env(safe-area-inset-bottom));
    background: #050912; color: #e8eefc;
    font: 16px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
  }}
  main {{ width: 100%; max-width: 420px; }}
  h1 {{ font-size: 22px; margin: 0 0 8px; }}
  p {{ color: #93a4c4; margin: 0 0 20px; font-size: 14px; }}
  input {{
    width: 100%; min-height: {touch}px; padding: 12px 14px; font-size: 24px;
    letter-spacing: 8px; text-align: center; border-radius: 12px;
    border: 1px solid #2b3a58; background: #0c1425; color: #e8eefc;
  }}
  button {{
    width: 100%; min-height: {touch}px; margin-top: 16px; border: 0; border-radius: 12px;
    background: #4f7df3; color: #fff; font-size: 16px; font-weight: 600;
  }}
  .hint {{ margin-top: 14px; font-size: 13px; color: #93a4c4; }}
</style>
</head>
<body>
<main>
  <h1>连接鎏灏</h1>
  <p>在电脑上打开「手机访问」，把这台设备的配对码填进下面。配对码只可用一次。</p>
  <form method="post" action="/__relay/pair">
    <input name="code" inputmode="numeric" autocomplete="one-time-code"
           maxlength="6" pattern="[0-9]{{6}}" placeholder="6 位配对码" value="{code}" required />
    <button type="submit">连接</button>
  </form>
  <div class="hint">{hint}</div>
</main>
</body>
</html>
"""


class QuietHTTPServer(ThreadingHTTPServer):
    """手机断网/被踢时客户端会直接断开（WinError 10053/10054），不该刷一整页堆栈。"""

    daemon_threads = True

    def handle_error(self, request, client_address) -> None:
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
            return
        super().handle_error(request, client_address)


class RelayHandler(BaseHTTPRequestHandler):
    server_version = "LiuHaoRelay/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:  # 静默，避免把配对码写进日志
        pass

    @property
    def relay(self) -> RelayState:
        return self.server.relay  # type: ignore[attr-defined]

    # ---- 小工具 -----------------------------------------------------------
    def _json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, status: int, body: str, extra: Optional[Dict[str, str]] = None) -> None:
        raw = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(raw)

    def _pair_page(self, code: str = "", hint: str = "") -> None:
        self._html(
            200,
            _PAIR_PAGE.format(touch=MIN_TOUCH_PX, code=code, hint=hint),
        )

    def _client_ip(self) -> str:
        return str((self.client_address or ("", 0))[0] or "")

    def _cookie_device(self) -> Optional[Dict[str, Any]]:
        raw = self.headers.get("cookie", "")
        for part in raw.split(";"):
            name, _, value = part.strip().partition("=")
            if name != COOKIE_NAME or not value:
                continue
            device_id, _, secret = value.partition(":")
            record = self.relay.devices.identify(device_id, secret)
            if record:
                return record
        return None

    def _set_device_cookie(self, record: Dict[str, Any]) -> str:
        value = f"{record['id']}:{record['secret']}"
        return (
            f"{COOKIE_NAME}={value}; Path=/; HttpOnly; SameSite=Lax; "
            f"Max-Age={60 * 60 * 24 * 365}"
        )

    # ---- 入口 -------------------------------------------------------------
    def do_GET(self) -> None:
        self._handle()

    def do_POST(self) -> None:
        self._handle()

    def do_PUT(self) -> None:
        self._handle()

    def do_DELETE(self) -> None:
        self._handle()

    def do_PATCH(self) -> None:
        self._handle()

    def do_HEAD(self) -> None:
        self._handle()

    def do_OPTIONS(self) -> None:
        self._handle()

    def _handle(self) -> None:
        parsed = urlsplit(self.path)
        path = parsed.path or "/"

        if path.startswith("/__relay/"):
            self._relay_route(path, parsed.query)
            return

        # 开发接口对局域网一律「不存在」：未配对也不能通过 401/404 的差异探测出它们。
        if path in _BLOCKED_EXACT or any(path.startswith(prefix) for prefix in _BLOCKED_PREFIXES):
            self._json(404, {"detail": "局域网访问不提供开发接口。"})
            return

        device = self._cookie_device()
        if device is None:
            if self.command in ("GET", "HEAD") and path in ("/", "/index.html"):
                # 带着已失效的设备 Cookie 回来，多半是电脑端刚把它踢了。
                stale = COOKIE_NAME in (self.headers.get("cookie") or "")
                hint = (
                    "这台设备已经被电脑端移除，请让电脑重新生成配对码后再配对。"
                    if stale
                    else "配对后才会进入登录页。"
                )
                self._pair_page(hint=hint)
                return
            self._json(401, {"detail": "设备未配对：请先在电脑端生成配对码，并在手机上完成配对。"})
            return

        self.relay.devices.touch(str(device["id"]), self._client_ip())

        if self.command in ("POST", "PUT", "PATCH", "DELETE") and not self._origin_is_own():
            self._json(403, {"detail": "跨站写请求被中继拒绝：来源与当前地址不同。"})
            return

        if path == "/v1/health" and self.command in ("GET", "HEAD"):
            self._forward_health()
            return

        if path == "/v1/auth/login" and self.command == "POST":
            self._forward_login(device)
            return

        self._forward(device)

    # ---- 配对 -------------------------------------------------------------
    def _relay_route(self, path: str, query: str) -> None:
        if path != "/__relay/pair":
            self._json(404, {"detail": "未知的中继入口"})
            return
        code = ""
        if self.command == "GET":
            code = (parse_qs(query).get("code") or [""])[0]
        elif self.command == "POST":
            code = self._read_form_code()
        else:
            self._json(405, {"detail": "配对只接受 GET / POST"})
            return

        if not code:
            self._pair_page()
            return

        if not self.relay.consume_code(code):
            self._pair_page(hint="配对码无效或已过期。请在电脑端重新生成。")
            return

        record = self.relay.devices.add(
            name=_device_name(self.headers.get("user-agent", "")),
            ip=self._client_ip(),
        )
        cookie = self._set_device_cookie(record)
        if self.command == "HEAD":
            self.send_response(303)
            self.send_header("Location", "/")
            self.send_header("Set-Cookie", cookie)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(303)
        self.send_header("Location", "/")
        self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _read_form_code(self) -> str:
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError:
            return ""
        if length <= 0 or length > 4096:
            return ""
        raw = self.rfile.read(length).decode("utf-8", "replace")
        values = parse_qs(raw)
        return str((values.get("code") or [""])[0]).strip()

    # ---- 转发 -------------------------------------------------------------
    def _upstream_headers(self) -> Dict[str, str]:
        headers: Dict[str, str] = {}
        for name in self.headers.keys():
            lowered = name.lower()
            if lowered in _HOP_BY_HOP:
                continue
            headers[name] = self.headers[name]
        port = self.relay.backend_port
        # 关键：让网关看到「本机回环来源」。Origin 已在入口校验后剥掉——
        # 安装版网关是 production 模式，回环放行依赖 LIUHAO_LISTEN_PORT，
        # 而该变量在桌面壳里不一定存在；不带 Origin 的写请求不会被守卫拦。
        # 跨站写已经在 _origin_is_own 处拒绝，这一层就是新的信任边界。
        headers["Host"] = f"127.0.0.1:{port}"
        return headers

    def _origin_is_own(self) -> bool:
        """拒绝跨站写：Origin 必须与手机当前访问的中继地址同源。"""
        origin = (self.headers.get("origin") or "").strip()
        if not origin:
            return True
        host = (self.headers.get("host") or "").strip()
        if not host:
            return False
        expected = f"http://{host}"
        return origin.rstrip("/").lower() == expected.rstrip("/").lower()

    def _read_body(self) -> Optional[bytes]:
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError:
            return None
        if length <= 0:
            return None
        return self.rfile.read(length)

    def _connect(self) -> http.client.HTTPConnection:
        return http.client.HTTPConnection(
            self.relay.backend_host, self.relay.backend_port, timeout=60
        )

    def _forward_login(self, device: Dict[str, Any]) -> None:
        ip = self._client_ip()
        device_id = str(device["id"])
        blocked = self.relay.guard.blocked_for(device_id, ip)
        if blocked:
            self._json(
                429,
                {
                    "detail": f"登录尝试过于频繁，请在 {max(1, blocked // 60)} 分钟后再试。",
                    "retry_after": blocked,
                },
            )
            return

        body = self._read_body()
        connection = self._connect()
        try:
            connection.request(
                "POST",
                self.path,
                body=body,
                headers=self._upstream_headers(),
            )
            response = connection.getresponse()
            raw = response.read()
        except OSError as exc:
            self._json(502, {"detail": f"本地服务没有响应：{exc}"})
            return
        finally:
            connection.close()

        status = response.status
        if status in (400, 401):
            self.relay.guard.register_failure(device_id, ip)
        elif status in (200, 201):
            self.relay.guard.clear(device_id, ip)
            try:
                payload = json.loads(raw.decode("utf-8"))
                token = str(payload.get("access_token") or "")
                if token:
                    self.relay.devices.remember_token(device_id, token)
            except (ValueError, UnicodeDecodeError):
                pass

        self.send_response(status)
        for name, value in response.getheaders():
            if name.lower() in _HOP_BY_HOP:
                continue
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _forward_health(self) -> None:
        connection = self._connect()
        try:
            connection.request("GET", "/v1/health", headers=self._upstream_headers())
            response = connection.getresponse()
            raw = response.read()
        except OSError as exc:
            self._json(502, {"detail": f"本地服务没有响应：{exc}"})
            return
        finally:
            connection.close()

        try:
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self._json(502, {"detail": "本地服务的健康探针返回了无法解析的内容。"})
            return

        # 只留必要字段：版本、AI provider、trace 回显一律不给局域网。
        minimal = {
            "status": payload.get("status"),
            "timestamp": payload.get("timestamp"),
        }
        body = json.dumps(minimal, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _forward(self, device: Dict[str, Any]) -> None:
        body = self._read_body()
        connection = self._connect()
        try:
            connection.request(
                self.command,
                self.path,
                body=body,
                headers=self._upstream_headers(),
            )
            response = connection.getresponse()
        except OSError as exc:
            self._json(502, {"detail": f"本地服务没有响应：{exc}"})
            return

        status = response.status
        self.send_response(status)
        length_header = response.getheader("Content-Length")
        for name, value in response.getheaders():
            if name.lower() in _HOP_BY_HOP:
                continue
            self.send_header(name, value)

        if self.command == "HEAD" or status in (204, 304):
            self.send_header("Content-Length", "0")
            self.end_headers()
            connection.close()
            return

        if length_header is not None:
            self.send_header("Content-Length", length_header)
            self.end_headers()
            while True:
                chunk = response.read(4096)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        else:
            # 未知长度（SSE / 流式回复）：分块写回，避免整段缓冲后一直不显示。
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            while True:
                chunk = response.read(4096)
                if not chunk:
                    break
                self.wfile.write(b"%x\r\n" % len(chunk))
                self.wfile.write(chunk)
                self.wfile.write(b"\r\n")
                try:
                    self.wfile.flush()
                except OSError:
                    break
            try:
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except OSError:
                pass
        connection.close()


def _hard_stop(relay: RelayState) -> None:
    """关掉开关立刻断开所有手机连接。"""
    server = relay._server
    if server is not None:
        try:
            server.shutdown()
            server.server_close()
        except OSError:
            pass
    # 已有 keep-alive 连接由进程退出兜底断开，不留给手机继续复用。
    time.sleep(0.2)
    os._exit(0)


def _write_state(relay: RelayState) -> None:
    payload = {
        "admin_port": relay.admin_port,
        "lan_port": relay.lan_port,
        "backend": f"{relay.backend_host}:{relay.backend_port}",
        "pid": os.getpid(),
        "started_at": relay.started_at,
        "lan_urls": relay.lan_urls(),
    }
    _write_private(_relay_dir() / STATE_FILE, json.dumps(payload, ensure_ascii=False, indent=2))


def _serve(relay: RelayState, lan_host: str, lan_port: int, admin_port: int) -> int:
    lan_server = QuietHTTPServer((lan_host, lan_port), RelayHandler)
    lan_server.relay = relay  # type: ignore[attr-defined]

    admin_server = QuietHTTPServer(("127.0.0.1", admin_port), AdminHandler)
    admin_server.relay = relay  # type: ignore[attr-defined]

    relay._server = lan_server

    def _admin_forever() -> None:
        # 管理面死掉会让 start/stop/kick 全部失灵，而且没有任何提示；
        # 这里兜住异常并记录，线程意外退出时能立刻从日志看出来。
        while True:
            try:
                admin_server.serve_forever(poll_interval=0.5)
                return
            except Exception:  # pragma: no cover - 仅防御
                traceback.print_exc()
                time.sleep(0.3)

    threading.Thread(target=_admin_forever, name="relay-admin", daemon=True).start()

    def stop(*_args) -> None:
        threading.Thread(target=_hard_stop, args=(relay,), daemon=True).start()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    print("[relay] 局域网中继已开启", flush=True)
    for base in relay.lan_urls() or ["（未探测到局域网地址）"]:
        print(f"[relay]   手机打开：{base}", flush=True)
    snapshot = relay.pair_snapshot()
    print(f"[relay]   配对码：{snapshot['pair_code']}（{PAIR_TTL_SECONDS // 60} 分钟内一次有效）", flush=True)
    for url in relay.status().get("pair_urls") or []:
        print(f"[relay]   一步配对：{url}", flush=True)
    print(f"[relay]   转发到：http://{relay.backend_host}:{relay.backend_port}", flush=True)
    print(f"[relay]   管理面：http://127.0.0.1:{admin_port}", flush=True)

    try:
        lan_server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            lan_server.server_close()
            admin_server.shutdown()
        except OSError:
            pass
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="鎏灏局域网手机中继（默认关闭，显式运行才开启）")
    parser.add_argument("--lan-host", default="0.0.0.0", help="局域网监听地址（默认 0.0.0.0）")
    parser.add_argument("--lan-port", type=int, default=8090, help="局域网监听端口（默认 8090）")
    parser.add_argument(
        "--admin-port", type=int, default=8091, help="管理面端口（只绑 127.0.0.1，默认 8091）"
    )
    parser.add_argument(
        "--backend-host", default="127.0.0.1", help="回环网关地址（默认 127.0.0.1）"
    )
    parser.add_argument("--backend-port", type=int, default=8080, help="回环网关端口（默认 8080）")
    args = parser.parse_args(argv)

    devices = DeviceStore(_relay_dir() / DEVICE_FILE)
    relay = RelayState(
        backend_host=args.backend_host,
        backend_port=args.backend_port,
        lan_host=args.lan_host,
        lan_port=args.lan_port,
        admin_port=args.admin_port,
        devices=devices,
        admin_token=_admin_token(),
    )
    _write_state(relay)
    return _serve(relay, args.lan_host, args.lan_port, args.admin_port)


if __name__ == "__main__":
    raise SystemExit(main())
