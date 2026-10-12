"""
Security audit trail for LiuHao AI OS.

Provides security event logging, tamper-evident logging (hash chain),
audit log search/filtering, retention policies, compliance report
generation, and suspicious activity detection patterns.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class AuditEventType(str, Enum):
    """Types of security audit events."""

    LOGIN = "login"
    LOGOUT = "logout"
    LOGIN_FAILED = "login_failed"
    PERMISSION_CHANGE = "permission_change"
    ROLE_CHANGE = "role_change"
    DATA_ACCESS = "data_access"
    DATA_MODIFY = "data_modify"
    DATA_EXPORT = "data_export"
    CONFIG_CHANGE = "config_change"
    SECURITY_ALERT = "security_alert"
    KEY_ROTATION = "key_rotation"
    API_KEY_CREATE = "api_key_create"
    API_KEY_REVOKE = "api_key_revoke"
    PASSWORD_CHANGE = "password_change"
    MFA_ENABLE = "mfa_enable"
    MFA_DISABLE = "mfa_disable"
    RATE_LIMIT_EXCEEDED = "rate_limit_exceeded"
    SUSPICIOUS_ACTIVITY = "suspicious_activity"
    COMPLIANCE_VIOLATION = "compliance_violation"
    SYSTEM_ACCESS = "system_access"


class AuditSeverity(str, Enum):
    """Severity levels for audit events."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AuditLogStatus(str, Enum):
    """Status of audit log integrity verification."""

    VALID = "valid"
    TAMPERED = "tampered"
    UNVERIFIED = "unverified"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class AuditEvent:
    """Represents a single security audit event."""

    event_id: str
    event_type: AuditEventType
    severity: AuditSeverity
    timestamp: float
    actor_id: Optional[str] = None  # Who performed the action
    actor_type: Optional[str] = None  # user, system, api_key, etc.
    target_type: Optional[str] = None  # What was affected
    target_id: Optional[str] = None  # ID of what was affected
    action: Optional[str] = None  # Specific action taken
    resource: Optional[str] = None  # Resource path/identifier
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    status: Optional[str] = None  # success, failure, etc.
    details: dict[str, Any] = field(default_factory=dict)
    previous_hash: Optional[str] = None  # For hash chain
    event_hash: Optional[str] = None  # Hash of this event

    def to_dict(self) -> dict[str, Any]:
        """Serialize event to dictionary."""
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "severity": self.severity.value,
            "timestamp": self.timestamp,
            "actor_id": self.actor_id,
            "actor_type": self.actor_type,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "action": self.action,
            "resource": self.resource,
            "ip_address": self.ip_address,
            "user_agent": self.user_agent,
            "status": self.status,
            "details": self.details,
            "previous_hash": self.previous_hash,
            "event_hash": self.event_hash,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AuditEvent":
        """Deserialize event from dictionary."""
        return cls(
            event_id=data["event_id"],
            event_type=AuditEventType(data["event_type"]),
            severity=AuditSeverity(data["severity"]),
            timestamp=data["timestamp"],
            actor_id=data.get("actor_id"),
            actor_type=data.get("actor_type"),
            target_type=data.get("target_type"),
            target_id=data.get("target_id"),
            action=data.get("action"),
            resource=data.get("resource"),
            ip_address=data.get("ip_address"),
            user_agent=data.get("user_agent"),
            status=data.get("status"),
            details=data.get("details", {}),
            previous_hash=data.get("previous_hash"),
            event_hash=data.get("event_hash"),
        )


@dataclass
class AuditRetentionPolicy:
    """Defines an audit log retention policy."""

    name: str
    retention_days: int  # How many days to keep logs
    description: str = ""
    min_severity: AuditSeverity = AuditSeverity.LOW
    event_types: Optional[list[AuditEventType]] = None  # None = all
    archive_after_days: Optional[int] = None  # Move to archive after N days
    legal_hold: bool = False  # If true, never delete


@dataclass
class ComplianceReport:
    """Generated compliance report from audit data."""

    report_id: str
    generated_at: float
    period_start: float
    period_end: float
    total_events: int
    events_by_type: dict[str, int] = field(default_factory=dict)
    events_by_severity: dict[str, int] = field(default_factory=dict)
    high_severity_events: list[AuditEvent] = field(default_factory=list)
    failed_logins: int = 0
    permission_changes: int = 0
    security_alerts: int = 0
    suspicious_activities: int = 0
    summary: str = ""
    recommendations: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Audit Store Interface
# ---------------------------------------------------------------------------


class AuditStore(ABC):
    """Abstract base class for audit log storage backends."""

    @abstractmethod
    def append(self, event: AuditEvent) -> None:
        """Append an event to the audit log."""
        ...

    @abstractmethod
    def get_event(self, event_id: str) -> Optional[AuditEvent]:
        """Get a single event by ID."""
        ...

    @abstractmethod
    def query(
        self,
        event_type: Optional[AuditEventType] = None,
        severity: Optional[AuditSeverity] = None,
        actor_id: Optional[str] = None,
        target_id: Optional[str] = None,
        start_time: Optional[float] = None,
        end_time: Optional[float] = None,
        ip_address: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditEvent]:
        """Query audit events with filters."""
        ...

    @abstractmethod
    def count(
        self,
        event_type: Optional[AuditEventType] = None,
        severity: Optional[AuditSeverity] = None,
        start_time: Optional[float] = None,
        end_time: Optional[float] = None,
    ) -> int:
        """Count events matching filters."""
        ...

    @abstractmethod
    def get_latest_hash(self) -> Optional[str]:
        """Get the hash of the most recent event (for chain verification)."""
        ...

    @abstractmethod
    def get_events_in_range(
        self, start_time: float, end_time: float
    ) -> list[AuditEvent]:
        """Get all events within a time range."""
        ...

    @abstractmethod
    def cleanup(self, older_than: float) -> int:
        """Remove events older than the specified timestamp.

        Returns the count of removed events.
        """
        ...


class InMemoryAuditStore(AuditStore):
    """In-memory audit store for testing and development.

    NOT suitable for production - logs are lost on restart and not
    tamper-evident at the storage layer.
    """

    def __init__(self, max_events: int = 100000) -> None:
        self._events: dict[str, AuditEvent] = {}
        self._event_list: list[AuditEvent] = []
        self._max_events = max_events
        self._latest_hash: Optional[str] = None

    def append(self, event: AuditEvent) -> None:
        self._events[event.event_id] = event
        self._event_list.append(event)
        self._latest_hash = event.event_hash
        # Trim to max events
        if len(self._event_list) > self._max_events:
            removed = self._event_list.pop(0)
            self._events.pop(removed.event_id, None)

    def get_event(self, event_id: str) -> Optional[AuditEvent]:
        return self._events.get(event_id)

    def query(
        self,
        event_type: Optional[AuditEventType] = None,
        severity: Optional[AuditSeverity] = None,
        actor_id: Optional[str] = None,
        target_id: Optional[str] = None,
        start_time: Optional[float] = None,
        end_time: Optional[float] = None,
        ip_address: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditEvent]:
        results = []
        for event in self._event_list:
            if event_type and event.event_type != event_type:
                continue
            if severity and event.severity != severity:
                continue
            if actor_id and event.actor_id != actor_id:
                continue
            if target_id and event.target_id != target_id:
                continue
            if start_time and event.timestamp < start_time:
                continue
            if end_time and event.timestamp > end_time:
                continue
            if ip_address and event.ip_address != ip_address:
                continue
            if status and event.status != status:
                continue
            results.append(event)

        # Apply pagination (most recent first)
        results = results[::-1]  # Reverse for newest first
        return results[offset : offset + limit]

    def count(
        self,
        event_type: Optional[AuditEventType] = None,
        severity: Optional[AuditSeverity] = None,
        start_time: Optional[float] = None,
        end_time: Optional[float] = None,
    ) -> int:
        count = 0
        for event in self._event_list:
            if event_type and event.event_type != event_type:
                continue
            if severity and event.severity != severity:
                continue
            if start_time and event.timestamp < start_time:
                continue
            if end_time and event.timestamp > end_time:
                continue
            count += 1
        return count

    def get_latest_hash(self) -> Optional[str]:
        return self._latest_hash

    def get_events_in_range(
        self, start_time: float, end_time: float
    ) -> list[AuditEvent]:
        return [
            e
            for e in self._event_list
            if start_time <= e.timestamp <= end_time
        ]

    def cleanup(self, older_than: float) -> int:
        original_len = len(self._event_list)
        self._event_list = [e for e in self._event_list if e.timestamp >= older_than]
        removed = original_len - len(self._event_list)
        # Also clean up the dict
        for eid in list(self._events.keys()):
            if eid not in self._events:
                continue
            evt = self._events[eid]
            if evt.timestamp < older_than:
                del self._events[eid]
        return removed


# ---------------------------------------------------------------------------
# Tamper-Evident Hash Chain
# ---------------------------------------------------------------------------


class HashChainAuditor:
    """Implements tamper-evident audit logging using a hash chain.

    Each event's hash includes the previous event's hash, creating a
    chain where any tampering with historical events would break all
    subsequent hashes.

    Hash format: SHA-256 of (previous_hash + event_data)
    """

    def __init__(self, store: AuditStore, secret_key: Optional[bytes] = None) -> None:
        """Initialize hash chain auditor.

        Args:
            store: Audit log storage backend.
            secret_key: Optional HMAC key for additional integrity.
        """
        self._store = store
        self._secret_key = secret_key

    def compute_hash(self, event: AuditEvent, previous_hash: Optional[str]) -> str:
        """Compute the hash for an event.

        Includes the previous hash to form a chain.
        Uses HMAC-SHA256 if a secret key is provided, otherwise SHA-256.
        """
        # Create a canonical representation of the event
        event_data = {
            "event_id": event.event_id,
            "event_type": event.event_type.value,
            "severity": event.severity.value,
            "timestamp": event.timestamp,
            "actor_id": event.actor_id,
            "target_type": event.target_type,
            "target_id": event.target_id,
            "action": event.action,
            "status": event.status,
            "details": event.details,
        }
        canonical = json.dumps(event_data, sort_keys=True, default=str)

        if self._secret_key:
            # HMAC-SHA256 with previous hash as part of message
            message = (previous_hash or "") + canonical
            return hmac.new(self._secret_key, message.encode("utf-8"), hashlib.sha256).hexdigest()
        else:
            # Plain SHA-256 hash chain
            message = (previous_hash or "") + canonical
            return hashlib.sha256(message.encode("utf-8")).hexdigest()

    def record_event(self, event: AuditEvent) -> AuditEvent:
        """Record an event with hash chain integrity.

        Sets previous_hash and event_hash on the event before storing.
        """
        previous_hash = self._store.get_latest_hash()
        event.previous_hash = previous_hash
        event.event_hash = self.compute_hash(event, previous_hash)
        self._store.append(event)
        return event

    def verify_chain(
        self, start_time: Optional[float] = None, end_time: Optional[float] = None
    ) -> tuple[bool, int, list[str]]:
        """Verify the integrity of the audit log hash chain.

        Returns:
            Tuple of (is_valid, verified_count, errors)
        """
        events = self._store.get_events_in_range(
            start_time or 0, end_time or time.time()
        )
        if not events:
            return True, 0, []

        errors: list[str] = []
        previous_hash: Optional[str] = events[0].previous_hash

        for i, event in enumerate(events):
            expected_hash = self.compute_hash(event, previous_hash)
            if event.event_hash != expected_hash:
                errors.append(
                    f"Hash mismatch at event {event.event_id} (index {i}): "
                    f"expected {expected_hash}, got {event.event_hash}"
                )
                break
            previous_hash = event.event_hash

        is_valid = len(errors) == 0
        return is_valid, len(events), errors


# ---------------------------------------------------------------------------
# Suspicious Activity Detection
# ---------------------------------------------------------------------------


class SuspiciousActivityDetector:
    """Detects suspicious activity patterns in audit logs.

    Implements common security detection patterns:
    - Brute force login attempts
    - Unusual access times
    - Privilege escalation
    - Data exfiltration patterns
    - Account takeover indicators
    """

    def __init__(
        self,
        failed_login_threshold: int = 5,
        failed_login_window: int = 300,  # 5 minutes
        unusual_hours: Optional[tuple[int, int]] = None,
    ) -> None:
        self._failed_login_threshold = failed_login_threshold
        self._failed_login_window = failed_login_window
        self._unusual_hours = unusual_hours or (0, 5)  # Midnight to 5 AM

    def analyze_events(self, events: list[AuditEvent]) -> list[dict[str, Any]]:
        """Analyze audit events for suspicious activity patterns.

        Returns a list of detected alerts.
        """
        alerts: list[dict[str, Any]] = []

        alerts.extend(self._detect_brute_force(events))
        alerts.extend(self._detect_unusual_access_times(events))
        alerts.extend(self._detect_privilege_escalation(events))
        alerts.extend(self._detect_data_exfiltration(events))
        alerts.extend(self._detect_rapid_access(events))

        return alerts

    def _detect_brute_force(self, events: list[AuditEvent]) -> list[dict[str, Any]]:
        """Detect brute force login patterns."""
        alerts: list[dict[str, Any]] = []

        # Group failed logins by IP
        failed_by_ip: dict[str, list[AuditEvent]] = {}
        for event in events:
            if event.event_type == AuditEventType.LOGIN_FAILED and event.ip_address:
                if event.ip_address not in failed_by_ip:
                    failed_by_ip[event.ip_address] = []
                failed_by_ip[event.ip_address].append(event)

        for ip, failures in failed_by_ip.items():
            # Check within sliding window
            for i in range(len(failures)):
                window_start = failures[i].timestamp
                window_end = window_start + self._failed_login_window
                count = sum(
                    1 for f in failures if window_start <= f.timestamp <= window_end
                )
                if count >= self._failed_login_threshold:
                    alerts.append({
                        "type": "brute_force_login",
                        "severity": AuditSeverity.HIGH,
                        "ip_address": ip,
                        "failed_attempts": count,
                        "window_seconds": self._failed_login_window,
                        "first_attempt": window_start,
                        "description": f"Brute force login pattern detected from IP {ip}: {count} failures in {self._failed_login_window}s",
                    })
                    break  # One alert per IP is enough

        return alerts

    def _detect_unusual_access_times(self, events: list[AuditEvent]) -> list[dict[str, Any]]:
        """Detect access during unusual hours."""
        alerts: list[dict[str, Any]] = []
        start_hour, end_hour = self._unusual_hours

        unusual_events = []
        for event in events:
            if event.event_type not in (AuditEventType.LOGIN, AuditEventType.DATA_ACCESS):
                continue
            hour = time.localtime(event.timestamp).tm_hour
            if start_hour <= hour < end_hour:
                unusual_events.append(event)

        if unusual_events:
            by_actor: dict[str, list[AuditEvent]] = {}
            for e in unusual_events:
                if e.actor_id:
                    if e.actor_id not in by_actor:
                        by_actor[e.actor_id] = []
                    by_actor[e.actor_id].append(e)

            for actor_id, actor_events in by_actor.items():
                alerts.append({
                    "type": "unusual_access_time",
                    "severity": AuditSeverity.MEDIUM,
                    "actor_id": actor_id,
                    "event_count": len(actor_events),
                    "hours_range": f"{start_hour}:00-{end_hour}:00",
                    "description": f"User {actor_id} accessed the system during unusual hours ({len(actor_events)} events)",
                })

        return alerts

    def _detect_privilege_escalation(self, events: list[AuditEvent]) -> list[dict[str, Any]]:
        """Detect potential privilege escalation patterns."""
        alerts: list[dict[str, Any]] = []

        # Look for permission/role changes followed by sensitive access
        permission_changes = [
            e for e in events
            if e.event_type in (AuditEventType.PERMISSION_CHANGE, AuditEventType.ROLE_CHANGE)
        ]

        for change in permission_changes:
            # Check if there was elevated access shortly after
            post_change_time = change.timestamp
            elevated_access = [
                e for e in events
                if e.timestamp > post_change_time
                and e.timestamp < post_change_time + 3600  # Within 1 hour
                and e.actor_id == change.actor_id
                and e.event_type in (AuditEventType.DATA_EXPORT, AuditEventType.SYSTEM_ACCESS)
            ]
            if elevated_access:
                alerts.append({
                    "type": "potential_privilege_escalation",
                    "severity": AuditSeverity.HIGH,
                    "actor_id": change.actor_id,
                    "change_event_id": change.event_id,
                    "elevated_access_count": len(elevated_access),
                    "description": f"Potential privilege escalation: {change.actor_id} had {len(elevated_access)} elevated access events after permission change",
                })

        return alerts

    def _detect_data_exfiltration(self, events: list[AuditEvent]) -> list[dict[str, Any]]:
        """Detect potential data exfiltration patterns."""
        alerts: list[dict[str, Any]] = []

        # Multiple data exports in short time
        exports_by_actor: dict[str, list[AuditEvent]] = {}
        for event in events:
            if event.event_type == AuditEventType.DATA_EXPORT and event.actor_id:
                if event.actor_id not in exports_by_actor:
                    exports_by_actor[event.actor_id] = []
                exports_by_actor[event.actor_id].append(event)

        for actor_id, exports in exports_by_actor.items():
            if len(exports) >= 5:  # More than 5 exports
                # Check time window
                time_span = exports[-1].timestamp - exports[0].timestamp
                if time_span < 3600:  # Within 1 hour
                    alerts.append({
                        "type": "potential_data_exfiltration",
                        "severity": AuditSeverity.HIGH,
                        "actor_id": actor_id,
                        "export_count": len(exports),
                        "time_span_seconds": time_span,
                        "description": f"Potential data exfiltration: {actor_id} performed {len(exports)} exports in {time_span:.0f}s",
                    })

        return alerts

    def _detect_rapid_access(self, events: list[AuditEvent]) -> list[dict[str, Any]]:
        """Detect unusually rapid access patterns (possible automation)."""
        alerts: list[dict[str, Any]] = []

        access_by_actor: dict[str, list[AuditEvent]] = {}
        for event in events:
            if event.event_type == AuditEventType.DATA_ACCESS and event.actor_id:
                if event.actor_id not in access_by_actor:
                    access_by_actor[event.actor_id] = []
                access_by_actor[event.actor_id].append(event)

        for actor_id, accesses in access_by_actor.items():
            if len(accesses) < 20:
                continue
            # Check average interval
            accesses_sorted = sorted(accesses, key=lambda e: e.timestamp)
            intervals = [
                accesses_sorted[i + 1].timestamp - accesses_sorted[i].timestamp
                for i in range(len(accesses_sorted) - 1)
            ]
            avg_interval = sum(intervals) / len(intervals)
            if avg_interval < 0.5:  # Less than 500ms between requests
                alerts.append({
                    "type": "rapid_access_pattern",
                    "severity": AuditSeverity.MEDIUM,
                    "actor_id": actor_id,
                    "request_count": len(accesses),
                    "avg_interval_seconds": avg_interval,
                    "description": f"Unusually rapid access pattern from {actor_id}: avg {avg_interval:.3f}s between {len(accesses)} requests",
                })

        return alerts


# ---------------------------------------------------------------------------
# Audit Trail Manager
# ---------------------------------------------------------------------------


class AuditTrailManager:
    """Main audit trail management class.

    Coordinates event logging, hash chain integrity, retention,
    search/filtering, compliance reporting, and suspicious activity detection.
    """

    def __init__(
        self,
        store: Optional[AuditStore] = None,
        hash_chain: Optional[HashChainAuditor] = None,
        detector: Optional[SuspiciousActivityDetector] = None,
        retention_policy: Optional[AuditRetentionPolicy] = None,
    ) -> None:
        """Initialize audit trail manager.

        Args:
            store: Audit log storage backend.
            hash_chain: Hash chain auditor for tamper evidence.
            detector: Suspicious activity detector.
            retention_policy: Log retention policy.
        """
        self._store = store or InMemoryAuditStore()
        self._hash_chain = hash_chain or HashChainAuditor(self._store)
        self._detector = detector or SuspiciousActivityDetector()
        self._retention_policy = retention_policy or AuditRetentionPolicy(
            name="default",
            retention_days=365,
            description="Default 1-year retention policy",
        )
        self._retention_policies: list[AuditRetentionPolicy] = [self._retention_policy]

    # ---- Event Logging ----

    def log_event(
        self,
        event_type: AuditEventType,
        severity: AuditSeverity,
        actor_id: Optional[str] = None,
        actor_type: Optional[str] = None,
        target_type: Optional[str] = None,
        target_id: Optional[str] = None,
        action: Optional[str] = None,
        resource: Optional[str] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        status: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
    ) -> AuditEvent:
        """Log a security event to the audit trail.

        Args:
            event_type: Type of security event.
            severity: Event severity level.
            actor_id: ID of the entity performing the action.
            actor_type: Type of actor (user, system, api_key, etc.).
            target_type: Type of entity affected.
            target_id: ID of entity affected.
            action: Specific action taken.
            resource: Resource path or identifier.
            ip_address: Source IP address.
            user_agent: Client user agent.
            status: Outcome status (success, failure, etc.).
            details: Additional event details.

        Returns:
            The recorded AuditEvent.
        """
        event = AuditEvent(
            event_id=str(uuid.uuid4()),
            event_type=event_type,
            severity=severity,
            timestamp=time.time(),
            actor_id=actor_id,
            actor_type=actor_type,
            target_type=target_type,
            target_id=target_id,
            action=action,
            resource=resource,
            ip_address=ip_address,
            user_agent=user_agent,
            status=status,
            details=details or {},
        )

        return self._hash_chain.record_event(event)

    # ---- Convenience logging methods ----

    def log_login(
        self, user_id: str, success: bool, ip_address: Optional[str] = None, **kwargs: Any
    ) -> AuditEvent:
        """Log a login event."""
        if success:
            return self.log_event(
                AuditEventType.LOGIN,
                AuditSeverity.LOW,
                actor_id=user_id,
                actor_type="user",
                ip_address=ip_address,
                status="success",
                details=kwargs,
            )
        else:
            return self.log_event(
                AuditEventType.LOGIN_FAILED,
                AuditSeverity.MEDIUM,
                actor_id=user_id,
                actor_type="user",
                ip_address=ip_address,
                status="failure",
                details=kwargs,
            )

    def log_permission_change(
        self,
        actor_id: str,
        target_user_id: str,
        action: str,
        ip_address: Optional[str] = None,
        **kwargs: Any,
    ) -> AuditEvent:
        """Log a permission change event."""
        return self.log_event(
            AuditEventType.PERMISSION_CHANGE,
            AuditSeverity.HIGH,
            actor_id=actor_id,
            actor_type="user",
            target_type="user",
            target_id=target_user_id,
            action=action,
            ip_address=ip_address,
            status="success",
            details=kwargs,
        )

    def log_security_alert(
        self,
        alert_type: str,
        severity: AuditSeverity = AuditSeverity.HIGH,
        **kwargs: Any,
    ) -> AuditEvent:
        """Log a security alert."""
        return self.log_event(
            AuditEventType.SECURITY_ALERT,
            severity,
            action=alert_type,
            details=kwargs,
        )

    # ---- Search and Filtering ----

    def search(
        self,
        query: Optional[str] = None,
        event_type: Optional[AuditEventType] = None,
        severity: Optional[AuditSeverity] = None,
        actor_id: Optional[str] = None,
        target_id: Optional[str] = None,
        start_time: Optional[float] = None,
        end_time: Optional[float] = None,
        ip_address: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditEvent]:
        """Search audit events with filters.

        Args:
            query: Optional text search in details.
            event_type: Filter by event type.
            severity: Filter by severity.
            actor_id: Filter by actor.
            target_id: Filter by target.
            start_time: Start of time range.
            end_time: End of time range.
            ip_address: Filter by IP.
            limit: Maximum results.
            offset: Pagination offset.

        Returns:
            List of matching AuditEvents.
        """
        events = self._store.query(
            event_type=event_type,
            severity=severity,
            actor_id=actor_id,
            target_id=target_id,
            start_time=start_time,
            end_time=end_time,
            ip_address=ip_address,
            limit=limit,
            offset=offset,
        )

        if query:
            # Filter by text search in details
            query_lower = query.lower()
            events = [
                e for e in events
                if query_lower in json.dumps(e.details, default=str).lower()
            ]

        return events

    def get_event(self, event_id: str) -> Optional[AuditEvent]:
        """Get a specific event by ID."""
        return self._store.get_event(event_id)

    def count_events(
        self,
        event_type: Optional[AuditEventType] = None,
        severity: Optional[AuditSeverity] = None,
        start_time: Optional[float] = None,
        end_time: Optional[float] = None,
    ) -> int:
        """Count events matching filters."""
        return self._store.count(event_type, severity, start_time, end_time)

    # ---- Integrity Verification ----

    def verify_integrity(
        self, start_time: Optional[float] = None, end_time: Optional[float] = None
    ) -> tuple[bool, int, list[str]]:
        """Verify the integrity of the audit log hash chain.

        Returns:
            Tuple of (is_valid, verified_count, error_list)
        """
        return self._hash_chain.verify_chain(start_time, end_time)

    # ---- Retention Management ----

    def add_retention_policy(self, policy: AuditRetentionPolicy) -> None:
        """Add a retention policy."""
        self._retention_policies.append(policy)

    def apply_retention(self) -> int:
        """Apply retention policies, removing expired events.

        Returns the count of removed events.
        """
        now = time.time()
        # Find the longest retention period
        max_retention_days = max(p.retention_days for p in self._retention_policies)
        cutoff = now - (max_retention_days * 86400)
        return self._store.cleanup(cutoff)

    # ---- Compliance Reporting ----

    def generate_compliance_report(
        self,
        period_start: Optional[float] = None,
        period_end: Optional[float] = None,
    ) -> ComplianceReport:
        """Generate a compliance report from audit data.

        Args:
            period_start: Start of report period (default: 24 hours ago).
            period_end: End of report period (default: now).

        Returns:
            ComplianceReport with summary and statistics.
        """
        now = time.time()
        if period_end is None:
            period_end = now
        if period_start is None:
            period_start = now - 86400  # Last 24 hours

        events = self._store.get_events_in_range(period_start, period_end)

        # Count by type
        events_by_type: dict[str, int] = {}
        events_by_severity: dict[str, int] = {}
        failed_logins = 0
        permission_changes = 0
        security_alerts = 0
        suspicious_activities = 0
        high_severity_events: list[AuditEvent] = []

        for event in events:
            # By type
            type_name = event.event_type.value
            events_by_type[type_name] = events_by_type.get(type_name, 0) + 1

            # By severity
            sev_name = event.severity.value
            events_by_severity[sev_name] = events_by_severity.get(sev_name, 0) + 1

            # Specific counts
            if event.event_type == AuditEventType.LOGIN_FAILED:
                failed_logins += 1
            if event.event_type in (AuditEventType.PERMISSION_CHANGE, AuditEventType.ROLE_CHANGE):
                permission_changes += 1
            if event.event_type == AuditEventType.SECURITY_ALERT:
                security_alerts += 1
            if event.event_type == AuditEventType.SUSPICIOUS_ACTIVITY:
                suspicious_activities += 1
            if event.severity in (AuditSeverity.HIGH, AuditSeverity.CRITICAL):
                high_severity_events.append(event)

        # Generate recommendations
        recommendations: list[str] = []
        if failed_logins > 10:
            recommendations.append(
                f"High number of failed logins ({failed_logins}): review and consider "
                "strengthening rate limiting or account lockout policies"
            )
        if security_alerts > 0:
            recommendations.append(
                f"{security_alerts} security alerts recorded: investigate and remediate"
            )
        if permission_changes > 0:
            recommendations.append(
                f"{permission_changes} permission changes: review for appropriateness"
            )
        if suspicious_activities > 0:
            recommendations.append(
                f"{suspicious_activities} suspicious activities: conduct further investigation"
            )

        # Generate summary
        summary = (
            f"Audit compliance report for period "
            f"{time.ctime(period_start)} to {time.ctime(period_end)}. "
            f"Total events: {len(events)}. "
            f"Events by severity: {', '.join(f'{k}={v}' for k, v in events_by_severity.items())}. "
            f"Failed logins: {failed_logins}. "
            f"Permission changes: {permission_changes}. "
            f"Security alerts: {security_alerts}."
        )

        return ComplianceReport(
            report_id=str(uuid.uuid4()),
            generated_at=now,
            period_start=period_start,
            period_end=period_end,
            total_events=len(events),
            events_by_type=events_by_type,
            events_by_severity=events_by_severity,
            high_severity_events=high_severity_events[:50],  # Top 50
            failed_logins=failed_logins,
            permission_changes=permission_changes,
            security_alerts=security_alerts,
            suspicious_activities=suspicious_activities,
            summary=summary,
            recommendations=recommendations,
        )

    # ---- Suspicious Activity Detection ----

    def detect_suspicious_activity(
        self,
        start_time: Optional[float] = None,
        end_time: Optional[float] = None,
    ) -> list[dict[str, Any]]:
        """Run suspicious activity detection on audit events.

        Returns a list of detected alerts.
        """
        now = time.time()
        if end_time is None:
            end_time = now
        if start_time is None:
            start_time = now - 3600  # Last hour

        events = self._store.get_events_in_range(start_time, end_time)
        alerts = self._detector.analyze_events(events)

        # Log each alert as a security event
        for alert in alerts:
            self.log_security_alert(
                alert_type=alert["type"],
                severity=alert.get("severity", AuditSeverity.MEDIUM),
                details=alert,
            )

        return alerts
