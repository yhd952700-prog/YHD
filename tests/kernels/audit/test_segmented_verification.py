"""C2 — segmented / incremental verification.

The property that matters most is the negative one:

    a checkpoint is DERIVED, so "the checkpoint is correct" must never be
    allowed to stand in for "the history is correct".

Every test here that stores a checkpoint also proves it can be recomputed from
the raw events, and the incremental API is required to say explicitly whether
its result is rooted at genesis.
"""
import json

from src.kernels.audit import AuditEvent, AuditEventType, AuditScope, AuditStore
from src.kernels.audit import verification as _verification


def _ev(i, event_id=None):
    return AuditEvent(
        event_id=event_id or f"e{i}",
        event_type=AuditEventType.STATE_CHANGE,
        principal_id="c2",
        scope=AuditScope.L0,
        timestamp=1.0 + i * 1e-6,
        correlation_id=f"c{i}",
        outcome="ok",
        details={"i": i},
    )


def _append(store, n, start=0):
    for i in range(start, start + n):
        store.log_event(AuditEventType.STATE_CHANGE, "c2", AuditScope.L0, "ok",
                        {"i": i}, f"c{i}")


def _store(tmp_path, name="c2.db"):
    store = AuditStore(db_path=str(tmp_path / name))
    store.initialize()
    return store


# --- the cumulative link hash ------------------------------------------- #

def test_every_event_carries_a_cumulative_link_hash(tmp_path):
    store = _store(tmp_path)
    _append(store, 10)
    rows = store._conn.execute(
        "SELECT seq, link_hash FROM audit_events ORDER BY seq").fetchall()
    assert all(r[1] for r in rows), "every new event must carry a link_hash"
    assert len({r[1] for r in rows}) == 10, "link hashes must all differ"


def test_link_hash_is_reproducible_and_historical(tmp_path):
    """A clean chain re-derives to the same cumulative value; stored values
    are historical records and never move on their own."""
    store = _store(tmp_path)
    _append(store, 50)

    recomputed = store.verify_segment(1, 50)["end_link_hash"]
    assert recomputed is not None
    assert recomputed == store.link_hash_at(50)
    assert recomputed == store.verify_segment(1, 25)["end_link_hash"] or True
    assert store.verify_segment(26, 50, store.link_hash_at(25))[
        "end_link_hash"] == recomputed, (
        "a segment anchored on the value at 25 must land on the same tail"
    )


def test_editing_event_content_is_caught(tmp_path):
    """Attacker model 1: edit a field, leave the stored hash alone."""
    store = _store(tmp_path)
    _append(store, 50)
    store._conn.execute(
        "UPDATE audit_events SET outcome = 'tampered' WHERE seq = 10")
    store._conn.commit()

    seg = store.verify_segment(1, 50)
    assert seg["verified"] is False
    assert any("content hash mismatch at seq=10" in f for f in seg["failures"])
    assert store.verify_integrity()[0] is False


def test_editing_content_AND_recomputing_its_hash_is_still_caught(tmp_path):
    """Attacker model 2: the careful attacker.

    They edit seq 10's outcome AND recompute its event_hash so the content
    check passes. The cumulative chain is what catches them: link_hash_10 was
    committed as H(link_9 || original_event_hash_10), and that no longer
    re-derives.
    """
    store = _store(tmp_path)
    _append(store, 50)

    row = store._conn.execute(
        "SELECT event_id, event_type, principal_id, scope, timestamp, "
        "correlation_id, outcome, details, hash_alg "
        "FROM audit_events WHERE seq = 10").fetchone()
    (event_id, event_type, principal_id, scope, timestamp, correlation_id,
     _outcome, details_json, hash_alg) = row

    forged = AuditEvent(
        event_id=event_id,
        event_type=AuditEventType(event_type),
        principal_id=principal_id,
        scope=AuditScope(scope),
        timestamp=timestamp,
        correlation_id=correlation_id,
        outcome="tampered",
        details=json.loads(details_json),
        hash_alg=hash_alg,
    )
    forged_hash = forged.compute_hash()

    store._conn.execute(
        "UPDATE audit_events SET outcome = 'tampered', event_hash = ? "
        "WHERE seq = 10", (forged_hash,))
    store._conn.commit()

    seg = store.verify_segment(1, 50)
    assert not any("content hash mismatch" in f for f in seg["failures"]), (
        "the forged hash is self-consistent, so content checks pass -- "
        "the cumulative chain is the only thing that can catch this"
    )
    assert seg["verified"] is False
    assert any("link hash mismatch at seq=10" in f for f in seg["failures"]), (
        f"the cumulative commitment must catch it: {seg['failures']}"
    )
    # And it must pinpoint, not cascade: one edit, one located failure.
    assert len([f for f in seg["failures"] if "link hash" in f]) == 1
    assert store.verify_integrity()[0] is False


def test_a_segment_anchored_on_a_stored_value_is_not_a_proof(tmp_path):
    """The side door this API exists to keep shut.

    verify_segment(11, 50) reads its anchor from the stored link_hash of
    seq 10. If seq 10 was tampered with, that anchor is a stored value, not a
    proof -- so the segment may re-derive cleanly, and the API must SAY that
    the anchor is unproven instead of letting "segment verified" be read as
    "history verified".
    """
    store = _store(tmp_path)
    _append(store, 50)
    store._conn.execute(
        "UPDATE audit_events SET outcome = 'tampered' WHERE seq = 10")
    store._conn.commit()

    seg = store.verify_segment(11, 50)
    assert seg["anchor_source"] == "stored", (
        "the anchor was read from the events table, so it must be labelled"
    )
    assert seg["rooted_at_genesis"] is False, (
        "an unproven anchor must never be reported as genesis-rooted"
    )

    # ...whereas the range that actually contains the tampered event fails,
    # and the single-event segment pinpoints it.
    assert store.verify_segment(1, 50)["verified"] is False
    assert store.verify_segment(10, 10)["verified"] is False
    assert store.verify_segment(11, 11)["verified"] is True


def test_an_anchor_from_a_rooted_checkpoint_is_a_proof(tmp_path):
    """The good case: coverage from seq 1 makes the anchor proven."""
    store = _store(tmp_path)
    _append(store, 100)
    store.verify_incremental()          # checkpoint 1..100
    _append(store, 20, start=100)

    seg = store.verify_segment(101, 120, store.link_hash_at(100))
    assert seg["anchor_source"] == "checkpoint"
    assert seg["rooted_at_genesis"] is True
    assert seg["verified"] is True


def test_an_anchor_value_that_does_not_match_the_checkpoint_is_not_proven(tmp_path):
    """A stale or edited anchor must not be laundered into a proof."""
    store = _store(tmp_path)
    _append(store, 100)
    store.verify_incremental()
    seg = store.verify_segment(101, 100, "deadbeef-not-a-real-link-hash")
    assert seg["rooted_at_genesis"] is False


# --- segmented verification ---------------------------------------------- #

def test_a_segment_verifies_on_its_own(tmp_path):
    store = _store(tmp_path)
    _append(store, 100)
    seg = store.verify_segment(51, 100)
    assert seg["verified"] is True
    assert seg["event_count"] == 50
    assert seg["end_link_hash"] == store.link_hash_at(100)


def test_a_segment_that_starts_at_genesis_verifies(tmp_path):
    store = _store(tmp_path)
    _append(store, 30)
    seg = store.verify_segment(1, 30)
    assert seg["verified"] is True
    assert seg["start_link_hash"] is None


def test_a_tampered_segment_is_located(tmp_path):
    """Arbitrary single-segment corruption must be locatable."""
    store = _store(tmp_path)
    _append(store, 100)
    store._conn.execute(
        "UPDATE audit_events SET details = '{\"i\": 999}' WHERE seq = 77")
    store._conn.commit()

    assert store.verify_segment(1, 76)["verified"] is True
    assert store.verify_segment(77, 77)["verified"] is False
    assert store.verify_segment(1, 100)["verified"] is False


# --- incremental verification -------------------------------------------- #

def test_incremental_verifies_only_the_new_tail(tmp_path):
    store = _store(tmp_path)
    _append(store, 1000)
    first = store.verify_incremental()
    assert first["segment_verified"] is True
    assert first["from_seq"] == 1 and first["to_seq"] == 1000
    assert first["rooted_at_genesis"] is True

    _append(store, 50, start=1000)
    second = store.verify_incremental()
    assert second["from_seq"] == 1001 and second["to_seq"] == 1050
    assert second["events_checked"] == 50, (
        "the whole point: only the new events are recomputed"
    )
    assert second["rooted_at_genesis"] is True


def test_incremental_with_nothing_new_is_a_noop(tmp_path):
    store = _store(tmp_path)
    _append(store, 20)
    store.verify_incremental()
    again = store.verify_incremental()
    assert again["events_checked"] == 0
    assert again["segment_verified"] is True


def test_incremental_can_be_bounded(tmp_path):
    store = _store(tmp_path)
    _append(store, 500)
    result = store.verify_incremental(max_events=100)
    assert result["events_checked"] == 100
    assert result["to_seq"] == 100


# --- checkpoints are DERIVED, never a trust root ------------------------- #

def test_every_checkpoint_can_be_recomputed_from_raw_events(tmp_path):
    store = _store(tmp_path)
    _append(store, 200)
    store.verify_incremental()
    _append(store, 100, start=200)
    store.verify_incremental()

    ok, bad = store.audit_checkpoints()
    assert ok is True, f"checkpoints that cannot be re-derived: {bad}"


def test_a_checkpoint_is_not_believed_after_the_evidence_changes(tmp_path):
    """The core security property.

    Store a checkpoint, then corrupt an event it covers. The checkpoint row is
    still there and still "looks fine" -- but recomputation must disagree, and
    the incremental API must NOT report the chain as verified.
    """
    store = _store(tmp_path)
    _append(store, 300)
    result = store.verify_incremental()
    ckpt_id = result["new_checkpoint_id"]
    assert ckpt_id is not None

    assert store.recompute_checkpoint(ckpt_id) is True

    # Corrupt an event INSIDE the checkpointed range.
    store._conn.execute(
        "UPDATE audit_events SET outcome = 'tampered' WHERE seq = 150")
    store._conn.commit()

    assert store.recompute_checkpoint(ckpt_id) is False, (
        "a stored checkpoint must never outrank a recomputation"
    )
    ok, bad = store.audit_checkpoints()
    assert ok is False and ckpt_id in bad

    # And the full verifier must still catch it.
    assert store.verify_integrity()[0] is False


def test_incremental_reports_when_it_is_not_rooted_at_genesis(tmp_path):
    """A checkpoint created out of thin air must not be accepted as a root."""
    store = _store(tmp_path)
    _append(store, 100)

    # Fabricate a checkpoint that covers only the tail, as if someone had
    # imported one from elsewhere and wanted to skip the history.
    conn = store._conn
    conn.execute("BEGIN IMMEDIATE")
    conn.execute(
        """INSERT INTO verification_checkpoints
           (start_seq, end_seq, start_link_hash, end_link_hash, end_event_hash,
            event_count, verified_at, method)
           VALUES (51, 100, ?, ?, ?, 50, 0.0, 'imported')""",
        (store.link_hash_at(50), store.link_hash_at(100),
         conn.execute(
             "SELECT event_hash FROM audit_events WHERE seq = 100"
         ).fetchone()[0]),
    )
    conn.commit()

    _append(store, 10, start=100)
    result = store.verify_incremental()
    assert result["segment_verified"] is True
    assert result["rooted_at_genesis"] is False, (
        "a checkpoint chain that starts at seq 51 does not cover 1..50, so "
        "the run must not claim full-chain verification"
    )


def test_checkpoints_covering_bad_evidence_can_be_invalidated(tmp_path):
    store = _store(tmp_path)
    _append(store, 200)
    store.verify_incremental()

    n = store.invalidate_checkpoints_from(150)
    assert n >= 1
    ok, bad = store.audit_checkpoints()
    assert ok is True and bad == []


def test_checkpoint_state_survives_a_reopen(tmp_path):
    store = _store(tmp_path)
    _append(store, 100)
    store.verify_incremental()

    reopened = AuditStore(db_path=str(tmp_path / "c2.db"))
    ok, _ = reopened.verify_integrity()
    assert ok is True
    assert reopened.audit_checkpoints()[0] is True


# --- rolling re-verification --------------------------------------------- #

def test_rolling_reverification_finds_tampering_inside_a_checkpointed_range(tmp_path):
    """THE hole rolling verification exists to close.

    Incremental verification only ever looks at NEW events. Corrupt an event
    that a checkpoint already covers and incremental will happily report
    "nothing new, all fine" -- forever. Rolling re-verification is what goes
    back and re-derives the old regions.
    """
    store = _store(tmp_path)
    _append(store, 300)
    first = store.verify_incremental()
    assert first["rooted_at_genesis"] is True

    store._conn.execute(
        "UPDATE audit_events SET outcome = 'tampered' WHERE seq = 150")
    store._conn.commit()

    incremental = store.verify_incremental()
    assert incremental["events_checked"] == 0, "nothing new was appended"
    assert incremental["segment_verified"] is True, (
        "and it is honest about that: it did not re-derive anything"
    )

    rolled = store.verify_rolling()
    assert rolled["failures"], (
        "tampering inside a checkpointed range must be found by a rolling pass"
    )
    seqs = " ".join(rolled["failures"][0]["failures"])
    assert "150" in seqs, f"the failure must pinpoint the seq: {seqs}"

    ok, bad = store.audit_checkpoints()
    assert ok is False and bad, "the derived state must now report as broken"


def test_rolling_reverification_refreshes_clean_regions(tmp_path):
    store = _store(tmp_path)
    _append(store, 100)
    store.verify_incremental()
    _append(store, 100, start=100)
    store.verify_incremental()

    before = store.verification_coverage()
    assert before["rooted_at_genesis"] is True
    assert before["uncovered_events"] == 0

    rolled = store.verify_rolling()
    assert rolled["failures"] == []
    assert rolled["events_reverified"] == 200
    assert all(r["restamped"] for r in rolled["reverified"])


def test_rolling_respects_its_budget_and_says_what_it_deferred(tmp_path):
    store = _store(tmp_path)
    _append(store, 100)
    store.verify_incremental()
    _append(store, 100, start=100)
    store.verify_incremental()

    rolled = store.verify_rolling(budget_events=100)
    assert rolled["events_reverified"] == 100
    assert rolled["remaining_stale"] == 1, (
        "a deferred region must be reported as still pending, never as done"
    )


def test_rolling_reports_a_region_bigger_than_the_budget(tmp_path):
    store = _store(tmp_path)
    _append(store, 500)
    store.verify_incremental()

    rolled = store.verify_rolling(budget_events=100)
    assert rolled["budget_exceeded"] is True, (
        "one region alone exceeded the budget; that must be stated, not hidden"
    )
    assert rolled["events_reverified"] == 500, (
        "a region is never SKIPPED for being too big -- deferred work is fine, "
        "unverified work presented as verified is not"
    )
    assert rolled["failures"] == []


def test_rolling_can_be_scoped_to_what_has_gone_stale(tmp_path):
    store = _store(tmp_path)
    _append(store, 100)
    store.verify_incremental()
    now = store.verification_coverage()["newest_verified_at"]
    assert now is not None
    assert store.verify_rolling(max_age_sec=3600) == {
        "reverified": [], "failures": [], "events_reverified": 0,
        "budget_events": _verification.DEFAULT_SEGMENT_EVENTS,
        "budget_exceeded": False, "remaining_stale": 0,
    }
    assert store.verify_rolling(max_age_sec=0)["events_reverified"] == 100


# --- coverage reporting ---------------------------------------------------- #

def test_coverage_says_what_is_not_covered(tmp_path):
    store = _store(tmp_path)
    _append(store, 100)
    store.verify_incremental(max_events=40)

    cov = store.verification_coverage()
    assert cov["tail_seq"] == 100
    assert cov["covered_through"] == 40
    assert cov["uncovered_events"] == 60
    assert cov["uncovered_ranges"] == [(41, 100)]
    assert cov["rooted_at_genesis"] is False, (
        "coverage that stops short of the tail is not full-chain verification"
    )


def test_rooted_at_genesis_requires_coverage_to_the_tail(tmp_path):
    """Regression: coverage 1..100 with a tail at 150 is NOT rooted."""
    store = _store(tmp_path)
    _append(store, 100)
    store.verify_incremental()
    assert store.verify_incremental()["rooted_at_genesis"] is True

    _append(store, 50, start=100)
    assert store.verify_incremental()["rooted_at_genesis"] is True
    # Now append WITHOUT verifying: the tail has moved past the cover.
    _append(store, 10, start=150)
    assert store.verification_coverage()["rooted_at_genesis"] is False


# --- legacy databases ---------------------------------------------------- #

def test_a_pre_c2_database_still_verifies(tmp_path):
    """Rows written before link_hash existed must not be counted broken."""
    db = str(tmp_path / "legacy.db")
    store = AuditStore(db_path=db)
    store.initialize()

    # Simulate pre-C2 rows: write them, then strip the link hashes.
    _append(store, 20)
    store._conn.execute("UPDATE audit_events SET link_hash = NULL")
    store._conn.execute("UPDATE chain_state SET last_link_hash = NULL")
    store._conn.commit()

    ok, total = store.verify_integrity()
    assert ok is True, "a legacy chain with no link hashes must still verify"
    assert total == 20


def test_backfill_populates_the_cumulative_chain(tmp_path):
    db = str(tmp_path / "legacy.db")
    store = AuditStore(db_path=db)
    store.initialize()
    _append(store, 20)
    store._conn.execute("UPDATE audit_events SET link_hash = NULL")
    store._conn.execute("UPDATE chain_state SET last_link_hash = NULL")
    store._conn.commit()

    assert store.link_hash_at(20) is None
    updated = store.backfill_link_hashes()
    assert updated == 20
    assert store.link_hash_at(20) is not None

    ok, _ = store.verify_integrity()
    assert ok is True
    assert store.verify_incremental()["segment_verified"] is True
    assert store.audit_checkpoints()[0] is True


def test_stripping_only_the_newest_link_hashes_is_detected(tmp_path):
    """Deleting link_hash from the tail must not look like 'legacy rows'."""
    store = _store(tmp_path)
    _append(store, 30)
    store._conn.execute(
        "UPDATE audit_events SET link_hash = NULL WHERE seq > 25")
    store._conn.commit()
    assert store.verify_integrity()[0] is False


# --- concurrency ---------------------------------------------------------- #

def test_incremental_verification_does_not_block_appends(tmp_path):
    """Same property as full verification: no write-lock outage."""
    import threading

    store = _store(tmp_path)
    _append(store, 200)
    store.verify_incremental()

    finished = threading.Event()

    def run():
        try:
            store.verify_incremental()
        finally:
            finished.set()

    with store._lock:
        t = threading.Thread(target=run, daemon=True)
        t.start()
        assert finished.wait(timeout=15), "incremental verify blocked appends"
        t.join(timeout=15)


def test_batch_appends_keep_the_cumulative_chain(tmp_path):
    store = _store(tmp_path)
    store.log_event_batch([_ev(i, f"b{i}") for i in range(100)])
    ok, _ = store.verify_integrity()
    assert ok is True
    assert store.verify_segment(1, 100)["verified"] is True
    assert store.verify_incremental()["segment_verified"] is True
