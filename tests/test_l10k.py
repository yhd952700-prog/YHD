"""
Tests for L10K / VHL benchmark layer (MASTER-SPEC S112-116).

Asserts REAL behaviour: unique deterministic task ids, successful recording,
the two anti-cheat rejections (duplicate record + trivial level), exact VHL
arithmetic (including the honest 0.0 for zero human minutes), level aggregation
with held-out isolation and weight application, and a faithful audit trail.

NO FAKE: rejections really happen; the audit log really records them.
"""

import pytest

from src.ai.l10k import L10KRegistry, TRIVIAL_LEVEL


class TestRegisterTask:
    def test_register_returns_unique_ids(self):
        r = L10KRegistry()
        a = r.register_task("build-feature", "medium")
        b = r.register_task("fix-bug", "easy")
        c = r.register_task("design-arch", "hard")
        assert a != b and b != c and a != c
        # deterministic, monotonic shape
        assert a == "L10K-0001"
        assert b == "L10K-0002"
        assert c == "L10K-0003"

    def test_register_defaults_and_held_out(self):
        r = L10KRegistry()
        t = r.register_task("x", "easy")
        task = r.baseline_report()  # sanity: registry knows the task
        assert task is not None
        # default weight 1.0, held_out False
        info = r.audit_log()[0]
        assert info["weight"] == 1.0
        assert info["held_out"] is False

    def test_register_rejects_unknown_level(self):
        r = L10KRegistry()
        with pytest.raises(ValueError):
            r.register_task("bad", "unknown-level")

    def test_register_rejects_non_positive_weight(self):
        r = L10KRegistry()
        with pytest.raises(ValueError):
            r.register_task("bad", "easy", weight=0.0)


class TestRecordVerifiedOutput:
    def test_record_success_counts(self):
        r = L10KRegistry()
        t = r.register_task("medium-task", "medium")
        assert r.record_verified_output(t, 2.0) is True

    def test_record_unknown_task_refused(self):
        r = L10KRegistry()
        assert r.record_verified_output("L10K-9999", 1.0) is False

    def test_duplicate_record_same_task_rejected(self):
        r = L10KRegistry()
        t = r.register_task("repeatable", "medium")
        assert r.record_verified_output(t, 1.0) is True
        # replaying the same task must NOT add to verified units
        assert r.record_verified_output(t, 1.0) is False
        report = r.baseline_report()
        assert report["main"]["verified_units"] == pytest.approx(1.0)

    def test_trivial_task_refused_and_not_counted(self):
        r = L10KRegistry()
        t = r.register_task("trivial-chore", TRIVIAL_LEVEL)
        assert r.record_verified_output(t, 5.0) is False
        report = r.baseline_report()
        assert report["main"]["verified_units"] == pytest.approx(0.0)
        assert report["total_recorded_tasks"] == 0

    def test_invalid_verified_value_refused(self):
        r = L10KRegistry()
        t = r.register_task("neg", "easy")
        assert r.record_verified_output(t, 0.0) is False
        assert r.record_verified_output(t, -3.0) is False


class TestComputeVHL:
    def test_vhl_correct_ratio(self):
        assert L10KRegistry.compute_vhl(10.0, 1.0) == pytest.approx(10.0)
        assert L10KRegistry.compute_vhl(20.0, 2.0) == pytest.approx(10.0)

    def test_vhl_zero_minutes_returns_zero(self):
        assert L10KRegistry.compute_vhl(10.0, 0.0) == 0.0
        assert L10KRegistry.compute_vhl(10.0, -5.0) == 0.0

    def test_vhl_rejects_negative_units(self):
        with pytest.raises(ValueError):
            L10KRegistry.compute_vhl(-1.0, 1.0)


class TestBaselineReport:
    def test_level_aggregation_and_weighting(self):
        r = L10KRegistry()
        easy = r.register_task("e", "easy", weight=1.0)
        hard = r.register_task("h", "hard", weight=3.0)
        r.record_verified_output(easy, 1.0)  # weighted 1.0
        r.record_verified_output(hard, 2.0)  # weighted 6.0
        report = r.baseline_report()
        assert report["main"]["verified_units"] == pytest.approx(3.0)
        assert report["main"]["weighted_units"] == pytest.approx(7.0)
        assert report["levels"]["easy"]["verified_units"] == pytest.approx(1.0)
        assert report["levels"]["hard"]["verified_units"] == pytest.approx(2.0)
        assert report["levels"]["hard"]["weighted_units"] == pytest.approx(6.0)

    def test_held_out_isolated_from_main(self):
        r = L10KRegistry()
        main_t = r.register_task("m", "medium")
        ho_t = r.register_task("ho", "hard", weight=2.0, held_out=True)
        r.record_verified_output(main_t, 4.0)
        r.record_verified_output(ho_t, 8.0)  # weighted 16.0
        report = r.baseline_report()
        # held-out task must NOT inflate the headline main metric
        assert report["main"]["verified_units"] == pytest.approx(4.0)
        assert report["main"]["weighted_units"] == pytest.approx(4.0)
        # but it is preserved, isolated, in its own bucket
        assert report["held_out"]["verified_units"] == pytest.approx(8.0)
        assert report["held_out"]["weighted_units"] == pytest.approx(16.0)
        # total recorded counts both
        assert report["total_recorded_tasks"] == 2

    def test_reproducible_and_auditable_flags(self):
        r = L10KRegistry()
        r.register_task("a", "easy")
        report = r.baseline_report()
        assert report["reproducible"] is True
        assert report["auditable"] is True
        assert report["spec"].startswith("MASTER-SPEC")
        assert report["target"] == 10000


class TestAuditLog:
    def test_audit_records_register_and_record(self):
        r = L10KRegistry()
        t = r.register_task("x", "medium")
        r.record_verified_output(t, 1.0)
        log = r.audit_log()
        actions = [e["action"] for e in log]
        assert "register" in actions
        assert "record" in actions
        reg = next(e for e in log if e["action"] == "register")
        rec = next(e for e in log if e["action"] == "record")
        assert reg["task_id"] == t
        assert rec["outcome"] == "counted"
        assert rec["verified_value"] == pytest.approx(1.0)

    def test_audit_records_trivial_and_duplicate_refusals(self):
        r = L10KRegistry()
        tv = r.register_task("t", TRIVIAL_LEVEL)
        r.record_verified_output(tv, 1.0)
        m = r.register_task("m", "medium")
        r.record_verified_output(m, 1.0)
        r.record_verified_output(m, 1.0)  # duplicate
        log = r.audit_log()
        reasons = [e.get("reason") for e in log if e["action"] == "record"]
        assert "trivial_level_not_counted" in reasons
        assert "duplicate_record_rejected" in reasons
        # trivial refusal must NOT be "counted"
        counted = [e for e in log if e.get("outcome") == "counted"]
        assert len(counted) == 1
