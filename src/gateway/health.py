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
from typing import Dict, Any
import time

from ..security import get_api_key_manager, get_jwt_handler, get_rbac_manager, get_encryption_manager
from ..gateway.rate_limiter import get_rate_limiter


health_router = APIRouter(prefix="/v1", tags=["health"])

#: 本模块被导入的时刻，用作"/metrics 的运行时长"基准。
#: 这是一个真实可得的量（进程从加载网关到现在的秒数），
#: 而不是曾经常用的 ``time.time()`` —— 那是时间戳，冒充不了运行时长。
_PROCESS_START = time.time()


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
        get_jwt_handler()
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
        get_encryption_manager()
        checks["encryption_manager"] = {"status": "healthy"}
    except Exception as e:
        errors.append(f"Encryption Manager: {str(e)}")
        checks["encryption_manager"] = {"status": "unhealthy", "error": str(e)}

    # Check Rate Limiter
    try:
        get_rate_limiter()
        checks["rate_limiter"] = {"status": "healthy"}
    except Exception as e:
        errors.append(f"Rate Limiter: {str(e)}")
        checks["rate_limiter"] = {"status": "unhealthy", "error": str(e)}

    # CRIT-1C / D17 (Layer 1) + C2 observability: surface audit write failures
    # AND verification coverage as observable signals. Only the store
    # *unavailability* marks it unhealthy (LOW tier may still keep running); the
    # failure *count* and verification *coverage* are reported so operators can
    # see "Evidence=missing" / "chain not re-derived" without it taking the
    # service down. Coverage being incomplete does NOT flip readiness to 503
    # (there is no periodic verifier mandated yet, so a populated-but-unverified
    # deployment must not self-terminate); it is reported in `checks` + `errors`
    # so it is visible instead of hidden behind a green light.
    try:
        from ..kernels.audit import audit_stats, audit_verification_coverage

        astats = audit_stats()
        cov = audit_verification_coverage()
        incomplete = (cov.get("uncovered_events", 0) > 0) or (
            not cov.get("rooted_at_genesis", True)
        )
        checks["audit_store"] = {
            "status": "healthy",
            "total_events": astats.get("total_events", 0),
            "failures": astats.get("failures", 0),
            "coverage_ratio": cov.get("coverage_ratio"),
            "uncovered_events": cov.get("uncovered_events"),
            "covered_through": cov.get("covered_through"),
            "newest_verified_at": cov.get("newest_verified_at"),
            "rooted_at_genesis": cov.get("rooted_at_genesis"),
        }
        if incomplete:
            errors.append(
                "Audit chain verification incomplete: "
                f"{cov.get('uncovered_events')} events uncovered, "
                f"rooted_at_genesis={cov.get('rooted_at_genesis')}"
            )
    except Exception as e:
        errors.append(f"Audit Store: {str(e)}")
        checks["audit_store"] = {"status": "unhealthy", "error": str(e)}

    # P0-3 (boss decision 2026-09-22): the human-identity registry integrity
    # posture must be OBSERVABLE at the readiness edge. When a registry is in
    # use (humans can hold sovereignty) but no integrity key is configured, the
    # posture is "degraded_unverified" -- the system must NOT present as fully
    # sovereign. Mark it "degraded" so the overall readiness degrades and a
    # deployment gate can see it, instead of a warning that disappears.
    try:
        from ..kernels.identity import get_identity_manager

        mgr = get_identity_manager()
        reg = mgr.describe_identity_namespaces().get("registry_integrity", {})
        state = reg.get("integrity_state", "unknown")
        if state == "degraded_unverified":
            checks["human_identity_registry"] = {
                "status": "degraded",
                "integrity_state": state,
                "detail": (
                    "a human registry is in use but LIUHAO_HUMAN_IDENTITIES_"
                    "INTEGRITY_KEY is unset -- rows are attacker-writable"
                ),
            }
            errors.append(
                "Human Identity Registry: degraded_unverified (no integrity key)"
            )
        else:
            checks["human_identity_registry"] = {
                "status": "healthy",
                "integrity_state": state,
            }
    except Exception as e:  # pragma: no cover - defensive
        errors.append(f"Human Identity Registry: {str(e)}")
        checks["human_identity_registry"] = {"status": "unhealthy", "error": str(e)}

    # UBX-005 observability: surface the human-sovereignty-critical executor
    # fence at the readiness edge. This is a VISIBILITY signal, not a gating
    # one -- a deployment that deliberately leaves the gate unarmed must not be
    # marked unhealthy, but operators must be able to SEE that the gate is off.
    # A genuine failure (exception) is reported as unhealthy so it is visible.
    try:
        from ..kernels.execution.fence import (
            current_executor_fence,
            executor_fence_armed,
            get_executor_fence,
            get_executor_fence_total,
        )

        fence = get_executor_fence()
        armed = executor_fence_armed()
        proc_ctx = current_executor_fence()
        active_leases = fence.lease.count_active() if fence is not None else 0
        backend = type(fence.lease).__name__ if fence is not None else "none"
        heartbeat_failures = (
            getattr(fence.lease, "heartbeat_failures", 0) if fence is not None else 0
        )
        checks["executor_fence"] = {
            "status": "healthy",
            "armed": armed,
            "backend": backend,
            "active_leases": active_leases,
            "epoch": proc_ctx.epoch if proc_ctx is not None else None,
            "denials_total": get_executor_fence_total(),
            "heartbeat_failures": heartbeat_failures,
        }
        if not armed:
            # Informational only -- the default policy is that the gate is
            # opt-in, so an unarmed deployment is a config state, not a fault.
            checks["executor_fence"]["note"] = (
                "executor fence gate not armed (LIUHAO_EXECUTOR_FENCE != on): "
                "the default-deny autonomous-action gate is inactive"
            )
    except Exception as e:  # pragma: no cover - defensive
        errors.append(f"Executor Fence: {str(e)}")
        checks["executor_fence"] = {"status": "unhealthy", "error": str(e)}

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

    ⚠️ 诚实性说明
    -------------
    本端点长期不可达（main.py 曾经自建同名 health_router 并覆盖了这里的实现），
    因此它的两处字段错误一直没被发现：``expired_count`` / ``total_usage`` 在
    ``APIKeyManager.get_key_stats()`` 里**并不存在**（该函数只返回
    ``total`` / ``by_status`` / ``by_scope``），一调用就 KeyError 500；
    ``uptime_seconds`` 也曾直接填 ``time.time()``，是时间戳而不是运行时长。
    现在按真实可得的数据计算：过期的密钥数从 ``by_status`` 里数出来，
    运行时长用本模块加载时刻起算。
    """
    trace_id = request.headers.get("X-Trace-ID", "unknown")

    # Gather metrics from all components
    key_mgr = get_api_key_manager()
    key_stats = key_mgr.get_key_stats()

    rbac_mgr = get_rbac_manager()

    limiter = get_rate_limiter()

    by_status = key_stats.get("by_status") or {}
    metrics_data = {
        "service": "liuhao-gateway",
        "version": "1.0.0",
        "timestamp": time.time(),
        "trace_id": trace_id,
        "uptime_seconds": round(time.time() - _PROCESS_START, 1),
        "components": {
            "api_key_manager": {
                "total_keys": key_stats.get("total", 0),
                "by_status": by_status,
                "by_scope": key_stats.get("by_scope") or {},
                # 从真实的状态分布里数出来，而不是假设存在一个预先算好的字段。
                "expired_count": int(by_status.get("expired", 0)),
            },
            "rbac_manager": {
                "total_roles": len(rbac_mgr._roles),
            },
            "rate_limiter": {
                "active_buckets": len(limiter._buckets) if hasattr(limiter, "_buckets") else 0,
            },
        },
    }

    return JSONResponse(content=metrics_data, status_code=status.HTTP_200_OK)
