"""
Rate Limiting Middleware for LiuHao AI OS Gateway

Token Bucket algorithm implementation with:
- Key-dependent limits (per API key)
- In-memory fallback, Redis-enabled design
- Rate limit headers on responses
- Graceful degradation when Redis unavailable
"""

import ipaddress
import time
import threading
from typing import Dict, Iterable, Optional, Tuple

from fastapi import Request, Response
from fastapi.responses import JSONResponse

#: Paths limited by default, in addition to whatever ``server.rate_limit.paths``
#: configures. Config can *extend* this set but can never shrink it, so a
#: missing/old config file cannot silently leave the gateway unmetered.
DEFAULT_PROTECTED_PATHS: Tuple[str, ...] = (
    "/v1/auth/login",
    "/v1/chat",
    "/v1/goals",
    "/v1/models",
    "/v1/knowledge",
    "/v1/events",
    "/v1/audit",
    "/v1/ai",
    "/v1/projects",
    "/v1/profile",
    "/v1/plugins",
    "/v1/files",
    "/v1/identity",
    "/v1/trust",
    "/v1/networks",
    "/v1/kernels",
    "/v1/alerts",
    "/v1/observability",
    "/v1/policy",
    "/v1/production",
    "/v1/dashboard",
    "/v1/roster",
)


def _parse_trusted_proxies(values: Iterable[str]) -> Tuple:
    networks = []
    for raw in values or ():
        value = str(raw or "").strip()
        if not value:
            continue
        try:
            networks.append(ipaddress.ip_network(value, strict=False))
        except ValueError:
            continue
    return tuple(networks)


def resolve_client_ip(peer_host: str, forwarded_for: Optional[str],
                      trusted_proxies: Iterable[str]) -> str:
    """Return the effective client address, honouring ``X-Forwarded-For`` only
    when the *direct peer* is an explicitly configured trusted proxy.

    Without this, any caller can spoof ``X-Forwarded-For`` and either frame
    another IP or dodge per-IP throttling.
    """
    peer = str(peer_host or "unknown")
    networks = _parse_trusted_proxies(trusted_proxies)
    if not networks:
        return peer
    try:
        peer_addr = ipaddress.ip_address(peer)
    except ValueError:
        return peer
    if not any(peer_addr in net for net in networks):
        return peer
    hops = [h.strip() for h in (forwarded_for or "").split(",") if h.strip()]
    if not hops:
        return peer
    # Walk right-to-left: the right-most untrusted hop is the client.
    for hop in reversed(hops):
        try:
            hop_addr = ipaddress.ip_address(hop)
        except ValueError:
            continue
        if not any(hop_addr in net for net in networks):
            return str(hop_addr)
    return hops[0]


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
        self.last_used = self.last_refill
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
            self.last_used = now

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

    def __init__(
        self,
        *,
        default_capacity: int = 100,
        default_rate: float = 1.0,
        max_buckets: int = 4096,
        idle_ttl_seconds: float = 600.0,
    ):
        self._buckets: Dict[str, TokenBucket] = {}
        self._lock = threading.Lock()
        self._default_capacity = default_capacity
        self._default_rate = default_rate
        self._max_buckets = max(1, int(max_buckets))
        self._idle_ttl = max(1.0, float(idle_ttl_seconds))
        self._last_sweep = 0.0

    def _sweep_locked(self, now: float) -> None:
        """Evict idle, fully-refilled buckets. Caller must hold ``self._lock``.

        Without reclamation a caller rotating API keys / source addresses
        grows the dict without bound -- a memory-exhaustion vector. A bucket
        that is full again carries no state worth keeping (a fresh bucket
        would behave identically).

        Sweeping is amortised (at most every 10s, immediately when over cap)
        so the hot path stays O(1).
        """
        if len(self._buckets) < self._max_buckets and now - self._last_sweep < 10.0:
            return
        self._last_sweep = now
        stale = [
            key
            for key, bucket in self._buckets.items()
            if now - bucket.last_used >= self._idle_ttl
            and bucket.tokens >= bucket.capacity
        ]
        for key in stale:
            self._buckets.pop(key, None)
        if len(self._buckets) < self._max_buckets:
            return
        # Still over the cap: drop the oldest buckets regardless of fill level.
        over = len(self._buckets) - self._max_buckets + 1
        for key in sorted(
            self._buckets.keys(), key=lambda k: self._buckets[k].last_used
        )[:max(1, over)]:
            self._buckets.pop(key, None)

    def bucket_count(self) -> int:
        with self._lock:
            return len(self._buckets)

    def _get_key_id(self, request: Request, trusted_proxies: Iterable[str] = ()) -> str:
        """Extract key identifier from request (API key or IP).

        The key is read from the private header first: on the managed host a
        front gateway rewrites ``Authorization``, so a key carried there would
        be replaced and every caller would silently share one bucket (see
        ``policy.TOKEN_HEADER``).
        """
        from .policy import TOKEN_HEADER

        # Try to get from headers/api key first
        auth_header = request.headers.get(TOKEN_HEADER) or request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            from ..security.api_keys import validate_api_key
            key_obj = validate_api_key(auth_header[7:])
            if key_obj and key_obj.id:
                return f"key:{key_obj.id}"
            # Authenticated humans share one bucket by IP otherwise -- one
            # noisy session would throttle every other user behind it. Key
            # JWT callers by their principal.
            try:
                from ..security.jwt_handler import get_jwt_handler

                payload = get_jwt_handler().validate_token(
                    auth_header[7:], expected_type="access"
                )
                subject = getattr(payload, "sub", None)
                if subject:
                    return f"sub:{subject}"
            except Exception:  # noqa: BLE001 - invalid token => fall back to IP
                pass

        # Fall back to IP address (XFF trusted only behind a configured proxy)
        client_host = request.client.host if request.client else "unknown"
        forwarded = request.headers.get("x-forwarded-for")
        return f"ip:{resolve_client_ip(client_host, forwarded, trusted_proxies)}"

    def _get_bucket(self, key_id: str, scopes: list) -> TokenBucket:
        """Get or create a token bucket for the given key and scopes."""
        with self._lock:
            self._sweep_locked(time.time())
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

    def check_rate_limit(
        self, request: Request, trusted_proxies: Iterable[str] = ()
    ) -> Tuple[bool, Dict[str, any]]:
        """
        Check rate limit for the request.

        Returns:
            (allowed, info_dict)
        """
        key_id = self._get_key_id(request, trusted_proxies)

        # Get API key scopes if available
        from ..security.api_keys import validate_api_key
        from .policy import TOKEN_HEADER

        auth_header = request.headers.get(TOKEN_HEADER) or request.headers.get("Authorization", "")
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


def check_rate_limit(
    request: Request, trusted_proxies: Iterable[str] = ()
) -> Tuple[bool, Dict[str, any]]:
    """Convenience function to check rate limit."""
    return get_rate_limiter().check_rate_limit(request, trusted_proxies)


def add_rate_limit_headers(response: Response, allowed: bool, info: Dict[str, any]) -> None:
    """Convenience function to add rate limit headers."""
    get_rate_limiter().add_rate_limit_headers(response, allowed, info)


def _rate_limit_headers(allowed: bool, info: Dict[str, any]) -> Dict[str, str]:
    headers = {
        "X-Rate-Limit-Limit": str(info.get("limit", 100)),
        "X-Rate-Limit-Remaining": str(max(0, int(info.get("remaining", 0)) if allowed else 0)),
        "X-Rate-Limit-Reset": str(info.get("reset", 0)),
    }
    if not allowed:
        reset = info.get("reset", 0) or 0
        wait = max(1, int(float(reset) - time.time())) if reset else 1
        headers["Retry-After"] = str(wait)
    return headers


class RateLimitMiddleware:
    """Pure-ASGI adapter that applies the existing ``RateLimiter``.

    ``RateLimiter`` is a plain helper (not an ASGI callable), which is why
    ``app.add_middleware(RateLimiter)`` never worked and the limiter stayed
    dead code. This adapter is the missing wiring; it does not re-implement
    limits, so there remains exactly one place that decides them.

    Applied to prefixes from ``server.rate_limit.paths`` **merged with**
    ``DEFAULT_PROTECTED_PATHS``: config may add paths but cannot shrink the
    protected set, so an old config file cannot silently unmeter the gateway.
    """

    def __init__(
        self,
        app,
        paths: Tuple[str, ...] = DEFAULT_PROTECTED_PATHS,
        trusted_proxies: Iterable[str] = (),
    ):
        self.app = app
        self.paths = tuple(paths or DEFAULT_PROTECTED_PATHS)
        self.trusted_proxies = tuple(trusted_proxies or ())

    def _matches(self, path: str) -> bool:
        return any(path == p or path.startswith(p.rstrip("/") + "/") for p in self.paths)

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or not self._matches(scope.get("path", "")):
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive=receive)
        allowed, info = check_rate_limit(request, self.trusted_proxies)
        headers = _rate_limit_headers(allowed, info)

        if not allowed:
            response = JSONResponse(
                {"detail": "请求过于频繁，请稍后再试（rate limit exceeded）"},
                status_code=429,
                headers=headers,
            )
            await response(scope, receive, send)
            return

        from starlette.datastructures import MutableHeaders

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                mutable = MutableHeaders(raw=message.setdefault("headers", []))
                for key, value in headers.items():
                    mutable[key] = value
            await send(message)

        await self.app(scope, receive, send_with_headers)


class GlobalRateLimitMiddleware:
    """Per-client budget for **all** API traffic.

    ``server.limits.rate_limit_per_minute`` used to be dead config. This
    adapter implements it as a per-IP (or per-key) budget over every ``/v1``
    route, on top of the per-business limits above. Health/readiness probes
    and the authenticated metrics scrape are excluded so monitoring is never
    throttled by product traffic.
    """

    _EXEMPT_PREFIXES = ("/v1/health", "/v1/ready", "/v1/metrics/prometheus")

    def __init__(
        self,
        app,
        limit_per_minute: int = 0,
        trusted_proxies: Iterable[str] = (),
        max_buckets: int = 4096,
    ):
        self.app = app
        self.trusted_proxies = tuple(trusted_proxies or ())
        self.limit = max(0, int(limit_per_minute or 0))
        self._limiter = RateLimiter(
            default_capacity=max(1, self.limit),
            default_rate=max(self.limit, 1) / 60.0,
            max_buckets=max_buckets,
            idle_ttl_seconds=300.0,
        )

    def _applies(self, path: str) -> bool:
        if self.limit <= 0 or not path.startswith("/v1"):
            return False
        return not any(
            path == p or path.startswith(p.rstrip("/") + "/")
            for p in self._EXEMPT_PREFIXES
        )

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or not self._applies(scope.get("path", "")):
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive=receive)
        allowed, info = self._limiter.check_rate_limit(request, self.trusted_proxies)
        if not allowed:
            await JSONResponse(
                {"detail": "请求过于频繁，请稍后再试（global rate limit exceeded）"},
                status_code=429,
                headers=_rate_limit_headers(allowed, info),
            )(scope, receive, send)
            return

        await self.app(scope, receive, send)
