"""Comprehensive error code tests for LiuHao AI OS.

Covers:
- Error categories and codes
- Error severity levels
- HTTP status mapping
- Retryable flag
- Error factory functions
- Custom error registration
- Error formatting
- Error metadata
"""

import pytest
from typing import Any

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


# ======================================================================
# ErrorCategory Tests (7 tests)
# ======================================================================

class TestErrorCategory:
    """Tests for ErrorCategory enumeration."""

    def test_all_categories_exist(self):
        """Test all expected error categories are defined."""
        categories = list(ErrorCategory)
        expected = [
            "SYSTEM", "AUTH", "VALIDATION", "RATE_LIMIT",
            "NOT_FOUND", "AI_PROVIDER", "MEMORY", "DATABASE",
            "CONFIGURATION", "NETWORK", "INTERNAL",
        ]
        for cat in expected:
            assert hasattr(ErrorCategory, cat), f"Missing category: {cat}"
        assert len(categories) >= 11

    def test_system_category(self):
        """Test SYSTEM category."""
        assert ErrorCategory.SYSTEM.value == "SYSTEM"

    def test_auth_category(self):
        """Test AUTH category."""
        assert ErrorCategory.AUTH.value == "AUTH"

    def test_validation_category(self):
        """Test VALIDATION category."""
        assert ErrorCategory.VALIDATION.value == "VALIDATION"

    def test_rate_limit_category(self):
        """Test RATE_LIMIT category."""
        assert ErrorCategory.RATE_LIMIT.value == "RATE_LIMIT"

    def test_not_found_category(self):
        """Test NOT_FOUND category."""
        assert ErrorCategory.NOT_FOUND.value == "NOT_FOUND"

    def test_internal_category(self):
        """Test INTERNAL category."""
        assert ErrorCategory.INTERNAL.value == "INTERNAL"


# ======================================================================
# ErrorSeverity Tests (6 tests)
# ======================================================================

class TestErrorSeverity:
    """Tests for ErrorSeverity enumeration."""

    def test_all_severity_levels(self):
        """Test all severity levels exist."""
        levels = list(ErrorSeverity)
        assert ErrorSeverity.LOW in levels
        assert ErrorSeverity.MEDIUM in levels
        assert ErrorSeverity.HIGH in levels
        assert ErrorSeverity.CRITICAL in levels
        assert len(levels) == 4

    def test_low_severity(self):
        """Test LOW severity."""
        assert ErrorSeverity.LOW.value == "low"

    def test_medium_severity(self):
        """Test MEDIUM severity."""
        assert ErrorSeverity.MEDIUM.value == "medium"

    def test_high_severity(self):
        """Test HIGH severity."""
        assert ErrorSeverity.HIGH.value == "high"

    def test_critical_severity(self):
        """Test CRITICAL severity."""
        assert ErrorSeverity.CRITICAL.value == "critical"


# ======================================================================
# ErrorDefinition Tests (7 tests)
# ======================================================================

class TestErrorDefinition:
    """Tests for ErrorDefinition."""

    def test_definition_creation(self):
        """Test creating an error definition."""
        ed = ErrorDefinition(
            code="TEST_ERROR",
            category=ErrorCategory.VALIDATION,
            severity=ErrorSeverity.MEDIUM,
            message_template="Invalid input: {field}",
            http_status=400,
            retryable=False,
            description="Test error for validation",
        )
        assert ed.code == "TEST_ERROR"
        assert ed.category == ErrorCategory.VALIDATION
        assert ed.severity == ErrorSeverity.MEDIUM
        assert ed.http_status == 400
        assert ed.retryable is False

    def test_default_values(self):
        """Test default values for ErrorDefinition."""
        ed = ErrorDefinition(
            code="SIMPLE",
            category=ErrorCategory.SYSTEM,
            severity=ErrorSeverity.LOW,
        )
        assert ed.message_template == "An error occurred"
        assert ed.http_status == 500
        assert ed.retryable is False
        assert ed.description == ""

    def test_format_message_with_params(self):
        """Test message template formatting."""
        ed = ErrorDefinition(
            code="PARAM_ERROR",
            category=ErrorCategory.VALIDATION,
            severity=ErrorSeverity.MEDIUM,
            message_template="Parameter '{name}' is invalid: {reason}",
        )
        msg = ed.format_message(name="email", reason="bad format")
        assert msg == "Parameter 'email' is invalid: bad format"

    def test_format_message_missing_params(self):
        """Test format_message with missing params returns template."""
        ed = ErrorDefinition(
            code="E1",
            category=ErrorCategory.SYSTEM,
            severity=ErrorSeverity.LOW,
            message_template="Error: {missing_var}",
        )
        msg = ed.format_message()
        # Should return template as-is when formatting fails
        assert "Error:" in msg

    def test_retryable_flag(self):
        """Test retryable flag on error definition."""
        transient = ErrorDefinition(
            code="TRANSIENT",
            category=ErrorCategory.NETWORK,
            severity=ErrorSeverity.HIGH,
            retryable=True,
        )
        permanent = ErrorDefinition(
            code="PERMANENT",
            category=ErrorCategory.VALIDATION,
            severity=ErrorSeverity.MEDIUM,
            retryable=False,
        )
        assert transient.retryable is True
        assert permanent.retryable is False

    def test_http_status_mapping(self):
        """Test HTTP status code mapping."""
        ed_400 = ErrorDefinition(
            code="BAD_REQ",
            category=ErrorCategory.VALIDATION,
            severity=ErrorSeverity.MEDIUM,
            http_status=400,
        )
        ed_404 = ErrorDefinition(
            code="NOT_FOUND_ERR",
            category=ErrorCategory.NOT_FOUND,
            severity=ErrorSeverity.MEDIUM,
            http_status=404,
        )
        ed_500 = ErrorDefinition(
            code="SYS_ERR",
            category=ErrorCategory.SYSTEM,
            severity=ErrorSeverity.CRITICAL,
            http_status=500,
        )
        assert ed_400.http_status == 400
        assert ed_404.http_status == 404
        assert ed_500.http_status == 500

    def test_description_field(self):
        """Test error description field."""
        ed = ErrorDefinition(
            code="DESC_TEST",
            category=ErrorCategory.AUTH,
            severity=ErrorSeverity.HIGH,
            description="Raised when authentication token is invalid",
        )
        assert "authentication token" in ed.description


# ======================================================================
# LiuHaoErrorCodeError Tests (6 tests)
# ======================================================================

class TestLiuHaoErrorCodeError:
    """Tests for LiuHaoErrorCodeError exception."""

    def test_error_creation(self):
        """Test creating a LiuHaoErrorCodeError."""
        err = LiuHaoErrorCodeError(
            error_code="TEST_001",
            message="Something went wrong",
            category=ErrorCategory.SYSTEM,
            severity=ErrorSeverity.HIGH,
        )
        assert err.error_code == "TEST_001"
        assert err.message == "Something went wrong"
        assert err.category == ErrorCategory.SYSTEM
        assert err.severity == ErrorSeverity.HIGH
        assert str(err) == "Something went wrong"

    def test_default_values(self):
        """Test default values for LiuHaoErrorCodeError."""
        err = LiuHaoErrorCodeError(error_code="DEFAULT_TEST")
        assert err.category == ErrorCategory.INTERNAL
        assert err.severity == ErrorSeverity.MEDIUM
        assert err.message == "An error occurred"
        assert err.http_status == 500
        assert err.retryable is False
        assert err.details == {}

    def test_details_metadata(self):
        """Test error details/metadata."""
        err = LiuHaoErrorCodeError(
            error_code="DETAIL_TEST",
            message="Validation failed",
            details={
                "field": "email",
                "value": "not-an-email",
                "constraints": ["email format"],
            },
        )
        assert err.details["field"] == "email"
        assert err.details["value"] == "not-an-email"
        assert len(err.details["constraints"]) == 1

    def test_to_dict_serialization(self):
        """Test error to_dict serialization."""
        err = LiuHaoErrorCodeError(
            error_code="SERIALIZE_TEST",
            message="test error",
            category=ErrorCategory.VALIDATION,
            severity=ErrorSeverity.MEDIUM,
            http_status=400,
            retryable=False,
            details={"key": "value"},
        )
        d = err.to_dict()
        assert d["error_code"] == "SERIALIZE_TEST"
        assert d["category"] == "VALIDATION"
        assert d["severity"] == "medium"
        assert d["message"] == "test error"
        assert d["http_status"] == 400
        assert d["retryable"] is False
        assert d["details"] == {"key": "value"}

    def test_repr_format(self):
        """Test error __repr__ format."""
        err = LiuHaoErrorCodeError(
            error_code="REPR_TEST",
            message="repr test",
            severity=ErrorSeverity.HIGH,
        )
        r = repr(err)
        assert "REPR_TEST" in r
        assert "high" in r
        assert "repr test" in r

    def test_is_exception(self):
        """Test that LiuHaoErrorCodeError is a proper Exception."""
        err = LiuHaoErrorCodeError(error_code="EXC_TEST", message="test")
        assert isinstance(err, Exception)
        assert isinstance(err, LiuHaoErrorCodeError)


# ======================================================================
# ErrorCodeRegistry Tests (8 tests)
# ======================================================================

class TestErrorCodeRegistry:
    """Tests for ErrorCodeRegistry."""

    def test_register_error_definition(self):
        """Test registering an error definition."""
        registry = ErrorCodeRegistry()
        ed = ErrorDefinition(
            code="CUSTOM_ERR",
            category=ErrorCategory.VALIDATION,
            severity=ErrorSeverity.MEDIUM,
            message_template="Custom error occurred",
        )
        registry.register(ed)
        assert registry.has("CUSTOM_ERR")

    def test_register_duplicate_raises(self):
        """Test registering duplicate error code raises ValueError."""
        registry = ErrorCodeRegistry()
        ed = ErrorDefinition(
            code="DUPLICATE",
            category=ErrorCategory.SYSTEM,
            severity=ErrorSeverity.LOW,
        )
        registry.register(ed)
        with pytest.raises(ValueError, match="already registered"):
            registry.register(ed)

    def test_get_definition(self):
        """Test getting an error definition."""
        registry = ErrorCodeRegistry()
        ed = ErrorDefinition(
            code="GET_TEST",
            category=ErrorCategory.AUTH,
            severity=ErrorSeverity.HIGH,
            message_template="Auth failed",
            http_status=401,
        )
        registry.register(ed)
        found = registry.get("GET_TEST")
        assert found is not None
        assert found.code == "GET_TEST"
        assert found.http_status == 401

    def test_get_nonexistent_returns_none(self):
        """Test getting nonexistent error code returns None."""
        registry = ErrorCodeRegistry()
        assert registry.get("NO_SUCH_CODE") is None

    def test_list_codes(self):
        """Test listing all registered error codes."""
        registry = ErrorCodeRegistry()
        registry.register(ErrorDefinition(
            code="A", category=ErrorCategory.SYSTEM, severity=ErrorSeverity.LOW
        ))
        registry.register(ErrorDefinition(
            code="B", category=ErrorCategory.VALIDATION, severity=ErrorSeverity.MEDIUM
        ))
        codes = registry.list_codes()
        assert "A" in codes
        assert "B" in codes
        assert len(codes) == 2

    def test_list_codes_filtered_by_category(self):
        """Test listing error codes filtered by category."""
        registry = ErrorCodeRegistry()
        registry.register(ErrorDefinition(
            code="S1", category=ErrorCategory.SYSTEM, severity=ErrorSeverity.LOW
        ))
        registry.register(ErrorDefinition(
            code="V1", category=ErrorCategory.VALIDATION, severity=ErrorSeverity.MEDIUM
        ))
        registry.register(ErrorDefinition(
            code="V2", category=ErrorCategory.VALIDATION, severity=ErrorSeverity.HIGH
        ))

        validation_codes = registry.list_codes(category=ErrorCategory.VALIDATION)
        assert "V1" in validation_codes
        assert "V2" in validation_codes
        assert "S1" not in validation_codes
        assert len(validation_codes) == 2

    def test_create_error_from_registry(self):
        """Test creating an error instance from registry."""
        registry = ErrorCodeRegistry()
        ed = ErrorDefinition(
            code="CREATE_TEST",
            category=ErrorCategory.VALIDATION,
            severity=ErrorSeverity.MEDIUM,
            message_template="Field '{field}' is required",
            http_status=400,
            retryable=False,
        )
        registry.register(ed)

        err = registry.create_error(
            "CREATE_TEST",
            message_params={"field": "username"},
            details={"value": None},
        )
        assert isinstance(err, LiuHaoErrorCodeError)
        assert err.error_code == "CREATE_TEST"
        assert err.category == ErrorCategory.VALIDATION
        assert err.http_status == 400
        assert "username" in err.message

    def test_unregister_error(self):
        """Test unregistering an error code."""
        registry = ErrorCodeRegistry()
        registry.register(ErrorDefinition(
            code="REMOVE_ME",
            category=ErrorCategory.SYSTEM,
            severity=ErrorSeverity.LOW,
        ))
        assert registry.has("REMOVE_ME")

        result = registry.unregister("REMOVE_ME")
        assert result is True
        assert not registry.has("REMOVE_ME")


# ======================================================================
# Default Registry and Factory Tests (6 tests)
# ======================================================================

class TestDefaultRegistry:
    """Tests for default registry and factory functions."""

    def test_default_definitions_exist(self):
        """Test that DEFAULT_ERROR_DEFINITIONS is populated."""
        assert isinstance(DEFAULT_ERROR_DEFINITIONS, list)
        assert len(DEFAULT_ERROR_DEFINITIONS) > 0
        assert all(isinstance(d, ErrorDefinition) for d in DEFAULT_ERROR_DEFINITIONS)

    def test_get_default_registry(self):
        """Test get_default_registry returns a valid registry."""
        registry = get_default_registry()
        assert isinstance(registry, ErrorCodeRegistry)
        assert len(registry.list_codes()) > 0

    def test_create_error_factory(self):
        """Test create_error factory function."""
        # Should be able to create errors from default registry
        # (at least some common ones should exist)
        registry = get_default_registry()
        codes = registry.list_codes()
        if len(codes) > 0:
            first_code = codes[0]
            err = create_error(first_code)
            assert isinstance(err, LiuHaoErrorCodeError)
            assert err.error_code == first_code

    def test_register_error_function(self):
        """Test register_error convenience function."""
        ed = ErrorDefinition(
            code="FACTORY_TEST_001",
            category=ErrorCategory.INTERNAL,
            severity=ErrorSeverity.LOW,
        )
        register_error(ed)
        registry = get_default_registry()
        assert registry.has("FACTORY_TEST_001")

    def test_register_many(self):
        """Test register_many bulk registration."""
        registry = ErrorCodeRegistry()
        defs = [
            ErrorDefinition(code="B1", category=ErrorCategory.SYSTEM, severity=ErrorSeverity.LOW),
            ErrorDefinition(code="B2", category=ErrorCategory.AUTH, severity=ErrorSeverity.HIGH),
            ErrorDefinition(code="B3", category=ErrorCategory.NETWORK, severity=ErrorSeverity.MEDIUM),
        ]
        registry.register_many(defs)
        assert registry.has("B1")
        assert registry.has("B2")
        assert registry.has("B3")
        assert len(registry.list_codes()) == 3

    def test_custom_message_override(self):
        """Test custom message overrides template in create_error."""
        registry = ErrorCodeRegistry()
        registry.register(ErrorDefinition(
            code="CUSTOM_MSG",
            category=ErrorCategory.SYSTEM,
            severity=ErrorSeverity.LOW,
            message_template="Default message",
        ))
        err = registry.create_error("CUSTOM_MSG", custom_message="Overridden message")
        assert err.message == "Overridden message"
