"""#99 regression tests — writer-lease identity upgrade.

Covers the ADR-audit-writer-lease-identity decision table (§4.3) and migration /
rollback contract (§6). These tests NEVER touch the production audit_store.db or
any Evidence file; they use :memory: connections or tmp_path.

Default mode stays "pid" (the legacy owner/PID model, unchanged). The uuid model
is exercised by setting LIUHAO_AUDIT_LEASE_IDENTITY=uuid. Every test is
discriminating: it must fail if the change is reverted.
"""

import os
import sqlite3
import time

import pytest

from src.kernels.audit import AuditStore, AuditEventType, AuditScope
from src.kernels.audit.fencing import (
    SqliteWriterLease,
    StaleWriterError,
    FencedWriterError,
    _process_writer_id,
    _process_boot_gen,
    _LEASE_SCHEMA,
    _set_last_token,
)


@pytest.fixture
def uuid_mode(monkeypatch):
    """Run the uuid identity model and reset process-global fence state."""
    monkeypatch.setenv("LIUHAO_AUDIT_LEASE_IDENTITY", "uuid")
    _set_last_token(None)
    yield
    monkeypatch.delenv("LIUHAO_AUDIT_LEASE_IDENTITY", raising=False)
    _set_last_token(None)


def _fresh_lease_conn():
    conn = sqlite3.connect(":memory:")
    conn.executescript(_LEASE_SCHEMA)
    lease = SqliteWriterLease.__new__(SqliteWriterLease)
    lease._conn = conn
    return lease


# --- uuid decision table (ADR §4.3) ----------------------------------------- #


def test_uuid_first_install_token_one_epoch_zero(uuid_mode):
    lease = _fresh_lease_conn()
    wid = _process_writer_id()
    bg = _process_boot_gen()
    assert lease.acquire_within("o", writer_id=wid, boot_gen=bg,
                                my_last_token=None) == 1
    v = lease.current()
    assert v.token == 1 and v.writer_epoch == 0 and v.writer_id == wid


def test_uuid_same_writer_renewal_keeps_token_and_epoch(uuid_mode):
    lease = _fresh_lease_conn()
    wid = _process_writer_id()
    bg = _process_boot_gen()
    t1 = lease.acquire_within("o", writer_id=wid, boot_gen=bg, my_last_token=None)
    t2 = lease.acquire_within("o", writer_id=wid, boot_gen=bg, my_last_token=t1)
    assert t2 == t1, "renewal must keep the same token (U38)"
    assert lease.current().writer_epoch == 0, "renewal must not bump epoch"


def test_uuid_different_writer_live_is_refused(uuid_mode):
    lease = _fresh_lease_conn()
    bg = _process_boot_gen()
    t_a = lease.acquire_within("o-a", writer_id="a" * 16, boot_gen=bg,
                               my_last_token=None)
    # Different writer_id, incumbent lease still live -> must be refused, not
    # silently renewed (this is the fix for T6 cross-host collision at the
    # lease level, where pid mode would have renewed the same pid).
    with pytest.raises(StaleWriterError):
        lease.acquire_within("o-b", writer_id="b" * 16, boot_gen=bg,
                             my_last_token=None)
    assert lease.validate(t_a) is True


def test_uuid_stale_owner_takeover_bumps_epoch(uuid_mode):
    lease = _fresh_lease_conn()
    bg = _process_boot_gen()
    lease.acquire_within("o-a", writer_id="a" * 16, boot_gen=bg, my_last_token=None)
    # Release / expire the incumbent, then a different writer takes over.
    lease._conn.execute(
        "UPDATE writer_lease SET expires_at=0, owner=NULL, released_at=?",
        (time.time(),))
    t_b = lease.acquire_within("o-b", writer_id="b" * 16, boot_gen=bg,
                               my_last_token=None)
    assert t_b == 2
    assert lease.current().writer_epoch == 1, "forced takeover must bump epoch"


def test_uuid_fenced_writer_detection(uuid_mode):
    lease = _fresh_lease_conn()
    wid = _process_writer_id()
    bg = _process_boot_gen()
    t1 = lease.acquire_within("o", writer_id=wid, boot_gen=bg, my_last_token=None)
    # Clock-skew T3b shape: the row still names US as holder but our token was
    # superseded (epoch advanced) while we believed we still held it.
    lease._conn.execute(
        "UPDATE writer_lease SET token=10, writer_epoch=1, owner=NULL, "
        "expires_at=0 WHERE id=1")
    with pytest.raises(FencedWriterError) as exc:
        lease.acquire_within("o", writer_id=wid, boot_gen=bg, my_last_token=t1)
    assert exc.value.fenced_epoch == 1
    assert exc.value.fenced_token == 10


def test_uuid_never_decrease_token_and_row_never_deleted(uuid_mode):
    lease = _fresh_lease_conn()
    bg = _process_boot_gen()
    wids = ("a" * 16, "b" * 16)
    prev_token = 0
    for i in range(1000):
        wid = wids[i % 2]
        tok = lease.acquire_within("o", writer_id=wid, boot_gen=bg,
                                   my_last_token=None)
        assert tok > prev_token, "token must strictly increase"
        prev_token = tok
        n = lease._conn.execute(
            "SELECT COUNT(*) FROM writer_lease WHERE id=1").fetchone()[0]
        assert n == 1, "the lease row must never be deleted"
        lease.release_within(tok)


def test_uuid_boot_gen_takeover_and_refuse(uuid_mode):
    lease = _fresh_lease_conn()
    wid_a = "a" * 16
    wid_b = "b" * 16
    # Incumbent with boot_gen 100000.
    lease.acquire_within("o-a", writer_id=wid_a, boot_gen=100000,
                         my_last_token=None)
    # (a) our boot gen much HIGHER than the row's -> the host rebooted since ->
    # the row is stale -> takeover allowed.
    t_b = lease.acquire_within("o-b", writer_id=wid_b, boot_gen=200000,
                               my_last_token=None)
    assert t_b == 2 and lease.current().writer_epoch == 1
    # (b) now plant a row from a FOREIGN / impossible boot (boot_gen far above
    # ours) and confirm it is REFUSED -> T6 / uptime-went-backwards guard.
    lease._conn.execute(
        "UPDATE writer_lease SET writer_id=?, boot_gen=900000, expires_at=?, "
        "owner=NULL WHERE id=1", (wid_a, time.time() + 30,))
    with pytest.raises(StaleWriterError):
        lease.acquire_within("o-b", writer_id=wid_b, boot_gen=200000,
                             my_last_token=t_b)


def test_uuid_legacy_null_writer_id_falls_back_to_ttl(uuid_mode):
    lease = _fresh_lease_conn()
    # A legacy 5-column-shaped row: writer_id NULL, owner a pid string, LIVE.
    lease._conn.execute(
        "INSERT INTO writer_lease (id, token, owner, acquired_at, expires_at, "
        "writer_id, writer_epoch, boot_gen, released_at) "
        "VALUES (1, 1, ?, ?, ?, NULL, 0, NULL, NULL)",
        ("audit-store:99999", time.time(), time.time() + 30,))
    # uuid-mode writer must refuse a LIVE legacy row (status quo preserved: no
    # new takeover path opened just because the model changed).
    with pytest.raises(StaleWriterError):
        lease.acquire_within("o", writer_id=_process_writer_id(),
                             boot_gen=_process_boot_gen(), my_last_token=None)
    # But an EXPIRED legacy row IS taken over (epoch bumps) — the old behaviour.
    lease._conn.execute(
        "UPDATE writer_lease SET expires_at=0, owner=NULL, released_at=?",
        (time.time(),))
    t = lease.acquire_within("o", writer_id=_process_writer_id(),
                             boot_gen=_process_boot_gen(), my_last_token=None)
    assert t == 2 and lease.current().writer_epoch == 1


# --- migration / rollback (ADR §6) ------------------------------------------ #


def test_migration_old_5col_schema_plus_new_code(uuid_mode, tmp_path):
    """A pre-existing 5-column writer_lease is migrated additively in
    _apply_schema (inside BEGIN IMMEDIATE) and appends still work."""
    db = str(tmp_path / "legacy.db")
    # Build a legacy 5-column writer_lease by hand.
    seed = sqlite3.connect(db)
    seed.execute(
        "CREATE TABLE writer_lease (id INTEGER PRIMARY KEY CHECK (id=1), "
        "token INTEGER NOT NULL, owner TEXT, acquired_at REAL, expires_at REAL)")
    seed.commit()
    seed.close()

    store = AuditStore(db_path=db)
    store.initialize()
    cols = [r[1] for r in store._conn.execute(
        "PRAGMA table_info(writer_lease)").fetchall()]
    for expected in ("writer_id", "writer_epoch", "boot_gen", "released_at"):
        assert expected in cols, f"migration missed column {expected}"
    store.log_event(AuditEventType.STATE_CHANGE, "svc", AuditScope.L0,
                    "success", correlation_id="x")
    ok, n = store.verify_integrity()
    assert ok and n == 1


def test_rollback_new_schema_plus_old_code(tmp_path):
    """New 9-column schema must still work with the legacy pid-mode acquire
    (proves rollback: an old binary against a new database)."""
    db = str(tmp_path / "new.db")
    store = AuditStore(db_path=db)
    store.initialize()
    # Default (pid) mode acquire against the migrated 9-column table.
    lease = store._lease
    t = lease.acquire("audit-store:1", ttl_sec=30.0)
    assert t >= 1
    view = lease.current()
    assert view.owner == "audit-store:1"
    assert view.writer_epoch == 0


def test_owner_format_unchanged_for_one_release():
    """_lease_owner still produces exactly 'audit-store:<pid>' so the legacy
    pid-parsing path (§6.3) is not broken by the new columns."""
    import tempfile
    db = os.path.join(tempfile.mkdtemp(), "x.db")
    store = AuditStore(db_path=db)
    owner = store._lease_owner()
    assert owner == f"audit-store:{os.getpid()}"


# --- default-mode regression ------------------------------------------------ #


def test_default_mode_is_pid_and_appends():
    """With no env var the legacy owner/PID model is used and appends work."""
    import tempfile
    db = os.path.join(tempfile.mkdtemp(), "pid.db")
    store = AuditStore(db_path=db)
    store.initialize()
    for i in range(3):
        store.log_event(AuditEventType.STATE_CHANGE, f"svc{i}", AuditScope.L0,
                        "success", correlation_id=f"c{i}")
    ok, n = store.verify_integrity()
    assert ok and n == 3
    assert store.get_stats()["lease_identity_mode"] == "pid"


def test_two_stores_in_one_process_both_append_uuid(uuid_mode, tmp_path):
    """End-to-end U38 guard under the uuid model: two AuditStore instances in one
    process share a writer_id and must both append (no self-fence)."""
    db = str(tmp_path / "two.db")
    first = AuditStore(db_path=db)
    first.initialize()
    first.log_event(AuditEventType.STATE_CHANGE, "a", AuditScope.L0, "success",
                    correlation_id="1")
    second = AuditStore(db_path=db)
    second.initialize()
    second.log_event(AuditEventType.STATE_CHANGE, "b", AuditScope.L0, "success",
                     correlation_id="2")
    first.log_event(AuditEventType.STATE_CHANGE, "a", AuditScope.L0, "success",
                    correlation_id="3")
    ok, n = first.verify_integrity()
    assert ok and n == 3
