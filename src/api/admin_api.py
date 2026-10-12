"""Admin API endpoints for LiuHao AI OS.

RESTful API endpoints for administration:
- System configuration
- User management
- System statistics
- Audit log access
- Feature flags
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field


router = APIRouter(prefix="/admin", tags=["admin"])


# =====================================================================
# Models
# =====================================================================


class SystemConfigUpdate(BaseModel):
    """System configuration update request."""
    key: str = Field(..., description="Config key")
    value: Any = Field(..., description="Config value")


class UserCreate(BaseModel):
    """User creation request."""
    username: str = Field(..., min_length=3, max_length=50)
    email: str = Field(..., min_length=5, max_length=100)
    role: str = Field(default="user", description="User role")
    password_hash: str = Field(..., min_length=10)


class UserUpdate(BaseModel):
    """User update request."""
    email: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None


class UserResponse(BaseModel):
    """User response model."""
    id: str
    username: str
    email: str
    role: str
    is_active: bool
    created_at: str
    last_login: Optional[str] = None


class FeatureFlagCreate(BaseModel):
    """Feature flag creation request."""
    name: str = Field(..., min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    enabled: bool = False
    target_percentage: float = Field(default=0.0, ge=0.0, le=100.0)


class FeatureFlagResponse(BaseModel):
    """Feature flag response model."""
    id: str
    name: str
    description: str
    enabled: bool
    target_percentage: float
    created_at: str
    updated_at: str


# =====================================================================
# In-memory stores (scaffold)
# =====================================================================


_system_config: Dict[str, Any] = {
    "max_agents_per_user": 10,
    "default_model": "gpt-4",
    "rate_limit_per_minute": 60,
    "memory_retention_days": 90,
    "backup_enabled": True,
    "maintenance_mode": False,
}

_users: Dict[str, Dict[str, Any]] = {}

_audit_logs: List[Dict[str, Any]] = [
    {
        "id": "audit-001",
        "timestamp": "2026-01-15T10:30:00Z",
        "actor": "system",
        "action": "login",
        "resource": "auth",
        "status": "success",
        "details": {"ip": "127.0.0.1"},
    },
]

_feature_flags: Dict[str, Dict[str, Any]] = {}


def _get_user_or_404(user_id: str) -> Dict[str, Any]:
    """Get user by ID or raise 404."""
    user = _users.get(user_id)
    if not user:
        raise HTTPException(status_code=404, detail=f"User not found: {user_id}")
    return user


# =====================================================================
# System Config Endpoints
# =====================================================================


@router.get("/config")
async def get_system_config() -> Dict[str, Any]:
    """Get all system configuration values.

    Returns:
        Dict with all config key-value pairs.
    """
    return {
        "config": dict(_system_config),
        "count": len(_system_config),
    }


@router.get("/config/{key}")
async def get_config_value(key: str) -> Dict[str, Any]:
    """Get a specific configuration value.

    Args:
        key: Config key.

    Returns:
        Config value.

    Raises:
        HTTPException: 404 if key not found.
    """
    if key not in _system_config:
        raise HTTPException(status_code=404, detail=f"Config key not found: {key}")
    return {"key": key, "value": _system_config[key]}


@router.put("/config/{key}")
async def update_config_value(key: str, body: Dict[str, Any]) -> Dict[str, Any]:
    """Update a system configuration value.

    Args:
        key: Config key.
        body: Dict with 'value' field.

    Returns:
        Updated config value.
    """
    value = body.get("value")
    if value is None:
        raise HTTPException(status_code=400, detail="'value' field is required")
    _system_config[key] = value
    return {"key": key, "value": value, "updated_at": datetime.utcnow().isoformat()}


# =====================================================================
# User Management Endpoints
# =====================================================================


@router.get("/users")
async def list_users(
    role: Optional[str] = Query(None),
    active: Optional[bool] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> Dict[str, Any]:
    """List all users with pagination and filtering.

    Args:
        role: Filter by role.
        active: Filter by active status.
        limit: Result limit.
        offset: Pagination offset.

    Returns:
        Paginated user list.
    """
    users = list(_users.values())
    if role:
        users = [u for u in users if u["role"] == role]
    if active is not None:
        users = [u for u in users if u["is_active"] == active]
    total = len(users)
    items = users[offset : offset + limit]
    return {"total": total, "limit": limit, "offset": offset, "items": items}


@router.post("/users", status_code=201)
async def create_user(body: UserCreate) -> Dict[str, Any]:
    """Create a new user.

    Args:
        body: User creation request.

    Returns:
        Created user details.
    """
    user_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    user = {
        "id": user_id,
        "username": body.username,
        "email": body.email,
        "role": body.role,
        "is_active": True,
        "created_at": now,
        "last_login": None,
    }
    _users[user_id] = user
    return user


@router.get("/users/{user_id}")
async def get_user(user_id: str) -> Dict[str, Any]:
    """Get user details.

    Args:
        user_id: User identifier.

    Returns:
        User details.

    Raises:
        HTTPException: 404 if not found.
    """
    return _get_user_or_404(user_id)


@router.put("/users/{user_id}")
async def update_user(user_id: str, body: UserUpdate) -> Dict[str, Any]:
    """Update a user.

    Args:
        user_id: User identifier.
        body: Update request.

    Returns:
        Updated user details.

    Raises:
        HTTPException: 404 if not found.
    """
    user = _get_user_or_404(user_id)
    update_data = body.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        if value is not None:
            user[key] = value
    return user


@router.delete("/users/{user_id}", status_code=204)
async def delete_user(user_id: str) -> None:
    """Delete a user.

    Args:
        user_id: User identifier.

    Raises:
        HTTPException: 404 if not found.
    """
    if user_id not in _users:
        raise HTTPException(status_code=404, detail=f"User not found: {user_id}")
    del _users[user_id]


# =====================================================================
# System Stats Endpoint
# =====================================================================


@router.get("/stats")
async def get_system_stats() -> Dict[str, Any]:
    """Get comprehensive system statistics.

    Returns:
        Dict with system statistics.
    """
    return {
        "timestamp": datetime.utcnow().isoformat(),
        "users": {
            "total": len(_users),
            "active": sum(1 for u in _users.values() if u.get("is_active", True)),
        },
        "config_keys": len(_system_config),
        "feature_flags": len(_feature_flags),
        "audit_logs": len(_audit_logs),
        "system": {
            "version": "1.0.0",
            "environment": "production",
            "uptime_hours": 720.5,
        },
    }


# =====================================================================
# Audit Log Endpoints
# =====================================================================


@router.get("/audit-log")
async def list_audit_logs(
    action: Optional[str] = Query(None),
    actor: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> Dict[str, Any]:
    """List audit log entries with filtering.

    Args:
        action: Filter by action type.
        actor: Filter by actor.
        limit: Result limit.
        offset: Pagination offset.

    Returns:
        Paginated audit log entries.
    """
    logs = list(_audit_logs)
    if action:
        logs = [l for l in logs if l["action"] == action]
    if actor:
        logs = [l for l in logs if l["actor"] == actor]
    logs.sort(key=lambda l: l["timestamp"], reverse=True)
    total = len(logs)
    items = logs[offset : offset + limit]
    return {"total": total, "limit": limit, "offset": offset, "items": items}


@router.get("/audit-log/{log_id}")
async def get_audit_log_entry(log_id: str) -> Dict[str, Any]:
    """Get a specific audit log entry.

    Args:
        log_id: Audit log entry ID.

    Returns:
        Audit log entry details.

    Raises:
        HTTPException: 404 if not found.
    """
    for entry in _audit_logs:
        if entry["id"] == log_id:
            return entry
    raise HTTPException(status_code=404, detail=f"Audit log entry not found: {log_id}")


# =====================================================================
# Feature Flags Endpoints
# =====================================================================


@router.get("/feature-flags")
async def list_feature_flags(
    enabled_only: bool = Query(False),
) -> Dict[str, Any]:
    """List all feature flags.

    Args:
        enabled_only: Only return enabled flags.

    Returns:
        List of feature flags.
    """
    flags = list(_feature_flags.values())
    if enabled_only:
        flags = [f for f in flags if f["enabled"]]
    return {"flags": flags, "total": len(flags)}


@router.post("/feature-flags", status_code=201)
async def create_feature_flag(body: FeatureFlagCreate) -> Dict[str, Any]:
    """Create a new feature flag.

    Args:
        body: Feature flag creation request.

    Returns:
        Created flag details.
    """
    flag_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    flag = {
        "id": flag_id,
        "name": body.name,
        "description": body.description,
        "enabled": body.enabled,
        "target_percentage": body.target_percentage,
        "created_at": now,
        "updated_at": now,
    }
    _feature_flags[flag_id] = flag
    return flag


@router.put("/feature-flags/{flag_id}")
async def update_feature_flag(
    flag_id: str,
    body: Dict[str, Any],
) -> Dict[str, Any]:
    """Update a feature flag.

    Args:
        flag_id: Feature flag ID.
        body: Update fields.

    Returns:
        Updated flag details.

    Raises:
        HTTPException: 404 if not found.
    """
    if flag_id not in _feature_flags:
        raise HTTPException(status_code=404, detail=f"Feature flag not found: {flag_id}")
    flag = _feature_flags[flag_id]
    for key in ("name", "description", "enabled", "target_percentage"):
        if key in body:
            flag[key] = body[key]
    flag["updated_at"] = datetime.utcnow().isoformat()
    return flag


@router.delete("/feature-flags/{flag_id}", status_code=204)
async def delete_feature_flag(flag_id: str) -> None:
    """Delete a feature flag.

    Args:
        flag_id: Feature flag ID.

    Raises:
        HTTPException: 404 if not found.
    """
    if flag_id not in _feature_flags:
        raise HTTPException(status_code=404, detail=f"Feature flag not found: {flag_id}")
    del _feature_flags[flag_id]
