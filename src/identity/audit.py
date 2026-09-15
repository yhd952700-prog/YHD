"""Minimal audit service consumed by the Memory System refactor.

``MemoryService`` calls ``audit.log(...)`` and ``audit.log_permission_denied(...)``
with an ``AsyncSession``. This implementation records events in memory so the
audit contract is satisfied and observable in tests without requiring a
dedicated audit-log table yet. A persistence backend can be added later without
changing the call sites.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


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
    """In-memory audit log."""

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
