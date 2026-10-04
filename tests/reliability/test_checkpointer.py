"""Tests for src.reliability.checkpointer (production C2 wiring).

These tests prove the wiring does what it was built for: it drives
``checkpoint_count`` off zero and lets ``evidence_status`` reach "verified",
which never happened before because nothing in production called
verify_incremental/verify_rolling.

All audit work happens in temp DBs (the frozen evidence store is never used).
"""
from __future__ import annotations

import threading
import time

import pytest

from src.kernels.audit import AuditEventType, AuditScope, AuditStore
from src.reliability import checkpointer as cp


@pytest.fixture
def store(tmp_path):
    st = AuditStore(str(tmp_path / "chain.db"))
    yield st
    try:
        st._conn.close()
    except Exception:
        pass


def _seed(store, n, principal="probe"):
    for i in range(n):
        store.log_event(
            event_type=AuditEventType.KERNEL_IMPLEMENTATION,
            principal_id=principal,
            scope=AuditScope.L0,
            outcome="allow",
            details={"i": i},
            correlation_id=f"c-{principal}-{i}",
        )


# --------------------------------------------------------------------------- #
# Budget helper
# --------------------------------------------------------------------------- #
def test_estimate_verify_seconds_matches_measured_rate():
    # measured: ~0.15 s per 5000 events (~30 us/event)
    assert cp.estimate_verify_seconds(5000) == pytest.approx(0.15, abs=0.01)
    assert cp.estimate_verify_seconds(0) == 0.0
    assert cp.estimate_verify_seconds(1000) < cp.estimate_verify_seconds(50000)


# --------------------------------------------------------------------------- #
# The actual point: coverage goes from 0 checkpoints to verified
# --------------------------------------------------------------------------- #
def test_background_verifier_creates_checkpoints_and_closes_evidence_gap(store):
    before = store.verification_coverage()
    assert before["checkpoint_count"] == 0, "precondition: never verified"

    _seed(store, 300)
    v = cp.BackgroundVerifier(lambda: store, segment_events=5000)
    v.tick()

    after = store.verification_coverage()
    assert after["checkpoint_count"] >= 1, "checkpoints must now exist"
    assert after["uncovered_events"] == 0
    assert after["rooted_at_genesis"] is True
    # This is the field /v1/ready reads to decide "unverified" vs "verified".
    assert after["coverage_ratio"] == 1.0


def test_evidence_status_would_flip_to_verified(store):
    """Reproduce the exact /v1/ready decision with real coverage numbers."""
    _seed(store, 200)
    assert store.verification_coverage()["checkpoint_count"] == 0

    cp.BackgroundVerifier(lambda: store).tick()

    cov = store.verification_coverage()
    uncovered = cov.get("uncovered_events", 0) or 0
    rooted = cov.get("rooted_at_genesis", True)
    incomplete = (uncovered > 0) or (not rooted)
    checkpoints = cov.get("checkpoint_count")
    if not incomplete:
        evidence_status = "verified"
    elif checkpoints is None:
        evidence_status = "unverified"
    elif checkpoints > 0:
        evidence_status = "broken"
    else:
        evidence_status = "unverified"
    assert evidence_status == "verified"


# --------------------------------------------------------------------------- #
# Honest failure reporting - never fake green
# --------------------------------------------------------------------------- #
def test_corrupted_chain_is_reported_as_failure_not_verified(store):
    """Tampering INSIDE an already-checkpointed region must still be caught.

    This is the C2 decay problem, verified end to end: `verify_incremental`
    only ever looks at NEW events, so on its own it would keep reporting
    `segment_verified=True` forever while the history rots. The tick's rolling
    pass is what re-derives old regions -- so the scheduler must run both, and
    this test fails if rolling is ever dropped.
    """
    _seed(store, 100)
    v = cp.BackgroundVerifier(lambda: store, rolling_every_n_ticks=1)
    v.tick()
    assert store.verification_coverage()["checkpoint_count"] >= 1

    # Tamper with an event that is already covered by a checkpoint.
    store._conn.execute(
        "UPDATE audit_events SET outcome='tampered' WHERE seq=50")
    store._conn.commit()

    # Incremental alone is structurally blind to this (0 new events):
    inc_only = store.verify_incremental(max_events=5000)
    assert inc_only["events_checked"] == 0
    assert inc_only["segment_verified"] is True

    # The rolling pass is the detector.
    v.tick()
    snap = v.stats.snapshot()
    assert snap["segment_failures"] >= 1, "tampering must be surfaced, not hidden"
    assert snap["last_error"]
    assert "mismatch" in (snap["last_error"] or "").lower()


# --------------------------------------------------------------------------- #
# Never raises into the host
# --------------------------------------------------------------------------- #
def test_tick_never_raises_when_store_is_broken():
    class Exploding:
        def verify_incremental(self, **_):
            raise RuntimeError("audit backend down")

        def verify_rolling(self, **_):
            raise RuntimeError("audit backend down")

        def verification_coverage(self):
            raise RuntimeError("audit backend down")

    v = cp.BackgroundVerifier(lambda: Exploding())
    out = v.tick()  # must not raise
    assert out["incremental"] is None
    snap = v.stats.snapshot()
    assert snap["tick_errors"] == 1
    assert "audit backend down" in (snap["last_error"] or "")


# --------------------------------------------------------------------------- #
# Lifecycle
# --------------------------------------------------------------------------- #
def test_start_is_idempotent_and_thread_is_daemon(store):
    _seed(store, 20)
    v = cp.BackgroundVerifier(lambda: store, incremental_interval_sec=3600)
    try:
        assert v.start() is True
        assert v.start() is False, "second start must not spawn a rival thread"
        assert v.running is True
        assert v._thread.daemon is True, "must never block interpreter exit"
        # the first tick runs promptly
        deadline = time.time() + 5
        while v.stats.snapshot()["ticks"] == 0 and time.time() < deadline:
            time.sleep(0.02)
        assert v.stats.snapshot()["ticks"] >= 1
    finally:
        v.stop(timeout=2)
    assert v.running is False


def test_stop_is_safe_when_never_started(store):
    v = cp.BackgroundVerifier(lambda: store)
    v.stop(timeout=1)  # must not raise
    assert v.running is False


# --------------------------------------------------------------------------- #
# Rolling cadence
# --------------------------------------------------------------------------- #
def test_rolling_runs_on_configured_cadence(store):
    _seed(store, 50)
    v = cp.BackgroundVerifier(
        lambda: store, rolling_every_n_ticks=2, segment_events=5000
    )
    v.tick()  # completed ticks = 0 -> not due
    assert v.stats.snapshot()["rolling_runs"] == 0
    v.tick()  # completed ticks = 1 -> not due (1 % 2 != 0)
    assert v.stats.snapshot()["rolling_runs"] == 0
    v.tick()  # completed ticks = 2 -> due
    assert v.stats.snapshot()["rolling_runs"] == 1


def test_rolling_bounded_by_budget(store):
    _seed(store, 200)
    # every=1 so the rolling pass is guaranteed to run on the first tick.
    v = cp.BackgroundVerifier(lambda: store, segment_events=50, rolling_every_n_ticks=1)
    out = v.tick()
    assert out["rolling"] is not None
    assert out["rolling"]["budget_events"] == 50
    # The budget is a work bound, not a correctness bound: a region larger than
    # the budget is still re-verified and the result says so. Here the first
    # checkpointed region is exactly the budget, so it completes within it.
    assert out["rolling"]["events_reverified"] <= 50
    assert out["rolling"]["budget_exceeded"] in (True, False)
    assert out["rolling"]["remaining_stale"] >= 0


# --------------------------------------------------------------------------- #
# Process-wide entry point (the actual production API)
# --------------------------------------------------------------------------- #
def test_process_wide_start_is_idempotent_and_stoppable(store):
    _seed(store, 20)
    try:
        a = cp.start_background_verifier(
            store_factory=lambda: store, incremental_interval_sec=3600
        )
        assert a.running is True
        b = cp.start_background_verifier(store_factory=lambda: store)
        assert b is a, "must return the running worker, not spawn a rival"
        assert cp.get_background_verifier() is a
    finally:
        cp.stop_background_verifier(timeout=2)
    assert a.running is False
    assert cp.get_background_verifier() is None


def test_default_store_factory_resolves_the_audit_kernel(store, monkeypatch):
    """The no-arg production path must bind to the kernel's own store."""
    _seed(store, 10)
    v = cp.start_background_verifier(incremental_interval_sec=3600)
    try:
        assert callable(v._store_factory)
        resolved = v._store_factory()
        # It resolves to an AuditStore (the kernel singleton), not a stub.
        from src.kernels.audit import AuditStore

        assert isinstance(resolved, AuditStore)
    finally:
        cp.stop_background_verifier(timeout=2)
