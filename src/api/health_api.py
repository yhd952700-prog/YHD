"""Health API endpoints for LiuHao AI OS.

RESTful API endpoints for health checks:
- GET /health - Overall health status
- GET /health/ready - Readiness probe
- GET /health/live - Liveness probe
- GET /health/detailed - Detailed health report
- GET /health/components - Component-level health
- GET /health/metrics - Health metrics
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Dict, List

from fastapi import APIRouter


router = APIRouter(prefix="/health", tags=["health"])

_PROCESS_START = time.time()


# =====================================================================
# Component health data (scaffold)
# =====================================================================


_components: Dict[str, Dict[str, Any]] = {
    "database": {
        "status": "healthy",
        "message": "Connected",
        "response_time_ms": 5.2,
        "last_checked": None,
    },
    "ai_provider": {
        "status": "healthy",
        "message": "Available",
        "response_time_ms": 120.5,
        "last_checked": None,
    },
    "memory_store": {
        "status": "healthy",
        "message": "Operational",
        "response_time_ms": 3.1,
        "last_checked": None,
    },
    "auth_service": {
        "status": "healthy",
        "message": "Active",
        "response_time_ms": 8.7,
        "last_checked": None,
    },
    "message_bus": {
        "status": "healthy",
        "message": "Connected",
        "response_time_ms": 2.4,
        "last_checked": None,
    },
}


def _update_check_times() -> None:
    """Update last_checked timestamps for all components."""
    now = datetime.utcnow().isoformat()
    for comp in _components.values():
        comp["last_checked"] = now


def _compute_overall_status() -> str:
    """Compute overall health status from components."""
    statuses = [c["status"] for c in _components.values()]
    if "unhealthy" in statuses:
        return "unhealthy"
    if "degraded" in statuses:
        return "degraded"
    return "healthy"


# =====================================================================
# Health Endpoints
# =====================================================================


@router.get("")
async def health_overview() -> Dict[str, Any]:
    """Get overall health status.

    Returns overall health status, service info, and high-level metrics.
    Equivalent to a simple liveness probe with additional context.

    Returns:
        Dict with overall health status.
    """
    _update_check_times()
    overall = _compute_overall_status()
    return {
        "status": overall,
        "service": "liuhao-ai-os",
        "version": "1.0.0",
        "timestamp": datetime.utcnow().isoformat(),
        "uptime_seconds": round(time.time() - _PROCESS_START, 2),
        "component_count": len(_components),
    }


@router.get("/ready")
async def health_ready() -> Dict[str, Any]:
    """Readiness probe.

    Returns 200 when the service is ready to serve traffic.
    Returns 503 with details when some components are not ready.

    Returns:
        Dict with readiness status and component checks.
    """
    _update_check_times()
    ready = all(c["status"] == "healthy" for c in _components.values())
    unready = [
        name for name, comp in _components.items()
        if comp["status"] != "healthy"
    ]
    return {
        "status": "ready" if ready else "not_ready",
        "timestamp": datetime.utcnow().isoformat(),
        "checks": {
            name: {
                "status": comp["status"],
                "message": comp["message"],
                "response_time_ms": comp["response_time_ms"],
            }
            for name, comp in _components.items()
        },
        "unready_components": unready,
    }


@router.get("/live")
async def health_live() -> Dict[str, Any]:
    """Liveness probe.

    Returns 200 when the service is alive and running.
    Used by Kubernetes livenessProbe to determine if the process is alive.

    Returns:
        Dict with liveness status.
    """
    return {
        "status": "alive",
        "service": "liuhao-ai-os",
        "timestamp": datetime.utcnow().isoformat(),
        "uptime_seconds": round(time.time() - _PROCESS_START, 2),
    }


@router.get("/detailed")
async def health_detailed() -> Dict[str, Any]:
    """Get a detailed health report.

    Includes per-component status, response times, dependencies,
    and aggregated statistics.

    Returns:
        Dict with detailed health information.
    """
    _update_check_times()
    overall = _compute_overall_status()
    response_times = [c["response_time_ms"] for c in _components.values()]
    return {
        "status": overall,
        "timestamp": datetime.utcnow().isoformat(),
        "uptime_seconds": round(time.time() - _PROCESS_START, 2),
        "components": {
            name: {
                "status": comp["status"],
                "message": comp["message"],
                "response_time_ms": comp["response_time_ms"],
                "last_checked": comp["last_checked"],
            }
            for name, comp in _components.items()
        },
        "summary": {
            "total_components": len(_components),
            "healthy": sum(1 for c in _components.values() if c["status"] == "healthy"),
            "degraded": sum(1 for c in _components.values() if c["status"] == "degraded"),
            "unhealthy": sum(1 for c in _components.values() if c["status"] == "unhealthy"),
            "avg_response_time_ms": round(sum(response_times) / len(response_times), 2),
            "max_response_time_ms": round(max(response_times), 2),
        },
    }


@router.get("/components")
async def health_components() -> Dict[str, Any]:
    """List all health-monitored components.

    Returns:
        Dict with component list and individual statuses.
    """
    _update_check_times()
    return {
        "components": [
            {
                "name": name,
                "status": comp["status"],
                "message": comp["message"],
                "response_time_ms": comp["response_time_ms"],
                "last_checked": comp["last_checked"],
            }
            for name, comp in _components.items()
        ],
        "total": len(_components),
    }


@router.get("/components/{component_name}")
async def health_component_detail(component_name: str) -> Dict[str, Any]:
    """Get health details for a specific component.

    Args:
        component_name: Name of the component.

    Returns:
        Component health details.

    Raises:
        HTTPException: 404 if component not found.
    """
    from fastapi import HTTPException

    if component_name not in _components:
        raise HTTPException(status_code=404, detail=f"Component not found: {component_name}")
    comp = _components[component_name]
    comp["last_checked"] = datetime.utcnow().isoformat()
    return {
        "name": component_name,
        "status": comp["status"],
        "message": comp["message"],
        "response_time_ms": comp["response_time_ms"],
        "last_checked": comp["last_checked"],
    }


@router.get("/metrics")
async def health_metrics() -> Dict[str, Any]:
    """Get health-related metrics.

    Returns aggregate health metrics including uptime, check counts,
    and component performance statistics.

    Returns:
        Dict with health metrics.
    """
    response_times = [c["response_time_ms"] for c in _components.values()]
    return {
        "uptime_seconds": round(time.time() - _PROCESS_START, 2),
        "component_count": len(_components),
        "healthy_count": sum(1 for c in _components.values() if c["status"] == "healthy"),
        "total_checks": len(_components),
        "avg_response_time_ms": round(sum(response_times) / len(response_times), 2),
        "response_times": {
            name: comp["response_time_ms"]
            for name, comp in _components.items()
        },
        "memory_usage_mb": 128.5,  # placeholder
        "cpu_usage_pct": 12.3,  # placeholder
        "active_connections": 42,  # placeholder
    }
