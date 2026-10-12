"""
Circuit Breaker Pattern Implementation for LiuHao AI OS.

Implements the circuit breaker pattern to prevent cascading failures
in distributed systems. The circuit breaker monitors failures and
stops making calls to a failing service, allowing it time to recover.

States:
    - CLOSED: Normal operation, all calls pass through
    - OPEN: Circuit is tripped, all calls are rejected immediately
    - HALF_OPEN: A limited number of calls are allowed to test recovery
"""

import time
import threading
import functools
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Any, Optional, Tuple, Type


class CircuitState(Enum):
    """Circuit breaker states.

    Attributes:
        CLOSED: Normal operation - all requests pass through.
        OPEN: Circuit is tripped - all requests are rejected.
        HALF_OPEN: Recovery testing - limited requests are allowed.
    """
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitOpenError(Exception):
    """Raised when a call is rejected because the circuit is open."""

    def __init__(self, name: str = "circuit_breaker", message: str = None):
        self.name = name
        super().__init__(
            message or f"Circuit breaker '{name}' is OPEN. Calls are rejected."
        )


@dataclass
class CircuitBreakerMetrics:
    """Metrics tracked by a circuit breaker.

    Attributes:
        total_calls: Total number of calls made.
        success_calls: Number of successful calls.
        failure_calls: Number of failed calls.
        rejected_calls: Number of calls rejected while circuit was open.
        state_changes: Number of times the circuit state changed.
        last_failure_time: Timestamp of the last failure.
        last_success_time: Timestamp of the last success.
        last_state_change_time: Timestamp of the last state change.
    """
    total_calls: int = 0
    success_calls: int = 0
    failure_calls: int = 0
    rejected_calls: int = 0
    state_changes: int = 0
    last_failure_time: Optional[float] = None
    last_success_time: Optional[float] = None
    last_state_change_time: Optional[float] = None

    def to_dict(self) -> dict:
        """Convert metrics to a dictionary.

        Returns:
            Dictionary representation of metrics.
        """
        return {
            "total_calls": self.total_calls,
            "success_calls": self.success_calls,
            "failure_calls": self.failure_calls,
            "rejected_calls": self.rejected_calls,
            "state_changes": self.state_changes,
            "last_failure_time": self.last_failure_time,
            "last_success_time": self.last_success_time,
            "last_state_change_time": self.last_state_change_time,
        }

    def reset(self) -> None:
        """Reset all metrics to initial values."""
        self.total_calls = 0
        self.success_calls = 0
        self.failure_calls = 0
        self.rejected_calls = 0
        self.state_changes = 0
        self.last_failure_time = None
        self.last_success_time = None
        self.last_state_change_time = None


@dataclass
class CircuitBreakerConfig:
    """Configuration for a circuit breaker.

    Attributes:
        failure_threshold: Number of consecutive failures before tripping.
        recovery_timeout: Time in seconds to wait before testing recovery.
        half_open_limit: Maximum calls allowed in HALF_OPEN state.
        expected_exceptions: Tuple of exception types considered as failures.
        name: Name identifier for the circuit breaker.
    """
    failure_threshold: int = 5
    recovery_timeout: float = 30.0
    half_open_limit: int = 1
    expected_exceptions: Tuple[Type[Exception], ...] = (Exception,)
    name: str = "default"


class CircuitBreaker:
    """Circuit breaker pattern implementation.

    A circuit breaker monitors calls to a downstream service. When failures
    exceed a threshold, the circuit "opens" and subsequent calls are rejected
    immediately, giving the downstream service time to recover. After a
    timeout, the circuit enters HALF_OPEN state, allowing a limited number
    of calls to test if the service has recovered.

    Usage as a context manager::

        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=10)
        try:
            with cb:
                result = call_service()
        except CircuitOpenError:
            # Handle circuit open
            pass

    Usage as a decorator::

        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=10)

        @cb
        def call_service():
            ...
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        half_open_limit: int = 1,
        expected_exceptions: Optional[Tuple[Type[Exception], ...]] = None,
        name: str = "default",
    ):
        """Initialize the circuit breaker.

        Args:
            failure_threshold: Number of consecutive failures before tripping.
            recovery_timeout: Time in seconds before entering HALF_OPEN.
            half_open_limit: Max calls allowed in HALF_OPEN state.
            expected_exceptions: Exception types that count as failures.
            name: Name for this circuit breaker instance.
        """
        self._config = CircuitBreakerConfig(
            failure_threshold=failure_threshold,
            recovery_timeout=recovery_timeout,
            half_open_limit=half_open_limit,
            expected_exceptions=expected_exceptions or (Exception,),
            name=name,
        )
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._half_open_count = 0
        self._opened_at: Optional[float] = None
        self._metrics = CircuitBreakerMetrics()
        self._lock = threading.RLock()

    @property
    def state(self) -> CircuitState:
        """Get the current circuit state.

        Returns:
            Current CircuitState value.
        """
        with self._lock:
            self._maybe_transition_to_half_open()
            return self._state

    @property
    def metrics(self) -> CircuitBreakerMetrics:
        """Get a copy of current metrics.

        Returns:
            CircuitBreakerMetrics with current values.
        """
        with self._lock:
            return CircuitBreakerMetrics(**self._metrics.to_dict())

    @property
    def config(self) -> CircuitBreakerConfig:
        """Get the circuit breaker configuration.

        Returns:
            CircuitBreakerConfig instance.
        """
        return self._config

    def _maybe_transition_to_half_open(self) -> None:
        """Check if circuit should transition from OPEN to HALF_OPEN.

        Must be called with the lock held.
        """
        if (
            self._state == CircuitState.OPEN
            and self._opened_at is not None
            and (time.monotonic() - self._opened_at) >= self._config.recovery_timeout
        ):
            self._transition_to(CircuitState.HALF_OPEN)
            self._half_open_count = 0

    def _transition_to(self, new_state: CircuitState) -> None:
        """Transition to a new state.

        Must be called with the lock held.

        Args:
            new_state: The state to transition to.
        """
        if self._state != new_state:
            self._state = new_state
            self._metrics.state_changes += 1
            self._metrics.last_state_change_time = time.monotonic()
            if new_state == CircuitState.OPEN:
                self._opened_at = time.monotonic()

    def _record_success(self) -> None:
        """Record a successful call.

        Must be called with the lock held.
        """
        self._metrics.total_calls += 1
        self._metrics.success_calls += 1
        self._metrics.last_success_time = time.monotonic()
        self._failure_count = 0

        if self._state == CircuitState.HALF_OPEN:
            self._half_open_count += 1
            # If we've had enough successes in half-open, close the circuit
            if self._half_open_count >= self._config.half_open_limit:
                self._transition_to(CircuitState.CLOSED)
                self._half_open_count = 0

    def _record_failure(self) -> None:
        """Record a failed call.

        Must be called with the lock held.
        """
        self._metrics.total_calls += 1
        self._metrics.failure_calls += 1
        self._metrics.last_failure_time = time.monotonic()
        self._failure_count += 1

        if self._state == CircuitState.CLOSED:
            if self._failure_count >= self._config.failure_threshold:
                self._transition_to(CircuitState.OPEN)
        elif self._state == CircuitState.HALF_OPEN:
            # Any failure in half-open reopens the circuit
            self._transition_to(CircuitState.OPEN)
            self._half_open_count = 0

    def _record_rejection(self) -> None:
        """Record a rejected call (circuit was open).

        Must be called with the lock held.
        """
        self._metrics.rejected_calls += 1
        self._metrics.total_calls += 1

    def _can_execute(self) -> bool:
        """Check if a call can be executed right now.

        Must be called with the lock held.

        Returns:
            True if the call can proceed, False otherwise.
        """
        self._maybe_transition_to_half_open()

        if self._state == CircuitState.CLOSED:
            return True
        elif self._state == CircuitState.OPEN:
            return False
        elif self._state == CircuitState.HALF_OPEN:
            # Allow limited calls in half-open state
            return self._half_open_count < self._config.half_open_limit
        return False

    def call(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Execute a function under circuit breaker protection.

        Args:
            func: The function to call.
            *args: Positional arguments for the function.
            **kwargs: Keyword arguments for the function.

        Returns:
            The return value of func.

        Raises:
            CircuitOpenError: If the circuit is open.
            Exception: Re-raises any exception from func.
        """
        with self._lock:
            if not self._can_execute():
                self._record_rejection()
                raise CircuitOpenError(
                    name=self._config.name,
                    message=(
                        f"Circuit breaker '{self._config.name}' is OPEN. "
                        f"Failures: {self._failure_count}/"
                        f"{self._config.failure_threshold}. "
                        f"Retry after: {self._config.recovery_timeout:.1f}s"
                    ),
                )

        try:
            result = func(*args, **kwargs)
        except self._config.expected_exceptions:
            with self._lock:
                self._record_failure()
            raise

        with self._lock:
            self._record_success()
        return result

    def reset(self) -> None:
        """Reset the circuit breaker to CLOSED state and clear metrics."""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._half_open_count = 0
            self._opened_at = None
            self._metrics.reset()

    def __call__(self, func: Callable) -> Callable:
        """Use the circuit breaker as a decorator.

        Args:
            func: The function to wrap.

        Returns:
            Wrapped function with circuit breaker protection.
        """
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return self.call(func, *args, **kwargs)
        return wrapper

    def __enter__(self) -> "CircuitBreaker":
        """Enter the context manager.

        Returns:
            Self, for use as context manager.

        Raises:
            CircuitOpenError: If the circuit is open.
        """
        with self._lock:
            if not self._can_execute():
                self._record_rejection()
                raise CircuitOpenError(
                    name=self._config.name,
                    message=(
                        f"Circuit breaker '{self._config.name}' is OPEN. "
                        f"Failures: {self._failure_count}/"
                        f"{self._config.failure_threshold}"
                    ),
                )
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[Any],
    ) -> bool:
        """Exit the context manager, recording success or failure.

        Args:
            exc_type: Exception type if an exception was raised.
            exc_val: Exception value if an exception was raised.
            exc_tb: Traceback if an exception was raised.

        Returns:
            False - exceptions are always propagated.
        """
        with self._lock:
            if exc_type is None:
                self._record_success()
            elif issubclass(exc_type, self._config.expected_exceptions):
                self._record_failure()
            else:
                # Unexpected exceptions don't count as failures
                self._metrics.total_calls += 1
        return False

    def __repr__(self) -> str:
        """Return a string representation of the circuit breaker."""
        with self._lock:
            return (
                f"CircuitBreaker(name={self._config.name!r}, "
                f"state={self._state.value}, "
                f"failures={self._failure_count}/"
                f"{self._config.failure_threshold})"
            )
