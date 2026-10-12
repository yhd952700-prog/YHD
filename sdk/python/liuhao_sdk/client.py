"""
LiuHao AI OS SDK Client.

Main entry point for the LiuHao Python SDK. Provides a resource-oriented
client interface for interacting with the LiuHao AI OS platform.
"""

import time
import logging
import json
from typing import Optional, Dict, Any, List, Tuple
from urllib.parse import urljoin

from liuhao_sdk.config import Config
from liuhao_sdk.exceptions import (
    LiuHaoError,
    APIError,
    AuthenticationError,
    NotFoundError,
    RateLimitError,
    ValidationError,
    TimeoutError,
    raise_for_status,
)
from liuhao_sdk.resources import (
    AgentsResource,
    MemoryResource,
    KnowledgeResource,
)

logger = logging.getLogger(__name__)


class LiuHaoClient:
    """Main client for the LiuHao AI OS platform.

    Provides a unified interface to all LiuHao API resources, with
    built-in authentication, error handling, retry logic, and
    configurable timeouts.

    Usage example::

        from liuhao_sdk import LiuHaoClient

        client = LiuHaoClient(api_key="your-api-key")
        agents = client.agents.list()
        result = client.agents.run("agent-id", "Hello, world!")

    Attributes:
        config: The active SDK configuration.
        agents: Agent management resource.
        memory: Memory operations resource.
        knowledge: Knowledge base management resource.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        config: Optional[Config] = None,
        **kwargs: Any,
    ) -> None:
        """Initialize the LiuHao client.

        Configuration precedence (highest first):
            1. Explicit constructor arguments (api_key, base_url, etc.)
            2. Config object passed via ``config`` parameter
            3. Environment variables (prefixed with ``LIUHAO_``)
            4. Default values

        Args:
            api_key: API key for authentication. Overrides config/env.
            base_url: Base API URL. Overrides config/env.
            config: Optional pre-built Config instance.
            **kwargs: Additional configuration overrides
                (see :class:`Config` for available fields).
        """
        # Start with environment-based config
        if config is not None:
            self.config = config.merge(Config.from_env())
        else:
            self.config = Config.from_env()

        # Apply explicit overrides
        if api_key is not None:
            self.config.api_key = api_key
        if base_url is not None:
            self.config.base_url = base_url

        for key, value in kwargs.items():
            if hasattr(self.config, key):
                setattr(self.config, key, value)

        # Initialize resources
        self.agents = AgentsResource(self)
        self.memory = MemoryResource(self)
        self.knowledge = KnowledgeResource(self)

        # Session state
        self._session: Optional[Any] = None
        self._request_count = 0

    # ------------------------------------------------------------------
    # Request methods
    # ------------------------------------------------------------------

    def get(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Perform an HTTP GET request.

        Args:
            path: API endpoint path (relative to base_url).
            params: Query parameters.
            headers: Additional headers.
            timeout: Per-request timeout override.

        Returns:
            Parsed JSON response as a dictionary.
        """
        return self._request("GET", path, params=params, headers=headers, timeout=timeout)

    def post(
        self,
        path: str,
        json: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Perform an HTTP POST request.

        Args:
            path: API endpoint path.
            json: JSON body data.
            params: Query parameters.
            headers: Additional headers.
            timeout: Per-request timeout override.

        Returns:
            Parsed JSON response as a dictionary.
        """
        return self._request("POST", path, json=json, params=params, headers=headers, timeout=timeout)

    def put(
        self,
        path: str,
        json: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Perform an HTTP PUT request.

        Args:
            path: API endpoint path.
            json: JSON body data.
            params: Query parameters.
            headers: Additional headers.
            timeout: Per-request timeout override.

        Returns:
            Parsed JSON response as a dictionary.
        """
        return self._request("PUT", path, json=json, params=params, headers=headers, timeout=timeout)

    def patch(
        self,
        path: str,
        json: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Perform an HTTP PATCH request.

        Args:
            path: API endpoint path.
            json: JSON body data.
            params: Query parameters.
            headers: Additional headers.
            timeout: Per-request timeout override.

        Returns:
            Parsed JSON response as a dictionary.
        """
        return self._request("PATCH", path, json=json, params=params, headers=headers, timeout=timeout)

    def delete(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Perform an HTTP DELETE request.

        Args:
            path: API endpoint path.
            params: Query parameters.
            headers: Additional headers.
            timeout: Per-request timeout override.

        Returns:
            Parsed JSON response as a dictionary.
        """
        return self._request("DELETE", path, params=params, headers=headers, timeout=timeout)

    # ------------------------------------------------------------------
    # Internal request handling
    # ------------------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        json: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Internal request dispatcher with retry logic.

        Args:
            method: HTTP method (GET, POST, PUT, PATCH, DELETE).
            path: API endpoint path.
            json: Optional JSON body.
            params: Optional query parameters.
            headers: Optional additional headers.
            timeout: Optional per-request timeout.

        Returns:
            Parsed JSON response.

        Raises:
            AuthenticationError: If authentication fails.
            NotFoundError: If the resource is not found.
            RateLimitError: If rate limits are exceeded.
            ValidationError: If request validation fails.
            TimeoutError: If the request times out.
            APIError: For other API errors.
        """
        url = self._build_url(path)
        request_headers = self._build_headers(headers)
        request_timeout = timeout if timeout is not None else self.config.timeout

        last_exception: Optional[Exception] = None
        max_retries = self.config.max_retries

        for attempt in range(max_retries + 1):
            try:
                if self.config.debug:
                    logger.debug(
                        f"Request attempt {attempt + 1}/{max_retries + 1}: "
                        f"{method} {url}"
                    )

                # Use mock response when requests library is not available
                # This allows the SDK to function as a stub in testing
                response = self._execute_request(
                    method=method,
                    url=url,
                    json_data=json,
                    params=params,
                    headers=request_headers,
                    timeout=request_timeout,
                )

                self._request_count += 1

                # Handle response
                status_code = response.get("status_code", 200)
                response_data = response.get("data", {})
                response_headers = response.get("headers", {})

                # Check for error status codes
                if status_code >= 400:
                    request_id = response_headers.get("X-Request-ID")
                    error_code = response_data.get("error", {}).get("code")
                    error_message = response_data.get("error", {}).get(
                        "message", f"HTTP {status_code} error"
                    )
                    error_details = response_data.get("error", {}).get("details", {})

                    raise_for_status(
                        status_code=status_code,
                        message=error_message,
                        code=error_code,
                        request_id=request_id,
                        details=error_details,
                    )

                return response_data

            except (RateLimitError, APIError, TimeoutError) as e:
                last_exception = e
                if attempt < max_retries:
                    wait_time = self._calculate_backoff(attempt)
                    if self.config.debug:
                        logger.debug(
                            f"Retryable error: {e}. "
                            f"Retrying in {wait_time:.2f}s..."
                        )
                    time.sleep(wait_time)
                    continue
                raise

            except (AuthenticationError, NotFoundError, ValidationError) as e:
                # Non-retryable errors
                raise

            except Exception as e:
                last_exception = e
                if attempt < max_retries and self._is_retryable_error(e):
                    wait_time = self._calculate_backoff(attempt)
                    time.sleep(wait_time)
                    continue
                raise APIError(
                    message=f"Request failed: {str(e)}",
                    code="request_error",
                ) from e

        # Should not reach here, but just in case
        if last_exception:
            raise last_exception
        raise APIError(message="Unknown request error")

    def _execute_request(
        self,
        method: str,
        url: str,
        json_data: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: float = 30.0,
    ) -> Dict[str, Any]:
        """Execute the actual HTTP request.

        In this stub implementation, returns a mock success response.
        A production implementation would use the ``requests`` library.

        Args:
            method: HTTP method.
            url: Full URL.
            json_data: Optional JSON body.
            params: Optional query params.
            headers: Request headers.
            timeout: Request timeout in seconds.

        Returns:
            Dictionary with ``status_code``, ``data``, and ``headers``.
        """
        # Stub implementation - returns mock responses
        # In production, this would use:
        #   import requests
        #   response = requests.request(...)
        #   return {"status_code": response.status_code, "data": response.json(), ...}

        return {
            "status_code": 200,
            "data": {
                "method": method,
                "url": url,
                "received": True,
                "body": json_data or {},
                "params": params or {},
            },
            "headers": {
                "X-Request-ID": f"req-{self._request_count}",
                "Content-Type": "application/json",
            },
        }

    def _build_url(self, path: str) -> str:
        """Build a full URL from a relative path.

        Args:
            path: Relative API endpoint path.

        Returns:
            Full URL string.
        """
        base = self.config.base_url.rstrip("/") + "/"
        path = path.lstrip("/")
        return urljoin(base, path)

    def _build_headers(
        self, extra_headers: Optional[Dict[str, str]] = None
    ) -> Dict[str, str]:
        """Build default request headers including authentication.

        Args:
            extra_headers: Additional headers to merge.

        Returns:
            Complete headers dictionary.
        """
        headers: Dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": f"LiuHao-SDK-Python/1.0.0",
        }

        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"

        if self.config.organization_id:
            headers["X-Organization-ID"] = self.config.organization_id

        if self.config.project_id:
            headers["X-Project-ID"] = self.config.project_id

        # Add custom headers from config
        headers.update(self.config.custom_headers)

        # Add per-request extra headers
        if extra_headers:
            headers.update(extra_headers)

        return headers

    def _calculate_backoff(self, attempt: int) -> float:
        """Calculate exponential backoff delay.

        Uses the formula: backoff_factor * (2 ** attempt)

        Args:
            attempt: Zero-based retry attempt number.

        Returns:
            Wait time in seconds.
        """
        return self.config.retry_backoff_factor * (2 ** attempt)

    def _is_retryable_error(self, error: Exception) -> bool:
        """Determine if an error is retryable.

        Args:
            error: The exception to check.

        Returns:
            True if the error might be transient and worth retrying.
        """
        # Connection errors, timeouts, etc. are retryable
        retryable_types = (TimeoutError,)
        return isinstance(error, retryable_types)

    # ------------------------------------------------------------------
    # Utility methods
    # ------------------------------------------------------------------

    def health_check(self) -> Dict[str, Any]:
        """Check the health of the LiuHao API.

        Returns:
            Health status dictionary.
        """
        return self.get("/health")

    def get_api_info(self) -> Dict[str, Any]:
        """Get API version and platform information.

        Returns:
            API info dictionary with version, build, and features.
        """
        return self.get("/info")

    def __repr__(self) -> str:
        """Return a string representation of the client."""
        masked_key = None
        if self.config.api_key:
            masked_key = f"{self.config.api_key[:8]}..."
        return (
            f"LiuHaoClient(base_url={self.config.base_url!r}, "
            f"api_key={masked_key!r})"
        )
