"""
Tests for the Evolution engine (MASTER-SPEC 80).

Asserts REAL behaviour of the guarded, audited self-improvement pipeline:
- propose starts in PROPOSED;
- benchmark computes real improvement (positive and negative);
- approve refuses any experiment that is not benchmarked or not improved;
- deploy refuses anything not approved (core "no self-modification without
  evaluation" guard) and only succeeds when benchmark + approve + improved all
  hold;
- rollback / reject transition state correctly;
- history records a complete, append-only audit trail.

No fakes. Assert behaviour, not mere existence.
"""

import pytest

from src.ai.evolution import (
    ExperimentStatus,
    EvolutionEngine,
)


# ===================== propose (§80) =====================
class TestPropose:
    def test_propose_starts_in_proposed(self):
        eng = EvolutionEngine()
        eid = eng.propose("faster planner", baseline_metric=0.5, proposed_change="cache")
        exp = eng.get(eid)
        assert exp.status == ExperimentStatus.PROPOSED
        assert exp.baseline_metric == pytest.approx(0.5)
        assert exp.observed_metric is None
        assert exp.approver is None

    def test_propose_records_initial_history(self):
        eng = EvolutionEngine()
        eid = eng.propose("desc", baseline_metric=1.0, proposed_change="x")
        hist = eng.history(eid)
        assert len(hist) == 1
        assert hist[0]["event"] == "proposed"
        assert hist[0]["baseline_metric"] == pytest.approx(1.0)


# ===================== benchmark (§80) =====================
class TestBenchmark:
    def test_benchmark_positive_improvement(self):
        eng = EvolutionEngine()
        eid = eng.propose("desc", baseline_metric=1.0, proposed_change="x")
        res = eng.benchmark(eid, observed_metric=1.4)
        assert res["baseline"] == pytest.approx(1.0)
        assert res["observed"] == pytest.approx(1.4)
        assert res["improvement"] == pytest.approx(0.4)
        assert res["improved"] is True
        assert eng.get(eid).status == ExperimentStatus.RUNNING
        assert eng.get(eid).is_improved() is True

    def test_benchmark_negative_improvement(self):
        eng = EvolutionEngine()
        eid = eng.propose("desc", baseline_metric=1.0, proposed_change="x")
        res = eng.benchmark(eid, observed_metric=0.7)
        assert res["improvement"] == pytest.approx(-0.3)
        assert res["improved"] is False
        assert eng.get(eid).is_improved() is False

    def test_benchmark_equal_is_not_improved(self):
        eng = EvolutionEngine()
        eid = eng.propose("desc", baseline_metric=1.0, proposed_change="x")
        res = eng.benchmark(eid, observed_metric=1.0)
        assert res["improvement"] == pytest.approx(0.0)
        assert res["improved"] is False


# ===================== approve (§80) =====================
class TestApprove:
    def test_approve_refuses_unbenchmarked(self):
        eng = EvolutionEngine()
        eid = eng.propose("desc", baseline_metric=1.0, proposed_change="x")
        # never benchmarked
        assert eng.approve(eid, approver="alice") is False
        assert eng.get(eid).status == ExperimentStatus.PROPOSED
        assert eng.get(eid).approver is None

    def test_approve_refuses_not_improved(self):
        eng = EvolutionEngine()
        eid = eng.propose("desc", baseline_metric=1.0, proposed_change="x")
        eng.benchmark(eid, observed_metric=0.8)  # worse
        assert eng.approve(eid, approver="alice") is False
        assert eng.get(eid).status == ExperimentStatus.RUNNING
        assert eng.get(eid).approver is None

    def test_approve_succeeds_when_benchmarked_and_improved(self):
        eng = EvolutionEngine()
        eid = eng.propose("desc", baseline_metric=1.0, proposed_change="x")
        eng.benchmark(eid, observed_metric=1.5)
        assert eng.approve(eid, approver="alice") is True
        exp = eng.get(eid)
        assert exp.status == ExperimentStatus.APPROVED
        assert exp.approver == "alice"


# ===================== deploy guard (§80 core) =====================
class TestDeployGuard:
    def test_deploy_refuses_unapproved(self):
        eng = EvolutionEngine()
        eid = eng.propose("desc", baseline_metric=1.0, proposed_change="x")
        eng.benchmark(eid, observed_metric=1.5)  # benchmarked + improved, but no approve
        assert eng.deploy(eid) is False
        assert eng.get(eid).status == ExperimentStatus.RUNNING  # untouched

    def test_deploy_refuses_no_benchmark(self):
        eng = EvolutionEngine()
        eid = eng.propose("desc", baseline_metric=1.0, proposed_change="x")
        # approved path cannot even be reached without benchmark, but assert the
        # guard rejects outright if somehow approved flag set without benchmark
        assert eng.deploy(eid) is False
        assert eng.get(eid).status == ExperimentStatus.PROPOSED

    def test_deploy_refuses_not_improved(self):
        eng = EvolutionEngine()
        eid = eng.propose("desc", baseline_metric=1.0, proposed_change="x")
        eng.benchmark(eid, observed_metric=0.8)  # benchmarked but worse
        assert eng.approve(eid, approver="alice") is False  # cannot approve
        assert eng.deploy(eid) is False
        assert eng.get(eid).status == ExperimentStatus.RUNNING

    def test_deploy_succeeds_when_benchmarked_approved_improved(self):
        eng = EvolutionEngine()
        eid = eng.propose("desc", baseline_metric=1.0, proposed_change="x")
        eng.benchmark(eid, observed_metric=1.5)
        eng.approve(eid, approver="alice")
        assert eng.deploy(eid) is True
        assert eng.get(eid).status == ExperimentStatus.APPLIED

    def test_deploy_requires_all_three_conditions_no_shortcut(self):
        eng = EvolutionEngine()

        # (a) benchmark + improve but no approval -> no deploy
        a = eng.propose("a", baseline_metric=1.0, proposed_change="x")
        eng.benchmark(a, observed_metric=2.0)
        assert eng.deploy(a) is False, "must not deploy without approval"

        # (b) approve but no benchmark -> no deploy
        b = eng.propose("b", baseline_metric=1.0, proposed_change="x")
        # force a fake approval by directly mutating state to prove the guard
        eng._experiments[b].status = ExperimentStatus.APPROVED
        assert eng.deploy(b) is False, "must not deploy without benchmark"

        # (c) benchmark + approve but no improvement -> no deploy
        c = eng.propose("c", baseline_metric=1.0, proposed_change="x")
        eng.benchmark(c, observed_metric=0.9)
        eng._experiments[c].status = ExperimentStatus.APPROVED
        assert eng.deploy(c) is False, "must not deploy without improvement"

        # After deploy, all three must hold
        for eid in (a, b, c):
            assert eng.get(eid).status != ExperimentStatus.APPLIED


# ===================== rollback / reject (§80) =====================
class TestRollbackAndReject:
    def test_rollback_only_from_applied(self):
        eng = EvolutionEngine()
        eid = eng.propose("desc", baseline_metric=1.0, proposed_change="x")
        eng.benchmark(eid, observed_metric=1.5)
        eng.approve(eid, approver="alice")
        # not yet deployed
        assert eng.rollback(eid) is False
        assert eng.get(eid).status == ExperimentStatus.APPROVED
        # now deploy then roll back
        assert eng.deploy(eid) is True
        assert eng.rollback(eid) is True
        assert eng.get(eid).status == ExperimentStatus.ROLLED_BACK

    def test_reject_sets_rejected(self):
        eng = EvolutionEngine()
        eid = eng.propose("desc", baseline_metric=1.0, proposed_change="x")
        assert eng.reject(eid) is True
        assert eng.get(eid).status == ExperimentStatus.REJECTED

    def test_reject_from_running(self):
        eng = EvolutionEngine()
        eid = eng.propose("desc", baseline_metric=1.0, proposed_change="x")
        eng.benchmark(eid, observed_metric=0.5)
        assert eng.reject(eid) is True
        assert eng.get(eid).status == ExperimentStatus.REJECTED

    def test_reject_terminal_is_false(self):
        eng = EvolutionEngine()
        eid = eng.propose("desc", baseline_metric=1.0, proposed_change="x")
        eng.reject(eid)
        # already REJECTED -> cannot reject again
        assert eng.reject(eid) is False


# ===================== monitor (§80) =====================
class TestMonitor:
    def test_monitor_after_deploy(self):
        eng = EvolutionEngine()
        eid = eng.propose("desc", baseline_metric=1.0, proposed_change="x")
        eng.benchmark(eid, observed_metric=1.5)
        eng.approve(eid, approver="alice")
        eng.deploy(eid)
        res = eng.monitor(eid, metric=1.6)
        assert res["baseline"] == pytest.approx(1.0)
        assert res["benchmark_observed"] == pytest.approx(1.5)
        assert res["deployed_metric"] == pytest.approx(1.6)
        assert res["delta_vs_baseline"] == pytest.approx(0.6)

    def test_monitor_rejects_non_applied(self):
        eng = EvolutionEngine()
        eid = eng.propose("desc", baseline_metric=1.0, proposed_change="x")
        with pytest.raises(ValueError):
            eng.monitor(eid, metric=1.0)


# ===================== history (§80 audit) =====================
class TestHistory:
    def test_history_is_complete_audit_trail(self):
        eng = EvolutionEngine()
        eid = eng.propose("desc", baseline_metric=1.0, proposed_change="x")
        eng.benchmark(eid, observed_metric=1.5)
        eng.approve(eid, approver="bob")
        eng.deploy(eid)
        eng.monitor(eid, metric=1.4)
        events = [h["event"] for h in eng.history(eid)]
        assert events == [
            "proposed",
            "benchmarked",
            "approved",
            "deployed",
            "monitored",
        ]
        # the approved event captured the approver
        approved = [h for h in eng.history(eid) if h["event"] == "approved"][0]
        assert approved["approver"] == "bob"
        # the deployed event captured the realized improvement
        deployed = [h for h in eng.history(eid) if h["event"] == "deployed"][0]
        assert deployed["improvement"] == pytest.approx(0.5)
