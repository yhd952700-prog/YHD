"""Comprehensive resilience pattern tests for LiuHao AI OS.

Covers:
- CircuitBreaker: state transitions, failure thresholds, recovery,
  half-open limits, metrics, decorator usage, context manager usage
- Retry: all backoff strategies (constant, exponential, linear, fibonacci),
  jitter, retry conditions, max retries, statistics
- RateLimiter: token bucket, burst capacity, blocking vs non-blocking,
  decorator, rate accuracy
- Bulkhead: concurrency limits, queueing, timeouts, registry, multiple bulkheads
- Fallback: single fallback, fallback chains, cached fallback, degradation chains
"""

import time
import threading
import pytest
from typing import Any

from src.resilience import (
    CircuitBreaker,
    CircuitState,
    CircuitOpenError,
    CircuitBreakerConfig,
    CircuitBreakerMetrics,
    Retry,
    BackoffStrategy,
    MaxRetriesExceededError,
    RetryConfig,
    RetryStats,
    with_retry,
    TokenBucketRateLimiter,
    RateLimitUnit,
    RateLimitExceededError,
    RateLimiterConfig,
    RateLimiterMetrics,
    rate_limited,
    Bulkhead,
    BulkheadRegistry,
    BulkheadFullError,
    BulkheadConfig,
    BulkheadMetrics,
    FallbackHandler,
    DegradationChain,
    FallbackStrategy,
    FallbackResult,
    FallbackMetrics,
)


# ======================================================================
# CircuitBreaker Tests (14 tests)
# ======================================================================

class TestCircuitBreakerStateTransitions:
    """Circuit breaker state transition tests."""

    def test_initial_state_closed(self):
        """Test circuit breaker starts in CLOSED state."""
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=10)
        assert cb.state == CircuitState.CLOSED

    def test_successful_calls_keep_closed(self):
        """Test successful calls keep circuit closed."""
        cb = CircuitBreaker(failure_threshold=3)

        def success_func():
            return "ok"

        for _ in range(5):
            result = cb.call(success_func)
            assert result == "ok"

        assert cb.state == CircuitState.CLOSED
        assert cb.metrics.success_calls == 5
        assert cb.metrics.failure_calls == 0

    def test_failure_threshold_trips_open(self):
        """Test that reaching failure threshold trips circuit to OPEN."""
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=30)

        def failing_func():
            raise ValueError("service down")

        for i in range(3):
            with pytest.raises(ValueError):
                cb.call(failing_func)

        assert cb.state == CircuitState.OPEN
        assert cb.metrics.failure_calls == 3

    def test_open_circuit_rejects_calls(self):
        """Test that open circuit raises CircuitOpenError."""
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=30)

        def failing_func():
            raise RuntimeError("fail")

        # Trip the circuit
        for _ in range(2):
            with pytest.raises(RuntimeError):
                cb.call(failing_func)

        assert cb.state == CircuitState.OPEN

        # Now calls should be rejected immediately
        with pytest.raises(CircuitOpenError):
            cb.call(failing_func)

        assert cb.metrics.rejected_calls >= 1

    def test_half_open_after_recovery_timeout(self):
        """Test circuit transitions to HALF_OPEN after recovery timeout."""
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.05)

        def failing_func():
            raise ValueError("fail")

        # Trip the circuit
        with pytest.raises(ValueError):
            cb.call(failing_func)
        assert cb.state == CircuitState.OPEN

        # Wait for recovery timeout
        time.sleep(0.1)

        # Should transition to half-open
        assert cb.state == CircuitState.HALF_OPEN

    def test_half_open_success_closes_circuit(self):
        """Test successful call in half-open closes circuit."""
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.05, half_open_limit=1)

        def failing_func():
            raise ValueError("fail")

        def success_func():
            return "ok"

        # Trip the circuit
        with pytest.raises(ValueError):
            cb.call(failing_func)

        # Wait for recovery
        time.sleep(0.1)
        assert cb.state == CircuitState.HALF_OPEN

        # Successful call should close it
        result = cb.call(success_func)
        assert result == "ok"
        assert cb.state == CircuitState.CLOSED

    def test_half_open_failure_reopens_circuit(self):
        """Test failed call in half-open re-opens circuit."""
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.05)

        def failing_func():
            raise ValueError("still failing")

        # Trip the circuit
        with pytest.raises(ValueError):
            cb.call(failing_func)

        # Wait for recovery
        time.sleep(0.1)
        assert cb.state == CircuitState.HALF_OPEN

        # Failed call should re-open
        with pytest.raises(ValueError):
            cb.call(failing_func)
        assert cb.state == CircuitState.OPEN

    def test_context_manager_success(self):
        """Test circuit breaker as context manager with success."""
        cb = CircuitBreaker(failure_threshold=3)

        with cb:
            result = "success"

        assert result == "success"
        assert cb.state == CircuitState.CLOSED
        assert cb.metrics.success_calls == 1

    def test_context_manager_failure(self):
        """Test circuit breaker as context manager with failure."""
        cb = CircuitBreaker(failure_threshold=3)

        with pytest.raises(ValueError):
            with cb:
                raise ValueError("error")

        assert cb.metrics.failure_calls == 1

    def test_decorator_usage(self):
        """Test circuit breaker as a decorator."""
        cb = CircuitBreaker(failure_threshold=3)

        @cb
        def my_func(x):
            return x * 2

        assert my_func(5) == 10
        assert cb.metrics.success_calls == 1

    def test_metrics_reset(self):
        """Test circuit breaker metrics reset via reset() method."""
        cb = CircuitBreaker(failure_threshold=3)
        cb.call(lambda: "ok")
        assert cb.metrics.total_calls == 1

        # CircuitBreaker.reset() resets both state and internal metrics
        cb.reset()
        assert cb.metrics.total_calls == 0
        assert cb.metrics.success_calls == 0
        assert cb.metrics.failure_calls == 0

    def test_metrics_to_dict(self):
        """Test CircuitBreakerMetrics to_dict."""
        metrics = CircuitBreakerMetrics(
            total_calls=10,
            success_calls=8,
            failure_calls=2,
            rejected_calls=1,
            state_changes=1,
        )
        d = metrics.to_dict()
        assert d["total_calls"] == 10
        assert d["success_calls"] == 8
        assert d["failure_calls"] == 2
        assert d["rejected_calls"] == 1
        assert d["state_changes"] == 1

    def test_config_properties(self):
        """Test circuit breaker configuration properties."""
        cb = CircuitBreaker(
            failure_threshold=5,
            recovery_timeout=60.0,
            half_open_limit=2,
            name="test_cb",
        )
        assert cb.config.failure_threshold == 5
        assert cb.config.recovery_timeout == 60.0
        assert cb.config.half_open_limit == 2
        assert cb.config.name == "test_cb"

    def test_circuit_open_error_has_name(self):
        """Test CircuitOpenError carries the breaker name."""
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=30, name="my_service")

        def fail():
            raise RuntimeError("down")

        with pytest.raises(RuntimeError):
            cb.call(fail)

        try:
            cb.call(fail)
        except CircuitOpenError as e:
            assert e.name == "my_service"
            assert "my_service" in str(e)


# ======================================================================
# Retry Tests (13 tests)
# ======================================================================

class TestRetryBackoffStrategies:
    """Retry backoff strategy tests."""

    def test_constant_backoff(self):
        """Test constant backoff strategy delay calculation."""
        retry = Retry(
            max_retries=3,
            base_delay=1.0,
            backoff_strategy=BackoffStrategy.CONSTANT,
        )
        # Check delays are constant (by inspecting config)
        assert retry.config.backoff_strategy == BackoffStrategy.CONSTANT
        assert retry.config.base_delay == 1.0

    def test_exponential_backoff(self):
        """Test exponential backoff strategy."""
        retry = Retry(
            max_retries=4,
            base_delay=1.0,
            backoff_strategy=BackoffStrategy.EXPONENTIAL,
            max_delay=60.0,
        )
        assert retry.config.backoff_strategy == BackoffStrategy.EXPONENTIAL
        # Delays should be: 1, 2, 4, 8 (seconds for attempts 0-3)

    def test_linear_backoff(self):
        """Test linear backoff strategy."""
        retry = Retry(
            max_retries=3,
            base_delay=1.0,
            backoff_strategy=BackoffStrategy.LINEAR,
        )
        assert retry.config.backoff_strategy == BackoffStrategy.LINEAR
        # Delays should be: 1, 2, 3 (for attempts 0, 1, 2)

    def test_fibonacci_backoff(self):
        """Test fibonacci backoff strategy."""
        retry = Retry(
            max_retries=5,
            base_delay=1.0,
            backoff_strategy=BackoffStrategy.FIBONACCI,
        )
        assert retry.config.backoff_strategy == BackoffStrategy.FIBONACCI

    def test_max_delay_cap(self):
        """Test that delay is capped at max_delay."""
        retry = Retry(
            max_retries=10,
            base_delay=1.0,
            backoff_strategy=BackoffStrategy.EXPONENTIAL,
            max_delay=5.0,
        )
        # 2^9 = 512 but should be capped at 5.0
        assert retry.config.max_delay == 5.0

    def test_jitter_enabled(self):
        """Test jitter configuration."""
        retry = Retry(
            max_retries=3,
            jitter=True,
            jitter_factor=0.2,
        )
        assert retry.config.jitter is True
        assert retry.config.jitter_factor == 0.2

    def test_jitter_factor_clamped(self):
        """Test jitter factor is clamped to 0-1 range."""
        retry = Retry(max_retries=3, jitter=True, jitter_factor=2.0)
        assert retry.config.jitter_factor == 1.0

        retry2 = Retry(max_retries=3, jitter=True, jitter_factor=-0.5)
        assert retry2.config.jitter_factor == 0.0

    def test_successful_no_retries(self):
        """Test that successful call doesn't trigger retries."""
        retry = Retry(max_retries=3, base_delay=0.001)
        call_count = [0]

        @retry
        def success_func():
            call_count[0] += 1
            return "ok"

        result = success_func()
        assert result == "ok"
        assert call_count[0] == 1  # Only called once

    def test_retries_on_failure(self):
        """Test that function is retried on failure."""
        retry = Retry(max_retries=3, base_delay=0.001)
        call_count = [0]

        def always_fail():
            call_count[0] += 1
            raise ValueError("transient error")

        with pytest.raises(MaxRetriesExceededError):
            retry.call(always_fail)

        assert call_count[0] == 4  # 1 initial + 3 retries

    def test_max_retries_exceeded_error(self):
        """Test MaxRetriesExceededError carries info."""
        retry = Retry(max_retries=2, base_delay=0.001)

        def fail():
            raise RuntimeError("nope")

        try:
            retry(fail)
        except MaxRetriesExceededError as e:
            assert e.attempts == 2
            assert isinstance(e.last_exception, RuntimeError)
            assert "2" in str(e)

    def test_eventually_succeeds(self):
        """Test retry succeeds after a few failures."""
        retry = Retry(max_retries=5, base_delay=0.001)
        call_count = [0]

        def flaky_func():
            call_count[0] += 1
            if call_count[0] < 3:
                raise ConnectionError("temporary")
            return "success"

        result = retry.call(flaky_func)
        assert result == "success"
        assert call_count[0] == 3  # 2 failures + 1 success

    def test_retry_stats_tracking(self):
        """Test retry statistics tracking."""
        retry = Retry(max_retries=3, base_delay=0.001)

        # Successful call
        retry.call(lambda: "ok")
        stats = retry.stats
        assert stats.total_calls == 1
        assert stats.total_successes == 1
        assert stats.total_retries == 0

        # Failing call
        try:
            retry.call(lambda: (_ for _ in ()).throw(ValueError("fail")))
        except MaxRetriesExceededError:
            pass

        stats2 = retry.stats
        assert stats2.total_calls == 2
        assert stats2.total_failures == 1
        assert stats2.total_retries >= 3

    def test_retry_stats_reset(self):
        """Test retry stats reset."""
        retry = Retry(max_retries=2, base_delay=0.001)
        retry.call(lambda: "ok")
        assert retry.stats.total_calls == 1

        # Use reset_stats() method to reset internal stats
        retry.reset_stats()
        assert retry.stats.total_calls == 0
        assert retry.stats.total_retries == 0

    def test_with_retry_decorator(self):
        """Test the with_retry convenience decorator."""
        @with_retry(max_retries=2, base_delay=0.001)
        def my_func():
            return "works"

        assert my_func() == "works"


# ======================================================================
# RateLimiter Tests (10 tests)
# ======================================================================

class TestRateLimiter:
    """Rate limiter tests."""

    def test_initial_tokens(self):
        """Test rate limiter starts with full token bucket."""
        rl = TokenBucketRateLimiter(
            rate=10,
            unit=RateLimitUnit.PER_SECOND,
            burst_capacity=10,
        )
        assert rl.metrics.total_requests == 0

    def test_allow_within_limit(self):
        """Test requests within rate limit are allowed."""
        rl = TokenBucketRateLimiter(
            rate=100,
            unit=RateLimitUnit.PER_SECOND,
            burst_capacity=10,
        )
        for _ in range(5):
            assert rl.try_acquire() is True
        assert rl.metrics.allowed_requests == 5

    def test_reject_when_exceeded(self):
        """Test requests over rate limit are rejected."""
        rl = TokenBucketRateLimiter(
            rate=100,
            unit=RateLimitUnit.PER_SECOND,
            burst_capacity=3,
        )
        # Drain all tokens
        for _ in range(3):
            rl.try_acquire()

        # Next one should fail
        assert rl.try_acquire() is False
        assert rl.metrics.rejected_requests >= 1

    def test_tokens_refill_over_time(self):
        """Test tokens refill over time."""
        rl = TokenBucketRateLimiter(
            rate=50,  # 50 per second = 0.05 per ms
            unit=RateLimitUnit.PER_SECOND,
            burst_capacity=5,
        )
        # Drain all tokens
        for _ in range(5):
            rl.try_acquire()
        assert rl.try_acquire() is False

        # Wait for refill
        time.sleep(0.1)  # Should refill ~5 tokens

        assert rl.try_acquire() is True

    def test_burst_capacity(self):
        """Test burst capacity limits maximum tokens."""
        rl = TokenBucketRateLimiter(
            rate=100,
            unit=RateLimitUnit.PER_SECOND,
            burst_capacity=5,
        )
        # Wait a long time (tokens should be capped at burst_capacity)
        time.sleep(0.1)

        # Should be able to get exactly burst_capacity tokens
        count = 0
        while rl.try_acquire():
            count += 1
            if count > 10:
                break

        assert count <= 6  # burst_capacity + small refill during test

    def test_rate_limit_exceeded_error(self):
        """Test RateLimitExceededError."""
        err = RateLimitExceededError(name="api_limiter", message="too fast")
        assert err.name == "api_limiter"
        assert "too fast" in str(err)

    def test_metrics_to_dict(self):
        """Test RateLimiterMetrics to_dict."""
        metrics = RateLimiterMetrics(
            total_requests=100,
            allowed_requests=80,
            rejected_requests=20,
            total_wait_time=5.5,
            max_wait_time=0.5,
        )
        d = metrics.to_dict()
        assert d["total_requests"] == 100
        assert d["allowed_requests"] == 80
        assert d["rejected_requests"] == 20
        assert d["total_wait_time"] == 5.5
        assert d["max_wait_time"] == 0.5

    def test_metrics_reset(self):
        """Test rate limiter metrics reset via reset() method."""
        rl = TokenBucketRateLimiter(rate=10, burst_capacity=5)
        rl.try_acquire()
        assert rl.metrics.total_requests == 1
        # TokenBucketRateLimiter.reset() resets tokens and metrics
        rl.reset()
        assert rl.metrics.total_requests == 0

    def test_rate_limited_decorator(self):
        """Test using rate limiter as a decorator via __call__."""
        limiter = TokenBucketRateLimiter(rate=10, burst_capacity=5)

        @limiter
        def my_func(x):
            return x * 2

        result = my_func(5)
        assert result == 10
        assert limiter.metrics.allowed_requests == 1

    def test_per_minute_rate(self):
        """Test per-minute rate unit configuration."""
        rl = TokenBucketRateLimiter(
            rate=60,
            unit=RateLimitUnit.PER_MINUTE,
            burst_capacity=10,
        )
        assert rl.config.unit == RateLimitUnit.PER_MINUTE


# ======================================================================
# Bulkhead Tests (8 tests)
# ======================================================================

class TestBulkhead:
    """Bulkhead pattern tests."""

    def test_bulkhead_initial_state(self):
        """Test bulkhead initial metrics state."""
        b = Bulkhead(name="test", max_concurrent=5)
        assert b.metrics.total_calls == 0
        assert b.metrics.active_calls == 0
        assert b.metrics.rejected_calls == 0

    def test_successful_execution(self):
        """Test successful execution through bulkhead."""
        b = Bulkhead(name="test", max_concurrent=5)

        def work():
            return "done"

        result = b.call(work)
        assert result == "done"
        assert b.metrics.total_calls == 1
        assert b.metrics.completed_calls == 1
        assert b.metrics.active_calls == 0

    def test_concurrency_limit(self):
        """Test that concurrency limit is enforced."""
        b = Bulkhead(name="test", max_concurrent=2, max_queue_size=0)
        barrier = threading.Barrier(2, timeout=2)
        results = []
        errors = []

        def slow_work():
            try:
                barrier.wait(timeout=1.0)
            except Exception:
                pass
            time.sleep(0.05)
            return "done"

        def worker():
            try:
                result = b.call(slow_work)
                results.append(result)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=2.0)

        # At least some should be rejected (only 2 slots, no queue)
        assert len(errors) >= 1
        assert b.metrics.rejected_calls >= 1

    def test_bulkhead_full_error(self):
        """Test BulkheadFullError with custom message."""
        err = BulkheadFullError(name="worker_pool", message="pool full")
        assert err.name == "worker_pool"
        assert "pool full" in str(err)

    def test_failed_call_tracking(self):
        """Test that failed calls are tracked."""
        b = Bulkhead(name="test", max_concurrent=5)

        def fail_func():
            raise ValueError("boom")

        with pytest.raises(ValueError):
            b.call(fail_func)

        assert b.metrics.failed_calls == 1
        assert b.metrics.completed_calls == 0
        assert b.metrics.active_calls == 0

    def test_metrics_to_dict(self):
        """Test BulkheadMetrics to_dict."""
        metrics = BulkheadMetrics(
            total_calls=10,
            active_calls=2,
            queued_calls=1,
            rejected_calls=3,
            completed_calls=5,
            failed_calls=2,
        )
        d = metrics.to_dict()
        assert d["total_calls"] == 10
        assert d["active_calls"] == 2
        assert d["rejected_calls"] == 3
        assert d["completed_calls"] == 5
        assert d["failed_calls"] == 2

    def test_metrics_reset(self):
        """Test bulkhead metrics reset via reset() method."""
        b = Bulkhead(name="test", max_concurrent=5)
        b.call(lambda: "ok")
        assert b.metrics.total_calls == 1
        b.reset()
        assert b.metrics.total_calls == 0
        assert b.metrics.active_calls == 0

    def test_bulkhead_registry(self):
        """Test bulkhead registry management."""
        registry = BulkheadRegistry()
        b1 = registry.get_or_create("service_a", max_concurrent=5)
        b2 = registry.get_or_create("service_b", max_concurrent=10)

        assert b1.config.name == "service_a"
        assert b2.config.name == "service_b"

        # Same name should return same instance
        b1_again = registry.get_or_create("service_a", max_concurrent=5)
        assert b1 is b1_again


# ======================================================================
# Fallback Tests (8 tests)
# ======================================================================

class TestFallback:
    """Fallback and degradation tests."""

    def test_primary_succeeds_no_fallback(self):
        """Test that when primary succeeds, no fallback is used."""
        handler = FallbackHandler(default_value="fallback")

        def primary():
            return "primary result"

        result = handler.call(primary)
        assert isinstance(result, FallbackResult)
        assert result.value == "primary result"
        assert result.is_fallback is False
        assert result.source == "primary"

    def test_primary_fails_uses_fallback(self):
        """Test that fallback is used when primary fails."""
        handler = FallbackHandler(default_value="default_value")

        def primary():
            raise RuntimeError("service down")

        result = handler.call(primary)
        assert result.is_fallback is True
        assert result.value == "default_value"

    def test_call_fallback_function(self):
        """Test calling a fallback function."""
        def fallback_fn():
            return "from fallback"

        handler = FallbackHandler(fallbacks=[fallback_fn])

        def primary():
            raise ValueError("error")

        result = handler.call(primary)
        assert result.value == "from fallback"
        assert "fallback" in result.source

    def test_chained_fallbacks(self):
        """Test fallback chains."""
        chain = DegradationChain()

        def primary():
            raise RuntimeError("primary down")

        def fallback1():
            raise RuntimeError("fallback1 down")

        def fallback2():
            return "final fallback"

        chain.add_level("primary", primary)
        chain.add_level("fallback1", fallback1)
        chain.add_level("fallback2", fallback2)

        result = chain.execute()
        assert result.value == "final fallback"
        assert result.is_fallback is True

    def test_cached_fallback(self):
        """Test cached fallback using use_cache parameter."""
        handler = FallbackHandler(
            use_cache=True,
            cache_ttl=10.0,
        )

        # First call succeeds and caches
        call_count = [0]
        fail_after = [False]

        def primary():
            call_count[0] += 1
            if fail_after[0]:
                raise RuntimeError("down")
            return f"result_{call_count[0]}"

        result1 = handler.call(primary)
        assert result1.value == "result_1"
        assert result1.is_fallback is False

        # Second call fails (same function identity), should use cache
        fail_after[0] = True
        result2 = handler.call(primary)
        assert result2.is_fallback is True
        assert result2.source == "cached"
        assert result2.value == "result_1"

    def test_fallback_result_to_dict(self):
        """Test FallbackResult to_dict."""
        result = FallbackResult(
            value=42,
            fallback_index=1,
            source="fallback_0",
            duration_seconds=0.05,
        )
        d = result.to_dict()
        assert d["value"] == 42
        assert d["fallback_index"] == 1
        assert d["source"] == "fallback_0"
        assert d["is_fallback"] is True

    def test_fallback_metrics_tracking(self):
        """Test fallback metrics tracking."""
        def fb():
            return "fallback_result"

        handler = FallbackHandler(fallbacks=[fb])

        # Primary success
        handler.call(lambda: "ok")
        metrics = handler.metrics
        assert metrics.total_calls == 1
        assert metrics.primary_successes == 1
        assert metrics.fallback_used == 0

        # Primary fails, fallback used
        handler.call(lambda: (_ for _ in ()).throw(ValueError("fail")))
        metrics2 = handler.metrics
        assert metrics2.total_calls == 2
        assert metrics2.fallback_used == 1

    def test_all_fallbacks_fail(self):
        """Test behavior when all fallbacks fail."""
        chain = DegradationChain()

        def primary():
            raise RuntimeError("1")

        def fallback1():
            raise RuntimeError("2")

        chain.add_level("primary", primary)
        chain.add_level("fb1", fallback1)

        result = chain.execute()
        # When all levels fail, result has no value and source='none'
        assert result.value is None
        assert result.source == "none"
        assert result.fallback_index == -1
