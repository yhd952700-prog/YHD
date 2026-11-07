"""运行时出站目标校验（模型网关）。

保存/测试时校验只能证明"那一刻"的目标是安全的；真正的请求发生在之后，
攻击面有两种：

* 保存时是公网域名，运行时该域名 302 到 ``169.254.169.254``；
* 保存时解析到公网 IP，运行时 DNS 变成私网地址（DNS rebinding）。

因此这里提供一个**每次请求前**都可调用的校验，并配合关闭 SDK 的自动重定向：
RFC1918 私网、链路本地（含 ``169.254.169.254``）、保留/组播/未指定地址一律
拒绝；**回环地址一律放行**（LM Studio 1234、vLLM 8000、llama.cpp 8080、
Ollama 11434 都跑在回环上，端口不设限）。``OLLAMA_BASE_URL`` 指向的远端
Ollama 只对 Ollama provider 生效，不作为其他 provider 的例外。
异常消息保持中性中文，不携带上游响应体或解析到的内网 IP。
"""
from __future__ import annotations

import ipaddress
import os
import socket
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


def assert_safe_runtime_target(
    base_url: Optional[str],
    *,
    context: str = "模型服务",
    allow_private_service: bool = False,
) -> None:
    """每次真实请求前校验 ``base_url``；不安全时抛 ``RuntimeTargetRejected``。

    空地址表示使用 SDK 官方默认端点，无需校验；非空地址必须：

    * 是 ``http``/``https``；
    * 不带 URL 内嵌凭据（避免把 Key 拼进 URL 被日志带走）；
    * 每次重新解析 DNS，任一结果落在私网/链路本地/保留段即拒绝；
    * 回环地址（``127.0.0.0/8``、``::1``、``localhost``）一律放行，端口不限
      —— 本地模型服务（LM Studio / vLLM / llama.cpp / Ollama）都住在这里。

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
    host = parsed.hostname
    if not host:
        raise RuntimeTargetRejected(f"{context}地址缺少合法主机名，已拒绝请求。")
    if allow_private_service and is_local_ollama(value):
        return
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, ValueError):
        raise RuntimeTargetRejected(
            f"{context}地址当前无法解析，已停止请求；请检查主机名或网络。"
        ) from None
    for info in infos:
        try:
            address = ipaddress.ip_address(info[4][0])
        except ValueError:
            raise RuntimeTargetRejected(
                f"{context}地址解析结果异常，已停止请求。"
            ) from None
        if address.is_loopback:
            # 回环永远放行：它到不了内网，也不经过任何代理。
            continue
        if any(getattr(address, flag) for flag in _PRIVATE_IP_FLAGS):
            raise RuntimeTargetRejected(
                f"{context}地址指向内网/本机，已按安全策略拒绝。"
            )
