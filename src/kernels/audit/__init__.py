"""Audit Kernel — Tamper-evident audit trail enforcement

The Audit Kernel provides tamper-evident audit logging with hash-chain integrity,
correlation-aware querying, and full event lifecycle management.

依据 Definition Lock §113: Audit Kernel 必须能够
- log() — log events with correlation IDs and scope
- verify_integrity() — verify audit log integrity with SHA256 hash chains
- query() — query audit entries by principal, scope, time range
- Immutable audit chain — no modification of past events
- Human sovereignty audit override logging
- Scope enforcement L0-L7 for audit entries
"""
from __future__ import annotations

import json
import hashlib
import time
import sqlite3
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
import uuid


class AuditEventType(str, Enum):
    """Types of audit events."""
    ACCESS_CHECK = "access_check"
    ROLE_GRANT = "role_grant"
    ROLE_REVOKE = "role_revoke"
    PERMISSION_CHECK = "permission_check"
    POLICY_EVAL = "policy_eval"
    ACCESS_DENIED = "access_denied"
    ACCESS_ALLOWED = "access_allowed"
    CONDITIONAL_ACCESS = "conditional_access"
    DEFERRED_ACCESS = "deferred_access"
    HUMAN_SOVEREIGNTY_OVERRIDE = "human_sovereignty_override"
    KERNEL_IMPLEMENTATION = "kernel_implementation"
    KERNEL_STATUS = "kernel_status"
    PHASE_GATE = "phase_gate"
    STATE_CHANGE = "state_change"


class AuditScope(str, Enum):
    """Audit scope levels L0-L7."""
    L0 = "L0"
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"
    L4 = "L4"
    L5 = "L5"
    L6 = "L6"
    L7 = "L7"


@dataclass
class AuditEvent:
    """Immutable audit event with hash-chain linkage."""
    event_id: str
    event_type: AuditEventType
    principal_id: str
    scope: AuditScope
    timestamp: float
    correlation_id: str
    outcome: str  # "allow", "deny", "conditional", "defer"
    details: Dict[str, Any] = field(default_factory=dict)
    event_hash: Optional[str] = None
    prev_event_hash: Optional[str] = None

    def compute_hash(self) -> str:
        """Compute SHA256 hash of this event's canonical form."""
        data = {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "principal_id": self.principal_id,
            "scope": self.scope.value,
            "timestamp": self.timestamp,
            "correlation_id": self.correlation_id,
            "outcome": self.outcome,
            "details": self.details,
        }
        # Sort keys for canonical form
        raw = json.dumps(data, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "principal_id": self.principal_id,
            "scope": self.scope.value,
            "timestamp": self.timestamp,
            "correlation_id": self.correlation_id,
            "outcome": self.outcome,
            "details": self.details,
            "event_hash": self.event_hash,
            "prev_event_hash": self.prev_event_hash,
        }


class AuditStore:
    """Persistent audit store with SQLite backend and hash-chain integrity."""

    def __init__(self, db_path: str = None):
        if db_path is None:
            # Default to D:\ drive per user constraint
            db_path = os.environ.get(
                "AUDIT_DB_PATH",
                "D:/LiuHao-AI-OS/audit_store.db"
            )
        self._db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        """Initialize the audit store database with hash-chain schema.

        The schema carries:
        - audit_events.seq: monotonic sequence number assigned at log
          time (immune to timestamp ties on coarse clocks)
        - chain_state: single-row anchor storing the last seq/hash so
          that truncation of the log tail is detectable
        Legacy databases without the seq column are migrated in place.
        """
        os.makedirs(os.path.dirname(os.path.abspath(self._db_path)), exist_ok=True)
        self._conn = sqlite3.connect(self._db_path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS audit_events (
                event_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                principal_id TEXT NOT NULL,
                scope TEXT NOT NULL,
                timestamp REAL NOT NULL,
                correlation_id TEXT NOT NULL,
                outcome TEXT NOT NULL,
                details TEXT,
                event_hash TEXT NOT NULL,
                prev_event_hash TEXT,
                seq INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))
            );
            CREATE INDEX IF NOT EXISTS idx_principal ON audit_events(principal_id);
            CREATE INDEX IF NOT EXISTS idx_scope ON audit_events(scope);
            CREATE INDEX IF NOT EXISTS idx_timestamp ON audit_events(timestamp);
            CREATE INDEX IF NOT EXISTS idx_correlation ON audit_events(correlation_id);
            CREATE TABLE IF NOT EXISTS chain_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                last_seq INTEGER NOT NULL,
                last_hash TEXT
            );
        """)
        # Migration for legacy databases created before the seq column
        columns = [row[1] for row in
                   self._conn.execute("PRAGMA table_info(audit_events)").fetchall()]
        if "seq" not in columns:
            self._conn.execute(
                "ALTER TABLE audit_events ADD COLUMN seq INTEGER NOT NULL DEFAULT 0"
            )
            # Backfill seq in insertion order for existing rows
            legacy_rows = self._conn.execute(
                "SELECT event_id FROM audit_events ORDER BY rowid"
            ).fetchall()
            for index, row in enumerate(legacy_rows, start=1):
                self._conn.execute(
                    "UPDATE audit_events SET seq = ? WHERE event_id = ?",
                    (index, row[0]),
                )
        # Ensure the seq index exists. This must run AFTER the seq column is
        # guaranteed to exist (either fresh schema or migrated legacy schema),
        # otherwise SQLite raises "no such column: seq".
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_seq ON audit_events(seq)"
        )
        # Seed the chain_state anchor for pre-existing data so integrity
        # verification covers it
        state = self._conn.execute(
            "SELECT last_seq FROM chain_state WHERE id = 1"
        ).fetchone()
        if state is None:
            last = self._conn.execute(
                "SELECT seq, event_hash FROM audit_events ORDER BY seq DESC LIMIT 1"
            ).fetchone()
            if last:
                self._conn.execute(
                    "INSERT INTO chain_state (id, last_seq, last_hash) VALUES (1, ?, ?)",
                    (last[0], last[1]),
                )
        self._conn.commit()

    def log_event(
        self,
        event_type: AuditEventType,
        principal_id: str,
        scope: AuditScope,
        outcome: str,
        details: Optional[Dict[str, Any]] = None,
        correlation_id: Optional[str] = None,
    ) -> AuditEvent:
        """Log an audit event with hash-chain linkage."""
        if correlation_id is None:
            correlation_id = str(uuid.uuid4())[:8]

        timestamp = time.time()

        # The chain_state anchor is the single source of truth for the
        # chain head: it assigns the next monotonic sequence number and
        # provides the previous event hash. Unlike timestamp ordering,
        # this is immune to clock granularity ties.
        state = self._conn.execute(
            "SELECT last_seq, last_hash FROM chain_state WHERE id = 1"
        ).fetchone()
        if state:
            last_seq, prev_hash = state[0], state[1]
        else:
            last_seq, prev_hash = 0, None
        seq = last_seq + 1

        event_id = str(uuid.uuid4())[:12]
        event = AuditEvent(
            event_id=event_id,
            event_type=event_type,
            principal_id=principal_id,
            scope=scope,
            timestamp=timestamp,
            correlation_id=correlation_id,
            outcome=outcome,
            details=details or {},
            prev_event_hash=prev_hash,
        )

        event.event_hash = event.compute_hash()

        self._conn.execute(
            """INSERT INTO audit_events
               (event_id, event_type, principal_id, scope, timestamp,
                correlation_id, outcome, details, event_hash, prev_event_hash, seq)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event.event_id,
                event.event_type.value,
                event.principal_id,
                event.scope.value,
                event.timestamp,
                event.correlation_id,
                event.outcome,
                json.dumps(event.details, sort_keys=True) if event.details else None,
                event.event_hash,
                event.prev_event_hash,
                seq,
            ),
        )
        self._conn.execute(
            """INSERT INTO chain_state (id, last_seq, last_hash) VALUES (1, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                   last_seq = excluded.last_seq,
                   last_hash = excluded.last_hash""",
            (seq, event.event_hash),
        )
        self._conn.commit()

        return event

    def verify_integrity(self) -> Tuple[bool, int]:
        """Verify the integrity of the audit event hash chain.

        Checks performed per Definition Lock section 113:
        1. Chain linkage: each event's prev_event_hash equals the
           previous event's event_hash (first event has none).
        2. Sequence continuity: seq numbers increment by exactly one.
        3. Content integrity: each event's stored hash is recomputed
           from its stored fields (outcome, details, ...) and compared,
           so tampering with content is detected.
        4. Tail integrity: the chain_state anchor (last seq/hash) must
           match the last stored event, so deleting the newest events
           is detected.

        Returns:
            (is_integrity_ok, total_events)
        """
        rows = self._conn.execute(
            "SELECT seq, event_id, event_type, principal_id, scope, timestamp, "
            "correlation_id, outcome, details, event_hash, prev_event_hash "
            "FROM audit_events ORDER BY seq ASC"
        ).fetchall()

        state = self._conn.execute(
            "SELECT last_seq, last_hash FROM chain_state WHERE id = 1"
        ).fetchone()

        if not rows:
            # Empty log: valid only if the anchor agrees that nothing was
            # ever logged (or points at zero events).
            if state is not None and state[0] > 0:
                return False, 0
            return True, 0

        total = len(rows)
        broken = 0

        for i, row in enumerate(rows):
            (seq, event_id, event_type, principal_id, scope, timestamp,
             correlation_id, outcome, details_json, event_hash,
             prev_event_hash) = row

            # 1. Chain linkage to the previous event
            if i == 0:
                # First event must have no previous
                if prev_event_hash is not None:
                    broken += 1
            else:
                prev_row = rows[i - 1]
                # Subsequent events must reference previous event's hash
                if prev_event_hash != prev_row[9]:
                    broken += 1
                # 2. Sequence numbers must be contiguous
                if seq != prev_row[0] + 1:
                    broken += 1

            # 3. Content integrity: recompute the canonical hash from
            # the stored fields and compare with the stored hash.
            try:
                details = json.loads(details_json) if details_json else {}
                reconstructed = AuditEvent(
                    event_id=event_id,
                    event_type=AuditEventType(event_type),
                    principal_id=principal_id,
                    scope=AuditScope(scope),
                    timestamp=timestamp,
                    correlation_id=correlation_id,
                    outcome=outcome,
                    details=details,
                )
                if reconstructed.compute_hash() != event_hash:
                    broken += 1
            except (ValueError, KeyError):
                # Corrupted enum value or malformed details JSON
                broken += 1

        # 4. Tail integrity: the anchor must match the last stored event
        if state is not None:
            if state[0] != rows[-1][0] or state[1] != rows[-1][9]:
                broken += 1

        is_ok = broken == 0
        return is_ok, total

    def query_events(
        self,
        principal_id: Optional[str] = None,
        scope: Optional[AuditScope] = None,
        start_time: Optional[float] = None,
        end_time: Optional[float] = None,
        outcome: Optional[str] = None,
        event_type: Optional[AuditEventType] = None,
        correlation_id: Optional[str] = None,
        limit: Optional[int] = None,
        reverse: bool = False,
    ) -> List[Dict[str, Any]]:
        """Query audit events with filter support.

        Events can be filtered by correlation_id for correlation-aware
        querying (Definition Lock section 113). Returns list of event
        dicts ordered by the monotonic sequence number (ascending by
        default, descending when reverse=True).
        """
        query = """SELECT event_id, event_type, principal_id, scope,
                   timestamp, correlation_id, outcome, details,
                   event_hash, prev_event_hash
                   FROM audit_events WHERE 1=1"""
        params = []

        if principal_id:
            query += " AND principal_id = ?"
            params.append(principal_id)

        if scope:
            query += " AND scope = ?"
            params.append(scope.value)

        if start_time is not None:
            query += " AND timestamp >= ?"
            params.append(start_time)

        if end_time is not None:
            query += " AND timestamp <= ?"
            params.append(end_time)

        if outcome:
            query += " AND outcome = ?"
            params.append(outcome)

        if event_type:
            query += " AND event_type = ?"
            params.append(event_type.value)

        if correlation_id:
            query += " AND correlation_id = ?"
            params.append(correlation_id)

        # Order by the monotonic sequence number so the result order is
        # deterministic even when timestamps tie.
        query += " ORDER BY seq DESC" if reverse else " ORDER BY seq ASC"

        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)

        cursor = self._conn.execute(query, params)
        results = []

        for row in cursor.fetchall():
            event_dict = {
                "event_id": row[0],
                "event_type": row[1],
                "principal_id": row[2],
                "scope": AuditScope(row[3]).value if isinstance(row[3], AuditScope) else row[3],
                "timestamp": row[4],
                "correlation_id": row[5],
                "outcome": row[6],
                "details": json.loads(row[7]) if row[7] else {},
                "event_hash": row[8],
                "prev_event_hash": row[9],
            }
            results.append(event_dict)

        return results

    def get_event(self, event_id: str) -> Optional[Dict[str, Any]]:
        """Get a single audit event by ID."""
        cursor = self._conn.execute(
            """SELECT event_id, event_type, principal_id, scope,
               timestamp, correlation_id, outcome, details,
               event_hash, prev_event_hash
               FROM audit_events WHERE event_id = ?""",
            (event_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None

        return {
            "event_id": row[0],
            "event_type": row[1],
            "principal_id": row[2],
            "scope": AuditScope(row[3]).value if isinstance(row[3], AuditScope) else row[3],
            "timestamp": row[4],
            "correlation_id": row[5],
            "outcome": row[6],
            "details": json.loads(row[7]) if row[7] else {},
            "event_hash": row[8],
            "prev_event_hash": row[9],
        }

    def get_stats(self) -> Dict[str, Any]:
        """Get audit store statistics."""
        cursor = self._conn.execute(
            "SELECT event_type, outcome, COUNT(*) as cnt "
            "FROM audit_events GROUP BY event_type, outcome"
        )
        breakdown = {}
        for row in cursor.fetchall():
            key = f"{row[0]}:{row[1]}"
            breakdown[key] = row[2]

        cursor = self._conn.execute("SELECT COUNT(*) FROM audit_events")
        total = cursor.fetchone()[0]

        return {
            "total_events": total,
            "breakdown": breakdown,
            "db_path": self._db_path,
        }


# Global audit store instance
_audit_store: Optional[AuditStore] = None


def get_audit_store() -> AuditStore:
    """Get or create the global audit store instance."""
    global _audit_store
    if _audit_store is None:
        _audit_store = AuditStore()
    return _audit_store


def log_event(
    event_type: AuditEventType,
    principal_id: str,
    scope: AuditScope,
    outcome: str,
    details: Optional[Dict[str, Any]] = None,
    correlation_id: Optional[str] = None,
) -> AuditEvent:
    """Log an audit event."""
    return get_audit_store().log_event(event_type, principal_id, scope, outcome, details, correlation_id)


def verify_audit_integrity() -> Tuple[bool, int]:
    """Verify audit log integrity."""
    return get_audit_store().verify_integrity()


def audit_query(
    principal_id: Optional[str] = None,
    scope: Optional[AuditScope] = None,
    start_time: Optional[float] = None,
    end_time: Optional[float] = None,
    outcome: Optional[str] = None,
    event_type: Optional[AuditEventType] = None,
    correlation_id: Optional[str] = None,
    limit: Optional[int] = None,
    reverse: bool = False,
) -> List[Dict[str, Any]]:
    """Query audit events."""
    return get_audit_store().query_events(
        principal_id=principal_id,
        scope=scope,
        start_time=start_time,
        end_time=end_time,
        outcome=outcome,
        event_type=event_type,
        correlation_id=correlation_id,
        limit=limit,
        reverse=reverse,
    )


def audit_get_event(event_id: str) -> Optional[Dict[str, Any]]:
    """Get a single audit event."""
    return get_audit_store().get_event(event_id)


def audit_stats() -> Dict[str, Any]:
    """Get audit store statistics."""
    return get_audit_store().get_stats()
