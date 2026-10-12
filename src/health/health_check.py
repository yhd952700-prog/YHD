"""
Health Check System for LiuHao AI OS.

Provides a comprehensive health check system with multiple check types,
component-level monitoring, aggregated health status, and history tracking.

Health statuses:
    - HEALTHY: All components are functioning normally
    - DEGRADED: Some components are degraded but service is operational
    - UNHEALTHY: Critical components are not functioning
    - UNKNOWN: Health status cannot be determined

Check types:
    - liveness: Whether the service is alive and running
    - readiness: Whether the service is ready to serve traffic
    - startup: Whether the service has completed startup
"""

import time
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Any, Optional, Dict, List, Tuple
from collections import deque


class HealthStatus(Enum):
    """Health status enumeration.

    Attributes:
        HEALTHY: All components are functioning normally.
        DEGRADED: Service is operational but some components are degraded.
        UNHEALTHY: Critical components are not functioning.
        UNKNOWN: Health status cannot be determined.
    """
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNHEALTHY = "UNHEALTHY"
    UNKNOWN = "UNKNOWN"

    def is_worse_than(self, other: "HealthStatus") -> bool:
        """Check if this status is worse than another.

        Args:
            other: The other health status to compare.

        Returns:
            True if this status is worse.
        """
        order = {
            HealthStatus.HEALTHY: 0,
            HealthStatus.DEGRADED: 1,
            HealthStatus.UNHEALTHY: 2,
            HealthStatus.UNKNOWN: 3,
        }
        return order[self] > order[other]


class CheckType(Enum):
    """Types of health checks.

    Attributes:
        LIVENESS: Whether the service is alive and should be restarted if not.
        READINESS: Whether the service is ready to serve traffic.
        STARTUP: Whether the service has completed startup initialization.
    """
    LIVENESS = "liveness"
    READINESS = "readiness"
    STARTUP = "startup"


@dataclass
class HealthCheckResult:
    """Result of a single health check.

    Attributes:
        name: Name of the health check.
        status: Health status result.
        message: Human-readable message about the check.
        details: Additional details about the check result.
        duration_seconds: Time taken to perform the check.
        timestamp: When the check was performed.
        check_type: Type of check (liveness, readiness, startup).
    """
    name: str
    status: HealthStatus
    message: str = ""
    details: Dict[str, Any] = field(default_factory=dict)
    duration_seconds: float = 0.0
    timestamp: float = 0.0
    check_type: CheckType = CheckType.READINESS

    def to_dict(self) -> dict:
        """Convert to dictionary.

        Returns:
            Dictionary representation.
        """
        return {
            "name": self.name,
            "status": self.status.value,
            "message": self.message,
            "details": self.details,
            "duration_seconds": self.duration_seconds,
            "timestamp": self.timestamp,
            "check_type": self.check_type.value,
        }


@dataclass
class HealthCheckHistoryEntry:
    """An entry in the health check history.

    Attributes:
        result: The health check result.
        timestamp: When the check was performed.
    """
    result: HealthCheckResult
    timestamp: float


@dataclass
class AggregatedHealthResult:
    """Aggregated health result from multiple checks.

    Attributes:
        status: Overall health status.
        checks: Individual check results by name.
        total_checks: Total number of checks performed.
        healthy_count: Number of healthy checks.
        degraded_count: Number of degraded checks.
        unhealthy_count: Number of unhealthy checks.
        unknown_count: Number of unknown checks.
        duration_seconds: Total time taken for all checks.
        timestamp: When the aggregation was performed.
    """
    status: HealthStatus
    checks: Dict[str, HealthCheckResult]
    total_checks: int = 0
    healthy_count: int = 0
    degraded_count: int = 0
    unhealthy_count: int = 0
    unknown_count: int = 0
    duration_seconds: float = 0.0
    timestamp: float = 0.0

    def to_dict(self) -> dict:
        """Convert to dictionary.

        Returns:
            Dictionary representation.
        """
        return {
            "status": self.status.value,
            "checks": {name: result.to_dict() for name, result in self.checks.items()},
            "total_checks": self.total_checks,
            "healthy_count": self.healthy_count,
            "degraded_count": self.degraded_count,
            "unhealthy_count": self.unhealthy_count,
            "unknown_count": self.unknown_count,
            "duration_seconds": self.duration_seconds,
            "timestamp": self.timestamp,
        }


class HealthCheck:
    """Base class for health checks.

    Subclasses should implement the ``check`` method to perform
    the actual health check logic.

    Attributes:
        name: Unique name for this health check.
        check_type: Type of check (liveness, readiness, startup).
        timeout: Maximum time in seconds for the check to complete.
        critical: Whether this check is critical (affects overall health).
    """

    def __init__(
        self,
        name: str,
        check_type: CheckType = CheckType.READINESS,
        timeout: float = 5.0,
        critical: bool = True,
    ):
        """Initialize the health check.

        Args:
            name: Unique name for this health check.
            check_type: Type of check.
            timeout: Maximum execution time in seconds.
            critical: Whether failure makes the overall status UNHEALTHY.
        """
        self.name = name
        self.check_type = check_type
        self.timeout = timeout
        self.critical = critical

    def check(self) -> HealthCheckResult:
        """Perform the health check.

        Subclasses must implement this method.

        Returns:
            HealthCheckResult with the check status.
        """
        raise NotImplementedError("Subclasses must implement check()")

    def execute(self) -> HealthCheckResult:
        """Execute the health check with timing.

        Returns:
            HealthCheckResult with timing information.
        """
        start_time = time.monotonic()
        try:
            result = self.check()
        except Exception as exc:
            duration = time.monotonic() - start_time
            status = HealthStatus.UNHEALTHY if self.critical else HealthStatus.DEGRADED
            return HealthCheckResult(
                name=self.name,
                status=status,
                message=f"Check raised exception: {exc}",
                details={"exception_type": type(exc).__name__, "exception_msg": str(exc)},
                duration_seconds=duration,
                timestamp=time.time(),
                check_type=self.check_type,
            )

        result.duration_seconds = time.monotonic() - start_time
        if result.timestamp == 0:
            result.timestamp = time.time()
        result.check_type = self.check_type
        return result


class FunctionHealthCheck(HealthCheck):
    """A health check that wraps a callable function.

    The function should return a tuple of (HealthStatus, message, details)
    or a single HealthStatus value.

    Usage::

        def check_database():
            try:
                conn = db.connect()
                conn.ping()
                return HealthStatus.HEALTHY, "Database is reachable"
            except Exception as e:
                return HealthStatus.UNHEALTHY, str(e)

        db_check = FunctionHealthCheck("database", check_database)
    """

    def __init__(
        self,
        name: str,
        func: Callable[[], Any],
        check_type: CheckType = CheckType.READINESS,
        timeout: float = 5.0,
        critical: bool = True,
    ):
        """Initialize the function-based health check.

        Args:
            name: Unique name for this health check.
            func: Callable that performs the check.
            check_type: Type of check.
            timeout: Maximum execution time.
            critical: Whether failure is critical.
        """
        super().__init__(name, check_type, timeout, critical)
        self._func = func

    def check(self) -> HealthCheckResult:
        """Execute the wrapped function as a health check.

        Returns:
            HealthCheckResult from the function's return value.
        """
        result = self._func()

        if isinstance(result, HealthCheckResult):
            result.name = self.name
            return result

        status = HealthStatus.UNKNOWN
        message = ""
        details = {}

        if isinstance(result, tuple):
            if len(result) >= 1:
                status = result[0] if isinstance(result[0], HealthStatus) else HealthStatus.UNKNOWN
            if len(result) >= 2:
                message = str(result[1])
            if len(result) >= 3 and isinstance(result[2], dict):
                details = result[2]
        elif isinstance(result, HealthStatus):
            status = result
        elif isinstance(result, bool):
            status = HealthStatus.HEALTHY if result else HealthStatus.UNHEALTHY
        elif isinstance(result, str):
            message = result
            status = HealthStatus.HEALTHY

        return HealthCheckResult(
            name=self.name,
            status=status,
            message=message,
            details=details,
            check_type=self.check_type,
        )


class HealthCheckRegistry:
    """Registry for managing and aggregating health checks.

    Provides a central registry for health checks with support for
    multiple check types, history tracking, and aggregated results.

    Usage::

        registry = HealthCheckRegistry()
        registry.register(db_check)
        registry.register(cache_check)

        # Check all readiness probes
        result = registry.check_all(CheckType.READINESS)
    """

    def __init__(self, history_size: int = 100):
        """Initialize the health check registry.

        Args:
            history_size: Maximum number of history entries to keep per check.
        """
        self._checks: Dict[str, HealthCheck] = {}
        self._history: Dict[str, deque] = {}
        self._history_size = history_size
        self._lock = threading.Lock()

    def register(self, check: HealthCheck) -> None:
        """Register a health check.

        Args:
            check: The health check to register.

        Raises:
            ValueError: If a check with the same name already exists.
        """
        with self._lock:
            if check.name in self._checks:
                raise ValueError(f"Health check '{check.name}' already registered")
            self._checks[check.name] = check
            self._history[check.name] = deque(maxlen=self._history_size)

    def unregister(self, name: str) -> bool:
        """Unregister a health check.

        Args:
            name: Name of the check to unregister.

        Returns:
            True if the check was found and removed.
        """
        with self._lock:
            if name in self._checks:
                del self._checks[name]
                self._history.pop(name, None)
                return True
            return False

    def get_check(self, name: str) -> Optional[HealthCheck]:
        """Get a health check by name.

        Args:
            name: Name of the check.

        Returns:
            The HealthCheck instance, or None if not found.
        """
        with self._lock:
            return self._checks.get(name)

    def list_checks(self, check_type: Optional[CheckType] = None) -> List[str]:
        """List registered health check names.

        Args:
            check_type: Optional filter by check type.

        Returns:
            List of check names.
        """
        with self._lock:
            if check_type is None:
                return list(self._checks.keys())
            return [
                name for name, check in self._checks.items()
                if check.check_type == check_type
            ]

    def check_one(self, name: str) -> Optional[HealthCheckResult]:
        """Execute a single health check by name.

        Args:
            name: Name of the check to execute.

        Returns:
            HealthCheckResult, or None if check not found.
        """
        check = self.get_check(name)
        if check is None:
            return None

        result = check.execute()

        with self._lock:
            if name in self._history:
                self._history[name].append(HealthCheckHistoryEntry(
                    result=result,
                    timestamp=time.time(),
                ))

        return result

    def check_all(
        self,
        check_type: Optional[CheckType] = None,
    ) -> AggregatedHealthResult:
        """Execute all health checks and aggregate results.

        Args:
            check_type: Optional filter by check type.

        Returns:
            AggregatedHealthResult with overall status.
        """
        start_time = time.monotonic()
        results: Dict[str, HealthCheckResult] = {}
        overall_status = HealthStatus.HEALTHY

        with self._lock:
            checks_to_run = {
                name: check for name, check in self._checks.items()
                if check_type is None or check.check_type == check_type
            }

        for name, check in checks_to_run.items():
            result = check.execute()
            results[name] = result

            # Record in history
            with self._lock:
                if name in self._history:
                    self._history[name].append(HealthCheckHistoryEntry(
                        result=result,
                        timestamp=time.time(),
                    ))

            # Determine overall status
            if result.status.is_worse_than(overall_status):
                # Non-critical degraded checks don't make overall unhealthy
                if (
                    result.status == HealthStatus.UNHEALTHY
                    and not check.critical
                ):
                    if overall_status != HealthStatus.UNHEALTHY:
                        overall_status = HealthStatus.DEGRADED
                else:
                    overall_status = result.status

        duration = time.monotonic() - start_time

        # Count statuses
        healthy_count = sum(1 for r in results.values() if r.status == HealthStatus.HEALTHY)
        degraded_count = sum(1 for r in results.values() if r.status == HealthStatus.DEGRADED)
        unhealthy_count = sum(1 for r in results.values() if r.status == HealthStatus.UNHEALTHY)
        unknown_count = sum(1 for r in results.values() if r.status == HealthStatus.UNKNOWN)

        return AggregatedHealthResult(
            status=overall_status,
            checks=results,
            total_checks=len(results),
            healthy_count=healthy_count,
            degraded_count=degraded_count,
            unhealthy_count=unhealthy_count,
            unknown_count=unknown_count,
            duration_seconds=duration,
            timestamp=time.time(),
        )

    def get_history(self, name: str, limit: int = 10) -> List[HealthCheckHistoryEntry]:
        """Get the history of a specific health check.

        Args:
            name: Name of the health check.
            limit: Maximum number of history entries to return.

        Returns:
            List of history entries (most recent last).
        """
        with self._lock:
            if name not in self._history:
                return []
            entries = list(self._history[name])
            return entries[-limit:]

    def get_trend(self, name: str, window: int = 5) -> Dict[str, Any]:
        """Get trend information for a health check.

        Args:
            name: Name of the health check.
            window: Number of recent checks to analyze.

        Returns:
            Dictionary with trend information.
        """
        history = self.get_history(name, limit=window)
        if not history:
            return {"available": False, "message": "No history available"}

        statuses = [entry.result.status for entry in history]
        healthy_count = statuses.count(HealthStatus.HEALTHY)
        total = len(statuses)
        current_status = statuses[-1] if statuses else HealthStatus.UNKNOWN

        # Detect transitions
        transitions = 0
        for i in range(1, len(statuses)):
            if statuses[i] != statuses[i - 1]:
                transitions += 1

        # Average duration
        durations = [entry.result.duration_seconds for entry in history]
        avg_duration = sum(durations) / len(durations) if durations else 0

        return {
            "available": True,
            "current_status": current_status.value,
            "healthy_ratio": healthy_count / total if total > 0 else 0,
            "transitions": transitions,
            "avg_duration_seconds": avg_duration,
            "window_size": total,
        }

    def clear_history(self, name: Optional[str] = None) -> None:
        """Clear health check history.

        Args:
            name: Specific check to clear, or None for all.
        """
        with self._lock:
            if name is not None:
                if name in self._history:
                    self._history[name].clear()
            else:
                for history in self._history.values():
                    history.clear()

    @property
    def check_count(self) -> int:
        """Get the total number of registered checks.

        Returns:
            Number of registered health checks.
        """
        with self._lock:
            return len(self._checks)
