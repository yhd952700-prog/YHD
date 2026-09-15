"""Minimal RBAC service consumed by the Memory System refactor.

Provides the permission contract ``MemoryService`` depends on:
``has_permission(user, permission)`` and ``is_admin(user)``.

This is a self-contained, in-memory implementation. It is intentionally minimal
and will be converged with the full identity authority
(``src/identity/__init__.py``) per the access-control design; the call sites in
``memory_system.py`` already match this contract, so no refactor edits are
needed when that convergence happens.
"""

from __future__ import annotations

from enum import Enum
from typing import Dict, Optional, Set

from .models import User


class Permission(str, Enum):
    """Knowledge-layer permissions."""

    KNOWLEDGE_READ = "knowledge:read"
    KNOWLEDGE_WRITE = "knowledge:write"


class RBACService:
    """In-memory role/permission store keyed by user id."""

    def __init__(self, grants: Optional[Dict[str, Set[Permission]]] = None):
        self._grants: Dict[str, Set[Permission]] = {
            uid: set(perms) for uid, perms in (grants or {}).items()
        }

    def grant(self, user: User, permission: Permission) -> None:
        """Grant ``permission`` to ``user``."""
        self._grants.setdefault(user.id, set()).add(permission)

    def revoke(self, user: User, permission: Permission) -> None:
        """Revoke ``permission`` from ``user`` (no-op if absent)."""
        self._grants.get(user.id, set()).discard(permission)

    def has_permission(self, user: User, permission: Permission) -> bool:
        """Return True if ``user`` holds ``permission``."""
        return permission in self._grants.get(user.id, set())

    def is_admin(self, user: User) -> bool:
        """Return True if ``user`` is flagged as an administrator."""
        return bool(getattr(user, "is_admin", False))
