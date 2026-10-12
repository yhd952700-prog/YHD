"""
Bulkhead Pattern Implementation for LiuHao AI OS.

Implements the bulkhead (isolation) pattern to prevent cascading failures
by isolating different services into separate concurrency pools. If one
service fails or becomes slow, it only exhausts its own pool and doesn't
affect other services.
"""

import time
import threading
import functools
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Any, Optional, Dict
from collections import deque
import queue


class BulkheadFullError(Exception):
    """Raised when a bulkhead's concurrency limit is reached."""

    def __init__(self, name: str = "bulkhead", message: str = None):
        self.name = name
        super().__init__(
            message or f"Bulkhead '{name}' is full. Call rejected."
        )


@dataclass
class BulkheadMetrics:
    """Metrics tracked by a bulkhead.

    Attributes:
        total_calls: Total number of calls processed.
        active_calls: Number of currently active calls.
        queued_calls: Number of calls currently in the queue.
        rejected_calls: Number of calls rejected due to full capacity.
        completed_calls: Number of successfully completed calls.
        failed_calls: Number of calls that raised exceptions.
        total_wait_time: Total time spent waiting in queue.
        max_wait_time: Maximum wait time in queue.
    """
    total_calls: int = 0
    active_calls: int = 0
    queued_calls: int = 0
    rejected_calls: int = 0
    completed_calls: int = 0
    failed_calls: int = 0
    total_wait_time: float = 0.0
    max_wait_time: float = 0.0

    def to_dict(self) -> dict:
        """Convert metrics to a dictionary.

        Returns:
            Dictionary representation of metrics.
        """
        return {
            "total_calls": self.total_calls,
            "active_calls": self.active_calls,
            "queued_calls": self.queued_calls,
            "rejected_calls": self.rejected_calls,
            "completed_calls": self.completed_calls,
            "failed_calls": self.failed_calls,
            "total_wait_time": self.total_wait_time,
            "max_wait_time": self.max_wait_time,
        }

    def reset(self) -> None:
        """Reset all metrics to initial values."""
        self.total_calls = 0
        self.active_calls = 0
        self.queued_calls = 0
        self.rejected_calls = 0
        self.completed_calls = 0
        self.failed_calls = 0
        self.total_wait_time = 0.0
        self.max_wait_time = 0.0


@dataclass
class BulkheadConfig:
    """Configuration for a bulkhead.

    Attributes:
        max_concurrent: Maximum number of concurrent calls allowed.
        max_queue_size: Maximum number of calls that can wait in queue.
                       0 means no queue, -1 means unlimited queue.
        queue_timeout: Maximum time to wait in queue in seconds.
                      None means wait forever.
        name: Name identifier for the bulkhead.
    """
    max_concurrent: int = 10
    max_queue_size: int = 10
    queue_timeout: Optional[float] = 5.0
    name: str = "default"


class Bulkhead:
    """Bulkhead pattern implementation for service isolation.

    Limits the number of concurrent calls to a service, with an optional
    queue for overflow. This prevents a slow or failing service from
    consuming all available resources and causing cascading failures.

    Usage as a context manager::

        bulkhead = Bulkhead(max_concurrent=10, max_queue_size=5)
        try:
            with bulkhead:
                result = call_service()
        except BulkheadFullError:
            # Handle capacity reached
            pass

    Usage as a decorator::

        bulkhead = Bulkhead(max_concurrent=10)

        @bulkhead
        def call_service():
            ...
    """

    def __init__(
        self,
        max_concurrent: int = 10,
        max_queue_size: int = 10,
        queue_timeout: Optional[float] = 5.0,
        name: str = "default",
    ):
        """Initialize the bulkhead.

        Args:
            max_concurrent: Maximum concurrent calls allowed.
            max_queue_size: Max queue size (0=no queue, -1=unlimited).
            queue_timeout: Max wait time in queue (None=forever).
            name: Name for this bulkhead instance.
        """
        self._config = BulkheadConfig(
            max_concurrent=max_concurrent,
            max_queue_size=max_queue_size,
            queue_timeout=queue_timeout,
            name=name,
        )
        self._active = 0
        self._queue = deque()
        self._metrics = BulkheadMetrics()
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)

    @property
    def config(self) -> BulkheadConfig:
        """Get the bulkhead configuration.

        Returns:
            BulkheadConfig instance.
        """
        return self._config

    @property
    def metrics(self) -> BulkheadMetrics:
        """Get a copy of current metrics.

        Returns:
            BulkheadMetrics with current values.
        """
        with self._lock:
            return BulkheadMetrics(**self._metrics.to_dict())

    @property
    def active_count(self) -> int:
        """Get the number of currently active calls.

        Returns:
            Number of active calls.
        """
        with self._lock:
            return self._active

    @property
    def queue_count(self) -> int:
        """Get the number of calls currently waiting in queue.

        Returns:
            Number of queued calls.
        """
        with self._lock:
            return len(self._queue)

    def _try_enter(self) -> bool:
        """Try to enter the bulkhead (non-blocking check).

        Must be called with the lock held.

        Returns:
            True if entered successfully, False otherwise.
        """
        if self._active < self._config.max_concurrent:
            self._active += 1
            self._metrics.total_calls += 1
            self._metrics.active_calls = self._active
            return True
        return False

    def acquire(self, timeout: Optional[float] = None) -> bool:
        """Acquire a slot in the bulkhead.

        Args:
            timeout: Maximum time to wait. Overrides config queue_timeout.
                    None uses the configured timeout.

        Returns:
            True if slot was acquired, False otherwise.

        Raises:
            BulkheadFullError: If queue is full or timeout expires.
        """
        effective_timeout = timeout if timeout is not None else self._config.queue_timeout
        start_time = time.monotonic()
        queue_entry_time: Optional[float] = None
        queued = False

        with self._condition:
            # Fast path: try to enter immediately
            if self._try_enter():
                return True

            # Check if we can queue
            if self._config.max_queue_size == 0:
                self._metrics.rejected_calls += 1
                self._metrics.total_calls += 1
                return False

            if (
                self._config.max_queue_size > 0
                and len(self._queue) >= self._config.max_queue_size
            ):
                self._metrics.rejected_calls += 1
                self._metrics.total_calls += 1
                return False

            # Add to queue (use thread id or sentinel as queue entry)
            self._queue.append(start_time)
            queued = True
            queue_entry_time = time.monotonic()
            self._metrics.queued_calls = len(self._queue)

            # Wait for a slot to open up
            remaining = effective_timeout
            while True:
                if self._active < self._config.max_concurrent:
                    # Got a slot - remove from queue
                    if queued and self._queue:
                        self._queue.popleft()
                    self._active += 1
                    wait_time = time.monotonic() - queue_entry_time
                    self._metrics.total_wait_time += wait_time
                    if wait_time > self._metrics.max_wait_time:
                        self._metrics.max_wait_time = wait_time
                    self._metrics.total_calls += 1
                    self._metrics.active_calls = self._active
                    self._metrics.queued_calls = len(self._queue)
                    return True

                if remaining is not None and remaining <= 0:
                    # Timeout - remove from queue
                    if queued and self._queue:
                        self._queue.popleft()
                    self._metrics.rejected_calls += 1
                    self._metrics.total_calls += 1
                    self._metrics.queued_calls = len(self._queue)
                    return False

                # Wait for notification
                if remaining is None:
                    self._condition.wait()
                else:
                    self._condition.wait(timeout=remaining)
                    elapsed = time.monotonic() - queue_entry_time
                    remaining = effective_timeout - elapsed

    def release(self) -> None:
        """Release a slot in the bulkhead."""
        with self._condition:
            if self._active > 0:
                self._active -= 1
                self._metrics.active_calls = self._active
                self._metrics.completed_calls += 1
                # Notify waiting threads
                self._condition.notify_all()

    def call(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Execute a function under bulkhead protection.

        Args:
            func: The function to call.
            *args: Positional arguments for the function.
            **kwargs: Keyword arguments for the function.

        Returns:
            The return value of func.

        Raises:
            BulkheadFullError: If bulkhead is at capacity and queue is full.
            Exception: Re-raises any exception from func.
        """
        if not self.acquire():
            raise BulkheadFullError(
                name=self._config.name,
                message=(
                    f"Bulkhead '{self._config.name}' is full. "
                    f"Active: {self._active}/{self._config.max_concurrent}, "
                    f"Queue: {len(self._queue)}/{self._config.max_queue_size}"
                ),
            )

        try:
            result = func(*args, **kwargs)
        except Exception:
            with self._condition:
                if self._active > 0:
                    self._active -= 1
                    self._metrics.active_calls = self._active
                    self._metrics.failed_calls += 1
                    self._condition.notify_all()
            raise
        else:
            self.release()
            return result

    def reset(self) -> None:
        """Reset the bulkhead (only safe when no active calls)."""
        with self._condition:
            self._active = 0
            self._queue.clear()
            self._metrics.reset()
            self._condition.notify_all()

    def __call__(self, func: Callable) -> Callable:
        """Use bulkhead as a decorator.

        Args:
            func: The function to wrap.

        Returns:
            Wrapped function with bulkhead protection.
        """
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return self.call(func, *args, **kwargs)
        return wrapper

    def __enter__(self) -> "Bulkhead":
        """Enter the context manager.

        Returns:
            Self, for use as context manager.

        Raises:
            BulkheadFullError: If bulkhead capacity is reached.
        """
        if not self.acquire():
            raise BulkheadFullError(
                name=self._config.name,
                message=(
                    f"Bulkhead '{self._config.name}' is full. "
                    f"Active: {self._active}/{self._config.max_concurrent}"
                ),
            )
        return self

    def __exit__(
        self,
        exc_type: Optional[type],
        exc_val: Optional[BaseException],
        exc_tb: Optional[Any],
    ) -> bool:
        """Exit the context manager, releasing the slot.

        Args:
            exc_type: Exception type if raised.
            exc_val: Exception value if raised.
            exc_tb: Traceback if raised.

        Returns:
            False - exceptions are always propagated.
        """
        self.release()
        return False

    def __repr__(self) -> str:
        """Return a string representation of the bulkhead."""
        with self._lock:
            return (
                f"Bulkhead(name={self._config.name!r}, "
                f"active={self._active}/{self._config.max_concurrent}, "
                f"queue={len(self._queue)}/{self._config.max_queue_size})"
            )


class BulkheadRegistry:
    """Registry for managing multiple bulkheads by name.

    Provides a central place to create and retrieve bulkheads,
    enabling service isolation across the application.

    Usage::

        registry = BulkheadRegistry()
        db_bulkhead = registry.get_or_create("database", max_concurrent=20)
        ai_bulkhead = registry.get_or_create("ai_provider", max_concurrent=5)
    """

    def __init__(self):
        """Initialize the bulkhead registry."""
        self._bulkheads: Dict[str, Bulkhead] = {}
        self._lock = threading.Lock()

    def get_or_create(
        self,
        name: str,
        max_concurrent: int = 10,
        max_queue_size: int = 10,
        queue_timeout: Optional[float] = 5.0,
    ) -> Bulkhead:
        """Get an existing bulkhead or create a new one.

        Args:
            name: Name of the bulkhead.
            max_concurrent: Max concurrent calls (used if creating).
            max_queue_size: Max queue size (used if creating).
            queue_timeout: Queue timeout (used if creating).

        Returns:
            Bulkhead instance.
        """
        with self._lock:
            if name not in self._bulkheads:
                self._bulkheads[name] = Bulkhead(
                    max_concurrent=max_concurrent,
                    max_queue_size=max_queue_size,
                    queue_timeout=queue_timeout,
                    name=name,
                )
            return self._bulkheads[name]

    def get(self, name: str) -> Optional[Bulkhead]:
        """Get a bulkhead by name.

        Args:
            name: Name of the bulkhead.

        Returns:
            Bulkhead instance, or None if not found.
        """
        with self._lock:
            return self._bulkheads.get(name)

    def remove(self, name: str) -> bool:
        """Remove a bulkhead from the registry.

        Args:
            name: Name of the bulkhead to remove.

        Returns:
            True if removed, False if not found.
        """
        with self._lock:
            if name in self._bulkheads:
                del self._bulkheads[name]
                return True
            return False

    def list_all(self) -> Dict[str, BulkheadMetrics]:
        """Get metrics for all registered bulkheads.

        Returns:
            Dictionary mapping bulkhead names to their metrics.
        """
        with self._lock:
            return {
                name: bulkhead.metrics
                for name, bulkhead in self._bulkheads.items()
            }
