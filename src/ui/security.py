"""Security audit console for the product console."""

from dataclasses import dataclass, field
from typing import Dict, Any, List
from enum import Enum
from datetime import datetime


class AuditSeverity(Enum):
    """Audit log severity levels."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class SecurityAuditConsole:
    """Console for security audit monitoring and alerts."""

    audit_logs: List[Dict[str, Any]] = field(default_factory=list)
    high_risk_events: int = 0
    medium_risk_events: int = 0
    total_audit_events: int = 0

    def add_audit_event(self, event: Dict[str, Any], severity: Any = AuditSeverity.MEDIUM) -> None:
        """Add an audit event to the console log."""
        if isinstance(severity, str):
            severity_value = severity
        else:
            severity_value = severity.value
        event_with_time = {**event, "timestamp": datetime.now().isoformat(), "severity": severity_value}
        self.audit_logs.append(event_with_time)
        self.total_audit_events += 1

        if severity_value == "high":
            self.high_risk_events += 1
        elif severity_value == "medium":
            self.medium_risk_events += 1

    def get_recent_events(self, count: int = 10) -> List[Dict[str, Any]]:
        """Get the most recent audit events."""
        return self.audit_logs[-count:] if self.audit_logs else []

    def get_high_risk_events(self) -> List[Dict[str, Any]]:
        """Get all high-risk audit events."""
        return [e for e in self.audit_logs if e.get("severity") == "high"]

    def to_dict(self) -> Dict[str, Any]:
        """Convert security console to dictionary for UI rendering."""
        return {
            "total_audit_events": self.total_audit_events,
            "high_risk_events": self.high_risk_events,
            "medium_risk_events": self.medium_risk_events,
            "recent_events": self.get_recent_events(),
            "high_risk": self.get_high_risk_events(),
        }
