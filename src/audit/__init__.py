"""
Audit Module for LiuHao AI OS

Provides:
- Audit event data models
- Audit event storage
- Convenience functions for common audit events
"""

from typing import Optional

from .models import AuditEvent, EventType, EventStatus, Severity, auth_event, system_event, security_violation_event  # noqa: F401
from .store import AuditStore

# Module-level store instance
_default_store: Optional[AuditStore] = None


def get_audit_store() -> AuditStore:
    """Get the default audit store instance."""
    global _default_store
    if _default_store is None:
        _default_store = AuditStore()
    return _default_store


def emit(event: AuditEvent) -> str:
    """Emit (store) an audit event using the default store."""
    return get_audit_store().emit(event)


def emit_simple(event_type: str, source: str, **kwargs) -> str:
    """Simple event emission using the default store."""
    return get_audit_store().emit_simple(event_type, source, **kwargs)
