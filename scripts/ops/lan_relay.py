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
#: 5 分钟：同一 Wi-Fi 内 6 位码可被穷举，窗口越长越危险（见 PairGuard）。
PAIR_TTL_SECONDS = 300
PAIR_CODE_DIGITS = 6

#: 配对失败锁定：同一来源 IP 10 分钟内错 5 次 → 锁 15 分钟；
#: 全局（不限 IP）错 20 次 → 当前码直接作废，必须回电脑重新生成。
PAIR_FAIL_WINDOW_SECONDS = 600
PAIR_FAIL_MAX = 5
PAIR_LOCK_SECONDS = 900
PAIR_GLOBAL_FAIL_MAX = 20

#: 设备 Cookie 校验失败限流：同一 IP 1 分钟内 10 次无效 → 429。
TOKEN_FAIL_WINDOW_SECONDS = 60
TOKEN_FAIL_MAX = 10

#: 登录失败冷却：同一设备 + 来源 IP，窗口内 5 次失败 → 冷却 15 分钟。
FAIL_WINDOW_SECONDS = 300
FAIL_MAX = 5
FAIL_COOLDOWN_SECONDS = 900

#: 手机请求明细（只在本机，2MB 轮转）。与网关审计互补：网关记业务动作，
#: 这里记"哪台设备、哪个 IP、什么时候、请求了什么"。
ACCESS_LOG_FILE = "access.jsonl"
ACCESS_LOG_MAX_BYTES = 2 * 1024 * 1024

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


def _relay_secret() -> str:
    """与网关共享的密钥：证明"这次转发来自本机中继"，而不是手机自己编的请求头。

    网关侧由 ``src/gateway/device_sessions.py`` 读取同一个文件；两边路径必须一致。
    """
    path = _relay_dir() / "relay.secret"
    try:
        value = (path.read_text(encoding="utf-8") or "").strip()
    except OSError:
        value = ""
    if not value:
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

    def remember_session(self, device_id: str, session: Dict[str, Any]) -> None:
        """记住这台设备换到的会话（令牌 + 展示字段），避免每次加载都换一次。"""
        token = str(session.get("access_token") or "")
        if not token:
            return
        with self._lock:
            record = self._devices.get(device_id)
            if not record:
                return
            record["session"] = session
            tokens = [str(item) for item in (record.get("tokens") or [])]
            if token in tokens:
                tokens.remove(token)
            tokens.append(token)
            record["tokens"] = tokens[-10:]
            self._save()

    def session(self, device_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            record = self._devices.get(device_id)
            if not record:
                return None
            session = record.get("session")
            return dict(session) if isinstance(session, dict) else None

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


class PairGuard:
    """配对码失败锁定：同一 Wi-Fi 里有人暴力猜码时，把窗口和次数都收紧。

    6 位码只有一百万种，5 分钟内放开猜等于给同一网段的人一台没有锁的保险柜。
    这里做两件事：

    * 按来源 IP 计数：10 分钟内错 5 次 → 该 IP 锁 15 分钟；
    * 全局计数：当前配对码累计错 20 次 → 直接作废，必须回电脑重新生成。

    成功配对会清空全部计数（那说明码本来就是对的，不应该牵连后来的人）。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._failures: Dict[str, deque] = {}
        self._locked_until: Dict[str, float] = {}
        self._global_failures = 0

    def blocked_for(self, ip: str) -> int:
        with self._lock:
            until = self._locked_until.get(ip, 0.0)
            if until <= time.time():
                self._locked_until.pop(ip, None)
                return 0
            return int(until - time.time()) + 1

    def register_failure(self, ip: str) -> None:
        now = time.time()
        with self._lock:
            recent = self._failures.setdefault(ip, deque())
            while recent and now - recent[0] > PAIR_FAIL_WINDOW_SECONDS:
                recent.popleft()
            recent.append(now)
            if len(recent) >= PAIR_FAIL_MAX:
                self._locked_until[ip] = now + PAIR_LOCK_SECONDS
                recent.clear()
            self._global_failures += 1

    def global_exhausted(self) -> bool:
        with self._lock:
            return self._global_failures >= PAIR_GLOBAL_FAIL_MAX

    def reset(self) -> None:
        with self._lock:
            self._failures.clear()
            self._locked_until.clear()
            self._global_failures = 0


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
        self.pair_guard = PairGuard()
        # Cookie 校验失败按来源 IP 计数（设备 ID 此时本来就不可信）。
        self.token_guard = LoginGuard()
        # 每台设备当前打开的连接：踢设备 / 关开关时要能立刻把它们掐断，
        # 而不是等浏览器自觉断开。
        self.connections: Dict[str, set] = {}
        self.stopping = False
        # 可重入：rotate_pair_code() 持锁时会再调用 pair_snapshot()，普通 Lock 会自锁死。
        self._lock = threading.RLock()
        # 局域网地址只在启动时探测一次：DNS / 网卡枚举在某些网络下会阻塞十几秒，
        # 放进请求路径会让管理面的 /status 直接超时。
        probed = _lan_ipv4_addresses()
        if lan_host and lan_host not in ("0.0.0.0", "::"):
            # 显式绑定了某个网卡地址：只报这一个地址。其它网卡（WSL/Hyper-V 虚拟
            # 网卡等）根本没在监听，报出去只会让用户扫了打不开。
            self._lan_urls = [lan_host]
        else:
            self._lan_urls = probed
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

    # ---- 活动连接 ---------------------------------------------------------
    def register_connection(self, device_id: str, connection) -> None:
        if not device_id or connection is None:
            return
        with self._lock:
            self.connections.setdefault(device_id, set()).add(connection)

    def unregister_connection(self, device_id: str, connection) -> None:
        if not device_id or connection is None:
            return
        with self._lock:
            bucket = self.connections.get(device_id)
            if bucket is None:
                return
            bucket.discard(connection)
            if not bucket:
                self.connections.pop(device_id, None)

    def close_device_connections(self, device_id: str) -> int:
        """立刻掐断一台手机的现有连接（不等它下次请求才发现被踢）。"""
        with self._lock:
            bucket = list(self.connections.pop(device_id, set()))
        return _shutdown_sockets(bucket)

    def close_all_connections(self) -> int:
        with self._lock:
            buckets = list(self.connections.values())
            self.connections.clear()
        closed = 0
        for bucket in buckets:
            closed += _shutdown_sockets(list(bucket))
        return closed

    def online_device_ids(self) -> set:
        """60 秒内有请求，或仍有打开的连接，都算"在线"。"""
        now = time.time()
        online = set()
        for record in self.devices.list():
            last = record.get("last_seen_at")
            if isinstance(last, (int, float)) and now - float(last) <= 60:
                online.add(str(record.get("id")))
        with self._lock:
            online |= set(self.connections.keys())
        return online

    def devices_snapshot(self) -> List[Dict[str, Any]]:
        online = self.online_device_ids()
        items = []
        for record in self.devices.list():
            items.append(
                {
                    **record,
                    "online": str(record.get("id")) in online,
                }
            )
        return items

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
            "online_count": len(self.online_device_ids()),
            "devices": self.devices_snapshot(),
            "started_at": self.started_at,
            "lan_host": self.lan_host,
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
            self._json(200, {"devices": self.relay.devices_snapshot()})
            return
        if path == "/devices/revoke-all" and method == "POST":
            records = self.relay.devices.clear()
            for record in records:
                _revoke_tokens(self.relay, record)
            self.relay.close_all_connections()
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
            self.relay.close_device_connections(str(record.get("id")))
            self._json(200, {"revoked": 1, "id": record.get("id")})
            return
        if path == "/shutdown" and method == "POST":
            self._json(200, {"stopping": True})
            threading.Thread(target=_hard_stop, args=(self.relay,), daemon=True).start()
            return
        self._json(404, {"detail": "未知的管理操作"})

    do_GET = do_POST = _handle


def _shutdown_sockets(sockets) -> int:
    """对一批已建立的连接 socket 做 shutdown：让手机的请求立刻失败。

    只关监听端口不够 —— keep-alive 连接会继续复用，手机看起来"还连着"。
    """
    closed = 0
    for sock in sockets:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            sock.close()
        except OSError:
            pass
        closed += 1
    return closed


def _log_access(record: Dict[str, Any]) -> None:
    """把一条手机请求写进本机明细日志；写不进去也不能影响请求本身。"""
    try:
        path = _relay_dir() / ACCESS_LOG_FILE
        try:
            if path.stat().st_size > ACCESS_LOG_MAX_BYTES:
                backup = path.with_suffix(".jsonl.1")
                os.replace(path, backup)
        except OSError:
            pass
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        pass


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
                    # 私有头与标准头一样要求 ``Bearer <token>``：网关只认这个格式，
                    # 裸令牌会被当成"没提供令牌"，注销静默失败而令牌仍然可用。
                    "X-Liuhao-Token": f"Bearer {token}",
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
<title>连接贾维斯</title>
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
  <h1>连接贾维斯</h1>
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

    # ---- 连接跟踪 ---------------------------------------------------------
    def setup(self) -> None:
        super().setup()
        self._device_key = ""

    def finish(self) -> None:
        try:
            self.relay.unregister_connection(getattr(self, "_device_key", ""), self.connection)
        finally:
            super().finish()

    def _bind_device(self, device_id: str) -> None:
        """把这条 TCP 连接记到设备名下，踢设备/关开关时能立刻掐断。"""
        if not device_id or device_id == getattr(self, "_device_key", ""):
            return
        previous = getattr(self, "_device_key", "")
        if previous:
            self.relay.unregister_connection(previous, self.connection)
        self._device_key = device_id
        self.relay.register_connection(device_id, self.connection)

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

        ip = self._client_ip()
        # 拿着无效 Cookie 反复试：同一 IP 1 分钟 10 次 → 429（防止在配对页之外爆破设备 secret）。
        had_cookie = COOKIE_NAME in (self.headers.get("cookie") or "")
        device = self._cookie_device()
        if device is None and had_cookie:
            blocked = self.relay.token_guard.blocked_for("cookie", ip)
            if blocked:
                self._json(
                    429,
                    {"detail": "设备校验失败次数过多，请稍后再试。", "retry_after": blocked},
                )
                return
            self.relay.token_guard.register_failure("cookie", ip)

        # 手机端不做账号管理：密码登录、设备换令牌、恢复码、账号增删与「手机访问」本身
        # 都只能在电脑上完成。中继先拦一层，网关侧还有来源校验兜底。
        if self._blocked_for_lan(path):
            self._json(
                403,
                {
                    "detail": (
                        "手机端不支持这项操作：管理员密码、恢复码与账号管理只在电脑上可用。"
                        "手机通过配对码获得自己的设备令牌，不需要也不会输入密码。"
                    )
                },
            )
            return

        if device is None:
            if self.command in ("GET", "HEAD") and path in ("/", "/index.html"):
                # 带着已失效的设备 Cookie 回来，多半是电脑端刚把它踢了。
                hint = (
                    "这台设备已经被电脑端移除，请让电脑重新生成配对码后再配对。"
                    if had_cookie
                    else "配对后才会进入登录页。"
                )
                self._pair_page(hint=hint)
                return
            self._json(401, {"detail": "设备未配对：请先在电脑端生成配对码，并在手机上完成配对。"})
            return

        self.relay.devices.touch(str(device["id"]), ip)
        self._bind_device(str(device["id"]))
        # 校验通过就清掉失败计数，避免正常用户在弱网下被自己之前的重试拖累。
        self.relay.token_guard.clear("cookie", ip)

        if self.command in ("POST", "PUT", "PATCH", "DELETE") and not self._origin_is_own():
            self._json(403, {"detail": "跨站写请求被中继拒绝：来源与当前地址不同。"})
            return

        if path == "/v1/health" and self.command in ("GET", "HEAD"):
            self._forward_health()
            return

        session = self._ensure_session(device)
        if session is None:
            self._json(
                502,
                {
                    "detail": (
                        "本地服务没有给出设备会话：请确认电脑上的贾维斯正在运行，"
                        "并在设置里重新开启手机访问。"
                    )
                },
            )
            return

        _log_access(
            {
                "ts": time.time(),
                "device_id": device.get("id"),
                "device": device.get("name"),
                "ip": ip,
                "method": self.command,
                "path": path,
                "query": parsed.query[:200],
                "ua": (self.headers.get("user-agent") or "")[:200],
            }
        )

        if self.command in ("GET", "HEAD") and path in ("/", "/index.html"):
            self._forward_index(device, session)
            return

        self._forward(device, session)

    def _blocked_for_lan(self, path: str) -> bool:
        if path.startswith("/v1/auth/login"):
            return True
        if path.startswith("/v1/auth/device"):
            return True
        if path.startswith("/v1/auth/recovery"):
            return True
        if path.startswith("/v1/accounts"):
            return True
        # 手机不能自己开关局域网访问、重新配对或吊销设备 —— 那是电脑端的主权。
        if path.startswith("/v1/lan-access"):
            return True
        return False

    # ---- 配对 -------------------------------------------------------------
    def _relay_route(self, path: str, query: str) -> None:
        if path == "/__relay/session":
            self._relay_session()
            return
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

        ip = self._client_ip()
        blocked = self.relay.pair_guard.blocked_for(ip)
        if blocked:
            self._pair_page(
                hint=f"配对失败次数过多，请 {max(1, blocked // 60)} 分钟后再试；或在电脑上重新生成配对码。"
            )
            return

        if not code:
            self._pair_page()
            return

        # 全局失败次数达到上限：这个码已经不能再用，直接换一个新的并提示回电脑查看。
        if self.relay.pair_guard.global_exhausted():
            self.relay.rotate_pair_code()
            self.relay.pair_guard.reset()
            self._pair_page(hint="配对失败次数过多，当前配对码已作废。请在电脑上重新生成。")
            return

        if not self.relay.consume_code(code):
            self.relay.pair_guard.register_failure(ip)
            self._pair_page(hint="配对码无效或已过期。请在电脑端重新生成。")
            return

        self.relay.pair_guard.reset()
        record = self.relay.devices.add(
            name=_device_name(self.headers.get("user-agent", "")),
            ip=ip,
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

    def _relay_session(self) -> None:
        """已配对设备换一份浏览器可用的会话（bootstrap 之外的人工排查入口）。"""
        device = self._cookie_device()
        if device is None:
            self._json(401, {"detail": "设备未配对。"})
            return
        session = self._ensure_session(device)
        if session is None:
            self._json(502, {"detail": "本地服务没有给出设备会话。"})
            return
        self._json(200, _session_payload(session))

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
    def _upstream_headers(
        self,
        session: Optional[Dict[str, Any]] = None,
        device: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, str]:
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
        if session is not None:
            token = str(session.get("access_token") or "")
            if token:
                # 覆盖手机自己带的令牌：手机只持有设备 Cookie，业务令牌由中继代持。
                # 两个头都按 ``Bearer <token>`` 写：网关私有头优先，格式不对会退回标准头。
                headers["X-Liuhao-Token"] = f"Bearer {token}"
                headers["Authorization"] = f"Bearer {token}"
        if device is not None:
            headers["X-Liuhao-Channel"] = "lan"
            headers["X-Liuhao-Device"] = str(device.get("id") or "")
            headers["X-Liuhao-Client-IP"] = self._client_ip()
        # 共享密钥：网关只在「回环 + 密钥匹配」时采信上面的来源头。
        headers["X-Liuhao-Relay"] = _relay_secret()
        return headers

    # ---- 设备会话 ---------------------------------------------------------
    def _ensure_session(self, device: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """取这台设备当前可用的网关令牌；没有就向回环网关换一张。

        手机上不会出现密码输入框：换令牌靠的是「设备 ID + 中继共享密钥」，
        两者都只在本机流转（Cookie 里的 secret 由中继校验，共享密钥手机没有）。
        """
        cached = self.relay.devices.session(str(device.get("id")))
        if cached and cached.get("access_token"):
            return cached
        payload = self._mint_session(device)
        if payload is None:
            return None
        self.relay.devices.remember_session(str(device.get("id")), payload)
        return payload

    def _mint_session(self, device: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        body = json.dumps(
            {
                "device_id": str(device.get("id") or ""),
                "device_name": str(device.get("name") or "手机"),
            },
            ensure_ascii=False,
        ).encode("utf-8")
        connection = self._connect()
        try:
            connection.request(
                "POST",
                "/v1/auth/device",
                body=body,
                headers={
                    "Host": f"127.0.0.1:{self.relay.backend_port}",
                    "Content-Type": "application/json",
                    "Content-Length": str(len(body)),
                    "X-Liuhao-Relay": _relay_secret(),
                },
            )
            response = connection.getresponse()
            raw = response.read()
        except OSError:
            return None
        finally:
            connection.close()
        if response.status != 200:
            return None
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

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

    def _forward_index(self, device: Dict[str, Any], session: Dict[str, Any]) -> None:
        """首页 HTML 在发出去之前注入会话：手机不再看到登录页，也不需要输密码。

        为什么注入而不是改前端：``App.tsx`` / ``lib/auth.ts`` / ``Login.tsx``
        当前有别人未提交的改动，不能碰。注入点选在第一个 ``<script type="module">``
        之前，保证 ``main.tsx`` 执行时 ``sessionStorage`` 里已经有会话。
        """
        connection = self._connect()
        try:
            connection.request("GET", self.path, headers=self._upstream_headers(session, device))
            response = connection.getresponse()
            raw = response.read()
        except OSError as exc:
            self._json(502, {"detail": f"本地服务没有响应：{exc}"})
            return
        finally:
            connection.close()

        status = response.status
        content_type = (response.getheader("Content-Type") or "").lower()
        if status == 200 and "html" in content_type:
            html = raw.decode("utf-8", "replace")
            raw = self._inject_bootstrap(html, session).encode("utf-8")

        self.send_response(status)
        for name, value in response.getheaders():
            if name.lower() in _HOP_BY_HOP or name.lower() == "content-length":
                continue
            self.send_header(name, value)
        # 页面里带着设备令牌：不允许缓存，换设备/踢掉后不能从缓存里恢复。
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(raw)

    def _inject_bootstrap(self, html: str, session: Dict[str, Any]) -> str:
        payload = json.dumps(_session_payload(session), ensure_ascii=False)
        # 防止账号昵称里出现 "</script>" 把注入脚本提前闭合。
        payload = payload.replace("<", "\\u003c")
        script = (
            "<script>window.__LIUHAO_LAN__=true;try{"
            "var s=" + payload + ";"
            "sessionStorage.setItem('liuhao.session.tab',JSON.stringify(s));"
            "sessionStorage.setItem('liuhao.session',JSON.stringify(s));"
            "}catch(e){}</script>"
        )
        lowered = html.lower()
        index = lowered.find('<script type="module"')
        if index < 0:
            index = lowered.find("</body>")
        if index < 0:
            return html + script
        return html[:index] + script + html[index:]

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

    def _forward(self, device: Dict[str, Any], session: Dict[str, Any]) -> None:
        body = self._read_body()
        connection = self._connect()
        try:
            connection.request(
                self.command,
                self.path,
                body=body,
                headers=self._upstream_headers(session, device),
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


def _session_payload(session: Dict[str, Any]) -> Dict[str, Any]:
    """网关令牌响应 → 前端 ``parseSession`` 认识的形状（字段名必须完全对上）。"""
    return {
        "token": str(session.get("access_token") or ""),
        "principal": session.get("principal"),
        "displayName": session.get("display_name") or session.get("displayName"),
        "scope": session.get("scope"),
        "permissions": session.get("permissions") or [],
        "expiresAt": session.get("expires_at") or session.get("expiresAt"),
        "client": "mobile",
        "isRoot": bool(session.get("is_root", session.get("isRoot", False))),
        "canRotateRecovery": session.get("can_rotate_recovery"),
    }


class RelayRuntime:
    """进程内运行的中继（网关的「手机访问」开关直接用它）。

    与 CLI 模式的区别只有生命周期：这里没有独立进程，关闭是同步的，
    不需要 ``--parent-pid`` 看门狗，也不会出现"父进程退了、子进程还在监听"。
    """

    def __init__(self, relay: RelayState, lan_server, admin_server) -> None:
        self.relay = relay
        self._lan = lan_server
        self._admin = admin_server
        self._threads: List[threading.Thread] = []
        self._stopped = False

    # 网关侧只依赖这三个只读入口，不直接摸 relay 内部字段。
    @property
    def lan_host(self) -> str:
        return self.relay.lan_host

    @property
    def lan_port(self) -> int:
        return self.relay.lan_port

    def status(self) -> Dict[str, Any]:
        return self.relay.status()

    def start(self) -> None:
        for name, server in (("relay-lan", self._lan), ("relay-admin", self._admin)):
            thread = threading.Thread(
                target=server.serve_forever,
                kwargs={"poll_interval": 0.25},
                name=name,
                daemon=True,
            )
            thread.start()
            self._threads.append(thread)

    def stop(self) -> int:
        """同步关闭：先掐断已建立的连接，再关监听端口。返回断开的连接数。"""
        if self._stopped:
            return 0
        self._stopped = True
        self.relay.stopping = True
        closed = self.relay.close_all_connections()
        for server in (self._lan, self._admin):
            try:
                server.shutdown()
                server.server_close()
            except OSError:
                pass
        for thread in self._threads:
            thread.join(timeout=2)
        return closed


def start_inline(
    *,
    backend_host: str = "127.0.0.1",
    backend_port: int = 8080,
    lan_host: str,
    lan_port: int,
) -> RelayRuntime:
    """绑定 ``lan_host:lan_port`` 并返回运行句柄；端口占用会抛 ``OSError``。

    只绑一个显式网卡地址（不是 ``0.0.0.0``）：换 Wi-Fi / 断网时这个地址会消失，
    连接自然进不来，不会顺带监听新网络的地址。
    """
    devices = DeviceStore(_relay_dir() / DEVICE_FILE)
    relay = RelayState(
        backend_host=backend_host,
        backend_port=backend_port,
        lan_host=lan_host,
        lan_port=lan_port,
        admin_port=0,
        devices=devices,
        admin_token="",
    )
    lan_server = QuietHTTPServer((lan_host, lan_port), RelayHandler)
    lan_server.relay = relay  # type: ignore[attr-defined]
    # 管理面仍然起在回环的随机端口：CLI/排查脚本可以复用，手机碰不到。
    admin_server = QuietHTTPServer(("127.0.0.1", 0), AdminHandler)
    admin_server.relay = relay  # type: ignore[attr-defined]
    relay.admin_port = int(admin_server.server_address[1])
    relay._server = lan_server
    runtime = RelayRuntime(relay, lan_server, admin_server)
    runtime.start()
    _write_state(relay)
    return runtime


def _hard_stop(relay: RelayState) -> None:
    """关掉开关立刻断开所有手机连接。"""
    server = relay._server
    relay.close_all_connections()
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
    parser = argparse.ArgumentParser(description="贾维斯局域网手机中继（默认关闭，显式运行才开启）")
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
