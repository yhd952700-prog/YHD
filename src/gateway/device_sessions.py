"""手机设备的会话换发（只对局域网中继开放）。

为什么不在手机上输管理员密码
----------------------------
手机浏览器与电脑之间是明文 HTTP。把管理员密码放进请求体，等于把长期凭据
暴露给同网段任何一个抓包的人，而且用户会在手机上反复输入它。这里改成：

1. 手机只持有一枚**设备 Cookie**（配对码换来的随机 secret，HttpOnly）；
2. 局域网中继（回环进程）用「设备 ID + 共享密钥」向网关换一张令牌；
3. 网关只对**回环 + 共享密钥匹配**的请求签发，且签出的令牌只用于这台设备。

共享密钥写在数据目录 ``mobile_access/relay.secret``，只有本机当前用户可读；
手机永远拿不到它，也永远不需要知道管理员密码。

令牌有效期沿用账号体系的 TTL 配置（不在这里另设一套），吊销仍走
``/v1/auth/logout`` —— 中继记录着每台设备拿到的令牌，踢设备时逐个注销。
"""
from __future__ import annotations

import json
import os
import secrets
import threading
import time
from collections import deque
from pathlib import Path
from typing import Deque, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..common.fs_permissions import restrict_to_current_user
from ..common.request_context import (
    LAN_CHANNEL,
    RequestOrigin,
    reset_origin,
    set_origin,
)
from .auth import issue_token_for

router = APIRouter(prefix="/v1", tags=["auth"])

RELAY_HEADER = "x-liuhao-relay"
CHANNEL_HEADER = "x-liuhao-channel"
DEVICE_HEADER = "x-liuhao-device"
CLIENT_IP_HEADER = "x-liuhao-client-ip"

RELAY_DIR_REL = Path("mobile_access")
SECRET_FILE = "relay.secret"

#: 每台设备换令牌的上限：正常一次页面加载只换一次，60 次/分钟足够，
#: 同时挡住"配对后拿 Cookie 刷令牌"的滥用。
_ISSUE_WINDOW_SECONDS = 60
_ISSUE_MAX = 60

_secret_lock = threading.Lock()
_secret_cache: Optional[str] = None
_secret_mtime: float = 0.0

_issue_lock = threading.Lock()
_issue_history: Dict[str, Deque[float]] = {}


def _data_dir() -> Path:
    """与 ``scripts/ops/lan_relay.py`` 完全一致的解析顺序（两处必须指向同一目录）。"""
    raw = (os.environ.get("LIUHAO_DATA_DIR") or "").strip()
    if raw:
        return Path(raw).expanduser()
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home())
        return base / "LiuHao" / "data"
    base = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
    return base / "liuhao"


def relay_dir() -> Path:
    path = _data_dir() / RELAY_DIR_REL
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_relay_secret() -> str:
    """读取（必要时创建）中继共享密钥；文件权限收紧到当前用户。"""
    global _secret_cache, _secret_mtime
    path = relay_dir() / SECRET_FILE
    with _secret_lock:
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = 0.0
        if _secret_cache and mtime and mtime == _secret_mtime:
            return _secret_cache
        try:
            value = (path.read_text(encoding="utf-8") or "").strip()
        except OSError:
            value = ""
        if not value:
            value = secrets.token_urlsafe(32)
            path.write_text(value, encoding="utf-8")
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
            restrict_to_current_user(path)
            mtime = path.stat().st_mtime
        _secret_cache = value
        _secret_mtime = mtime
        return value


def _is_loopback(request: Request) -> bool:
    client = request.client
    host = (client.host if client else "") or ""
    return host in {"127.0.0.1", "::1", "localhost"}


def relay_authorized(request: Request) -> bool:
    """中继身份：必须来自回环，且共享密钥匹配。"""
    if not _is_loopback(request):
        return False
    supplied = (request.headers.get(RELAY_HEADER) or "").strip()
    if not supplied:
        return False
    try:
        expected = load_relay_secret()
    except OSError:
        return False
    return secrets.compare_digest(supplied, expected)


def _device_principal() -> Optional[str]:
    """手机设备令牌以谁的身份签发。

    优先用「在电脑上打开开关的那个人」——记录在 ``mobile_access/lan_access.json``；
    找不到时退回本机根账号。两者都没有就返回 ``None``：不猜、不降级成匿名。
    """
    try:
        settings = json.loads((relay_dir() / "lan_access.json").read_text(encoding="utf-8"))
        principal = str((settings or {}).get("principal") or "").strip()
        if principal:
            return principal
    except (OSError, ValueError):
        pass
    return _root_principal()


def _root_principal() -> Optional[str]:
    """本机最高权限账号；没有账号树时返回 ``None``（不猜、不降级）。"""
    try:
        from ..kernels.identity import get_identity_manager

        nodes = get_identity_manager().account_nodes()
    except Exception:  # noqa: BLE001 - 账号内核不可用按"没有可登录账号"处理
        return None
    roots = sorted(node.principal for node in nodes.values() if node.is_root)
    return roots[0] if roots else None


def install_origin_middleware(app) -> None:
    """把"这次请求来自本机还是手机"写进 ContextVar，供审计与策略读取。

    只有「回环 + 中继共享密钥」的请求才被标记为 ``lan``。手机可以直接伪造
    ``X-Liuhao-Channel: lan``，但拿不到 ``relay.secret``，因此不会被采信。
    """

    @app.middleware("http")
    async def _origin_middleware(request: Request, call_next):
        origin = RequestOrigin()
        try:
            channel = (request.headers.get(CHANNEL_HEADER) or "").strip().lower()
            if channel == LAN_CHANNEL and relay_authorized(request):
                origin = RequestOrigin(
                    channel=LAN_CHANNEL,
                    device_id=(request.headers.get(DEVICE_HEADER) or "").strip()[:64],
                    remote_ip=(request.headers.get(CLIENT_IP_HEADER) or "").strip()[:64],
                )
        except Exception:  # noqa: BLE001 - 来源识别失败按本机处理，不影响请求
            origin = RequestOrigin()
        token = set_origin(origin)
        try:
            return await call_next(request)
        finally:
            reset_origin(token)


def _rate_limited(device_id: str) -> bool:
    now = time.time()
    with _issue_lock:
        history = _issue_history.setdefault(device_id, deque())
        while history and now - history[0] > _ISSUE_WINDOW_SECONDS:
            history.popleft()
        if len(history) >= _ISSUE_MAX:
            return True
        history.append(now)
        return False


class DeviceLoginRequest(BaseModel):
    device_id: str = Field(..., min_length=4, max_length=64, description="中继分配的设备 ID")
    device_name: str = Field("手机", max_length=64, description="设备名（仅用于展示）")


@router.post("/auth/device", response_model=Dict)
def auth_device(req: DeviceLoginRequest, request: Request) -> Dict:
    """用设备身份换一张令牌：只接受局域网中继的回环请求。"""
    if not relay_authorized(request):
        raise HTTPException(
            status_code=403,
            detail="设备会话只能由本机的手机访问中继换取。手机上不需要、也不能输入管理员密码。",
        )
    if _rate_limited(req.device_id):
        raise HTTPException(status_code=429, detail="这台设备换令牌过于频繁，请稍后再试。")
    principal = _device_principal()
    if not principal:
        raise HTTPException(
            status_code=409,
            detail="这台电脑还没有可登录的账号，先在电脑上完成账号设置再开启手机访问。",
        )
    payload = issue_token_for(principal, "mobile")
    payload["device_id"] = req.device_id
    return payload
