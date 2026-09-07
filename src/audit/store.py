"""
Audit Event Store for LiuHao AI OS

Provides:
- Persistent storage of audit events using JSON
- Event querying and retrieval
- Hash chain integrity verification
"""

from pathlib import Path
import json
import hashlib
import time
import uuid
from typing import Dict, List, Optional, Any

from .models import AuditEvent


class AuditStore:
    """
    Audit event storage with integrity verification.

    Features:
    - JSON-based persistent storage
    - Hash chain for tamper evidence
    - Query and filter support
    - Automatic integrity verification
    """

    def __init__(self, storage_path: str = "data/audit/events.json"):
        self.storage_path = Path(storage_path)
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self._events: Dict[str, AuditEvent] = {}
        self._hash_chain: Optional[List[str]] = None
        self._load()

    def _load(self) -> None:
        """Load existing events from storage."""
        if self.storage_path.exists():
            try:
                with open(self.storage_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self._events = {
                    k: AuditEvent.from_dict(v) for k, v in data.get("events", {}).items()
                }
                self._hash_chain = data.get("hash_chain")
                # Rebuild hash chain if missing or inconsistent
                if not self._hash_chain or len(self._hash_chain) != len(self._events):
                    self._hash_chain = None
                    self._build_hash_chain()
            except Exception as e:
                print(f"Warning: Failed to load audit store: {e}")
                self._events = {}
                self._hash_chain = None

    def _build_hash_chain(self) -> None:
        """Build or rebuild the hash chain from current events."""
        chain: List[str] = []
        sorted_ids = sorted(self._events.keys())
        prev_hash = "genesis"
        for eid in sorted_ids:
            event = self._events[eid]
            event_dict = event.to_dict()
            # Add hash of previous event
            event_dict["prev_hash"] = prev_hash
            # Compute hash of this event (excluding prev_hash)
            event_data = json.dumps(event_dict, sort_keys=True, separators=(",", ":"))
            event_hash = hashlib.sha256(event_data.encode()).hexdigest()
            chain.append(event_hash)
            prev_hash = event_hash

        self._hash_chain = chain
        self._save()

    def _save(self) -> None:
        """Persist events to storage with hash chain."""
        # Ensure hash chain is built
        if self._hash_chain is None:
            self._build_hash_chain()

        data = {
            "version": 1,
            "saved_at": time.time(),
            "hash_chain": self._hash_chain,
            "events": {k: v.to_dict() for k, v in self._events.items()},
        }
        with open(self.storage_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)

    # ==================== Event Operations ====================

    def emit(self, event: AuditEvent) -> str:
        """
        Emit (store) an audit event.

        Args:
            event: The audit event to store

        Returns:
            The event ID
        """
        eid = event.event_id
        self._events[eid] = event
        self._save()
        return eid

    def emit_simple(self, event_type: str, source: str, **kwargs) -> str:
        """
        Simple event emission with automatic model construction.

        Args:
            event_type: Type of event
            source: Event source
            **kwargs: Additional fields for AuditEvent
                - user_id: Optional user ID
                - severity: Severity level (default: "medium")
                - status: Event status (default: "success")
                - message: Event message
                - details: Additional details dict
                - request_id: Request ID
                - trace_id: Trace ID

        Returns:
            The event ID
        """
        # Extract known kwargs, ignore unknown ones
        event_id = kwargs.pop('event_id', str(uuid.uuid4()))
        user_id = kwargs.get('user_id')
        severity = kwargs.get('severity', 'medium')
        status = kwargs.get('status', 'success')
        message = kwargs.get('message', '')
        details = kwargs.get('details', {})
        request_id = kwargs.get('request_id')
        trace_id = kwargs.get('trace_id')

        event = AuditEvent(
            event_id=event_id,
            event_type=event_type,
            timestamp=time.time(),
            source=source,
            user_id=user_id,
            severity=severity,
            status=status,
            message=message,
            details=details,
            request_id=request_id,
            trace_id=trace_id,
        )
        return self.emit(event)

    def get(self, event_id: str) -> Optional[AuditEvent]:
        """Get a single event by ID."""
        return self._events.get(event_id)

    def list(self, filters: Optional[Dict[str, Any]] = None) -> List[AuditEvent]:
        """
        List events with optional filters.

        Supported filter keys:
        - event_type
        - severity
        - status
        - source
        - user_id
        - start_time / end_time (timestamp range)
        """
        events = list(self._events.values())

        if not filters:
            return events

        result = []
        for event in events:
            match = True
            for key, value in filters.items():
                event_value = getattr(event, key, None)
                if event_value != value:
                    match = False
                    break
            if match:
                result.append(event)
        return result

    def filter_by_type(self, event_type: str) -> List[AuditEvent]:
        """Filter events by type."""
        return [e for e in self._events.values() if e.event_type == event_type]

    def filter_by_severity(self, severity: str) -> List[AuditEvent]:
        """Filter events by severity."""
        return [e for e in self._events.values() if e.severity == severity]

    def filter_by_status(self, status: str) -> List[AuditEvent]:
        """Filter events by status."""
        return [e for e in self._events.values() if e.status == status]

    def filter_by_user(self, user_id: str) -> List[AuditEvent]:
        """Filter events by user ID."""
        return [e for e in self._events.values() if e.user_id == user_id]

    def filter_by_time_range(self, start_time: float, end_time: float) -> List[AuditEvent]:
        """Filter events by timestamp range."""
        return [e for e in self._events.values() if start_time <= e.timestamp <= end_time]

    # ==================== Integrity ====================

    def verify_integrity(self) -> bool:
        """
        Verify the hash chain integrity of stored events.

        Returns:
            True if the hash chain is intact, False otherwise.
        """
        if not self._events or not self._hash_chain:
            return True  # Empty or not loaded yet is considered valid

        chain = self._hash_chain
        if len(chain) != len(self._events):
            return False

        sorted_ids = sorted(self._events.keys())
        prev_hash = "genesis"
        for i, eid in enumerate(sorted_ids):
            event = self._events[eid]
            event_dict = event.to_dict()
            event_dict["prev_hash"] = prev_hash
            event_data = json.dumps(event_dict, sort_keys=True, separators=(",", ":"))
            expected_hash = hashlib.sha256(event_data.encode()).hexdigest()

            if expected_hash != chain[i]:
                return False

            prev_hash = expected_hash

        return True

    # ==================== Statistics ====================

    def get_stats(self) -> Dict[str, Any]:
        """Get audit event statistics."""
        events = list(self._events.values())
        if not events:
            return {"total": 0, "by_type": {}, "by_severity": {}, "by_status": {}}

        by_type: Dict[str, int] = {}
        by_severity: Dict[str, int] = {}
        by_status: Dict[str, int] = {}

        for event in events:
            by_type[event.event_type] = by_type.get(event.event_type, 0) + 1
            by_severity[event.severity] = by_severity.get(event.severity, 0) + 1
            by_status[event.status] = by_status.get(event.status, 0) + 1

        return {
            "total": len(events),
            "by_type": by_type,
            "by_severity": by_severity,
            "by_status": by_status,
        }


# Module-level convenience functions
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
