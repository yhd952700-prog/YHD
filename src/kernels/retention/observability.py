"""HD-06 — observability hooks for retention actions.

Retention actions are observable but observability must never break control flow.
The default sink routes structured events to the Audit Kernel (reusing existing
event types, so ``src/kernels/audit/__init__.py`` is not modified), wrapped so a
backend failure degrades to "evidence=missing" rather than crashing the
retention operation. Tests inject a fake sink to avoid touching the audit DB.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

#: event-name -> details. Sinks are callables with this signature.
RetentionEventSink = Callable[[str, Dict[str, Any]], None]


@dataclass
class RetentionMetrics:
    """Cumulative counters for retention operations (observability only)."""

    evaluations: int = 0
    keeps: int = 0
    archives: int = 0
    deletes_derivative: int = 0
    blocked_by_hold: int = 0
    immutable_protected: int = 0  # attempts to delete an original that were blocked
    errors: int = 0

    def snapshot(self) -> Dict[str, int]:
        return {
            "evaluations": self.evaluations,
            "keeps": self.keeps,
            "archives": self.archives,
            "deletes_derivative": self.deletes_derivative,
            "blocked_by_hold": self.blocked_by_hold,
            "immutable_protected": self.immutable_protected,
            "errors": self.errors,
        }


def default_event_sink(event: str, details: Dict[str, Any]) -> None:
    """Route a retention event to the Audit Kernel (fail-soft)."""
    try:
        from src.kernels.audit import (
            AuditEventType,
            AuditScope,
            log_event,
        )

        outcome = details.get("outcome", "ok")
        log_event(
            AuditEventType.STATE_CHANGE,
            details.get("principal", "retention-manager"),
            AuditScope.L5,
            outcome,
            {**details, "retention_event": event},
        )
    except Exception:
        # Observability must not fail the retention control path. The audit
        # kernel itself records write failures via its own counter.
        pass
