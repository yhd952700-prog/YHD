"""一次 HTTP 请求的来源上下文（本机 / 局域网手机）。

为什么需要
----------
审计要在**写入那一刻**知道"这条指令来自手机"，但审计写入点
（``src/kernels/audit`` 的 ``log_event``）拿不到 FastAPI 的 ``Request``：
它可能在内核深处、在后台线程里、在策略判定之后才被调用。把 ``Request``
一路透传下去会污染所有内核签名，所以用 ``ContextVar``：

* 网关入口的中间件根据「是否回环 + 中继共享密钥」设置来源；
* ``log_event`` 落盘时读取，写进 ``details``；
* 没有设置时一律按 ``local`` —— 默认不把本机操作标成"来自手机"。

安全口径
--------
来源**不能**由请求体或普通请求头决定。``client`` 字段来自登录请求体，
可被任何调用方伪造；只有中继在回环上用共享密钥签过名的请求才可信，
这一点由 ``src/gateway/device_sessions.py`` 的 ``relay_authorized`` 保证。
"""
from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Dict, Optional

LOCAL_CHANNEL = "local"
LAN_CHANNEL = "lan"


@dataclass(frozen=True)
class RequestOrigin:
    """一条请求的来源；默认是"本机"。"""

    channel: str = LOCAL_CHANNEL
    device_id: str = ""
    remote_ip: str = ""

    @property
    def is_lan(self) -> bool:
        return self.channel == LAN_CHANNEL


_DEFAULT = RequestOrigin()
#: ``None`` 表示"这次调用不在一次 HTTP 请求里"（后台线程、内核自检、测试直接调用）。
#: 只有中间件显式设置过才写来源字段，避免给所有历史审计事件都塞一个 ``local``。
_origin: ContextVar[Optional[RequestOrigin]] = ContextVar("liuhao_request_origin", default=None)


def set_origin(origin: RequestOrigin) -> Token:
    return _origin.set(origin)


def reset_origin(token: Token) -> None:
    _origin.reset(token)


def current_origin() -> RequestOrigin:
    """当前请求来源；不在请求里时返回 ``local``。"""
    return _origin.get() or _DEFAULT


def mask_ip(ip: str) -> str:
    """审计只留网段，不留完整地址。

    ``192.168.1.23`` → ``192.168.1.*``。IPv6 与异常值原样返回空串，避免
    把地址的一部分当成本机标识泄露出去。
    """
    raw = (ip or "").strip()
    if not raw:
        return ""
    if ":" in raw:
        # IPv6 前缀本身就可能定位到家庭网络，不做部分保留。
        return ""
    parts = raw.split(".")
    if len(parts) != 4:
        return ""
    return ".".join(parts[:3] + ["*"])


def origin_details() -> Dict[str, str]:
    """要合并进审计 ``details`` 的字段（调用方显式字段优先）。

    不在一次 HTTP 请求里时返回空字典 —— 后台线程产生的审计事件没有"来源"，
    硬写一个 ``local`` 只会让历史事件全多出一个字段。
    """
    origin = _origin.get()
    if origin is None:
        return {}
    details = {"source_channel": origin.channel or LOCAL_CHANNEL}
    if origin.device_id:
        details["source_device"] = origin.device_id
    masked = mask_ip(origin.remote_ip)
    if masked:
        details["source_ip"] = masked
    return details
