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
from src.kernels._base import KernelLifecycle, KernelStateError
from src.common.hash_chain import HASH_ALGORITHMS, DEFAULT_HASH_ALG
from .fencing import SqliteWriterLease, require_writer_lease

import json
import logging
import time
import sqlite3
import os
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
import uuid

logger = logging.getLogger("liuhao.kernel.audit")

# How long SQLite waits on the write lock before reporting SQLITE_BUSY.
# Raised from the driver default (5s) because initialisation now holds the
# write lock across the whole schema migration: with N processes starting at
# once, the losers must WAIT for the winner rather than fail.
_SQLITE_BUSY_TIMEOUT_SEC = 30.0

# --- transient-fault recovery (see AuditStore._write_with_retry) ------------
# SQLite primary result codes, per https://sqlite.org/rescode.html
_SQLITE_BUSY = 5
_SQLITE_LOCKED = 6
_SQLITE_READONLY = 8
_RETRYABLE_SQLITE_CODES = frozenset({_SQLITE_BUSY, _SQLITE_LOCKED, _SQLITE_READONLY})
_MAX_WRITE_ATTEMPTS = 8
_WRITE_RETRY_BASE_SEC = 0.02
_WRITE_RETRY_MAX_DELAY_SEC = 0.5

# Schema applied as individual statements (never executescript()) so it can
# run inside one BEGIN IMMEDIATE -- see the cold-start race note in _init_db.
_SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS audit_events (
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
        hash_alg TEXT NOT NULL DEFAULT 'sha256',
        created_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))
    )""",
    "CREATE INDEX IF NOT EXISTS idx_principal ON audit_events(principal_id)",
    "CREATE INDEX IF NOT EXISTS idx_scope ON audit_events(scope)",
    "CREATE INDEX IF NOT EXISTS idx_timestamp ON audit_events(timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_correlation ON audit_events(correlation_id)",
    """CREATE TABLE IF NOT EXISTS chain_state (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        last_seq INTEGER NOT NULL,
        last_hash TEXT
    )""",
)

# --- U39 / ADR "audit single-writer lease" Option B -------------------------
# The writer lease is now taken INSIDE the append's write transaction and
# yielded when that transaction commits (see ``_log_event_locked``). Contention
# is therefore absorbed by SQLite's write lock -- a competing process waits for
# the current append -- instead of being reported as "evidence channel
# unavailable", which the fail-closed gate turned into a denial of every
# governed action. A genuinely fenced writer is still refused immediately.


# --------------------------------------------------------------------------- #
# PHASE 3.6 / A5 — cryptographic algorithm identifiers
# --------------------------------------------------------------------------- #
#
# The chain has always used SHA-256, but never *said so*: ``compute_hash``
# called ``hashlib.sha256`` directly and nothing recorded which algorithm
# produced a given ``event_hash``. Two consequences:
#
#   * an auditor reading a five-year-old event cannot state what verifies it --
#     they can only infer it from the source revision that happened to write it;
#   * migrating to a stronger algorithm becomes a silent, all-or-nothing act,
#     because there is no per-event record to migrate against.
#
# The requirement is: "今天生成的证据，到未来仍然明确知道用什么算法验证".
# Each event therefore carries its algorithm name.
#
# The name is deliberately NOT folded into the hashed payload: that would change
# the hash domain and invalidate every event already written. It travels beside
# the hash, and verification dispatches on it. An event naming an algorithm this
# build does not implement **fails** verification (fail-closed) rather than being
# hashed with whatever the default happens to be -- "I cannot verify this" is
# the honest answer, and the wrong algorithm would only produce a wrong-but-green
# one.

# P0-8: this module now consumes the shared algorithm registry
# (``src.common.hash_chain``) instead of keeping a private copy, so the audit
# chain can never drift from the rest of the system's algorithm set and verifies
# fail-closed through the same dispatch used by HC-02..HC-08. The write path
# (``AuditEvent.compute_hash``) and the verify path (``_verify_integrity_locked``)
# both resolve the declared algorithm through this single source of truth.


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
class AuditBatchResult:
    """Outcome of one :meth:`AuditStore.log_event_batch` call.

    ``appended``   -- events that were actually committed, in batch order.
    ``duplicates`` -- event_ids that were already committed, so they were
                      skipped (retry-safe idempotency). They consumed no seq.

    A returned result means the batch COMMITTED: there is no partial batch.
    If the batch could not commit, the call raised instead.
    """

    appended: List["AuditEvent"]
    duplicates: List[str]


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
    #: Name of the algorithm that produced :attr:`event_hash` (PHASE 3.6 / A5).
    hash_alg: str = DEFAULT_HASH_ALG

    def compute_hash(self) -> str:
        """Compute this event's hash using the algorithm it declares.

        Raises :class:`ValueError` when the declared algorithm is not one this
        build implements. Callers that verify integrity treat that as a broken
        event -- see :meth:`AuditStore._verify_integrity_locked` -- so an
        unverifiable event can never be mistaken for a verified one.
        """
        algorithm = HASH_ALGORITHMS.get(self.hash_alg)
        if algorithm is None:
            raise ValueError(
                f"unknown hash algorithm {self.hash_alg!r}; this build can "
                f"verify {sorted(HASH_ALGORITHMS)}"
            )
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
        return algorithm(raw.encode())

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
            "hash_alg": self.hash_alg,
        }


class AuditStore:
    """Persistent audit store with SQLite backend and hash-chain integrity."""
    lifecycle: KernelLifecycle = KernelLifecycle.UNINITIALIZED

    def __init__(self, db_path: str = None):
        if db_path is None:
            # 默认落在项目根目录，而非写死某台机器的绝对路径（写死会在 CI
            # 工作区造出名为 "D:" 的目录，upload-artifact 因含冒号失败）。
            _project_root = os.path.dirname(
                os.path.dirname(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                )
            )
            db_path = os.environ.get(
                "AUDIT_DB_PATH",
                os.path.join(_project_root, "audit_store.db")
            )
        self._db_path = db_path
        self._writer_token: Optional[int] = None
        # Created before _init_db() and NEVER recreated: _write_with_retry()
        # can re-open the connection while this lock is held, and swapping the
        # lock object underneath a held lock would open a window in which a
        # second thread serialises against a different lock.
        self._lock = threading.RLock()
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
        # check_same_thread=False：审计是全局单例，而 FastAPI 的同步端点跑在
        # 线程池里 —— 连接会被多线程复用（此前缺此参数，导致
        # "SQLite objects created in a thread can only be used in that same thread"）。
        # 线程安全由 self._lock 保证（hash-chain 的 seq/prev_hash 必须串行推进）。
        self._conn = sqlite3.connect(
            self._db_path, timeout=_SQLITE_BUSY_TIMEOUT_SEC, check_same_thread=False
        )
        # NOTE: PRAGMA journal_mode cannot be changed from inside a
        # transaction, so it must stay above the BEGIN IMMEDIATE below.
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")

        # ---- cold-start race (measured, not guessed) ----------------------
        # When several processes open the SAME brand-new database at once, the
        # first write is also what materialises the -wal / -shm files. That
        # moment loses the race often enough to matter: 6 processes x 12
        # rounds produced "attempt to write a readonly database"
        # (SQLITE_READONLY) in 2-4 of the 12 rounds, while staggering the
        # process starts produced 0/12. Holding the write lock across the DDL
        # serialises it -- whoever arrives first creates the WAL files, the
        # rest wait on the busy handler instead of racing it.
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            # executescript() implicitly commits, so the schema is applied as
            # individual statements inside the transaction instead.
            for statement in _SCHEMA_STATEMENTS:
                self._conn.execute(statement)

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
            # PHASE 3.6 / A5: bring a legacy table forward. Every row that
            # predates the column was hashed with SHA-256, so the column
            # default is factually correct for them -- this only records what
            # was already true, it does not reinterpret any hash.
            if "hash_alg" not in columns:
                self._conn.execute(
                    "ALTER TABLE audit_events ADD COLUMN hash_alg TEXT NOT NULL "
                    "DEFAULT 'sha256'"
                )
            # Seed the chain_state anchor for pre-existing data so integrity
            # verification covers it.
            #
            # INSERT OR IGNORE (not a plain INSERT): this read-then-insert pair
            # runs on every process that opens the store, and it is NOT atomic
            # on its own -- two processes can both observe "no anchor yet" and
            # both try to insert id=1, which raises
            # "UNIQUE constraint failed: chain_state.id" (observed 1/12 rounds
            # with 6 concurrent processes). Losing that race is benign: the row
            # is already correct.
            state = self._conn.execute(
                "SELECT last_seq FROM chain_state WHERE id = 1"
            ).fetchone()
            if state is None:
                last = self._conn.execute(
                    "SELECT seq, event_hash FROM audit_events ORDER BY seq DESC LIMIT 1"
                ).fetchone()
                if last:
                    self._insert_chain_anchor(last[0], last[1])
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        # Q3.5 (fencing): the single-writer lease lives in the audit DB itself,
        # so the fence check and the append are atomic on the same connection.
        self._lease = SqliteWriterLease(self._conn)

    # ------------------------------------------------------------------ #
    # Q3.5 — single-writer fencing helpers
    # ------------------------------------------------------------------ #
    def _lease_owner(self) -> str:
        """Stable owner id for this store instance's writer lease."""
        return f"audit-store:{os.getpid()}"

    def _ensure_writer_lease(self) -> None:
        """Deprecated shim: the lease is now taken *inside* the append
        transaction (see ``_log_event_locked``). Kept only because it is part
        of the historical internal API; it intentionally does nothing.
        """

    def _insert_chain_anchor(self, last_seq: int, last_hash: Optional[str]) -> None:
        """Write the chain_state anchor, tolerating a peer that got there first.

        INSERT OR IGNORE (never a plain INSERT): this is the tail of a
        read-then-write pair that every process performs when it opens the
        store. Two processes can both read "no anchor yet" and both try to
        write id=1 -- observed once in 12 runs with 6 concurrent processes as
        "UNIQUE constraint failed: chain_state.id". Losing that race is
        harmless: the row a peer wrote is already correct.
        """
        self._conn.execute(
            "INSERT OR IGNORE INTO chain_state (id, last_seq, last_hash) "
            "VALUES (1, ?, ?)",
            (last_seq, last_hash),
        )

    def _reopen(self) -> None:
        """Drop the connection and establish a fresh one.

        Needed because a SQLITE_READONLY fault of the "cannot initialise the
        WAL shared-memory file" kind poisons the *connection*, not the
        statement: retrying on the same handle keeps failing (measured), while
        a new handle succeeds. Schema creation is idempotent, so re-running
        ``_init_db`` is safe -- this never rewrites existing evidence.
        """
        try:
            self._conn.close()
        except Exception:  # noqa: BLE001 - the handle may already be unusable
            pass
        self._writer_token = None
        self._init_db()

    def _write_with_retry(self, attempt_fn):
        """Run one write transaction, recovering from transient SQLite faults.

        Why this exists (measured, not guessed): with several processes opening
        the same database at once, SQLite intermittently answers
        SQLITE_READONLY ("attempt to write a readonly database", code 8) while
        the WAL shared-memory file is being created or torn down by a peer.
        Six concurrent processes hit it in 2-4 runs out of 12. It is NOT
        recoverable by retrying on the same connection -- the handle is
        poisoned -- so the retry re-opens it.

        Why retrying cannot duplicate evidence:
          * only BUSY / LOCKED / READONLY are retried, and SQLite raises all
            three while acquiring a lock or preparing a statement, i.e. before
            any row of this transaction has been applied;
          * the transaction is still rolled back first, so if it was open at
            all it is discarded whole;
          * therefore a retry re-runs a transaction that provably committed
            nothing -- it cannot create a duplicate event or burn a seq number.

        Any other failure (a data error, a disk error, a denied lease) still
        propagates on the first attempt, unchanged and fail-closed.
        """
        last: Optional[BaseException] = None
        for attempt in range(_MAX_WRITE_ATTEMPTS):
            try:
                return attempt_fn()
            except sqlite3.OperationalError as exc:
                code = getattr(exc, "sqlite_errorcode", None)
                if code not in _RETRYABLE_SQLITE_CODES:
                    raise
                last = exc
                try:
                    self._conn.rollback()
                except Exception:  # noqa: BLE001 - best-effort cleanup
                    pass
                self._writer_token = None
                if code == _SQLITE_READONLY:
                    try:
                        self._reopen()
                    except sqlite3.Error:
                        pass  # the next attempt will try to re-open again
                if attempt + 1 < _MAX_WRITE_ATTEMPTS:
                    time.sleep(
                        min(_WRITE_RETRY_BASE_SEC * (2 ** attempt),
                            _WRITE_RETRY_MAX_DELAY_SEC)
                    )
        raise last

    def log_event(
        self,
        event_type: AuditEventType,
        principal_id: str,
        scope: AuditScope,
        outcome: str,
        details: Optional[Dict[str, Any]] = None,
        correlation_id: Optional[str] = None,
    ) -> AuditEvent:
        """Log an audit event with hash-chain linkage.

        线程安全：审计 store 是全局单例，而 FastAPI 同步端点跑在线程池里，
        hash-chain 的 seq / prev_event_hash 必须串行推进，故整个写路径加锁。
        """
        with self._lock:
            return self._log_event_locked(
                event_type, principal_id, scope, outcome, details, correlation_id
            )

    def _log_event_locked(
        self,
        event_type: AuditEventType,
        principal_id: str,
        scope: AuditScope,
        outcome: str,
        details: Optional[Dict[str, Any]],
        correlation_id: Optional[str],
    ) -> AuditEvent:
        try:
            return self._write_with_retry(
                lambda: self._attempt_log_event(
                    event_type, principal_id, scope, outcome, details, correlation_id
                )
            )
        except Exception:
            # CRIT-1C / D17 (Layer 1): do NOT swallow silently. Record the
            # failure so operators can observe "Evidence=missing" via
            # audit_stats() / /v1/ready, then re-raise so the *caller's* own
            # policy (allow-and-swallow for LOW, or block for critical) decides.
            record_audit_failure()
            raise

    def _attempt_log_event(
        self,
        event_type: AuditEventType,
        principal_id: str,
        scope: AuditScope,
        outcome: str,
        details: Optional[Dict[str, Any]],
        correlation_id: Optional[str],
    ) -> AuditEvent:
        if correlation_id is None:
            correlation_id = str(uuid.uuid4())[:8]

        timestamp = time.time()

        # U39 / ADR Option B: ONE atomic transaction for the whole
        # read-modify-write. Previously the SELECT of chain_state ran OUTSIDE
        # any transaction (only the INSERTs were implicitly wrapped), so two
        # writers could read the same last_seq and both append it -- the HC-01
        # fork (duplicate seq / broken joins). The process-lifetime lease used
        # to paper over that; now the transaction is the real guarantee and the
        # lease is an additional, explicit fence on top of it.
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                # Q3.5 fencing, now genuinely atomic with the append: take the
                # lease INSIDE the write transaction. A competing process blocks
                # on SQLite's write lock instead of being told the evidence
                # channel is unavailable; a genuinely fenced writer (a newer live
                # owner) is still refused immediately.
                self._writer_token = self._lease.acquire_within(
                    owner=self._lease_owner()
                )
                require_writer_lease(self._lease, self._writer_token)

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
                        correlation_id, outcome, details, event_hash, prev_event_hash,
                        seq, hash_alg)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                        event.hash_alg,
                    ),
                )
                self._conn.execute(
                    """INSERT INTO chain_state (id, last_seq, last_hash) VALUES (1, ?, ?)
                       ON CONFLICT(id) DO UPDATE SET
                           last_seq = excluded.last_seq,
                           last_hash = excluded.last_hash""",
                    (seq, event.event_hash),
                )
                # Per-append lease (U39): yield it in the SAME transaction, so
                # the moment this append commits another process may take over.
                # Rolling back the append also rolls back the yield -- the two
                # can never disagree.
                self._lease.release_within(self._writer_token)
                self._conn.commit()
                self._writer_token = None
            except Exception:
                self._conn.rollback()
                self._writer_token = None
                raise
        except Exception:
            # Belt and braces: _write_with_retry() also rolls back before it
            # retries, but the connection must never be left mid-transaction
            # on the way out.
            try:
                self._conn.rollback()
            except Exception:  # noqa: BLE001 - the handle may already be dead
                pass
            self._writer_token = None
            raise

        return event

    def log_event_batch(self, events: List[AuditEvent]) -> "AuditBatchResult":
        """Append N events in ONE atomic transaction (Option C1).

        This exists because measurement showed the append ceiling is
        per-transaction cost, not hashing: single appends run ~2k/sec while a
        batch of 250 runs ~23k/sec (see ADR-audit-single-writer-lease.md §8).

        Semantics -- every one of these is deliberate and tested:

        * **Ordering** -- events are appended in list order; ``seq`` increases by
          exactly one per appended event, and ``prev_event_hash`` chains through
          the batch and onto the previously committed tail. Order within a batch
          is therefore the caller's list order, which is the only defensible
          definition for a scheduler-independent pipeline.
        * **Atomicity** -- all-or-nothing. "Committed" means every non-duplicate
          event in the batch is durable. A crash mid-batch rolls the whole batch
          back: never a partial batch, never a gap in ``seq``.
        * **Idempotency** -- a caller-supplied ``event_id`` that is already
          committed is skipped: it consumes no seq and creates no duplicate.
          This is what makes retry-after-uncertain-outcome safe, and it is the
          reason a batched pipeline does not silently duplicate evidence.
        * **Fencing** -- the lease is taken once per batch, inside the same
          transaction, so a fenced writer is still refused.
        * **Fail-closed** -- if the batch cannot commit the error propagates and
          the mandatory-evidence gate denies the governed action.

        Returns :class:`AuditBatchResult`.
        """
        if not events:
            return AuditBatchResult(appended=[], duplicates=[])

        with self._lock:
            return self._log_event_batch_locked(events)

    def _log_event_batch_locked(self, events: List[AuditEvent]) -> "AuditBatchResult":
        try:
            return self._write_with_retry(
                lambda: self._attempt_log_event_batch(events)
            )
        except Exception:
            # Same fail-closed contract as the single append (CRIT-1C / D17).
            record_audit_failure()
            raise

    def _attempt_log_event_batch(self, events: List[AuditEvent]) -> "AuditBatchResult":
        conn = self._conn
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                self._writer_token = self._lease.acquire_within(
                    owner=self._lease_owner()
                )
                require_writer_lease(self._lease, self._writer_token)

                state = conn.execute(
                    "SELECT last_seq, last_hash FROM chain_state WHERE id = 1"
                ).fetchone()
                last_seq, prev_hash = (state[0], state[1]) if state else (0, None)

                # Idempotency: which of these event_ids are already committed?
                existing: set = set()
                ids = [e.event_id for e in events if e.event_id]
                for start in range(0, len(ids), 500):
                    chunk = ids[start:start + 500]
                    placeholders = ",".join("?" * len(chunk))
                    rows = conn.execute(
                        f"SELECT event_id FROM audit_events "
                        f"WHERE event_id IN ({placeholders})",
                        chunk,
                    ).fetchall()
                    existing.update(row[0] for row in rows)

                appended: List[AuditEvent] = []
                duplicates: List[str] = []
                seen: set = set()

                for event in events:
                    if not event.event_id:
                        event.event_id = str(uuid.uuid4())[:12]
                    if event.event_id in existing or event.event_id in seen:
                        duplicates.append(event.event_id)
                        continue
                    seen.add(event.event_id)

                    last_seq += 1
                    event.prev_event_hash = prev_hash
                    event.event_hash = event.compute_hash()

                    conn.execute(
                        """INSERT INTO audit_events
                           (event_id, event_type, principal_id, scope, timestamp,
                            correlation_id, outcome, details, event_hash,
                            prev_event_hash, seq, hash_alg)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            event.event_id,
                            event.event_type.value,
                            event.principal_id,
                            event.scope.value,
                            event.timestamp,
                            event.correlation_id,
                            event.outcome,
                            json.dumps(event.details, sort_keys=True)
                            if event.details else None,
                            event.event_hash,
                            event.prev_event_hash,
                            last_seq,
                            event.hash_alg,
                        ),
                    )
                    prev_hash = event.event_hash
                    appended.append(event)

                if appended:
                    conn.execute(
                        """INSERT INTO chain_state (id, last_seq, last_hash)
                           VALUES (1, ?, ?)
                           ON CONFLICT(id) DO UPDATE SET
                               last_seq = excluded.last_seq,
                               last_hash = excluded.last_hash""",
                        (last_seq, prev_hash),
                    )
                self._lease.release_within(self._writer_token)
                conn.commit()
                self._writer_token = None
            except Exception:
                conn.rollback()
                self._writer_token = None
                raise
        except Exception:
            # Same belt-and-braces guarantee as the single-append path.
            try:
                conn.rollback()
            except Exception:  # noqa: BLE001 - the handle may already be dead
                pass
            self._writer_token = None
            raise

        return AuditBatchResult(appended=appended, duplicates=duplicates)

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
        # 与写路径同一把锁：校验期间不允许并发写入，避免读到链中间态。
        with self._lock:
            return self._verify_integrity_locked()

    def _verify_integrity_locked(self) -> Tuple[bool, int]:
        rows = self._conn.execute(
            "SELECT seq, event_id, event_type, principal_id, scope, timestamp, "
            "correlation_id, outcome, details, event_hash, prev_event_hash, "
            "hash_alg FROM audit_events ORDER BY seq ASC"
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
             prev_event_hash, row_hash_alg) = row

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
            # P0-8c: NO default fallback. A row that declares no algorithm is
            # UNVERIFIED -- it is counted broken rather than silently being
            # read as the default algorithm.
            declared_alg = row_hash_alg
            if not declared_alg:
                logger.error(
                    "audit event %s carries no hash_alg declaration; this row "
                    "is UNVERIFIED and is counted as broken rather than "
                    "assuming %s", event_id, DEFAULT_HASH_ALG,
                )
                broken += 1
                continue
            if declared_alg not in HASH_ALGORITHMS:
                # An event whose algorithm this build cannot perform is not
                # "probably fine" -- it is unverifiable, and unverifiable must
                # never be read as verified (PHASE 3.6 / A5, fail-closed).
                logger.error(
                    "audit event %s declares hash algorithm %r, which this "
                    "build cannot verify; counting it as broken rather than "
                    "assuming a default algorithm", event_id, declared_alg,
                )
                broken += 1
                continue
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
                    hash_alg=declared_alg,
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
        # 与写路径同一把锁：共享的 sqlite3 连接不并发使用。
        with self._lock:
            return self._query_events_locked(
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

    def _query_events_locked(
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
        query = """SELECT event_id, event_type, principal_id, scope,
                   timestamp, correlation_id, outcome, details,
                   event_hash, prev_event_hash, hash_alg
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
                # A5: the algorithm that produced event_hash. Without it on the
                # *read* surface the field would be write-only, and "what was
                # this verified with" would be unanswerable from the record --
                # which is the whole point of storing it.
                # P0-8c: report the declared algorithm verbatim. An absent
                # declaration must surface as None (UNVERIFIED), never as an
                # assumed default -- a reader must be able to tell the
                # difference between "sha256" and "nothing was declared".
                "hash_alg": row[10] if len(row) > 10 else None,
            }
            results.append(event_dict)

        return results

    def get_event(self, event_id: str) -> Optional[Dict[str, Any]]:
        """Get a single audit event by ID."""
        cursor = self._conn.execute(
            """SELECT event_id, event_type, principal_id, scope,
               timestamp, correlation_id, outcome, details,
               event_hash, prev_event_hash, hash_alg
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
            "hash_alg": row[10] if len(row) > 10 else DEFAULT_HASH_ALG,
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
            "failures": get_audit_failure_count(),
        }

    def initialize(self) -> None:
        self.lifecycle = KernelLifecycle.READY

    def shutdown(self) -> None:
        self.lifecycle = KernelLifecycle.STOPPED

    def pause(self) -> None:
        if self.lifecycle not in (KernelLifecycle.READY, KernelLifecycle.UNINITIALIZED):
            raise KernelStateError(f"cannot pause from {self.lifecycle}")
        self.lifecycle = KernelLifecycle.PAUSED

    def resume(self) -> None:
        if self.lifecycle is not KernelLifecycle.PAUSED:
            raise KernelStateError(f"cannot resume from {self.lifecycle}")
        self.lifecycle = KernelLifecycle.READY


# Global audit store instance
_audit_store: Optional[AuditStore] = None

# CRIT-1C (PHASE 3.6 / D17): audit write failures must not be silently swallowed.
# Central failure counter -- incremented whenever an audit write to the
# authoritative store raises, so operators can observe "Evidence=missing"
# instead of a green dashboard. This is Layer 1 of the two-layer fix; it makes
# the audit Evidence layer *observable* WITHOUT changing availability semantics
# (LOW tier is still allowed to keep running, but the failure is no longer
# silent). Layer 2 (blocking for critical/sovereignty-sensitive actions) is a
# separate, deliberate decision owned by the human (see H-D17 :163).
_audit_failure_total = 0
_audit_failure_lock = threading.Lock()
_audit_failure_ever = False


def record_audit_failure() -> None:
    """Increment the global audit-failure counter (CRIT-1C / D17 LOW tier signal)."""
    global _audit_failure_total, _audit_failure_ever
    with _audit_failure_lock:
        _audit_failure_total += 1
        _audit_failure_ever = True


def get_audit_failure_count() -> int:
    """Return the number of audit writes that failed since process start."""
    return _audit_failure_total


def audit_failure_occurred() -> bool:
    """True once any audit write has failed this process (monotonic)."""
    return _audit_failure_ever


def get_audit_store() -> AuditStore:
    """Get or create the global audit store instance."""
    global _audit_store
    if _audit_store is None:
        _audit_store = AuditStore()
        _audit_store.initialize()  # 存在即 READY：构造完成即视为就绪
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


def audit_failure_count() -> int:
    """Return the number of audit writes that failed since process start.

    CRIT-1C / D17 (Layer 1): this is the observable "Evidence=missing" signal.
    It is monotonically increasing for the life of the process and is surfaced
    via audit_stats() and the /v1/ready health endpoint.
    """
    return get_audit_failure_count()
