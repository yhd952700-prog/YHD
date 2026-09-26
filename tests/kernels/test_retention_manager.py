"""HD-06 — retention manager tests (immutable-history invariant enforcement)."""

import time

from src.kernels.retention import (
    LifecycleState,
    RecordClass,
    RetentionConfig,
    RetentionMode,
    RetentionManager,
)
from src.kernels.retention.policy import RetentionDecision, RetentionPolicy


def _sink():
    events = []
    return events, (lambda e, d: events.append((e, d)))


def _manager(mode=RetentionMode.NO_DELETE, delete_after_days=None, sink=None):
    cfg = RetentionConfig(mode=mode, delete_after_days=delete_after_days)
    return RetentionManager(config=cfg, event_sink=sink or (lambda e, d: None))


def test_register_original_refuses_overwrite():
    mgr = _manager()
    mgr.register_original("orig-1", b"payload-a", origin_hash="h1")
    with __import__("pytest").raises(ValueError):
        mgr.register_original("orig-1", b"payload-b", origin_hash="h2")
    assert mgr.get("orig-1").origin_hash == "h1"


class _AggressivePolicy(RetentionPolicy):
    """A policy that would delete ANY old record, including immutable originals.

    Used to prove the manager's hard guard is the genuine enforcement point:
    even an aggressive policy cannot destroy an original.
    """

    def decide(self, meta, config, now):
        age_days = (now - meta.created_at) / 86400.0
        if config.delete_after_days and age_days >= config.delete_after_days:
            return RetentionDecision(
                "delete_derivative", "delete everything past horizon",
                LifecycleState.PURGED, deletable_target=True,
            )
        return RetentionDecision("keep", "within horizon", meta.state)


def test_original_never_deleted_even_in_delete_after_days():
    events, sink = _sink()
    # The shipped ConfigurableRetentionPolicy already protects originals; here we
    # install an aggressive policy that *asks* to delete the old original, so the
    # manager guard is what actually refuses and protects it.
    cfg = RetentionConfig(mode=RetentionMode.DELETE_AFTER_DAYS, delete_after_days=1)
    mgr = RetentionManager(config=cfg, policy=_AggressivePolicy(), event_sink=sink)
    mgr.register_original("orig-old", b"x", origin_hash="h")
    meta = mgr.get("orig-old")
    meta.created_at = time.time() - 100 * 86400.0  # 100 days old
    decision = mgr.evaluate("orig-old")
    assert decision.action == "keep"        # guard rewrote the aggressive ask
    assert meta.state is LifecycleState.ACTIVE  # unchanged
    assert mgr.metrics.immutable_protected == 1  # guard fired


def test_derivative_purged_in_delete_after_days():
    mgr = _manager(mode=RetentionMode.DELETE_AFTER_DAYS, delete_after_days=1)
    mgr.register_derivative("deriv-1", "orig-1", b"x")
    meta = mgr.get("deriv-1")
    meta.created_at = time.time() - 100 * 86400.0
    decision = mgr.evaluate("deriv-1")
    assert decision.action == "delete_derivative"
    assert meta.state is LifecycleState.PURGED
    assert mgr.metrics.deletes_derivative == 1


def test_no_delete_mode_keeps_everything():
    mgr = _manager(mode=RetentionMode.NO_DELETE)
    mgr.register_original("o", b"x")
    mgr.register_derivative("d", "o", b"x")
    mgr.evaluate("o")
    mgr.evaluate("d")
    assert mgr.get("o").state is LifecycleState.ACTIVE
    assert mgr.get("d").state is LifecycleState.ACTIVE
    assert mgr.metrics.keeps == 2


def test_archive_only_copies_not_destroys():
    mgr = _manager(mode=RetentionMode.ARCHIVE_ONLY)
    mgr.register_original("o", b"original-bytes")
    decision = mgr.evaluate("o")
    assert decision.action == "archive"
    # Original still present and retained; a cold copy exists.
    assert mgr.get("o") is not None
    assert mgr.get("o").state is LifecycleState.ARCHIVED
    assert mgr._archiver.backend().exists("o") is True
    assert mgr.metrics.archives == 1


def test_legal_hold_blocks_purge():
    mgr = _manager(mode=RetentionMode.DELETE_AFTER_DAYS, delete_after_days=1)
    mgr.register_derivative("d", "o", b"x")
    meta = mgr.get("d")
    meta.created_at = time.time() - 100 * 86400.0
    mgr.place_legal_hold("litigation hold", "legal-team", scope="d")
    decision = mgr.evaluate("d")
    assert decision.action == "blocked_by_hold"
    assert meta.state is LifecycleState.HELD
    assert mgr.metrics.blocked_by_hold == 1
    # Release and re-evaluate -> now purgeable.
    hold_ids = [h.hold_id for h in mgr.holds.active_holds()]
    assert mgr.release_legal_hold(hold_ids[0])
    decision2 = mgr.evaluate("d")
    assert decision2.action == "delete_derivative"


def test_summary_reports_state_and_metrics():
    mgr = _manager()
    mgr.register_original("o", b"x")
    mgr.evaluate("o")
    summary = mgr.summary()
    assert summary["mode"] == RetentionMode.NO_DELETE.value
    assert summary["protect_immutable_originals"] is True
    assert summary["record_count"] == 1
    assert summary["by_class"].get(RecordClass.IMMUTABLE_ORIGINAL.value) == 1
