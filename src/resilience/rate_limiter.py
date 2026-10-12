"""
Rate Limiter Implementation for LiuHao AI OS.

Implements the token bucket algorithm for rate limiting, supporting
both blocking and non-blocking acquisition modes, and decorator support.
"""

import time
import threading
import functools
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Any, Optional


class RateLimitUnit(Enum):
    """Time units for rate specification.

    Attributes:
        PER_SECOND: Requests per second.
        PER_MINUTE: Requests per minute.
        PER_HOUR: Requests per hour.
    """
    PER_SECOND = "per_second"
    PER_MINUTE = "per_minute"
    PER_HOUR = "per_hour"


class RateLimitExceededError(Exception):
    """Raised when rate limit is exceeded in non-blocking mode."""

    def __init__(self, name: str = "rate_limiter", message: str = None):
        self.name = name
        super().__init__(
            message or f"Rate limit exceeded for '{name}'."
        )


@dataclass
class RateLimiterMetrics:
    """Metrics tracked by a rate limiter.

    Attributes:
        total_requests: Total number of requests processed.
        allowed_requests: Number of requests that were allowed.
        rejected_requests: Number of requests that were rejected.
        total_wait_time: Total time spent waiting for tokens.
        max_wait_time: Maximum wait time for a single request.
    """
    total_requests: int = 0
    allowed_requests: int = 0
    rejected_requests: int = 0
    total_wait_time: float = 0.0
    max_wait_time: float = 0.0

    def to_dict(self) -> dict:
        """Convert metrics to a dictionary.

        Returns:
            Dictionary representation of metrics.
        """
        return {
            "total_requests": self.total_requests,
            "allowed_requests": self.allowed_requests,
            "rejected_requests": self.rejected_requests,
            "total_wait_time": self.total_wait_time,
            "max_wait_time": self.max_wait_time,
        }

    def reset(self) -> None:
        """Reset all metrics to initial values."""
        self.total_requests = 0
        self.allowed_requests = 0
        self.rejected_requests = 0
        self.total_wait_time = 0.0
        self.max_wait_time = 0.0


@dataclass
class RateLimiterConfig:
    """Configuration for a rate limiter.

    Attributes:
        rate: Number of requests allowed per time unit.
        unit: Time unit for the rate (per second, minute, hour).
        burst_capacity: Maximum burst capacity (token bucket size).
        name: Name identifier for the rate limiter.
    """
    rate: float = 10.0
    unit: RateLimitUnit = RateLimitUnit.PER_SECOND
    burst_capacity: float = 10.0
    name: str = "default"

    @property
    def refill_rate(self) -> float:
        """Get the refill rate in tokens per second.

        Returns:
            Tokens per second.
        """
        if self.unit == RateLimitUnit.PER_SECOND:
            return self.rate
        elif self.unit == RateLimitUnit.PER_MINUTE:
            return self.rate / 60.0
        elif self.unit == RateLimitUnit.PER_HOUR:
            return self.rate / 3600.0
        return self.rate


class TokenBucketRateLimiter:
    """Token bucket rate limiter implementation.

    A token bucket algorithm that allows a configurable rate of requests
    with burst capacity. Tokens are added to the bucket at a steady rate
    up to the burst capacity. Each request consumes one token.

    Supports both blocking (wait for token) and non-blocking (fail fast)
    acquisition modes.

    Usage::

        limiter = TokenBucketRateLimiter(rate=10, burst_capacity=20)

        # Non-blocking
        if limiter.try_acquire():
            process_request()

        # Blocking
        limiter.acquire()  # waits until token is available
        process_request()

        # As decorator
        @limiter
        def api_call():
            ...
    """

    def __init__(
        self,
        rate: float = 10.0,
        unit: RateLimitUnit = RateLimitUnit.PER_SECOND,
        burst_capacity: float = 10.0,
        name: str = "default",
    ):
        """Initialize the token bucket rate limiter.

        Args:
            rate: Number of requests allowed per time unit.
            unit: Time unit for the rate.
            burst_capacity: Maximum burst capacity (bucket size).
            name: Name for this rate limiter instance.
        """
        self._config = RateLimiterConfig(
            rate=rate,
            unit=unit,
            burst_capacity=burst_capacity,
            name=name,
        )
        self._tokens = float(burst_capacity)  # Start full
        self._last_refill_time = time.monotonic()
        self._metrics = RateLimiterMetrics()
        self._lock = threading.Lock()

    @property
    def config(self) -> RateLimiterConfig:
        """Get the rate limiter configuration.

        Returns:
            RateLimiterConfig instance.
        """
        return self._config

    @property
    def metrics(self) -> RateLimiterMetrics:
        """Get a copy of current metrics.

        Returns:
            RateLimiterMetrics with current values.
        """
        with self._lock:
            return RateLimiterMetrics(**self._metrics.to_dict())

    @property
    def available_tokens(self) -> float:
        """Get the current number of available tokens.

        Returns:
            Number of tokens currently in the bucket.
        """
        with self._lock:
            self._refill()
            return self._tokens

    def _refill(self) -> None:
        """Refill tokens based on elapsed time.

        Must be called with the lock held.
        """
        now = time.monotonic()
        elapsed = now - self._last_refill_time
        if elapsed > 0:
            tokens_to_add = elapsed * self._config.refill_rate
            self._tokens = min(
                self._tokens + tokens_to_add,
                self._config.burst_capacity,
            )
            self._last_refill_time = now

    def try_acquire(self, tokens: float = 1.0) -> bool:
        """Try to acquire tokens without blocking.

        Args:
            tokens: Number of tokens to acquire.

        Returns:
            True if tokens were acquired, False otherwise.
        """
        with self._lock:
            self._refill()
            self._metrics.total_requests += 1

            if self._tokens >= tokens:
                self._tokens -= tokens
                self._metrics.allowed_requests += 1
                return True
            else:
                self._metrics.rejected_requests += 1
                return False

    def acquire(self, tokens: float = 1.0, timeout: Optional[float] = None) -> bool:
        """Acquire tokens, blocking until available.

        Args:
            tokens: Number of tokens to acquire.
            timeout: Maximum time to wait in seconds. None means wait forever.

        Returns:
            True if tokens were acquired, False if timed out.
        """
        start_time = time.monotonic()

        while True:
            with self._lock:
                self._refill()
                self._metrics.total_requests += 1

                if self._tokens >= tokens:
                    self._tokens -= tokens
                    self._metrics.allowed_requests += 1
                    wait_time = time.monotonic() - start_time
                    self._metrics.total_wait_time += wait_time
                    if wait_time > self._metrics.max_wait_time:
                        self._metrics.max_wait_time = wait_time
                    return True

            # Calculate how long to wait for enough tokens
            with self._lock:
                tokens_needed = tokens - self._tokens
                wait_time = tokens_needed / self._config.refill_rate
                # Small sleep to prevent busy-waiting
                wait_time = max(wait_time, 0.001)

            if timeout is not None:
                elapsed = time.monotonic() - start_time
                if elapsed + wait_time > timeout:
                    # Try one more time after the remaining time
                    remaining = timeout - elapsed
                    if remaining > 0:
                        time.sleep(remaining)
                    with self._lock:
                        self._refill()
                        if self._tokens >= tokens:
                            self._tokens -= tokens
                            self._metrics.allowed_requests += 1
                            return True
                        self._metrics.rejected_requests += 1
                    return False

            time.sleep(min(wait_time, 0.1))  # Sleep at most 100ms per iteration

    def wait_time(self, tokens: float = 1.0) -> float:
        """Estimate the wait time for acquiring tokens.

        Args:
            tokens: Number of tokens to acquire.

        Returns:
            Estimated wait time in seconds. 0 if tokens are available now.
        """
        with self._lock:
            self._refill()
            if self._tokens >= tokens:
                return 0.0
            tokens_needed = tokens - self._tokens
            return tokens_needed / self._config.refill_rate

    def reset(self) -> None:
        """Reset the rate limiter to full capacity and clear metrics."""
        with self._lock:
            self._tokens = self._config.burst_capacity
            self._last_refill_time = time.monotonic()
            self._metrics.reset()

    def __call__(self, func: Callable) -> Callable:
        """Use rate limiter as a decorator (blocking mode).

        Args:
            func: The function to wrap.

        Returns:
            Wrapped function that acquires a token before execution.
        """
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            self.acquire()
            return func(*args, **kwargs)
        return wrapper

    def __repr__(self) -> str:
        """Return a string representation of the rate limiter."""
        with self._lock:
            return (
                f"TokenBucketRateLimiter(name={self._config.name!r}, "
                f"rate={self._config.rate}/{self._config.unit.value}, "
                f"burst={self._config.burst_capacity}, "
                f"tokens={self._tokens:.2f})"
            )


def rate_limited(
    rate: float = 10.0,
    unit: RateLimitUnit = RateLimitUnit.PER_SECOND,
    burst_capacity: float = 10.0,
    name: str = "default",
    blocking: bool = True,
) -> Callable:
    """Decorator factory for rate limiting.

    Args:
        rate: Number of requests allowed per time unit.
        unit: Time unit for the rate.
        burst_capacity: Maximum burst capacity.
        name: Name for the rate limiter.
        blocking: Whether to block until tokens are available.

    Returns:
        Decorator function.

    Example::

        @rate_limited(rate=100, unit=RateLimitUnit.PER_MINUTE)
        def api_call():
            ...
    """
    limiter = TokenBucketRateLimiter(
        rate=rate,
        unit=unit,
        burst_capacity=burst_capacity,
        name=name,
    )

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if blocking:
                limiter.acquire()
            else:
                if not limiter.try_acquire():
                    raise RateLimitExceededError(
                        name=name,
                        message=f"Rate limit exceeded for '{name}': "
                                f"{rate} per {unit.value}",
                    )
            return func(*args, **kwargs)
        return wrapper
    return decorator
