"""
Resilience patterns for LiuHao AI OS.

Provides production-grade resilience patterns including:
- Circuit Breaker: Prevents cascading failures
- Retry: Automatic retry with configurable backoff
- Rate Limiter: Token bucket rate limiting
- Bulkhead: Service isolation via concurrency limits
- Fallback: Graceful degradation and fallback chains
"""

from src.resilience.circuit_breaker import (
    CircuitBreaker,
    CircuitState,
    CircuitOpenError,
    CircuitBreakerConfig,
    CircuitBreakerMetrics,
)

from src.resilience.retry import (
    Retry,
    BackoffStrategy,
    MaxRetriesExceededError,
    RetryConfig,
    RetryStats,
    with_retry,
)

from src.resilience.rate_limiter import (
    TokenBucketRateLimiter,
    RateLimitUnit,
    RateLimitExceededError,
    RateLimiterConfig,
    RateLimiterMetrics,
    rate_limited,
)

from src.resilience.bulkhead import (
    Bulkhead,
    BulkheadRegistry,
    BulkheadFullError,
    BulkheadConfig,
    BulkheadMetrics,
)

from src.resilience.fallback import (
    FallbackHandler,
    DegradationChain,
    FallbackStrategy,
    FallbackResult,
    FallbackMetrics,
)

__all__ = [
    # Circuit Breaker
    "CircuitBreaker",
    "CircuitState",
    "CircuitOpenError",
    "CircuitBreakerConfig",
    "CircuitBreakerMetrics",
    # Retry
    "Retry",
    "BackoffStrategy",
    "MaxRetriesExceededError",
    "RetryConfig",
    "RetryStats",
    "with_retry",
    # Rate Limiter
    "TokenBucketRateLimiter",
    "RateLimitUnit",
    "RateLimitExceededError",
    "RateLimiterConfig",
    "RateLimiterMetrics",
    "rate_limited",
    # Bulkhead
    "Bulkhead",
    "BulkheadRegistry",
    "BulkheadFullError",
    "BulkheadConfig",
    "BulkheadMetrics",
    # Fallback
    "FallbackHandler",
    "DegradationChain",
    "FallbackStrategy",
    "FallbackResult",
    "FallbackMetrics",
]
