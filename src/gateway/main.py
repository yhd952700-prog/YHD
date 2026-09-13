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
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Request, APIRouter
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse

from ..config_manager import get_config
from ..security import get_jwt_handler, get_rbac_manager
from ..observability.tracing import get_tracer

logger = logging.getLogger(__name__)


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
    logger.info("Starting LiuHao AI OS Gateway...")

    # Initialize security managers
    from ..security.api_keys import get_api_key_manager as _get_key_mgr
    key_mgr = _get_key_mgr()
    logger.info(f"API Key Manager initialized: {key_mgr.get_key_stats()['total']} keys")

    get_jwt_handler()
    logger.info("JWT Handler initialized")

    rbac_mgr = get_rbac_manager()
    logger.info(f"RBAC Manager initialized: {len(rbac_mgr._roles)} roles")

    # Initialize observability
    get_tracer()
    logger.info("Tracer initialized")

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
    from ..kernels.identity import get_identity_manager, is_human_identity
    _identity_manager = get_identity_manager()
    _store = _identity_manager.describe_store()
    _humans = sorted(
        identity.principal
        for identity in _identity_manager.list_identities()
        if is_human_identity(identity)
    )
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

    logger.info("Gateway startup complete")

    yield  # App runs here

    # Shutdown
    logger.info("Shutting down LiuHao AI OS Gateway...")
    logger.info("Gateway shutdown complete")


# ==================== FastAPI Application ====================

def get_app() -> FastAPI:
    """
    Create and configure the FastAPI application.

    Returns:
        Configured FastAPI instance
    """
    settings = get_config()

    # Policy Controlled: an enforced kernel action raises these. Map them to
    # honest HTTP statuses instead of letting the catch-all handler turn
    # "needs a human approval" into a 500 internal error.
    from ..kernels._crosscutting import PolicyDeferredError, PolicyDeniedError

    app = FastAPI(
        title="LiuHao AI OS Gateway",
        version="1.0.0",
        description="Unified API Gateway for LiuHao AI OS",
        lifespan=lifespan,
        docs_url="/docs" if settings.get("api", {}).get("swagger_ui", True) else None,
        redoc_url="/redoc" if settings.get("api", {}).get("swagger_ui", True) else None,
    )

    # ==================== Middleware ====================

    # CORS middleware
    cors_origins = settings.get("api", {}).get("cors_origins", ["*"])
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # GZip compression
    app.add_middleware(GZipMiddleware, minimum_size=1000)

    # ==================== Request logging middleware ====================

    @app.middleware("http")
    async def logging_middleware(request: Request, call_next):
        start_time = time.time()

        # Trace ID from headers or generate new
        trace_id = request.headers.get("X-Trace-ID", "")
        if not trace_id:
            import uuid
            trace_id = str(uuid.uuid4())

        response = await call_next(request)

        process_time = time.time() - start_time
        response.headers["X-Process-Time"] = str(process_time)
        response.headers["X-Trace-ID"] = trace_id

        logger.info(
            f"{request.method} {request.url.path} "
            f"{response.status_code} {process_time:.4f}s "
            f"trace_id={trace_id}"
        )

        return response

    # ==================== Exception handlers ====================

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        logger.error(
            f"Unhandled exception: {exc}\n"
            f"Path: {request.url}\n"
            f"Method: {request.method}",
            exc_info=True,
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": "internal_server_error",
                "detail": "An unexpected error occurred",
                "trace_id": getattr(request.state, "trace_id", "unknown"),
            },
        )

    @app.exception_handler(ValueError)
    async def value_error_handler(request: Request, exc: ValueError):
        return JSONResponse(
            status_code=400,
            content={"error": "validation_error", "detail": str(exc)},
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
                    "This action requires verified human sovereignty (OD-010). "
                    "POST /v1/policy/approvals records the authorization (who, "
                    "for which actions, until when). Recording it does not by "
                    "itself unblock a retry: the gateway exposes no route that "
                    "executes a kernel action, so execution must happen "
                    "in-process inside grant_window(grant)."
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
                "detail": "The kernel policy engine denied this action.",
                "action": exc.action,
                "verdict": exc.verdict,
                "rule": exc.rule_id,
            },
        )

    # ==================== Include routers ====================

    app.include_router(health_router)
    app.include_router(api_router, prefix="/api")

    # Business routers (Phase 2.3 RAG knowledge endpoints).
    from src.api.routes.knowledge import router as knowledge_router
    app.include_router(knowledge_router)

    # 鎏灏对话端点（真实 LLM 对话闭环）。
    from .chat import router as chat_router
    app.include_router(chat_router)

    # 驾驶舱遥测端点（console CEO Command Center 的真实数据源）。
    from .dashboard import router as dashboard_router
    app.include_router(dashboard_router)

    # AI 员工名册端点（真实注册表：14 内核 + 14 能力层 + provider 面）。
    from .roster import router as roster_router
    app.include_router(roster_router)

    # 个人画像端点（KAREN Personal Intelligence 的真实读写面）。
    from .profile import router as profile_router
    app.include_router(profile_router)

    # Policy Controlled 审批端点（内核层真拦截的人工授权入口，C-4）。
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
