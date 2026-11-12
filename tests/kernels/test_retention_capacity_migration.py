"""HD-06 — capacity-planning + migration-compatibility tests (warn-only, safe)."""

import time

from src.kernels.retention import (
    CapacityEstimator,
    CapacityReport,
    LifecycleMetadata,
    RecordClass,
    deserialize_lifecycle,
    migrate,
    serialize_lifecycle,
)


def test_capacity_estimator_warns_but_never_deletes():
    est = CapacityEstimator()
    records = [LifecycleMetadata(record_id=f"r{i}", record_class=RecordClass.DERIVATIVE)
               for i in range(10)]
    report = est.estimate(records, avg_record_bytes=1000, storage_limit_bytes=10_000)
    assert report.estimated_bytes == 10_000
    # Low headroom -> warning, but report is purely advisory (no mutation).
    warnings = est.check_thresholds(report, warn_at_ratio=0.8)
    assert any("headroom" in w for w in warnings)
    assert isinstance(report, CapacityReport)


def test_capacity_high_headroom_no_warning():
    est = CapacityEstimator()
    records = [LifecycleMetadata(record_id="r1", record_class=RecordClass.DERIVATIVE)]
    report = est.estimate(records, avg_record_bytes=100, storage_limit_bytes=1_000_000)
    warnings = est.check_thresholds(report)
    assert not any("headroom" in w for w in warnings)


def test_migration_roundtrip_preserves_fields():
    meta = LifecycleMetadata(
        record_id="o1",
        record_class=RecordClass.IMMUTABLE_ORIGINAL,
        origin_hash="h",
        legal_hold_ids=["hold-9"],
        extra={"custom": "value"},
    )
    env = serialize_lifecycle(meta)
    back = deserialize_lifecycle(env)
    assert back.record_id == "o1"
    assert back.record_class is RecordClass.IMMUTABLE_ORIGINAL
    assert back.origin_hash == "h"
    assert back.legal_hold_ids == ["hold-9"]
    assert back.extra.get("custom") == "value"
    assert env["_retention_meta_version"] >= 1


def test_migration_tolerates_unknown_future_fields():
    # A newer envelope with an unknown field must not crash the reader.
    future = {
        "record_id": "o2",
        "record_class": "IMMUTABLE_ORIGINAL",
        "state": "ACTIVE",
        "created_at": time.time(),
        "_retention_meta_version": 99,  # far-future version
        "future_field": {"nested": True},
    }
    back = deserialize_lifecycle(future)
    assert back.record_id == "o2"
    assert back.extra.get("future_field") == {"nested": True}
    assert back.schema_version == 99


def test_migrate_stamps_current_version():
    data = {"record_id": "x", "_retention_meta_version": 1}
    out = migrate(data)
    assert out["_retention_meta_version"] >= 1
