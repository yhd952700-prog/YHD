"""Narrow contracts the Memory System (``memory_system.py``) depends on.

**What this module is NOT.** It is *not* the system's identity / permission /
audit authority. Those live elsewhere and must stay there:

* identity & permission authority -> ``src/identity/__init__.py``
  (``Principal``, ``IdentityManager``, ``Permission`` the *definition* dataclass)
* kernel-layer errors -> ``src/kernels/_base.py`` (``KernelError``)

**Why these live here now.** They were originally added as
``src/identity/{models,rbac,audit}.py`` and ``src/core/errors.py`` while the
Memory System refactor was being completed. That placement was wrong in two
concrete ways:

1. ``src/identity/rbac.Permission`` collided **by name** with the pre-existing
   ``src/identity.Permission`` while meaning something different (an enum of
   knowledge capabilities vs. a permission *definition* record) -- a textbook
   dual-authority trap inside the one package that must have a single voice.
2. ``src/core/errors.KnowledgeError`` and the ``User`` / ``AuditService`` models
   are knowledge-layer concerns sitting in packages named ``core`` / ``identity``,
   which reads as if they were shared, system-wide contracts. They are not: the
   only consumers are ``memory_system.py`` and its tests.

Moving them under ``src/knowledge/`` removes the name collision and makes the
ownership obvious. The classes themselves are unchanged.

**Scope note.** ``RBACService`` is an *injected*, in-memory implementation, not a
global policy source: ``MemoryService`` takes it as a constructor argument, so
production wiring can supply a real authority that satisfies the same two-method
contract (``has_permission`` / ``is_admin``) without touching call sites.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class KnowledgeError(Exception):
    """Base error for knowledge-layer operations."""


class NotFoundError(KnowledgeError):
    """The requested resource does not exist."""


class PermissionDeniedError(KnowledgeError):
    """The caller lacks the required permission for this operation."""


class ValidationError(KnowledgeError):
    """Input failed validation before the operation was attempted."""


# ---------------------------------------------------------------------------
# Subject
# ---------------------------------------------------------------------------


@dataclass
class User:
    """A knowledge/memory subject.

    Minimal and dependency-free: it only needs to carry ``id`` into the RBAC
    and audit calls below. Convergence with the full identity authority
    (``src/identity/__init__.py``) is a separate, deliberate step -- do not
    quietly alias this to ``Principal`` without deciding what happens to the
    permissions already granted in memory.
    """

    id: str
    is_admin: bool = False
    name: Optional[str] = None
    roles: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# RBAC
# ---------------------------------------------------------------------------


class Permission(str, Enum):
    """Knowledge-layer permissions.

    Deliberately *not* ``src.identity.Permission`` -- that one is a permission
    definition record (id / scope / resource pattern / actions). This is the
    capability being checked at a call site.
    """

    KNOWLEDGE_READ = "knowledge:read"
    KNOWLEDGE_WRITE = "knowledge:write"


class RBACService:
    """In-memory role/permission store keyed by user id.

    Injected into ``MemoryService``; replaceable by any object with the same
    two-method contract.
    """

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


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


class AuditAction(str, Enum):
    """Audit action types."""

    CREATE = "create"
    READ = "read"
    UPDATE = "update"
    DELETE = "delete"
    ACCESS = "access"


@dataclass
class AuditEntry:
    """A single recorded audit event."""

    action: AuditAction
    status: str
    user_id: Optional[str]
    resource_type: str
    resource_id: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)
    denied: bool = False


class AuditService:
    """In-memory audit log.

    Records events in memory so the audit contract is satisfied and observable
    in tests without requiring a dedicated audit-log table yet. A persistence
    backend can be added later without changing the call sites.
    """

    def __init__(self) -> None:
        self._entries: List[AuditEntry] = []

    @property
    def entries(self) -> List[AuditEntry]:
        """Return a copy of all recorded entries."""
        return list(self._entries)

    async def log(
        self,
        session,
        action: AuditAction,
        status: str,
        user_id: Optional[str],
        resource_type: str,
        resource_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> AuditEntry:
        """Record a successful (or non-denied) audit event."""
        entry = AuditEntry(
            action=action,
            status=status,
            user_id=user_id,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details or {},
        )
        self._entries.append(entry)
        return entry

    async def log_permission_denied(
        self,
        user_id: Optional[str],
        action: str,
        resource_type: str,
        resource_id: Optional[str] = None,
    ) -> AuditEntry:
        """Record a permission-denied event."""
        entry = AuditEntry(
            action=AuditAction.ACCESS,
            status="denied",
            user_id=user_id,
            resource_type=resource_type,
            resource_id=resource_id,
            details={"attempted_action": action},
            denied=True,
        )
        self._entries.append(entry)
        return entry
