"""Audit Kernel — Full audit trail with hash chain integrity

The Audit Kernel provides complete audit logging for all security-relevant
events across every kernel in the system. Each entry is cryptographically
linked via a hash chain to ensure tamper evidence.

依据 Definition Lock §114: Audit Kernel 必须能够
- log(event_type, principal_id, permission, scope, result, reason)
- verify_integrity() → bool — verify complete hash chain
- query(filter_principal=None, filter_scope=None, filter_since=None)
- Export audit reports for compliance
- Correlation ID propagation across all kernel boundaries
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple
from enum import Enum
from dataclasses import dataclass, field
import hashlib
import json
import threading
import uuid


class AuditEventType(str, Enum):
    """Predefined audit event types matching kernel operations."""
    RBAC_CHECK = "rbac_check"
    ABAC_CHECK = "abac_check"
    ACCESS_DECISION = "access_decision"
    ROLE_GRANT = "role_grant"
    ROLE_REVOKE = "role_revoke"
    IDENTITY_CREATE = "identity_create"
    IDENTITY_GRANT = "identity_grant"
    IDENTITY_REVOKE = "identity_revoke"
    MEMORY_STORE = "memory_store"
    MEMORY_RECALL = "memory_recall"
    EXECUTION_PLAN = "execution_plan"
    EXECUTION_TRIGGER = "execution_trigger"
    RESOURCE_ALLOCATE = "resource_allocate"
    RESOURCE_RELEASE = "resource_release"
    POLICY_MANAGE = "policy_manage"
    EVALUATION_RUN = "evaluation_run"
    KERNEL_IMPLEMENT = "kernel_implement"
    KERNEL_STATUS_CHANGE = "kernel_status_change"
    SYSTEM_BOOT = "system_boot"
    SYSTEM_SHUTDOWN = "system_shutdown"


@dataclass
class AuditEntry:
    """Single audit log entry with hash chain linkage."""
    id: str
    timestamp: datetime
    event_type: AuditEventType
    principal_id: str
    permission: Optional[str]
    scope: str
    result: str
    reason: str
    correlation_id: str
    prev_hash: str = ""
    hash: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def compute_hash(self) -> str:
        """Compute the cryptographic hash for this entry, chaining to previous."""
        # Handle both AuditEventType enum and plain string event_type
        etype = self.event_type
        if isinstance(etype, Enum):
            et = etype.value
        elif isinstance(etype, str):
            et = etype
        else:
            et = str(etype)

        chain_data = {
            "id": self.id,
            "timestamp": self.timestamp.isoformat(),
            "event_type": et,
            "principal_id": self.principal_id,
            "permission": self.permission,
            "scope": self.scope,
            "result": self.result,
            "reason": self.reason,
            "correlation_id": self.correlation_id,
            "prev_hash": self.prev_hash,
        }
        chain_string = json.dumps(chain_data, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(chain_string.encode("utf-8")).hexdigest()

    def set_hash(self) -> None:
        """Set this entry's hash after computing it."""
        self.hash = self.compute_hash()


class AuditKernel:
    """Complete audit kernel with hash-chain integrity and query capabilities."""

    def __init__(self):
        self._entries: List[AuditEntry] = []
        self._lock = threading.RLock()

    def log(
        self,
        event_type: AuditEventType,
        principal_id: str,
        permission: Optional[str] = None,
        scope: str = "L1",
        result: str = "unknown",
        reason: str = "",
        correlation_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AuditEntry:
        """Log an audit event with full chain linkage."""
        with self._lock:
            if correlation_id is None:
                correlation_id = str(uuid.uuid4())[:8]

            # Compute prev_hash: if this is the first entry, use genesis; otherwise chain
            if not self._entries:
                prev_hash = "genesis_hash_0"
            else:
                prev_hash = self._entries[-1].hash

            entry = AuditEntry(
                id=str(uuid.uuid4())[:16],
                timestamp=datetime.now(timezone.utc),
                event_type=event_type,
                principal_id=principal_id,
                permission=permission,
                scope=scope,
                result=result,
                reason=reason or f"{event_type if isinstance(event_type, str) else event_type.value} event processed",
                correlation_id=correlation_id,
                prev_hash=prev_hash,
                metadata=metadata or {},
            )

            entry.set_hash()
            self._entries.append(entry)

            return entry

    def verify_integrity(self) -> Tuple[bool, Optional[str]]:
        """Verify complete hash chain integrity.

        Returns (is_valid, broken_entry_id) — if invalid, returns the first
        entry where the hash chain breaks.
        """
        with self._lock:
            if not self._entries:
                return True, None

            for i, entry in enumerate(self._entries):
                # Recompute hash and compare
                recomputed = entry.compute_hash()
                if entry.hash != recomputed:
                    return False, entry.id

                # Verify chain linkage: each entry's prev_hash must match
                # the previous entry's hash (except first entry)
                if i > 0:
                    expected_prev = self._entries[i - 1].hash
                    if entry.prev_hash != expected_prev:
                        return False, entry.id

            return True, None

    def query(
        self,
        filter_principal: Optional[str] = None,
        filter_scope: Optional[str] = None,
        filter_since: Optional[datetime] = None,
        filter_until: Optional[datetime] = None,
        event_types: Optional[List[AuditEventType]] = None,
    ) -> List[AuditEntry]:
        """Query audit entries with flexible filtering."""
        with self._lock:
            results = []

            for entry in self._entries:
                # Principal filter
                if filter_principal and entry.principal_id != filter_principal:
                    continue

                # Scope filter
                if filter_scope and entry.scope != filter_scope:
                    continue

                # Time since filter
                if filter_since and entry.timestamp < filter_since:
                    continue

                # Time until filter
                if filter_until and entry.timestamp > filter_until:
                    continue

                # Event type filter
                if event_types and entry.event_type not in event_types:
                    continue

                results.append(entry)

            # Return sorted by timestamp descending (newest first)
            return sorted(results, key=lambda e: e.timestamp, reverse=True)

    def get_correlation_path(self, correlation_id: str) -> List[AuditEntry]:
        """Get all entries sharing a correlation ID (cross-kernel trace)."""
        with self._lock:
            return [
                e for e in self._entries
                if e.correlation_id == correlation_id
            ]

    def stats(self) -> Dict[str, Any]:
        """Get audit kernel statistics."""
        with self._lock:
            total = len(self._entries)

            by_type: Dict[str, int] = {}
            by_result: Dict[str, int] = {}
            by_scope: Dict[str, int] = {}
            by_principal: Dict[str, int] = {}

            for entry in self._entries:
                et = entry.event_type.value
                by_type[et] = by_type.get(et, 0) + 1

                by_result[entry.result] = by_result.get(entry.result, 0) + 1

                by_scope[entry.scope] = by_scope.get(entry.scope, 0) + 1

                by_principal[entry.principal_id] = by_principal.get(entry.principal_id, 0) + 1

            # Chain integrity status
            is_valid, broken_id = self.verify_integrity()

            # Time range
            timestamps = [e.timestamp for e in self._entries]
            if timestamps:
                first_ts = min(timestamps)
                last_ts = max(timestamps)
            else:
                first_ts = last_ts = None

            return {
                "total_entries": total,
                "by_event_type": by_type,
                "by_result": by_result,
                "by_scope": by_scope,
                "by_principal": by_principal,
                "chain_integrity_valid": is_valid,
                "broken_chain_entry": broken_id,
                "first_entry_timestamp": first_ts.isoformat() if first_ts else None,
                "last_entry_timestamp": last_ts.isoformat() if last_ts else None,
                "date_range_days": (
                    (last_ts - first_ts).days
                    if first_ts and last_ts
                    else None
                ),
            }


# Global audit kernel instance
_global_audit_kernel: Optional[AuditKernel] = None


def get_audit_kernel() -> AuditKernel:
    """Get or create the global audit kernel instance."""
    global _global_audit_kernel
    if _global_audit_kernel is None:
        _global_audit_kernel = AuditKernel()
    return _global_audit_kernel


def audit_log(event_type, principal_id, permission=None, scope="L1", result="unknown",
              reason="", correlation_id=None, metadata=None):
    """Convenience function to log an audit event."""
    # Ensure event_type is AuditEventType enum for kernel compatibility
    if isinstance(event_type, str):
        try:
            event_type = AuditEventType(event_type)
        except ValueError:
            # Unknown event type - pass as string, kernel will handle
            pass
    # Call kernel's log method
    return get_audit_kernel().log(
        event_type=event_type,
        principal_id=principal_id,
        permission=permission,
        scope=scope,
        result=result,
        reason=reason,
        correlation_id=correlation_id,
        metadata=metadata,
    )


def audit_query(
    filter_principal=None,
    filter_scope=None,
    filter_since=None,
    filter_until=None,
    event_types=None,
):
    """Convenience function to query audit entries."""
    return get_audit_kernel().query(
        filter_principal=filter_principal,
        filter_scope=filter_scope,
        filter_since=filter_since,
        filter_until=filter_until,
        event_types=event_types,
    )


def audit_verify() -> Tuple[bool, Optional[str]]:
    """Convenience function to verify audit chain integrity."""
    return get_audit_kernel().verify_integrity()


def audit_stats() -> Dict[str, Any]:
    """Convenience function to get audit statistics."""
    return get_audit_kernel().stats()