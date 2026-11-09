"""运行时出站目标校验（模型网关）。

保存/测试时校验只能证明"那一刻"的目标是安全的；真正的请求发生在之后，
攻击面有两种：

* 保存时是公网域名，运行时该域名 302 到 ``169.254.169.254``；
* 保存时解析到公网 IP，运行时 DNS 变成私网地址（DNS rebinding）。

因此这里提供一个**每次请求前**都可调用的校验，并配合关闭 SDK 的自动重定向：
RFC1918 私网、链路本地（含 ``169.254.169.254``）、保留/组播/未指定地址一律
拒绝；回环地址只对本机推理放行：``provider=ollama``（调用方传
``allow_loopback=True``），或落在已知本地推理端口（Ollama 11434、LM Studio
1234、vLLM 8000、llama.cpp 8080）。``127.0.0.1:2375``（Docker API）之类的
任意回环端口不再放行。``OLLAMA_BASE_URL`` 指向的远端 Ollama 只对 Ollama
provider 生效，不作为其他 provider 的例外。

DNS rebinding（TOCTOU）
----------------------
校验通过后，解析结果会按线程固定下来；同一线程里 SDK 建连时再次调用
``socket.getaddrinfo`` 只会拿到已经校验过的地址，而不是重新查询 DNS。这样
"校验时公网、建连时私网"的切换窗口被消掉。异常消息保持中性中文，不携带
上游响应体或解析到的内网 IP。
"""
from __future__ import annotations

import ipaddress
import os
import socket
import threading
from typing import Optional
from urllib.parse import urlparse


class RuntimeTargetRejected(RuntimeError):
    """目标地址违反运行时出站策略。消息可直接展示给用户。"""


_PRIVATE_IP_FLAGS = (
    "is_private",
    "is_loopback",
    "is_link_local",
    "is_reserved",
    "is_unspecified",
    "is_multicast",
)

# 已知本地推理服务的回环端口：这些端口放行，其余回环端口按 SSRF 拒绝。
LOCAL_INFERENCE_PORTS = frozenset({11434, 1234, 8000, 8080})

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
) -> None:
    """每次真实请求前校验 ``base_url``；不安全时抛 ``RuntimeTargetRejected``。

    空地址表示使用 SDK 官方默认端点，无需校验；非空地址必须：

    * 是 ``http``/``https``；
    * 带 Key 时必须是 ``https``（http 仅限本机 Ollama/本地推理端口），否则
      Bearer 明文会发往任意公网地址；
    * 不带 URL 内嵌凭据（避免把 Key 拼进 URL 被日志带走）；
    * 每次重新解析 DNS，任一结果落在私网/链路本地/保留段即拒绝；
    * 回环只在 ``allow_loopback=True``（provider=ollama）或
      :func:`is_local_inference_target` 命中时放行；其余回环端口拒绝
      —— 防止网关被用来打本机管理面 / Docker 2375。

    校验通过后解析结果会被线程级固定，后续同主机的 ``getaddrinfo`` 不再
    重新查询，消除 DNS rebinding 的 TOCTOU 窗口。

    ``allow_private_service=True`` 只给 Ollama provider 用：它允许
    ``OLLAMA_BASE_URL`` 指向的**这台机器运营者自己配置的** Ollama 服务
    （可能是局域网里的另一台机器）。其他 provider 不得借它打开私网。
    """
    value = (base_url or "").strip()
    if not value:
        return
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https"):
        raise RuntimeTargetRejected(
            f"{context}地址只支持 http/https，已按安全策略拒绝。"
        )
    if parsed.username or parsed.password:
        raise RuntimeTargetRejected(
            f"{context}地址不能包含用户名或密码，请把凭据单独填在 Key 里。"
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
            "Ollama/本地推理端口（11434/1234/8000/8080）。"
        )
    host = parsed.hostname
    if not host:
        raise RuntimeTargetRejected(f"{context}地址缺少合法主机名，已拒绝请求。")
    skip_private = allow_private_service and is_local_ollama(value)
    loopback_permitted = allow_loopback or is_local_inference_target(value)
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, ValueError):
        raise RuntimeTargetRejected(
            f"{context}地址当前无法解析，已停止请求；请检查主机名或网络。"
        ) from None
    validated = []
    for info in infos:
        try:
            address = _coerce_address(ipaddress.ip_address(info[4][0]))
        except ValueError:
            raise RuntimeTargetRejected(
                f"{context}地址解析结果异常，已停止请求。"
            ) from None
        if address.is_loopback:
            if not loopback_permitted:
                raise RuntimeTargetRejected(
                    f"{context}地址指向本机回环，已按 SSRF 策略拒绝；"
                    "只有 Ollama/本地推理端口允许回环。"
                )
            validated.append(address)
            continue
        if any(getattr(address, flag) for flag in _PRIVATE_IP_FLAGS):
            if skip_private:
                validated.append(address)
                continue
            raise RuntimeTargetRejected(
                f"{context}地址指向内网/本机，已按安全策略拒绝。"
            )
        validated.append(address)
    _pin_resolution(host, validated)
