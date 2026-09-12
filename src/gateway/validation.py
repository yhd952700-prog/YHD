"""
Request Validation for LiuHao AI OS Gateway

Provides:
- Pydantic models for all request/response validation
- OpenAPI schema generation support
- Unified validation middleware
- Error response formatting
"""

from typing import Optional, List, Dict, Any, Type
from datetime import datetime
from src._time import utc_now
from pydantic import BaseModel, Field


class APIError(BaseModel):
    """Standard API error response model."""
    error: str = Field(..., description="Error category")
    detail: str = Field(..., description="Human-readable error detail")
    code: Optional[str] = Field(None, description="Error code (optional)")
    trace_id: Optional[str] = Field(None, description="Trace ID for debugging")
    timestamp: float = Field(default_factory=lambda: utc_now().timestamp())

    class Config:
        schema_extra = {
            "example": {
                "error": "validation_error",
                "detail": "Invalid input data",
                "code": "INVALID_INPUT",
                "trace_id": "abc-123-def"
            }
        }


# ==================== Common Validation Models ====================

class PaginationParams(BaseModel):
    """Pagination parameters for list endpoints."""
    page: int = Field(1, ge=1, description="Page number (1-indexed)")
    per_page: int = Field(
        20, ge=1, le=100, description="Items per page (max 100)"
    )


class SortParams(BaseModel):
    """Sorting parameters for list endpoints."""
    field: Optional[str] = Field(None, description="Field to sort by")
    direction: Optional[str] = Field(
        "asc", pattern="^(asc|desc)$", description="Sort direction"
    )


class FilterParams(BaseModel):
    """Base filter parameters for list endpoints."""
    search: Optional[str] = Field(None, max_length=255, description="Search query")
    created_after: Optional[datetime] = Field(None, description="Filter by creation date")
    created_before: Optional[datetime] = Field(None, description="Filter by creation date")


# ==================== Security Validation Models ====================

class AuthRequest(BaseModel):
    """Authentication request base model."""
    api_key: Optional[str] = Field(None, description="API key for authentication")
    scope: Optional[List[str]] = Field(None, description="Required scopes")


class TokenRefreshRequest(BaseModel):
    """Refresh token request model."""
    refresh_token: str = Field(..., description="Refresh token")
    new_scopes: Optional[List[str]] = Field(None, description="New scopes requested")


# ==================== Gateway Validation Models ====================

class GatewayHealthCheck(BaseModel):
    """Health check request/response model."""
    service: str = Field(..., description="Service name")
    version: str = Field(..., description="Service version")
    status: str = Field(..., description="Health status: ok, degraded, critical")
    timestamp: float = Field(default_factory=lambda: utc_now().timestamp())
    latency_ms: Optional[int] = Field(None, description="Response latency in ms")


class RateLimitInfo(BaseModel):
    """Rate limit information model."""
    limit: int = Field(..., description="Rate limit per window")
    remaining: int = Field(..., description="Remaining requests in current window")
    reset: float = Field(..., description="When the rate limit resets (unix timestamp)")
    limit_type: str = Field(..., description="Type of rate limit (per_key, per_ip, etc)")


# ==================== Validation Utilities ====================


def validate_request(model: Type[BaseModel], data: Dict[str, Any]) -> tuple:
    """
    Validate request data against a Pydantic model.

    Returns:
        (validated_object, errors_dict)
    """
    try:
        obj = model(**data)
        return obj, {}
    except Exception as e:
        errors = {}
        for field_name, field_errors in e.errors().items():
            field_name_str = str(field_name)
            errors[field_name_str] = [
                f"{err['msg']} (field: {err['loc']})" for err in field_errors
            ]
        return None, errors


def add_validation_error_headers(response, errors: Dict[str, Any]) -> None:
    """
    Add validation error headers to response.

    Sets X-Validation-Count and X-Validation-First-Field headers.
    """
    if errors:
        first_field = next(iter(errors), None) if errors else None
        response.headers["X-Validation-Count"] = str(len(errors))
        if first_field:
            response.headers["X-Validation-First-Field"] = first_field


# Convenience export
def get_config():
    """Get configuration instance for validation settings."""
    try:
        from ..config_manager import get_config as _get_config
        return _get_config()
    except Exception:
        return None
