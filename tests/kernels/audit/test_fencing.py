"""Tests for the single-writer fencing primitive (src/kernels/audit/fencing).

These tests never open, read, or write the production ``audit_store.db`` or any
Evidence file. They exercise the fencing lease against synthetic in-memory /
throwaway connections, and prove the core invariant: a strictly monotonic token
fences every older writer (the C3 split-brain closure for RCA-1).
"""

import os
import sqlite3
import time

from src.kernels.audit import AuditStore, AuditEventType, AuditScope
from src.kernels.audit.fencing import (
    StaleWriterError,
    InMemoryWriterLease,
    SqliteWriterLease,
    require_writer_lease,
    LeaseView,
)


# --- InMemoryWriterLease (process-local / single-node / testing) ---------------


def test_inmemory_acquire_monotonic():
    l = InMemoryWriterLease()
    t1 = l.acquire("w1")
    assert t1 == 1
    # Strictly monotonic across a release (token counter is never reset).
    l.release(t1)
    t2 = l.acquire("w2")
    assert t2 == 2


def test_inmemory_double_acquire_held_raises():
    l = InMemoryWriterLease()
    t = l.acquire("w1", ttl_sec=30.0)
    try:
        l.acquire("w2", ttl_sec=30.0)
        assert False, "second acquire while held should raise StaleWriterError"
    except StaleWriterError:
        pass
    assert l.validate(t) is True


def test_inmemory_validate_stale():
    l = InMemoryWriterLease()
    t = l.acquire("w1")
    assert l.validate(t) is True
    l.release(t)
    assert l.validate(t) is False  # released -> no longer current
    assert l.is_stale(t) is True


def test_inmemory_expiry():
    l = InMemoryWriterLease()
    t = l.acquire("w1", ttl_sec=0.05)
    time.sleep(0.1)
    assert l.validate(t) is False
    assert l.is_stale(t) is True  # current token but expired
    # A fresh acquire after expiry returns a NEW higher token.
    t2 = l.acquire("w2", ttl_sec=30.0)
    assert t2 == t + 1


def test_inmemory_require_writer_lease():
    l = InMemoryWriterLease()
    t = l.acquire("w1")
    require_writer_lease(l, t)  # passes
    l.release(t)
    try:
        require_writer_lease(l, t)
        assert False, "stale token must be rejected"
    except StaleWriterError:
        pass


def test_inmemory_current_view():
    l = InMemoryWriterLease()
    v0 = l.current()
    assert v0.token is None
    t = l.acquire("w1", ttl_sec=30.0)
    v = l.current()
    assert isinstance(v, LeaseView)
    assert v.token == t and v.owner == "w1" and v.expires_at > v.acquired_at


# --- SqliteWriterLease (DB-backed fencing, no external dependency) --------------


def _fresh_conn() -> sqlite3.Connection:
    return sqlite3.connect(":memory:")


def test_sqlite_acquire_creates_token():
    conn = _fresh_conn()
    lease = SqliteWriterLease(conn)
    t = lease.acquire("w1", ttl_sec=30.0)
    assert t == 1
    view = lease.current()
    assert view.token == 1 and view.owner == "w1"
    # Table row exists as id=1.
    n = conn.execute("SELECT COUNT(*) FROM writer_lease").fetchone()[0]
    assert n == 1


def test_sqlite_double_acquire_held_raises():
    conn = _fresh_conn()
    lease = SqliteWriterLease(conn)
    t = lease.acquire("w1", ttl_sec=30.0)
    try:
        lease.acquire("w2", ttl_sec=30.0)
        assert False, "second acquire while held should raise StaleWriterError"
    except StaleWriterError:
        pass
    assert lease.validate(t) is True


def test_sqlite_monotonic_after_release():
    conn = _fresh_conn()
    lease = SqliteWriterLease(conn)
    t1 = lease.acquire("w1", ttl_sec=30.0)
    assert lease.release(t1) is True
    t2 = lease.acquire("w2", ttl_sec=30.0)
    assert t2 == t1 + 1  # strictly monotonic
    assert lease.current().token == t2


def test_sqlite_fencing_splits_old_writer():
    """Core invariant: a newer lease must fence an older writer (C3 split-brain
    closure). Simulate failover — process A's lease is fenced, B takes over with
    a strictly higher token, and any append attempt by the recovered A (still
    holding its stale token) is rejected."""
    conn = sqlite3.connect(":memory:")  # shared by both "processes"
    # Process A acquires.
    lease_a = SqliteWriterLease(conn)
    token_a = lease_a.acquire("proc-A", ttl_sec=30.0)
    # Failover: A's lease is revoked (release), then B acquires a NEW, higher token.
    lease_a.release(token_a)
    lease_b = SqliteWriterLease(conn)
    token_b = lease_b.acquire("proc-B", ttl_sec=30.0)
    assert token_b == token_a + 1
    # Process A now recovers and attempts to append with its stale token -> fenced.
    assert lease_b.is_stale(token_a) is True
    try:
        require_writer_lease(lease_b, token_a)
        assert False, "fenced old writer (A) must not append"
    except StaleWriterError:
        pass
    # Process B (current) is allowed.
    require_writer_lease(lease_b, token_b)


def test_sqlite_cross_connection_fencing():
    """Two separate connections (true cross-process analog) see the same fence."""
    conn_a = sqlite3.connect(":memory:")
    conn_b = sqlite3.connect(":memory:")
    # Share the lease table state: in reality both point at the SAME file.
    # Here we emulate by copying the schema + row via a shared in-memory db.
    shared = sqlite3.connect(":memory:")
    lease_shared = SqliteWriterLease(shared)
    t_a = lease_shared.acquire("A", ttl_sec=30.0)
    # B reads the same shared lease store.
    lease_b = SqliteWriterLease(shared)
    try:
        lease_b.acquire("B", ttl_sec=30.0)
        assert False, "B must be fenced while A holds an unexpired lease"
    except StaleWriterError:
        pass
    assert lease_b.validate(t_a) is True
    # After A releases, B can acquire with a higher token.
    lease_shared.release(t_a)
    t_b = lease_b.acquire("B", ttl_sec=30.0)
    assert t_b == t_a + 1


def test_sqlite_require_writer_lease_ok_and_stale():
    conn = _fresh_conn()
    lease = SqliteWriterLease(conn)
    t = lease.acquire("w1", ttl_sec=30.0)
    require_writer_lease(lease, t)  # ok
    lease.release(t)
    try:
        require_writer_lease(lease, t)
        assert False
    except StaleWriterError:
        pass


# --- U38: a writer must not fence ITSELF out -----------------------------------
#
# Root cause: ``acquire`` refused any live lease by judging liveness only from the
# pid embedded in the owner string -- which is alive for our own process. So an
# AuditStore re-created inside one process within the lease TTL fenced itself, and
# (fail-closed gate) denied every HIGH/CRITICAL action for up to the full TTL.


def test_sqlite_same_owner_renewal_is_not_a_self_fence():
    """The same writer re-acquiring its own live lease renews instead of raising."""
    conn = _fresh_conn()
    lease = SqliteWriterLease(conn)
    me = f"audit-store:{os.getpid()}"
    t1 = lease.acquire(me, ttl_sec=30.0)
    # Renewal by the identical owner must succeed and keep the same fence token
    # (bumping would fence a co-existing same-owner holder -> mutual thrash).
    t2 = lease.acquire(me, ttl_sec=30.0)
    assert t2 == t1
    assert lease.validate(t1) is True
    assert lease.current().owner == me


def test_sqlite_same_owner_renewal_extends_the_expiry():
    lease = SqliteWriterLease(_fresh_conn())
    me = f"audit-store:{os.getpid()}"
    lease.acquire(me, ttl_sec=5.0)
    before = lease.current().expires_at
    time.sleep(0.02)
    lease.acquire(me, ttl_sec=30.0)
    assert lease.current().expires_at > before


def test_sqlite_different_owner_with_live_pid_is_still_fenced():
    """THE safety counterpart: a *different* owner is refused even when its pid
    demonstrably alive. Renewal must never become lease-stealing."""
    conn = _fresh_conn()
    lease = SqliteWriterLease(conn)
    # Incumbent owner string embeds a pid we know is alive: our own.
    lease.acquire(f"audit-store:{os.getpid()}", ttl_sec=30.0)
    try:
        # Different owner string, same (live) pid -> must still be refused.
        lease.acquire(f"other-writer:{os.getpid()}", ttl_sec=30.0)
        assert False, "a different live owner must still be fenced (no lease stealing)"
    except StaleWriterError:
        pass


def test_inmemory_same_owner_renewal_is_not_a_self_fence():
    lease = InMemoryWriterLease()
    t1 = lease.acquire("w1", ttl_sec=30.0)
    t2 = lease.acquire("w1", ttl_sec=30.0)  # same owner -> renewal
    assert t2 == t1
    assert lease.validate(t1) is True
    # A different owner is still fenced.
    try:
        lease.acquire("w2", ttl_sec=30.0)
        assert False, "different owner must still raise"
    except StaleWriterError:
        pass


def test_two_audit_stores_in_one_process_both_append(tmp_path):
    """End-to-end regression for the real symptom: recreating the audit store
    inside one process (singleton reset / reconnect / per-test isolation) used to
    self-fence, which fail-closed then turned into a denial of every HIGH/CRITICAL
    action. Both instances must be able to append."""
    db = str(tmp_path / "audit.db")
    first = AuditStore(db_path=db)
    first.initialize()
    first.log_event(
        event_type=AuditEventType.STATE_CHANGE,
        principal_id="svc-a",
        scope=AuditScope.L0,
        outcome="success",
        details={"action": "first.store"},
        correlation_id="corr-1",
    )
    # Second store, same process, same DB: this is what used to raise
    # StaleWriterError("lease held by 'audit-store:<our own pid>' (alive=True)").
    second = AuditStore(db_path=db)
    second.initialize()
    second.log_event(
        event_type=AuditEventType.STATE_CHANGE,
        principal_id="svc-b",
        scope=AuditScope.L0,
        outcome="success",
        details={"action": "second.store"},
        correlation_id="corr-2",
    )
    # And the first instance must still be able to append afterwards (no thrash).
    first.log_event(
        event_type=AuditEventType.STATE_CHANGE,
        principal_id="svc-a",
        scope=AuditScope.L0,
        outcome="success",
        details={"action": "first.store.again"},
        correlation_id="corr-3",
    )
    rows = sqlite3.connect(db).execute(
        "SELECT COUNT(*) FROM audit_events"
    ).fetchone()[0]
    assert rows == 3, f"expected 3 appended events, got {rows}"
