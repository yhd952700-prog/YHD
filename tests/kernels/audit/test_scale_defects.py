"""Two defects that only appear at scale, pinned by deterministic tests.

Both were found by reading the implementation against what a million-event
chain actually does, not by a failing test -- so the tests come first here,
before the failure has a chance to reach production.

1. ``verify_integrity()`` used to run under the SAME lock every append needs,
   for a full fetchall() of the chain. Measured ~23 us/row, so the
   production-sized store (~317k rows) lost ~7-8 seconds of append capacity
   per verification -- and because the mandatory-evidence path needs that
   lock, every HIGH/CRITICAL governed action was denied for that window.
2. ``event_id`` was 48 bits (``uuid4()[:12]``). By the birthday bound that
   collides with ~18% probability at 10^7 events, and in the batch path a
   collision is classified as "duplicate": real evidence dropped, no error.
"""
import threading

from src.kernels.audit import AuditEvent, AuditEventType, AuditScope, AuditStore

# PRAGMA synchronous integer codes.
SYNC_NORMAL = 1
SYNC_FULL = 2


def _store(tmp_path, name):
    store = AuditStore(db_path=str(tmp_path / name))
    store.initialize()
    return store


def test_verification_runs_on_a_snapshot_and_not_under_the_append_lock(tmp_path):
    """A full-chain verification must not stop the system from recording.

    Held the append lock for the whole scan before; now it reads a committed
    snapshot instead. This test holds the lock itself, so the old behaviour
    would block here until the timeout and fail.
    """
    store = _store(tmp_path, "a.db")
    for i in range(50):
        store.log_event(AuditEventType.STATE_CHANGE, "p", AuditScope.L0, "ok",
                        {"i": i}, f"c{i}")

    finished = threading.Event()
    outcome = {}

    def verify():
        try:
            outcome["ok"] = store.verify_integrity()
        except BaseException as exc:  # noqa: BLE001 - reported below
            outcome["exc"] = exc
        finally:
            finished.set()

    with store._lock:  # simulate an in-flight append holding the write path
        worker = threading.Thread(target=verify, daemon=True)
        worker.start()
        completed = finished.wait(timeout=15)
        worker.join(timeout=15)

    assert completed, (
        "verify_integrity() blocked on the append lock -- a verification would "
        "freeze every governed action for the length of the scan"
    )
    assert "exc" not in outcome, f"verification raised: {outcome['exc']}"
    assert outcome["ok"][0] is True
    assert outcome["ok"][1] == 50


def test_verification_still_sees_a_consistent_chain(tmp_path):
    """Reading a snapshot must not weaken what verification proves."""
    store = _store(tmp_path, "b.db")
    for i in range(30):
        store.log_event(AuditEventType.STATE_CHANGE, "p", AuditScope.L0, "ok",
                        {"i": i}, f"c{i}")
    ok, total = store.verify_integrity()
    assert ok is True
    assert total == 30

    # Tampering must still be detected on the snapshot path.
    store._conn.execute(
        "UPDATE audit_events SET outcome = 'tampered' WHERE seq = 10")
    store._conn.commit()
    ok, _ = store.verify_integrity()
    assert ok is False, "a snapshot read must still detect tampering"


def test_generated_event_ids_are_full_width_and_unique(tmp_path):
    """48-bit ids would collide inside a million-event chain."""
    store = _store(tmp_path, "c.db")
    ids = []
    for i in range(200):
        event = store.log_event(AuditEventType.STATE_CHANGE, "p",
                                AuditScope.L0, "ok", {"i": i}, f"c{i}")
        ids.append(event.event_id)

    assert all(len(eid) == 32 for eid in ids), (
        f"event_id must be a full 128-bit uuid, got lengths "
        f"{sorted({len(e) for e in ids})}"
    )
    assert len(set(ids)) == len(ids), "generated event_ids must be unique"


def test_batch_ids_are_never_mistaken_for_duplicates(tmp_path):
    """The failure mode of a short id: a new event silently dropped.

    10,000 ids at 48 bits would already be in collision territory for a large
    deployment; a 128-bit id makes the classification trustworthy.
    """
    store = _store(tmp_path, "d.db")
    events = [
        AuditEvent(event_id=None, event_type=AuditEventType.STATE_CHANGE,
                   principal_id="p", scope=AuditScope.L0, timestamp=1.0 + i * 1e-6,
                   correlation_id=None, outcome="ok", details={"i": i})
        for i in range(500)
    ]
    result = store.log_event_batch(events)
    assert len(result.appended) == 500, (
        f"{len(result.duplicates)} fresh events were classified as duplicates "
        "-- real evidence dropped"
    )
    assert result.duplicates == []
    assert len({e.event_id for e in result.appended}) == 500


def _applied_sync(store):
    return store._conn.execute("PRAGMA synchronous").fetchone()[0]


def test_evidence_store_opens_power_loss_durable_by_default(tmp_path, monkeypatch):
    """synchronous=NORMAL loses committed events on a power cut.

    A store whose promise is "the record exists" must not carry that hole by
    default. Pinned here so the grade cannot silently slip back.
    """
    monkeypatch.delenv("LIUHAO_AUDIT_SYNCHRONOUS", raising=False)
    store = _store(tmp_path, "e.db")
    assert _applied_sync(store) == SYNC_FULL


def test_durability_grade_can_be_chosen_per_deployment(tmp_path, monkeypatch):
    """...and the opt-out is explicit, not accidental."""
    monkeypatch.setenv("LIUHAO_AUDIT_SYNCHRONOUS", "NORMAL")
    store = _store(tmp_path, "f.db")
    assert _applied_sync(store) == SYNC_NORMAL


def test_an_unknown_durability_value_falls_back_to_the_safe_grade(
        tmp_path, monkeypatch):
    """A typo must not silently downgrade the evidence store."""
    monkeypatch.setenv("LIUHAO_AUDIT_SYNCHRONOUS", "not-a-level")
    store = _store(tmp_path, "g.db")
    assert _applied_sync(store) == SYNC_FULL
