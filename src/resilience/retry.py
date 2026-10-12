"""
Retry Pattern Implementation for LiuHao AI OS.

Provides configurable retry logic with multiple backoff strategies,
jitter support, and retry condition customization.

Backoff strategies:
    - constant: Fixed delay between retries
    - exponential: Delay doubles each retry
    - linear: Delay increases linearly
    - fibonacci: Delay follows Fibonacci sequence
"""

import time
import random
import functools
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Any, Optional, Tuple, Type, Union


class BackoffStrategy(Enum):
    """Backoff strategies for retry delay calculation.

    Attributes:
        CONSTANT: Fixed delay between retries.
        EXPONENTIAL: Delay doubles with each retry.
        LINEAR: Delay increases linearly with each retry.
        FIBONACCI: Delay follows Fibonacci sequence.
    """
    CONSTANT = "constant"
    EXPONENTIAL = "exponential"
    LINEAR = "linear"
    FIBONACCI = "fibonacci"


class MaxRetriesExceededError(Exception):
    """Raised when all retry attempts have been exhausted."""

    def __init__(self, message: str = None, attempts: int = 0, last_exception: Exception = None):
        self.attempts = attempts
        self.last_exception = last_exception
        super().__init__(
            message or f"Max retries ({attempts}) exceeded. "
                       f"Last error: {last_exception}"
        )


@dataclass
class RetryStats:
    """Statistics tracked by retry logic.

    Attributes:
        total_calls: Total number of calls made.
        total_retries: Total number of retries across all calls.
        total_failures: Total number of failed calls (after all retries).
        total_successes: Total number of successful calls.
        last_retry_count: Number of retries on the last call.
    """
    total_calls: int = 0
    total_retries: int = 0
    total_failures: int = 0
    total_successes: int = 0
    last_retry_count: int = 0

    def to_dict(self) -> dict:
        """Convert stats to a dictionary.

        Returns:
            Dictionary representation of stats.
        """
        return {
            "total_calls": self.total_calls,
            "total_retries": self.total_retries,
            "total_failures": self.total_failures,
            "total_successes": self.total_successes,
            "last_retry_count": self.last_retry_count,
        }

    def reset(self) -> None:
        """Reset all statistics to initial values."""
        self.total_calls = 0
        self.total_retries = 0
        self.total_failures = 0
        self.total_successes = 0
        self.last_retry_count = 0


@dataclass
class RetryConfig:
    """Configuration for retry logic.

    Attributes:
        max_retries: Maximum number of retry attempts.
        base_delay: Base delay in seconds between retries.
        max_delay: Maximum delay in seconds (caps exponential growth).
        backoff_strategy: Strategy for calculating retry delays.
        jitter: Whether to add random jitter to delays.
        jitter_factor: Factor controlling jitter magnitude (0-1).
        retry_on_exceptions: Tuple of exception types to retry on.
        retry_condition: Optional callable that returns True to retry.
    """
    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 60.0
    backoff_strategy: BackoffStrategy = BackoffStrategy.EXPONENTIAL
    jitter: bool = False
    jitter_factor: float = 0.1
    retry_on_exceptions: Tuple[Type[Exception], ...] = (Exception,)
    retry_condition: Optional[Callable[[Exception], bool]] = None


class Retry:
    """Retry pattern implementation with configurable backoff.

    Automatically retries failed operations with configurable delay
    strategies and jitter to prevent thundering herd problems.

    Usage as a decorator::

        retry = Retry(max_retries=3, backoff_strategy=BackoffStrategy.EXPONENTIAL)

        @retry
        def call_api():
            ...

    Usage as a callable::

        retry = Retry(max_retries=3)
        result = retry(call_api, arg1, arg2)
    """

    def __init__(
        self,
        max_retries: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        backoff_strategy: BackoffStrategy = BackoffStrategy.EXPONENTIAL,
        jitter: bool = False,
        jitter_factor: float = 0.1,
        retry_on_exceptions: Optional[Tuple[Type[Exception], ...]] = None,
        retry_condition: Optional[Callable[[Exception], bool]] = None,
    ):
        """Initialize the retry handler.

        Args:
            max_retries: Maximum number of retry attempts.
            base_delay: Base delay in seconds between retries.
            max_delay: Maximum delay in seconds.
            backoff_strategy: Strategy for delay calculation.
            jitter: Whether to add random jitter.
            jitter_factor: Jitter magnitude factor (0-1).
            retry_on_exceptions: Exception types that trigger retry.
            retry_condition: Custom callable to decide retry.
        """
        self._config = RetryConfig(
            max_retries=max_retries,
            base_delay=base_delay,
            max_delay=max_delay,
            backoff_strategy=backoff_strategy,
            jitter=jitter,
            jitter_factor=min(max(jitter_factor, 0.0), 1.0),
            retry_on_exceptions=retry_on_exceptions or (Exception,),
            retry_condition=retry_condition,
        )
        self._stats = RetryStats()
        self._lock = threading.Lock()
        # Pre-compute fibonacci sequence up to max_retries + 2
        self._fib_cache = self._compute_fibonacci(max_retries + 2)

    @staticmethod
    def _compute_fibonacci(n: int) -> list:
        """Compute Fibonacci sequence up to n terms.

        Args:
            n: Number of terms to compute.

        Returns:
            List of Fibonacci numbers.
        """
        fib = [0, 1]
        for i in range(2, n):
            fib.append(fib[i - 1] + fib[i - 2])
        return fib

    @property
    def stats(self) -> RetryStats:
        """Get a copy of current statistics.

        Returns:
            RetryStats with current values.
        """
        with self._lock:
            return RetryStats(**self._stats.to_dict())

    @property
    def config(self) -> RetryConfig:
        """Get the retry configuration.

        Returns:
            RetryConfig instance.
        """
        return self._config

    def _calculate_delay(self, attempt: int) -> float:
        """Calculate the delay for a given retry attempt.

        Args:
            attempt: The current retry attempt number (0-indexed).

        Returns:
            Delay in seconds.
        """
        base = self._config.base_delay
        strategy = self._config.backoff_strategy

        if strategy == BackoffStrategy.CONSTANT:
            delay = base
        elif strategy == BackoffStrategy.EXPONENTIAL:
            delay = base * (2 ** attempt)
        elif strategy == BackoffStrategy.LINEAR:
            delay = base * (attempt + 1)
        elif strategy == BackoffStrategy.FIBONACCI:
            fib_idx = min(attempt + 2, len(self._fib_cache) - 1)
            delay = base * self._fib_cache[fib_idx]
        else:
            delay = base

        # Apply max delay cap
        delay = min(delay, self._config.max_delay)

        # Apply jitter
        if self._config.jitter:
            jitter_amount = delay * self._config.jitter_factor
            delay = delay + random.uniform(-jitter_amount, jitter_amount)
            delay = max(0, delay)  # Ensure non-negative

        return delay

    def _should_retry(self, exc: Exception) -> bool:
        """Determine if an exception should trigger a retry.

        Args:
            exc: The exception that was raised.

        Returns:
            True if the operation should be retried.
        """
        # Check if exception type is in the retry list
        if not isinstance(exc, self._config.retry_on_exceptions):
            return False

        # Check custom retry condition if provided
        if self._config.retry_condition is not None:
            try:
                return bool(self._config.retry_condition(exc))
            except Exception:
                return False

        return True

    def call(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Execute a function with retry logic.

        Args:
            func: The function to call.
            *args: Positional arguments for the function.
            **kwargs: Keyword arguments for the function.

        Returns:
            The return value of func.

        Raises:
            MaxRetriesExceededError: If all retries are exhausted.
        """
        last_exception: Optional[Exception] = None
        retries = 0

        for attempt in range(self._config.max_retries + 1):
            try:
                result = func(*args, **kwargs)
                # Success
                with self._lock:
                    self._stats.total_calls += 1
                    self._stats.total_successes += 1
                    self._stats.total_retries += retries
                    self._stats.last_retry_count = retries
                return result
            except Exception as exc:
                last_exception = exc
                if attempt < self._config.max_retries and self._should_retry(exc):
                    retries += 1
                    delay = self._calculate_delay(attempt)
                    time.sleep(delay)
                else:
                    break

        # All retries exhausted
        with self._lock:
            self._stats.total_calls += 1
            self._stats.total_failures += 1
            self._stats.total_retries += retries
            self._stats.last_retry_count = retries

        raise MaxRetriesExceededError(
            message=(
                f"Max retries ({self._config.max_retries}) exceeded after "
                f"{retries} retries. Last error: {last_exception}"
            ),
            attempts=retries,
            last_exception=last_exception,
        )

    def reset_stats(self) -> None:
        """Reset all statistics counters."""
        with self._lock:
            self._stats.reset()

    def __call__(self, func: Callable) -> Callable:
        """Use retry as a decorator.

        Args:
            func: The function to wrap.

        Returns:
            Wrapped function with retry logic.
        """
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return self.call(func, *args, **kwargs)
        return wrapper

    def __repr__(self) -> str:
        """Return a string representation of the Retry instance."""
        return (
            f"Retry(max_retries={self._config.max_retries}, "
            f"strategy={self._config.backoff_strategy.value}, "
            f"base_delay={self._config.base_delay}s)"
        )


def with_retry(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    backoff_strategy: BackoffStrategy = BackoffStrategy.EXPONENTIAL,
    jitter: bool = False,
    jitter_factor: float = 0.1,
    retry_on_exceptions: Optional[Tuple[Type[Exception], ...]] = None,
    retry_condition: Optional[Callable[[Exception], bool]] = None,
) -> Callable:
    """Decorator factory for retry logic.

    Args:
        max_retries: Maximum number of retry attempts.
        base_delay: Base delay in seconds.
        max_delay: Maximum delay cap.
        backoff_strategy: Backoff strategy to use.
        jitter: Whether to add jitter.
        jitter_factor: Jitter factor (0-1).
        retry_on_exceptions: Exception types to retry on.
        retry_condition: Custom retry condition callable.

    Returns:
        Decorator function.

    Example::

        @with_retry(max_retries=3, backoff_strategy=BackoffStrategy.EXPONENTIAL)
        def fetch_data():
            ...
    """
    retry = Retry(
        max_retries=max_retries,
        base_delay=base_delay,
        max_delay=max_delay,
        backoff_strategy=backoff_strategy,
        jitter=jitter,
        jitter_factor=jitter_factor,
        retry_on_exceptions=retry_on_exceptions,
        retry_condition=retry_condition,
    )
    return retry
