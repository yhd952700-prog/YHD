"""运行时出站目标校验（模型网关）。

保存/测试时校验只能证明"那一刻"的目标是安全的；真正的请求发生在之后，
攻击面有两种：

* 保存时是公网域名，运行时该域名 302 到 ``169.254.169.254``；
* 保存时解析到公网 IP，运行时 DNS 变成私网地址（DNS rebinding）。

因此这里提供一个**每次请求前**都可调用的校验，并配合关闭 SDK 的自动重定向：
判断分三层顺序执行，任一层拒绝即停：

* 第 0 层形状：只许 ``http/https``；带 Key 时只许 ``https``（http 仅限本机
  Ollama/本地推理端口）；URL 里不许带用户名/密码。任何白名单都绕不过。
* 第 1 层身份：精确主机名比对内置可信厂商（``TRUSTED_VENDOR_HOSTS``）
  与 Owner 白名单（``data/net_guard_allowlist.json``，只 Owner 能改）。
  禁止子串匹配；国际化域名先转 punycode。
* 第 2 层地址：解析结果分三类——A 类（回环、RFC1918、链路本地、未指定、
  组播）永远拦；B 类（非 RFC1918 但 ``is_private``/``is_reserved`` 为真的段，
  如 ``198.18.0.0/15``、``100.64.0.0/10``）只在第 1 层通过时放行；C 类
  （``is_global``）正常放行。解析失败保持 fail-closed。

第 3 层传输环境：调用方传入本次请求是否真的走代理（``proxied`` 参数，
依据 HTTP 客户端实际用的代理配置，见 :func:`request_uses_proxy`）。
走代理时本地 DNS 答案不是连接目标——域名主机跳过第 2 层；IP 字面量
主机永远走第 2 层（字面量地址本身就是连接目标，代理也改不了它）。
``proxied=None`` 表示判断不出，按不走代理处理（fail-closed）。

例外（运营者已配置的本机推理，窄口子，保持不变）：
回环只对 Ollama/本地推理端口放行；``allow_private_service=True`` 且
目标是 ``OLLAMA_BASE_URL`` 指向的 Ollama 时，RFC1918 私网地址放行
（局域网另一台机器上的 Ollama）——链路本地/组播/未指定地址不在此例外内。

DNS rebinding（TOCTOU）
----------------------
校验通过后，解析结果会按线程固定下来；同一线程里 SDK 建连时再次调用
``socket.getaddrinfo`` 只会拿到已经校验过的地址，而不是重新查询 DNS。这样
"校验时公网、建连时私网"的切换窗口被消掉。异常消息保持中性中文，不携带
上游响应体或解析到的内网 IP。
"""
from __future__ import annotations

import ipaddress
import json
import logging
import os
import socket
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Collection, Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import urlparse

_logger = logging.getLogger(__name__)


class RuntimeTargetRejected(RuntimeError):
    """目标地址违反运行时出站策略。消息可直接展示给用户。

    ``kind`` 供调用方区分拒绝原因（不进用户文案，只做程序内分支）：
    ``scheme`` / ``credential`` / ``key_http`` / ``hostname`` /
    ``dns_failed`` / ``address_class_a`` / ``address_class_b_untrusted`` /
    ``address_invalid``。
    """

    def __init__(self, message: str, *, kind: str = "address_rejected") -> None:
        super().__init__(message)
        self.kind = kind


# 内置可信厂商主机名（精确匹配，唯一源头）。改动走代码评审。
# Ollama 本地不进此表，走本地推理例外。
TRUSTED_VENDOR_HOSTS = frozenset(
    {
        "api.deepseek.com",  # DeepSeek
        "api.openai.com",  # OpenAI
        "api.anthropic.com",  # Anthropic
        "generativelanguage.googleapis.com",  # Google Gemini
        "api.moonshot.cn",  # Moonshot（Kimi 国内）
        "api.moonshot.ai",  # Moonshot（Kimi 海外）
        "dashscope.aliyuncs.com",  # 通义千问 DashScope
        "open.bigmodel.cn",  # 智谱 GLM
        "ark.cn-beijing.volces.com",  # 豆包 / 火山方舟
    }
)

#: Owner 白名单文件：文件名 + 覆盖环境变量（路径覆盖，不是名单覆盖）。
ALLOWLIST_FILENAME = "net_guard_allowlist.json"
ALLOWLIST_FILE_ENV = "LIUHAO_NET_GUARD_ALLOWLIST_FILE"
#: Owner 白名单上限条数（设计稿 §4.2）。
ALLOWLIST_MAX_ENTRIES = 50

#: A 类网络：永远拦。注意 ``100.64.0.0/10``（CGNAT）不在此列，它是 B 类。
_CLASS_A_NETWORKS = (
    ipaddress.ip_network("127.0.0.0/8"),  # 回环
    ipaddress.ip_network("10.0.0.0/8"),  # RFC1918
    ipaddress.ip_network("172.16.0.0/12"),  # RFC1918
    ipaddress.ip_network("192.168.0.0/16"),  # RFC1918
    ipaddress.ip_network("169.254.0.0/16"),  # 链路本地（含元数据地址）
    ipaddress.ip_network("0.0.0.0/8"),  # 未指定 / 本网
    ipaddress.ip_network("224.0.0.0/4"),  # 组播
    ipaddress.ip_network("240.0.0.0/4"),  # 保留（含 255.255.255.255 广播）
    ipaddress.ip_network("::1/128"),  # IPv6 回环
    ipaddress.ip_network("::/128"),  # IPv6 未指定
    ipaddress.ip_network("fe80::/10"),  # IPv6 链路本地
    ipaddress.ip_network("fc00::/7"),  # IPv6 唯一本地
    ipaddress.ip_network("ff00::/8"),  # IPv6 组播
)

#: B 类网络：非 RFC1918 但非公网的段。显式列出是必要的：
#: 本机 Python 里 ``100.64.1.2`` 的 ``is_private`` 与 ``is_reserved`` 全是
#: False（``is_global`` 也是 False），只靠标志位会漏判成 C 类。
_CLASS_B_NETWORKS = (
    (ipaddress.ip_network("198.18.0.0/15"), "benchmarking-198.18/15"),
    (ipaddress.ip_network("100.64.0.0/10"), "cgnat-100.64/10"),
    (ipaddress.ip_network("192.0.0.0/24"), "ietf-protocol-192.0.0/24"),
    (ipaddress.ip_network("192.0.2.0/24"), "test-net-1-192.0.2/24"),
    (ipaddress.ip_network("198.51.100.0/24"), "test-net-2-198.51.100/24"),
    (ipaddress.ip_network("203.0.113.0/24"), "test-net-3-203.0.113/24"),
    (ipaddress.ip_network("192.88.99.0/24"), "6to4-relay-192.88.99/24"),
    (ipaddress.ip_network("2001:db8::/32"), "documentation-2001:db8/32"),
)

#: A 类里运营者已配置的 LAN Ollama 例外只豁免 RFC1918 段（见模块注释）。
_RFC1918_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
)
# 已知本地推理服务的回环端口：这些端口放行，其余回环端口按 SSRF 拒绝。
LOCAL_INFERENCE_PORTS = frozenset({11434, 1234, 8000, 8080})


def _normalize_host(host: Optional[str]) -> str:
    """主机名归一化：小写、去首尾空白、去末尾的点、IDN 转 punycode。

    端口不参与身份判定（调用方已用 ``urlparse().hostname`` 去掉端口）。
    非法主机名原样返回小写形式，由后续校验拒绝。
    """
    value = (host or "").strip().lower().rstrip(".")
    if not value:
        return value
    try:
        return value.encode("idna").decode("ascii")
    except (UnicodeError, ValueError):
        return value


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip().strip("[]"))
    except ValueError:
        return False
    return True


def _classify_address(address) -> Tuple[str, str]:
    """把解析地址分成 A（永远拦）/ B（可信才放）/ C（正常放）。

    返回 ``(类别, 段名)``；段名只用于日志与审计，不记具体 IP。
    判定顺序：A（显式网络 + 标志位）→ B（显式网络 + 标志位）→ C；
    既非 A 也非 B、但 ``is_global`` 为 False 的未知地址按 B 处理
    （向可信收敛，不默认放行）。
    """
    address = _coerce_address(address)
    for network in _CLASS_A_NETWORKS:
        if address.version == network.version and address in network:
            if address.is_loopback:
                return "A", "loopback"
            if address.is_link_local:
                return "A", "link-local"
            if address.is_multicast:
                return "A", "multicast"
            if address.is_unspecified:
                return "A", "unspecified"
            if any(
                address.version == net.version and address in net
                for net in _RFC1918_NETWORKS
            ):
                return "A", f"rfc1918-{network}"
            return "A", str(network)
    if (
        address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_unspecified
    ):
        return "A", "loopback" if address.is_loopback else (
            "link-local" if address.is_link_local else (
                "multicast" if address.is_multicast else "unspecified"
            )
        )
    for network, name in _CLASS_B_NETWORKS:
        if address.version == network.version and address in network:
            return "B", name
    if any(getattr(address, flag) for flag in ("is_private", "is_reserved")):
        return "B", "reserved-private"
    if address.is_global:
        return "C", "global"
    return "B", "non-global-unknown"


def _log_decision(
    *,
    host: str,
    layer: int,
    decision: str,
    ip_class: str,
    context: str,
    proxied: Optional[bool],
) -> None:
    """记一条守卫裁决日志：只有归一化主机名与类别，没有具体 IP 与凭据。"""
    _logger.info(
        "net_guard host=%s layer=%s decision=%s ip_class=%s context=%s proxied=%s",
        host,
        layer,
        decision,
        ip_class,
        context,
        proxied,
    )


# ---------------------------------------------------------------------------
# Owner 白名单（配置文件，运行时可改，只 Owner 能改——写接口在网关侧）
# ---------------------------------------------------------------------------

_ALLOWLIST_CACHE: Dict[str, object] = {
    "path": None,
    "mtime": None,
    "size": None,
    "hosts": frozenset(),
    "entries": [],
}
_ALLOWLIST_LOCK = threading.Lock()


def allowlist_path() -> str:
    """Owner 白名单文件路径（数据目录；环境变量只覆盖路径）。

    优先级：``LIUHAO_NET_GUARD_ALLOWLIST_FILE`` > ``LIUHAO_DATA_DIR`` >
    仓库 ``data/``。与账号设置文件同一约定（``resolve_store_path``）。
    """
    from ..common.data_paths import repo_root, resolve_store_path

    return resolve_store_path(
        ALLOWLIST_FILENAME,
        repo_root() / "data" / ALLOWLIST_FILENAME,
        env_var=ALLOWLIST_FILE_ENV,
    )


def validate_allowlist_entry(entry: object) -> Tuple[Optional[Dict[str, Optional[str]]], Optional[str]]:
    """校验一条 Owner 白名单条目；合法返回 ``(规范化条目, None)``。

    规则（设计稿 §4.2）：``host`` 必填，只能是单个主机名（小写、无 scheme、
    无路径、无端口、无 ``*`` 通配符、无 IP 字面量、无 ``.example.com``
    后缀写法）；``label``/``reason`` 必填；``expires_at`` 可空（``YYYY-MM-DD``
    或 ISO 时间）。返回的条目只含 ``host``/``label``/``reason``/
    ``expires_at`` 四个字段。
    """
    if not isinstance(entry, dict):
        return None, "条目必须是对象"
    raw_host = entry.get("host")
    if not isinstance(raw_host, str) or not raw_host.strip():
        return None, "host 必填"
    host = _normalize_host(raw_host)
    if (
        "://" in raw_host
        or "/" in raw_host
        or ":" in raw_host
        or "*" in raw_host
        or host.startswith(".")
        or ".." in host
        or " " in host
    ):
        return None, "host 只能是单个主机名，不能带 scheme、路径、端口、通配符或后缀写法"
    if host in ("localhost",) or host.endswith(".localhost"):
        return None, "localhost 不需要也不允许加入白名单"
    if _is_ip_literal(host):
        return None, "host 不能是 IP 地址"
    if host in TRUSTED_VENDOR_HOSTS:
        return None, "该域名已在内置可信厂商列表里，无需添加"
    labels = host.split(".")
    if len(labels) < 2 or any(
        not label or not all(ch.isalnum() or ch == "-" for ch in label) or label.startswith("-") or label.endswith("-")
        for label in labels
    ):
        return None, "host 不是合法主机名"
    label = entry.get("label")
    reason = entry.get("reason")
    if not isinstance(label, str) or not label.strip():
        return None, "label 必填（写清这是什么端点）"
    if not isinstance(reason, str) or not reason.strip():
        return None, "reason 必填（写清为什么要加）"
    expires_at = entry.get("expires_at")
    if expires_at is not None:
        if not isinstance(expires_at, str) or not expires_at.strip():
            return None, "expires_at 须为空或日期"
        try:
            datetime.fromisoformat(expires_at.strip().replace("Z", "+00:00"))
        except ValueError:
            try:
                date.fromisoformat(expires_at.strip())
            except ValueError:
                return None, "expires_at 不是合法日期"
    return (
        {
            "host": host,
            "label": label.strip(),
            "reason": reason.strip(),
            "expires_at": (expires_at.strip() if isinstance(expires_at, str) else None),
        },
        None,
    )


def _entry_expired(entry: Dict[str, Optional[str]], *, now: Optional[datetime] = None) -> bool:
    raw = entry.get("expires_at")
    if not raw:
        return False
    try:
        moment = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        try:
            moment = datetime.combine(date.fromisoformat(str(raw)), datetime.min.time())
        except ValueError:
            return True  # 解析不出的过期时间按已失效处理
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return moment <= (now or datetime.now().astimezone())


def _load_owner_allowlist_uncached(path: str) -> Tuple[Set[str], List[Dict[str, Optional[str]]]]:
    """读一次白名单文件并校验；文件缺失/损坏一律按空名单 fail-closed。"""
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return set(), []
    except OSError as exc:
        _logger.error("net_guard allowlist unreadable path=%s error=%s", path, exc)
        return set(), []
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        _logger.error(
            "net_guard allowlist corrupt path=%s error=%s; running with empty allowlist",
            path,
            exc,
        )
        return set(), []
    entries = payload.get("entries") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        _logger.error(
            "net_guard allowlist corrupt path=%s error=%s; running with empty allowlist",
            path,
            "entries is not a list",
        )
        return set(), []
    hosts: Set[str] = set()
    valid: List[Dict[str, Optional[str]]] = []
    for item in entries[:ALLOWLIST_MAX_ENTRIES]:
        normalized, error = validate_allowlist_entry(item)
        if error is not None or normalized is None:
            _logger.warning("net_guard allowlist entry skipped error=%s", error)
            continue
        if _entry_expired(normalized):
            continue
        hosts.add(normalized["host"])
        valid.append(normalized)
    return hosts, valid


def load_owner_allowlist() -> Tuple[Set[str], List[Dict[str, Optional[str]]]]:
    """Owner 白名单（mtime 缓存；文件改动后下次请求即生效，不用重启）。

    返回 ``(生效主机名集合, 规范化条目列表)``。文件损坏时记错并返回空集。
    """
    path = allowlist_path()
    try:
        stat = Path(path).stat()
        mtime, size = stat.st_mtime_ns, stat.st_size
    except OSError:
        mtime, size = None, None
    with _ALLOWLIST_LOCK:
        if (
            _ALLOWLIST_CACHE.get("path") == path
            and _ALLOWLIST_CACHE.get("mtime") == mtime
            and _ALLOWLIST_CACHE.get("size") == size
        ):
            return set(_ALLOWLIST_CACHE["hosts"]), list(_ALLOWLIST_CACHE["entries"])  # type: ignore[arg-type]
        hosts, entries = _load_owner_allowlist_uncached(path)
        _ALLOWLIST_CACHE.update(
            {"path": path, "mtime": mtime, "size": size, "hosts": frozenset(hosts), "entries": entries}
        )
        return set(hosts), list(entries)


def refresh_owner_allowlist() -> None:
    """写接口保存后主动刷新缓存（下次请求即用新名单）。"""
    with _ALLOWLIST_LOCK:
        _ALLOWLIST_CACHE.update({"path": None, "mtime": None, "size": None, "hosts": frozenset(), "entries": []})


def get_owner_allowlist_hosts() -> Set[str]:
    """当前生效的 Owner 白名单主机名集合（供调用方与诊断用）。"""
    hosts, _ = load_owner_allowlist()
    return hosts


def is_trusted_host(host: Optional[str], *, extra_hosts: Optional[Collection[str]] = None) -> bool:
    """第 1 层身份：精确主机名是否在可信名单里（内置厂商 + Owner 白名单）。

    ``extra_hosts`` 是调用方本次传入的补充名单（如测试）；不做后缀匹配，
    不做子串匹配。
    """
    normalized = _normalize_host(host)
    if not normalized:
        return False
    if normalized in TRUSTED_VENDOR_HOSTS:
        return True
    if extra_hosts:
        if any(_normalize_host(item) == normalized for item in extra_hosts):
            return True
    return normalized in get_owner_allowlist_hosts()


def request_uses_proxy(
    url: str, *, explicit_proxy: Optional[str] = None, trust_env: bool = True
) -> bool:
    """按 HTTP 客户端实际会用的代理配置，判断本次请求是否真的走代理。

    调用方必须把自己真实的传输配置传进来（显式 ``proxy`` / 是否读环境变量），
    本函数只做判断，不猜配置：

    * 显式代理 → 走代理（回环与本机地址除外，代理绕过它们没有意义）；
    * 否则读环境代理（``urllib.request.getproxies()``，与 ``web_research`` 同款
      先例）：有对应 scheme 的代理且主机不在 bypass 清单里 → 走代理；
    * 否则 → 不走代理。判断不出（配置含糊）时调用方应按不走代理处理。
    """
    try:
        parsed = urlparse((url or "").strip())
        host = (parsed.hostname or "").lower()
        scheme = (parsed.scheme or "").lower()
    except ValueError:
        return False
    if not host:
        return False
    if _host_is_loopback(host):
        return False
    if explicit_proxy:
        return True
    if not trust_env:
        return False
    try:
        import urllib.request

        proxies = urllib.request.getproxies()
        proxy = proxies.get(scheme) or proxies.get("https" if scheme == "http" else "http")
        if not proxy:
            return False
        return not urllib.request.proxy_bypass_environment(host)
    except Exception:  # noqa: BLE001 - 代理判断本身异常按不走代理处理
        return False


_PIN_STATE = threading.local()
_REAL_GETADDRINFO = getattr(socket.getaddrinfo, "_liuhao_real", socket.getaddrinfo)


def _normalize(value: Optional[str]) -> str:
    return (value or "").strip().rstrip("/")


def local_ollama_urls() -> set:
    """本机 Ollama 的已知地址（含配置覆盖）。"""
    configured = (
        os.environ.get("OLLAMA_BASE_URL") or os.environ.get("OLLAMA_HOST") or ""
    ).strip()
    if configured and not configured.startswith(("http://", "https://")):
        configured = f"http://{configured}"
    urls = {"http://127.0.0.1:11434", "http://localhost:11434"}
    if configured:
        urls.add(configured)
    return {_normalize(url) for url in urls}


def is_local_ollama(raw: Optional[str]) -> bool:
    """是否指向本机 Ollama（原生端口或它的 OpenAI 兼容 ``/v1`` 入口）。"""
    value = _normalize(raw)
    if value in local_ollama_urls():
        return True
    return value.endswith("/v1") and value[: -len("/v1")] in local_ollama_urls()


def _coerce_address(address):
    """IPv4-mapped IPv6（``::ffff:127.0.0.1``）按 IPv4 判定。"""
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        return address.ipv4_mapped
    return address


def _host_is_loopback(host: Optional[str]) -> bool:
    if not host:
        return False
    value = host.strip().strip("[]").lower().rstrip(".")
    if value == "localhost":
        return True
    try:
        return _coerce_address(ipaddress.ip_address(value)).is_loopback
    except ValueError:
        return False


def is_loopback_target(raw: Optional[str]) -> bool:
    """``raw`` 的主机是否为本机回环（不限制端口）。

    只用于"provider=ollama + 带 Key + http"这一种合法组合：Ollama 可以监听
    任意自定义端口，不能因为端口不在白名单就判定为 SSRF 攻击。
    """
    try:
        return _host_is_loopback(urlparse((raw or "").strip()).hostname)
    except ValueError:
        return False


def is_local_inference_port(raw: Optional[str]) -> bool:
    """回环主机 + 已知本地推理端口（Ollama/LM Studio/vLLM/llama.cpp）。"""
    parsed = urlparse((raw or "").strip())
    if not _host_is_loopback(parsed.hostname):
        return False
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        return False
    return port in LOCAL_INFERENCE_PORTS


def is_local_inference_target(raw: Optional[str]) -> bool:
    """``raw`` 是否属于允许回环的本地推理目标。

    覆盖：Ollama 已知地址（含 ``OLLAMA_BASE_URL``）、回环 + 已知推理端口。
    Docker API（2375）、管理面（80/443）等任意回环端口不在白名单内。
    """
    return is_local_ollama(raw) or is_local_inference_port(raw)


def is_local_service_target(raw: Optional[str]) -> bool:
    """``raw`` 是否指向本机/本局域网服务（用于**代理绕过**，不是安全校验）。

    ``localhost``、回环、``0.0.0.0``、私网/链路本地 IP 都按本地服务处理：
    这些地址走系统代理既没有意义，还会把"服务没启动"变成代理返回的 5xx。
    公网域名（哪怕解析到内网）不在此列——解析结果会变，交给
    :func:`assert_safe_runtime_target` 在每次请求前判定。
    """
    try:
        parsed = urlparse((raw or "").strip())
        host = parsed.hostname
    except ValueError:
        return False
    if not host:
        return False
    value = host.strip().strip("[]").lower().rstrip(".")
    if value == "localhost" or value.endswith(".localhost"):
        return True
    try:
        address = _coerce_address(ipaddress.ip_address(value))
    except ValueError:
        return False
    return bool(
        address.is_loopback
        or address.is_unspecified
        or address.is_private
        or address.is_link_local
    )


def _pinned_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    """带线程级固定的 ``socket.getaddrinfo``。

    只改写**已校验过**的主机名；其他查询与未经校验的线程走系统解析。
    """
    pins = getattr(_PIN_STATE, "pins", None)
    if pins and host is not None:
        entries = pins.get(str(host).lower())
        if entries:
            results = []
            for address_family, address_text in entries:
                if family not in (0, socket.AF_UNSPEC, address_family):
                    continue
                if address_family == socket.AF_INET6:
                    sockaddr = (address_text, port, 0, 0)
                else:
                    sockaddr = (address_text, port)
                results.append(
                    (
                        address_family,
                        type or socket.SOCK_STREAM,
                        proto or socket.IPPROTO_TCP,
                        "",
                        sockaddr,
                    )
                )
            if results:
                return results
    return _REAL_GETADDRINFO(host, port, family, type, proto, flags)


_pinned_getaddrinfo._liuhao_real = _REAL_GETADDRINFO
_pinned_getaddrinfo._liuhao_pinned = True
if not getattr(socket.getaddrinfo, "_liuhao_pinned", False):
    socket.getaddrinfo = _pinned_getaddrinfo


def _pin_resolution(host: str, addresses) -> None:
    """把本次校验过的地址固定到当前线程（覆盖该主机上次的固定）。"""
    entries = []
    seen = set()
    for address in addresses:
        address_family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
        key = (address_family, str(address))
        if key not in seen:
            seen.add(key)
            entries.append(key)
    current = dict(getattr(_PIN_STATE, "pins", {}) or {})
    current[str(host).lower()] = entries
    _PIN_STATE.pins = current


def assert_safe_runtime_target(
    base_url: Optional[str],
    *,
    context: str = "模型服务",
    allow_private_service: bool = False,
    allow_loopback: bool = False,
    has_credential: bool = False,
    allowlist: Optional[Collection[str]] = None,
    proxied: Optional[bool] = None,
) -> None:
    """每次真实请求前校验 ``base_url``；不安全时抛 ``RuntimeTargetRejected``。

    三层顺序执行，任一层拒绝即停（见模块注释）：

    * 第 0 层形状：``http``/``https``、无 URL 内嵌凭据、带 Key 时 ``https``
      （http 仅限本机 Ollama/本地推理端口）；
    * 第 1 层身份：``allowlist``（调用方补充）+ 内置可信厂商 + Owner 白名单；
    * 第 2 层地址：A 类永远拦；B 类只在第 1 层通过时放行（记警告日志）；
      C 类正常放行。多地址混合时任一 A 类即整次拒绝。
    * 第 3 层传输：``proxied=True`` 且主机是域名时跳过第 2 层（本地 DNS
      答案不是连接目标）；IP 字面量主机永远走第 2 层；``proxied=None``
      按不走代理处理（fail-closed）。

    校验通过后解析结果会被线程级固定，后续同主机的 ``getaddrinfo`` 不再
    重新查询，消除 DNS rebinding 的 TOCTOU 窗口。

    ``allow_private_service=True`` 只给 Ollama provider 用：它允许
    ``OLLAMA_BASE_URL`` 指向的**这台机器运营者自己配置的** Ollama 服务
    （可能是局域网里的另一台机器）。本轮收窄：只豁免 RFC1918 私网地址，
    链路本地/组播/未指定地址不在此例外内。其他 provider 不得借它打开私网。
    """
    value = (base_url or "").strip()
    if not value:
        return
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https"):
        raise RuntimeTargetRejected(
            f"{context}地址只支持 http/https，已按安全策略拒绝。",
            kind="scheme",
        )
    if parsed.username or parsed.password:
        raise RuntimeTargetRejected(
            f"{context}地址不能包含用户名或密码，请把凭据单独填在 Key 里。",
            kind="credential",
        )
    if (
        has_credential
        and parsed.scheme == "http"
        and not (
            is_local_inference_port(value)
            or (allow_loopback and is_loopback_target(value))
        )
    ):
        raise RuntimeTargetRejected(
            f"{context}带 Key 时必须使用 https；http 明文只允许本机 "
            "Ollama/本地推理端口（11434/1234/8000/8080）。",
            kind="key_http",
        )
    host = parsed.hostname
    if not host:
        raise RuntimeTargetRejected(
            f"{context}地址缺少合法主机名，已拒绝请求。",
            kind="hostname",
        )
    normalized_host = _normalize_host(host)
    trusted = is_trusted_host(normalized_host, extra_hosts=allowlist)
    host_is_literal = _is_ip_literal(normalized_host)
    if proxied is True and not host_is_literal:
        # 第 3 层：走代理时本地 DNS 答案不是连接目标，跳过第 2 层。
        _log_decision(
            host=normalized_host,
            layer=3,
            decision="skip_dns",
            ip_class="-",
            context=context,
            proxied=True,
        )
        return
    skip_private = allow_private_service and is_local_ollama(value)
    loopback_permitted = allow_loopback or is_local_inference_target(value)
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, ValueError):
        raise RuntimeTargetRejected(
            f"{context}地址当前无法解析，已停止请求；请检查主机名或网络。",
            kind="dns_failed",
        ) from None
    validated = []
    worst_class = "C"
    worst_segment = "global"
    for info in infos:
        try:
            address = _coerce_address(ipaddress.ip_address(info[4][0]))
        except ValueError:
            raise RuntimeTargetRejected(
                f"{context}地址解析结果异常，已停止请求。",
                kind="address_invalid",
            ) from None
        ip_class, segment = _classify_address(address)
        if address.is_loopback:
            if not loopback_permitted:
                _log_decision(
                    host=normalized_host,
                    layer=2,
                    decision="deny",
                    ip_class="A-loopback",
                    context=context,
                    proxied=proxied,
                )
                raise RuntimeTargetRejected(
                    f"{context}地址指向本机回环，已按 SSRF 策略拒绝；"
                    "只有 Ollama/本地推理端口允许回环。",
                    kind="address_class_a",
                )
            validated.append(address)
            continue
        if ip_class == "A":
            if skip_private and any(
                address.version == net.version and address in net
                for net in _RFC1918_NETWORKS
            ):
                # 运营者已配置的 LAN Ollama（窄口子，见模块注释）。
                validated.append(address)
                worst_class, worst_segment = "A", f"rfc1918-allowed:{segment}"
                continue
            _log_decision(
                host=normalized_host,
                layer=2,
                decision="deny",
                ip_class=f"A-{segment}",
                context=context,
                proxied=proxied,
            )
            raise RuntimeTargetRejected(
                f"{context}地址指向内网/本机，已按安全策略拒绝。",
                kind="address_class_a",
            )
        if ip_class == "B":
            if skip_private:
                validated.append(address)
                worst_class, worst_segment = "B", segment
                continue
            if not trusted:
                _log_decision(
                    host=normalized_host,
                    layer=2,
                    decision="deny",
                    ip_class=f"B-{segment}",
                    context=context,
                    proxied=proxied,
                )
                raise RuntimeTargetRejected(
                    f"{context}地址解析到了运营商保留段，且域名不在可信名单里；"
                    "Owner 可在设置里加白名单，或直接手填模型名。",
                    kind="address_class_b_untrusted",
                )
            _logger.warning(
                "net_guard host=%s layer=2 decision=allow ip_class=B-%s "
                "context=%s proxied=%s trusted_vendor_or_allowlist",
                normalized_host,
                segment,
                context,
                proxied,
            )
            validated.append(address)
            worst_class, worst_segment = "B", segment
            continue
        validated.append(address)
    _pin_resolution(host, validated)
    _log_decision(
        host=normalized_host,
        layer=2,
        decision="allow",
        ip_class=f"{worst_class}-{worst_segment}" if worst_class != "C" else "C-global",
        context=context,
        proxied=proxied,
    )


def resolve_connection_target(
    base_url: Optional[str],
    *,
    context: str = "模型服务",
    allow_private_service: bool = False,
    allow_loopback: bool = False,
    has_credential: bool = False,
    allowlist: Optional[Collection[str]] = None,
    proxied: Optional[bool] = None,
) -> list:
    """为实际建连取回**已校验**的地址列表；拿不到就拒绝（fail-closed）。

    供 HTTP 传输层（见 ``providers._guarded_transport_cls``）在建连前调用，
    让"守卫钉住/校验过的解析结果"成为唯一建连依据，而不是依赖 SDK 建连时
    恰好再次命中 ``socket.getaddrinfo`` 钉住：

    * 当前线程已钉住该主机 → 直接返回钉住条目（与 getaddrinfo 钉住同源，
      钉住期间不重新查询 DNS，同名漂移/校验后翻转都动不了连接目标）；
    * 未钉住 → 走 :func:`assert_safe_runtime_target` 同一套策略校验并钉住
      后返回。

    走代理时（``proxied=True`` 且主机是域名）：本地 DNS 不是连接目标，
    返回空列表——调用方保留主机名交由代理转发，不做 IP 改写。
    IP 字面量主机即使走代理也照常校验并返回钉住地址。

    返回条目形如 ``[(AF_INET, "1.2.3.4"), ...]``，顺序与解析结果一致，调用
    方按序逐个尝试（与 ``socket.create_connection`` 相同的回退语义）。校验
    不通过（解析失败/私网/回环未放行/带 Key http）时抛
    :class:`RuntimeTargetRejected`，调用方必须中止连接，不得退回自行解析。
    """
    value = (base_url or "").strip()
    if not value:
        return []
    parsed = urlparse(value)
    host = parsed.hostname
    if not host:
        raise RuntimeTargetRejected(
            f"{context}地址缺少合法主机名，已拒绝请求。",
            kind="hostname",
        )
    if proxied is True and not _is_ip_literal(_normalize_host(host)):
        # 走代理：先过第 0 + 第 1 层（域名身份与形状），第 2 层跳过。
        assert_safe_runtime_target(
            value,
            context=context,
            allow_private_service=allow_private_service,
            allow_loopback=allow_loopback,
            has_credential=has_credential,
            allowlist=allowlist,
            proxied=True,
        )
        return []
    pins = getattr(_PIN_STATE, "pins", None) or {}
    entries = pins.get(str(host).lower())
    if entries:
        return list(entries)
    assert_safe_runtime_target(
        value,
        context=context,
        allow_private_service=allow_private_service,
        allow_loopback=allow_loopback,
        has_credential=has_credential,
        allowlist=allowlist,
        proxied=proxied,
    )
    entries = (getattr(_PIN_STATE, "pins", None) or {}).get(str(host).lower())
    if not entries:
        # assert 成功后必已钉住；到不了这里，兜底按 fail-closed 处理。
        raise RuntimeTargetRejected(
            f"{context}地址未取得已校验的连接目标，已停止请求。",
            kind="address_invalid",
        )
    return list(entries)
