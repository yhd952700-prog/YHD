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

import shutil
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter

from ..version import APP_VERSION


router = APIRouter(prefix="/health", tags=["health"])

_PROCESS_START = time.time()

try:  # psutil is a declared dependency (requirements.txt / pyproject.toml).
    import psutil as _psutil
except ImportError:  # pragma: no cover - degraded environment
    _psutil = None

# Components below have no live probe wired in this module: there is no
# database / AI-provider / auth / message-bus client configured here, so
# their status must NOT be reported as healthy. They are reported as
# "unknown" until a real probe is wired for each of them.
_UNPROBED_MESSAGE = (
    "Not probed: no live check is wired for this component, "
    "status is not verified"
)

_UNPROBED_COMPONENTS: List[str] = [
    "database",
    "ai_provider",
    "memory_store",
    "auth_service",
    "message_bus",
]

# Disk is reported degraded once usage reaches this fraction; the
# measurement itself (shutil.disk_usage) is real, the threshold is an
# operational choice, not a measured value.
_DISK_DEGRADED_USAGE = 0.90


# =====================================================================
# Component health: real probes where one exists, "unknown" otherwise
# =====================================================================


def _probe_process() -> Dict[str, Any]:
    """Probe the current process (real measurement, psutil or stdlib)."""
    started = time.monotonic()
    try:
        if _psutil is not None:
            proc = _psutil.Process()
            rss_mb = proc.memory_info().rss / (1024 * 1024)
            return {
                "status": "healthy",
                "message": f"Process running (RSS {rss_mb:.1f} MB)",
                "response_time_ms": round((time.monotonic() - started) * 1000, 2),
                "last_checked": datetime.utcnow().isoformat(),
            }
        import resource

        rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return {
            "status": "healthy",
            "message": f"Process running (peak RSS {rss_kb / 1024:.1f} MB, psutil unavailable)",
            "response_time_ms": round((time.monotonic() - started) * 1000, 2),
            "last_checked": datetime.utcnow().isoformat(),
        }
    except Exception as exc:  # noqa: BLE001 - probe must never raise
        return {
            "status": "unknown",
            "message": f"Process probe failed: {exc}",
            "response_time_ms": None,
            "last_checked": datetime.utcnow().isoformat(),
        }


def _probe_disk() -> Dict[str, Any]:
    """Probe the root filesystem (real measurement via shutil)."""
    started = time.monotonic()
    try:
        usage = shutil.disk_usage("/")
        used_fraction = usage.used / usage.total if usage.total else 0.0
        return {
            "status": "degraded" if used_fraction >= _DISK_DEGRADED_USAGE else "healthy",
            "message": (
                f"Disk {used_fraction:.1%} used "
                f"({usage.free // (1024 ** 3)} GiB free)"
            ),
            "response_time_ms": round((time.monotonic() - started) * 1000, 2),
            "last_checked": datetime.utcnow().isoformat(),
        }
    except Exception as exc:  # noqa: BLE001 - probe must never raise
        return {
            "status": "unknown",
            "message": f"Disk probe failed: {exc}",
            "response_time_ms": None,
            "last_checked": datetime.utcnow().isoformat(),
        }


def _probe_components() -> Dict[str, Dict[str, Any]]:
    """Probe every component; unprobed ones are reported as unknown.

    Returns a fresh snapshot on every call (no cached fake values).
    """
    now = datetime.utcnow().isoformat()
    components: Dict[str, Dict[str, Any]] = {
        name: {
            "status": "unknown",
            "message": _UNPROBED_MESSAGE,
            "response_time_ms": None,
            "last_checked": now,
        }
        for name in _UNPROBED_COMPONENTS
    }
    components["process"] = _probe_process()
    components["disk"] = _probe_disk()
    return components


def _update_check_times() -> None:
    """Backwards-compatible no-op.

    Probing is done fresh by :func:`_probe_components` on every request;
    there is no longer a static component table whose timestamps need
    refreshing. Kept so existing callers do not break.
    """
    return None


def _compute_overall_status(components: Optional[Dict[str, Dict[str, Any]]] = None) -> str:
    """Compute overall health status from components.

    "unknown" is never reported as "healthy": if any component is
    unprobed, the overall status is at best "unknown".
    """
    statuses = [c["status"] for c in (components or {}).values()]
    if "unhealthy" in statuses:
        return "unhealthy"
    if "degraded" in statuses:
        return "degraded"
    if "unknown" in statuses:
        return "unknown"
    return "healthy"


def _process_resources() -> Dict[str, Any]:
    """Measure this process's real resource usage.

    Returns None for any value that cannot be measured instead of a
    fabricated number. ``active_connections`` counts this process's own
    inet connections (psutil); without psutil there is no reliable
    source for it, so it is None.
    """
    memory_mb: Optional[float] = None
    cpu_pct: Optional[float] = None
    active_connections: Optional[int] = None
    source = "unavailable"
    if _psutil is not None:
        try:
            proc = _psutil.Process()
            memory_mb = round(proc.memory_info().rss / (1024 * 1024), 2)
            cpu_pct = round(proc.cpu_percent(interval=0.1), 2)
            source = "psutil"
        except Exception:  # noqa: BLE001 - fall through to stdlib fallback
            memory_mb = None
            cpu_pct = None
        if memory_mb is not None:
            try:
                active_connections = len(proc.net_connections(kind="inet"))
            except Exception:  # noqa: BLE001 - e.g. AccessDenied
                active_connections = None
    if memory_mb is None:
        try:
            import resource

            rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            # ru_maxrss is KiB on Linux, bytes on macOS.
            memory_mb = round(rss_kb / 1024, 2)
            source = "resource_fallback_peak_rss"
        except Exception:  # noqa: BLE001 - nothing measurable
            memory_mb = None
    return {
        "memory_usage_mb": memory_mb,
        "cpu_usage_pct": cpu_pct,
        "active_connections": active_connections,
        "resource_source": source,
    }


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
    components = _probe_components()
    overall = _compute_overall_status(components)
    return {
        "status": overall,
        "service": "liuhao-ai-os",
        "version": APP_VERSION,
        "timestamp": datetime.utcnow().isoformat(),
        "uptime_seconds": round(time.time() - _PROCESS_START, 2),
        "component_count": len(components),
    }


@router.get("/ready")
async def health_ready() -> Dict[str, Any]:
    """Readiness probe.

    Returns 200 when the service is ready to serve traffic.
    Returns 503 with details when some components are not ready.

    Returns:
        Dict with readiness status and component checks.
    """
    components = _probe_components()
    ready = all(c["status"] == "healthy" for c in components.values())
    unready = [
        name for name, comp in components.items()
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
            for name, comp in components.items()
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
    components = _probe_components()
    overall = _compute_overall_status(components)
    response_times = [
        c["response_time_ms"] for c in components.values()
        if c["response_time_ms"] is not None
    ]
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
            for name, comp in components.items()
        },
        "summary": {
            "total_components": len(components),
            "healthy": sum(1 for c in components.values() if c["status"] == "healthy"),
            "degraded": sum(1 for c in components.values() if c["status"] == "degraded"),
            "unhealthy": sum(1 for c in components.values() if c["status"] == "unhealthy"),
            "unknown": sum(1 for c in components.values() if c["status"] == "unknown"),
            "avg_response_time_ms": round(sum(response_times) / len(response_times), 2) if response_times else None,
            "max_response_time_ms": round(max(response_times), 2) if response_times else None,
        },
    }


@router.get("/components")
async def health_components() -> Dict[str, Any]:
    """List all health-monitored components.

    Returns:
        Dict with component list and individual statuses.
    """
    components = _probe_components()
    return {
        "components": [
            {
                "name": name,
                "status": comp["status"],
                "message": comp["message"],
                "response_time_ms": comp["response_time_ms"],
                "last_checked": comp["last_checked"],
            }
            for name, comp in components.items()
        ],
        "total": len(components),
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

    components = _probe_components()
    if component_name not in components:
        raise HTTPException(status_code=404, detail=f"Component not found: {component_name}")
    comp = components[component_name]
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

    ``memory_usage_mb`` / ``cpu_usage_pct`` / ``active_connections``
    are measured from this process (psutil, stdlib fallback for memory).
    Any value that cannot be measured is returned as null with
    ``resource_source`` explaining why — never a fabricated number.

    Returns:
        Dict with health metrics.
    """
    components = _probe_components()
    response_times = {
        name: comp["response_time_ms"]
        for name, comp in components.items()
    }
    measured_times = [v for v in response_times.values() if v is not None]
    resources = _process_resources()
    return {
        "uptime_seconds": round(time.time() - _PROCESS_START, 2),
        "component_count": len(components),
        "healthy_count": sum(1 for c in components.values() if c["status"] == "healthy"),
        "unknown_count": sum(1 for c in components.values() if c["status"] == "unknown"),
        "total_checks": len(components),
        "avg_response_time_ms": round(sum(measured_times) / len(measured_times), 2) if measured_times else None,
        "response_times": response_times,
        "memory_usage_mb": resources["memory_usage_mb"],
        "cpu_usage_pct": resources["cpu_usage_pct"],
        "active_connections": resources["active_connections"],
        "resource_source": resources["resource_source"],
    }
