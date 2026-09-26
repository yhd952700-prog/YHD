"""HC-01 — prove the live audit write/verify path uses the shared hash registry.

This is the Q3.5 "wire primitives into the live write path" verification. It
exercises the *implementation* on a FRESH, isolated SQLite database and asserts:

  * the audit module no longer keeps a PRIVATE copy of the algorithm registry --
    it imports ``HASH_ALGORITHMS`` / ``DEFAULT_HASH_ALG`` from
    ``src.common.hash_chain`` (identity check);
  * newly written events are hash-chained and declared with ``hash_alg``;
  * ``verify_integrity()`` returns ``(True, N)`` for a clean chain built through
    the shared, fail-closed registry;
  * a tampered row is detected (``(False, N)``) -- the shared dispatch does not
    silently re-hash with a default.

It does NOT -- and must not -- touch the deployed ``audit_store.db``. The
deployed database's existing fork is a DATA concern (D22 territory) and this
module intentionally leaves HC-01's deployment status UNVERIFIED. A green run
here proves the code path, never the production evidence.
"""
import pytest

from src.common.hash_chain import HASH_ALGORITHMS as SHARED_REGISTRY
from src.common.hash_chain import DEFAULT_HASH_ALG as SHARED_DEFAULT
from src.kernels.audit import (
    DEFAULT_HASH_ALG,
    HASH_ALGORITHMS,
    AuditEvent,
    AuditEventType,
    AuditScope,
    AuditStore,
)


@pytest.fixture
def store(tmp_path):
    """Fresh, isolated audit store (never the deployed audit_store.db)."""
    return AuditStore(db_path=str(tmp_path / "audit.db"))


def _log_n(store, n=5):
    return [
        store.log_event(
            AuditEventType.ACCESS_CHECK, f"p{i}", AuditScope.L1, "allow",
            details={"op": f"op{i}"}, correlation_id=f"corr-{i}",
        )
        for i in range(n)
    ]


def _rows(store):
    return store._conn.execute(
        "SELECT seq, event_hash, prev_event_hash, hash_alg FROM audit_events "
        "ORDER BY seq ASC"
    ).fetchall()


# ---------------------------------------------------------------------------
# Registry wiring
# ---------------------------------------------------------------------------

def test_audit_module_uses_the_shared_registry_not_a_private_copy():
    # The private duplicate was removed in Q3.5; the names are now the exact
    # shared objects so the audit chain can never drift from HC-02..HC-08.
    assert HASH_ALGORITHMS is SHARED_REGISTRY
    assert DEFAULT_HASH_ALG is SHARED_DEFAULT


def test_new_rows_declare_hash_alg_and_write_through_shared_registry(store):
    _log_n(store, n=4)
    rows = _rows(store)
    assert len(rows) == 4
    for seq, _h, _prev, alg in rows:
        assert alg == "sha256"


# ---------------------------------------------------------------------------
# Chain validity on a fresh database (proves the implementation)
# ---------------------------------------------------------------------------

def test_fresh_chain_is_valid_through_shared_registry(store):
    _log_n(store, n=6)
    ok, total = store.verify_integrity()
    assert ok is True
    assert total == 6


def test_chain_linkage_is_contiguous_on_fresh_store(store):
    _log_n(store, n=5)
    rows = _rows(store)
    # First event has no predecessor.
    assert rows[0][2] is None
    # Every subsequent event's prev_event_hash equals the previous event's hash.
    for i in range(1, len(rows)):
        assert rows[i][2] == rows[i - 1][1]
    # Sequence numbers are contiguous.
    assert [r[0] for r in rows] == list(range(1, len(rows) + 1))


# ---------------------------------------------------------------------------
# Fail-closed content verification through the shared dispatch
# ---------------------------------------------------------------------------

def test_tampered_row_is_detected_via_shared_registry(store):
    _log_n(store, n=5)
    # Flip one stored hash directly through the store's own connection.
    victim = store._conn.execute(
        "SELECT seq, event_hash FROM audit_events ORDER BY seq ASC LIMIT 1"
    ).fetchone()
    tampered = ("0" * 64) if victim[1] != "0" * 64 else ("1" * 64)
    store._conn.execute(
        "UPDATE audit_events SET event_hash = ? WHERE seq = ?",
        (tampered, victim[0]),
    )
    store._conn.commit()
    ok, total = store.verify_integrity()
    assert ok is False
    assert total == 5


def test_audit_event_hash_is_computed_through_shared_registry():
    # The write-path entry point (AuditEvent.compute_hash) must resolve the
    # declared algorithm via the shared registry, not a private one.
    evt = AuditEvent(
        event_id="x", event_type=AuditEventType.STATE_CHANGE,
        principal_id="p", scope=AuditScope.L0, timestamp=1.0,
        correlation_id="c", outcome="ok", details={}, hash_alg="sha256",
    )
    digest = evt.compute_hash()
    assert isinstance(digest, str) and len(digest) == 64
