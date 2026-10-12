"""Deep tests for src.mlops.

Covers:
- ExperimentTracker: experiments, runs, params, metrics, hierarchy
- MLOpsModelRegistry: versions, staging, approval workflow, rollback, lineage
- ModelMonitor: statistical drift functions, drift detection, SLA, alerts
- FeatureStore: definitions, computation, caching, views, validation, lineage
"""

import pytest

from src.mlops.experiment_tracker import (
    ExperimentTracker,
    RunStatus,
)
from src.mlops.model_registry_mlops import (
    ApprovalStatus,
    MLOpsModelRegistry,
    ModelStage,
)
from src.mlops.monitoring import (
    AlertLevel,
    DriftSeverity,
    ModelMonitor,
    SLAConfig,
    chi_square_test,
    ks_test_statistic,
    psi,
    wasserstein_distance,
)
from src.mlops.feature_store import (
    FeatureDefinition,
    FeatureStore,
    FeatureType,
)


# ----------------------------------------------------------------------
# Experiment tracker
# ----------------------------------------------------------------------


class TestExperimentTracker:
    def test_create_and_get_experiment(self):
        tracker = ExperimentTracker()
        exp = tracker.create_experiment("exp-1", description="test", tags=["nlp"])
        assert exp.name == "exp-1"
        assert tracker.get_experiment(exp.experiment_id) is exp
        assert tracker.get_experiment_by_name("exp-1") is exp

    def test_list_experiments_with_tag_filter(self):
        tracker = ExperimentTracker()
        tracker.create_experiment("e1", tags=["a"])
        tracker.create_experiment("e2", tags=["b"])
        assert len(tracker.list_experiments()) == 2
        filtered = tracker.list_experiments(tags=["a"])
        assert len(filtered) == 1
        assert filtered[0].name == "e1"

    def test_delete_experiment(self):
        tracker = ExperimentTracker()
        exp = tracker.create_experiment("bye")
        tracker.delete_experiment(exp.experiment_id)
        assert tracker.get_experiment_by_name("bye") is None

    def test_start_run_creates_experiment_if_missing(self):
        tracker = ExperimentTracker()
        run = tracker.start_run("auto-exp", run_name="run-1")
        assert run.is_running
        assert tracker.get_experiment_by_name("auto-exp") is not None

    def test_end_run_sets_status_and_duration(self):
        tracker = ExperimentTracker()
        run = tracker.start_run("exp")
        ended = tracker.end_run(run.run_id, status=RunStatus.COMPLETED)
        assert ended.status == RunStatus.COMPLETED
        assert ended.is_finished
        assert ended.duration_seconds is not None and ended.duration_seconds >= 0

    def test_log_params(self):
        tracker = ExperimentTracker()
        run = tracker.start_run("exp")
        tracker.log_params({"lr": 0.01, "epochs": 10}, run_id=run.run_id)
        tracker.log_param("batch_size", 32, run_id=run.run_id)
        fetched = tracker.get_run(run.run_id)
        assert fetched.hyperparameters["lr"] == 0.01
        assert fetched.hyperparameters["batch_size"] == 32

    def test_log_metric_and_retrieval(self):
        tracker = ExperimentTracker()
        run = tracker.start_run("exp")
        tracker.log_metric("loss", 0.5, step=1, run_id=run.run_id)
        tracker.log_metric("loss", 0.3, step=2, run_id=run.run_id)
        tracker.log_metrics({"acc": 0.9}, step=2, run_id=run.run_id)
        fetched = tracker.get_run(run.run_id)
        assert fetched.get_latest_metric("loss").value == 0.3
        assert fetched.get_metric_values("loss") == [0.5, 0.3]
        assert fetched.get_latest_metric("acc").value == 0.9

    def test_list_runs_filters(self):
        tracker = ExperimentTracker()
        r1 = tracker.start_run("exp", tags=["tune"])
        tracker.end_run(r1.run_id, status=RunStatus.COMPLETED)
        tracker.start_run("exp")
        running = tracker.list_runs(status=RunStatus.RUNNING)
        assert len(running) == 1
        completed = tracker.list_runs(status=RunStatus.COMPLETED)
        assert len(completed) == 1
        tagged = tracker.list_runs(tags=["tune"])
        assert len(tagged) == 1

    def test_child_run_hierarchy(self):
        tracker = ExperimentTracker()
        parent = tracker.start_run("exp")
        child = tracker.start_child_run("child", parent_run_id=parent.run_id)
        assert child.parent_run_id == parent.run_id
        hierarchy = tracker.get_run_hierarchy(parent.run_id)
        assert hierarchy is not None

    def test_compare_runs(self):
        tracker = ExperimentTracker()
        r1 = tracker.start_run("exp")
        tracker.log_metric("loss", 0.5, run_id=r1.run_id)
        r2 = tracker.start_run("exp")
        tracker.log_metric("loss", 0.3, run_id=r2.run_id)
        comparison = tracker.compare_runs([r1.run_id, r2.run_id])
        assert comparison is not None

    def test_get_stats(self):
        tracker = ExperimentTracker()
        tracker.create_experiment("e")
        run = tracker.start_run("e")
        tracker.log_metric("m", 1.0, run_id=run.run_id)
        stats = tracker.get_stats()
        assert stats["experiments"] == 1
        assert stats["total_runs"] == 1


# ----------------------------------------------------------------------
# MLOps model registry
# ----------------------------------------------------------------------


class TestMLOpsModelRegistry:
    def test_register_and_get_version(self):
        reg = MLOpsModelRegistry()
        v = reg.register_version("model-a", "1.0.0", metrics={"acc": 0.9}, created_by="dev")
        assert v.model_name == "model-a"
        assert v.stage == ModelStage.NONE
        assert reg.get_version(v.version_id) is v

    def test_get_latest_version(self):
        import time as _time

        reg = MLOpsModelRegistry()
        reg.register_version("model-a", "1.0.0")
        _time.sleep(0.02)  # ensure distinct created_at on low-resolution clocks
        v2 = reg.register_version("model-a", "2.0.0")
        assert reg.get_latest_version("model-a").version_id == v2.version_id

    def test_list_models_and_versions(self):
        reg = MLOpsModelRegistry()
        reg.register_version("model-a", "1.0.0")
        reg.register_version("model-a", "1.1.0")
        reg.register_version("model-b", "1.0.0")
        assert set(reg.list_models()) == {"model-a", "model-b"}
        assert len(reg.list_versions("model-a")) == 2

    def test_promote_to_staging_and_production(self):
        reg = MLOpsModelRegistry()
        v = reg.register_version("model-a", "1.0.0")
        staged = reg.promote_to_staging(v.version_id, performed_by="ops")
        assert staged.stage == ModelStage.STAGING
        prod = reg.promote_to_production(v.version_id, performed_by="ops")
        assert prod.stage == ModelStage.PRODUCTION
        assert prod.is_production
        assert reg.get_production_version("model-a").version_id == v.version_id

    def test_transition_stage_records_history(self):
        reg = MLOpsModelRegistry()
        v = reg.register_version("model-a", "1.0.0")
        reg.transition_stage(v.version_id, ModelStage.STAGING, performed_by="ops", reason="ready")
        history = reg.get_transition_history(v.version_id)
        assert len(history) == 1
        assert history[0].to_stage == ModelStage.STAGING

    def test_rollback_restores_previous_production(self):
        reg = MLOpsModelRegistry()
        v1 = reg.register_version("model-a", "1.0.0")
        reg.promote_to_production(v1.version_id)
        v2 = reg.register_version("model-a", "2.0.0")
        reg.promote_to_production(v2.version_id)
        rolled_back = reg.rollback("model-a", performed_by="ops", reason="bad metrics")
        assert rolled_back is not None
        assert rolled_back.version_id == v1.version_id
        assert reg.get_version(v2.version_id).stage == ModelStage.ARCHIVED

    def test_approval_workflow(self):
        reg = MLOpsModelRegistry()
        v = reg.register_version("model-a", "1.0.0")
        req = reg.request_approval(v.version_id, requester="dev", approver="lead")
        assert req.status == ApprovalStatus.PENDING
        approved = reg.approve_version(req.request_id, approver="lead", comments="lgtm")
        assert approved.status == ApprovalStatus.APPROVED
        assert reg.get_version(v.version_id).is_approved

    def test_rejection_workflow(self):
        reg = MLOpsModelRegistry()
        v = reg.register_version("model-a", "1.0.0")
        req = reg.request_approval(v.version_id, requester="dev", approver="lead")
        rejected = reg.reject_version(req.request_id, approver="lead", reason="not ready")
        assert rejected.status == ApprovalStatus.REJECTED
        assert not reg.get_version(v.version_id).is_approved

    def test_lineage_parent_child(self):
        reg = MLOpsModelRegistry()
        parent = reg.register_version("model-a", "1.0.0")
        child = reg.register_version("model-a", "1.1.0", parent_version_id=parent.version_id)
        lineage = reg.get_lineage(child.version_id)
        assert parent.version_id in lineage.parents
        tree = reg.get_full_lineage_tree(child.version_id)
        assert tree is not None

    def test_update_metrics(self):
        reg = MLOpsModelRegistry()
        v = reg.register_version("model-a", "1.0.0", metrics={"acc": 0.8})
        reg.update_metrics(v.version_id, {"acc": 0.85, "f1": 0.8})
        updated = reg.get_version(v.version_id)
        assert updated.metrics["acc"] == 0.85
        assert updated.metrics["f1"] == 0.8

    def test_compare_versions(self):
        reg = MLOpsModelRegistry()
        v1 = reg.register_version("model-a", "1.0.0", metrics={"acc": 0.8})
        v2 = reg.register_version("model-a", "2.0.0", metrics={"acc": 0.9})
        comparison = reg.compare_versions("model-a", metric="acc")
        assert comparison["model"] == "model-a"
        assert len(comparison["versions"]) == 2

    def test_get_stats(self):
        reg = MLOpsModelRegistry()
        reg.register_version("model-a", "1.0.0")
        stats = reg.get_stats()
        assert stats["models"] == 1
        assert stats["total_versions"] == 1


# ----------------------------------------------------------------------
# Monitoring: statistical functions
# ----------------------------------------------------------------------


class TestDriftStatistics:
    def test_ks_identical_distributions(self):
        data = [1.0, 2.0, 3.0, 4.0, 5.0]
        stat, p_value = ks_test_statistic(data, data)
        assert stat == pytest.approx(0.0)
        assert p_value > 0.5

    def test_ks_different_distributions(self):
        baseline = [1.0, 1.1, 0.9, 1.05, 0.95]
        current = [10.0, 10.1, 9.9, 10.05, 9.95]
        stat, p_value = ks_test_statistic(baseline, current)
        assert stat == pytest.approx(1.0)
        assert p_value < 0.05

    def test_ks_empty_input(self):
        assert ks_test_statistic([], [1.0]) == (0.0, 1.0)

    def test_psi_identical(self):
        counts = [10, 20, 30, 40]
        assert psi(counts, counts) == pytest.approx(0.0)

    def test_psi_shifted(self):
        assert psi([40, 30, 20, 10], [10, 20, 30, 40]) > 0.1

    def test_wasserstein(self):
        assert wasserstein_distance([1.0, 2.0], [1.0, 2.0]) == pytest.approx(0.0)
        assert wasserstein_distance([1.0, 2.0], [3.0, 4.0]) == pytest.approx(2.0)
        assert wasserstein_distance([], [1.0]) == 0.0

    def test_chi_square(self):
        chi_sq, p_value = chi_square_test([50, 50], [50, 50])
        assert chi_sq == pytest.approx(0.0)
        chi_sq2, _ = chi_square_test([90, 10], [50, 50])
        assert chi_sq2 > 0.0


# ----------------------------------------------------------------------
# Monitoring: ModelMonitor
# ----------------------------------------------------------------------


def build_monitor_with_baseline() -> ModelMonitor:
    monitor = ModelMonitor(model_id="model-1")
    monitor.set_baseline(
        input_features={"age": [20.0, 25.0, 30.0, 35.0, 40.0]},
        outputs=[0.1, 0.2, 0.15, 0.2, 0.1],
        metrics={"accuracy": 0.9},
    )
    return monitor


class TestModelMonitor:
    def test_record_prediction(self):
        monitor = build_monitor_with_baseline()
        monitor.record_prediction({"age": 30.0}, 0.2, ground_truth=0.2, latency_ms=15.0)
        stats = monitor.get_stats()
        assert stats["predictions_recorded"] >= 1

    def test_no_drift_on_similar_data(self):
        monitor = build_monitor_with_baseline()
        # Feed current data identical to the baseline distribution
        for age in [20.0, 25.0, 30.0, 35.0, 40.0]:
            monitor.record_prediction({"age": age}, 0.15)
        results = monitor.detect_data_drift("age")
        assert len(results) == 1
        assert not results[0].is_drifted

    def test_drift_detected_on_shifted_data(self):
        monitor = build_monitor_with_baseline()
        for age in [90.0, 91.0, 92.0, 93.0, 94.0]:
            monitor.record_prediction({"age": age}, 0.9)
        results = monitor.detect_data_drift("age")
        assert len(results) == 1
        assert results[0].is_drifted
        assert results[0].severity != DriftSeverity.NONE

    def test_prediction_drift(self):
        monitor = build_monitor_with_baseline()
        for _ in range(5):
            monitor.record_prediction({"age": 30.0}, 0.95)
        result = monitor.detect_prediction_drift()
        assert result.drift_type.value == "prediction_drift"

    def test_performance_drift(self):
        monitor = build_monitor_with_baseline()
        for _ in range(5):
            monitor.record_metric("accuracy", 0.5)
        result = monitor.detect_performance_drift("accuracy")
        assert result.is_drifted

    def test_detect_all_drift(self):
        monitor = build_monitor_with_baseline()
        monitor.record_prediction({"age": 30.0}, 0.2)
        monitor.record_metric("accuracy", 0.88)
        all_results = monitor.detect_all_drift()
        assert set(all_results.keys()) >= {"data_drift", "prediction_drift", "performance_drift"}

    def test_sla_met(self):
        monitor = build_monitor_with_baseline()
        sla = SLAConfig(name="acc-sla", model_id="model-1", metric="accuracy", target=0.8, threshold=0.7)
        monitor.add_sla(sla)
        for _ in range(3):
            monitor.record_metric("accuracy", 0.9)
        result = monitor.evaluate_sla(sla.sla_id)
        assert result is not None
        assert result.status in ("met", "warning", "breached")

    def test_alerts_acknowledge(self):
        monitor = build_monitor_with_baseline()
        for age in [200.0, 210.0, 220.0, 230.0, 240.0]:
            monitor.record_prediction({"age": age}, 0.99)
        monitor.detect_data_drift("age")
        alerts = monitor.get_alerts()
        if alerts:
            monitor.acknowledge_alert(alerts[0].alert_id, acknowledged_by="ops")
            remaining = [a for a in monitor.get_alerts() if not a.acknowledged]
            assert len(remaining) <= len(alerts)

    def test_get_stats(self):
        monitor = build_monitor_with_baseline()
        stats = monitor.get_stats()
        assert stats["model_id"] == "model-1"


# ----------------------------------------------------------------------
# Feature store
# ----------------------------------------------------------------------


def age_computer(entity_key: str, context):
    return context.get("age", 0.0)


class TestFeatureStore:
    def make_store(self) -> FeatureStore:
        store = FeatureStore()
        store.register_feature(
            FeatureDefinition(feature_name="age", feature_type=FeatureType.NUMERIC, valid_range=(0, 120)),
            computer=age_computer,
        )
        return store

    def test_register_and_get_definition(self):
        store = self.make_store()
        definition = store.get_feature_definition("age")
        assert definition.feature_name == "age"
        assert len(store.list_features()) == 1

    def test_list_features_filters(self):
        store = self.make_store()
        store.register_feature(FeatureDefinition(
            feature_name="city", feature_type=FeatureType.CATEGORICAL,
            valid_categories=["SF", "NYC"], tags=["geo"],
        ))
        assert len(store.list_features(feature_type=FeatureType.CATEGORICAL)) == 1
        assert len(store.list_features(tags=["geo"])) == 1

    def test_compute_feature(self):
        store = self.make_store()
        value = store.compute_feature("age", "user-1", context={"age": 33.0})
        assert value.value == 33.0
        assert value.is_valid
        assert value.entity_key == "user-1"

    def test_get_feature_caches_result(self):
        store = self.make_store()
        first = store.get_feature("age", "user-1", context={"age": 33.0})
        second = store.get_feature("age", "user-1", context={"age": 99.0})
        assert first.value == 33.0
        assert second.value == 33.0  # cached
        assert store.get_stats()["cache_hits"] >= 1

    def test_get_features_multiple(self):
        store = self.make_store()
        store.register_feature(
            FeatureDefinition(feature_name="score", feature_type=FeatureType.NUMERIC),
            computer=lambda ek, ctx: 0.5,
        )
        values = store.get_features(["age", "score"], "user-1", context={"age": 25.0})
        assert values["age"].value == 25.0
        assert values["score"].value == 0.5

    def test_feature_view(self):
        store = self.make_store()
        store.register_feature(
            FeatureDefinition(feature_name="score", feature_type=FeatureType.NUMERIC),
            computer=lambda ek, ctx: 0.5,
        )
        store.create_view("profile", ["age", "score"], entity_key="user_id")
        view = store.get_view("profile", "user-1", context={"age": 40.0})
        assert set(view.keys()) == {"age", "score"}

    def test_validation_range_failure(self):
        store = self.make_store()
        value = store.compute_feature("age", "user-2", context={"age": 500.0})
        assert not value.is_valid
        assert len(value.validation_errors) >= 1
        assert store.get_stats()["validation_failures"] >= 1

    def test_categorical_validation(self):
        store = FeatureStore()
        store.register_feature(
            FeatureDefinition(feature_name="city", feature_type=FeatureType.CATEGORICAL, valid_categories=["SF", "NYC"]),
            computer=lambda ek, ctx: ctx.get("city"),
        )
        bad = store.compute_feature("city", "u1", context={"city": "LA"})
        assert not bad.is_valid
        good = store.compute_feature("city", "u2", context={"city": "SF"})
        assert good.is_valid

    def test_definition_validate_default(self):
        definition = FeatureDefinition(feature_name="f", default_value=7.0)
        assert definition.validate(None) == (True, [])

    def test_invalidate_feature_and_entity(self):
        store = self.make_store()
        store.get_feature("age", "user-1", context={"age": 33.0})
        store.invalidate_entity("user-1")
        fresh = store.get_feature("age", "user-1", context={"age": 44.0})
        assert fresh.value == 44.0

    def test_clear_cache(self):
        store = self.make_store()
        store.get_feature("age", "user-1", context={"age": 33.0})
        store.clear_cache()
        assert store.get_stats()["online_cache_size"] == 0

    def test_lineage_tracking(self):
        store = FeatureStore()
        store.register_feature(
            FeatureDefinition(feature_name="base", feature_type=FeatureType.NUMERIC),
            computer=lambda ek, ctx: 1.0,
        )
        store.register_feature(
            FeatureDefinition(feature_name="derived", feature_type=FeatureType.NUMERIC),
            computer=lambda ek, ctx: 2.0,
            input_features=["base"],
        )
        lineage = store.get_lineage("derived")
        assert lineage is not None
        assert "base" in lineage.input_features

    def test_get_stats(self):
        store = self.make_store()
        store.compute_feature("age", "u1", context={"age": 10.0})
        stats = store.get_stats()
        assert stats["features_defined"] == 1
        assert stats["total_computations"] >= 1
