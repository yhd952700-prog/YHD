"""
Main FastAPI Application for LiuHao AI OS Gateway

Provides the unified entry point for all services with:
- Lifecycle management (startup/shutdown)
- Middleware stack (CORS, compression, tracing)
- Unified exception handling
- Router inclusion
"""

import time
import logging
import os
import threading
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

from fastapi import FastAPI, Request, APIRouter, Depends  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.middleware.gzip import GZipMiddleware  # noqa: E402
from fastapi.responses import JSONResponse, PlainTextResponse  # noqa: E402
from fastapi.encoders import jsonable_encoder  # noqa: E402
from fastapi.exceptions import RequestValidationError  # noqa: E402
from starlette.exceptions import HTTPException as StarletteHTTPException  # noqa: E402

from ..security import get_jwt_handler, get_rbac_manager  # noqa: E402
# 注意：``..observability.tracing``（OpenTelemetry SDK，实测约 0.3s）**不在**
# 顶层导入 —— 追踪只服务可观测性，不属于"登录/对话/模型配置"的就绪条件。
# 它在 lifespan 末尾随后台预热一起初始化，失败会告警并出现在 /v1/startup。

# HTTP request metrics (families declared in ``src/observability/metrics.py``).
# Historically these were never fed, so ``/v1/metrics/prometheus`` exported their
# families with **zero per-request samples** -- a scrape looked instrumented while
# carrying no request data. Importing them here lets ``logging_middleware`` feed
# them with real traffic. Endpoint labels come from the matched route template
# (see ``record_http_request``) so dynamic ids cannot grow metric cardinality.
try:  # pragma: no cover - depends on the optional prometheus_client package
    from ..observability.metrics import (
        record_http_request as _RECORD_HTTP_REQUEST,
    )
except Exception:  # metrics unavailable here; the endpoint 503s anyway
    _RECORD_HTTP_REQUEST = None

# Real metric collector that feeds the alert engine (OB / blocker #3). The
# collector is independent of prometheus_client so the alert loop works even
# when prometheus_client is absent.
try:  # pragma: no cover - depends on the metrics module being importable
    from ..observability.metrics import get_metric_collector as _get_metric_collector
except Exception:  # metrics unavailable; middleware becomes a no-op
    _get_metric_collector = None

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
                or ["Authorization", "Content-Type", "X-Request-Id", "X-Liuhao-Token"]
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

    def __init__(self, app, allowed_hosts, allowed_origins=(), allow_ip_literals=False):
        self.app = app
        self.allowed_hosts = {str(h).lower() for h in allowed_hosts if h}
        # 只保留**完整** Origin；裸主机名/IP/``*`` 不会被当成来源白名单。
        self.allowed_origins = set()
        for item in allowed_origins or ():
            value = canonical_origin(str(item))
            if value:
                self.allowed_origins.add(value)
        self.allow_ip_literals = bool(allow_ip_literals)

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

    def _origin_allowed(self, origin: str) -> bool:
        """只做完整来源精确匹配；主机名命中白名单**不足以**放行。"""
        value = canonical_origin(origin)
        return bool(value) and value in self.allowed_origins

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
        if method in _WRITE_METHODS and origin and not self._origin_allowed(origin):
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

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle management for the gateway."""

    # Startup
    _startup_begin_run()
    logger.info("Starting LiuHao AI OS Gateway...")
    _startup_mark("lifespan 启动开始")

    # Initialize security managers
    from ..security.api_keys import get_api_key_manager as _get_key_mgr
    key_mgr = _get_key_mgr()
    logger.info(f"API Key Manager initialized: {key_mgr.get_key_stats()['total']} keys")

    get_jwt_handler()
    logger.info("JWT Handler initialized")

    rbac_mgr = get_rbac_manager()
    logger.info(f"RBAC Manager initialized: {len(rbac_mgr._roles)} roles")

    _startup_mark("密钥 / JWT / RBAC 初始化完成")

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
            "(HIGH/CRITICAL kernel actions will be REJECTED)",
            _enf["env_var"], _enf["spec"], _enf["config_error"],
        )
    elif _enf["enabled"]:
        logger.warning(
            "Kernel policy enforcement ARMED: %s=%r -> %s",
            _enf["env_var"], _enf["spec"], ", ".join(_enf["enforced_actions"]),
        )
    else:
        logger.info(
            "Kernel policy enforcement off (%s unset) -- record-only / L1",
            _enf["env_var"],
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
    from ..kernels._registry import initialize_all, shutdown_all

    _startup_mark("内核生命周期 initialize_all 开始")
    _init_report = initialize_all()
    _startup_mark("内核生命周期 initialize_all 完成")
    _init_initialized = _init_report.get("initialized", [])
    _init_skipped = _init_report.get("skipped_not_present", [])
    _init_no_canonical = _init_report.get("no_canonical_instance", [])
    _init_errors = _init_report.get("errors", [])
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
        from ..observability.metrics import get_metric_collector
        from ..observability.alerts import get_alert_manager

        _metrics_stop = threading.Event()
        app.state.metrics_evaluator_stop = _metrics_stop

        def _metrics_evaluator() -> None:
            collector = get_metric_collector()
            manager = get_alert_manager()
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

    # 核心就绪：登录（密钥/JWT/RBAC/身份）、对话路由、模型配置路由此时都已
    # 真实可用。下面的"对话链预热"只是让第一次发消息不必再等导入，**不**阻塞
    # 就绪；预热失败必须响（日志 + /v1/startup.background.errors），绝不能
    # 悄悄变成"第一次发消息才 500"。
    _startup_ready()

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

    def _warm_chat_stack() -> None:
        _bg_started()
        _run_background_step("初始化追踪（OpenTelemetry SDK）", _warm_observability)
        _run_background_step("预热对话链（LLM/provider/会话存储）", _warm_chat)
        _bg_finished()

    _warm_thread = threading.Thread(
        target=_warm_chat_stack, name="chat-stack-warmup", daemon=True
    )
    _warm_thread.start()
    app.state.chat_warmup_thread = _warm_thread
    _startup_mark("核心就绪（登录/对话/模型配置可用），对话链转入后台预热")

    _startup_report()

    yield  # App runs here

    # Shutdown
    logger.info("Shutting down LiuHao AI OS Gateway...")

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


def public_validation_errors(errors) -> list:
    """把 Pydantic 校验错误脱敏后再回给客户端。

    Pydantic 默认把 ``input`` 原样带出：短口令会以
    ``{"loc": ["body","password"], "input": "S3cret"}`` 直接回显。敏感字段
    （口令 / 恢复码 / 口令短语 / 令牌 / 凭据包）以及整个请求体一律不返回
    ``input``/``ctx``；其余字段保持默认形状，便于前端定位。
    """
    cleaned = []
    for err in errors or ():
        loc = list(err.get("loc") or ())
        names = [str(part).lower() for part in loc]
        item = {"loc": loc, "msg": err.get("msg"), "type": err.get("type")}
        exposes_secret = (
            not names
            # ``loc == ["body"]`` 时 input 是整个请求体，可能同时含口令。
            or (names[0] in ("body", "json") and len(names) <= 1)
            or any(part in _SENSITIVE_VALIDATION_FIELDS for part in names)
        )
        if not exposes_secret:
            if "input" in err:
                item["input"] = err["input"]
            if "ctx" in err:
                item["ctx"] = err["ctx"]
        cleaned.append(item)
    return cleaned


def get_app() -> FastAPI:
    """
    Create and configure the FastAPI application.

    Returns:
        Configured FastAPI instance
    """
    _startup_mark("get_app 开始")
    runtime = load_runtime_config()
    production = bool(runtime["production"])
    _startup_mark("运行配置加载完成（config/default.yaml + APP_ENV）")

    # Policy Controlled: an enforced kernel action raises these. Map them to
    # honest HTTP statuses instead of letting the catch-all handler turn
    # "needs a human approval" into a 500 internal error.
    from ..kernels._crosscutting import PolicyDeferredError, PolicyDeniedError

    app = FastAPI(
        title="LiuHao AI OS Gateway",
        version="1.0.0",
        description="Unified API Gateway for LiuHao AI OS",
        lifespan=lifespan,
        # OpenAPI/docs are a broad reconnaissance surface (every route, every
        # schema, every query parameter). Development keeps them; production
        # turns them off regardless of what the legacy config key says.
        docs_url=None if production else "/docs",
        redoc_url=None if production else "/redoc",
        openapi_url=None if production else "/openapi.json",
    )
    if production:
        logger.info(
            "Debug API surface DISABLED (APP_ENV=production): "
            "/docs, /redoc, /openapi.json return 404"
        )
    else:
        logger.warning(
            "Debug API surface ENABLED (APP_ENV=%s): /docs, /redoc, "
            "/openapi.json are readable on this listener -- set "
            "APP_ENV=production to turn them off",
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
    from .http_limits import BodyLimitMiddleware

    app.add_middleware(
        BodyLimitMiddleware,
        max_body_bytes=int(limits.get("max_body_bytes") or 0),
        max_upload_bytes=int(limits.get("max_upload_bytes") or 0),
    )

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

    # GZip compression
    app.add_middleware(GZipMiddleware, minimum_size=1000)

    # Host 白名单 + 写方法 Origin 校验（最外层安全闸，见类文档）。
    # 不论 CORS 是否启用都必须挂：两者保护的不是同一件事。
    # 回环来源只在开发态**显式**加入（精确到端口）；生产只认配置里的域名。
    guard_origins = list(cors["allow_origins"])
    if not production:
        guard_origins.extend(development_loopback_origins())
    app.add_middleware(
        HostGuardMiddleware,
        allowed_hosts=runtime["trusted_hosts"],
        allowed_origins=guard_origins,
        allow_ip_literals=runtime["host_guard_allow_ip_literals"],
    )
    logger.info(
        "Host allow-list (DNS-rebinding guard): %s",
        ", ".join(runtime["trusted_hosts"]),
    )

    # ==================== Request logging middleware ====================

    @app.middleware("http")
    async def logging_middleware(request: Request, call_next):
        start_time = time.time()

        # Trace ID from headers or generate new
        trace_id = request.headers.get("X-Trace-ID", "")
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
        response.headers["X-Process-Time"] = str(process_time)
        response.headers["X-Trace-ID"] = trace_id

        # Feed the HTTP request metrics so a scrape carries real per-request
        # samples. The label is the MATCHED ROUTE TEMPLATE, never the raw path:
        # a raw path embeds unbounded ids and every new id would permanently
        # add a label set to every HTTP histogram (measured in the 22-minute
        # soak). Instrumentation must never break the request itself, hence the
        # guard.
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
                    process_time, response.status_code
                )
            except Exception as exc:  # pragma: no cover - never fail a request
                logger.warning(f"metric recording failed: {exc}")
        return response

    # ==================== Exception handlers ====================

    # 账号面／策略面用 ``HTTPException(detail={"code","message"})`` 表达稳定错误码。
    # 默认 FastAPI 只吐 ``{"detail": {...}}``，驾驶舱读的是顶层 ``code``；这里把
    # 结构化错误**同时**摊到顶层（``code`` / ``message`` / ``detail``），非结构化
    # detail（普通字符串）完全沿用默认行为，不影响既有端点。
    from .account_context import structured_error_body
    from .display import user_facing_detail

    @app.exception_handler(StarletteHTTPException)
    async def structured_http_error_handler(request: Request, exc: StarletteHTTPException):
        # 边界翻译：内部治理术语 / 政策编号 / 脚本路径不上屏。原始英文只进日志，
        # 面向用户的 detail 一律是"发生了什么 + 下一步"的中性中文。
        detail = user_facing_detail(exc.detail, exc.status_code)
        if detail != exc.detail:
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
        return JSONResponse(
            status_code=422,
            content=jsonable_encoder(
                {"detail": public_validation_errors(exc.errors())}
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
            content={
                "error": "internal_server_error",
                "detail": (
                    "服务端处理这个请求时遇到意外问题，本次操作没有完成。"
                    "请稍后重试；如果反复出现，请导出诊断包，"
                    "并把下面的追踪编号一起提供给技术支持。"
                ),
                "trace_id": trace_id,
            },
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

    app.include_router(health_router)

    # 启动状态（只读、免鉴权）：前端在网关启动期间用它显示"正在启动"。
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

    # Knowledge base: canonical /v1/knowledge (unified with the rest of the
    # gateway) plus a deprecated /knowledge alias for existing clients.
    from src.api.routes.knowledge import (
        legacy_router as knowledge_legacy_router,
        router as knowledge_router,
    )
    app.include_router(knowledge_router, dependencies=[Depends(_require_human)])
    app.include_router(knowledge_legacy_router, dependencies=[Depends(_require_human)])
    _startup_mark("路由：knowledge 完成")

    # 鎏灏对话端点（真实 LLM 对话闭环）。
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

    # Apps 面（Top-3 #2 缺口）：把"插件"真实呈现给人类，并允许激活（真实加载）。
    # 与 Projects 面一致，挂 require_human_principal 人类主权闸门。
    from .plugins import router as plugins_router
    app.include_router(plugins_router, dependencies=[Depends(_require_human)])

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
    # 严格只读：不建任何 send/写端点（真实外部 send 是安全风险且无真实路径）。
    from .networks import router as networks_router
    app.include_router(networks_router, dependencies=[Depends(_require_human)])

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

    # 记忆面（列表 / 检索 / 导出 / 代管开关）。每个端点都按令牌主体 + 账号树
    # 收敛 owner：别人的条目一律 404；主账号只读聚合整棵子树，不提供改写入口。
    from .account_memory import router as memory_router
    app.include_router(memory_router)
    _startup_mark("路由：memory（账号记忆）完成")

    # Policy Controlled 审批端点（内核层真拦截的人工授权入口，C-4）。
    # 该 router 内部已对每个端点声明 require_human_principal，这里不重复挂。
    from .policy import router as policy_router
    app.include_router(policy_router)

    # 登录端点（凭据 -> JWT）。三端（桌面/网页/手机）共用同一会话。
    from .auth import router as auth_router
    app.include_router(auth_router)

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
