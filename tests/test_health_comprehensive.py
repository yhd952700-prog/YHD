"""Comprehensive health check tests for LiuHao AI OS.

Covers:
- Health status transitions
- Liveness/readiness/startup check types
- Critical vs non-critical components
- Check timeout handling
- Health aggregation
- History and trends
- Multiple health check registries
"""

import time
import pytest
from typing import Any

from src.health.health_check import (
    HealthStatus,
    CheckType,
    HealthCheckResult,
    HealthCheck,
    FunctionHealthCheck,
    HealthCheckRegistry,
    AggregatedHealthResult,
    HealthCheckHistoryEntry,
)


# ======================================================================
# HealthStatus Tests (8 tests)
# ======================================================================

class TestHealthStatus:
    """Tests for HealthStatus enumeration and comparison."""

    def test_health_status_values(self):
        """Test all health status values exist."""
        statuses = list(HealthStatus)
        assert HealthStatus.HEALTHY in statuses
        assert HealthStatus.DEGRADED in statuses
        assert HealthStatus.UNHEALTHY in statuses
        assert HealthStatus.UNKNOWN in statuses
        assert len(statuses) == 4

    def test_is_worse_than_healthy(self):
        """Test is_worse_than comparisons from HEALTHY."""
        assert HealthStatus.DEGRADED.is_worse_than(HealthStatus.HEALTHY)
        assert HealthStatus.UNHEALTHY.is_worse_than(HealthStatus.HEALTHY)
        assert HealthStatus.UNKNOWN.is_worse_than(HealthStatus.HEALTHY)
        assert not HealthStatus.HEALTHY.is_worse_than(HealthStatus.HEALTHY)

    def test_is_worse_than_degraded(self):
        """Test is_worse_than comparisons from DEGRADED."""
        assert HealthStatus.UNHEALTHY.is_worse_than(HealthStatus.DEGRADED)
        assert HealthStatus.UNKNOWN.is_worse_than(HealthStatus.DEGRADED)
        assert not HealthStatus.DEGRADED.is_worse_than(HealthStatus.DEGRADED)
        assert not HealthStatus.HEALTHY.is_worse_than(HealthStatus.DEGRADED)

    def test_is_worse_than_unhealthy(self):
        """Test is_worse_than comparisons from UNHEALTHY."""
        assert HealthStatus.UNKNOWN.is_worse_than(HealthStatus.UNHEALTHY)
        assert not HealthStatus.UNHEALTHY.is_worse_than(HealthStatus.UNHEALTHY)
        assert not HealthStatus.DEGRADED.is_worse_than(HealthStatus.UNHEALTHY)

    def test_is_worse_than_unknown(self):
        """Test is_worse_than comparisons from UNKNOWN."""
        assert not HealthStatus.UNKNOWN.is_worse_than(HealthStatus.UNKNOWN)
        assert not HealthStatus.HEALTHY.is_worse_than(HealthStatus.UNKNOWN)

    def test_health_status_values_are_strings(self):
        """Test health status values are string-based enums."""
        assert HealthStatus.HEALTHY.value == "HEALTHY"
        assert HealthStatus.DEGRADED.value == "DEGRADED"
        assert HealthStatus.UNHEALTHY.value == "UNHEALTHY"
        assert HealthStatus.UNKNOWN.value == "UNKNOWN"


# ======================================================================
# CheckType Tests (5 tests)
# ======================================================================

class TestCheckType:
    """Tests for CheckType enumeration."""

    def test_all_check_types_exist(self):
        """Test all check types are defined."""
        types = list(CheckType)
        assert CheckType.LIVENESS in types
        assert CheckType.READINESS in types
        assert CheckType.STARTUP in types
        assert len(types) == 3

    def test_liveness_type(self):
        """Test liveness check type."""
        assert CheckType.LIVENESS.value == "liveness"

    def test_readiness_type(self):
        """Test readiness check type."""
        assert CheckType.READINESS.value == "readiness"

    def test_startup_type(self):
        """Test startup check type."""
        assert CheckType.STARTUP.value == "startup"

    def test_default_is_readiness(self):
        """Test that default check type is READINESS."""
        hc = FunctionHealthCheck("test", lambda: HealthStatus.HEALTHY)
        assert hc.check_type == CheckType.READINESS


# ======================================================================
# HealthCheckResult Tests (6 tests)
# ======================================================================

class TestHealthCheckResult:
    """Tests for HealthCheckResult."""

    def test_result_creation_healthy(self):
        """Test creating a healthy result."""
        result = HealthCheckResult(
            name="db_check",
            status=HealthStatus.HEALTHY,
            message="Database is responsive",
        )
        assert result.name == "db_check"
        assert result.status == HealthStatus.HEALTHY
        assert result.message == "Database is responsive"

    def test_result_creation_unhealthy(self):
        """Test creating an unhealthy result."""
        result = HealthCheckResult(
            name="api_check",
            status=HealthStatus.UNHEALTHY,
            message="API not responding",
            details={"error": "connection refused"},
        )
        assert result.status == HealthStatus.UNHEALTHY
        assert result.details["error"] == "connection refused"

    def test_result_defaults(self):
        """Test default values for HealthCheckResult."""
        result = HealthCheckResult(name="test", status=HealthStatus.HEALTHY)
        assert result.message == ""
        assert result.details == {}
        assert result.duration_seconds == 0.0
        assert result.timestamp == 0.0
        assert result.check_type == CheckType.READINESS

    def test_to_dict_serialization(self):
        """Test HealthCheckResult to_dict."""
        result = HealthCheckResult(
            name="test",
            status=HealthStatus.DEGRADED,
            message="slow response",
            details={"latency_ms": 5000},
            duration_seconds=5.0,
            check_type=CheckType.LIVENESS,
        )
        d = result.to_dict()
        assert d["name"] == "test"
        assert d["status"] == "DEGRADED"
        assert d["message"] == "slow response"
        assert d["details"]["latency_ms"] == 5000
        assert d["duration_seconds"] == 5.0
        assert d["check_type"] == "liveness"

    def test_result_with_timestamp(self):
        """Test result with timestamp set."""
        ts = time.time()
        result = HealthCheckResult(
            name="test",
            status=HealthStatus.HEALTHY,
            timestamp=ts,
        )
        assert result.timestamp == ts

    def test_history_entry(self):
        """Test HealthCheckHistoryEntry."""
        result = HealthCheckResult(name="test", status=HealthStatus.HEALTHY)
        entry = HealthCheckHistoryEntry(result=result, timestamp=time.time())
        assert entry.result is result
        assert entry.timestamp > 0


# ======================================================================
# FunctionHealthCheck Tests (8 tests)
# ======================================================================

class TestFunctionHealthCheck:
    """Tests for FunctionHealthCheck."""

    def test_returns_healthy(self):
        """Test function returning HealthStatus.HEALTHY."""
        hc = FunctionHealthCheck(
            "test_check",
            lambda: HealthStatus.HEALTHY,
        )
        result = hc.execute()
        assert result.status == HealthStatus.HEALTHY
        assert result.name == "test_check"

    def test_returns_tuple_status_message(self):
        """Test function returning (status, message) tuple."""
        def check_fn():
            return HealthStatus.DEGRADED, "Running slow"

        hc = FunctionHealthCheck("perf_check", check_fn)
        result = hc.execute()
        assert result.status == HealthStatus.DEGRADED
        assert "Running slow" in result.message

    def test_returns_tuple_with_details(self):
        """Test function returning (status, message, details) tuple."""
        def check_fn():
            return HealthStatus.HEALTHY, "All good", {"latency": 10}

        hc = FunctionHealthCheck("check", check_fn)
        result = hc.execute()
        assert result.status == HealthStatus.HEALTHY
        assert result.details["latency"] == 10

    def test_exception_makes_unhealthy_critical(self):
        """Test that exception in critical check makes it UNHEALTHY."""
        def failing_check():
            raise RuntimeError("connection lost")

        hc = FunctionHealthCheck("db", failing_check, critical=True)
        result = hc.execute()
        assert result.status == HealthStatus.UNHEALTHY
        assert "RuntimeError" in result.details.get("exception_type", "")

    def test_exception_makes_degraded_non_critical(self):
        """Test that exception in non-critical check makes it DEGRADED."""
        def failing_check():
            raise ValueError("minor issue")

        hc = FunctionHealthCheck("metrics", failing_check, critical=False)
        result = hc.execute()
        assert result.status == HealthStatus.DEGRADED

    def test_liveness_check_type(self):
        """Test liveness type health check."""
        hc = FunctionHealthCheck(
            "alive",
            lambda: HealthStatus.HEALTHY,
            check_type=CheckType.LIVENESS,
        )
        result = hc.execute()
        assert result.check_type == CheckType.LIVENESS

    def test_startup_check_type(self):
        """Test startup type health check."""
        hc = FunctionHealthCheck(
            "initialized",
            lambda: HealthStatus.HEALTHY,
            check_type=CheckType.STARTUP,
        )
        result = hc.execute()
        assert result.check_type == CheckType.STARTUP

    def test_duration_is_recorded(self):
        """Test that execution duration is recorded."""
        def slow_check():
            time.sleep(0.01)
            return HealthStatus.HEALTHY

        hc = FunctionHealthCheck("slow", slow_check)
        result = hc.execute()
        assert result.duration_seconds >= 0.005  # at least some time passed


# ======================================================================
# HealthCheckRegistry Tests (8 tests)
# ======================================================================

class TestHealthCheckRegistry:
    """Tests for HealthCheckRegistry."""

    def test_register_and_check_all_healthy(self):
        """Test registry with all healthy checks."""
        registry = HealthCheckRegistry()
        registry.register(FunctionHealthCheck("a", lambda: HealthStatus.HEALTHY))
        registry.register(FunctionHealthCheck("b", lambda: HealthStatus.HEALTHY))

        result = registry.check_all()
        assert isinstance(result, AggregatedHealthResult)
        assert result.status == HealthStatus.HEALTHY
        assert result.total_checks == 2
        assert result.healthy_count == 2

    def test_one_unhealthy_makes_overall_unhealthy(self):
        """Test that one critical unhealthy check makes overall UNHEALTHY."""
        registry = HealthCheckRegistry()
        registry.register(FunctionHealthCheck("good", lambda: HealthStatus.HEALTHY))
        registry.register(FunctionHealthCheck(
            "bad",
            lambda: HealthStatus.UNHEALTHY,
            critical=True,
        ))

        result = registry.check_all()
        assert result.status == HealthStatus.UNHEALTHY
        assert result.unhealthy_count == 1
        assert result.healthy_count == 1

    def test_non_critical_degraded(self):
        """Test non-critical degraded doesn't make overall UNHEALTHY."""
        registry = HealthCheckRegistry()
        registry.register(FunctionHealthCheck("main", lambda: HealthStatus.HEALTHY))
        registry.register(FunctionHealthCheck(
            "minor",
            lambda: HealthStatus.DEGRADED,
            critical=False,
        ))

        result = registry.check_all()
        assert result.status == HealthStatus.DEGRADED

    def test_filter_by_check_type(self):
        """Test filtering checks by type."""
        registry = HealthCheckRegistry()
        registry.register(FunctionHealthCheck(
            "live",
            lambda: HealthStatus.HEALTHY,
            check_type=CheckType.LIVENESS,
        ))
        registry.register(FunctionHealthCheck(
            "ready",
            lambda: HealthStatus.HEALTHY,
            check_type=CheckType.READINESS,
        ))

        liveness_result = registry.check_all(check_type=CheckType.LIVENESS)
        assert liveness_result.total_checks == 1

        readiness_result = registry.check_all(check_type=CheckType.READINESS)
        assert readiness_result.total_checks == 1

    def test_unregister_check(self):
        """Test unregistering a health check."""
        registry = HealthCheckRegistry()
        registry.register(FunctionHealthCheck("a", lambda: HealthStatus.HEALTHY))
        registry.register(FunctionHealthCheck("b", lambda: HealthStatus.HEALTHY))

        assert registry.check_count == 2
        registry.unregister("a")
        assert registry.check_count == 1

    def test_unregister_nonexistent(self):
        """Test unregistering nonexistent check returns False."""
        registry = HealthCheckRegistry()
        result = registry.unregister("nonexistent")
        assert result is False

    def test_get_check(self):
        """Test getting a check by name."""
        registry = HealthCheckRegistry()
        hc = FunctionHealthCheck("my_check", lambda: HealthStatus.HEALTHY)
        registry.register(hc)

        found = registry.get_check("my_check")
        assert found is hc

    def test_history_tracking(self):
        """Test health check history tracking."""
        registry = HealthCheckRegistry(history_size=5)
        registry.register(FunctionHealthCheck("a", lambda: HealthStatus.HEALTHY))

        for _ in range(3):
            registry.check_all()

        history = registry.get_history("a")
        assert len(history) >= 1
        assert all(isinstance(h, HealthCheckHistoryEntry) for h in history)


# ======================================================================
# AggregatedHealthResult Tests (5 tests)
# ======================================================================

class TestAggregatedHealthResult:
    """Tests for AggregatedHealthResult."""

    def test_all_healthy(self):
        """Test aggregation of all healthy checks."""
        registry = HealthCheckRegistry()
        registry.register(FunctionHealthCheck("a", lambda: HealthStatus.HEALTHY))
        registry.register(FunctionHealthCheck("b", lambda: HealthStatus.HEALTHY))
        result = registry.check_all()

        assert result.total_checks == 2
        assert result.healthy_count == 2
        assert result.degraded_count == 0
        assert result.unhealthy_count == 0
        assert result.unknown_count == 0

    def test_mixed_statuses(self):
        """Test aggregation with mixed statuses."""
        registry = HealthCheckRegistry()
        registry.register(FunctionHealthCheck("h", lambda: HealthStatus.HEALTHY))
        registry.register(FunctionHealthCheck(
            "d",
            lambda: HealthStatus.DEGRADED,
            critical=False,
        ))
        registry.register(FunctionHealthCheck(
            "u",
            lambda: HealthStatus.UNHEALTHY,
            critical=True,
        ))

        result = registry.check_all()
        assert result.healthy_count == 1
        assert result.degraded_count == 1
        assert result.unhealthy_count == 1
        assert result.total_checks == 3

    def test_to_dict_serialization(self):
        """Test AggregatedHealthResult to_dict."""
        registry = HealthCheckRegistry()
        registry.register(FunctionHealthCheck("a", lambda: HealthStatus.HEALTHY))
        result = registry.check_all()
        d = result.to_dict()

        assert d["status"] == "HEALTHY"
        assert d["total_checks"] == 1
        assert d["healthy_count"] == 1
        assert "checks" in d
        assert "a" in d["checks"]

    def test_duration_recorded(self):
        """Test that total duration is recorded."""
        def slow():
            time.sleep(0.01)
            return HealthStatus.HEALTHY

        registry = HealthCheckRegistry()
        registry.register(FunctionHealthCheck("slow", slow))
        result = registry.check_all()
        assert result.duration_seconds >= 0.005

    def test_timestamp_set(self):
        """Test that timestamp is set on aggregation."""
        registry = HealthCheckRegistry()
        registry.register(FunctionHealthCheck("a", lambda: HealthStatus.HEALTHY))
        result = registry.check_all()
        assert result.timestamp > 0
