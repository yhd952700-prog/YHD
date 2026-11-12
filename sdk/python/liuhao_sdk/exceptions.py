"""
LiuHao AI OS SDK Exception Hierarchy.

Defines all custom exceptions used throughout the SDK,
following a layered inheritance pattern for granular error handling.
"""

from typing import Optional, Dict, Any


class LiuHaoError(Exception):
    """Base exception class for all LiuHao SDK errors.

    All custom SDK exceptions inherit from this class,
    allowing callers to catch all SDK-related errors with a single except clause.

    Attributes:
        message: Human-readable error description.
        code: Numeric or string error code from the API.
        request_id: Unique request identifier for debugging.
        details: Additional structured error details.
    """

    def __init__(
        self,
        message: str = "An unexpected error occurred in the LiuHao SDK",
        code: Optional[str] = None,
        request_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Initialize the base LiuHao error.

        Args:
            message: Human-readable error description.
            code: Numeric or string error code from the API.
            request_id: Unique request identifier for debugging.
            details: Additional structured error details.
        """
        self.message = message
        self.code = code
        self.request_id = request_id
        self.details = details or {}
        super().__init__(self.message)

    def __str__(self) -> str:
        """Return a string representation including request_id if available."""
        parts = [self.message]
        if self.code:
            parts.append(f"(code: {self.code})")
        if self.request_id:
            parts.append(f"[request_id: {self.request_id}]")
        return " ".join(parts)

    def __repr__(self) -> str:
        """Return a detailed string representation for debugging."""
        return (
            f"{self.__class__.__name__}("
            f"message={self.message!r}, "
            f"code={self.code!r}, "
            f"request_id={self.request_id!r}, "
            f"details={self.details!r})"
        )


class AuthenticationError(LiuHaoError):
    """Raised when authentication fails.

    This typically indicates an invalid API key, missing credentials,
    or an expired token.

    HTTP status codes: 401, 403
    """

    def __init__(
        self,
        message: str = "Authentication failed. Please check your API key.",
        code: Optional[str] = None,
        request_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message=message, code=code, request_id=request_id, details=details)


class APIError(LiuHaoError):
    """Raised when the API returns an unexpected error response.

    This is a catch-all for API errors that don't fit into more specific
    exception classes.

    HTTP status codes: 500, 502, 503, 504, and other 5xx errors
    """

    def __init__(
        self,
        message: str = "The API returned an unexpected error.",
        code: Optional[str] = None,
        request_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        status_code: Optional[int] = None,
    ) -> None:
        self.status_code = status_code
        super().__init__(message=message, code=code, request_id=request_id, details=details)


class NotFoundError(LiuHaoError):
    """Raised when a requested resource is not found.

    This typically indicates that the entity (agent, memory entry,
    knowledge base, etc.) does not exist or has been deleted.

    HTTP status code: 404
    """

    def __init__(
        self,
        message: str = "The requested resource was not found.",
        code: Optional[str] = None,
        request_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
    ) -> None:
        self.resource_type = resource_type
        self.resource_id = resource_id
        super().__init__(message=message, code=code, request_id=request_id, details=details)


class RateLimitError(LiuHaoError):
    """Raised when the API rate limit is exceeded.

    Callers should implement exponential backoff when catching this exception.

    HTTP status code: 429
    """

    def __init__(
        self,
        message: str = "Rate limit exceeded. Please reduce your request frequency.",
        code: Optional[str] = None,
        request_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        retry_after: Optional[float] = None,
    ) -> None:
        self.retry_after = retry_after
        super().__init__(message=message, code=code, request_id=request_id, details=details)


class ValidationError(LiuHaoError):
    """Raised when request data fails validation.

    This typically indicates malformed input, missing required fields,
    or data that doesn't conform to the expected schema.

    HTTP status code: 400, 422
    """

    def __init__(
        self,
        message: str = "Request validation failed.",
        code: Optional[str] = None,
        request_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        field_errors: Optional[Dict[str, str]] = None,
    ) -> None:
        self.field_errors = field_errors or {}
        super().__init__(message=message, code=code, request_id=request_id, details=details)


class TimeoutError(LiuHaoError):
    """Raised when a request times out.

    This indicates that the server did not respond within the configured
    timeout period. The request may or may not have been processed.

    Note: This shadows the built-in TimeoutError within the SDK context.
    Import builtins.TimeoutError if you need the built-in version.
    """

    def __init__(
        self,
        message: str = "Request timed out. The server did not respond in time.",
        code: Optional[str] = None,
        request_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None,
    ) -> None:
        self.timeout = timeout
        super().__init__(message=message, code=code, request_id=request_id, details=details)


# Map HTTP status codes to exception classes
_STATUS_CODE_MAP: Dict[int, type] = {
    400: ValidationError,
    401: AuthenticationError,
    403: AuthenticationError,
    404: NotFoundError,
    422: ValidationError,
    429: RateLimitError,
    500: APIError,
    502: APIError,
    503: APIError,
    504: APIError,
}


def raise_for_status(
    status_code: int,
    message: Optional[str] = None,
    code: Optional[str] = None,
    request_id: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
) -> None:
    """Raise the appropriate exception based on HTTP status code.

    Args:
        status_code: HTTP response status code.
        message: Optional custom error message.
        code: Optional error code from the API response.
        request_id: Optional request identifier.
        details: Optional additional error details.

    Raises:
        LiuHaoError: The appropriate exception subclass for the status code.
            Returns silently if status_code is in the 2xx range.
    """
    if 200 <= status_code < 300:
        return

    exc_class = _STATUS_CODE_MAP.get(status_code, APIError)

    if message is None:
        message = f"HTTP {status_code} error"

    if exc_class is APIError:
        raise exc_class(
            message=message,
            code=code,
            request_id=request_id,
            details=details,
            status_code=status_code,
        )
    elif exc_class is RateLimitError:
        retry_after = None
        if details and "retry_after" in details:
            retry_after = float(details["retry_after"])
        raise exc_class(
            message=message,
            code=code,
            request_id=request_id,
            details=details,
            retry_after=retry_after,
        )
    elif exc_class is NotFoundError:
        resource_type = details.get("resource_type") if details else None
        resource_id = details.get("resource_id") if details else None
        raise exc_class(
            message=message,
            code=code,
            request_id=request_id,
            details=details,
            resource_type=resource_type,
            resource_id=resource_id,
        )
    elif exc_class is ValidationError:
        field_errors = details.get("field_errors") if details else None
        raise exc_class(
            message=message,
            code=code,
            request_id=request_id,
            details=details,
            field_errors=field_errors,
        )
    elif exc_class is TimeoutError:
        timeout = details.get("timeout") if details else None
        raise exc_class(
            message=message,
            code=code,
            request_id=request_id,
            details=details,
            timeout=timeout,
        )
    else:
        raise exc_class(
            message=message,
            code=code,
            request_id=request_id,
            details=details,
        )
