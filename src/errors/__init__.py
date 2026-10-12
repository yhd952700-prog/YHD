"""
Unified error code system for LiuHao AI OS.

Provides a comprehensive error code registry with categories,
severity levels, and factory functions for creating consistent
error objects across the system.
"""

from src.errors.error_codes import (
    ErrorCategory,
    ErrorSeverity,
    ErrorDefinition,
    LiuHaoErrorCodeError,
    ErrorCodeRegistry,
    DEFAULT_ERROR_DEFINITIONS,
    get_default_registry,
    create_error,
    register_error,
)

__all__ = [
    "ErrorCategory",
    "ErrorSeverity",
    "ErrorDefinition",
    "LiuHaoErrorCodeError",
    "ErrorCodeRegistry",
    "DEFAULT_ERROR_DEFINITIONS",
    "get_default_registry",
    "create_error",
    "register_error",
]
