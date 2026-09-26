"""HD-06 — retention policy interface tests (each implementation + no-delete default)."""

import time

from src.kernels.retention import (
    ArchiveOnlyPolicy,
    ConfigurableRetentionPolicy,
    LifecycleMetadata,
    LifecycleState,
    NoDeletePolicy,
    RecordClass,
    RetentionConfig,
    RetentionMode,
)
from src.kernels.retention.policy import RetentionDecision


def _meta(record_class=RecordClass.IMMUTABLE_ORIGINAL, state=LifecycleState.ACTIVE, age_days=0):
    return LifecycleMetadata(
        record_id="r1",
        record_class=record_class,
        state=state,
        created_at=time.time() - age_days * 86400.0,
    )


def test_nodelete_policy_keeps_everything():
    cfg = RetentionConfig(mode=RetentionMode.NO_DELETE)
    for meta in [
        _meta(RecordClass.IMMUTABLE_ORIGINAL),
        _meta(RecordClass.DERIVATIVE),
        _meta(RecordClass.IMMUTABLE_ORIGINAL, state=LifecycleState.HELD),
    ]:
        d = NoDeletePolicy().decide(meta, cfg, time.time())
        assert d.action == "keep"
        assert d.next_state == meta.state


def test_nodelete_policy_blocks_when_held():
    cfg = RetentionConfig(mode=RetentionMode.NO_DELETE)
    meta = _meta(RecordClass.DERIVATIVE, state=LifecycleState.HELD)
    meta.legal_hold_ids.append("hold-1")
    d = NoDeletePolicy().decide(meta, cfg, time.time())
    assert d.action == "blocked_by_hold"


def test_archive_only_copies_not_deletes():
    cfg = RetentionConfig(mode=RetentionMode.ARCHIVE_ONLY)
    meta = _meta(RecordClass.IMMUTABLE_ORIGINAL, state=LifecycleState.ACTIVE)
    d = ArchiveOnlyPolicy().decide(meta, cfg, time.time())
    assert d.action == "archive"
    assert d.next_state is LifecycleState.ARCHIVED


def test_configurable_delete_after_days_keeps_originals():
    cfg = RetentionConfig(mode=RetentionMode.DELETE_AFTER_DAYS, delete_after_days=1)
    old_original = _meta(RecordClass.IMMUTABLE_ORIGINAL, age_days=10)
    d = ConfigurableRetentionPolicy().decide(old_original, cfg, time.time())
    assert d.action == "keep"
    assert "IMMUTABLE_ORIGINAL" in d.rationale


def test_configurable_delete_after_days_purges_derivatives():
    cfg = RetentionConfig(mode=RetentionMode.DELETE_AFTER_DAYS, delete_after_days=1)
    old_derivative = _meta(RecordClass.DERIVATIVE, age_days=10)
    d = ConfigurableRetentionPolicy().decide(old_derivative, cfg, time.time())
    assert d.action == "delete_derivative"
    assert d.deletable_target is True


def test_configurable_delete_after_days_within_horizon_keeps():
    cfg = RetentionConfig(mode=RetentionMode.DELETE_AFTER_DAYS, delete_after_days=30)
    young = _meta(RecordClass.DERIVATIVE, age_days=5)
    d = ConfigurableRetentionPolicy().decide(young, cfg, time.time())
    assert d.action == "keep"


def test_configurable_dispatches_no_delete():
    cfg = RetentionConfig(mode=RetentionMode.NO_DELETE)
    d = ConfigurableRetentionPolicy().decide(
        _meta(RecordClass.DERIVATIVE), cfg, time.time()
    )
    assert d.action == "keep"


def test_policy_interface_is_implemented():
    # Every policy exposes decide() returning a RetentionDecision.
    cfg = RetentionConfig()
    meta = _meta()
    for policy in (NoDeletePolicy(), ArchiveOnlyPolicy(), ConfigurableRetentionPolicy()):
        out = policy.decide(meta, cfg, time.time())
        assert isinstance(out, RetentionDecision)
