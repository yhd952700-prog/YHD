"""
DEPRECATED — Legacy audit compatibility layer. NOT the runtime audit chain.

⚠️  This module (`src.audit`) is a *legacy compatibility shim*. Its events are
written to a plain JSON file (default `data/audit/events.json`) and are **NOT**
part of the tamper-evident audit chain.

The authoritative, evidence-grade audit implementation is
`src.kernels.audit` (SQLite-backed, hash-chain, `chain_state` anchor). That
store is the **single source of truth** for audit evidence — it backs the
L10K gate, the dashboard `audit.total_events` metric, and the reliability gate.

`from src.audit import ...` is kept importable ONLY so older documentation and
the quickstart self-check do not break. New code MUST use `src.kernels.audit`.
Events emitted through this layer carry no tamper-evident chain.
"""

import warnings
from typing import Optional

from .models import AuditEvent, EventType, EventStatus, Severity, auth_event, system_event, security_violation_event  # noqa: F401
from .store import AuditStore

warnings.warn(
    "src.audit is a deprecated legacy compatibility layer and is NOT the "
    "tamper-evident audit chain. Use src.kernels.audit (SQLite + hash chain) "
    "for authoritative, evidence-grade audit logging.",
    DeprecationWarning,
    stacklevel=2,
)

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
