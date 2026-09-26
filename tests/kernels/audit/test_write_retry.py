"""Transient-fault recovery in the audit write path.

These cover two defects that were found by MEASUREMENT, not by inspection --
both are intermittent, which is exactly why they need deterministic tests
instead of "run it a few times and hope":

1. SQLITE_READONLY ("attempt to write a readonly database", code 8).
   Several processes opening one SQLite database intermittently hit this while
   the WAL shared-memory file is created or torn down by a peer. Measured:
   6 concurrent processes x 12 rounds -> failures in 2-4 rounds; staggering
   the process starts -> 0/12. Retrying on the SAME connection does not
   recover (the handle is poisoned), so the fix re-opens it.

2. "UNIQUE constraint failed: chain_state.id" when seeding the chain anchor.
   Two processes both observe "no anchor yet" and both insert id=1.

The tests inject the fault directly, so they fail loudly if the recovery is
removed -- a timing-based version of test 1 would only fail by luck.
"""
import sqlite3

import pytest

from src.kernels import audit as audit_mod
from src.kernels.audit import AuditEvent, AuditEventType, AuditScope, AuditStore

SQLITE_BUSY = 5
SQLITE_READONLY = 8
SQLITE_ERROR = 1


class _FlakyConnection:
    """Wraps a real connection and fails the first N BEGIN statements.

    sqlite3.Connection attributes are read-only, so the fault is injected by
    wrapping the connection object rather than by patching a method on it.
    """

    def __init__(self, real, fail_code, fail_times):
        self._real = real
        self._fail_code = fail_code
        self._fail_times = fail_times
        self.begin_calls = 0

    def execute(self, sql, *args):
        if sql.strip().upper().startswith("BEGIN"):
            self.begin_calls += 1
            if self.begin_calls <= self._fail_times:
                exc = sqlite3.OperationalError(f"injected fault code={self._fail_code}")
                exc.sqlite_errorcode = self._fail_code
                raise exc
        return self._real.execute(sql, *args)

    def commit(self):
        return self._real.commit()

    def rollback(self):
        return self._real.rollback()

    def __getattr__(self, name):
        return getattr(self._real, name)


def _install_fault(store, fail_code, fail_times):
    proxy = _FlakyConnection(store._conn, fail_code, fail_times)
    store._conn = proxy
    return proxy


def _rows(db):
    conn = sqlite3.connect(db)
    seqs = [r[0] for r in conn.execute("SELECT seq FROM audit_events ORDER BY seq")]
    conn.close()
    return seqs


def test_readonly_fault_is_recovered_by_reopening_the_connection(tmp_path):
    db = str(tmp_path / "a.db")
    store = AuditStore(db_path=db)
    store.initialize()
    proxy = _install_fault(store, SQLITE_READONLY, fail_times=1)

    store.log_event(AuditEventType.STATE_CHANGE, "p", AuditScope.L0, "ok",
                    {"i": 0}, "c0")

    # The fault was injected once on the poisoned handle; the successful retry
    # ran on a NEW connection, which is why the proxy only ever saw one BEGIN.
    assert proxy.begin_calls == 1
    # The poisoned handle must have been replaced, not merely retried.
    assert store._conn is not proxy, "a SQLITE_READONLY fault must re-open"
    assert _rows(db) == [1]


def test_readonly_recovery_commits_exactly_one_event(tmp_path):
    """The retry must not duplicate evidence: one call, one event, seq 1."""
    db = str(tmp_path / "b.db")
    store = AuditStore(db_path=db)
    store.initialize()
    _install_fault(store, SQLITE_READONLY, fail_times=2)

    store.log_event(AuditEventType.STATE_CHANGE, "p", AuditScope.L0, "ok",
                    {"i": 0}, "c0")

    assert _rows(db) == [1], "a retry must never leave two events behind"
    ok, _ = store.verify_integrity()
    assert ok is True


def test_busy_is_retried_without_reopening(tmp_path):
    """SQLITE_BUSY is ordinary lock contention: wait, do not re-open."""
    db = str(tmp_path / "c.db")
    store = AuditStore(db_path=db)
    store.initialize()
    proxy = _install_fault(store, SQLITE_BUSY, fail_times=1)

    store.log_event(AuditEventType.STATE_CHANGE, "p", AuditScope.L0, "ok",
                    {"i": 0}, "c0")

    assert proxy.begin_calls == 2
    assert store._conn is proxy, "BUSY is contention, not a poisoned handle"
    assert _rows(db) == [1]


def test_a_non_retryable_failure_is_not_retried(tmp_path):
    """Only lock/readonly faults are retried -- a real error must propagate."""
    db = str(tmp_path / "d.db")
    store = AuditStore(db_path=db)
    store.initialize()
    proxy = _install_fault(store, SQLITE_ERROR, fail_times=99)

    with pytest.raises(sqlite3.OperationalError):
        store.log_event(AuditEventType.STATE_CHANGE, "p", AuditScope.L0, "ok",
                        {"i": 0}, "c0")

    assert proxy.begin_calls == 1, "a non-retryable code must not be retried"
    assert _rows(db) == []


def test_the_retry_budget_is_bounded(tmp_path, monkeypatch):
    """A permanently broken database must fail, not spin forever."""
    db = str(tmp_path / "e.db")
    store = AuditStore(db_path=db)
    store.initialize()
    # Keep the poisoned handle in place so every attempt fails.
    monkeypatch.setattr(store, "_reopen", lambda: None)
    proxy = _install_fault(store, SQLITE_READONLY, fail_times=9999)

    with pytest.raises(sqlite3.OperationalError):
        store.log_event(AuditEventType.STATE_CHANGE, "p", AuditScope.L0, "ok",
                        {"i": 0}, "c0")

    assert proxy.begin_calls == audit_mod._MAX_WRITE_ATTEMPTS
    assert _rows(db) == []


def test_batch_recovery_is_all_or_nothing(tmp_path):
    db = str(tmp_path / "f.db")
    store = AuditStore(db_path=db)
    store.initialize()
    _install_fault(store, SQLITE_READONLY, fail_times=1)

    events = [
        AuditEvent(event_id=f"b{i}", event_type=AuditEventType.STATE_CHANGE,
                   principal_id="p", scope=AuditScope.L0, timestamp=1.0 + i * 1e-6,
                   correlation_id=f"c{i}", outcome="ok", details={"i": i})
        for i in range(25)
    ]
    result = store.log_event_batch(events)

    assert len(result.appended) == 25
    assert _rows(db) == list(range(1, 26)), "the batch must not be split"
    ok, _ = store.verify_integrity()
    assert ok is True


def test_chain_anchor_insert_tolerates_a_racing_peer(tmp_path):
    """Regression for 'UNIQUE constraint failed: chain_state.id'.

    A plain INSERT here raises IntegrityError the moment a peer has already
    written id=1; INSERT OR IGNORE makes losing that race harmless.
    """
    db = str(tmp_path / "g.db")
    store = AuditStore(db_path=db)
    store.initialize()

    store._conn.execute("BEGIN IMMEDIATE")
    store._insert_chain_anchor(1, "hash-one")
    store._insert_chain_anchor(1, "hash-from-a-peer")   # would raise before
    store._conn.commit()

    row = store._conn.execute(
        "SELECT COUNT(*) FROM chain_state WHERE id = 1").fetchone()
    assert row[0] == 1
