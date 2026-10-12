"""
Comprehensive tests for LiuHao AI OS resilience, health, and error systems.

Covers:
- Circuit Breaker pattern
- Retry pattern with backoff strategies
- Rate Limiter (token bucket)
- Bulkhead pattern
- Fallback / degradation
- Health check system
- Error code registry
"""

import time
import threading
import pytest
from typing import Any

# Import all modules to test
from src.resilience.circuit_breaker import (
    CircuitBreaker,
    CircuitState,
    CircuitOpenError,
    CircuitBreakerMetrics,
    CircuitBreakerConfig,
)
from src.resilience.retry import (
    Retry,
    BackoffStrategy,
    MaxRetriesExceededError,
    RetryStats,
    RetryConfig,
)
from src.resilience.rate_limiter import (
    TokenBucketRateLimiter,
    RateLimitUnit,
    RateLimitExceededError,
    RateLimiterMetrics,
)
from src.resilience.bulkhead import (
    Bulkhead,
    BulkheadRegistry,
    BulkheadFullError,
    BulkheadMetrics,
)
from src.resilience.fallback import (
    FallbackHandler,
    DegradationChain,
    FallbackResult,
    FallbackMetrics,
    FallbackStrategy,
)
from src.health.health_check import (
    HealthStatus,
    CheckType,
    HealthCheckResult,
    HealthCheck,
    FunctionHealthCheck,
    HealthCheckRegistry,
    AggregatedHealthResult,
)
from src.errors.error_codes import (
    ErrorCategory,
    ErrorSeverity,
    ErrorDefinition,
    LiuHaoErrorCodeError,
    ErrorCodeRegistry,
    DEFAULT_ERROR_DEFINITIONS,
    get_default_registry,
    create_error,
)


# ============================================================================
# Circuit Breaker Tests (5 tests)
# ============================================================================

class TestCircuitBreaker:
    """Tests for the CircuitBreaker pattern."""

    def test_initial_state_is_closed(self):
        """Test that circuit breaker starts in CLOSED state."""
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=10)
        assert cb.state == CircuitState.CLOSED
        assert cb.metrics.total_calls == 0

    def test_successful_calls_keep_circuit_closed(self):
        """Test that successful calls keep the circuit closed."""
        cb = CircuitBreaker(failure_threshold=3)

        def success_func():
            return "ok"

        for _ in range(5):
            result = cb.call(success_func)
            assert result == "ok"

        assert cb.state == CircuitState.CLOSED
        assert cb.metrics.success_calls == 5
        assert cb.metrics.failure_calls == 0

    def test_failure_threshold_trips_circuit(self):
        """Test that reaching the failure threshold opens the circuit."""
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=30)

        def failing_func():
            raise ValueError("test error")

        for _ in range(3):
            with pytest.raises(ValueError):
                cb.call(failing_func)

        assert cb.state == CircuitState.OPEN
        assert cb.metrics.failure_calls == 3
        assert cb.metrics.state_changes == 1

    def test_open_circuit_rejects_calls(self):
        """Test that open circuit immediately rejects calls."""
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=30)

        def failing_func():
            raise ValueError("test error")

        # Trip the circuit
        for _ in range(2):
            with pytest.raises(ValueError):
                cb.call(failing_func)

        # Now calls should be rejected with CircuitOpenError
        with pytest.raises(CircuitOpenError) as exc_info:
            cb.call(failing_func)

        assert "OPEN" in str(exc_info.value)
        assert cb.metrics.rejected_calls == 1

    def test_context_manager_support(self):
        """Test that circuit breaker works as a context manager."""
        cb = CircuitBreaker(failure_threshold=3)

        with cb:
            pass  # Successful call

        assert cb.metrics.success_calls == 1
        assert cb.state == CircuitState.CLOSED

    def test_decorator_support(self):
        """Test that circuit breaker works as a decorator."""
        cb = CircuitBreaker(failure_threshold=3)

        @cb
        def my_func(x):
            return x * 2

        result = my_func(5)
        assert result == 10
        assert cb.metrics.success_calls == 1

    def test_reset_restores_circuit(self):
        """Test that reset() returns circuit to CLOSED state."""
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=30)

        def failing_func():
            raise ValueError("test")

        for _ in range(2):
            with pytest.raises(ValueError):
                cb.call(failing_func)

        assert cb.state == CircuitState.OPEN
        cb.reset()
        assert cb.state == CircuitState.CLOSED
        assert cb.metrics.total_calls == 0


# ============================================================================
# Retry Tests (5 tests)
# ============================================================================

class TestRetry:
    """Tests for the Retry pattern with backoff strategies."""

    def test_successful_call_no_retries(self):
        """Test that a successful call doesn't trigger retries."""
        retry = Retry(max_retries=3, base_delay=0.01)

        def success_func():
            return "success"

        result = retry.call(success_func)
        assert result == "success"
        assert retry.stats.total_successes == 1
        assert retry.stats.total_retries == 0
        assert retry.stats.last_retry_count == 0

    def test_retries_on_failure(self):
        """Test that retries happen when function fails."""
        retry = Retry(max_retries=3, base_delay=0.01)
        call_count = [0]

        def sometimes_fails():
            call_count[0] += 1
            if call_count[0] < 3:
                raise ValueError("transient error")
            return "success"

        result = retry.call(sometimes_fails)
        assert result == "success"
        assert call_count[0] == 3  # 2 failures + 1 success
        assert retry.stats.total_retries == 2

    def test_max_retries_exceeded_raises_error(self):
        """Test that MaxRetriesExceededError is raised when all retries fail."""
        retry = Retry(max_retries=3, base_delay=0.01)

        def always_fails():
            raise ValueError("persistent error")

        with pytest.raises(MaxRetriesExceededError) as exc_info:
            retry.call(always_fails)

        assert exc_info.value.attempts == 3
        assert retry.stats.total_failures == 1
        assert retry.stats.total_retries == 3

    def test_constant_backoff_strategy(self):
        """Test constant backoff strategy calculates correct delays."""
        retry = Retry(
            max_retries=3,
            base_delay=1.0,
            backoff_strategy=BackoffStrategy.CONSTANT,
        )
        # Test internal delay calculation
        delay_0 = retry._calculate_delay(0)
        delay_1 = retry._calculate_delay(1)
        assert delay_0 == delay_1 == 1.0

    def test_exponential_backoff_strategy(self):
        """Test exponential backoff strategy grows delays."""
        retry = Retry(
            max_retries=5,
            base_delay=1.0,
            backoff_strategy=BackoffStrategy.EXPONENTIAL,
            max_delay=60.0,
        )
        delay_0 = retry._calculate_delay(0)
        delay_1 = retry._calculate_delay(1)
        delay_2 = retry._calculate_delay(2)
        assert delay_0 == 1.0
        assert delay_1 == 2.0
        assert delay_2 == 4.0

    def test_jitter_adds_randomness(self):
        """Test that jitter adds random variation to delays."""
        retry = Retry(
            max_retries=3,
            base_delay=1.0,
            backoff_strategy=BackoffStrategy.CONSTANT,
            jitter=True,
            jitter_factor=0.5,
        )
        # With 50% jitter on 1.0s base, delay should be between 0.5 and 1.5
        delays = [retry._calculate_delay(0) for _ in range(20)]
        # Check that all delays are within expected range
        for d in delays:
            assert 0.0 <= d <= 1.5  # 1.0 + 0.5 jitter

    def test_decorator_support(self):
        """Test retry works as a decorator."""
        retry = Retry(max_retries=3, base_delay=0.01)

        @retry
        def my_func(x):
            return x + 1

        result = my_func(5)
        assert result == 6
        assert retry.stats.total_successes == 1


# ============================================================================
# Rate Limiter Tests (5 tests)
# ============================================================================

class TestRateLimiter:
    """Tests for the TokenBucketRateLimiter."""

    def test_initial_tokens_equals_burst_capacity(self):
        """Test that rate limiter starts with full bucket."""
        limiter = TokenBucketRateLimiter(rate=10, burst_capacity=20)
        assert limiter.available_tokens == 20.0

    def test_try_acquire_consumes_tokens(self):
        """Test that try_acquire consumes tokens from the bucket."""
        limiter = TokenBucketRateLimiter(rate=10, burst_capacity=10)

        assert limiter.try_acquire() is True
        assert limiter.available_tokens == 9.0
        assert limiter.metrics.allowed_requests == 1

    def test_try_acquire_returns_false_when_empty(self):
        """Test that try_acquire returns False when no tokens available."""
        limiter = TokenBucketRateLimiter(rate=1, burst_capacity=2)

        assert limiter.try_acquire() is True
        assert limiter.try_acquire() is True
        assert limiter.try_acquire() is False
        assert limiter.metrics.rejected_requests == 1

    def test_token_refill_over_time(self):
        """Test that tokens refill over time at the correct rate."""
        limiter = TokenBucketRateLimiter(
            rate=100,
            unit=RateLimitUnit.PER_SECOND,
            burst_capacity=10,
        )
        # Drain all tokens
        for _ in range(10):
            limiter.try_acquire()
        assert limiter.available_tokens == 0.0

        # Wait for refill
        time.sleep(0.05)  # 50ms should give ~5 tokens at 100/s

        tokens = limiter.available_tokens
        assert tokens > 0
        assert tokens <= 10  # Should not exceed burst capacity

    def test_blocking_acquire_waits_for_token(self):
        """Test that blocking acquire waits for a token."""
        limiter = TokenBucketRateLimiter(
            rate=50,  # 50 per second = 20ms per token
            unit=RateLimitUnit.PER_SECOND,
            burst_capacity=1,
        )
        # Drain the single token
        assert limiter.try_acquire() is True

        # Blocking acquire should wait and succeed
        start = time.monotonic()
        result = limiter.acquire(timeout=1.0)
        elapsed = time.monotonic() - start

        assert result is True
        assert elapsed > 0.01  # Should have waited at least a bit

    def test_rate_per_minute_unit(self):
        """Test rate calculation with per-minute unit."""
        limiter = TokenBucketRateLimiter(
            rate=60,
            unit=RateLimitUnit.PER_MINUTE,
            burst_capacity=5,
        )
        # 60 per minute = 1 per second
        assert abs(limiter.config.refill_rate - 1.0) < 0.001

    def test_decorator_support(self):
        """Test rate limiter works as a decorator."""
        limiter = TokenBucketRateLimiter(rate=100, burst_capacity=10)

        @limiter
        def my_func(x):
            return x * 3

        result = my_func(7)
        assert result == 21
        assert limiter.metrics.allowed_requests == 1


# ============================================================================
# Bulkhead Tests (4 tests)
# ============================================================================

class TestBulkhead:
    """Tests for the Bulkhead isolation pattern."""

    def test_bulkhead_allows_calls_within_limit(self):
        """Test that calls within the concurrency limit are allowed."""
        bulkhead = Bulkhead(max_concurrent=5, max_queue_size=0)

        def quick_func():
            return "done"

        results = []
        for _ in range(3):
            results.append(bulkhead.call(quick_func))

        assert len(results) == 3
        assert all(r == "done" for r in results)
        assert bulkhead.metrics.completed_calls == 3

    def test_bulkhead_rejects_when_full(self):
        """Test that calls are rejected when bulkhead is full."""
        bulkhead = Bulkhead(max_concurrent=1, max_queue_size=0)

        barrier = threading.Event()

        def slow_func():
            barrier.wait(timeout=5)
            return "done"

        # Start one call that will hold the slot
        thread = threading.Thread(target=lambda: bulkhead.call(slow_func))
        thread.start()
        time.sleep(0.05)  # Let the thread acquire the slot

        # This should be rejected immediately
        with pytest.raises(BulkheadFullError):
            bulkhead.call(lambda: "nope")

        barrier.set()
        thread.join(timeout=2)
        assert bulkhead.metrics.rejected_calls == 1

    def test_bulkhead_queue_overflow(self):
        """Test that queue overflow causes immediate rejection."""
        bulkhead = Bulkhead(max_concurrent=1, max_queue_size=1, queue_timeout=5.0)
        start_event = threading.Event()
        release_event = threading.Event()

        def slow_func():
            start_event.set()
            release_event.wait(timeout=5)
            return "done"

        # Start one call that holds the slot
        t1 = threading.Thread(target=lambda: bulkhead.call(slow_func))
        t1.start()
        start_event.wait(timeout=2)  # Wait for t1 to start
        assert bulkhead.active_count == 1

        # Second call should enter the queue (queue size = 1)
        t2_started = threading.Event()
        def queued_func():
            t2_started.set()
            release_event.wait(timeout=5)
            return "done"

        t2 = threading.Thread(target=lambda: bulkhead.call(queued_func))
        t2.start()
        time.sleep(0.1)  # Give t2 time to enter queue

        # Verify t2 is in queue (not yet started executing)
        assert bulkhead.queue_count == 1
        assert not t2_started.is_set()

        # Third call should be rejected immediately (queue is full)
        with pytest.raises(BulkheadFullError):
            bulkhead.call(lambda: "nope")

        assert bulkhead.metrics.rejected_calls >= 1

        # Release both threads
        release_event.set()
        t1.join(timeout=2)
        t2.join(timeout=2)

        assert bulkhead.metrics.completed_calls == 2

    def test_context_manager_support(self):
        """Test bulkhead works as a context manager."""
        bulkhead = Bulkhead(max_concurrent=2)

        with bulkhead:
            assert bulkhead.active_count == 1

        assert bulkhead.active_count == 0
        assert bulkhead.metrics.completed_calls == 1

    def test_bulkhead_registry(self):
        """Test bulkhead registry creates and manages bulkheads."""
        registry = BulkheadRegistry()

        bh1 = registry.get_or_create("db", max_concurrent=10)
        bh2 = registry.get_or_create("ai", max_concurrent=5)
        bh1_again = registry.get_or_create("db", max_concurrent=20)

        assert bh1 is bh1_again  # Same instance
        assert bh1.config.max_concurrent == 10  # Original config preserved
        assert registry.get("db") is bh1
        assert registry.get("nonexistent") is None
        assert registry.remove("db") is True
        assert registry.get("db") is None


# ============================================================================
# Fallback Tests (4 tests)
# ============================================================================

class TestFallback:
    """Tests for the Fallback/degradation pattern."""

    def test_primary_success_returns_value(self):
        """Test that successful primary call returns normally."""
        handler = FallbackHandler(default_value="default")

        def primary():
            return "primary_result"

        result = handler.call(primary)
        assert result.value == "primary_result"
        assert result.source == "primary"
        assert result.fallback_index == 0
        assert not result.is_fallback

    def test_fallback_chain_used_when_primary_fails(self):
        """Test that fallback functions are tried when primary fails."""
        call_order = []

        def primary():
            call_order.append("primary")
            raise ValueError("primary failed")

        def fallback1():
            call_order.append("fb1")
            raise ValueError("fb1 failed")

        def fallback2():
            call_order.append("fb2")
            return "fallback2_result"

        handler = FallbackHandler(
            fallbacks=[fallback1, fallback2],
            default_value="default",
        )

        result = handler.call(primary)
        assert result.value == "fallback2_result"
        assert result.source == "fallback_2"
        assert result.fallback_index == 2
        assert result.is_fallback
        assert call_order == ["primary", "fb1", "fb2"]

    def test_default_value_when_all_fail(self):
        """Test that default value is returned when all fallbacks fail."""
        handler = FallbackHandler(
            fallbacks=[lambda: (_ for _ in ()).throw(ValueError("fb fail"))],
            default_value="ultimate_default",
        )

        def primary():
            raise ValueError("primary fail")

        result = handler.call(primary)
        assert result.value == "ultimate_default"
        assert result.source == "default"
        assert handler.metrics.all_failed == 1

    def test_cached_fallback(self):
        """Test that cached results are used as fallback."""
        call_count = [0]

        def primary():
            call_count[0] += 1
            if call_count[0] == 1:
                return "success"
            raise ValueError("fail")

        handler = FallbackHandler(use_cache=True, cache_ttl=60.0)

        # First call: success, gets cached
        result1 = handler.call(primary)
        assert result1.value == "success"
        assert result1.source == "primary"

        # Second call: fails, returns cached value
        result2 = handler.call(primary)
        assert result2.value == "success"
        assert result2.source == "cached"
        assert handler.metrics.cache_hits >= 1

    def test_degradation_chain(self):
        """Test DegradationChain with multiple levels."""
        chain = DegradationChain(name="feature_x")
        chain.add_level("full", lambda: "full_feature", "Full functionality")
        chain.add_level("reduced", lambda: (_ for _ in ()).throw(ValueError()), "Reduced")
        chain.add_level("minimal", lambda: "minimal_feature", "Minimal functionality")

        result = chain.execute()
        assert result.value == "full_feature"
        assert result.source == "full"
        assert chain.current_level == "full"
        assert chain.level_count == 3


# ============================================================================
# Health Check Tests (4 tests)
# ============================================================================

class TestHealthCheck:
    """Tests for the health check system."""

    def test_health_status_ordering(self):
        """Test health status comparison works correctly."""
        assert HealthStatus.UNHEALTHY.is_worse_than(HealthStatus.DEGRADED)
        assert HealthStatus.DEGRADED.is_worse_than(HealthStatus.HEALTHY)
        assert HealthStatus.UNKNOWN.is_worse_than(HealthStatus.UNHEALTHY)
        assert not HealthStatus.HEALTHY.is_worse_than(HealthStatus.HEALTHY)

    def test_function_health_check_success(self):
        """Test FunctionHealthCheck with a successful check."""
        def check_fn():
            return HealthStatus.HEALTHY, "All good"

        check = FunctionHealthCheck("test_check", check_fn)
        result = check.execute()

        assert result.status == HealthStatus.HEALTHY
        assert result.name == "test_check"
        assert "All good" in result.message
        assert result.duration_seconds >= 0

    def test_function_health_check_failure(self):
        """Test FunctionHealthCheck with a failing check."""
        def check_fn():
            raise ConnectionError("DB unreachable")

        check = FunctionHealthCheck("db_check", check_fn, critical=True)
        result = check.execute()

        assert result.status == HealthStatus.UNHEALTHY
        assert "DB unreachable" in result.message
        assert result.details.get("exception_type") == "ConnectionError"
        assert "DB unreachable" in result.details.get("exception_msg", "")

    def test_health_check_registry_aggregation(self):
        """Test HealthCheckRegistry aggregates results correctly."""
        registry = HealthCheckRegistry()

        registry.register(FunctionHealthCheck(
            "db", lambda: (HealthStatus.HEALTHY, "Database OK"),
            check_type=CheckType.READINESS, critical=True,
        ))
        registry.register(FunctionHealthCheck(
            "cache", lambda: (HealthStatus.DEGRADED, "Cache slow"),
            check_type=CheckType.READINESS, critical=False,
        ))
        registry.register(FunctionHealthCheck(
            "liveness", lambda: HealthStatus.HEALTHY,
            check_type=CheckType.LIVENESS, critical=True,
        ))

        result = registry.check_all(CheckType.READINESS)
        assert isinstance(result, AggregatedHealthResult)
        assert result.total_checks == 2  # Only readiness checks
        assert result.healthy_count == 1
        assert result.degraded_count == 1
        # Non-critical degraded shouldn't make overall unhealthy
        assert result.status == HealthStatus.DEGRADED

    def test_health_check_history_and_trend(self):
        """Test health check history tracking and trend analysis."""
        registry = HealthCheckRegistry(history_size=10)

        call_count = [0]

        def fluctuating_check():
            call_count[0] += 1
            if call_count[0] % 2 == 0:
                return HealthStatus.HEALTHY
            return HealthStatus.DEGRADED

        registry.register(FunctionHealthCheck("flaky", fluctuating_check))

        for _ in range(5):
            registry.check_one("flaky")

        history = registry.get_history("flaky", limit=5)
        assert len(history) == 5

        trend = registry.get_trend("flaky", window=5)
        assert trend["available"] is True
        assert "transitions" in trend
        assert trend["window_size"] == 5

    def test_critical_vs_non_critical(self):
        """Test that critical failures make overall UNHEALTHY."""
        registry = HealthCheckRegistry()

        registry.register(FunctionHealthCheck(
            "critical_fail",
            lambda: (_ for _ in ()).throw(Exception("down")),
            critical=True,
        ))
        registry.register(FunctionHealthCheck(
            "non_critical_fail",
            lambda: (_ for _ in ()).throw(Exception("slow")),
            critical=False,
        ))

        result = registry.check_all()
        # Critical failure should make overall UNHEALTHY
        assert result.status == HealthStatus.UNHEALTHY
        assert result.unhealthy_count == 1
        assert result.degraded_count == 1


# ============================================================================
# Error Codes Tests (4 tests)
# ============================================================================

class TestErrorCodes:
    """Tests for the error code system."""

    def test_error_definition_creation(self):
        """Test ErrorDefinition creation and message formatting."""
        defn = ErrorDefinition(
            code="TEST_ERROR",
            category=ErrorCategory.SYSTEM,
            severity=ErrorSeverity.HIGH,
            message_template="Error in {component}: {detail}",
            http_status=500,
            retryable=True,
        )

        assert defn.code == "TEST_ERROR"
        assert defn.category == ErrorCategory.SYSTEM
        assert defn.severity == ErrorSeverity.HIGH
        assert defn.retryable is True

        msg = defn.format_message(component="db", detail="connection lost")
        assert "db" in msg
        assert "connection lost" in msg

    def test_error_code_registry(self):
        """Test ErrorCodeRegistry registration and lookup."""
        registry = ErrorCodeRegistry()

        defn = ErrorDefinition(
            code="CUSTOM_001",
            category=ErrorCategory.VALIDATION,
            severity=ErrorSeverity.MEDIUM,
            message_template="Validation error",
        )
        registry.register(defn)

        assert registry.has("CUSTOM_001")
        assert registry.get("CUSTOM_001") is defn
        assert registry.count == 1

        codes = registry.list_codes(ErrorCategory.VALIDATION)
        assert "CUSTOM_001" in codes

    def test_create_error_from_registry(self):
        """Test creating error instances from the registry."""
        registry = ErrorCodeRegistry()
        registry.register(ErrorDefinition(
            code="RATE_LIMIT_TEST",
            category=ErrorCategory.RATE_LIMIT,
            severity=ErrorSeverity.MEDIUM,
            message_template="Rate limit exceeded for {resource}",
            http_status=429,
            retryable=True,
        ))

        error = registry.create_error(
            "RATE_LIMIT_TEST",
            message_params={"resource": "api"},
            details={"retry_after": 30},
        )

        assert isinstance(error, LiuHaoErrorCodeError)
        assert error.error_code == "RATE_LIMIT_TEST"
        assert error.category == ErrorCategory.RATE_LIMIT
        assert error.http_status == 429
        assert error.retryable is True
        assert "api" in error.message
        assert error.details["retry_after"] == 30

    def test_default_registry_has_standard_errors(self):
        """Test that the default registry contains standard error codes."""
        registry = get_default_registry()

        assert registry.count > 20  # Should have many standard errors
        assert registry.has("SYSTEM_INTERNAL_ERROR")
        assert registry.has("AUTH_UNAUTHORIZED")
        assert registry.has("VALIDATION_FAILED")
        assert registry.has("RATE_LIMIT_EXCEEDED")
        assert registry.has("NOT_FOUND")
        assert registry.has("AI_PROVIDER_UNAVAILABLE")
        assert registry.has("MEMORY_STORE_ERROR")
        assert registry.has("DATABASE_CONNECTION_ERROR")

    def test_create_error_convenience_function(self):
        """Test the global create_error convenience function."""
        error = create_error(
            "AUTH_TOKEN_EXPIRED",
            details={"token_id": "abc123"},
        )
        assert isinstance(error, LiuHaoErrorCodeError)
        assert error.error_code == "AUTH_TOKEN_EXPIRED"
        assert error.category == ErrorCategory.AUTH
        assert error.http_status == 401

    def test_error_to_dict_serialization(self):
        """Test that errors can be serialized to dictionaries."""
        error = LiuHaoErrorCodeError(
            error_code="TEST",
            message="test message",
            category=ErrorCategory.SYSTEM,
            severity=ErrorSeverity.HIGH,
            details={"key": "value"},
            http_status=500,
            retryable=False,
        )

        d = error.to_dict()
        assert d["error_code"] == "TEST"
        assert d["category"] == "SYSTEM"
        assert d["severity"] == "high"
        assert d["message"] == "test message"
        assert d["details"]["key"] == "value"
        assert d["http_status"] == 500
        assert d["retryable"] is False

    def test_duplicate_registration_raises_error(self):
        """Test that registering a duplicate error code raises ValueError."""
        registry = ErrorCodeRegistry()
        defn = ErrorDefinition(
            code="DUP", category=ErrorCategory.SYSTEM,
            severity=ErrorSeverity.LOW, message_template="x",
        )
        registry.register(defn)

        with pytest.raises(ValueError, match="already registered"):
            registry.register(defn)
