"""
Main FastAPI Application for Jarvis Gateway

Provides the unified entry point for all services with:
- Lifecycle management (startup/shutdown)
- Middleware stack (CORS, compression, tracing)
- Unified exception handling
- Router inclusion
"""

import time
import logging
import os
import sys
import threading
from _thread import start_new_thread as _start_new_thread
from contextlib import asynccontextmanager

# 启动阶段计时：必须最先导入（它持有进程内最早的时钟基准 T0）。
from .startup_profile import (
    mark as _startup_mark,
    mark_ready as _startup_ready,
    begin_run as _startup_begin_run,
    report as _startup_report,
    background_started as _bg_started,
    background_step as _bg_step,
    background_finished as _bg_finished,
)

_startup_mark("src.gateway.main 开始导入（接下来是 FastAPI / security / metrics）")
# 上一行 mark 必须先于下面这些导入执行：它持有进程内最早的时钟基准 T0，
# 启动耗时统计以此为起点。因此这些导入不能移到文件顶部，逐行 E402 是有意为之。
from pathlib import Path  # noqa: E402
from typing import Any, Dict, Optional  # noqa: E402
import asyncio  # noqa: E402

from fastapi import FastAPI, Request, APIRouter, Depends  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.middleware.gzip import GZipMiddleware  # noqa: E402
from fastapi.responses import JSONResponse, PlainTextResponse  # noqa: E402
from fastapi.encoders import jsonable_encoder  # noqa: E402
from fastapi.exceptions import RequestValidationError  # noqa: E402
from starlette.exceptions import HTTPException as StarletteHTTPException  # noqa: E402

from ..security import get_jwt_handler, get_rbac_manager  # noqa: E402
from ..common.input_guard import (  # noqa: E402
    body_model_from_field,
    field_label_from_info,
    translate_validation_error,
    trim_for_error,
)
from ..brand import BRAND_LOCKUP  # noqa: E402
from ..version import APP_VERSION  # noqa: E402
# 注意：``..observability.tracing``（OpenTelemetry SDK，实测约 0.3s）**不在**
# 顶层导入 —— 追踪只服务可观测性，不属于"登录/对话/模型配置"的就绪条件。
# 它在 lifespan 末尾随后台预热一起初始化，失败会告警并出现在 /v1/startup。

# HTTP request metrics (families declared in ``src/observability/metrics.py``).
# Historically these were never fed, so ``/v1/metrics/prometheus`` exported their
# families with **zero per-request samples** -- a scrape looked instrumented while
# carrying no request data. ``logging_middleware`` still feeds them with real
# traffic; endpoint labels come from the matched route template (see
# ``record_http_request``) so dynamic ids cannot grow metric cardinality.
#
# 延迟导入：``import prometheus_client`` 会在导入期构造 PlatformCollector，
# 其中的 ``platform.system()`` 在 Windows 上要拉起一次 ``cmd /c ver``，实测
# 单独吃掉 0.5–1.1s（高负载下更久），而它只服务 /v1/metrics/prometheus。
# 改为后台预热（见 ``_warm_chat_stack``）：就绪前不付这笔成本；预热完成前
# 的极短窗口内请求不被计数，但请求处理本身完全不受影响。
_RECORD_HTTP_REQUEST = None
_get_metric_collector = None
_metrics_import_attempted = False


def _ensure_http_metrics() -> None:
    """按需导入 prometheus 指标并缓存（幂等，失败不抛）。"""
    global _RECORD_HTTP_REQUEST, _get_metric_collector, _metrics_import_attempted
    if _metrics_import_attempted:
        return
    _metrics_import_attempted = True
    try:  # pragma: no cover - depends on the optional prometheus_client package
        from ..observability.metrics import record_http_request as _recorder
    except Exception:  # metrics unavailable here; the endpoint 503s anyway
        _recorder = None
    _RECORD_HTTP_REQUEST = _recorder
    try:  # pragma: no cover - depends on the metrics module being importable
        from ..observability.metrics import get_metric_collector as _collector
    except Exception:  # metrics unavailable; middleware becomes a no-op
        _collector = None
    _get_metric_collector = _collector


logger = logging.getLogger(__name__)
_startup_mark("main 顶层依赖导入完成（fastapi / security / prometheus metrics）")

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_CONFIG_DIR = _PROJECT_ROOT / "config"

#: Comma-separated override for ``server.cors.allow_origins``.
CORS_ORIGINS_ENV = "LIUHAO_CORS_ALLOW_ORIGINS"
#: Comma-separated override for the Host allow-list (防 DNS rebinding)。
#: 不设时按"回环 + 实际绑定地址 + CORS 白名单域名"推导，绝不含 ``*``。
TRUSTED_HOSTS_ENV = "LIUHAO_TRUSTED_HOSTS"

_CORS_DEFAULT_ORIGINS = ["http://localhost:3000", "http://localhost:5173"]

#: 永远可信的本机主机名。``testserver`` 是 Starlette TestClient 的默认 Host，
#: 不是可被公网 DNS 解析的名字（DNS rebinding 用不到它），只在非生产放行。
_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")


def hostname_of(value: str) -> str:
    """把 ``Host`` / URL / origin 里的主机名规范化（去端口、去方括号、小写）。

    ``[::1]:8080`` -> ``::1``；``LocalHost:5173`` -> ``localhost``。
    """
    text = (value or "").strip().lower()
    if not text:
        return ""
    if text.startswith("["):
        end = text.find("]")
        return text[1:end] if end != -1 else text
    return text.split(":", 1)[0]


def _origin_hostname(origin: str) -> str:
    """从 Origin URL 取主机名；非法/``null`` 返回空串。"""
    from urllib.parse import urlsplit

    try:
        parsed = urlsplit((origin or "").strip())
    except ValueError:
        return ""
    if parsed.scheme not in ("http", "https"):
        return ""
    return (parsed.hostname or "").lower()


def canonical_origin(origin: str) -> str:
    """把 Origin 规范成 ``scheme://host:port``（缺省端口显式化）；非法返回空串。

    Origin 校验必须按**完整来源**精确比对：协议、主机、端口任一不同都算
    跨站。只比主机名会让 ``http://127.0.0.1:9999``、``https://127.0.0.1``
    这类"同主机"来源绕过写方法守卫，等于没有校验。
    """
    from urllib.parse import urlsplit

    raw = (origin or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
        scheme = (parsed.scheme or "").lower()
        host = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError:
        return ""
    if scheme not in ("http", "https") or not host:
        return ""
    if port is None:
        port = 443 if scheme == "https" else 80
    if ":" in host:  # IPv6 字面量在拼接时还原方括号
        host = f"[{host}]"
    return f"{scheme}://{host}:{port}"


def resolve_listen_port() -> int | None:
    """本次进程真正监听的端口（用于放行同端口回环 Origin）。

    端口来源按优先级：启动器写入的 ``LIUHAO_LISTEN_PORT``（桌面壳
    ``--port 8137`` 走这里，``PORT`` 并不存在）、单端口平台的 ``PORT``、
    以及旧的 ``LIUHAO_API_PORT``。读不到合法值就返回 ``None``：宁可不放行
    回环写请求，也不猜一个端口给所有来源开口。
    """
    for name in ("LIUHAO_LISTEN_PORT", "PORT", "LIUHAO_API_PORT"):
        raw = (os.environ.get(name) or "").strip()
        if not raw:
            continue
        try:
            port = int(raw)
        except ValueError:
            continue
        if 1 <= port <= 65535:
            return port
    return None


_LOOPBACK_ORIGIN_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _is_loopback_address(value: str) -> bool:
    raw = (value or "").strip().strip("[]").split("%", 1)[0]
    if not raw:
        return False
    try:
        import ipaddress

        return ipaddress.ip_address(raw).is_loopback
    except ValueError:
        return raw.lower() == "localhost"


def loopback_origin_allowed(
    origin: str, listen_port: int | None, client_host: str
) -> bool:
    """回环写请求放行判定：来源、端口、对端三者同时成立。

    * ``client_host`` 必须是本机回环地址（远端进程即使伪造 Origin 也不放行）；
    * Origin 必须是 http + 回环主机名；
    * Origin 端口必须等于本次实际监听端口 —— 不能因为主机是 127.0.0.1
      就对所有端口永久开口。
    """
    if not listen_port or not _is_loopback_address(client_host):
        return False
    from urllib.parse import urlsplit

    try:
        parsed = urlsplit((origin or "").strip())
        port = parsed.port
    except ValueError:
        return False
    if (parsed.scheme or "").lower() != "http":
        return False
    if (parsed.hostname or "").lower() not in _LOOPBACK_ORIGIN_HOSTS:
        return False
    return port == listen_port


def development_loopback_origins() -> list:
    """开发态**显式**列出的回环 Origin（精确到端口；生产环境不注入）。

    精确匹配意味着端口必须逐个列出：``http://127.0.0.1:9999`` 不在名单里，
    写请求就是 403。默认端口（80/443）同样要显式写进名单才会放行。
    """
    http_ports: list = []
    for candidate in (
        (os.environ.get("PORT") or "").strip(),
        (os.environ.get("LIUHAO_API_PORT") or "").strip(),
        "8080",
        "3000",
        "5173",
    ):
        if candidate and candidate not in http_ports:
            http_ports.append(candidate)
    tls_ports: list = []
    for candidate in ((os.environ.get("LIUHAO_TLS_PORT") or "").strip(), "8443"):
        if candidate and candidate not in tls_ports:
            tls_ports.append(candidate)

    origins: list = []
    for scheme, ports in (("http", http_ports), ("https", tls_ports)):
        for port in ports:
            for host in ("127.0.0.1", "localhost", "[::1]"):
                origins.append(f"{scheme}://{host}:{port}")
    return origins


def _lan_ipv4_addresses() -> list:
    """本机非回环 IPv4 地址（显式对外绑 0.0.0.0 时用于 Host 白名单）。"""
    import ipaddress
    import socket

    found: list = []
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    except OSError:
        return found
    for info in infos:
        ip = info[4][0]
        try:
            parsed = ipaddress.ip_address(ip)
        except (ValueError, IndexError):
            continue
        if parsed.is_loopback or parsed.is_link_local or parsed.is_multicast:
            continue
        if ip not in found:
            found.append(ip)
    return found


def resolve_listen_host() -> str:
    """本次进程真正绑定的地址（``__main__._run`` 会写 ``LIUHAO_LISTEN_HOST``）。"""
    return (
        os.environ.get("LIUHAO_LISTEN_HOST") or os.environ.get("HOST") or ""
    ).strip().lower()


def external_bind_enabled() -> bool:
    """是否显式绑了非回环地址（``0.0.0.0`` / ``::`` / 指定网卡 IP）。

    只有对外绑定才需要放行局域网来源的写请求：默认的 ``127.0.0.1`` 根本
    收不到局域网连接，给 LAN Origin 开口只会扩大攻击面。
    """
    listen = resolve_listen_host()
    if not listen:
        return False
    if listen in ("0.0.0.0", "::", "*"):
        return True
    return not _is_loopback_address(listen)


def _is_ip_literal(value: str) -> bool:
    """``Host`` / Origin 里的主机名是否为 IP 字面量（而非域名）。"""
    raw = (value or "").strip().strip("[]").split("%", 1)[0]
    if not raw:
        return False
    try:
        import ipaddress

        ipaddress.ip_address(raw)
        return True
    except ValueError:
        return False


def lan_write_origins(listen_port: int | None) -> frozenset:
    """对外绑定时自动放行的局域网写来源：「本机 LAN IP + 实际监听端口」。

    手机/平板访问的是 ``http://<本机局域网 IP>:<端口>``，浏览器随后带着
    同源的 ``Origin`` 发写请求（登录、发消息、改设置）。这些来源不可能
    提前写进配置白名单，而要求用户手改配置正是「手机端不可用」的根因。
    这里按**完整来源**（协议 + 主机 + 端口）精确生成，不会退化成 ``*``。

    地址枚举是尽力而为（多网卡/主机名解析不全时可能缺项）；同源比对由
    ``HostGuardMiddleware._lan_origin_allowed`` 用请求自身的 ``Host`` 兜底。
    """
    if not listen_port or not external_bind_enabled():
        return frozenset()
    origins = set()
    for ip in _lan_ipv4_addresses():
        value = canonical_origin(f"http://{ip}:{listen_port}")
        if value:
            origins.add(value)
    return frozenset(origins)


def resolve_trusted_hosts(allow_origins: list, production: bool) -> list:
    """推导 Host 白名单：回环 + 实际绑定地址 + 已配置的 CORS 域名。

    DNS rebinding 的手法是让恶意域名解析到 127.0.0.1，浏览器随后带着
    ``Host: evil.example.com`` 发请求。只允许本机名字即可挡住它；这里**不使用
    ``*``**。显式对外绑定（``HOST=0.0.0.0`` / 指定网卡）时把该地址与局域网
    IPv4 加进来，避免"能启动但所有请求 400"。``LIUHAO_TRUSTED_HOSTS`` 可覆盖
    这份推导（例如自定义反代域名）。
    """
    raw = (os.environ.get(TRUSTED_HOSTS_ENV) or "").strip()
    if raw:
        hosts = [hostname_of(item) for item in raw.split(",")]
        return [h for h in dict.fromkeys(hosts) if h]

    hosts = list(_LOOPBACK_HOSTS)
    if not production:
        hosts.append("testserver")
    for origin in allow_origins or []:
        name = _origin_hostname(str(origin))
        if name:
            hosts.append(name)

    listen = (
        os.environ.get("LIUHAO_LISTEN_HOST") or os.environ.get("HOST") or ""
    ).strip().lower()
    if listen in ("0.0.0.0", "::", "*"):
        hosts.extend(_lan_ipv4_addresses())
        try:
            import socket

            hosts.append(socket.gethostname().strip().lower())
        except OSError:  # pragma: no cover - hostname lookup is best-effort
            pass
    elif listen:
        name = hostname_of(listen)
        if name:
            hosts.append(name)
    return [h for h in dict.fromkeys(hosts) if h]


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _read_yaml(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        import yaml  # PyYAML is already a runtime dependency (logging/roster use it)

        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        return document if isinstance(document, dict) else {}
    except Exception as exc:  # noqa: BLE001 - config problem must be visible, not fatal
        logger.error("could not read %s: %s", path, exc)
        return {}


def load_runtime_config() -> Dict[str, Any]:
    """Load ``config/default.yaml`` + ``config/{APP_ENV}.yaml`` (deep merge).

    ``ConfigManager`` only reads ``configs/*.yml`` and its parser cannot express
    nested lists, so the gateway's real HTTP settings (CORS, docs) previously
    lived in the YAML files but were never read: the code fell back to
    ``allow_origins=["*"]``. This loader is the missing bridge.

    Returns the ``server``/``app`` slices the gateway actually consumes, plus a
    normalised ``environment`` string and the ``allow_origins`` override.
    """
    environment = (os.environ.get("APP_ENV") or "development").strip().lower()
    document = _read_yaml(_CONFIG_DIR / "default.yaml")
    document = _deep_merge(document, _read_yaml(_CONFIG_DIR / f"{environment}.yaml"))

    app_cfg = document.get("app") if isinstance(document.get("app"), dict) else {}
    # The env var is authoritative; the file value only fills the gap.
    environment = (
        (os.environ.get("APP_ENV") or str(app_cfg.get("environment") or "development"))
        .strip()
        .lower()
    )

    server_cfg = document.get("server") if isinstance(document.get("server"), dict) else {}
    cors_cfg = server_cfg.get("cors") if isinstance(server_cfg.get("cors"), dict) else {}
    rate_cfg = (
        server_cfg.get("rate_limit")
        if isinstance(server_cfg.get("rate_limit"), dict)
        else {}
    )
    limits_cfg = (
        server_cfg.get("limits") if isinstance(server_cfg.get("limits"), dict) else {}
    )

    raw_origins = os.environ.get(CORS_ORIGINS_ENV)
    if raw_origins:
        allow_origins = [item.strip() for item in raw_origins.split(",") if item.strip()]
    else:
        allow_origins = [
            str(item).strip()
            for item in (cors_cfg.get("allow_origins") or [])
            if str(item).strip()
        ]
    production = environment == "production"

    # 接口文档（/docs、/redoc、/openapi.json）是完整的路由与参数侦察面。
    # 默认按生产处理：没有**显式**开发开关时一律关闭。显式开关有两种：
    #   * APP_ENV=development / testing（环境变量显式声明）；
    #   * LIUHAO_HTTP_DOCS=1（单独临时打开，不改变其他运行配置）。
    # 未设置 APP_ENV 时即使 config/default.yaml 写着 development，也不开会话
    # —— 安装版与源码直跑都默认关闭。
    explicit_env = (os.environ.get("APP_ENV") or "").strip().lower()
    raw_docs = (os.environ.get("LIUHAO_HTTP_DOCS") or "").strip().lower()
    if raw_docs in {"1", "true", "yes", "on"}:
        http_docs = True
    elif raw_docs in {"0", "false", "no", "off"}:
        http_docs = False
    else:
        http_docs = explicit_env in {"development", "testing"}
    http_docs = bool(http_docs and not production)

    if not allow_origins:
        if production:
            # 生产不注入 localhost 开发来源：同源部署根本不需要 CORS，
            # 跨域部署则必须显式列名单（宁可跨域不可用，也不下发开发白名单）。
            logger.warning(
                "No server.cors.allow_origins configured in production; "
                "cross-origin consoles will be refused (same-origin is unaffected)"
            )
        else:
            logger.warning(
                "No server.cors.allow_origins configured; falling back to localhost "
                "development origins (%s)", ", ".join(_CORS_DEFAULT_ORIGINS),
            )
            allow_origins = list(_CORS_DEFAULT_ORIGINS)

    if production:
        # 生产即便配置文件里残留开发来源，也不把它们下发给浏览器。
        allow_origins = [
            origin
            for origin in allow_origins
            if _origin_hostname(origin) not in ("localhost", "127.0.0.1", "::1")
        ]

    listen = (
        os.environ.get("LIUHAO_LISTEN_HOST") or os.environ.get("HOST") or ""
    ).strip().lower()
    return {
        "environment": environment,
        "production": production,
        "http_docs": http_docs,
        # Host 头白名单（防 DNS rebinding）；不含 ``*``。
        "trusted_hosts": resolve_trusted_hosts(allow_origins, production),
        # 显式对外绑 0.0.0.0 时无法枚举"所有可达域名"，但 IP 字面量访问必须
        # 放行（容器/局域网都靠 IP 访问）。DNS rebinding 用的是域名，所以放行
        # IP 字面量不会削弱这道闸；域名仍需进白名单（LIUHAO_TRUSTED_HOSTS）。
        "host_guard_allow_ip_literals": listen in ("0.0.0.0", "::", "*"),
        "cors": {
            "enabled": bool(cors_cfg.get("enabled", True)),
            "allow_origins": allow_origins,
            "allow_methods": list(
                cors_cfg.get("allow_methods")
                or ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
            ),
            "allow_headers": list(
                cors_cfg.get("allow_headers")
                or [
                    "Authorization",
                    "Content-Type",
                    "X-Request-Id",
                    "X-Liuhao-Token",
                    # 桌面端一次性证明（取恢复码 / 重置密码各一枚）
                    "X-LiuHao-Local-Proof",
                    "X-LiuHao-Reset-Proof",
                ]
            ),
            "allow_credentials": bool(cors_cfg.get("allow_credentials", True)),
            "max_age": int(cors_cfg.get("max_age") or 600),
        },
        # 真正挂到 ASGI 的限流面。默认只覆盖 chat / goals：这两个面成本高、
        # 且是写操作集中区；其它路由要加需显式写进配置（避免"全局限流"
        # 突然把健康探针/指标采集也卡住）。
        "rate_limit": {
            "enabled": bool(rate_cfg.get("enabled", True)),
            "paths": list(rate_cfg.get("paths") or ["/v1/chat", "/v1/goals"]),
        },
        # 请求体上限与全局单 IP 限流。此前 ``server.limits.*`` 只存在于
        # YAML，没有任何代码读取；现在由 BodyLimitMiddleware /
        # GlobalRateLimitMiddleware 真正执行。
        "limits": {
            "max_body_bytes": int((limits_cfg or {}).get("max_body_bytes") or 0),
            "max_upload_bytes": int((limits_cfg or {}).get("max_upload_bytes") or 0),
            "max_backup_bytes": int((limits_cfg or {}).get("max_backup_bytes") or 0),
            "rate_limit_per_minute": int(
                (limits_cfg or {}).get("rate_limit_per_minute") or 0
            ),
        },
        "trusted_proxies": [
            str(item).strip()
            for item in (server_cfg.get("trusted_proxies") or [])
            if str(item).strip()
        ],
        # ``idle_seconds`` 由 ``__main__.py`` 映射为 uvicorn 的
        # ``timeout_keep_alive``（真实生效）；``read_seconds/write_seconds``
        # 是套接字级 deadline，uvicorn 不提供对应参数，启动时会显式告警
        # “未在应用层启用”，不允许静默装作已生效。
        "timeouts": dict(server_cfg.get("timeouts") or {}),
    }


def uvicorn_timeout_kwargs(runtime: Dict[str, Any]) -> Dict[str, Any]:
    """Map ``server.timeouts`` onto uvicorn kwargs that actually exist.

    ``idle_seconds`` is the keep-alive/idle socket timeout and maps onto
    ``timeout_keep_alive`` — implemented, not decorative. ``read_seconds`` /
    ``write_seconds`` are per-socket read/write deadlines that uvicorn does not
    expose as options; they must be configured at the reverse proxy or socket
    layer. :func:`unenforced_timeouts` names them so startup can say so out
    loud instead of leaving them looking active.
    """
    timeouts = runtime.get("timeouts") or {}
    kwargs: Dict[str, Any] = {}
    try:
        idle = float(timeouts.get("idle_seconds") or 0)
    except (TypeError, ValueError):
        idle = 0
    if idle > 0:
        kwargs["timeout_keep_alive"] = max(1, int(idle))
    return kwargs


def unenforced_timeouts(runtime: Dict[str, Any]) -> list:
    """``server.timeouts.*`` keys configured but not enforceable in-app."""
    timeouts = runtime.get("timeouts") or {}
    return [name for name in ("read_seconds", "write_seconds") if timeouts.get(name)]


#: 走"带副作用"判定的写方法。GET/HEAD/OPTIONS 不参与 Origin 拦截。
_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class OverloadGuardMiddleware:
    """满载快速失败闸（D-1）。

    慢模型调用会把 anyio 的 40 个同步 token 占满，此时新来的同步请求只能无限
    排队：客户端看到的是"卡住"，而不是"忙"。本中间件在请求进入业务线程池
    **之前**计数，超过 ``max_in_flight`` 立刻返回 503 + 中文原因 + Retry-After，
    让调用方（控制台/探针/客户端）在一秒内拿到确定答复，而不是陪跑几十分钟。

    探针（``/v1/health``、``/v1/ready``、``/v1/metrics``、``/v1/metrics/prometheus``）
    永不参与计数：它们要么是纯内存路径，要么在独立探针线程池里跑并自带超时，
    满载时仍能如实回答"服务还活着"。
    """

    EXEMPT_PATHS = frozenset(
        {"/v1/health", "/v1/ready", "/v1/metrics", "/v1/metrics/prometheus"}
    )

    def __init__(self, app, max_in_flight: int = 150):
        self.app = app
        self.max_in_flight = max(1, int(max_in_flight))
        self._in_flight = 0
        self._lock = threading.Lock()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path") in self.EXEMPT_PATHS \
                or scope.get("method") == "OPTIONS":
            await self.app(scope, receive, send)
            return

        with self._lock:
            over = self._in_flight >= self.max_in_flight
            if not over:
                self._in_flight += 1
        if over:
            import json

            body = json.dumps(
                {
                    "status": "busy",
                    "status_label": "繁忙",
                    "reason": "too_many_in_flight",
                    "user_message": (
                        "系统繁忙：当前在途请求已达上限，请求未被排队，"
                        "请 1 秒后重试。服务本身仍在运行。"
                    ),
                    "max_in_flight": self.max_in_flight,
                },
                ensure_ascii=False,
            ).encode("utf-8")
            await send(
                {
                    "type": "http.response.start",
                    "status": 503,
                    "headers": [
                        (b"content-type", b"application/json; charset=utf-8"),
                        (b"retry-after", b"1"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return

        try:
            await self.app(scope, receive, send)
        finally:
            with self._lock:
                self._in_flight -= 1


#: 计入"用户请求错误率"之外的探针/运维面（``record_request(is_probe=True)``）。
#: 这些端点的 503 意思是"服务正忙于回答探针"，不是"用户请求失败"；混入
#: ``error_rate_percent`` 会造出假 CRITICAL（实测 50.0% / 25.0%）。
#: Prometheus 的 ``http_requests_total`` 仍按 endpoint 记录，探针失败不会被静音。
PROBE_ONLY_PATHS = frozenset(
    OverloadGuardMiddleware.EXEMPT_PATHS
    | {"/v1/ready/subsystems", "/v1/production/preflight"}
)


class LoopStallWatchdog:
    """事件循环停滞看门狗（D-1 取证与告警）。

    它不修复任何东西，只保证"僵死"不再无声：事件循环超过 ``threshold`` 秒
    没有心跳，就写一条 ERROR，并在前 ``max_dumps`` 次附上全线程栈
    （``faulthandler`` 从看门狗线程直接调用，不需要信号，也不会被卡住的
    事件循环阻塞）。栈写进 stderr，也就是网关日志——下次长跑再僵死时，
    日志里直接有"谁占着事件循环"。
    """

    def __init__(self, threshold: float = 15.0, max_dumps: int = 3):
        self.threshold = threshold
        self.max_dumps = max_dumps
        self._beat = time.monotonic()
        self._stop = threading.Event()
        self._thread = None
        self._task = None

    async def _heartbeat(self) -> None:
        import asyncio

        while not self._stop.is_set():
            self._beat = time.monotonic()
            await asyncio.sleep(0.5)

    def _watch(self) -> None:
        dumps = 0
        last_log = 0.0
        while not self._stop.wait(1.0):
            stalled = time.monotonic() - self._beat
            if stalled < self.threshold:
                continue
            if dumps < self.max_dumps:
                dumps += 1
                try:
                    import faulthandler
                    import sys

                    faulthandler.dump_traceback(file=sys.stderr, all_threads=True)
                except Exception:  # pragma: no cover - 诊断失败不能拖垮看门狗
                    pass
            now = time.monotonic()
            if now - last_log >= self.threshold:
                last_log = now
                logger.error(
                    "事件循环已停滞 %.1f 秒（阈值 %.0fs）：异步请求处理被"
                    "同步调用占住，见随后线程栈。",
                    stalled,
                    self.threshold,
                )

    def start(self) -> None:
        import asyncio

        self._task = asyncio.get_running_loop().create_task(self._heartbeat())
        self._thread = threading.Thread(
            target=self._watch, name="liuhao-loop-watchdog", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()


def install_nonblocking_worker_start() -> bool:
    """让 anyio 的同步工作线程"启动但不等待启动完成"（D-1 根治）。

    为什么必须这么做
    ----------------
    ``anyio`` 在**事件循环线程上**按需创建线程池工作线程，走的是
    ``threading.Thread.start()``，而其中 ``self._started.wait()`` **没有超时**。
    2026-11-07 的实测（`post30` 之后的 `rss8h`，见
    ``docs/operations/soak-report-2026-11-08.md`` §12.7）：宿主 CPU 100% 时，
    新线程在 bootstrap 阶段卡住不再推进，事件循环就**永久**停在
    ``Thread.start`` 里，``/v1/health``、``/v1/metrics``、``/v1/ready`` 全部
    停摆 967 秒以上，看门狗每 15 秒告警却无法自愈，只能重启。

    同一时刻 ``liuhao-diag`` 线程也卡在**同一行**（``ThreadingMixIn`` 也是
    每个连接 ``Thread.start``），诊断口因此失联 —— 这不是某一把业务锁，
    而是**进程级的"新线程跑不起来"**。既有线程照常运行（看门狗一直在写日志），
    所以也不是 GIL 饥饿。

    补丁做什么
    ----------
    只替换 anyio 自己的 ``WorkerThread.start``：线程照常创建、任务照常入队，
    唯独省掉"等新线程真正跑起来"这一步。这样即使线程创建停滞，最多是**触发
    它的那一个请求**拿不到结果（按满载策略由外层限流/超时收口），事件循环和
    探针接口继续工作 —— 从"整站僵死"降级为"个别变慢/503"。

    返回是否安装成功；任何失败只记日志，绝不阻断启动。
    """

    try:
        from anyio._backends import _asyncio as _anyio_asyncio
    except Exception:  # pragma: no cover - 依赖缺失时保持原行为
        logger.warning("anyio 线程池非阻塞启动补丁不可用（导入失败）", exc_info=True)
        return False

    worker_cls = getattr(_anyio_asyncio, "WorkerThread", None)
    if worker_cls is None:  # pragma: no cover - anyio 版本变化
        logger.warning("anyio WorkerThread 不存在，跳过非阻塞启动补丁")
        return False
    if getattr(worker_cls, "_liuhao_nonblocking_start", False):
        return True

    limbo_lock = getattr(threading, "_active_limbo_lock", None)
    limbo = getattr(threading, "_limbo", None)
    if limbo_lock is None or limbo is None:  # pragma: no cover - CPython 内部结构变化
        logger.warning("threading 内部结构缺失，跳过非阻塞启动补丁")
        return False

    def _start(self) -> None:  # type: ignore[no-untyped-def]
        if self._started.is_set():
            raise RuntimeError("threads can only be started once")
        with limbo_lock:
            limbo[self] = self
        try:
            _start_new_thread(self._bootstrap, ())
        except Exception:
            with limbo_lock:
                limbo.pop(self, None)
            raise

    worker_cls.start = _start
    worker_cls._liuhao_nonblocking_start = True
    logger.info("anyio 线程池已切换为“启动不阻塞事件循环”（D-1 护栏）")
    return True


async def warm_default_executor(count: int | None = None) -> int:
    """把事件循环**默认线程池**的线程在启动时就全部建好。

    为什么：``asyncio.to_thread`` / ``loop.run_in_executor(None, ...)`` 的线程也是
    "第一次 submit 才创建"，而创建发生在**事件循环线程**上，走的仍是
    ``threading.Thread.start()`` → ``self._started.wait()``。这与 §12.8 里 anyio
    的卡点是**同一个机制**：本仓库在异步端点里用 ``asyncio.to_thread`` 做文档
    解析（``src/api/routes/knowledge.py``），满载时同样可能把循环卡在建线程上。

    ``ThreadPoolExecutor`` 的线程数**不会超过** ``max_workers``：只要启动时（CPU
    空闲）把全部线程建满，运行期这条路径就再也不会新建线程。返回预建线程数；
    失败只记日志，不阻断启动。

    注意：不能只连续 submit 一组空任务 —— 空闲的 worker 会被复用
    （``_adjust_thread_count`` 的 ``_idle_semaphore``），线程数不会涨到 N。
    这里让 N 个任务都在栅栏上同时阻塞，迫使执行器真的建出 N 条线程，再一起放行。
    """
    try:
        import asyncio
        import threading

        loop = asyncio.get_running_loop()
        workers = max(1, int(count or min(32, (os.cpu_count() or 1) + 4)))
        gate = threading.Barrier(workers)

        def _hold() -> None:
            try:
                gate.wait(5.0)
            except threading.BrokenBarrierError:  # pragma: no cover - 线程被系统拒绝
                pass

        await asyncio.gather(
            *[loop.run_in_executor(None, _hold) for _ in range(workers)]
        )
        executor = getattr(loop, "_default_executor", None)
        return len(executor._threads) if executor is not None else workers
    except Exception:  # pragma: no cover - 预热失败不影响服务
        logger.warning("默认线程池预热失败（不影响启动）", exc_info=True)
        return 0


class HostGuardMiddleware:
    """Host 白名单 + 写请求 Origin 校验（纵深防御，纯 ASGI）。

    1) **Host**：只放行白名单里的主机名（去端口、大小写不敏感、支持 IPv6）。
       陌生 Host 返回 400。这是 DNS rebinding 的第一道闸：恶意域名解析到
       127.0.0.1 后，浏览器发来的请求就是 ``Host: evil.example.com``。
    2) **Origin**：写方法若带 ``Origin`` 且与白名单**完整来源**（scheme +
       host + port）不完全一致，直接 403 并写安全日志。**不按主机名回落**：
       IP 字面量或同主机不同端口/协议都不是同源。不能只靠 CORS —— CORS 只
       限制"浏览器能不能读到响应"，一个被放行的简单请求其副作用在服务端
       已经发生（CSRF 的经典盲区）。
    """

    def __init__(
        self,
        app,
        allowed_hosts,
        allowed_origins=(),
        allow_ip_literals=False,
        loopback_port=None,
        lan_bind=False,
        lan_port=None,
    ):
        self.app = app
        self.allowed_hosts = {str(h).lower() for h in allowed_hosts if h}
        # 只保留**完整** Origin；裸主机名/IP/``*`` 不会被当成来源白名单。
        self.allowed_origins = set()
        for item in allowed_origins or ():
            value = canonical_origin(str(item))
            if value:
                self.allowed_origins.add(value)
        self.allow_ip_literals = bool(allow_ip_literals)
        # 本次实际监听端口：仅用于"本机回环 + 同端口"这一条放行依据。
        self.loopback_port = loopback_port
        # 对外绑定时的 LAN 来源：本机 IP + 实际端口（精确来源，不含 ``*``）。
        self.lan_bind = bool(lan_bind)
        self.lan_port = lan_port if lan_bind else None
        self.lan_origins = lan_write_origins(self.lan_port)

    def _host_allowed(self, host: str) -> bool:
        if host in self.allowed_hosts:
            return True
        if not self.allow_ip_literals:
            return False
        try:
            import ipaddress

            ipaddress.ip_address(host)
            return True
        except ValueError:
            return False

    def _origin_allowed(self, origin: str, client_host: str = "") -> bool:
        """完整来源精确匹配；本机回环同端口来源额外放行。"""
        value = canonical_origin(origin)
        if value and value in self.allowed_origins:
            return True
        return loopback_origin_allowed(origin, self.loopback_port, client_host)

    def _lan_origin_allowed(self, origin: str, raw_host: str) -> bool:
        """对外绑定时，放行「本机 LAN IP + 实际端口」的同源写请求。

        两条依据，都是**完整来源**精确比对：

        1. 命中启动时枚举的本机局域网地址（``lan_write_origins``）；
        2. Origin 的主机与本次请求的 ``Host`` 完全一致、且是 IP 字面量、端口
           等于实际监听端口 —— 覆盖主机名解析枚举不到的多网卡/虚拟网卡。

        浏览器无法凭空伪造 ``Origin``（它就等于页面来源）；跨站页面带来的
        ``Origin`` 与 ``Host`` 必然不同，所以第 2 条不会把 ``*`` 放开。
        """
        if not self.lan_bind or not self.lan_port:
            return False
        value = canonical_origin(origin)
        if not value:
            return False
        if value in self.lan_origins:
            return True
        host = hostname_of(raw_host)
        if not host or not _is_ip_literal(host):
            return False
        if _origin_hostname(origin) != host:
            return False
        return value == canonical_origin(f"http://{host}:{self.lan_port}")

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        from starlette.datastructures import Headers

        headers = Headers(scope=scope)
        raw_host = headers.get("host", "")
        host = hostname_of(raw_host)
        client = scope.get("client") or ("", 0)
        if not host or not self._host_allowed(host):
            logger.warning(
                "SECURITY_REJECTED reason=untrusted_host method=%s path=%s "
                "host=%r client=%s",
                scope.get("method"), scope.get("path"), raw_host, client[0],
            )
            response = PlainTextResponse("Invalid host header", status_code=400)
            await response(scope, receive, send)
            return

        method = (scope.get("method") or "GET").upper()
        origin = headers.get("origin")
        if (
            method in _WRITE_METHODS
            and origin
            and not (
                self._origin_allowed(origin, client[0])
                or self._lan_origin_allowed(origin, raw_host)
            )
        ):
            logger.warning(
                "SECURITY_REJECTED reason=cross_site_write method=%s path=%s "
                "origin=%r client=%s",
                method, scope.get("path"), origin, client[0],
            )
            response = JSONResponse(
                status_code=403,
                content={
                    "error": "origin_not_allowed",
                    "detail": "跨站写请求被拒绝：Origin 不在白名单内。",
                },
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


# ==================== Routers ====================

# Health router —— 用 health.py 的真实实现，**不要**在这里另建一个同名 router。
#
# 这里曾经声明 ``health_router = APIRouter(prefix="/v1")`` 并在 lifespan 里挂两个
# trivial 端点（``{"status": "ok"}`` / ``{"status": "ready"}``）。由于名字相同，
# 看起来像"健康检查已接入"，实际上：
#   * health.py 的 router 从未被 include —— 整份真实实现（/v1/ready 的五项依赖
#     检查：api_key_manager / jwt_handler / rbac_manager / encryption_manager /
#     rate_limiter）是彻底不可达的死代码；
#   * 监控拿到的是一个无论子系统死活都回答 "ready" 的探针。
# 即"健康检查说谎"那一类缺陷的完整版。现在直接复用权威实现。
from .health import health_router  # noqa: E402

# API router (will include all service endpoints)
api_router = APIRouter(prefix="/v1", tags=["api"])


# ==================== Startup/Shutdown ====================


def _enforce_startup_data_gate() -> None:
    """启动门禁：数据目录由更高 schema 版本写入时拒绝启动。

    为什么在 lifespan 的第一个动作：任何安全管理器、内核生命周期与后台
    预热都还没开始，拒绝时进程直接退出，不会留下半套初始化。判断本身
    兼容优先（标记缺失/损坏一律放行），见 ``src/upgrade/startup_guard.py``。
    """
    from ..logging_config import resolve_data_dir
    from ..upgrade.startup_guard import StartupDataBlocked, check_startup_data

    gate = check_startup_data(resolve_data_dir())
    if gate.message:
        print(gate.message, file=sys.stderr)
        logger.warning(gate.message)
    if not gate.ok:
        raise StartupDataBlocked(gate)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle management for the gateway."""

    # 先把"能不能读这份数据"定下来，再谈初始化。
    _enforce_startup_data_gate()

    # 有界清扫上一次进程被强杀 / 断电后留在临时目录里的自产产物（升级暂存、
    # 自检沙箱、恢复 staging、提醒脚本）。只认本程序命名模式 + pid 已退出 +
    # 超过保留期；失败只记 debug，绝不阻断启动。
    try:
        from ..common.temp_sweep import run_startup_temp_sweep

        run_startup_temp_sweep()
    except Exception:  # noqa: BLE001 - 清扫失败不影响启动
        logger.debug("启动临时产物清扫跳过", exc_info=True)

    # Startup
    _startup_begin_run()
    logger.info("Starting Jarvis Gateway...")

    # D-1 护栏：必须在任何请求进入线程池之前装好。它只影响 anyio 的工作线程
    # 启动方式（不再在事件循环里无限等新线程跑起来），失败也不阻断启动。
    install_nonblocking_worker_start()
    # 探针线程池同样在启动时就建好全部线程，避免"第一个 /v1/health 请求"
    # 在事件循环里现场 Thread.start（与上面同一个卡点）。
    try:
        from .health import warm_probe_pool

        warm_probe_pool()
    except Exception:  # pragma: no cover - 预热失败不影响服务
        logger.warning("探针线程池预热调用失败（忽略）", exc_info=True)
    # 默认线程池同理：asyncio.to_thread（知识库文档解析）的线程若在满载时
    # 现场创建，会走同一个卡点。启动时建满，运行期就不再新建。
    await warm_default_executor()
    _startup_mark("lifespan 启动开始")
    # 每次启动都是一份新的降级清单：上一次进程的失败不能污染 /v1/startup。
    try:
        from ..observability.boot_report import reset_degraded as _reset_degraded

        _reset_degraded()
    except Exception:  # noqa: BLE001
        pass

    # Initialize security managers
    from ..security.api_keys import get_api_key_manager as _get_key_mgr
    key_mgr = _get_key_mgr()
    logger.info(f"API Key Manager initialized: {key_mgr.get_key_stats()['total']} keys")

    get_jwt_handler()
    logger.info("JWT Handler initialized")

    rbac_mgr = get_rbac_manager()
    logger.info(f"RBAC Manager initialized: {len(rbac_mgr._roles)} roles")

    _startup_mark("密钥 / JWT / RBAC 初始化完成")

    # 主密钥文件 ACL 强校验（启动期，失败必须可见）：老装机留下的 master.key
    # 可能仍是继承 ACL（Users / CodexSandboxUsers 可读）。这里在密钥初始化之后
    # 做一次读检查，需要时自愈；失败登记 degraded，/v1/startup 与
    # evt=startup.degraded 同源可见，绝不静默 pass。
    try:
        from ..observability.boot_report import add_degraded as _add_degraded
        from ..security.encryption import ensure_master_key_permissions

        _acl_ok, _acl_state, _acl_path = ensure_master_key_permissions()
        if _acl_ok:
            if _acl_state == "tightened":
                logger.warning(
                    "主密钥文件 ACL 过宽，已自动收紧为仅当前用户：%s", _acl_path
                )
            else:
                logger.info("主密钥文件 ACL 校验通过（%s）：%s", _acl_state, _acl_path)
        else:
            _add_degraded(
                "master_key_acl",
                str(_acl_path or ""),
                f"master.key ACL 收紧后复核仍不通过（{_acl_state}），"
                "其他主体仍可读取",
                "degraded: 检查数据目录/文件是否只读或 ACL 不可写，"
                "以当前用户身份修复后重启；详见 /v1/startup.degraded",
            )
            logger.error(
                "MASTER KEY ACL CHECK FAILED -- %s 仍可被其他主体读取（%s）",
                _acl_path,
                _acl_state,
            )
    except Exception as _acl_exc:  # noqa: BLE001 - 校验失败必须登记，不能静默
        try:
            from ..observability.boot_report import add_degraded as _add_degraded

            _add_degraded(
                "master_key_acl",
                "",
                f"{type(_acl_exc).__name__}: {_acl_exc}",
                "degraded: 主密钥 ACL 无法校验；检查 pywin32 安装与"
                "数据目录可读性后重启",
            )
        except Exception:  # noqa: BLE001 - 登记失败也不能阻断启动
            pass
        logger.error("MASTER KEY ACL CHECK FAILED: %s", _acl_exc, exc_info=True)

    # 旧 .env 里的 AI_PROVIDER_* 配置迁移成一条可编辑模型记录（只做一次）。
    # 迁移不覆盖任何已有数据；失败只记日志，绝不阻断启动。
    try:
        from ..ai.model_config import migrate_env_config_to_store

        migrate_env_config_to_store()
    except Exception:  # noqa: BLE001 - 迁移是增强能力，不是启动前置条件
        logger.warning("legacy model config migration failed", exc_info=True)

    # UBX-005: install the production executor fence AND bind a process-wide
    # executor lease *before* any in-process kernel action runs (identity
    # seeding, kernel lifecycle init below). This is the boot wiring that makes
    # arming ``LIUHAO_EXECUTOR_FENCE=on`` survivable: without the bound lease,
    # every fenced ``@kernel_action`` raised ``PolicyDeniedError`` at boot (the
    # old "0/24 checks, RC=1" crash) because bootstrap / service actions run with
    # no executor identity bound. Installing + binding here -- ahead of the first
    # fenced call -- guarantees the lease is valid against the REAL fence object
    # and lets legitimate in-process actions pass while every call is still
    # validated (token / expiry / liveness / epoch / replay / capability scope).
    try:
        from ..kernels.execution.fence import (
            attach_default_executor_fence,
            establish_process_executor_lease,
            executor_fence_armed,
            record_default_fence_failure,
            record_default_fence_success,
        )

        _fence = attach_default_executor_fence()
        record_default_fence_success()
        logger.info(
            "Executor fence installed (backend=%s, max_executors=%s)",
            os.environ.get("LIUHAO_DISTRIBUTED_LEASE_BACKEND", "sqlite"),
            getattr(_fence.lease, "max_executors", None),
        )
        # Arm the gate only when the operator opted in (LIUHAO_EXECUTOR_FENCE=on);
        # when armed, bind one stable lease so in-process actions satisfy the
        # default-DENY gate. The gate stays un-armed (pass-through) otherwise, so
        # the boot behaviour is byte-identical to before for operators who do not
        # set it.
        if executor_fence_armed():
            establish_process_executor_lease()
            logger.info(
                "Executor fence gate ARMED; process lease bound (boot survives)"
            )
    except Exception as _fence_exc:  # noqa: BLE001 - must be loud, must not crash boot
        from ..common.atomic_write import describe_storage_failure, is_storage_failure

        if is_storage_failure(_fence_exc):
            logger.error(
                "执行器围栏未能安装：%s 当前降级运行，自主动作将在无围栏模式下执行"
                "（有风险）。请清理磁盘空间或解除数据目录只读后重启网关。",
                describe_storage_failure(
                    _fence_exc, os.environ.get("LIUHAO_DATA_DIR", ".")
                ),
            )
        else:
            logger.error(
                "EXECUTOR FENCE INSTALL FAILED -- autonomous actions will run "
                "UN-FENCED (fail-open risk): %s",
                _fence_exc, exc_info=True,
            )
        try:
            from ..kernels.execution.fence import record_default_fence_failure

            record_default_fence_failure(_fence_exc)
        except Exception:  # noqa: BLE001 - recording must never mask the boot failure
            logger.error(
                "Failed to record the executor-fence install failure: %s",
                _fence_exc, exc_info=True,
            )
        _require_fence = os.environ.get(
            "LIUHAO_REQUIRE_EXECUTOR_FENCE", ""
        ).strip().lower()
        if _require_fence in ("1", "true", "yes", "on"):
            # fail-closed: refuse to serve autonomous actions un-fenced.
            raise RuntimeError(
                "EXECUTOR FENCE REQUIRED (LIUHAO_REQUIRE_EXECUTOR_FENCE=1) "
                f"but the default executor fence could not be attached: "
                f"{type(_fence_exc).__name__}: {_fence_exc}"
            ) from _fence_exc

    _startup_mark("执行器围栏安装完成")

    # Policy Controlled: surface the kernel enforcement selection at boot.
    # A mistyped selection is fail-loud (parse_spec raises, which would reject
    # every HIGH/CRITICAL action), so it must never be discovered only at the
    # first such call -- say it here, at ERROR level, first.
    from ..kernels._enforcement import describe as _enforcement_snapshot
    _enf = _enforcement_snapshot()
    if _enf["config_error"]:
        logger.error(
            "KERNEL POLICY ENFORCEMENT CONFIG ERROR -- %s=%r :: %s "
            "(HIGH/CRITICAL kernel actions will be REJECTED) policy_enforced=%s",
            _enf["env_var"], _enf["spec"], _enf["config_error"], True,
        )
    elif _enf["enabled"]:
        logger.warning(
            "Kernel policy enforcement ARMED: %s=%r -> %s policy_enforced=%s",
            _enf["env_var"], _enf["spec"], ", ".join(_enf["enforced_actions"]),
            True,
        )
    else:
        logger.info(
            "Kernel policy enforcement off (%s unset) -- record-only / L1 "
            "policy_enforced=%s",
            _enf["env_var"], False,
        )

    # Log at WARNING when nobody can approve, not INFO: uvicorn's default
    # logging config leaves non-uvicorn loggers at WARNING, so an INFO line
    # here would be invisible in exactly the deployment that needs it. "Zero
    # humans" is a legitimate state but never a quiet one -- otherwise it is
    # discovered only when a HIGH/CRITICAL action is first attempted.
    _startup_mark("策略执法快照完成")
    from ..kernels.identity import get_identity_manager, is_human_identity
    _identity_manager = get_identity_manager()
    _store = _identity_manager.describe_store()
    _humans = sorted(
        identity.principal
        for identity in _identity_manager.list_identities()
        if is_human_identity(identity)
    )
    _startup_mark("身份存储加载完成", humans=len(_humans))
    if _humans:
        logger.info(
            "Human identity store: %s @ %s -- %d registered human(s): %s",
            _store["backend"],
            _store["location"] or "(unconfigured)",
            len(_humans),
            ", ".join(_humans),
        )
    else:
        logger.warning(
            "Human identity store: %s @ %s -- 0 registered humans, so NOBODY "
            "CAN APPROVE a HIGH/CRITICAL action (fail-closed, OD-010). "
            "Register one with scripts/register_human_identity.py",
            _store["backend"],
            _store["location"] or "(unconfigured)",
        )

    # 恢复码安全收紧的启动面：
    # * 旧版明文交接文件原地转成 DPAPI 密文（失败保留原文件等下轮重试）；
    # * 复核交接文件的实际 ACL（Windows 上 chmod 不算数）；
    # * 远程恢复开关打开时必须留下明确日志。
    try:
        from . import recovery_handoff

        _handoff_migration = recovery_handoff.migrate_legacy_plaintext(_identity_manager)
        _startup_mark("恢复码交接迁移完成", legacy=_handoff_migration)
        for _handoff_problem in recovery_handoff.handoff_selfcheck():
            logger.warning("恢复码交接自检未通过：%s", _handoff_problem)
        if recovery_handoff.remote_recovery_enabled():
            logger.warning(
                "已允许非本机恢复（%s=1）：远程来源可以调用 "
                "/v1/accounts/recovery/reset。",
                recovery_handoff.REMOTE_RECOVERY_ENV,
            )
    except Exception as _handoff_exc:  # noqa: BLE001 - 启动面失败不阻断服务
        logger.error("恢复码交接启动处理失败：%s", _handoff_exc, exc_info=True)

    # 本机恢复码交接的进程级证明：DACL 只授予当前用户的命名管道（POSIX
    # 为 0600 Unix socket）。桌面端从这里申请一次性令牌，HTTP 接口只认令牌，
    # 不再把“来自 127.0.0.1”当成“本人”。启动失败不阻断服务，但自愈日志要响。
    try:
        from . import local_proof

        local_proof.start_broker()
        app.state.local_proof_broker = True
        logger.info("本机恢复证明通道已启动（%s）", local_proof.channel_name())
        if not local_proof.trusted_launch():
            logger.warning(
                "开发模式：未设置 %s=1，首次注册不要求桌面端证明，"
                "也不会校验 HTTP 对端是否与网关同用户。"
                "请从桌面壳/启动器进入生产使用。",
                local_proof.TRUSTED_LAUNCH_ENV,
            )
            # 只写日志不够：直接 `python -m src.gateway` 的人未必会去翻日志文件，
            # 而这条放宽的正是“谁能抢先注册本机唯一根账号”，必须一眼看见。
            print("!" * 62)
            print(
                f"!! 警告：开发模式（未设置 {local_proof.TRUSTED_LAUNCH_ENV}=1）。"
            )
            print("!! 首次注册不要求桌面端一次性证明，也不校验调用者与本机是否同一")
            print("!! Windows 账户 —— 本机任何进程都可能抢先注册掉第一个账号。")
            print("!! 正式使用请从贾维斯桌面窗口/启动器进入。")
            print("!" * 62)
    except Exception as _proof_exc:  # noqa: BLE001
        app.state.local_proof_broker = False
        logger.error("本机恢复证明通道启动失败：%s", _proof_exc, exc_info=True)

    # 凭据包导出同样默认只接受本机来源；开关打开时启动日志必须写清楚。
    try:
        from .account_backup import REMOTE_EXPORT_ENV, remote_export_enabled

        if remote_export_enabled():
            logger.warning(
                "已允许非本机备份导出（%s=1）：远程来源可以调用 "
                "/v1/accounts/backup/export。",
                REMOTE_EXPORT_ENV,
            )
    except Exception as _export_exc:  # noqa: BLE001 - 启动面失败不阻断服务
        logger.error("备份导出开关自检失败：%s", _export_exc, exc_info=True)

    # 0.1.0 老记忆 -> 账号记忆：启动时先做一次幂等迁移（放在身份加载之后，
    # 归属才能落到正确的根主体）。失败不阻断启动，下次访问记忆页会重试。
    try:
        from .account_memory import ensure_legacy_memory_migrated

        _legacy_memory = ensure_legacy_memory_migrated()
        _startup_mark(
            "老记忆迁移完成",
            ran=bool(_legacy_memory.get("ran")),
            entries=int(_legacy_memory.get("entries", 0) or 0),
            ttl_refreshed=int(_legacy_memory.get("ttl_refreshed", 0) or 0),
        )
    except Exception as _legacy_exc:  # noqa: BLE001 - 迁移可重试，不能阻断服务启动
        from ..common.atomic_write import describe_storage_failure, is_storage_failure

        if is_storage_failure(_legacy_exc):
            logger.error(
                "老记忆迁移未完成：%s 迁移会在下次启动时自动重试，不影响本次启动。",
                describe_storage_failure(
                    _legacy_exc, os.environ.get("LIUHAO_DATA_DIR", ".")
                ),
            )
        else:
            logger.error(
                "legacy memory migration failed: %s", _legacy_exc, exc_info=True
            )
        _startup_mark("老记忆迁移失败", error=str(_legacy_exc))

    # 注意：这里**不**再注册 /v1/health 与 /v1/ready。
    #
    # 历史上 lifespan 里注册过两个 trivial 版本
    # （``{"status": "ok"}`` / ``{"status": "ready"}``），它们把 health.py 的
    # 真实探针整个盖掉了 —— 因为 FastAPI 0.141 的 ``include_router`` 是惰性
    # 包装（``_IncludedRouter``），后注册的同路径路由会生效。后果是真实的就绪
    # 检查（api_key_manager / jwt_handler / rbac_manager / encryption_manager /
    # rate_limiter 五项）成了**永远不可达的死代码**，而所有监控看到的是一个
    # 无论子系统死活都回答 "ready" 的探针 —— 正是"健康检查说谎"那一类缺陷。
    #
    # 现在只保留 health.py 的实现：/v1/health = liveness（恒 200），
    # /v1/ready = 真实依赖检查（子系统不健康时如实返回 503 + errors）。
    # 实测健康状态下二者行为与旧实现一致（ready / 200），差别只在真出问题时。

    # Drive the 14-kernel lifecycle protocol into the real runtime.
    #
    # This is the missing driver: ``src.kernels._registry.initialize_all()`` /
    # ``shutdown_all()`` existed but were NEVER called anywhere in src/ or
    # scripts/ (verified by grep). Without this, every kernel's ``lifecycle``
    # flag stays at UNINITIALIZED forever while the kernel is actually serving
    # requests -- a lying indicator light. Now the runtime genuinely drives it.
    #
    # ``initialize_all()`` only touches instances that *already exist* (it never
    # creates one -- the registry's hard constraint). At cold start only the
    # singletons materialized above (e.g. identity) are present, so it drives
    # those to READY and honestly reports the rest as skipped_not_present /
    # no_canonical_instance. Per-kernel failures are recorded (never swallowed)
    # and surfaced below loudly.
    from ..kernels._registry import initialize_all, shutdown_all, snapshot as kernel_snapshot

    _startup_mark("内核生命周期 initialize_all 开始")
    _init_report = initialize_all()
    _startup_mark("内核生命周期 initialize_all 完成")
    _init_initialized = _init_report.get("initialized", [])
    _init_skipped = _init_report.get("skipped_not_present", [])
    _init_no_canonical = _init_report.get("no_canonical_instance", [])
    _init_errors = _init_report.get("errors", [])
    # 同一份启动报告交给 /v1/ready 的聚合检查项（kernels）。快照只收录启动时
    # 已存在的实例：之后由请求路径创建的惰性单例天然停在 UNINITIALIZED，不能
    # 当成故障，否则每次开机都会 503。这里只记录事实，不重新驱动内核。
    app.state.kernel_lifecycle_report = {
        "initialized": _init_initialized,
        "skipped_not_present": _init_skipped,
        "no_canonical_instance": _init_no_canonical,
        "errors": _init_errors,
        "snapshot": kernel_snapshot(),
    }
    if _init_errors:
        # 失败必须响：绝不静默吞异常（这是本项目反复出现的失败模式）。
        logger.error(
            "KERNEL LIFECYCLE INIT DEGRADED -- %d kernel(s) failed to "
            "initialize: %s",
            len(_init_errors),
            _init_errors,
        )
    logger.info(
        "Kernel lifecycle: initialized=%s, skipped_not_present=%s, "
        "no_canonical_instance=%s, errors=%d",
        _init_initialized, _init_skipped, _init_no_canonical, len(_init_errors),
    )
    if _init_errors:
        # 明确上报降级：启动仍可继续，但把"部分内核未就绪"这个事实高亮出来，
        # 绝不假装一切正常。运维 / 主权主体可在 /v1/kernels 看到真实状态。
        logger.warning(
            "Gateway started in DEGRADED kernel lifecycle state: %d kernel(s) "
            "did not reach READY. See KERNEL LIFECYCLE INIT DEGRADED above.",
            len(_init_errors),
        )

    logger.info("Gateway startup complete")

    # NOTE: the executor fence is installed and the process lease is bound
    # EARLIER in this lifespan (before any in-process kernel action runs), so
    # arming LIUHAO_EXECUTOR_FENCE=on is survivable at boot. A second install
    # here would replace the fence object and invalidate the already-bound lease,
    # so the late install block was intentionally removed. See the UBX-005 block
    # near the top of this lifespan.

    # OB-closed-loop: wire REAL execution-failure events to the REAL alert
    # subsystem. Without this, execution failures never produced alerts (the
    # alert engine existed but was never driven). Idempotent and fail-safe;
    # a failure must be loud but must not crash boot.
    try:
        from ..observability.alerts.execution_binding import (
            install_execution_alert_binding,
        )

        install_execution_alert_binding()
        logger.info(
            "Execution->alert binding installed (real dispatch to store + console)"
        )
    except Exception as _alert_exc:  # noqa: BLE001 - loud, never crash boot
        logger.error(
            "EXECUTION->ALERT BINDING FAILED -- execution failures will NOT "
            "produce alerts (observability closed loop broken): %s",
            _alert_exc,
            exc_info=True,
        )

    _startup_mark("执行→告警绑定完成")

    # C2 audit-chain checkpointer: start the ONLY producer of verification
    # checkpoints. Without this, `verify_incremental` / `verify_rolling` have no
    # production caller, so `checkpoint_count` stays 0 forever and /v1/ready
    # reports `evidence_status="unverified"` permanently -- an honest gap that
    # can never close on its own, and "verified" decays into "we verified it
    # once, on an unknown date".
    #
    # Precedence: AUDIT_DB_PATH wins and is resolved FIRST, before any other
    # consumer of the audit store can construct it. The gap path is created ONLY
    # when AUDIT_DB_PATH is explicitly set; when it is unset we skip outright --
    # no thread, no file, and above all NO write to the default project-root
    # store. That default is the frozen HC-01 evidence store, and a background
    # thread must never be the thing that appends to it.
    #
    # Idempotent (start() returns the running worker) and fail-safe: a failure
    # is logged loudly and boot continues. This deliberately does NOT gate
    # readiness -- `unverified` must not 503 the deployment, because that would
    # pull every instance out of rotation with no mechanism to advance coverage.
    _audit_db_path = os.environ.get("AUDIT_DB_PATH")
    if not _audit_db_path:
        logger.warning(
            "AUDIT CHECKPOINTER SKIPPED -- AUDIT_DB_PATH is not set. The audit "
            "chain will NOT be checkpointed, so /v1/ready evidence_status stays "
            "'unverified'. Set AUDIT_DB_PATH to a volume path to enable it "
            "(the default project-root store is frozen evidence and is never "
            "written by this background thread)."
        )
    else:
        # ``start_background_verifier`` 是**惰性**解析 store 的：线程启动后才发现
        # 库打不开，而旧代码已经打印了 "Audit checkpointer started"。实测（损坏
        # 的 audit.db）日志里同时出现 "started" 与 "file is not a database"，
        # 互相矛盾。所以先真打开一次审计库；打不开就不启动、也不打印 started。
        _audit_store_opened = False
        try:
            from ..kernels.audit import get_audit_store

            get_audit_store()
            _audit_store_opened = True
        except Exception as _audit_exc:  # noqa: BLE001 - 降级必须响，但不能崩启动
            logger.error(
                "AUDIT CHECKPOINTER NOT STARTED -- 审计库打不开，本次运行不会写"
                "检查点（原因见上面的 evt=db.open.failed）: %s",
                _audit_exc,
            )
        if _audit_store_opened:
            try:
                from ..reliability.checkpointer import (
                    estimate_verify_seconds,
                    start_background_verifier,
                )

                _checkpointer = start_background_verifier()
                app.state.audit_checkpointer = _checkpointer
                # ``estimate_verify_seconds`` is a MODULE-LEVEL function, not a
                # BackgroundVerifier method (see checkpointer.py:73; the class's own
                # start() calls it the same way). Calling it off the instance raised
                # AttributeError *after* start() had already launched the thread and
                # mounted it on app.state -- so the handler below logged "FAILED TO
                # START ... evidence_status stays 'unverified'" while the
                # checkpointer was in fact running. A false failure claim does the
                # same damage as a false success: it trains people to ignore this
                # line. Any future edit must keep this a module-level call.
                logger.info(
                    "Audit checkpointer started (segment=%s events, interval=%ss, "
                    "rolling every %s ticks, ~%ss/tick) against AUDIT_DB_PATH=%s",
                    _checkpointer.segment_events,
                    _checkpointer.incremental_interval_sec,
                    _checkpointer.rolling_every_n_ticks,
                    round(estimate_verify_seconds(_checkpointer.segment_events), 3),
                    _audit_db_path,
                )
            except Exception as _ckpt_exc:  # noqa: BLE001 - loud, never crash boot
                logger.error(
                    "AUDIT CHECKPOINTER FAILED TO START -- the audit chain will not "
                    "be checkpointed and evidence_status stays 'unverified': %s",
                    _ckpt_exc, exc_info=True,
                )
    _startup_mark("审计 checkpointer 启动完成")

    # Register the production alert RULES into the store at boot. The rules are
    # all enabled=False (their metric emitters are not wired yet) -- this makes
    # their honest status visible via GET /v1/alerts/rules instead of leaving
    # the registry empty. Idempotent and fail-safe.
    try:
        from ..observability.alerts import install_production_rules
        install_production_rules()
        logger.info("Production alert rules registered (enabled=True; real metric emitters now wired)")
    except Exception as _rules_exc:  # noqa: BLE001 - loud, never crash boot
        from ..common.atomic_write import StorageWriteError

        if isinstance(_rules_exc, StorageWriteError):
            # 磁盘满/只读/无权限是**预期内的环境故障**：给中文可行动提示，
            # 不打印英文堆栈；启动继续，对话等其它能力不受影响。
            logger.error(
                "生产告警规则未能保存，/v1/alerts/rules 将为空（不影响对话）：%s",
                _rules_exc,
            )
        else:
            logger.error(
                "INSTALL PRODUCTION ALERT RULES FAILED -- /v1/alerts/rules will be empty: %s",
                _rules_exc,
                exc_info=True,
            )
    _startup_mark("生产告警规则注册完成")

    # Plugin loader (Top-3 #2): register the built-in, loadable plugins so that
    # GET /v1/plugins and the Apps surface have at least one real, activatable
    # entry. Idempotent and fail-safe — never crash boot.
    try:
        from src.kernels.plugin import register_builtin_plugins

        register_builtin_plugins()
        logger.info("Built-in plugins registered (Apps surface enabled)")
    except Exception as _plugin_exc:  # noqa: BLE001 - loud, never crash boot
        logger.error(
            "REGISTER BUILTIN PLUGINS FAILED -- /v1/plugins will be empty: %s",
            _plugin_exc,
            exc_info=True,
        )
    _startup_mark("内置插件注册完成")

    # OB / blocker #3: drive the REAL metrics -> alert evaluation loop.
    # install_production_rules() now registers *enabled* rules and
    # MetricCollector produces real values for all 6 metric names, so this
    # daemon thread genuinely closes the loop: collect_snapshot() every 15s,
    # then AlertManager.evaluate_all(snapshot). Fail-safe: any iteration error
    # is logged and never crashes the loop or boot. The stop Event is set in
    # the shutdown half of this lifespan.
    try:
        _metrics_stop = threading.Event()
        app.state.metrics_evaluator_stop = _metrics_stop

        def _metrics_evaluator() -> None:
            # 导入放在线程体内：prometheus_client 约为 0.5–1.1s，不能占用
            # 就绪前的 lifespan 时间；线程晚几百毫秒启动不影响 15s 评估节奏。
            try:
                from ..observability.metrics import get_metric_collector
                from ..observability.alerts import get_alert_manager

                collector = get_metric_collector()
                manager = get_alert_manager()
            except Exception as _eval_import_exc:  # noqa: BLE001
                logger.error(
                    "METRICS EVALUATOR LOOP FAILED TO START -- system-health "
                    "rules will stay inactive: %s",
                    _eval_import_exc,
                    exc_info=True,
                )
                return
            while not _metrics_stop.is_set():
                try:
                    snapshot = collector.collect_snapshot()
                    manager.evaluate_all(snapshot)
                except Exception as _eval_exc:  # noqa: BLE001 - never kill loop
                    logger.error(
                        "METRICS EVALUATOR ITERATION FAILED: %s",
                        _eval_exc, exc_info=True,
                    )
                # Interruptible wait so shutdown is responsive.
                _metrics_stop.wait(15.0)

        _metrics_thread = threading.Thread(
            target=_metrics_evaluator, name="metrics-evaluator", daemon=True
        )
        _metrics_thread.start()
        app.state.metrics_evaluator_thread = _metrics_thread
        logger.info(
            "Metrics evaluator loop started (15s interval; enabled system-health rules)"
        )
    except Exception as _eval_start_exc:  # noqa: BLE001 - loud, never crash boot
        logger.error(
            "METRICS EVALUATOR LOOP FAILED TO START -- system-health rules will "
            "NOT be evaluated (observability closed loop broken): %s",
            _eval_start_exc, exc_info=True,
        )
    _startup_mark("指标评估循环启动完成")

    # 首屏的**第一个**静态响应是 ``StaticFiles.get_response`` 的首次调用：
    # 它先把 stat 丢进 anyio 线程池，而线程池要到第一次调用才建 worker。
    # 实测（同进程、同一份 dist）首次 316.7ms、第二次 5.5ms——差的是
    # mimetypes 首次读注册表 + 线程池首次建 worker。mimetypes 已在挂载期
    # 开线程预热（与路由注册重叠），这里只需把 anyio 线程池热起来，再给
    # 那个并行线程一个有界收尾。
    static_warm = getattr(app.state, "console_static_warm_thread", None)
    if static_warm is not None:
        static_warm.join(1.5)
    try:
        import anyio

        await anyio.to_thread.run_sync(lambda: None)
    except Exception:  # noqa: BLE001 - 预热失败不影响启动
        logger.debug("anyio 线程池预热失败", exc_info=True)
    _startup_mark("静态服务线程池预热完成")

    # 核心就绪：登录（密钥/JWT/RBAC/身份）、对话路由、模型配置路由此时都已
    # 真实可用。下面的"对话链预热"只是让第一次发消息不必再等导入，**不**阻塞
    # 就绪；预热失败必须响（日志 + /v1/startup.background.errors），绝不能
    # 悄悄变成"第一次发消息才 500"。
    _startup_ready()

    # ── 启动自检：一条结构化日志回答"能不能写 / 能不能开 / 能不能绑" ──
    # 这是"只看日志排障"的入口行。字段与 /v1/startup.startup_check 完全一致：
    # data_dir/data_writable/log_dir/log_writable/db/db_open/port/port_bindable/
    # console_dist/console_index/provider/model/key_fp。
    try:
        from ..logging_config import resolve_data_dir, resolve_log_file
        from ..observability import boot_report
        from .console_static import resolve_console_dist

        _data_dir = resolve_data_dir()
        _log_dir = resolve_log_file("logs/liuhao.log").parent
        try:
            from ..ai.conversation_store import DEFAULT_DB_PATH as _conversation_db
        except Exception:  # noqa: BLE001 - 自检不能因导入失败而中断
            _conversation_db = str(_data_dir / "conversation_store.db")
        boot_report.emit_startup_check(
            data_dir=_data_dir,
            log_dir=_log_dir,
            db_paths={
                "audit": (os.environ.get("AUDIT_DB_PATH") or "").strip() or "",
                "conversation": (os.environ.get("CONVERSATION_DB_PATH") or "").strip()
                or _conversation_db,
                "model_registry": (
                    os.environ.get("LIUHAO_MODEL_CONFIG_DB") or ""
                ).strip()
                or str(_data_dir / "model_registry.sqlite3"),
            },
            host=(
                os.environ.get("LIUHAO_LISTEN_HOST")
                or os.environ.get("HOST")
                or "127.0.0.1"
            ),
            port=int((os.environ.get("PORT") or "8080").strip() or "8080"),
            console_dist=resolve_console_dist(),
        )
        # 有降级组件时补 evt=startup.degraded（与 /v1/startup.degraded 同源）。
        boot_report.emit_startup_degraded()
    except Exception:  # noqa: BLE001 - 自检失败不能影响服务
        logger.warning("启动自检日志写入失败（服务继续）", exc_info=True)

    def _warm_observability() -> None:
        from ..observability.tracing import get_tracer

        get_tracer()

    def _run_background_step(name: str, action) -> None:
        started = time.perf_counter()
        try:
            action()
        except Exception as _step_exc:  # noqa: BLE001 - 必须响，不能让就绪失败
            logger.error(
                "BACKGROUND STARTUP STEP FAILED（%s）-- 该组件稍后首次使用时"
                "会同步重试；功能不会因此失真：%s",
                name,
                _step_exc,
                exc_info=True,
            )
            _bg_step(
                name,
                False,
                (time.perf_counter() - started) * 1000,
                f"{type(_step_exc).__name__}: {_step_exc}",
            )
        else:
            _bg_step(name, True, (time.perf_counter() - started) * 1000)
            logger.info("Background startup step done: %s (%.2fs)", name,
                        time.perf_counter() - started)

    def _warm_chat() -> None:
        from .chat import warmup as _impl

        _impl()

    def _warm_roster_employees() -> None:
        from ..ai.roster_payload import prewarm_roster_payload

        if not prewarm_roster_payload():
            raise RuntimeError("roster prewarm failed")

    def _warm_chat_stack() -> None:
        _bg_started()
        _run_background_step("初始化追踪（OpenTelemetry SDK）", _warm_observability)
        _run_background_step("初始化指标（prometheus_client / HTTP 采样）",
                             _ensure_http_metrics)
        # 首个 /v1/dashboard/roster 不再承担一次性导入/读盘成本。
        _run_background_step("预热名册（员工存储 / 注册表 / 服务商）", _warm_roster_employees)
        _run_background_step("预热对话链（LLM/provider/会话存储）", _warm_chat)
        _bg_finished()

    # 首屏优先：对话链预热会同步导入 LLM / provider / 会话存储，占着 GIL 时
    # 浏览器发出的第一个 ``/`` 与 ``/v1/auth/config`` 会被饿住（安装版实测
    # 首屏白等 1.1s，更差的时候 2~3s）。所以预热不再"到点就抢 CPU"，而是等
    # **首屏那一屏真的发出去之后**再开始：console 的 404 兜底在响应结束时
    # ``set()`` ``app.state.first_screen_served``，本线程等它，最多等
    # ``LIUHAO_WARMUP_GRACE_S`` 秒（默认 10s，没有浏览器打开时兜底）。
    # 设为 0 恢复"就绪后立刻预热"的旧行为（用于 A/B）。
    def _warmup_grace_seconds() -> float:
        raw = (os.environ.get("LIUHAO_WARMUP_GRACE_S") or "").strip()
        if raw:
            try:
                return max(0.0, float(raw))
            except ValueError:
                logger.warning("LIUHAO_WARMUP_GRACE_S=%r 不是数字，按默认 10s", raw)
        return 10.0

    def _start_local_scheduler() -> None:
        """在网关事件循环里托管本地排程器，并把失败如实上报。

        调用方是独立线程（``_deferred_scheduler``），必须把 lifespan 里拿到的
        事件循环显式传进去：排程器协程只能在该循环里 ``create_task``，否则
        就是修复前的 ``no running event loop``。失败写进
        ``/v1/startup.background.errors``，启动器会显示成
        "核心可用，但后台组件未就绪：<中文原因>"。
        """
        step_name = "启动本地排程器（提醒 / 定时任务）"
        started = time.perf_counter()
        if scheduler_loop.is_closed():
            logger.info("本地排程器跳过启动：网关事件循环已关闭")
            return
        try:
            from ..tasks.scheduler_host import HostedLocalScheduler

            hosted_scheduler = HostedLocalScheduler()
            ok = hosted_scheduler.start(loop=scheduler_loop)
            app.state.local_scheduler_host = hosted_scheduler
            _startup_mark(f"本地排程器：{hosted_scheduler.reason}")
            elapsed_ms = (time.perf_counter() - started) * 1000
            if ok:
                _bg_step(step_name, True, elapsed_ms)
            elif hosted_scheduler.start_error:
                # 真失败：提醒/定时任务不会触发，必须让用户看得见。
                _bg_step(step_name, False, elapsed_ms, hosted_scheduler.start_error)
            else:
                # 按配置不托管 / 另一个进程在跑：正常让位，不是故障。
                _bg_step(step_name, True, elapsed_ms, hosted_scheduler.reason)
        except Exception as _sched_exc:  # noqa: BLE001 - 托管失败不能阻断启动
            app.state.local_scheduler_host = None
            detail = (
                "本地排程器启动异常（提醒与定时任务不会触发）："
                f"{type(_sched_exc).__name__}: {_sched_exc}"
            )
            logger.error(
                "LOCAL SCHEDULER HOST FAILED TO START -- 提醒与定时任务将不会触发：%s",
                _sched_exc,
                exc_info=True,
            )
            _bg_step(step_name, False, (time.perf_counter() - started) * 1000, detail)

    def _scheduler_grace_seconds() -> float:
        """排程器延后多久启动。

        排程器要导入 ``tasks.scheduler_host`` → ``local_handlers`` →
        ``local_scheduler`` 一整条链；实测（安装版冷启动）这一步在高负载下要
        2.4s，而且它和对话链预热都占 GIL——首屏的 ``/`` 请求会被饿住。
        这里给它一个比对话链更长的窗口，等登录框渲染完再起；提醒是分钟级
        节奏，晚几秒无感。``LIUHAO_SCHEDULER_GRACE_S`` 可覆盖，0 = 旧行为。
        """
        raw = (os.environ.get("LIUHAO_SCHEDULER_GRACE_S") or "").strip()
        if raw:
            try:
                return max(0.0, float(raw))
            except ValueError:
                logger.warning(
                    "LIUHAO_SCHEDULER_GRACE_S=%r 不是数字，按默认 8s", raw
                )
        return 8.0

    def _scheduler_start_timeout_seconds() -> float:
        """后台启动线程的总预算（含宽限窗口）。

        启动线程可能卡在导入/build 里而不抛异常，``/v1/ready`` 会一直显示
        "启动中"。超过本阈值即视为启动超时并报 unhealthy。
        ``LIUHAO_SCHEDULER_START_TIMEOUT_S`` 可覆盖（秒，默认 60）。
        """
        raw = (os.environ.get("LIUHAO_SCHEDULER_START_TIMEOUT_S") or "").strip()
        if raw:
            try:
                return max(1.0, float(raw))
            except ValueError:
                logger.warning(
                    "LIUHAO_SCHEDULER_START_TIMEOUT_S=%r 不是数字，按默认 60s", raw
                )
        return 60.0

    def _deferred_background() -> None:
        grace = _warmup_grace_seconds()
        if grace > 0:
            # 等首屏发出去（或超时兜底），再让首屏的静态资源落地一小会儿。
            app.state.first_screen_served.wait(grace)
            time.sleep(0.5)
        _warm_chat_stack()

    def _deferred_scheduler() -> None:
        grace = _scheduler_grace_seconds()
        if grace > 0:
            time.sleep(grace)
        _start_local_scheduler()

    # 关闭流程用 getattr 默认 None 读这个属性；先置 None，避免"线程还没跑完
    # 就 Shutdown"时 AttributeError。
    app.state.local_scheduler_host = None
    # 排程器协程只能在这个循环里 create_task。下面两个后台线程没有运行中的
    # 事件循环（旧代码就在这里抛出 no running event loop），所以把循环显式
    # 交给 HostedLocalScheduler.start(loop=...)。
    scheduler_loop = asyncio.get_running_loop()
    app.state.first_screen_served = threading.Event()
    # 启动超时基准（含延后启动的宽限窗口）。线程若卡在导入/build 而不抛异常，
    # /v1/ready 会用这个截止时间把"启动中"转成"启动超时"。
    app.state.scheduler_start_timeout_s = _scheduler_start_timeout_seconds()
    app.state.scheduler_start_deadline = (
        time.monotonic() + app.state.scheduler_start_timeout_s
    )
    _sched_thread = threading.Thread(
        target=_deferred_scheduler, name="local-scheduler-host", daemon=True
    )
    _sched_thread.start()
    app.state.scheduler_host_thread = _sched_thread
    _warm_thread = threading.Thread(
        target=_deferred_background, name="chat-stack-warmup", daemon=True
    )
    _warm_thread.start()
    app.state.chat_warmup_thread = _warm_thread
    _startup_mark(
        "核心就绪（登录/对话/模型配置可用），对话链延后 "
        f"{_warmup_grace_seconds()}s、排程器延后 {_scheduler_grace_seconds()}s 后台启动"
    )

    _startup_report()

    # 手机访问：按上次记录恢复；网段变了就自动关闭并提示（不对陌生 Wi-Fi 开放）。
    try:
        from .lan_access import resume_on_startup

        resume_on_startup()
    except Exception:  # noqa: BLE001 - 恢复失败不能阻断网关启动
        logger.warning("lan access resume failed", exc_info=True)

    # 事件循环停滞看门狗：僵死时把"谁占着事件循环"写进日志（不修复，只取证）。
    _loop_watchdog = LoopStallWatchdog()
    try:
        _loop_watchdog.start()
        app.state.loop_watchdog = _loop_watchdog
    except Exception:  # noqa: BLE001 - 看门狗失败不能阻断启动
        logger.warning("loop stall watchdog failed to start", exc_info=True)

    yield  # App runs here

    # Shutdown
    logger.info("Shutting down Jarvis Gateway...")

    try:
        _loop_watchdog.stop()
    except Exception:  # noqa: BLE001 - shutdown must never raise
        pass

    # 先停排程器：让它把在途任务交还给队列（retry_wait），别留下"running"。
    try:
        _hosted_scheduler = getattr(app.state, "local_scheduler_host", None)
        if _hosted_scheduler is not None:
            await _hosted_scheduler.stop()
    except Exception:  # noqa: BLE001 - shutdown must never raise
        logger.exception("LOCAL SCHEDULER STOP FAILED (shutdown continues)")

    # 关掉手机访问中继：进程退出后不能留下一个还在监听局域网的端口。
    try:
        from .lan_access import shutdown_lan_access

        shutdown_lan_access()
    except Exception:  # noqa: BLE001 - shutdown must never raise
        logger.warning("lan access shutdown failed", exc_info=True)

    # Stop the audit checkpointer (best-effort; never stalls shutdown long).
    try:
        _checkpointer = getattr(app.state, "audit_checkpointer", None)
        if _checkpointer is not None:
            _checkpointer.stop(timeout=5.0)
            logger.info(
                "Audit checkpointer stopped (stats=%s)",
                _checkpointer.stats.snapshot(),
            )
    except Exception:  # noqa: BLE001 - shutdown must never raise
        logger.exception("AUDIT CHECKPOINTER STOP FAILED (shutdown continues)")

    # Stop the metrics evaluator loop (best-effort; never stalls shutdown long).
    try:
        _metrics_stop = getattr(app.state, "metrics_evaluator_stop", None)
        _metrics_thread = getattr(app.state, "metrics_evaluator_thread", None)
        if _metrics_stop is not None:
            _metrics_stop.set()
        if _metrics_thread is not None:
            _metrics_thread.join(timeout=5.0)
    except Exception:  # noqa: BLE001 - shutdown must never raise
        pass

    # 关闭本机证明通道；已发放但未消费的令牌随进程一起失效。
    try:
        from . import local_proof

        local_proof.stop_all_brokers()
        local_proof.reset_token_store()
    except Exception:  # noqa: BLE001 - shutdown must never raise
        pass
    _shutdown_report = shutdown_all()
    _shutdown_down = _shutdown_report.get("shutdown", [])
    _shutdown_errors = _shutdown_report.get("errors", [])
    if _shutdown_errors:
        logger.error(
            "KERNEL LIFECYCLE SHUTDOWN DEGRADED -- %d kernel(s) failed to "
            "shut down cleanly: %s",
            len(_shutdown_errors),
            _shutdown_errors,
        )
    logger.info(
        "Kernel lifecycle: shutdown=%s, skipped_not_present=%s, "
        "no_canonical_instance=%s, errors=%d",
        _shutdown_down,
        _shutdown_report.get("skipped_not_present", []),
        _shutdown_report.get("no_canonical_instance", []),
        len(_shutdown_errors),
    )
    logger.info("Gateway shutdown complete")


# ==================== FastAPI Application ====================

def describe_exception(exc: BaseException, limit: int = 300) -> str:
    """把异常压成一句**可读且真实**的话（供 500 响应体使用）。

    诚实 > 好看：兜底处理器不能为了"文案统一"把真实成因吞掉，否则运维拿到
    的只有一句"发生了一个错误"，什么都做不了。同时做两件事防止走极端：

    * 保留异常类型 + 消息（"sqlite3.OperationalError: no such table: x" 本身
      就是最精确的修复指引）；
    * 截断到 `limit` 字符 —— 异常消息可能含整段 payload / 路径，原样回给
      客户端等于把内部实现细节整包倒出去。完整堆栈留在服务端日志里。
    """
    text = str(exc).strip()
    if not text:
        return type(exc).__name__
    if len(text) > limit:
        text = text[:limit] + "…(已截断)"
    return f"{type(exc).__name__}: {text}"


#: 422 响应里绝不回显原值的字段（口令、恢复码、口令短语、令牌、凭据包）。
_SENSITIVE_VALIDATION_FIELDS = frozenset({
    "password",
    "secret",
    "recovery_code",
    "passphrase",
    "code",
    "token",
    "access_token",
    "refresh_token",
    "api_key",
    "authorization",
    "credential",
    "bundle",
})

#: 长度类校验错误：``input`` 是整段超长原文，必须改写成"上限/当前"的中文，
#: 绝不回显 input。覆盖 Pydantic v2 的 string/list 版本与旧命名。
_LENGTH_ERROR_TYPES = frozenset({
    "string_too_long",
    "string_too_short",
    "list_too_long",
    "list_too_short",
    "too_long",
    "too_short",
})


def _validation_label_for_loc(loc, field_labels) -> str:
    """按 loc 的最长前缀取用户可读字段名；找不到时返回「该字段」。"""

    parts = [str(part) for part in loc]
    if field_labels:
        for size in range(len(parts), 0, -1):
            key = tuple(parts[:size])
            label = field_labels.get(key)
            if label:
                return label
    return "该字段"


def _collect_validation_labels(request: Request) -> dict:
    """从当前路由的 body / query / path 模型收集「字段 -> 中文名」映射。

    Pydantic 的 ``title``/``description`` 才是给用户看的；内部 snake_case 字段名
    只用于定位，不进入响应文案。
    """

    labels: dict = {}
    route = request.scope.get("route")
    if route is None:
        return labels
    body_field = getattr(route, "body_field", None)
    model = body_model_from_field(body_field)
    model_fields = getattr(model, "model_fields", None)
    if model_fields:
        for name, info in model_fields.items():
            label = field_label_from_info(info, fallback="该字段")
            labels[("body", str(name))] = label
            alias = getattr(info, "alias", None)
            if alias:
                labels[("body", str(alias))] = label
    dependant = getattr(route, "dependant", None)
    for attribute, kind in (
        ("query_params", "query"),
        ("path_params", "path"),
        ("header_params", "header"),
        ("cookie_params", "cookie"),
    ):
        for field in getattr(dependant, attribute, []) or ():
            name = getattr(field, "name", None)
            if not name:
                continue
            labels[(kind, str(name))] = field_label_from_info(
                getattr(field, "field_info", None), fallback="该字段"
            )
    return labels


def public_validation_errors(errors, field_labels=None) -> list:
    """把 Pydantic 校验错误脱敏 + **中文化**后再回给客户端。

    规则（与模糊测试的红线一致）：

    * 禁止透传英文 ``msg``：一律按 ``type`` 映射成中文；
    * 字段名取 schema 的 ``title``/``description``，不暴露 snake_case 内部名；
    * 敏感字段（口令 / 恢复码 / 口令短语 / 令牌 / 凭据包）与整个请求体
      不返回 ``input``/``ctx``；
    * 其余错误的 ``input`` 一律截断（超长原文会随输入线性放大响应体）。
    """

    cleaned = []
    for err in errors or ():
        loc = list(err.get("loc") or ())
        names = [str(part).lower() for part in loc]
        err_type = str(err.get("type") or "")
        label = _validation_label_for_loc(loc, field_labels)
        item = {"loc": loc}
        if err_type in _LENGTH_ERROR_TYPES:
            # 原始输入只用来算长度，绝不回显；中文文案 + 字段名即可行动。
            ctx = err.get("ctx") or {}
            raw_input = err.get("input")
            actual = len(raw_input) if isinstance(raw_input, (str, list, dict)) else None
            field = ".".join(
                str(part) for part in loc
                if str(part).lower() not in ("body", "query", "path", "header", "cookie")
            )
            max_length = ctx.get("max_length") if isinstance(ctx, dict) else None
            min_length = ctx.get("min_length") if isinstance(ctx, dict) else None
            item["field"] = field or None
            if err_type in ("string_too_long", "list_too_long", "too_long") and max_length is not None:
                if field == "message":
                    item["msg"] = f"单条消息上限 {max_length} 字，当前 {actual} 字"
                    item["code"] = "message_too_long"
                else:
                    item["msg"] = f"{label}上限 {max_length} 字，当前 {actual} 字"
                    item["code"] = "value_too_long"
            elif err_type in ("string_too_short", "list_too_short", "too_short") and min_length is not None:
                item["msg"] = f"{label}至少需要 {min_length} 个字，当前 {actual} 个。"
                item["code"] = "value_too_short"
            else:
                item["msg"] = "内容长度不符合要求，请调整后重试。"
                item["code"] = "value_length_invalid"
            cleaned.append(item)
            continue

        item["type"] = err.get("type")
        item["msg"] = translate_validation_error(err_type, label, err.get("msg"))
        exposes_secret = (
            not names
            # ``loc == ["body"]`` 时 input 是整个请求体，可能同时含口令。
            or (names[0] in ("body", "json") and len(names) <= 1)
            or any(part in _SENSITIVE_VALIDATION_FIELDS for part in names)
        )
        if not exposes_secret:
            if "input" in err:
                item["input"] = trim_for_error(err["input"])
            if "ctx" in err:
                item["ctx"] = trim_for_error(err["ctx"])
        cleaned.append(item)
    return cleaned


class _SseAwareGZip(GZipMiddleware):
    """只对 ``/v1/chat/stream`` 跳过压缩，其余接口保持既有压缩行为。

    ``GZipMiddleware`` 会把 ``http.response.start`` 延迟到首个 body chunk。
    SSE 在首个 token 之前没有 chunk，浏览器因此迟迟拿不到响应头，前端 45 秒
    "等响应头"看门狗会把正常冷启动误判成超时。这里按**精确路径**豁免，
    不做"凡 SSE 都豁免"：静态资源的 ``max-age=31536000`` 缓存头仍由后续
    中间件照常处理。
    """

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http" and scope.get("path") == "/v1/chat/stream":
            await self.app(scope, receive, send)
            return
        await super().__call__(scope, receive, send)


def get_app() -> FastAPI:
    """
    Create and configure the FastAPI application.

    Returns:
        Configured FastAPI instance
    """
    _startup_mark("get_app 开始")
    runtime = load_runtime_config()
    production = bool(runtime["production"])
    docs_enabled = bool(runtime.get("http_docs"))
    _startup_mark("运行配置加载完成（config/default.yaml + APP_ENV）")

    # Policy Controlled: an enforced kernel action raises these. Map them to
    # honest HTTP statuses instead of letting the catch-all handler turn
    # "needs a human approval" into a 500 internal error.
    from ..kernels._crosscutting import PolicyDeferredError, PolicyDeniedError

    app = FastAPI(
        title=f"{BRAND_LOCKUP} Gateway",
        version=APP_VERSION,
        description=f"Unified API Gateway for {BRAND_LOCKUP}",
        lifespan=lifespan,
        # OpenAPI/docs are a broad reconnaissance surface (every route, every
        # schema, every query parameter). Only an explicit development switch
        # opens them; production and the no-APP_ENV default stay closed.
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
    )
    if not docs_enabled:
        logger.info(
            "Debug API surface DISABLED (APP_ENV=%s, http_docs=%s): "
            "/docs, /redoc, /openapi.json return 404",
            runtime["environment"],
            runtime.get("http_docs"),
        )
    else:
        logger.warning(
            "Debug API surface ENABLED (APP_ENV=%s): /docs, /redoc, "
            "/openapi.json are readable on this listener -- set "
            "LIUHAO_HTTP_DOCS=0 or APP_ENV=production to turn them off",
            runtime["environment"],
        )

    # ==================== Middleware ====================

    # Rate limiting —— 覆盖配置里列出的高成本/写入面（默认 chat + goals）。
    # 必须比 CORS **先注册**：后注册的中间件在外层，这样 429 也会经过 CORS
    # 拿到跨域响应头，控制台才能读懂"限流了"而不是只看到 CORS 报错。
    rate_cfg = runtime.get("rate_limit") or {}
    trusted_proxies = tuple(runtime.get("trusted_proxies") or ())
    if rate_cfg.get("enabled", True):
        from .rate_limiter import DEFAULT_PROTECTED_PATHS, RateLimitMiddleware

        # union：配置只能“加”路径，不能“减”——旧配置文件不能让网关失守。
        configured_paths = tuple(rate_cfg.get("paths") or ())
        app.add_middleware(
            RateLimitMiddleware,
            paths=tuple(dict.fromkeys(DEFAULT_PROTECTED_PATHS + configured_paths)),
            trusted_proxies=trusted_proxies,
        )

    # 请求体上限（server.limits.max_body_bytes / max_upload_bytes）。
    limits = runtime.get("limits") or {}
    from .http_limits import (
        BodyLimitMiddleware,
        BodyPreflightMiddleware,
        JsonBodyGuardMiddleware,
    )

    # 解析阶段的结构闸：深嵌套 / 超大容器 / 非法 UTF-8 在进 Pydantic 之前拒绝。
    # 注册在 BodyLimit **之前**，构建中间件栈时它会落在 BodyLimit 内层，
    # 因而读到的是已被大小上限约束的字节流。
    app.add_middleware(JsonBodyGuardMiddleware)

    app.add_middleware(
        BodyLimitMiddleware,
        max_body_bytes=int(limits.get("max_body_bytes") or 0),
        max_upload_bytes=int(limits.get("max_upload_bytes") or 0),
        max_backup_bytes=int(limits.get("max_backup_bytes") or 0),
    )

    # 必填字段预检：注册在速率限制之后 => 位于其外层，422 才能抢在
    # RateLimitMiddleware 的 503 IDENTITY_UNAVAILABLE 之前返回。
    app.add_middleware(BodyPreflightMiddleware, fastapi_app=app)

    # 全局单客户端每分钟预算（server.limits.rate_limit_per_minute）。
    if int(limits.get("rate_limit_per_minute") or 0) > 0:
        from .rate_limiter import GlobalRateLimitMiddleware

        app.add_middleware(
            GlobalRateLimitMiddleware,
            limit_per_minute=int(limits.get("rate_limit_per_minute") or 0),
            trusted_proxies=trusted_proxies,
        )

    # CORS middleware —— 实际读取 config/default.yaml（+ APP_ENV 覆盖文件）的
    # server.cors.allow_origins。此前读的是 settings.get("api", {}).get(...)：
    # ConfigManager 只扫 configs/（本仓库里没有该键），于是永远落回 ["*"]，
    # 而 YAML 里写的白名单从未生效 -- "配置看起来能改，其实改不动"。
    cors = runtime["cors"]
    if production and "*" in cors["allow_origins"]:
        # 通配 + allow_credentials 等于把任意站点都当成可信来源。生产必须
        # 显式列名单，宁可在启动时报错，也不带病上线。
        raise RuntimeError(
            "生产环境禁止 CORS allow_origins='*'；请在 config/production.yaml "
            "的 server.cors.allow_origins 显式列出前端域名"
        )
    if cors["enabled"]:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors["allow_origins"],
            allow_credentials=cors["allow_credentials"],
            allow_methods=cors["allow_methods"],
            allow_headers=cors["allow_headers"],
            max_age=cors["max_age"],
            # 不暴露 X-Trace-ID，跨域的浏览器 JS 就读不到它（CORS 默认只放开
            # 简单响应头），于是"拿 trace_id 去查日志"这条唯一的排障路径在
            # 跨域部署下直接断掉。跨域也要把它交出去。
            expose_headers=["X-Trace-ID"],
        )
    else:
        logger.warning(
            "CORS disabled by config (server.cors.enabled=false); cross-origin "
            "console deployments will not work"
        )

    # GZip compression（仅 /v1/chat/stream 豁免：见 _SseAwareGZip 文档）
    app.add_middleware(_SseAwareGZip, minimum_size=1000)

    # Host 白名单 + 写方法 Origin 校验（最外层安全闸，见类文档）。
    # 不论 CORS 是否启用都必须挂：两者保护的不是同一件事。
    # 生产：只额外放行"本机回环 + 本次实际监听端口"的写来源（安装版从
    # http://127.0.0.1:<随机端口> 打开控制台，端口无法提前写死进白名单）。
    # 开发：回环来源仍按显式端口列表注入，行为不变。
    guard_origins = list(cors["allow_origins"])
    loopback_port = None
    listen_port = resolve_listen_port()
    # 显式对外绑定时，手机/局域网访问的写来源按「本机 LAN IP + 实际端口」
    # 自动放行（精确来源，不退化成 ``*``）。见 ``lan_write_origins``。
    lan_bind = external_bind_enabled()
    if not production:
        guard_origins.extend(development_loopback_origins())
    else:
        loopback_port = listen_port
    app.add_middleware(
        HostGuardMiddleware,
        allowed_hosts=runtime["trusted_hosts"],
        allowed_origins=guard_origins,
        allow_ip_literals=runtime["host_guard_allow_ip_literals"],
        loopback_port=loopback_port,
        lan_bind=lan_bind,
        lan_port=listen_port if lan_bind else None,
    )
    logger.info(
        "Host allow-list (DNS-rebinding guard): %s",
        ", ".join(runtime["trusted_hosts"]),
    )

    # ==================== 请求来源中间件（本机 / 局域网手机） ====================
    # 只有「回环 + 中继共享密钥」才被标记为 lan；手机伪造不了 relay.secret。
    # 单独成函数是为了让测试能装同一个中间件，不必启动整个网关。
    from .device_sessions import install_origin_middleware

    install_origin_middleware(app)

    # ==================== Request logging middleware ====================

    @app.middleware("http")
    async def logging_middleware(request: Request, call_next):
        start_time = time.time()

        # Trace ID from headers or generate new. Only a well-formed UUID is
        # accepted: the value goes into every log line, so an arbitrary string
        # (newlines, control chars) would be a log-injection vector.
        trace_id = request.headers.get("X-Trace-ID", "")
        if trace_id:
            try:
                import uuid

                trace_id = str(uuid.UUID(trace_id))
            except (ValueError, AttributeError, TypeError):
                trace_id = ""
        if not trace_id:
            import uuid
            trace_id = str(uuid.uuid4())

        # 必须落在 request.state：全局异常处理器（main.py 的
        # global_exception_handler）靠它把 500 响应和日志行串起来。此前它只
        # 是这里的局部变量，于是 500 响应里的 trace_id 恒为 "unknown" ——
        # 一个根本检索不到任何东西的死面包屑。
        request.state.trace_id = trace_id

        response = await call_next(request)

        process_time = time.time() - start_time
        response.headers["X-Trace-ID"] = trace_id

        # Feed the HTTP request metrics so a scrape carries real per-request
        # samples. The label is the MATCHED ROUTE TEMPLATE, never the raw path:
        # a raw path embeds unbounded ids and every new id would permanently
        # add a label set to every HTTP histogram (measured in the 22-minute
        # soak). Instrumentation must never break the request itself, hence the
        # guard.
        #
        # ``_ensure_http_metrics`` is normally deferred to the background
        # warmup (to avoid paying the prometheus_client import cost before the
        # first screen). If a request arrives before that warmup has run, we
        # trigger it inline here so the first real request is still counted
        # rather than silently dropped. The call is idempotent.
        if _RECORD_HTTP_REQUEST is None:
            try:
                _ensure_http_metrics()
            except Exception:  # pragma: no cover - never fail a request
                pass
        if _RECORD_HTTP_REQUEST is not None:
            try:
                _RECORD_HTTP_REQUEST(
                    request.method,
                    request.scope,
                    response.status_code,
                    process_time,
                )
            except Exception as exc:  # pragma: no cover - never fail a request
                logger.warning(f"http metrics instrumentation failed: {exc}")

        logger.info(
            f"{request.method} {request.url.path} "
            f"{response.status_code} {process_time:.4f}s "
            f"trace_id={trace_id}"
        )

        return response

    # ==================== Metrics recording middleware ====================
    # Feeds the REAL alert-engine emitters (MetricCollector) with live per-
    # request data: status class counters + a bounded latency window. This is
    # what makes error_rate_percent and latency_p99_ms real. It must NEVER
    # break the request, hence the guard + internal swallow.

    @app.middleware("http")
    async def metrics_middleware(request: Request, call_next):
        start_time = time.time()
        response = await call_next(request)
        process_time = time.time() - start_time
        if _get_metric_collector is not None:
            try:
                _get_metric_collector().record_request(
                    process_time, response.status_code,
                    is_probe=request.url.path in PROBE_ONLY_PATHS,
                )
            except Exception as exc:  # pragma: no cover - never fail a request
                logger.warning(f"metric recording failed: {exc}")
        return response

    # 满载快速失败闸：加在最后 = 最外层，慢请求还没进业务线程池就先被挡下并
    # 返回 503；探针路径豁免（见类文档）。阈值可用 LIUHAO_MAX_INFLIGHT 覆盖。
    try:
        _max_in_flight = int((os.environ.get("LIUHAO_MAX_INFLIGHT") or "150").strip())
    except ValueError:
        _max_in_flight = 150
    app.add_middleware(OverloadGuardMiddleware, max_in_flight=_max_in_flight)

    # ==================== Exception handlers ====================

    # 账号面／策略面用 ``HTTPException(detail={"code","message"})`` 表达稳定错误码。
    # 默认 FastAPI 只吐 ``{"detail": {...}}``，驾驶舱读的是顶层 ``code``；这里把
    # 结构化错误**同时**摊到顶层（``code`` / ``message`` / ``detail``），非结构化
    # detail（普通字符串）完全沿用默认行为，不影响既有端点。
    from .account_context import structured_error_body
    from .display import (
        UserReadableHTTPException,
        internal_error_body,
        is_public_5xx_detail_safe,
        sanitize_user_copy,
        user_facing_detail,
    )

    @app.exception_handler(StarletteHTTPException)
    async def structured_http_error_handler(request: Request, exc: StarletteHTTPException):
        # 唯一的 HTTPException 处理器。console 的"前端路由 404 回退"不再注册
        # 第二个 handler（那会以同一个 key 覆盖本函数，让所有 HTTPException
        # 绕过脱敏），而是挂在 ``app.state.console_fallback``，在这里先问一句。
        navigation = getattr(request.app.state, "console_fallback", None)
        if navigation is not None:
            rendered = await navigation(request, exc)
            if rendered is not None:
                return rendered

        raw_detail = exc.detail
        # 5xx 且 detail 是普通字符串：默认换成中文兜底 + trace_id。异常原文
        # （路径、表名、堆栈）只进服务端日志；任何端点都不再能靠一条
        # HTTPException 把内部细节带上屏。
        #
        # 例外是**显式标记**的 UserReadableHTTPException：它声明该文案是给用户
        # 看、可照做的（如"知识库未启用：请执行 ollama pull …"）。标记不等于免检，
        # 仍要过 is_public_5xx_detail_safe；命中路径/堆栈/SQL 时照旧掩码。
        if exc.status_code >= 500 and isinstance(raw_detail, str):
            trace_id = getattr(request.state, "trace_id", None) or "unknown"
            original = describe_exception(exc, limit=2000)
            if isinstance(exc, UserReadableHTTPException) and is_public_5xx_detail_safe(
                raw_detail
            ):
                logger.warning(
                    "http error public status=%s path=%s trace_id=%s original=%s",
                    exc.status_code,
                    request.url.path,
                    trace_id,
                    original,
                )
                detail = sanitize_user_copy(raw_detail).strip() or raw_detail
                return JSONResponse(
                    status_code=exc.status_code,
                    content={"detail": detail},
                    headers=getattr(exc, "headers", None),
                )
            logger.error(
                "http error masked status=%s path=%s trace_id=%s original=%s",
                exc.status_code,
                request.url.path,
                trace_id,
                original,
            )
            return JSONResponse(
                status_code=exc.status_code,
                content=internal_error_body(trace_id),
                headers=getattr(exc, "headers", None),
            )

        # 边界翻译：内部治理术语 / 政策编号 / 脚本路径 / 绝对路径不上屏。
        # 原始英文只进日志，面向用户的 detail 一律是"发生了什么 + 下一步"。
        detail = user_facing_detail(raw_detail, exc.status_code)
        if detail != raw_detail:
            logger.warning(
                "http error copy sanitized status=%s path=%s original=%s",
                exc.status_code,
                request.url.path,
                describe_exception(exc, limit=2000),
            )
        body = structured_error_body(detail)
        if body is not None:
            return JSONResponse(
                status_code=exc.status_code,
                content=body,
                headers=getattr(exc, "headers", None),
            )
        # 与 Starlette 默认处理器保持同一形状（``{"detail": ...}``），只换文案。
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": detail},
            headers=getattr(exc, "headers", None),
        )

    # 统一 422：FastAPI 默认处理器会把 ``input`` 原样回显（含口令/恢复码）。
    # 这里改成脱敏后的形状，敏感字段只保留 loc/msg/type。
    @app.exception_handler(RequestValidationError)
    async def request_validation_handler(
        request: Request, exc: RequestValidationError
    ):
        labels = _collect_validation_labels(request)
        return JSONResponse(
            status_code=422,
            content=jsonable_encoder(
                {"detail": public_validation_errors(exc.errors(), labels)}
            ),
        )

    # 兜底 500 的文案必须是**可行动**的：说清哪里坏了 + 怎么查 + 怎么办。
    # 此前这里是 "An unexpected error occurred" —— 与 "Internal Server Error"
    # 是同一类：说了一半（"坏了"）却不说另外两半，操作者只能干瞪眼。这里
    # 给出三件东西：真实异常（诚实，不为了统一而吞细节）、一个真能检索到
    # 日志的 trace_id（由 logging_middleware 写入 request.state）、以及下一步
    # 动作。完整堆栈**只**进服务端日志，不进响应体。
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        trace_id = getattr(request.state, "trace_id", None) or "unknown"
        logger.error(
            "Unhandled exception trace_id=%s path=%s method=%s: %s",
            trace_id,
            request.url,
            request.method,
            describe_exception(exc, limit=2000),
            exc_info=True,
        )
        # Never echo the exception text to the caller: internal messages leak
        # table names, paths, connection strings and business data to whoever
        # can trigger the 500. The client gets a stable message + trace_id;
        # the full exception and stack go to the server log only.
        return JSONResponse(
            status_code=500,
            content=internal_error_body(trace_id),
            # The logging middleware only decorates responses that return
            # normally; an exception response must still carry the same
            # breadcrumb the body names, otherwise the two disagree.
            headers={"X-Trace-ID": trace_id},
        )

    @app.exception_handler(ValueError)
    async def value_error_handler(request: Request, exc: ValueError):
        # 原始 ValueError 往往是英文实现细节（``Invalid ...``、字段名、路径），
        # 直接回显就是让用户读栈。技术细节只进服务端日志，客户端拿到中性中文。
        logger.warning(
            "请求参数校验失败 path=%s method=%s: %s",
            request.url.path,
            request.method,
            exc,
        )
        return JSONResponse(
            status_code=400,
            content={
                "error": "validation_error",
                "detail": "提交的内容格式不正确，本次操作没有完成。请检查填写项后重试。",
            },
        )

    # Policy Controlled (C-2/C-3). A deferred action is NOT forbidden -- it is
    # awaiting a verified human approval grant (OD-010), so 409. A hard deny
    # (fail-closed: the engine could not even be consulted) is 403. Operators
    # must be able to tell those two apart without reading the audit log.
    #
    # The body must also be *honest about the remedy*. Measured 2026-09-12:
    # issuing a grant and then retrying over HTTP still yields 409
    # (no-grant=defer -> after-grant=defer -> inside-window=allow), because
    # ``_adjudicate`` reads the sovereignty *contextvar* and nothing consumes
    # the grant store at runtime. Per design decision §10.8.3-1 this gateway
    # deliberately exposes no route that executes a kernel action, so the copy
    # names the in-process mechanism instead of promising a retry that cannot
    # succeed. Pinned by tests/test_policy_approval_http.py.
    @app.exception_handler(PolicyDeferredError)
    async def policy_deferred_handler(request: Request, exc: PolicyDeferredError):
        logger.warning(
            "policy deferred action=%s rule=%s path=%s",
            exc.action, exc.rule_id, request.url.path,
        )
        return JSONResponse(
            status_code=409,
            content={
                "error": "policy_approval_required",
                "detail": (
                    "该操作需要人工审批，本次操作没有完成。请到「审批中心」记录"
                    "这次授权（谁、哪些动作、有效期到什么时候）；记录授权本身"
                    "不会直接放行，实际执行仍需在授权范围内完成。重复调用不会"
                    "改变结果。"
                ),
                "action": exc.action,
                "verdict": exc.verdict,
                "rule": exc.rule_id,
            },
        )

    @app.exception_handler(PolicyDeniedError)
    async def policy_denied_handler(request: Request, exc: PolicyDeniedError):
        logger.warning(
            "policy denied action=%s verdict=%s rule=%s path=%s",
            exc.action, exc.verdict, exc.rule_id, request.url.path,
        )
        return JSONResponse(
            status_code=403,
            content={
                "error": "policy_denied",
                # 原文案只说"被拒了"，不说"下一步能做什么"，操作者会去重试
                # 或去找一个并不存在的放行端点。这里把"没有放行入口"也说
                # 出来 —— 知道此路不通本身就是可行动的信息。
                "detail": (
                    "该操作被系统策略拒绝，本次操作没有完成。重复尝试不会改变"
                    "结果；如确需执行，请联系管理员调整授权后再试。"
                ),
                "action": exc.action,
                "verdict": exc.verdict,
                "rule": exc.rule_id,
            },
        )

    # ==================== Include routers ====================

    _startup_mark("中间件注册完成，开始注册路由")

    # 鉴权闸门：单个依赖实例，供下面所有"登录后才能用"的路由复用。
    #
    # 为什么在 include_router 这一层挂，而不是逐个端点加参数：
    #   1) 一处声明 = 一处可审计。逐个端点加签名，漏掉一个就是一个洞，而且
    #      新增端点的人不会知道自己漏了；挂在这里，新增路由必须显式选择
    #      "公开"还是"走闸门"，漏挂会立刻在测试里暴 401。
    #   2) 与前端一致：console 的 Login.tsx/auth.ts 早已做了登录墙，
    #      后端此前没跟上 —— 这是"前端有门、后端有洞"的修复。
    #
    # 公开（**故意不挂**）：health(/v1/health /ready /metrics)、
    # 生产化就绪(/v1/metrics/prometheus /v1/ready/subsystems)、auth(/v1/auth/*)、
    # 以及同源静态驾驶舱（登录页本身必须能在未登录时加载）。
    from .policy import require_human_principal as _require_human
    from .account_context import (
        deny as _deny_account,
        require_account_principal as _require_account,
    )

    # 设置类写操作的收敛闸门：成员可读，写只归根账号。
    #
    # 为什么按方法而不是逐端点挂：``/v1/settings/logs`` 与 ``/v1/network/research``
    # 都是"读同一个资源 + 写同一个资源"的 REST 形状，读要留给成员（他们需要知道
    # 当前策略），写必须收归根账号。挂在 include_router 这一层，新增的写端点会
    # 自动继承，不会因为忘了加依赖而重新开口子。
    #
    # 拒绝走 ``account_context.deny``：稳定错误码 + 顶层 ``code``/``message``，
    # 前端能直接读出"只能查看、不能修改"，而不是一句无信息的 403。
    def _require_settings_owner(
        request: Request, context=Depends(_require_account)
    ):
        if request.method.upper() in {"GET", "HEAD", "OPTIONS"}:
            return context
        if not context.is_root:
            raise _deny_account(
                403,
                "SETTINGS_ROOT_ONLY",
                "该设置由最高权限账号统一管理，当前账号只能查看；修改没有生效。",
            )
        return context

    app.include_router(health_router)

    # 启动状态（只读、仅本机回环）：前端在网关启动期间用它显示"正在启动"。
    # 对外监听时远端请求不会拿到启动阶段/耗时/降级组件名。
    # 契约见 src/gateway/startup.py 的模块文档与 docs/startup-profile.md。
    from .startup import router as startup_router

    app.include_router(startup_router)

    app.include_router(api_router, prefix="/api")

    # 生产化就绪端点（Phase 7a）：/v1/metrics/prometheus、/v1/ready/subsystems
    # 公开；/v1/production/preflight 内部自带 require_human_principal 闸门。
    from .observability import router as observability_router
    app.include_router(observability_router)
    _startup_mark("路由：health / observability 完成")

    # 一键诊断包：版本/提交号/系统架构/安装方式/脱敏数据目录/健康与自检/日志尾部。
    # 内容按白名单生成，不含任何密钥（见 src/gateway/diagnostic_bundle.py）。
    from .diagnostic_bundle import router as diagnostics_router
    app.include_router(diagnostics_router, dependencies=[Depends(_require_human)])
    _startup_mark("路由：diagnostics 完成")

    # 日志占用与保留设置：成员只读，保存/清理仅根账号（持久化到数据目录）。
    from .log_settings import router as log_settings_router
    app.include_router(log_settings_router, dependencies=[Depends(_require_settings_owner)])
    _startup_mark("路由：log settings 完成")

    # 工作台名字：GET 公开（登录页未登录时也要显示名字），PUT 仅根账号
    # （写闸门在端点内用 require_account_principal 收敛，见 gateway/workspace.py）。
    from .workspace import router as workspace_router
    app.include_router(workspace_router)
    _startup_mark("路由：workspace name 完成")

    # Knowledge base: canonical /v1/knowledge (unified with the rest of the
    # gateway) plus a deprecated /knowledge alias for existing clients.
    from src.api.routes.knowledge import (
        legacy_router as knowledge_legacy_router,
        router as knowledge_router,
    )
    app.include_router(knowledge_router, dependencies=[Depends(_require_human)])
    app.include_router(knowledge_legacy_router, dependencies=[Depends(_require_human)])

    # 企业知识库：路由自带走令牌鉴权（require_viewer），这里不再套 _require_human。
    from .knowledge_enterprise import router as knowledge_enterprise_router
    app.include_router(knowledge_enterprise_router)
    _startup_mark("路由：knowledge 完成")

    # 贾维斯对话端点（真实 LLM 对话闭环）。
    from .chat import router as chat_router
    app.include_router(chat_router, dependencies=[Depends(_require_human)])
    _startup_mark("路由：chat 完成")

    # 驾驶舱遥测端点（console CEO Command Center 的真实数据源）。
    from .dashboard import router as dashboard_router
    app.include_router(dashboard_router, dependencies=[Depends(_require_human)])

    # AI 员工名册端点（真实注册表：14 内核 + 14 能力层 + provider 面）。
    from .roster import router as roster_router
    app.include_router(roster_router, dependencies=[Depends(_require_human)])

    # 个人画像端点（KAREN Personal Intelligence 的真实读写面）。
    from .profile import router as profile_router
    app.include_router(profile_router, dependencies=[Depends(_require_human)])

    # 内核生命周期可观测端点（14 内核 lifecycle 实时快照，受人类主权闸门保护）。
    from .kernels import kernels_router
    app.include_router(kernels_router, dependencies=[Depends(_require_human)])

    # AI Employee / Goal / Workflow 管理端点（真实 AgentRuntime + Employee 状态面）。
    from .ai_management import router as ai_mgmt_router
    app.include_router(ai_mgmt_router, dependencies=[Depends(_require_human)])

    # 工作流编排与人工审批：复用 ai_management 的 _goal_viewer 作为可见范围口径。
    # 工作流走 /v1/workflow（单数），避免与上面 ai_management 的
    # /v1/workflows/{goal_id} 抢占同一路径段。
    from .workflow import build_router as _build_workflow_router
    from .approvals import build_router as _build_approval_router
    from .ai_management import _goal_viewer as _workflow_viewer
    app.include_router(
        _build_workflow_router(_workflow_viewer),
        dependencies=[Depends(_require_human)],
    )
    app.include_router(
        _build_approval_router(_workflow_viewer),
        dependencies=[Depends(_require_human)],
    )
    _startup_mark("路由：ai_management / dashboard / roster / profile / kernels 完成")

    # 审计可解释性查询面（P8）：真实哈希链的**只读**查询/校验入口。
    # 只读是硬约束：任何"修复链"的能力都会摧毁 tamper-evidence（见 audit.py
    # 模块 docstring）。与其余业务面一样挂人类主权闸门 —— 审计轨迹不匿名可读。
    from .audit import router as audit_router
    app.include_router(audit_router, dependencies=[Depends(_require_human)])

    # 身份 / 权限可解释性面（P9 Permissions / Identity UX）：把**已经存在**的身份
    # 内核摊开给人看 —— 谁存在、每个主体持有哪些权限。真实来源只有
    # src/kernels.identity，没有任何在本层重建的主体表。
    # 只读是硬约束：加一层 HTTP 赋权/撤权端点就是新增赋权面，而这条路径此刻没有
    # 人类复核兜底（见 identity.py 模块 docstring）。与其余业务面一样挂人类主权
    # 闸门 —— 权限账本不匿名可读。
    from .identity import router as identity_router
    app.include_router(identity_router, dependencies=[Depends(_require_human)])

    # 工作区文件可观测面（P1 产品缺口 #1）：把 AI 员工**真实写出的产物**摊开给
    # 人看 —— 浏览/读取工作区内的文件。严格只读：本模块没有任何写入口，写仍由
    # 执行内核的 file_write 工具经执行围栏负责，避免开一条绕过审计的落盘侧门。
    # 路径统一经 src.ai.workspace.resolve_in_workspace 收敛到工作区根之内，
    # 越界即 400。与其余业务面一样挂人类主权闸门 —— 工作区内容不匿名可读。
    from .files import router as files_router
    app.include_router(files_router, dependencies=[Depends(_require_human)])

    # Projects 面（Top-3 #1 缺口之一）：把目标归类成人类可管理的项目容器。
    # 人类主权创建/读取/删除，挂 require_human_principal 闸门。
    from .projects import router as projects_router
    app.include_router(projects_router, dependencies=[Depends(_require_human)])

    # 组织架构数据面（企业工作台 P3A）：任意层级组织/岗位/任职 + 版本与影响检查。
    # 与 Projects 面一致挂人类主权闸门；组织数据按账号归属隔离，写入只作用于自己。
    from .org import router as org_router
    app.include_router(org_router, dependencies=[Depends(_require_human)])

    # 组织架构模板：模板只作参考，预览/套用必须由人触发；同样挂人类主权闸门。
    from .org_templates import router as org_templates_router
    app.include_router(org_templates_router, dependencies=[Depends(_require_human)])

    # Apps 面（Top-3 #2 缺口）：把"插件"真实呈现给人类，并允许激活（真实加载）。
    # 与 Projects 面一致，挂 require_human_principal 人类主权闸门。
    from .plugins import router as plugins_router
    app.include_router(plugins_router, dependencies=[Depends(_require_human)])

    from .skills import router as skills_router
    app.include_router(skills_router, dependencies=[Depends(_require_human)])

    # 事件流面（LHX-C-008 缺口）：把事件内核总线里**真实发生**的近期事件摊开给人看，
    # 只读、人类主权闸门。后端直接复用 event kernel 的内存近期事件缓冲
    # get_event_history，绝不另建一份可能分叉/造假的环形缓冲。与 Projects/Apps 面
    # 一致挂 require_human_principal 闸门。
    from .events import router as events_router
    app.include_router(events_router, dependencies=[Depends(_require_human)])

    # 信任只读面（LHX-C-010 缺口）：把信任内核的**真实**状态摊开给人看 ——
    # 撤销标记、各作用域信任分、自信任链探针、管理器统计。只读、人类主权闸门。
    # 与其余业务面一致挂 require_human_principal 闸门。
    from .trust import router as trust_router
    app.include_router(trust_router, dependencies=[Depends(_require_human)])

    # 网络只读面（LHX-C-009 缺口）：把网络总线里**真实发生**的消息与统计摊开给人看，
    # 只读、人类主权闸门。后端直接复用网络内核进程单例的 stats() 与 get_message_history()，
    # 绝不另建一份可能分叉/造假的副本。与其余业务面一致挂 require_human_principal 闸门。
    # 消息面严格只读；联网检索开关是本地偏好：成员可读，写入仅根账号。
    from .networks import router as networks_router
    app.include_router(networks_router, dependencies=[Depends(_require_settings_owner)])

    # 模型管理面（模型配置中心）：界面新增/编辑/删除/设为默认 + 连通性测试。
    # 读端点对所有已登录人类开放；写端点由 router 内的 require_model_admin
    # 单独校验收敛（admin 权限或 L0 人类主权）。密钥只以脱敏形式返回。
    from .models import router as models_router
    app.include_router(models_router, dependencies=[Depends(_require_human)])
    _startup_mark("路由：models（模型配置中心）完成")

    # 升级管理面（状态/检查/升级/回滚）：界面不再需要命令行。
    # 闸门沿用 require_human_principal；router 内部再收敛到 L0/L7 主权或
    # admin 角色，并对 apply/rollback 强制二次确认。api 模块只做轻量导入，
    # 重模块在请求内延迟加载（见 src/upgrade/api.py），冷启动增量约 20ms。
    from ..upgrade.api import router as upgrade_router
    app.include_router(upgrade_router, dependencies=[Depends(_require_human)])
    _startup_mark("路由：upgrade（升级管理）完成")

    # 账号树面（注册 / 成员 / 权限 / Key 共享 / 状态 / 恢复 / 备份）。
    # 不挂全局人类闸门：``/register``、``/activate``、``/recovery/reset``、
    # ``/backup/import``、``/roles`` 必须在登录前可用（首个注册者就是主账号）；
    # 其余端点在 router 内逐个依赖 ``require_account_principal``（令牌 → 主体），
    # 未鉴权即 401，越权一律中性 404/403。挂载顺序必须早于静态驾驶舱的 404 兜底。
    from .accounts import router as accounts_router
    app.include_router(accounts_router)
    _startup_mark("路由：accounts（账号树）完成")

    # Owner（超级管理员）面：全局总览 / 全部成员 / 全部任务 / 审计。
    # 无公开端点，全部走 require_owner（非 Owner 一律 403）；再套一层人类
    # 闸门做纵深防御。Owner 身份只来自账号树根位置，不来自请求体或令牌 claim。
    from .owner import router as owner_router
    app.include_router(owner_router, dependencies=[Depends(_require_human)])
    _startup_mark("路由：owner（超级管理员）完成")

    # 记忆面（列表 / 检索 / 导出 / 代管开关）。每个端点都按令牌主体 + 账号树
    # 收敛 owner：别人的条目一律 404；主账号只读聚合整棵子树，不提供改写入口。
    from .account_memory import router as memory_router
    app.include_router(memory_router)
    _startup_mark("路由：memory（账号记忆）完成")

    # 数据可携带性：一键导出「我的全部数据」（人可读 ZIP）、会话 Markdown 导出，
    # 以及记忆 / 知识库的回导。所有端点都按令牌主体过滤，根账号也不在这里
    # 聚合成员数据；凭据、Key 与内部路径一律不出现（见 data_portability.py）。
    from .data_portability import (
        accounts_router as data_accounts_router,
        chat_router as data_chat_router,
        knowledge_router as data_knowledge_router,
        memory_router as data_memory_router,
    )
    app.include_router(data_accounts_router)
    app.include_router(data_chat_router, dependencies=[Depends(_require_human)])
    app.include_router(data_knowledge_router, dependencies=[Depends(_require_human)])
    app.include_router(data_memory_router, dependencies=[Depends(_require_human)])
    _startup_mark("路由：data portability（导出/回导）完成")

    # Policy Controlled 审批端点（内核层真拦截的人工授权入口，C-4）。
    # 该 router 内部已对每个端点声明 require_human_principal，这里不重复挂。
    from .policy import router as policy_router
    app.include_router(policy_router)

    # 登录端点（凭据 -> JWT）。三端（桌面/网页/手机）共用同一会话。
    from .auth import router as auth_router
    app.include_router(auth_router)

    # 手机设备的会话换发：只对「回环 + 中继共享密钥」签发，手机永远不输密码。
    from .device_sessions import router as device_auth_router
    app.include_router(device_auth_router)

    # 同一 Wi-Fi 手机访问开关（默认关闭；只绑一个局域网地址，不是 0.0.0.0）。
    from .lan_access import router as lan_access_router
    app.include_router(lan_access_router)

    # 单端口化：把驾驶舱构建产物挂到同源根路径，供桌面/网页/手机三端复用。
    # 机制是 **404 异常处理器**（见 console_static.py）：只在没有任何路由匹配时
    # 被调用，因此不可能遮蔽任何 API 路由，也与注册时机无关。没有构建产物时
    # 自动跳过，API-only 部署行为不变。
    from .console_static import mount_console
    mount_console(app)

    _startup_mark("全部路由 + 静态驾驶舱挂载完成")

    return app


# Module-level app for convenience
settings = None
try:
    from ..config_manager import get_config as _get_config
    settings = _get_config()
except Exception:
    pass

app: Optional[FastAPI] = None

if settings:
    app = get_app()
