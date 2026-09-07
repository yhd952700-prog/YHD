"""
Audit Event Models for LiuHao AI OS

Defines the data model for audit events with:
- Event type enumeration
- Standardized event structure
- Provenance tracking
- Severity classification
"""

import json
import time
import uuid
from datetime import datetime
from typing import Dict, Any, Optional, List


class EventType:
    """Enumeration of event types."""
    AUTHENTICATION = "authentication"
    AUTHORIZATION = "authorization"
    MODEL_INVOKE = "model_invoke"
    PLUGIN_EXECUTE = "plugin_execute"
    SYSTEM_ACTION = "system_action"
    DATA_ACCESS = "data_access"
    SECURITY_VIOLATION = "security_violation"
    USER_SESSION = "user_session"
    CONFIG_CHANGE = "config_change"
    ERROR = "error"


class EventStatus:
    """Event status values."""
    SUCCESS = "success"
    FAILURE = "failure"
    BLOCKED = "blocked"


class Severity:
    """Severity levels for audit events."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AuditEvent:
    """Standard audit event data model."""
    
    def __init__(self, event_id: str, event_type: str, timestamp: float,
                 source: str, user_id: Optional[str] = None,
                 session_id: Optional[str] = None, severity: str = "medium",
                 status: str = "success", message: str = "",
                 details: Optional[Dict[str, Any]] = None,
                 request_id: Optional[str] = None,
                 trace_id: Optional[str] = None,
                 extra: Optional[Dict[str, Any]] = None):
        self.event_id = event_id
        self.event_type = event_type
        self.timestamp = timestamp
        self.source = source
        self.user_id = user_id
        self.session_id = session_id
        self.severity = severity
        self.status = status
        self.message = message
        self.details = details if details is not None else {}
        self.request_id = request_id
        self.trace_id = trace_id
        self.extra = extra if extra is not None else {}
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "timestamp": self.timestamp,
            "source": self.source,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "severity": self.severity,
            "status": self.status,
            "message": self.message,
            "details": self.details,
            "request_id": self.request_id,
            "trace_id": self.trace_id,
            "extra": self.extra,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AuditEvent":
        """Create from dictionary."""
        return cls(
            event_id=data.get("event_id", str(uuid.uuid4())),
            event_type=data.get("event_type", ""),
            timestamp=data.get("timestamp", time.time()),
            source=data.get("source", ""),
            user_id=data.get("user_id"),
            session_id=data.get("session_id"),
            severity=data.get("severity", "medium"),
            status=data.get("status", "success"),
            message=data.get("message", ""),
            details=data.get("details"),
            request_id=data.get("request_id"),
            trace_id=data.get("trace_id"),
            extra=data.get("extra", {}),
        )


# Convenience functions
def auth_event(event_type: str, user_id: str, success: bool, 
               source: str = "auth_module", **kwargs) -> AuditEvent:
    """Create an authentication/authorization event."""
    return AuditEvent(
        event_id=str(uuid.uuid4()),
        event_type=event_type,
        timestamp=time.time(),
        source=source,
        user_id=user_id,
        severity=Severity.HIGH if not success else Severity.LOW,
        status=EventStatus.SUCCESS if success else EventStatus.FAILURE,
        message=kwargs.get("message", ""),
        details=kwargs.get("details", {}),
        request_id=kwargs.get("request_id"),
        trace_id=kwargs.get("trace_id"),
    )


def system_event(event_type: str, source: str, severity: str = "medium",
                 status: str = "success", **kwargs) -> AuditEvent:
    """Create a system-level event."""
    return AuditEvent(
        event_id=str(uuid.uuid4()),
        event_type=event_type,
        timestamp=time.time(),
        source=source,
        severity=severity,
        status=status,
        message=kwargs.get("message", ""),
        details=kwargs.get("details", {}),
        request_id=kwargs.get("request_id"),
        trace_id=kwargs.get("trace_id"),
        user_id=kwargs.get("user_id"),
        session_id=kwargs.get("session_id"),
    )


def security_violation_event(event_type: str, user_id: str, detail: str,
                             **kwargs) -> AuditEvent:
    """Create a security violation event."""
    return AuditEvent(
        event_id=str(uuid.uuid4()),
        event_type=event_type,
        timestamp=time.time(),
        source=kwargs.get("source", "auth_module"),
        user_id=user_id,
        severity=Severity.CRITICAL,
        status=EventStatus.FAILURE,
        message=detail,
        details=kwargs.get("details", {}),
        request_id=kwargs.get("request_id"),
        trace_id=kwargs.get("trace_id"),
    )