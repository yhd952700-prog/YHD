"""Health check CLI commands for LiuHao AI OS.

Provides command-line interface for system health operations:
- health check, health status
- health components
- health history
- readiness, liveness checks
"""

from __future__ import annotations

import argparse
import json
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional

try:  # psutil is a declared dependency (requirements.txt / pyproject.toml).
    import psutil as _psutil
except ImportError:  # pragma: no cover - degraded environment
    _psutil = None

# Components below have no live probe wired in this module, so their
# status must NOT be reported as healthy. They are "unknown" until a
# real probe is wired for each of them.
_UNPROBED_MESSAGE = (
    "Not probed: no live check is wired for this component, "
    "status is not verified"
)

# Disk is reported degraded once usage reaches this fraction; the
# measurement itself (shutil.disk_usage) is real, the threshold is an
# operational choice, not a measured value.
_DISK_DEGRADED_USAGE = 0.90


def _probe_process() -> ComponentHealth:
    """Probe the current process (real measurement, psutil or stdlib)."""
    started = time.monotonic()
    try:
        if _psutil is not None:
            rss_mb = _psutil.Process().memory_info().rss / (1024 * 1024)
            return ComponentHealth(
                name="process",
                status=HealthStatus.HEALTHY,
                message=f"Process running (RSS {rss_mb:.1f} MB)",
                response_time_ms=round((time.monotonic() - started) * 1000, 2),
            )
        import resource

        rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return ComponentHealth(
            name="process",
            status=HealthStatus.HEALTHY,
            message=f"Process running (peak RSS {rss_kb / 1024:.1f} MB, psutil unavailable)",
            response_time_ms=round((time.monotonic() - started) * 1000, 2),
        )
    except Exception as exc:  # noqa: BLE001 - probe must never raise
        return ComponentHealth(
            name="process",
            status=HealthStatus.UNKNOWN,
            message=f"Process probe failed: {exc}",
        )


def _probe_disk() -> ComponentHealth:
    """Probe the root filesystem (real measurement via shutil)."""
    started = time.monotonic()
    try:
        usage = shutil.disk_usage("/")
        used_fraction = usage.used / usage.total if usage.total else 0.0
        return ComponentHealth(
            name="disk",
            status=HealthStatus.DEGRADED if used_fraction >= _DISK_DEGRADED_USAGE else HealthStatus.HEALTHY,
            message=f"Disk {used_fraction:.1%} used ({usage.free // (1024 ** 3)} GiB free)",
            response_time_ms=round((time.monotonic() - started) * 1000, 2),
        )
    except Exception as exc:  # noqa: BLE001 - probe must never raise
        return ComponentHealth(
            name="disk",
            status=HealthStatus.UNKNOWN,
            message=f"Disk probe failed: {exc}",
        )


class HealthStatus(str, Enum):
    """Overall health status."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


class CheckType(str, Enum):
    """Types of health checks."""
    LIVENESS = "liveness"
    READINESS = "readiness"
    STARTUP = "startup"


@dataclass
class ComponentHealth:
    """Health status of a single component."""
    name: str
    status: HealthStatus
    message: str = ""
    response_time_ms: float = 0.0
    last_checked: str = field(default_factory=lambda: datetime.utcnow().isoformat())


@dataclass
class HealthHistoryEntry:
    """A historical health check record."""
    timestamp: str
    overall_status: HealthStatus
    components: Dict[str, str]


class HealthChecker:
    """Health checker for CLI operations.

    Only the process itself and the local disk are really probed here
    (psutil / shutil). The other components have no live check wired in
    this module and are reported as UNKNOWN, never as healthy.
    In production this would integrate with the real health check system
    in src.health.health_check.
    """

    def __init__(self) -> None:
        self._components: Dict[str, ComponentHealth] = {
            name: ComponentHealth(
                name=name, status=HealthStatus.UNKNOWN, message=_UNPROBED_MESSAGE
            )
            for name in (
                "database",
                "ai_provider",
                "memory_store",
                "auth_service",
                "message_bus",
            )
        }
        self._components["process"] = _probe_process()
        self._components["disk"] = _probe_disk()
        self._history: List[HealthHistoryEntry] = []

    def _record_history(self) -> None:
        """Record current state to history."""
        self._history.append(
            HealthHistoryEntry(
                timestamp=datetime.utcnow().isoformat(),
                overall_status=self._compute_overall(),
                components={
                    name: comp.status.value
                    for name, comp in self._components.items()
                },
            )
        )
        # Keep last 100 entries
        if len(self._history) > 100:
            self._history = self._history[-100:]

    def _compute_overall(self) -> HealthStatus:
        """Compute overall health status from components.

        "unknown" is never reported as "healthy": if any component is
        unprobed, the overall status is at best UNKNOWN.
        """
        statuses = [c.status for c in self._components.values()]
        if HealthStatus.UNHEALTHY in statuses:
            return HealthStatus.UNHEALTHY
        if HealthStatus.DEGRADED in statuses:
            return HealthStatus.DEGRADED
        if HealthStatus.UNKNOWN in statuses:
            return HealthStatus.UNKNOWN
        return HealthStatus.HEALTHY

    def run_check(self) -> Dict[str, Any]:
        """Run a full health check on all components.

        Re-probes the process and disk for real; the other components
        have no live probe and stay UNKNOWN with their last_checked
        timestamp refreshed.

        Returns:
            Dict with overall status and component details.
        """
        # Re-probe what can actually be probed.
        self._components["process"] = _probe_process()
        self._components["disk"] = _probe_disk()
        for comp in self._components.values():
            comp.last_checked = datetime.utcnow().isoformat()
        self._record_history()
        overall = self._compute_overall()
        return {
            "overall_status": overall.value,
            "checked_at": datetime.utcnow().isoformat(),
            "component_count": len(self._components),
            "components": {
                name: {
                    "status": comp.status.value,
                    "message": comp.message,
                    "response_time_ms": comp.response_time_ms,
                    "last_checked": comp.last_checked,
                }
                for name, comp in self._components.items()
            },
        }

    def get_status(self) -> Dict[str, Any]:
        """Get current health status without running a new check.

        Returns:
            Dict with cached health status.
        """
        overall = self._compute_overall()
        return {
            "overall_status": overall.value,
            "component_count": len(self._components),
            "healthy_count": sum(
                1 for c in self._components.values() if c.status == HealthStatus.HEALTHY
            ),
            "degraded_count": sum(
                1 for c in self._components.values() if c.status == HealthStatus.DEGRADED
            ),
            "unhealthy_count": sum(
                1 for c in self._components.values() if c.status == HealthStatus.UNHEALTHY
            ),
            "unknown_count": sum(
                1 for c in self._components.values() if c.status == HealthStatus.UNKNOWN
            ),
        }

    def list_components(self) -> Dict[str, Any]:
        """List all health-monitored components.

        Returns:
            Dict with component list and statuses.
        """
        return {
            "components": [
                {
                    "name": name,
                    "status": comp.status.value,
                    "message": comp.message,
                    "response_time_ms": comp.response_time_ms,
                    "last_checked": comp.last_checked,
                }
                for name, comp in self._components.items()
            ]
        }

    def get_history(
        self, limit: int = 20, hours: Optional[int] = None
    ) -> Dict[str, Any]:
        """Get health check history.

        Args:
            limit: Maximum number of history entries.
            hours: Optional time window in hours.

        Returns:
            Dict with history entries.
        """
        entries = list(reversed(self._history))
        if hours:
            cutoff = datetime.utcnow() - timedelta(hours=hours)
            entries = [
                e for e in entries
                if datetime.fromisoformat(e.timestamp) >= cutoff
            ]
        entries = entries[:limit]
        return {
            "total": len(entries),
            "limit": limit,
            "hours": hours,
            "entries": [
                {
                    "timestamp": e.timestamp,
                    "overall_status": e.overall_status.value,
                    "components": e.components,
                }
                for e in entries
            ],
        }

    def check_liveness(self) -> Dict[str, Any]:
        """Perform a liveness check.

        Liveness is real here: this code is executing in the process.
        The process is dead only if its probe reports UNHEALTHY; an
        UNKNOWN probe (measurement unavailable) still means alive.

        Returns:
            Dict with liveness status.
        """
        alive = self._components["process"].status != HealthStatus.UNHEALTHY
        return {
            "status": "alive" if alive else "dead",
            "alive": alive,
            "checked_at": datetime.utcnow().isoformat(),
        }

    def check_readiness(self) -> Dict[str, Any]:
        """Perform a readiness check.

        Returns:
            Dict with readiness status.
        """
        ready = all(
            c.status == HealthStatus.HEALTHY
            for c in self._components.values()
        )
        unready = [
            name for name, comp in self._components.items()
            if comp.status != HealthStatus.HEALTHY
        ]
        return {
            "status": "ready" if ready else "not_ready",
            "ready": ready,
            "checked_at": datetime.utcnow().isoformat(),
            "unready_components": unready,
        }

    def set_component_status(
        self, component: str, status: str, message: str = ""
    ) -> Dict[str, Any]:
        """Set a component's health status (for testing / simulation).

        Args:
            component: Component name.
            status: New health status value.
            message: Optional status message.

        Returns:
            Dict with updated component info.

        Raises:
            ValueError: If component not found.
        """
        comp = self._components.get(component)
        if not comp:
            raise ValueError(f"Component not found: {component}")
        comp.status = HealthStatus(status)
        comp.message = message
        comp.last_checked = datetime.utcnow().isoformat()
        return {
            "component": component,
            "status": comp.status.value,
            "message": comp.message,
        }


# =====================================================================
# CLI Parser
# =====================================================================


def register_health_commands(
    subparsers: argparse._SubParsersAction,
) -> None:
    """Register health subcommands under the given argparse subparser.

    Args:
        subparsers: The parent subparsers action to attach to.
    """
    p_health = subparsers.add_parser("health", help="System health checks")
    health_sub = p_health.add_subparsers(dest="health_command", required=True)

    # health check
    p_check = health_sub.add_parser("check", help="Run a full health check")
    p_check.add_argument("--component", help="Check only a specific component")

    # health status
    health_sub.add_parser("status", help="Show current health status (cached)")

    # health components
    health_sub.add_parser("components", help="List all monitored components")

    # health history
    p_history = health_sub.add_parser("history", help="Show health check history")
    p_history.add_argument("--limit", type=int, default=20, help="Result limit")
    p_history.add_argument("--hours", type=int, help="Time window in hours")

    # health liveness
    health_sub.add_parser("liveness", help="Liveness probe")

    # health readiness
    health_sub.add_parser("readiness", help="Readiness probe")

    # health set (for testing)
    p_set = health_sub.add_parser(
        "set-component", help="Set component status (for testing/simulation)"
    )
    p_set.add_argument("component", help="Component name")
    p_set.add_argument(
        "status",
        choices=["healthy", "degraded", "unhealthy", "unknown"],
        help="New status",
    )
    p_set.add_argument("--message", default="", help="Status message")


# =====================================================================
# Command Handler
# =====================================================================


def handle_health_command(
    args: argparse.Namespace, checker: HealthChecker
) -> int:
    """Dispatch a health CLI command to the appropriate handler.

    Args:
        args: Parsed argparse namespace.
        checker: HealthChecker instance.

    Returns:
        Exit code (0 for success, 1 for error).
    """
    try:
        if args.health_command == "check":
            result = checker.run_check()
        elif args.health_command == "status":
            result = checker.get_status()
        elif args.health_command == "components":
            result = checker.list_components()
        elif args.health_command == "history":
            result = checker.get_history(limit=args.limit, hours=args.hours)
        elif args.health_command == "liveness":
            result = checker.check_liveness()
        elif args.health_command == "readiness":
            result = checker.check_readiness()
        elif args.health_command == "set-component":
            result = checker.set_component_status(
                args.component, args.status, args.message
            )
        else:
            print(f"Unknown health command: {args.health_command}")
            return 1

        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}, indent=2, ensure_ascii=False))
        return 1
