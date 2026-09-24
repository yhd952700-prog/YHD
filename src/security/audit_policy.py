"""Audit Kernel — Full audit trail with hash chain integrity (VOLATILE / IN-MEMORY ONLY / NON-AUTHORITATIVE)

WARNING — VOLATILE, IN-MEMORY ONLY, NON-AUTHORITATIVE:
    The Audit Kernel (HC-10) holds entries in ``self._entries`` ONLY. There is
    NO persistence layer: all entries are lost on process restart, and the chain
    is NOT backed by the authoritative AuditStore (src.kernels.audit, HC-01).
    This module is therefore NOT the authoritative source of audit evidence.
    Durable, tamper-evident audit lives in src.kernels.audit (HC-01), whose
    runtime chain-of-custody integrity is currently UNVERIFIED. Do NOT treat
    this kernel's output as audit-grade evidence. The ``dropped_count`` and
    ``high_water_mark`` counters are telemetry, not evidence.

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

import collections
import json
import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from enum import Enum
from dataclasses import dataclass, field
from src.common.hash_chain import HASH_ALGORITHMS as _CHAIN_HASH_ALGORITHMS

logger = logging.getLogger(__name__)


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
    #: P0-8c: which algorithm produced ``hash``, so the entry says how to
    #: verify itself instead of relying on a hard-coded sha256. NOTE: it is not
    #: part of ``chain_data``, so adding it changed no existing hash.
    hash_alg: str = "sha256"
    metadata: Dict[str, Any] = field(default_factory=dict)
    #: D21 / HC-10 — canon-version LABEL / contract selector. It IS part of the
    #: canonical form (so a future version change is detectable), but it confers
    #: NO durability or authority on the chain: this kernel is volatile and
    #: non-authoritative (see module docstring).
    canon_version: str = "CURRENT"

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
            "metadata": self.metadata,
            "canon_version": self.canon_version,
        }
        chain_string = json.dumps(chain_data, sort_keys=True, separators=(",", ":"))
        # P0-8c: dispatch on the entry's DECLARED algorithm. An unknown
        # declaration fails closed (raises) -- never a silent sha256 fallback.
        fn = _CHAIN_HASH_ALGORITHMS.get(self.hash_alg)
        if fn is None:
            raise ValueError(
                f"unknown hash algorithm {self.hash_alg!r}; this build can "
                f"verify {sorted(_CHAIN_HASH_ALGORITHMS)}"
            )
        return fn(chain_string.encode("utf-8"))

    def set_hash(self) -> None:
        """Set this entry's hash after computing it."""
        self.hash = self.compute_hash()


class AuditKernel:
    """Audit kernel with hash-chain integrity and query capabilities.

    WARNING — VOLATILE / IN-MEMORY ONLY / NON-AUTHORITATIVE (HC-10):
        Entries live in ``self._entries`` only; there is NO persistence layer, so
        all entries are lost on restart and the chain is NOT backed by the
        authoritative AuditStore (src.kernels.audit, HC-01). This kernel is NOT
        the authoritative audit source. Durable, tamper-evident audit is
        src.kernels.audit (HC-01), whose runtime integrity is currently
        UNVERIFIED. Do not treat this kernel's output as audit-grade evidence.
        The ``dropped_count`` and ``high_water_mark`` counters are telemetry, not
        evidence.
    """

    def __init__(self, hwm_path: Optional[str] = None):
        # D19: bounded ring buffer -> old entries evicted (telemetry, not evidence).
        self._entries: collections.deque = collections.deque(maxlen=10000)
        self._lock = threading.RLock()
        self._dropped_count = 0
        # HWM telemetry file. None => disabled (default for direct construction;
        # the production singleton may set a real path). Counter-only, never a
        # trust input.
        self._hwm_path = hwm_path if hwm_path is not None else os.environ.get("AUDIT_HWM_PATH")
        self._entries_ever_written = self._load_hwm()

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

            # D19: ring eviction -> if full, the append drops the oldest entry.
            # Count it as dropped (telemetry) so loss is observable.
            if len(self._entries) == self._entries.maxlen:
                self._dropped_count += 1
            self._entries.append(entry)

            self._entries_ever_written += 1
            if self._hwm_path is not None:
                self._save_hwm()

            return entry

    # ------------------------------------------------------------------
    # D19: high-water-mark (HWM) telemetry (counter-only, fail-open).
    # ------------------------------------------------------------------
    def _load_hwm(self) -> int:
        """Load the monotonic ``entries_ever_written`` counter from disk.

        Fail-open: ANY error returns 0. The HWM file holds ONLY a counter and
        must NEVER influence a trust decision.
        """
        if not self._hwm_path:
            return 0
        try:
            with open(self._hwm_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                val = data.get("entries_ever_written")
                if isinstance(val, int) and val >= 0:
                    return val
            logger.warning(
                "HWM file %s has unexpected content; starting counter at 0",
                self._hwm_path,
            )
            return 0
        except FileNotFoundError:
            return 0
        except Exception as exc:  # noqa: BLE001 - fail-open on any HWM error
            logger.warning("Failed to load HWM file %s: %s", self._hwm_path, exc)
            return 0

    def _save_hwm(self) -> None:
        """Persist ``entries_ever_written`` (telemetry only).

        Called under ``self._lock``. Fail-open: ANY error is swallowed. The file
        holds ONLY a counter and is NOT audit evidence.
        """
        if not self._hwm_path:
            return
        with self._lock:
            try:
                payload = {"entries_ever_written": self._entries_ever_written}
                tmp = f"{self._hwm_path}.tmp"
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh)
                try:
                    os.chmod(tmp, 0o600)
                except OSError:
                    pass
                os.replace(tmp, self._hwm_path)
                try:
                    os.chmod(self._hwm_path, 0o600)
                except OSError:
                    pass
            except Exception as exc:  # noqa: BLE001 - fail-open on any HWM error
                logger.warning("Failed to save HWM file %s: %s", self._hwm_path, exc)

    @property
    def dropped_count(self) -> int:
        """Number of entries evicted from the ring buffer (telemetry, not evidence)."""
        return self._dropped_count

    @property
    def high_water_mark(self) -> int:
        """Monotonic count of entries ever written (survives restart via HWM file)."""
        return self._entries_ever_written

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
                try:
                    recomputed = entry.compute_hash()
                except ValueError:
                    # P0-8c: an entry declaring an algorithm this build cannot
                    # perform is UNVERIFIABLE -- fail closed, never assume sha256.
                    return False, entry.id
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
    """Get or create the global audit kernel instance.

    The production singleton is the ONLY construction that writes the HWM
    telemetry file: it resolves a real path from ``AUDIT_KERNEL_HWM_PATH`` or a
    default under the ``~/.liuhao`` data dir (distinct from the crypto logger's
    ``audit_hwm.json`` to avoid counter collision). Direct constructions keep
    ``hwm_path=None`` (file writing disabled) unless they opt in explicitly.
    """
    global _global_audit_kernel
    if _global_audit_kernel is None:
        hwm_path = os.environ.get("AUDIT_KERNEL_HWM_PATH")
        if not hwm_path:
            data_dir = os.path.join(os.path.expanduser("~"), ".liuhao")
            try:
                os.makedirs(data_dir, exist_ok=True)
            except OSError:
                data_dir = None
            hwm_path = os.path.join(data_dir, "audit_kernel_hwm.json") if data_dir else None
        _global_audit_kernel = AuditKernel(hwm_path=hwm_path)
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
    """Get audit kernel statistics."""
    return get_audit_kernel().stats()


def export_report(format: str = "json") -> str:
    """Export audit report in the specified format.

    Args:
        format: Output format, one of "json" or "csv" (default: "json")

    Returns:
        String containing the exported audit report

    Raises:
        ValueError: If format is not "json" or "csv"
    """
    ak = get_audit_kernel()
    entries = ak.query()

    if format == "json":
        report_data = []
        for entry in entries:
            report_data.append({
                "id": entry.id,
                "timestamp": entry.timestamp.isoformat() if entry.timestamp else None,
                "event_type": entry.event_type.value if hasattr(entry.event_type, 'value') else entry.event_type,
                "principal_id": entry.principal_id,
                "permission": entry.permission,
                "scope": entry.scope,
                "result": entry.result,
                "reason": entry.reason,
                "correlation_id": entry.correlation_id,
                "prev_hash": entry.prev_hash,
                "hash": entry.hash,
                "metadata": entry.metadata,
            })
        return json.dumps(report_data, indent=2, ensure_ascii=False)

    elif format == "csv":
        import csv
        import io
        output = io.StringIO()
        writer = csv.writer(output)

        # Header row
        writer.writerow([
            "id", "timestamp", "event_type", "principal_id",
            "permission", "scope", "result", "reason",
            "correlation_id", "prev_hash", "hash"
        ])

        # Data rows
        for entry in entries:
            writer.writerow([
                entry.id,
                entry.timestamp.isoformat() if entry.timestamp else "",
                entry.event_type.value if hasattr(entry.event_type, 'value') else entry.event_type,
                entry.principal_id,
                entry.permission or "",
                entry.scope,
                entry.result,
                entry.reason or "",
                entry.correlation_id,
                entry.prev_hash,
                entry.hash,
            ])
        return output.getvalue()

    else:
        raise ValueError(f"Unsupported export format: {format}. Use 'json' or 'csv'.")
