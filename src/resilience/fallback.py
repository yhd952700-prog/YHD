"""
Fallback/Degradation Pattern Implementation for LiuHao AI OS.

Provides graceful degradation with multiple fallback strategies,
fallback caching, and circuit breaker integration.
"""

import time
import threading
import functools
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Any, Optional, List, Dict, Type, Tuple


class FallbackStrategy(Enum):
    """Fallback strategy types.

    Attributes:
        RETURN_DEFAULT: Return a predefined default value.
        CALL_FALLBACK: Call a fallback function.
        CHAINED: Try multiple fallbacks in sequence.
        CACHED: Return a cached previous successful result.
    """
    RETURN_DEFAULT = "return_default"
    CALL_FALLBACK = "call_fallback"
    CHAINED = "chained"
    CACHED = "cached"


@dataclass
class FallbackResult:
    """Result of a fallback operation.

    Attributes:
        value: The fallback result value.
        fallback_index: Which fallback was used (0 for primary, -1 for none).
        source: Source of the result (primary, fallback_N, cached, default).
        duration_seconds: Time taken to get the result.
    """
    value: Any = None
    fallback_index: int = -1
    source: str = "none"
    duration_seconds: float = 0.0

    @property
    def is_fallback(self) -> bool:
        """Check if result came from a fallback (not primary).

        Returns:
            True if result is from a fallback source.
        """
        return self.fallback_index > 0 or self.source not in ("primary", "none")

    def to_dict(self) -> dict:
        """Convert to dictionary.

        Returns:
            Dictionary representation.
        """
        return {
            "value": self.value,
            "fallback_index": self.fallback_index,
            "source": self.source,
            "duration_seconds": self.duration_seconds,
            "is_fallback": self.is_fallback,
        }


@dataclass
class FallbackMetrics:
    """Metrics tracked by fallback handler.

    Attributes:
        total_calls: Total number of calls.
        primary_successes: Calls that succeeded on primary.
        fallback_used: Number of times a fallback was used.
        cache_hits: Number of cache hits.
        cache_misses: Number of cache misses.
        all_failed: Number of calls where all fallbacks failed.
    """
    total_calls: int = 0
    primary_successes: int = 0
    fallback_used: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    all_failed: int = 0

    def to_dict(self) -> dict:
        """Convert to dictionary.

        Returns:
            Dictionary representation.
        """
        return {
            "total_calls": self.total_calls,
            "primary_successes": self.primary_successes,
            "fallback_used": self.fallback_used,
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "all_failed": self.all_failed,
        }

    def reset(self) -> None:
        """Reset all metrics."""
        self.total_calls = 0
        self.primary_successes = 0
        self.fallback_used = 0
        self.cache_hits = 0
        self.cache_misses = 0
        self.all_failed = 0


class FallbackHandler:
    """Fallback/degradation handler with multiple strategies.

    Supports graceful degradation through a chain of fallback functions,
    cached previous results, and default values. Can be integrated with
    circuit breakers for automatic degradation.

    Usage::

        handler = FallbackHandler(
            fallbacks=[
                lambda: fetch_from_cache(),
                lambda: get_default_value(),
            ],
            default_value={"status": "degraded"},
        )

        result = handler.call(primary_function, arg1, arg2)
    """

    def __init__(
        self,
        fallbacks: Optional[List[Callable[[], Any]]] = None,
        default_value: Any = None,
        use_cache: bool = False,
        cache_ttl: float = 300.0,
        catch_exceptions: Tuple[Type[Exception], ...] = (Exception,),
        name: str = "default",
    ):
        """Initialize the fallback handler.

        Args:
            fallbacks: List of fallback functions to try in order.
            default_value: Final default value if all fallbacks fail.
            use_cache: Whether to cache successful results.
            cache_ttl: Time-to-live for cached results in seconds.
            catch_exceptions: Exception types that trigger fallback.
            name: Name for this fallback handler.
        """
        self._fallbacks = fallbacks or []
        self._default_value = default_value
        self._use_cache = use_cache
        self._cache_ttl = cache_ttl
        self._catch_exceptions = catch_exceptions
        self._name = name

        self._cache: Dict[str, Tuple[Any, float]] = {}
        self._metrics = FallbackMetrics()
        self._lock = threading.Lock()

    @property
    def metrics(self) -> FallbackMetrics:
        """Get a copy of current metrics.

        Returns:
            FallbackMetrics with current values.
        """
        with self._lock:
            return FallbackMetrics(**self._metrics.to_dict())

    @property
    def name(self) -> str:
        """Get the handler name.

        Returns:
            Handler name string.
        """
        return self._name

    def _cache_key(self, func: Callable, args: tuple, kwargs: dict) -> str:
        """Generate a cache key for a function call.

        Args:
            func: The function being called.
            args: Positional arguments.
            kwargs: Keyword arguments.

        Returns:
            Cache key string.
        """
        key_parts = [func.__qualname__ if hasattr(func, '__qualname__') else str(func)]
        key_parts.extend(repr(a) for a in args)
        key_parts.extend(f"{k}={repr(v)}" for k, v in sorted(kwargs.items()))
        return "|".join(key_parts)

    def _get_cached(self, key: str) -> Optional[Any]:
        """Get a value from cache if it exists and is not expired.

        Must be called with the lock held.

        Args:
            key: Cache key.

        Returns:
            Cached value, or None if not found/expired.
        """
        if not self._use_cache:
            return None

        entry = self._cache.get(key)
        if entry is None:
            self._metrics.cache_misses += 1
            return None

        value, timestamp = entry
        if time.monotonic() - timestamp > self._cache_ttl:
            # Expired
            del self._cache[key]
            self._metrics.cache_misses += 1
            return None

        self._metrics.cache_hits += 1
        return value

    def _set_cached(self, key: str, value: Any) -> None:
        """Store a value in the cache.

        Must be called with the lock held.

        Args:
            key: Cache key.
            value: Value to cache.
        """
        if not self._use_cache:
            return
        self._cache[key] = (value, time.monotonic())

    def call(
        self,
        func: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> FallbackResult:
        """Execute a function with fallback chain.

        Args:
            func: The primary function to call.
            *args: Positional arguments for the function.
            **kwargs: Keyword arguments for the function.

        Returns:
            FallbackResult with the value and source information.
        """
        start_time = time.monotonic()
        cache_key = self._cache_key(func, args, kwargs) if self._use_cache else ""

        # Try primary function
        try:
            result = func(*args, **kwargs)
            duration = time.monotonic() - start_time
            with self._lock:
                self._metrics.total_calls += 1
                self._metrics.primary_successes += 1
                if self._use_cache:
                    self._set_cached(cache_key, result)
            return FallbackResult(
                value=result,
                fallback_index=0,
                source="primary",
                duration_seconds=duration,
            )
        except self._catch_exceptions as primary_exc:
            # Primary failed, try fallbacks
            pass

        # Try each fallback in order
        for idx, fallback in enumerate(self._fallbacks, start=1):
            try:
                result = fallback()
                duration = time.monotonic() - start_time
                with self._lock:
                    self._metrics.total_calls += 1
                    self._metrics.fallback_used += 1
                return FallbackResult(
                    value=result,
                    fallback_index=idx,
                    source=f"fallback_{idx}",
                    duration_seconds=duration,
                )
            except Exception:
                continue

        # Try cached result as last resort
        if self._use_cache:
            with self._lock:
                cached = self._get_cached(cache_key)
                if cached is not None:
                    duration = time.monotonic() - start_time
                    self._metrics.total_calls += 1
                    self._metrics.fallback_used += 1
                    return FallbackResult(
                        value=cached,
                        fallback_index=len(self._fallbacks) + 1,
                        source="cached",
                        duration_seconds=duration,
                    )

        # All fallbacks failed, return default
        duration = time.monotonic() - start_time
        with self._lock:
            self._metrics.total_calls += 1
            self._metrics.all_failed += 1
        return FallbackResult(
            value=self._default_value,
            fallback_index=-1,
            source="default",
            duration_seconds=duration,
        )

    def clear_cache(self) -> None:
        """Clear the fallback cache."""
        with self._lock:
            self._cache.clear()

    def reset_metrics(self) -> None:
        """Reset all metrics."""
        with self._lock:
            self._metrics.reset()

    def add_fallback(self, fallback: Callable[[], Any]) -> None:
        """Add a fallback function to the end of the chain.

        Args:
            fallback: The fallback function to add.
        """
        with self._lock:
            self._fallbacks.append(fallback)

    def __call__(self, func: Callable) -> Callable:
        """Use fallback handler as a decorator.

        Note: The decorated function returns the raw result value,
        not a FallbackResult object.

        Args:
            func: The function to wrap.

        Returns:
            Wrapped function with fallback logic.
        """
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            result = self.call(func, *args, **kwargs)
            return result.value
        return wrapper

    def __repr__(self) -> str:
        """Return a string representation."""
        return (
            f"FallbackHandler(name={self._name!r}, "
            f"fallbacks={len(self._fallbacks)}, "
            f"cache={'on' if self._use_cache else 'off'})"
        )


class DegradationChain:
    """Graceful degradation chain with multiple service levels.

    Manages a chain of decreasing functionality levels, allowing
    the system to degrade gracefully when services fail.

    Levels are tried in order from highest to lowest functionality.
    Each level has a name, handler function, and description.

    Usage::

        chain = DegradationChain()
        chain.add_level("full", full_feature_handler, "Full functionality")
        chain.add_level("reduced", reduced_handler, "Reduced functionality")
        chain.add_level("minimal", minimal_handler, "Minimal functionality")

        result = chain.execute()
    """

    def __init__(self, name: str = "degradation_chain"):
        """Initialize the degradation chain.

        Args:
            name: Name for this degradation chain.
        """
        self._name = name
        self._levels: List[Dict[str, Any]] = []
        self._current_level: Optional[str] = None
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        """Get the chain name."""
        return self._name

    @property
    def current_level(self) -> Optional[str]:
        """Get the current degradation level name.

        Returns:
            Name of the current level, or None if not set.
        """
        return self._current_level

    @property
    def level_count(self) -> int:
        """Get the number of degradation levels."""
        return len(self._levels)

    def add_level(
        self,
        name: str,
        handler: Callable[..., Any],
        description: str = "",
    ) -> None:
        """Add a degradation level.

        Levels should be added from highest functionality to lowest.

        Args:
            name: Unique name for this level.
            handler: Function to execute at this level.
            description: Human-readable description.
        """
        with self._lock:
            self._levels.append({
                "name": name,
                "handler": handler,
                "description": description,
            })

    def execute(self, *args: Any, **kwargs: Any) -> FallbackResult:
        """Execute the degradation chain.

        Tries each level in order until one succeeds.

        Args:
            *args: Positional arguments to pass to handlers.
            **kwargs: Keyword arguments to pass to handlers.

        Returns:
            FallbackResult with the value and level information.
        """
        start_time = time.monotonic()

        with self._lock:
            levels = list(self._levels)

        for idx, level in enumerate(levels):
            try:
                result = level["handler"](*args, **kwargs)
                duration = time.monotonic() - start_time
                self._current_level = level["name"]
                return FallbackResult(
                    value=result,
                    fallback_index=idx,
                    source=level["name"],
                    duration_seconds=duration,
                )
            except Exception:
                continue

        duration = time.monotonic() - start_time
        self._current_level = None
        return FallbackResult(
            value=None,
            fallback_index=-1,
            source="none",
            duration_seconds=duration,
        )

    def get_level_info(self) -> List[Dict[str, str]]:
        """Get information about all degradation levels.

        Returns:
            List of level info dictionaries.
        """
        with self._lock:
            return [
                {"name": level["name"], "description": level["description"]}
                for level in self._levels
            ]

    def __repr__(self) -> str:
        """Return a string representation."""
        return (
            f"DegradationChain(name={self._name!r}, "
            f"levels={len(self._levels)}, "
            f"current={self._current_level!r})"
        )
