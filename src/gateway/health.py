"""
Health Check Endpoints for LiuHao AI OS Gateway

Provides:
- /health - 存活探针 (Kubernetes livenessProbe)
- /ready - 就绪探针 (Kubernetes readinessProbe)
- 依赖组件状态检查
- 详细的系统状态报告
"""

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from typing import Dict, Any, Optional
import time

from ..security import get_api_key_manager, get_jwt_handler, get_rbac_manager, get_encryption_manager
from ..gateway.rate_limiter import get_rate_limiter
from ..observability.tracing import get_tracer


health_router = APIRouter(prefix="/v1", tags=["health"])


@health_router.get("/health", include_in_schema=False)
async def liveness_probe(request: Request) -> JSONResponse:
    """
    Liveness probe - Kubernetes 检查容器是否存活。
    
    返回 200 表示容器运行正常，可以被调度器调用。
    """
    trace_id = request.headers.get("X-Trace-ID", "unknown")
    
    return JSONResponse(
        content={
            "status": "ok",
            "service": "liuhao-gateway",
            "timestamp": time.time(),
            "trace_id": trace_id,
            "version": "1.0.0",
        },
        status_code=status.HTTP_200_OK,
    )


@health_router.get("/ready", include_in_schema=False)
async def readiness_probe(request: Request) -> JSONResponse:
    """
    Readiness probe - Kubernetes 检查服务是否就绪接受流量。
    
    检查所有核心依赖组件是否就绪：
    - API Key Manager
    - JWT Handler
    - RBAC Manager
    - Encryption Manager
    - Rate Limiter
    """
    trace_id = request.headers.get("X-Trace-ID", "unknown")
    errors: list = []
    checks: Dict[str, Any] = {}
    
    # Check API Key Manager
    try:
        key_mgr = get_api_key_manager()
        key_stats = key_mgr.get_key_stats()
        checks["api_key_manager"] = {
            "status": "healthy",
            "total_keys": key_stats["total"],
            "active_keys": key_stats["by_status"].get("active", 0),
        }
    except Exception as e:
        errors.append(f"API Key Manager: {str(e)}")
        checks["api_key_manager"] = {"status": "unhealthy", "error": str(e)}
    
    # Check JWT Handler
    try:
        jwt_mgr = get_jwt_handler()
        # Quick validation test
        checks["jwt_handler"] = {"status": "healthy"}
    except Exception as e:
        errors.append(f"JWT Handler: {str(e)}")
        checks["jwt_handler"] = {"status": "unhealthy", "error": str(e)}
    
    # Check RBAC Manager
    try:
        rbac_mgr = get_rbac_manager()
        checks["rbac_manager"] = {"status": "healthy", "total_roles": len(rbac_mgr._roles)}
    except Exception as e:
        errors.append(f"RBAC Manager: {str(e)}")
        checks["rbac_manager"] = {"status": "unhealthy", "error": str(e)}
    
    # Check Encryption Manager
    try:
        em = get_encryption_manager()
        checks["encryption_manager"] = {"status": "healthy"}
    except Exception as e:
        errors.append(f"Encryption Manager: {str(e)}")
        checks["encryption_manager"] = {"status": "unhealthy", "error": str(e)}
    
    # Check Rate Limiter
    try:
        limiter = get_rate_limiter()
        checks["rate_limiter"] = {"status": "healthy"}
    except Exception as e:
        errors.append(f"Rate Limiter: {str(e)}")
        checks["rate_limiter"] = {"status": "unhealthy", "error": str(e)}
    
    # Determine overall status
    unhealthy_checks = [k for k, v in checks.items() if v.get("status") != "healthy"]
    
    overall_status = "ready" if not unhealthy_checks else "degraded"
    
    response_data = {
        "status": overall_status,
        "service": "liuhao-gateway",
        "timestamp": time.time(),
        "trace_id": trace_id,
        "checks": checks,
    }
    
    # Add error details if degraded/unhealthy
    if errors:
        response_data["errors"] = errors
    
    status_code = (
        status.HTTP_200_OK
        if overall_status == "ready"
        else status.HTTP_503_SERVICE_UNAVAILABLE
    )
    
    return JSONResponse(content=response_data, status_code=status_code)


@health_router.get("/metrics", include_in_schema=False)
async def metrics_endpoint(request: Request) -> JSONResponse:
    """
    Metrics endpoint - 返回详细的系统指标。
    
    包含：
    - 密钥统计
    - 角色统计
    - 速率限制计数
    - 服务运行时长
    """
    trace_id = request.headers.get("X-Trace-ID", "unknown")
    uptime = time.time()  # Simplified - would track actual start time
    
    # Gather metrics from all components
    key_mgr = get_api_key_manager()
    key_stats = key_mgr.get_key_stats()
    
    rbac_mgr = get_rbac_manager()
    
    limiter = get_rate_limiter()
    
    metrics_data = {
        "service": "liuhao-gateway",
        "version": "1.0.0",
        "timestamp": time.time(),
        "trace_id": trace_id,
        "uptime_seconds": uptime,
        "components": {
            "api_key_manager": {
                "total_keys": key_stats["total"],
                "by_status": key_stats["by_status"],
                "by_scope": key_stats["by_scope"],
                "expired_count": key_stats["expired_count"],
                "total_usage": key_stats["total_usage"],
            },
            "rbac_manager": {
                "total_roles": len(rbac_mgr._roles),
            },
            "rate_limiter": {
                "active_buckets": len(limiter._buckets) if hasattr(limiter, '_buckets') else 0,
            },
        },
    }
    
    return JSONResponse(content=metrics_data, status_code=status.HTTP_200_OK)