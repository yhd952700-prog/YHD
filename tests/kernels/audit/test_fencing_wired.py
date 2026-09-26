"""HC-01 Q3.5 — prove the single-writer fence is wired into the live write path.

These tests exercise the *implementation* on a FRESH, isolated SQLite database.
They confirm:

  * the ``writer_lease`` table is created on store init;
  * normal sequential writes succeed (the acquire-once / validate-per-write
    pattern does NOT trap the second write with a ``StaleWriterError``);
  * a second writer cannot acquire the lease while the first holds an unexpired
    one (cross-process fence);
  * a writer whose lease has been fenced by a newer token is REFUSED on the next
    append (correct fence behaviour -- it must not write).

None of this touches the deployed ``audit_store.db``; the deployment fork (D22)
remains UNVERIFIED.
"""
import time

import pytest

from src.kernels.audit import AuditEventType, AuditScope, AuditStore
from src.kernels.audit.fencing import SqliteWriterLease, StaleWriterError


def _write(store, i):
    return store.log_event(
        AuditEventType.STATE_CHANGE, f"p{i}", AuditScope.L0, "ok",
        details={"i": i}, correlation_id=f"c{i}",
    )


def test_writer_lease_table_is_created_on_init(tmp_path):
    store = AuditStore(db_path=str(tmp_path / "audit.db"))
    names = {r[0] for r in store._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "writer_lease" in names


def test_normal_writes_succeed_with_fencing(tmp_path):
    store = AuditStore(db_path=str(tmp_path / "audit.db"))
    for i in range(5):
        _write(store, i)
    ok, total = store.verify_integrity()
    assert ok is True
    assert total == 5


def test_second_writer_cannot_acquire_while_lease_held(tmp_path):
    store = AuditStore(db_path=str(tmp_path / "audit.db"))
    _write(store, 0)  # store acquires token T1 (unexpired)
    # A competing writer on the same DB must be refused while T1 is unexpired.
    lease_b = SqliteWriterLease(store._conn)
    with pytest.raises(StaleWriterError):
        lease_b.acquire(owner="writer-b")


def test_fenced_writer_is_refused_on_append(tmp_path):
    store = AuditStore(db_path=str(tmp_path / "audit.db"))
    _write(store, 0)  # acquires token T1
    # Simulate a newer writer having fenced this store: a higher token, owned by
    # someone else, still unexpired.
    store._conn.execute(
        "UPDATE writer_lease SET token=?, owner=?, expires_at=? WHERE id=1",
        (999, "intruder", time.time() + 100),
    )
    store._conn.commit()
    with pytest.raises(StaleWriterError):
        _write(store, 1)
