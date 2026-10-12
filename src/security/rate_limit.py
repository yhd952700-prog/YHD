"""
Advanced rate limiting for LiuHao AI OS.

Provides multiple rate limiting strategies (token bucket, sliding window,
fixed window), per-user/per-IP/per-endpoint limits, Redis/memory backend
interface, rate limit headers, burst handling, notifications, and
tiered rate limits (free/pro/enterprise).
"""

from __future__ import annotations

import time
import uuid
from abc import ABC, abstractmethod
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class RateLimitStrategy(str, Enum):
    """Rate limiting strategies."""

    TOKEN_BUCKET = "token_bucket"
    SLIDING_WINDOW = "sliding_window"
    FIXED_WINDOW = "fixed_window"


class RateLimitTier(str, Enum):
    """Rate limit tiers for different user plans."""

    FREE = "free"
    PRO = "pro"
    ENTERPRISE = "enterprise"


class RateLimitStatus(str, Enum):
    """Status of a rate limit check."""

    ALLOWED = "allowed"
    LIMITED = "limited"
    EXCEEDED = "exceeded"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class RateLimitResult:
    """Result of a rate limit check."""

    allowed: bool
    status: RateLimitStatus
    limit: int
    remaining: int
    reset_time: float  # Unix timestamp when the limit resets
    retry_after: Optional[float] = None  # Seconds to wait before retrying
    key: str = ""
    strategy: RateLimitStrategy = RateLimitStrategy.TOKEN_BUCKET
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RateLimitConfig:
    """Configuration for a rate limit rule."""

    name: str
    limit: int  # Maximum number of requests
    window: int  # Time window in seconds
    strategy: RateLimitStrategy = RateLimitStrategy.TOKEN_BUCKET
    burst: int = 0  # Additional burst capacity (for token bucket)
    scope: str = "global"  # Scope: global, user, ip, endpoint
    tier: RateLimitTier = RateLimitTier.FREE


@dataclass
class RateLimitHeaders:
    """Standard rate limit HTTP headers."""

    limit: int
    remaining: int
    reset: int  # Unix timestamp (seconds)
    retry_after: Optional[int] = None

    def to_dict(self) -> dict[str, str]:
        """Convert to HTTP headers dictionary."""
        headers = {
            "X-RateLimit-Limit": str(self.limit),
            "X-RateLimit-Remaining": str(self.remaining),
            "X-RateLimit-Reset": str(self.reset),
        }
        if self.retry_after is not None:
            headers["Retry-After"] = str(self.retry_after)
        return headers


# ---------------------------------------------------------------------------
# Rate Limit Backend Interface
# ---------------------------------------------------------------------------


class RateLimitBackend(ABC):
    """Abstract base class for rate limit storage backends."""

    @abstractmethod
    def get_token_bucket(
        self, key: str, limit: int, window: int, burst: int = 0
    ) -> tuple[int, float]:
        """Get current token count and last refill time for a key.

        Returns:
            Tuple of (current_tokens, last_refill_time).
        """
        ...

    @abstractmethod
    def set_token_bucket(
        self, key: str, tokens: int, last_refill: float, ttl: int
    ) -> None:
        """Set token bucket state."""
        ...

    @abstractmethod
    def get_sliding_window(self, key: str, window: int) -> deque[float]:
        """Get request timestamps within the current window."""
        ...

    @abstractmethod
    def add_sliding_window(self, key: str, timestamp: float, ttl: int) -> None:
        """Add a request timestamp to the sliding window."""
        ...

    @abstractmethod
    def get_fixed_window(
        self, key: str, window: int
    ) -> tuple[int, float]:
        """Get count and window start for fixed window.

        Returns:
            Tuple of (count, window_start_time).
        """
        ...

    @abstractmethod
    def increment_fixed_window(self, key: str, window: int) -> int:
        """Increment fixed window counter and return new count."""
        ...

    @abstractmethod
    def reset(self, key: str) -> None:
        """Reset rate limit state for a key."""
        ...

    @abstractmethod
    def reset_all(self, pattern: Optional[str] = None) -> int:
        """Reset all rate limit states matching a pattern.

        Returns the count of reset keys.
        """
        ...


class MemoryRateLimitBackend(RateLimitBackend):
    """In-memory rate limit backend for single-process deployments.

    NOT suitable for distributed systems. Use Redis backend for production.
    """

    def __init__(self) -> None:
        self._token_buckets: dict[str, dict[str, Any]] = {}
        self._sliding_windows: dict[str, deque[float]] = {}
        self._fixed_windows: dict[str, dict[str, Any]] = {}

    def get_token_bucket(
        self, key: str, limit: int, window: int, burst: int = 0
    ) -> tuple[int, float]:
        state = self._token_buckets.get(key)
        if state is None:
            max_tokens = limit + burst
            return max_tokens, time.time()
        return state["tokens"], state["last_refill"]

    def set_token_bucket(
        self, key: str, tokens: int, last_refill: float, ttl: int
    ) -> None:
        self._token_buckets[key] = {
            "tokens": tokens,
            "last_refill": last_refill,
            "expires_at": time.time() + ttl,
        }

    def get_sliding_window(self, key: str, window: int) -> deque[float]:
        now = time.time()
        window_data = self._sliding_windows.get(key, deque())
        # Remove expired entries
        cutoff = now - window
        while window_data and window_data[0] < cutoff:
            window_data.popleft()
        return window_data

    def add_sliding_window(self, key: str, timestamp: float, ttl: int) -> None:
        if key not in self._sliding_windows:
            self._sliding_windows[key] = deque()
        self._sliding_windows[key].append(timestamp)

    def get_fixed_window(
        self, key: str, window: int
    ) -> tuple[int, float]:
        now = time.time()
        window_start = now - (now % window)
        state = self._fixed_windows.get(key)
        if state is None or state["window_start"] != window_start:
            return 0, window_start
        return state["count"], state["window_start"]

    def increment_fixed_window(self, key: str, window: int) -> int:
        now = time.time()
        window_start = now - (now % window)
        state = self._fixed_windows.get(key)
        if state is None or state["window_start"] != window_start:
            state = {"count": 0, "window_start": window_start}
            self._fixed_windows[key] = state
        state["count"] += 1
        return state["count"]

    def reset(self, key: str) -> None:
        self._token_buckets.pop(key, None)
        self._sliding_windows.pop(key, None)
        self._fixed_windows.pop(key, None)

    def reset_all(self, pattern: Optional[str] = None) -> int:
        count = 0
        if pattern is None:
            count = (
                len(self._token_buckets)
                + len(self._sliding_windows)
                + len(self._fixed_windows)
            )
            self._token_buckets.clear()
            self._sliding_windows.clear()
            self._fixed_windows.clear()
        else:
            import fnmatch

            for key in list(self._token_buckets.keys()):
                if fnmatch.fnmatch(key, pattern):
                    self._token_buckets.pop(key, None)
                    count += 1
            for key in list(self._sliding_windows.keys()):
                if fnmatch.fnmatch(key, pattern):
                    self._sliding_windows.pop(key, None)
                    count += 1
            for key in list(self._fixed_windows.keys()):
                if fnmatch.fnmatch(key, pattern):
                    self._fixed_windows.pop(key, None)
                    count += 1
        return count

    def cleanup_expired(self) -> int:
        """Remove expired entries.

        Returns the count of cleaned entries.
        """
        now = time.time()
        count = 0

        for key in list(self._token_buckets.keys()):
            if now > self._token_buckets[key].get("expires_at", float("inf")):
                self._token_buckets.pop(key, None)
                count += 1

        for key in list(self._sliding_windows.keys()):
            if not self._sliding_windows[key]:
                self._sliding_windows.pop(key, None)
                count += 1

        for key in list(self._fixed_windows.keys()):
            state = self._fixed_windows[key]
            # Keep windows for at most 2 window periods
            if now - state["window_start"] > 7200:  # 2 hour max
                self._fixed_windows.pop(key, None)
                count += 1

        return count


# ---------------------------------------------------------------------------
# Rate Limiters (per strategy)
# ---------------------------------------------------------------------------


class TokenBucketLimiter:
    """Token bucket rate limiter.

    Tokens are added to the bucket at a steady rate (limit / window tokens
    per second). Each request consumes one token. If the bucket is empty,
    the request is rate limited. Burst capacity allows short bursts above
    the steady rate.
    """

    def __init__(self, backend: RateLimitBackend) -> None:
        self._backend = backend

    def check(
        self,
        key: str,
        limit: int,
        window: int,
        burst: int = 0,
        tokens_requested: int = 1,
    ) -> RateLimitResult:
        """Check and consume tokens from the bucket.

        Args:
            key: Rate limit key.
            limit: Maximum tokens per window (steady rate).
            window: Window size in seconds.
            burst: Additional burst capacity.
            tokens_requested: Number of tokens to consume.

        Returns:
            RateLimitResult with the decision.
        """
        now = time.time()
        max_tokens = limit + burst
        refill_rate = limit / window  # tokens per second

        # Get current state
        current_tokens, last_refill = self._backend.get_token_bucket(
            key, limit, window, burst
        )

        # Refill tokens
        elapsed = now - last_refill
        tokens_to_add = elapsed * refill_rate
        current_tokens = min(max_tokens, current_tokens + tokens_to_add)
        last_refill = now

        # Check if we have enough tokens
        if current_tokens >= tokens_requested:
            current_tokens -= tokens_requested
            allowed = True
            status = RateLimitStatus.ALLOWED
        else:
            allowed = False
            status = RateLimitStatus.LIMITED

        # Calculate reset time
        if allowed:
            # Time until bucket is full
            reset_time = now + (max_tokens - current_tokens) / refill_rate
        else:
            # Time until enough tokens are available
            tokens_needed = tokens_requested - current_tokens
            reset_time = now + tokens_needed / refill_rate

        remaining = int(current_tokens)

        # Save state
        self._backend.set_token_bucket(key, current_tokens, last_refill, window * 2)

        return RateLimitResult(
            allowed=allowed,
            status=status,
            limit=max_tokens,
            remaining=remaining,
            reset_time=reset_time,
            retry_after=None if allowed else (reset_time - now),
            key=key,
            strategy=RateLimitStrategy.TOKEN_BUCKET,
            metadata={
                "burst": burst,
                "refill_rate": refill_rate,
            },
        )


class SlidingWindowLimiter:
    """Sliding window rate limiter.

    Counts requests within a rolling time window. Provides more accurate
    rate limiting than fixed window at the cost of higher memory usage.
    """

    def __init__(self, backend: RateLimitBackend) -> None:
        self._backend = backend

    def check(
        self,
        key: str,
        limit: int,
        window: int,
    ) -> RateLimitResult:
        """Check if a request is allowed within the sliding window.

        Args:
            key: Rate limit key.
            limit: Maximum requests per window.
            window: Window size in seconds.

        Returns:
            RateLimitResult with the decision.
        """
        now = time.time()

        # Get current window entries
        timestamps = self._backend.get_sliding_window(key, window)
        current_count = len(timestamps)

        if current_count < limit:
            # Allow and record
            self._backend.add_sliding_window(key, now, window)
            current_count += 1
            allowed = True
            status = RateLimitStatus.ALLOWED
        else:
            allowed = False
            status = RateLimitStatus.LIMITED

        # Calculate reset time (oldest request + window)
        if timestamps:
            reset_time = timestamps[0] + window
        else:
            reset_time = now + window

        remaining = max(0, limit - current_count)
        retry_after = None if allowed else max(0, reset_time - now)

        return RateLimitResult(
            allowed=allowed,
            status=status,
            limit=limit,
            remaining=remaining,
            reset_time=reset_time,
            retry_after=retry_after,
            key=key,
            strategy=RateLimitStrategy.SLIDING_WINDOW,
        )


class FixedWindowLimiter:
    """Fixed window rate limiter.

    Counts requests within fixed time windows (e.g. per minute, per hour).
    Simpler and more memory-efficient than sliding window, but can allow
    burst at window boundaries.
    """

    def __init__(self, backend: RateLimitBackend) -> None:
        self._backend = backend

    def check(
        self,
        key: str,
        limit: int,
        window: int,
    ) -> RateLimitResult:
        """Check if a request is allowed within the current fixed window.

        Args:
            key: Rate limit key.
            limit: Maximum requests per window.
            window: Window size in seconds.

        Returns:
            RateLimitResult with the decision.
        """
        now = time.time()
        window_start = now - (now % window)
        reset_time = window_start + window

        count, _ = self._backend.get_fixed_window(key, window)

        if count < limit:
            new_count = self._backend.increment_fixed_window(key, window)
            remaining = max(0, limit - new_count)
            allowed = True
            status = RateLimitStatus.ALLOWED
        else:
            remaining = 0
            allowed = False
            status = RateLimitStatus.LIMITED

        retry_after = None if allowed else max(0, reset_time - now)

        return RateLimitResult(
            allowed=allowed,
            status=status,
            limit=limit,
            remaining=remaining,
            reset_time=reset_time,
            retry_after=retry_after,
            key=key,
            strategy=RateLimitStrategy.FIXED_WINDOW,
        )


# ---------------------------------------------------------------------------
# Tiered Rate Limits
# ---------------------------------------------------------------------------


class TieredRateLimits:
    """Tiered rate limit configurations for different user plans."""

    def __init__(self) -> None:
        self._configs: dict[str, dict[RateLimitTier, RateLimitConfig]] = defaultdict(dict)
        self._init_default_tiers()

    def _init_default_tiers(self) -> None:
        """Initialize default tiered rate limits."""
        # API requests per minute
        self.set_limit(
            "api_requests",
            RateLimitTier.FREE,
            RateLimitConfig(
                name="api_requests",
                limit=60,
                window=60,
                strategy=RateLimitStrategy.TOKEN_BUCKET,
                burst=20,
                scope="user",
                tier=RateLimitTier.FREE,
            ),
        )
        self.set_limit(
            "api_requests",
            RateLimitTier.PRO,
            RateLimitConfig(
                name="api_requests",
                limit=600,
                window=60,
                strategy=RateLimitStrategy.TOKEN_BUCKET,
                burst=200,
                scope="user",
                tier=RateLimitTier.PRO,
            ),
        )
        self.set_limit(
            "api_requests",
            RateLimitTier.ENTERPRISE,
            RateLimitConfig(
                name="api_requests",
                limit=6000,
                window=60,
                strategy=RateLimitStrategy.TOKEN_BUCKET,
                burst=1000,
                scope="user",
                tier=RateLimitTier.ENTERPRISE,
            ),
        )

        # Model inference per minute
        self.set_limit(
            "model_inference",
            RateLimitTier.FREE,
            RateLimitConfig(
                name="model_inference",
                limit=10,
                window=60,
                strategy=RateLimitStrategy.SLIDING_WINDOW,
                scope="user",
                tier=RateLimitTier.FREE,
            ),
        )
        self.set_limit(
            "model_inference",
            RateLimitTier.PRO,
            RateLimitConfig(
                name="model_inference",
                limit=100,
                window=60,
                strategy=RateLimitStrategy.SLIDING_WINDOW,
                scope="user",
                tier=RateLimitTier.PRO,
            ),
        )
        self.set_limit(
            "model_inference",
            RateLimitTier.ENTERPRISE,
            RateLimitConfig(
                name="model_inference",
                limit=1000,
                window=60,
                strategy=RateLimitStrategy.SLIDING_WINDOW,
                scope="user",
                tier=RateLimitTier.ENTERPRISE,
            ),
        )

        # Login attempts (fixed window, lower limits)
        self.set_limit(
            "login_attempts",
            RateLimitTier.FREE,
            RateLimitConfig(
                name="login_attempts",
                limit=5,
                window=300,  # 5 minutes
                strategy=RateLimitStrategy.FIXED_WINDOW,
                scope="ip",
                tier=RateLimitTier.FREE,
            ),
        )
        self.set_limit(
            "login_attempts",
            RateLimitTier.PRO,
            RateLimitConfig(
                name="login_attempts",
                limit=10,
                window=300,
                strategy=RateLimitStrategy.FIXED_WINDOW,
                scope="ip",
                tier=RateLimitTier.PRO,
            ),
        )
        self.set_limit(
            "login_attempts",
            RateLimitTier.ENTERPRISE,
            RateLimitConfig(
                name="login_attempts",
                limit=20,
                window=300,
                strategy=RateLimitStrategy.FIXED_WINDOW,
                scope="ip",
                tier=RateLimitTier.ENTERPRISE,
            ),
        )

    def set_limit(
        self,
        limit_name: str,
        tier: RateLimitTier,
        config: RateLimitConfig,
    ) -> None:
        """Set a rate limit configuration for a specific tier."""
        self._configs[limit_name][tier] = config

    def get_limit(
        self,
        limit_name: str,
        tier: RateLimitTier = RateLimitTier.FREE,
    ) -> Optional[RateLimitConfig]:
        """Get the rate limit config for a specific tier and limit name."""
        tier_configs = self._configs.get(limit_name, {})
        return tier_configs.get(tier)

    def list_limits(self) -> list[str]:
        """List all available rate limit names."""
        return list(self._configs.keys())

    def get_all_tiers(self, limit_name: str) -> dict[RateLimitTier, RateLimitConfig]:
        """Get all tier configurations for a limit name."""
        return dict(self._configs.get(limit_name, {}))


# ---------------------------------------------------------------------------
# Rate Limit Manager
# ---------------------------------------------------------------------------


class RateLimitManager:
    """Main rate limit manager coordinating strategies and backends.

    Provides a unified interface for rate limiting with support for
    multiple scopes (user, IP, endpoint), strategies, and tiered plans.
    """

    def __init__(
        self,
        backend: Optional[RateLimitBackend] = None,
        tiered_limits: Optional[TieredRateLimits] = None,
    ) -> None:
        """Initialize the rate limit manager.

        Args:
            backend: Storage backend. Defaults to in-memory.
            tiered_limits: Tiered rate limit configurations.
        """
        self._backend = backend or MemoryRateLimitBackend()
        self._tiered = tiered_limits or TieredRateLimits()

        self._token_bucket = TokenBucketLimiter(self._backend)
        self._sliding_window = SlidingWindowLimiter(self._backend)
        self._fixed_window = FixedWindowLimiter(self._backend)

        self._listeners: list[Callable[[RateLimitResult], None]] = []
        self._notification_threshold = 0.8  # Notify at 80% of limit

    # ---- Core check methods ----

    def check(
        self,
        key: str,
        limit: int,
        window: int,
        strategy: RateLimitStrategy = RateLimitStrategy.TOKEN_BUCKET,
        burst: int = 0,
    ) -> RateLimitResult:
        """Perform a rate limit check with the specified strategy.

        Args:
            key: Unique key for this rate limit (e.g. "user:123:api").
            limit: Maximum requests/tokens.
            window: Time window in seconds.
            strategy: Rate limiting strategy.
            burst: Burst capacity (token bucket only).

        Returns:
            RateLimitResult with the decision.
        """
        if strategy == RateLimitStrategy.TOKEN_BUCKET:
            result = self._token_bucket.check(key, limit, window, burst)
        elif strategy == RateLimitStrategy.SLIDING_WINDOW:
            result = self._sliding_window.check(key, limit, window)
        elif strategy == RateLimitStrategy.FIXED_WINDOW:
            result = self._fixed_window.check(key, limit, window)
        else:
            raise ValueError(f"Unknown strategy: {strategy}")

        # Notify listeners
        self._notify_listeners(result)

        return result

    def check_tiered(
        self,
        limit_name: str,
        identifier: str,
        tier: RateLimitTier = RateLimitTier.FREE,
        scope: Optional[str] = None,
    ) -> RateLimitResult:
        """Check a named rate limit for a specific user tier.

        Args:
            limit_name: Name of the rate limit (e.g. "api_requests").
            identifier: User/IP identifier.
            tier: User plan tier.
            scope: Override scope from config.

        Returns:
            RateLimitResult.
        """
        config = self._tiered.get_limit(limit_name, tier)
        if config is None:
            # Fall back to free tier
            config = self._tiered.get_limit(limit_name, RateLimitTier.FREE)
            if config is None:
                raise ValueError(f"Unknown rate limit: {limit_name}")

        key_scope = scope or config.scope
        key = f"{key_scope}:{identifier}:{limit_name}"

        return self.check(
            key=key,
            limit=config.limit,
            window=config.window,
            strategy=config.strategy,
            burst=config.burst,
        )

    # ---- Scope-specific convenience methods ----

    def check_user(
        self,
        user_id: str,
        limit_name: str,
        tier: RateLimitTier = RateLimitTier.FREE,
    ) -> RateLimitResult:
        """Check a per-user rate limit."""
        return self.check_tiered(limit_name, user_id, tier, scope="user")

    def check_ip(
        self,
        ip_address: str,
        limit_name: str,
        tier: RateLimitTier = RateLimitTier.FREE,
    ) -> RateLimitResult:
        """Check a per-IP rate limit."""
        return self.check_tiered(limit_name, ip_address, tier, scope="ip")

    def check_endpoint(
        self,
        endpoint: str,
        identifier: str,
        limit: int,
        window: int,
        strategy: RateLimitStrategy = RateLimitStrategy.TOKEN_BUCKET,
    ) -> RateLimitResult:
        """Check a per-endpoint rate limit."""
        key = f"endpoint:{endpoint}:{identifier}"
        return self.check(key, limit, window, strategy)

    def check_global(
        self,
        limit_name: str,
        limit: int,
        window: int,
        strategy: RateLimitStrategy = RateLimitStrategy.TOKEN_BUCKET,
    ) -> RateLimitResult:
        """Check a global (system-wide) rate limit."""
        key = f"global:{limit_name}"
        return self.check(key, limit, window, strategy)

    # ---- Headers ----

    @staticmethod
    def make_headers(result: RateLimitResult) -> RateLimitHeaders:
        """Create HTTP headers from a rate limit result."""
        return RateLimitHeaders(
            limit=result.limit,
            remaining=result.remaining,
            reset=int(result.reset_time),
            retry_after=int(result.retry_after) if result.retry_after is not None else None,
        )

    # ---- Notifications ----

    def add_listener(self, callback: Callable[[RateLimitResult], None]) -> None:
        """Add a listener for rate limit events.

        Listeners are called after every rate limit check and can be used
        for logging, alerts, or analytics.
        """
        self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[RateLimitResult], None]) -> bool:
        """Remove a listener. Returns True if found and removed."""
        try:
            self._listeners.remove(callback)
            return True
        except ValueError:
            return False

    def _notify_listeners(self, result: RateLimitResult) -> None:
        """Notify all registered listeners."""
        for listener in self._listeners:
            try:
                listener(result)
            except Exception:
                # Never let a listener break the rate limiter
                pass

    # ---- Management ----

    def reset(self, key: str) -> None:
        """Reset rate limit state for a specific key."""
        self._backend.reset(key)

    def reset_user(self, user_id: str) -> int:
        """Reset all rate limits for a user."""
        return self._backend.reset_all(f"user:{user_id}:*")

    def reset_ip(self, ip_address: str) -> int:
        """Reset all rate limits for an IP."""
        return self._backend.reset_all(f"ip:{ip_address}:*")

    def get_tiered_limits(self) -> TieredRateLimits:
        """Get the tiered rate limits configuration."""
        return self._tiered

    def get_backend(self) -> RateLimitBackend:
        """Get the current backend (for advanced operations)."""
        return self._backend
