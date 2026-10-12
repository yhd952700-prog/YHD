"""
Base Integration Adapter for LiuHao AI OS.

Provides the abstract base class that all integration adapters must extend,
along with common utilities for authentication, rate limiting, error handling,
and request/response logging.
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin


class AuthType(str, Enum):
    """Supported authentication types."""
    API_KEY = "api_key"
    OAUTH = "oauth"
    BEARER_TOKEN = "bearer_token"
    BASIC_AUTH = "basic_auth"
    WEBHOOK_SECRET = "webhook_secret"
    NONE = "none"


class ConnectionStatus(str, Enum):
    """Connection status enumeration."""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"
    RATE_LIMITED = "rate_limited"


@dataclass
class IntegrationConfig:
    """Configuration for an integration adapter."""
    name: str
    base_url: str
    auth_type: AuthType = AuthType.API_KEY
    api_key: Optional[str] = None
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    webhook_secret: Optional[str] = None
    timeout: int = 30
    max_retries: int = 3
    rate_limit_per_minute: int = 60
    rate_limit_per_hour: int = 1000
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RateLimitState:
    """Tracks rate limiting state for an adapter."""
    requests_this_minute: int = 0
    requests_this_hour: int = 0
    minute_window_start: float = 0.0
    hour_window_start: float = 0.0
    retry_after: float = 0.0
    last_request_time: float = 0.0


@dataclass
class IntegrationStatus:
    """Status report for an integration adapter."""
    name: str
    status: ConnectionStatus
    is_connected: bool
    uptime_seconds: float
    requests_sent: int
    errors_count: int
    rate_limit_hit_count: int
    last_error: Optional[str] = None
    last_request_at: Optional[float] = None
    extra: Dict[str, Any] = field(default_factory=dict)


class IntegrationError(Exception):
    """Base exception for integration errors."""

    def __init__(self, message: str, status_code: Optional[int] = None,
                 response_body: Optional[str] = None,
                 adapter_name: Optional[str] = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body
        self.adapter_name = adapter_name


class RateLimitError(IntegrationError):
    """Raised when rate limit is exceeded."""

    def __init__(self, message: str, retry_after: float = 0.0,
                 adapter_name: Optional[str] = None):
        super().__init__(message, status_code=429, adapter_name=adapter_name)
        self.retry_after = retry_after


class AuthenticationError(IntegrationError):
    """Raised when authentication fails."""

    def __init__(self, message: str = "Authentication failed",
                 status_code: Optional[int] = 401,
                 adapter_name: Optional[str] = None):
        super().__init__(message, status_code=status_code, adapter_name=adapter_name)


class BaseIntegrationAdapter(ABC):
    """
    Abstract base class for all integration adapters in LiuHao AI OS.

    Provides common interface methods, authentication handling,
    rate limiting, error handling, and request/response logging.

    All concrete integration adapters must extend this class and implement
    the abstract methods.
    """

    def __init__(self, config: IntegrationConfig):
        """
        Initialize the base integration adapter.

        Args:
            config: Integration configuration containing credentials,
                    base URL, rate limits, etc.
        """
        self.config = config
        self._status = ConnectionStatus.DISCONNECTED
        self._connected_at: Optional[float] = None
        self._rate_limit = RateLimitState()
        self._requests_sent: int = 0
        self._errors_count: int = 0
        self._rate_limit_hits: int = 0
        self._last_error: Optional[str] = None
        self._logger = logging.getLogger(
            f"liuhao.integrations.{config.name}"
        )
        self._session: Optional[Any] = None

    # ------------------------------------------------------------------
    # Abstract methods - must be implemented by subclasses
    # ------------------------------------------------------------------

    @abstractmethod
    def connect(self) -> bool:
        """
        Establish connection to the integration service.

        Returns:
            True if connection was successful, False otherwise.

        Raises:
            AuthenticationError: If authentication credentials are invalid.
            IntegrationError: If connection fails for other reasons.
        """
        ...

    @abstractmethod
    def disconnect(self) -> bool:
        """
        Close connection to the integration service.

        Returns:
            True if disconnection was successful.
        """
        ...

    @abstractmethod
    def is_connected(self) -> bool:
        """
        Check whether the adapter is currently connected.

        Returns:
            True if connected, False otherwise.
        """
        ...

    @abstractmethod
    def get_status(self) -> IntegrationStatus:
        """
        Get the current status of the integration adapter.

        Returns:
            IntegrationStatus with current metrics and state.
        """
        ...

    @abstractmethod
    def test_connection(self) -> bool:
        """
        Test the connection to the integration service.

        Returns:
            True if the connection test passes.
        """
        ...

    # ------------------------------------------------------------------
    # Common utility methods
    # ------------------------------------------------------------------

    def _check_rate_limit(self) -> None:
        """
        Check rate limits and throttle if necessary.

        Raises:
            RateLimitError: If rate limit has been exceeded and
                            retry-after is too long.
        """
        now = time.time()

        # Reset minute window if needed
        if now - self._rate_limit.minute_window_start >= 60:
            self._rate_limit.minute_window_start = now
            self._rate_limit.requests_this_minute = 0

        # Reset hour window if needed
        if now - self._rate_limit.hour_window_start >= 3600:
            self._rate_limit.hour_window_start = now
            self._rate_limit.requests_this_hour = 0

        # Check retry-after cooldown
        if self._rate_limit.retry_after > now:
            wait_time = self._rate_limit.retry_after - now
            if wait_time > 10:
                raise RateLimitError(
                    f"Rate limited, retry after {wait_time:.1f}s",
                    retry_after=wait_time,
                    adapter_name=self.config.name,
                )
            time.sleep(wait_time)

        # Check per-minute limit
        if (self.config.rate_limit_per_minute > 0
                and self._rate_limit.requests_this_minute
                >= self.config.rate_limit_per_minute):
            self._rate_limit_hits += 1
            wait_time = 60 - (now - self._rate_limit.minute_window_start)
            if wait_time > 0:
                if wait_time > 30:
                    raise RateLimitError(
                        "Per-minute rate limit exceeded",
                        retry_after=wait_time,
                        adapter_name=self.config.name,
                    )
                time.sleep(wait_time)
                self._rate_limit.minute_window_start = time.time()
                self._rate_limit.requests_this_minute = 0

        # Check per-hour limit
        if (self.config.rate_limit_per_hour > 0
                and self._rate_limit.requests_this_hour
                >= self.config.rate_limit_per_hour):
            self._rate_limit_hits += 1
            wait_time = 3600 - (now - self._rate_limit.hour_window_start)
            raise RateLimitError(
                "Per-hour rate limit exceeded",
                retry_after=wait_time,
                adapter_name=self.config.name,
            )

    def _mark_request(self) -> None:
        """Record that a request has been made for rate limiting."""
        now = time.time()
        self._rate_limit.requests_this_minute += 1
        self._rate_limit.requests_this_hour += 1
        self._rate_limit.last_request_time = now
        self._requests_sent += 1

    def _log_request(self, method: str, url: str,
                     headers: Optional[Dict[str, str]] = None,
                     body: Optional[Any] = None) -> None:
        """
        Log an outgoing request.

        Args:
            method: HTTP method (GET, POST, etc.)
            url: Full request URL.
            headers: Request headers (redacted for security).
            body: Request body (truncated).
        """
        safe_headers = {}
        if headers:
            for key, value in headers.items():
                if key.lower() in {"authorization", "x-api-key", "cookie"}:
                    safe_headers[key] = "***REDACTED***"
                else:
                    safe_headers[key] = value

        body_str = ""
        if body is not None:
            try:
                body_str = str(body)[:500]
            except Exception:
                body_str = "<binary>"

        self._logger.debug(
            "Request: %s %s | headers=%s | body=%s",
            method, url, safe_headers, body_str,
        )

    def _log_response(self, method: str, url: str,
                      status_code: int,
                      response_body: Optional[str] = None) -> None:
        """
        Log an incoming response.

        Args:
            method: HTTP method used for the request.
            url: Request URL.
            status_code: HTTP response status code.
            response_body: Response body (truncated).
        """
        body_str = ""
        if response_body:
            body_str = response_body[:500]

        log_level = logging.INFO if status_code < 400 else logging.WARNING
        self._logger.log(
            log_level,
            "Response: %s %s -> %d | body=%s",
            method, url, status_code, body_str,
        )

    def _build_url(self, path: str) -> str:
        """
        Build a full URL from a path.

        Args:
            path: API path (relative or absolute).

        Returns:
            Full URL string.
        """
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return urljoin(self.config.base_url.rstrip("/") + "/", path.lstrip("/"))

    def _get_auth_headers(self) -> Dict[str, str]:
        """
        Build authentication headers based on config auth_type.

        Returns:
            Dictionary of authentication headers.
        """
        headers: Dict[str, str] = {}

        if self.config.auth_type == AuthType.API_KEY and self.config.api_key:
            headers["X-API-Key"] = self.config.api_key

        elif self.config.auth_type == AuthType.BEARER_TOKEN:
            token = self.config.access_token or self.config.api_key
            if token:
                headers["Authorization"] = f"Bearer {token}"

        elif self.config.auth_type == AuthType.BASIC_AUTH:
            import base64
            username = self.config.username or ""
            password = self.config.password or ""
            credentials = f"{username}:{password}".encode("utf-8")
            encoded = base64.b64encode(credentials).decode("utf-8")
            headers["Authorization"] = f"Basic {encoded}"

        return headers

    def _handle_error(self, message: str,
                      status_code: Optional[int] = None,
                      response_body: Optional[str] = None) -> IntegrationError:
        """
        Create and log an integration error.

        Args:
            message: Error message.
            status_code: HTTP status code if applicable.
            response_body: Response body if applicable.

        Returns:
            IntegrationError instance.
        """
        self._errors_count += 1
        self._last_error = message
        self._logger.error(
            "Integration error: %s (status=%s)", message, status_code
        )
        return IntegrationError(
            message,
            status_code=status_code,
            response_body=response_body,
            adapter_name=self.config.name,
        )

    def _build_status(self, extra: Optional[Dict[str, Any]] = None) -> IntegrationStatus:
        """
        Build an IntegrationStatus from current state.

        Args:
            extra: Additional status fields to include.

        Returns:
            IntegrationStatus instance.
        """
        uptime = 0.0
        if self._connected_at is not None:
            uptime = time.time() - self._connected_at

        return IntegrationStatus(
            name=self.config.name,
            status=self._status,
            is_connected=self.is_connected(),
            uptime_seconds=uptime,
            requests_sent=self._requests_sent,
            errors_count=self._errors_count,
            rate_limit_hit_count=self._rate_limit_hits,
            last_error=self._last_error,
            last_request_at=self._rate_limit.last_request_time or None,
            extra=extra or {},
        )

    def __repr__(self) -> str:
        return (
            f"<{self.__class__.__name__} name={self.config.name} "
            f"status={self._status.value}>"
        )
