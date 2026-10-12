"""
Unified Error Code System for LiuHao AI OS.

Provides a comprehensive error code registry with categories,
severity levels, metadata, and factory functions for creating
consistent error objects across the system.

Error categories:
    - SYSTEM: System-level errors
    - AUTH: Authentication and authorization errors
    - VALIDATION: Input validation errors
    - RATE_LIMIT: Rate limiting errors
    - NOT_FOUND: Resource not found errors
    - AI_PROVIDER: AI provider related errors
    - MEMORY: Memory system errors
    - DATABASE: Database related errors
    - CONFIGURATION: Configuration errors
    - NETWORK: Network related errors
    - INTERNAL: Internal/unknown errors
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Dict, Any, Type, Callable
import threading


class ErrorCategory(Enum):
    """Error category enumeration.

    Categorizes errors by their domain or origin.

    Attributes:
        SYSTEM: System-level errors (startup, shutdown, runtime).
        AUTH: Authentication and authorization errors.
        VALIDATION: Input validation and schema errors.
        RATE_LIMIT: Rate limiting and quota errors.
        NOT_FOUND: Resource not found errors.
        AI_PROVIDER: AI model provider errors.
        MEMORY: Memory system errors.
        DATABASE: Database and persistence errors.
        CONFIGURATION: Configuration and setup errors.
        NETWORK: Network and connectivity errors.
        INTERNAL: Internal/unknown errors (catch-all).
    """
    SYSTEM = "SYSTEM"
    AUTH = "AUTH"
    VALIDATION = "VALIDATION"
    RATE_LIMIT = "RATE_LIMIT"
    NOT_FOUND = "NOT_FOUND"
    AI_PROVIDER = "AI_PROVIDER"
    MEMORY = "MEMORY"
    DATABASE = "DATABASE"
    CONFIGURATION = "CONFIGURATION"
    NETWORK = "NETWORK"
    INTERNAL = "INTERNAL"


class ErrorSeverity(Enum):
    """Error severity levels.

    Attributes:
        LOW: Informational, no action required.
        MEDIUM: Warning, may need attention.
        HIGH: Error, needs attention but system continues.
        CRITICAL: Critical failure, system may be impaired.
    """
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class ErrorDefinition:
    """Definition of a registered error code.

    Contains metadata about an error type including its code,
    message template, category, severity, and HTTP mapping.

    Attributes:
        code: Unique error code string.
        category: Error category.
        severity: Error severity level.
        message_template: Template string for error messages.
        http_status: HTTP status code to map to (if applicable).
        retryable: Whether the error is transient and retryable.
        description: Human-readable description of the error.
    """
    code: str
    category: ErrorCategory
    severity: ErrorSeverity
    message_template: str = "An error occurred"
    http_status: int = 500
    retryable: bool = False
    description: str = ""

    def format_message(self, **kwargs: Any) -> str:
        """Format the message template with provided parameters.

        Args:
            **kwargs: Parameters to substitute in the template.

        Returns:
            Formatted error message string.
        """
        try:
            return self.message_template.format(**kwargs)
        except (KeyError, IndexError):
            # If formatting fails, return the template as-is
            return self.message_template


class LiuHaoErrorCodeError(Exception):
    """Exception base class with error code metadata.

    All LiuHao AI OS errors should be created through this class
    or its subclasses to ensure consistent error handling.

    Attributes:
        error_code: The error code string.
        category: Error category.
        severity: Error severity level.
        message: Human-readable error message.
        details: Additional error details.
        http_status: HTTP status code mapping.
        retryable: Whether the error is retryable.
    """

    def __init__(
        self,
        error_code: str,
        message: Optional[str] = None,
        category: ErrorCategory = ErrorCategory.INTERNAL,
        severity: ErrorSeverity = ErrorSeverity.MEDIUM,
        details: Optional[Dict[str, Any]] = None,
        http_status: int = 500,
        retryable: bool = False,
    ):
        """Initialize the error.

        Args:
            error_code: Unique error code.
            message: Human-readable error message.
            category: Error category.
            severity: Error severity level.
            details: Additional error context.
            http_status: HTTP status code.
            retryable: Whether error is transient.
        """
        self.error_code = error_code
        self.category = category
        self.severity = severity
        self.message = message or "An error occurred"
        self.details = details or {}
        self.http_status = http_status
        self.retryable = retryable
        super().__init__(self.message)

    def to_dict(self) -> Dict[str, Any]:
        """Convert error to a dictionary for serialization.

        Returns:
            Dictionary representation of the error.
        """
        return {
            "error_code": self.error_code,
            "category": self.category.value,
            "severity": self.severity.value,
            "message": self.message,
            "details": self.details,
            "http_status": self.http_status,
            "retryable": self.retryable,
        }

    def __repr__(self) -> str:
        """Return string representation."""
        return (
            f"{type(self).__name__}("
            f"code={self.error_code!r}, "
            f"severity={self.severity.value}, "
            f"message={self.message!r})"
        )


class ErrorCodeRegistry:
    """Registry for error code definitions.

    Maintains a central registry of all error codes used in the system,
    providing lookup, validation, and error factory functions.

    Usage::

        registry = ErrorCodeRegistry()
        registry.register(ErrorDefinition(
            code="AUTH_TOKEN_EXPIRED",
            category=ErrorCategory.AUTH,
            severity=ErrorSeverity.HIGH,
            message_template="Authentication token has expired",
            http_status=401,
            retryable=False,
        ))

        error = registry.create_error("AUTH_TOKEN_EXPIRED")
    """

    def __init__(self):
        """Initialize the error code registry."""
        self._definitions: Dict[str, ErrorDefinition] = {}
        self._lock = threading.Lock()

    def register(self, definition: ErrorDefinition) -> None:
        """Register an error code definition.

        Args:
            definition: The error definition to register.

        Raises:
            ValueError: If the error code is already registered.
        """
        with self._lock:
            if definition.code in self._definitions:
                raise ValueError(
                    f"Error code '{definition.code}' is already registered"
                )
            self._definitions[definition.code] = definition

    def register_many(self, definitions: list) -> None:
        """Register multiple error code definitions.

        Args:
            definitions: List of ErrorDefinition instances.
        """
        for definition in definitions:
            self.register(definition)

    def unregister(self, code: str) -> bool:
        """Unregister an error code.

        Args:
            code: The error code to unregister.

        Returns:
            True if the code was found and removed.
        """
        with self._lock:
            if code in self._definitions:
                del self._definitions[code]
                return True
            return False

    def get(self, code: str) -> Optional[ErrorDefinition]:
        """Get an error definition by code.

        Args:
            code: The error code to look up.

        Returns:
            ErrorDefinition if found, None otherwise.
        """
        with self._lock:
            return self._definitions.get(code)

    def has(self, code: str) -> bool:
        """Check if an error code is registered.

        Args:
            code: The error code to check.

        Returns:
            True if the code is registered.
        """
        with self._lock:
            return code in self._definitions

    def list_codes(self, category: Optional[ErrorCategory] = None) -> list:
        """List all registered error codes.

        Args:
            category: Optional filter by category.

        Returns:
            List of error code strings.
        """
        with self._lock:
            if category is None:
                return sorted(self._definitions.keys())
            return sorted(
                code for code, defn in self._definitions.items()
                if defn.category == category
            )

    def create_error(
        self,
        code: str,
        message_params: Optional[Dict[str, Any]] = None,
        details: Optional[Dict[str, Any]] = None,
        custom_message: Optional[str] = None,
    ) -> LiuHaoErrorCodeError:
        """Create an error instance from a registered error code.

        Args:
            code: The error code.
            message_params: Parameters for message template formatting.
            details: Additional error details.
            custom_message: Override the default message template.

        Returns:
            LiuHaoErrorCodeError instance.

        Raises:
            KeyError: If the error code is not registered.
        """
        definition = self.get(code)
        if definition is None:
            raise KeyError(f"Error code '{code}' is not registered")

        message = custom_message or definition.format_message(
            **(message_params or {})
        )

        return LiuHaoErrorCodeError(
            error_code=code,
            message=message,
            category=definition.category,
            severity=definition.severity,
            details=details or {},
            http_status=definition.http_status,
            retryable=definition.retryable,
        )

    def to_dict(self) -> Dict[str, Dict[str, Any]]:
        """Export all error definitions as a dictionary.

        Returns:
            Dictionary mapping error codes to their metadata.
        """
        with self._lock:
            return {
                code: {
                    "category": defn.category.value,
                    "severity": defn.severity.value,
                    "message_template": defn.message_template,
                    "http_status": defn.http_status,
                    "retryable": defn.retryable,
                    "description": defn.description,
                }
                for code, defn in self._definitions.items()
            }

    @property
    def count(self) -> int:
        """Get the total number of registered error codes.

        Returns:
            Number of registered error codes.
        """
        with self._lock:
            return len(self._definitions)


# ============================================================================
# Default error codes
# ============================================================================

DEFAULT_ERROR_DEFINITIONS = [
    # SYSTEM errors
    ErrorDefinition(
        code="SYSTEM_INTERNAL_ERROR",
        category=ErrorCategory.SYSTEM,
        severity=ErrorSeverity.HIGH,
        message_template="Internal system error: {detail}",
        http_status=500,
        retryable=False,
        description="A generic internal system error occurred.",
    ),
    ErrorDefinition(
        code="SYSTEM_NOT_INITIALIZED",
        category=ErrorCategory.SYSTEM,
        severity=ErrorSeverity.HIGH,
        message_template="System component '{component}' is not initialized",
        http_status=503,
        retryable=True,
        description="A required system component has not been initialized.",
    ),
    ErrorDefinition(
        code="SYSTEM_SHUTDOWN",
        category=ErrorCategory.SYSTEM,
        severity=ErrorSeverity.MEDIUM,
        message_template="System is shutting down",
        http_status=503,
        retryable=False,
        description="The system is in the process of shutting down.",
    ),

    # AUTH errors
    ErrorDefinition(
        code="AUTH_UNAUTHORIZED",
        category=ErrorCategory.AUTH,
        severity=ErrorSeverity.HIGH,
        message_template="Authentication required",
        http_status=401,
        retryable=False,
        description="No valid authentication credentials provided.",
    ),
    ErrorDefinition(
        code="AUTH_TOKEN_EXPIRED",
        category=ErrorCategory.AUTH,
        severity=ErrorSeverity.HIGH,
        message_template="Authentication token has expired",
        http_status=401,
        retryable=True,
        description="The authentication token has expired and needs renewal.",
    ),
    ErrorDefinition(
        code="AUTH_TOKEN_INVALID",
        category=ErrorCategory.AUTH,
        severity=ErrorSeverity.HIGH,
        message_template="Authentication token is invalid",
        http_status=401,
        retryable=False,
        description="The authentication token is malformed or has an invalid signature.",
    ),
    ErrorDefinition(
        code="AUTH_FORBIDDEN",
        category=ErrorCategory.AUTH,
        severity=ErrorSeverity.HIGH,
        message_template="Access denied: insufficient permissions for '{resource}'",
        http_status=403,
        retryable=False,
        description="The authenticated subject lacks required permissions.",
    ),

    # VALIDATION errors
    ErrorDefinition(
        code="VALIDATION_FAILED",
        category=ErrorCategory.VALIDATION,
        severity=ErrorSeverity.MEDIUM,
        message_template="Validation failed: {detail}",
        http_status=400,
        retryable=False,
        description="Input data failed validation checks.",
    ),
    ErrorDefinition(
        code="VALIDATION_MISSING_FIELD",
        category=ErrorCategory.VALIDATION,
        severity=ErrorSeverity.MEDIUM,
        message_template="Missing required field: '{field}'",
        http_status=400,
        retryable=False,
        description="A required field was not provided.",
    ),
    ErrorDefinition(
        code="VALIDATION_INVALID_FORMAT",
        category=ErrorCategory.VALIDATION,
        severity=ErrorSeverity.MEDIUM,
        message_template="Invalid format for field '{field}': {detail}",
        http_status=400,
        retryable=False,
        description="A field value does not match the expected format.",
    ),

    # RATE_LIMIT errors
    ErrorDefinition(
        code="RATE_LIMIT_EXCEEDED",
        category=ErrorCategory.RATE_LIMIT,
        severity=ErrorSeverity.MEDIUM,
        message_template="Rate limit exceeded for '{resource}'. Try again after {retry_after}s",
        http_status=429,
        retryable=True,
        description="The request rate limit has been exceeded.",
    ),
    ErrorDefinition(
        code="QUOTA_EXCEEDED",
        category=ErrorCategory.RATE_LIMIT,
        severity=ErrorSeverity.HIGH,
        message_template="Quota exceeded for '{resource}'",
        http_status=429,
        retryable=False,
        description="The usage quota has been exceeded for the current period.",
    ),

    # NOT_FOUND errors
    ErrorDefinition(
        code="NOT_FOUND",
        category=ErrorCategory.NOT_FOUND,
        severity=ErrorSeverity.MEDIUM,
        message_template="Resource '{resource}' not found",
        http_status=404,
        retryable=False,
        description="The requested resource does not exist.",
    ),

    # AI_PROVIDER errors
    ErrorDefinition(
        code="AI_PROVIDER_UNAVAILABLE",
        category=ErrorCategory.AI_PROVIDER,
        severity=ErrorSeverity.HIGH,
        message_template="AI provider '{provider}' is unavailable",
        http_status=503,
        retryable=True,
        description="The AI model provider is currently unavailable.",
    ),
    ErrorDefinition(
        code="AI_PROVIDER_RATE_LIMIT",
        category=ErrorCategory.AI_PROVIDER,
        severity=ErrorSeverity.HIGH,
        message_template="AI provider '{provider}' rate limit exceeded",
        http_status=429,
        retryable=True,
        description="The AI provider rate limit has been reached.",
    ),
    ErrorDefinition(
        code="AI_PROVIDER_TIMEOUT",
        category=ErrorCategory.AI_PROVIDER,
        severity=ErrorSeverity.HIGH,
        message_template="AI provider '{provider}' request timed out after {timeout}s",
        http_status=504,
        retryable=True,
        description="The AI provider request timed out.",
    ),
    ErrorDefinition(
        code="AI_PROVIDER_INVALID_RESPONSE",
        category=ErrorCategory.AI_PROVIDER,
        severity=ErrorSeverity.HIGH,
        message_template="AI provider '{provider}' returned invalid response",
        http_status=502,
        retryable=True,
        description="The AI provider returned a malformed or invalid response.",
    ),
    ErrorDefinition(
        code="AI_MODEL_NOT_FOUND",
        category=ErrorCategory.AI_PROVIDER,
        severity=ErrorSeverity.MEDIUM,
        message_template="AI model '{model}' not found on provider '{provider}'",
        http_status=404,
        retryable=False,
        description="The requested AI model is not available from the provider.",
    ),

    # MEMORY errors
    ErrorDefinition(
        code="MEMORY_STORE_ERROR",
        category=ErrorCategory.MEMORY,
        severity=ErrorSeverity.HIGH,
        message_template="Memory store error: {detail}",
        http_status=500,
        retryable=True,
        description="An error occurred in the memory storage system.",
    ),
    ErrorDefinition(
        code="MEMORY_NOT_FOUND",
        category=ErrorCategory.MEMORY,
        severity=ErrorSeverity.LOW,
        message_template="Memory entry '{memory_id}' not found",
        http_status=404,
        retryable=False,
        description="The requested memory entry does not exist.",
    ),
    ErrorDefinition(
        code="MEMORY_CAPACITY_EXCEEDED",
        category=ErrorCategory.MEMORY,
        severity=ErrorSeverity.MEDIUM,
        message_template="Memory capacity exceeded for '{scope}'",
        http_status=413,
        retryable=False,
        description="Memory storage capacity has been exceeded.",
    ),

    # DATABASE errors
    ErrorDefinition(
        code="DATABASE_CONNECTION_ERROR",
        category=ErrorCategory.DATABASE,
        severity=ErrorSeverity.CRITICAL,
        message_template="Database connection error: {detail}",
        http_status=503,
        retryable=True,
        description="Failed to connect to the database.",
    ),
    ErrorDefinition(
        code="DATABASE_QUERY_ERROR",
        category=ErrorCategory.DATABASE,
        severity=ErrorSeverity.HIGH,
        message_template="Database query error: {detail}",
        http_status=500,
        retryable=False,
        description="A database query failed to execute.",
    ),

    # CONFIGURATION errors
    ErrorDefinition(
        code="CONFIG_INVALID",
        category=ErrorCategory.CONFIGURATION,
        severity=ErrorSeverity.HIGH,
        message_template="Invalid configuration: {detail}",
        http_status=500,
        retryable=False,
        description="The system configuration is invalid.",
    ),
    ErrorDefinition(
        code="CONFIG_MISSING",
        category=ErrorCategory.CONFIGURATION,
        severity=ErrorSeverity.HIGH,
        message_template="Missing configuration key: '{key}'",
        http_status=500,
        retryable=False,
        description="A required configuration key is missing.",
    ),

    # NETWORK errors
    ErrorDefinition(
        code="NETWORK_CONNECTION_ERROR",
        category=ErrorCategory.NETWORK,
        severity=ErrorSeverity.HIGH,
        message_template="Network connection error: {detail}",
        http_status=502,
        retryable=True,
        description="A network connection failed.",
    ),
    ErrorDefinition(
        code="NETWORK_TIMEOUT",
        category=ErrorCategory.NETWORK,
        severity=ErrorSeverity.HIGH,
        message_template="Network timeout after {timeout}s",
        http_status=504,
        retryable=True,
        description="A network operation timed out.",
    ),
]


# Global default registry instance
_default_registry: Optional[ErrorCodeRegistry] = None
_registry_lock = threading.Lock()


def get_default_registry() -> ErrorCodeRegistry:
    """Get the global default error code registry.

    Creates and populates the registry on first call.

    Returns:
        The global ErrorCodeRegistry instance.
    """
    global _default_registry
    with _registry_lock:
        if _default_registry is None:
            _default_registry = ErrorCodeRegistry()
            _default_registry.register_many(DEFAULT_ERROR_DEFINITIONS)
        return _default_registry


def create_error(
    code: str,
    message_params: Optional[Dict[str, Any]] = None,
    details: Optional[Dict[str, Any]] = None,
    custom_message: Optional[str] = None,
) -> LiuHaoErrorCodeError:
    """Create an error using the default registry.

    This is a convenience function that uses the global default registry.

    Args:
        code: The error code.
        message_params: Parameters for message formatting.
        details: Additional error details.
        custom_message: Override the default message.

    Returns:
        LiuHaoErrorCodeError instance.
    """
    registry = get_default_registry()
    return registry.create_error(
        code=code,
        message_params=message_params,
        details=details,
        custom_message=custom_message,
    )


def register_error(definition: ErrorDefinition) -> None:
    """Register a custom error code in the default registry.

    Args:
        definition: The error definition to register.
    """
    registry = get_default_registry()
    registry.register(definition)
