"""C2 segmented / incremental / rolling verification — hardening tests.

Covers the owner-listed "incremental / segmented verification" continuation
area, plus its interaction with crash/recovery:

- verify_segment: recomputes a range from raw storage and labels anchor
  provenance honestly (genesis vs stored-pointer vs checkpoint);
- tamper / gap detection: a content or link_hash edit inside a segment is
  pinned to its seq; an untouched segment still verifies;
- verify_incremental: cost is proportional to NEW events, not history; reports
  ``segment_verified`` vs ``rooted_at_genesis`` as separate claims; a
  ``max_events`` cap is reported honestly (a capped run is NOT rooted);
- verify_rolling: re-derives stale regions oldest-first within a budget; a
  region that no longer re-derives is kept as EVIDENCE (never deleted);
- crash / recovery: a segment over a recovered chain still verifies clean.

Never opens, reads, or writes the production ``audit_store.db``. All tests use
throwaway temp DBs with explicit paths (bypassing the global singleton and the
project-root default).
"""

import os
import time

from src.kernels.audit import (
    AuditStore,
    AuditEventType,
    AuditScope,
)


def _make_store(dirpath: str) -> AuditStore:
    # Explicit path: bypasses the global singleton AND the project-root default,
    # so this test never touches the live evidence DB regardless of env.
    path = os.path.join(dirpath, "audit_segmented.db")
    store = AuditStore(db_path=path)
    store.initialize()
    return store


def _append(store, n, principal="p1", scope=AuditScope.L2, start=0):
    for i in range(start, start + n):
        store.log_event(
            AuditEventType.POLICY_EVAL, principal, scope, "allow",
            details={"i": i},
        )


def _tamper_event_hash(store, seq, bad="deadbeef"):
    store._conn.execute(
        "UPDATE audit_events SET event_hash = ? WHERE seq = ?", (bad, seq))
    store._conn.commit()


def _delete_row(store, seq):
    store._conn.execute("DELETE FROM audit_events WHERE seq = ?", (seq,))
    store._conn.commit()


def _make_stale(store, checkpoint_id, age_sec=10_000):
    store._conn.execute(
        "UPDATE verification_checkpoints SET verified_at = ? WHERE id = ?",
        (time.time() - age_sec, checkpoint_id))
    store._conn.commit()


# --------------------------------------------------------------------------- #
# verify_segment — recompute a range from raw storage
# --------------------------------------------------------------------------- #
def test_verify_segment_interior_range_verifies_clean(tmp_path):
    store = _make_store(str(tmp_path))
    _append(store, 50)
    res = store.verify_segment(11, 30)
    assert res["verified"] is True
    assert res["event_count"] == 20
    # auto-anchored on a non-genesis start => honest "stored pointer" label
    assert res["anchor_source"] == "stored"
    assert res["rooted_at_genesis"] is False


def test_verify_segment_from_genesis_is_rooted(tmp_path):
    store = _make_store(str(tmp_path))
    _append(store, 30)
    res = store.verify_segment(1, 30)
    assert res["verified"] is True
    assert res["anchor_source"] == "genesis"
    assert res["rooted_at_genesis"] is True


def test_verify_segment_explicit_anchor_classified_as_checkpoint(tmp_path):
    store = _make_store(str(tmp_path))
    _append(store, 40)
    c1 = store.verify_incremental()           # cp covering 1..40, genesis-rooted
    _append(store, 40)                        # tail -> 80
    c2 = store.verify_incremental()           # cp covering 41..80
    assert c1["new_checkpoint_id"] and c2["new_checkpoint_id"]
    link = store.link_hash_at(40)
    res = store.verify_segment(41, 80, start_link_hash=link)
    assert res["verified"] is True
    # the caller supplied a proven checkpoint anchor => reported as proven
    assert res["anchor_source"] == "checkpoint"
    assert res["rooted_at_genesis"] is True


def test_verify_segment_detects_tampered_content(tmp_path):
    store = _make_store(str(tmp_path))
    _append(store, 30)
    _tamper_event_hash(store, 15)
    res = store.verify_segment(1, 30)
    assert res["verified"] is False
    assert any("seq=15" in f for f in res["failures"])
    # the untouched tail segment (16..30) still re-derives cleanly on its own:
    # tampering the CONTENT of 15 does not collapse the independent segment.
    res2 = store.verify_segment(16, 30)
    assert res2["verified"] is True


def test_verify_segment_detects_missing_row_gap(tmp_path):
    store = _make_store(str(tmp_path))
    _append(store, 30)
    _delete_row(store, 15)
    res = store.verify_segment(1, 30)
    assert res["verified"] is False
    assert any(
        ("seq gap" in f) or ("seq=15" in f) or ("seq=16" in f)
        for f in res["failures"]
    )


# --------------------------------------------------------------------------- #
# verify_incremental — cost proportional to NEW events, not history
# --------------------------------------------------------------------------- #
def test_verify_incremental_first_run_is_genesis_rooted(tmp_path):
    store = _make_store(str(tmp_path))
    _append(store, 40)
    res = store.verify_incremental()
    assert res["segment_verified"] is True
    assert res["rooted_at_genesis"] is True
    assert res["new_checkpoint_id"] is not None
    assert res["events_checked"] == 40
    assert res["from_seq"] == 1 and res["to_seq"] == 40


def test_verify_incremental_only_checks_new_events(tmp_path):
    store = _make_store(str(tmp_path))
    _append(store, 40)
    first = store.verify_incremental()        # covers 1..40, rooted
    assert first["rooted_at_genesis"] is True
    _append(store, 60)                        # tail -> 100
    second = store.verify_incremental()
    # only the 60 NEW events are re-derived, NOT the full 100 — C2's whole point
    assert second["events_checked"] == 60
    assert second["from_seq"] == 41 and second["to_seq"] == 100
    assert second["segment_verified"] is True
    # the checkpoint chain now reaches the tail => honestly rooted
    assert second["rooted_at_genesis"] is True


def test_verify_incremental_max_events_cap_not_rooted(tmp_path):
    store = _make_store(str(tmp_path))
    _append(store, 40)
    first = store.verify_incremental()        # 1..40, rooted
    assert first["rooted_at_genesis"] is True
    _append(store, 30)                        # tail -> 70
    capped = store.verify_incremental(max_events=5)
    assert capped["from_seq"] == 41
    assert capped["to_seq"] == 45
    assert capped["events_checked"] == 5
    # honest: tail is 70 but we only verified through 45 -> NOT rooted
    assert capped["rooted_at_genesis"] is False


# --------------------------------------------------------------------------- #
# verify_rolling — re-derive stale regions within a budget; keep failures as EVIDENCE
# --------------------------------------------------------------------------- #
def test_verify_rolling_reverifies_stale_regions(tmp_path):
    store = _make_store(str(tmp_path))
    _append(store, 40)
    c1 = store.verify_incremental()           # cp covering 1..40
    _append(store, 40)                        # tail -> 80
    c2 = store.verify_incremental()           # cp covering 41..80
    assert c1["new_checkpoint_id"] and c2["new_checkpoint_id"]
    _make_stale(store, c1["new_checkpoint_id"])
    _make_stale(store, c2["new_checkpoint_id"])
    res = store.verify_rolling(budget_events=10_000, max_age_sec=1)
    assert res["failures"] == []
    assert len(res["reverified"]) == 2
    ids = {r["checkpoint_id"] for r in res["reverified"]}
    assert ids == {c1["new_checkpoint_id"], c2["new_checkpoint_id"]}
    assert res["events_reverified"] == 80
    assert res["budget_exceeded"] is False


def test_verify_rolling_keeps_corrupted_region_as_evidence(tmp_path):
    store = _make_store(str(tmp_path))
    _append(store, 40)
    c1 = store.verify_incremental()           # cp covering 1..40
    _append(store, 40)                        # tail -> 80
    c2 = store.verify_incremental()           # cp covering 41..80
    _make_stale(store, c1["new_checkpoint_id"])
    _make_stale(store, c2["new_checkpoint_id"])
    # corrupt an event inside checkpoint 1's range
    _tamper_event_hash(store, 10)
    res = store.verify_rolling(budget_events=10_000, max_age_sec=1)
    # the corrupted region is reported as a failure, NOT silently dropped
    assert len(res["failures"]) >= 1
    assert res["failures"][0]["checkpoint_id"] == c1["new_checkpoint_id"]
    # the untouched region (41..80) still re-verifies cleanly
    assert any(
        r["checkpoint_id"] == c2["new_checkpoint_id"] for r in res["reverified"]
    )
    # EVIDENCE preserved: the failed checkpoint row is NOT deleted
    row = store._conn.execute(
        "SELECT id FROM verification_checkpoints WHERE id = ?",
        (c1["new_checkpoint_id"],),
    ).fetchone()
    assert row is not None


def test_verify_rolling_budget_is_work_bound_not_skip(tmp_path):
    store = _make_store(str(tmp_path))
    _append(store, 100)
    c1 = store.verify_incremental()           # one cp covering 1..100
    _make_stale(store, c1["new_checkpoint_id"])
    # budget far smaller than the single region => region exceeds budget
    res = store.verify_rolling(budget_events=10, max_age_sec=1)
    assert res["budget_exceeded"] is True
    # but the budget is a WORK bound, not a skip: the region is still re-derived
    assert len(res["reverified"]) == 1
    assert res["remaining_stale"] == 0


# --------------------------------------------------------------------------- #
# crash / recovery — a segment over a recovered chain still verifies
# --------------------------------------------------------------------------- #
def test_segment_over_recovered_chain_after_crash(tmp_path):
    store = _make_store(str(tmp_path))
    _append(store, 200)
    # simulate a process crash: slam the connection shut without shutdown()
    store._conn.close()
    reopened = _make_store(str(tmp_path))     # recovers from the same path
    ok, count = reopened.verify_integrity()
    assert ok, "chain not recoverable after abrupt close"
    assert count == 200, f"events lost on recovery: expected 200, got {count}"
    res = reopened.verify_segment(1, 200)
    assert res["verified"] is True
    assert res["event_count"] == 200
    assert res["anchor_source"] == "genesis"
    assert res["rooted_at_genesis"] is True
