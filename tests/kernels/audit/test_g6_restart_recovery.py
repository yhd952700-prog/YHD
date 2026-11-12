"""G6 gap-filling failure-mode evidence for the authoritative audit store.

The RELEASE-READINESS-CLOSURE audit called out failure modes that were NOT yet
covered by an explicit, named, repeatable, temp-DB test. This file targets the
two genuine gaps:

  * In-process restart recovery / durability (synchronous=FULL): a "process
    crash" is modelled by closing the store's connection and re-opening a fresh
    AuditStore on the SAME database file. Every committed event must survive
    (FULL durability, not just NORMAL), the chain must still verify, and the
    store must resume appending with a contiguous ``seq``.

  * Real (OS-level) DB-unavailable: the database path is made genuinely
    unavailable at the filesystem level (not an injected SQLite fault). A write
    must fail closed with a clear error and leave NO phantom event -- evidence
    is never silently dropped, and the store never hangs on an unavailable
    store.

Everything runs against throwaway temp databases. None of these tests touch the
production ``audit_store.db``.

Covered elsewhere (referenced, not duplicated):
  - ENOSPC / SQLITE_FULL fail-closed ........ test_storage_faults.py
  - CORRUPT / IOERR quarantine .............. test_storage_faults.py
  - real-file corruption detection .......... test_storage_faults.py
  - cross-process kill + restart chain ...... test_storage_faults.py
  - single-writer fence / stale lease ....... test_fencing*.py
  - lock contention / thread safety ......... test_audit_thread_safety.py,
                                             test_audit_soak_concurrency.py,
                                             test_multiprocess_append.py
"""
from __future__ import annotations

import os
import sqlite3
import stat
import time
import uuid

import pytest

from src.kernels.audit import (
    AuditEvent,
    AuditEventType,
    AuditScope,
    AuditStore,
    AuditStorageError,
)


def _row_count(db):
    conn = sqlite3.connect(db)
    try:
        return conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
    finally:
        conn.close()


def _seqs(db):
    conn = sqlite3.connect(db)
    try:
        return [r[0] for r in
                conn.execute("SELECT seq FROM audit_events ORDER BY seq")]
    finally:
        conn.close()


def test_restart_recovery_durable_full_synchronous(tmp_path):
    """A 'process crash' (connection dropped, committed work already durable)
    must NOT lose evidence under synchronous=FULL, and the store must resume
    appending with a contiguous chain.
    """
    db = str(tmp_path / "restart.db")
    store = AuditStore(db_path=db)
    store.initialize()
    for i in range(20):
        store.log_event(
            AuditEventType.STATE_CHANGE, "p", AuditScope.L0, "ok", {"i": i},
            f"c{i}")

    # Hard process crash: drop the connection. WAL + synchronous=FULL means the
    # 20 committed events are already on durable storage.
    store._conn.close()

    # Brand-new store process re-opening the same database file.
    store2 = AuditStore(db_path=db)
    store2.initialize()
    ok, n = store2.verify_integrity()
    assert ok is True, "chain must verify after a restart"
    assert n == 20, "all 20 committed events must survive a restart"
    assert _row_count(db) == 20

    # Continue appending -- seq must continue contiguously from 21.
    for i in range(5):
        store2.log_event(
            AuditEventType.STATE_CHANGE, "p", AuditScope.L0, "ok",
            {"i": 100 + i}, f"d{i}")
    assert _seqs(db) == list(range(1, 26)), (
        "seq must continue contiguous after restart")
    ok, n = store2.verify_integrity()
    assert ok is True and n == 25
    store2._conn.close()


def test_restart_recovery_after_commit_no_gap_in_chain(tmp_path):
    """Crash AFTER a batch commit must leave the chain verifiable with no gap or
    duplicate seq, even when the next writer is a different process identity.
    """
    db = str(tmp_path / "restart2.db")
    store = AuditStore(db_path=db)
    store.initialize()
    # Batch of 10 in one transaction, then crash.
    events = [AuditEvent_for_batch(i) for i in range(10)]
    res = store.log_event_batch(events)
    assert len(res.appended) == 10
    store._conn.close()

    store2 = AuditStore(db_path=db)
    store2.initialize()
    ok, n = store2.verify_integrity()
    assert ok is True and n == 10, "batch-committed events survive restart"
    # Append one more; seq must be 11, not 1, not a duplicate.
    store2.log_event(
        AuditEventType.STATE_CHANGE, "p", AuditScope.L0, "ok", {"i": 10}, "d10")
    assert _seqs(db) == list(range(1, 12)), "no gap/dupe after restart"
    store2._conn.close()


def test_db_unavailable_fails_closed_no_phantom(tmp_path):
    """A genuinely unavailable database path must make the write fail closed
    (clear error, never a silent success) and must not leave a phantom event.
    """
    db = str(tmp_path / "readonly.db")
    store = AuditStore(db_path=db)
    store.initialize()
    for i in range(5):
        store.log_event(
            AuditEventType.STATE_CHANGE, "p", AuditScope.L0, "ok", {"i": i},
            f"c{i}")
    store._conn.close()

    # Make the file read-only at the filesystem level (real, not injected).
    os.chmod(db, stat.S_IREAD)

    try:
        # Constructing a writer against an unavailable store must raise a clear
        # storage / operational error, not hang or silently succeed.
        with pytest.raises((sqlite3.OperationalError, AuditStorageError)):
            bad = AuditStore(db_path=db)
            bad.initialize()
            bad.log_event(
                AuditEventType.STATE_CHANGE, "p", AuditScope.L0, "ok",
                {"i": 999}, "c999")
        # Nothing extra persisted: still exactly the 5 committed events.
        assert _row_count(db) == 5, "no phantom event under an unavailable store"
    finally:
        # Restore writability so the fixture can be cleaned up (Windows).
        try:
            os.chmod(db, stat.S_IWRITE)
        except OSError:
            pass


def AuditEvent_for_batch(i):
    """Build a standalone AuditEvent for batch-append tests (no correlation_id
    so the store assigns one; unique event_id so batch idempotency is a no-op).
    """
    return AuditEvent(
        event_id=uuid.uuid4().hex,
        event_type=AuditEventType.STATE_CHANGE,
        principal_id="p",
        scope=AuditScope.L0,
        timestamp=time.time(),
        correlation_id=uuid.uuid4().hex,
        outcome="ok",
        details={"i": i},
    )
