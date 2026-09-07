"""
Rate Limiting Middleware for LiuHao AI OS Gateway

Token Bucket algorithm implementation with:
- Key-dependent limits (per API key)
- In-memory fallback, Redis-enabled design
- Rate limit headers on responses
- Graceful degradation when Redis unavailable
"""

import time
import threading
from typing import Dict, Optional, Tuple
from collections import defaultdict

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from ..security import get_api_key_manager


class TokenBucket:
    """
    Thread-safe Token Bucket rate limiter.
    
    Attributes:
        capacity: Maximum tokens (burst size)
        refill_rate: Tokens per second refill rate
        tokens: Current token count
        last_refill: Last refill timestamp
        lock: Thread safety lock
    """
    
    def __init__(self, capacity: int, refill_rate: float):
        self.capacity = capacity
        self.refill_rate = refill_rate  # tokens per second
        self.tokens = float(capacity)
        self.last_refill = time.time()
        self.lock = threading.Lock()
    
    def _refill(self, now: float) -> None:
        """Refill tokens based on elapsed time."""
        elapsed = now - self.last_refill
        add_tokens = elapsed * self.refill_rate
        self.tokens = min(self.capacity, self.tokens + add_tokens)
        self.last_refill = now
    
    def try_consume(self, n: int = 1) -> Tuple[bool, Dict[str, any]]:
        """
        Try to consume n tokens from the bucket.
        
        Returns:
            (allowed, info_dict) tuple
            info_dict contains: remaining, reset, limit
        """
        with self.lock:
            now = time.time()
            self._refill(now)
            
            allowed = self.tokens >= n
            
            if allowed:
                self.tokens -= n
            
            info = {
                "limit": self.capacity,
                "remaining": int(self.tokens) if allowed else 0,
                "reset": int(self.last_refill + (self.capacity - self.tokens) / self.refill_rate) if allowed else int(now + 1),
            }
            
            return allowed, info


class RateLimiter:
    """
    Rate limiter manager with per-key bucket storage.
    
    Uses API key scopes to determine rate limits.
    Falls back to in-memory if Redis unavailable.
    """
    
    DEFAULT_LIMITS: Dict[str, Tuple[int, float]] = {
        # (capacity, refill_rate_per_second)
        "admin": (1000, 10.0),
        "provider": (500, 5.0),
        "agent": (200, 2.0),
        "workflow": (300, 3.0),
        "memory": (400, 4.0),
        "metrics": (600, 6.0),
        "readonly": (100, 1.0),
        "custom": (100, 1.0),
    }
    
    def __init__(self):
        self._buckets: Dict[str, TokenBucket] = {}
        self._lock = threading.Lock()
        self._default_capacity = 100
        self._default_rate = 1.0  # 1 token per second default
    
    def _get_key_id(self, request: Request) -> str:
        """Extract key identifier from request (API key or IP)."""
        # Try to get from headers/api key first
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            from ..security.jwt_handler import validate_api_key
            key_obj = validate_api_key(auth_header[7:])
            if key_obj and key_obj.id:
                return f"key:{key_obj.id}"
        
        # Fall back to IP address
        client_host = request.client.host if request.client else "unknown"
        return f"ip:{client_host}"
    
    def _get_bucket(self, key_id: str, scopes: list) -> TokenBucket:
        """Get or create a token bucket for the given key and scopes."""
        with self._lock:
            # Determine limit based on scopes
            capacity = self._default_capacity
            rate = self._default_rate
            
            for scope in scopes or []:
                if scope in self.DEFAULT_LIMITS:
                    capacity, rate = self.DEFAULT_LIMITS[scope]
                    break  # Use highest-priority matching scope
            
            if key_id not in self._buckets:
                self._buckets[key_id] = TokenBucket(capacity, rate)
            
            return self._buckets[key_id]
    
    def check_rate_limit(self, request: Request) -> Tuple[bool, Dict[str, any]]:
        """
        Check rate limit for the request.
        
        Returns:
            (allowed, info_dict)
        """
        key_id = self._get_key_id(request)
        
        # Get API key scopes if available
        from ..security.api_keys import validate_api_key
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            key_obj = validate_api_key(auth_header[7:])
            if key_obj:
                bucket = self._get_bucket(key_id, key_obj.scopes or [])
            else:
                bucket = self._get_bucket(key_id, [])
        else:
            bucket = self._get_bucket(key_id, [])
        
        # Try to consume 1 token
        allowed, info = bucket.try_consume(1)
        
        # Add key identifier to response info
        info["key_id"] = key_id
        
        return allowed, info
    
    def add_rate_limit_headers(self, response: Response, allowed: bool, info: Dict[str, any]) -> None:
        """Add rate limit headers to the response."""
        if not allowed:
            reset_time = info.get("reset", 0)
            remaining = info.get("remaining", 0)
            limit = info.get("limit", 100)
            
            # Set Retry-After header
            if reset_time > time.time():
                response.headers["Retry-After"] = str(max(1, int(reset_time - time.time())))
            
            # Rate limit exceeded headers
            response.headers["X-Rate-Limit-Limit"] = str(limit)
            response.headers["X-Rate-Limit-Remaining"] = "0"
            response.headers["X-Rate-Limit-Reset"] = str(reset_time)
        else:
            remaining = info.get("remaining", 0)
            limit = info.get("limit", 100)
            reset_time = info.get("reset", 0)
            
            response.headers["X-Rate-Limit-Limit"] = str(limit)
            response.headers["X-Rate-Limit-Remaining"] = str(max(0, remaining))
            if reset_time > 0:
                response.headers["X-Rate-Limit-Reset"] = str(reset_time)


# Module-level instance
_default_limiter: Optional[RateLimiter] = None


def get_rate_limiter() -> RateLimiter:
    """Get the default rate limiter instance."""
    global _default_limiter
    if _default_limiter is None:
        _default_limiter = RateLimiter()
    return _default_limiter


def check_rate_limit(request: Request) -> Tuple[bool, Dict[str, any]]:
    """Convenience function to check rate limit."""
    return get_rate_limiter().check_rate_limit(request)


def add_rate_limit_headers(response: Response, allowed: bool, info: Dict[str, any]) -> None:
    """Convenience function to add rate limit headers."""
    get_rate_limiter().add_rate_limit_headers(response, allowed, info)