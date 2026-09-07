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

# Health router
health_router = APIRouter(prefix="/v1", tags=["health"])

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

    # Register health endpoints
    @health_router.get("/health", include_in_schema=False)
    async def health_check():
        return {"status": "ok", "timestamp": time.time()}

    @health_router.get("/ready", include_in_schema=False)
    async def readiness_check():
        return {"status": "ready", "timestamp": time.time()}

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

    # ==================== Include routers ====================

    app.include_router(health_router)
    app.include_router(api_router, prefix="/api")

    # Business routers (Phase 2.3 RAG knowledge endpoints).
    from src.api.routes.knowledge import router as knowledge_router
    app.include_router(knowledge_router)

    # 鎏灏对话端点（真实 LLM 对话闭环）。
    from .chat import router as chat_router
    app.include_router(chat_router)

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
