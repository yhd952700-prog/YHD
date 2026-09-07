"""Evaluation Kernel unit tests.

Covers: outcome evaluation against criteria (SUCCESS / PARTIAL /
OFF_TRACK / FAILED), value comparators with tolerance, weighted scores,
custom thresholds, critical criteria, feedback generation, replan
triggering, escalation, history/feedback/replan queries, and stats.

Defect-evidence tests (names prefixed with ``test_defect_``) assert the
behavior REQUIRED BY SPEC (Definition Lock section 112). They are
expected to fail until the kernel is fixed.
"""
import pytest

from src.kernels.evaluation import (
    EvaluationCriteria,
    EvaluationOutcome,
    Evaluator,
    FeedbackType,
    get_evaluator,
)


@pytest.fixture
def evaluator() -> Evaluator:
    """Fresh evaluator with default criteria."""
    return Evaluator()


def lenient_criteria(**kwargs):
    """Criteria where only names starting with critical_ are critical."""
    kwargs.setdefault("require_all_critical", False)
    return EvaluationCriteria(**kwargs)


def goal_with(criteria_dict, goal_id="g1"):
    return {"id": goal_id, "expected_outputs": dict(criteria_dict)}


# =====================================================================
# Outcome determination
# =====================================================================

class TestOutcomeDetermination:
    def test_all_criteria_met_is_success(self, evaluator):
        result = evaluator.evaluate(
            goal_with({"a": 1, "b": 2}), {"a": 1, "b": 2},
            criteria=lenient_criteria(),
        )
        assert result.outcome is EvaluationOutcome.SUCCESS
        assert result.is_success
        assert result.overall_score == pytest.approx(1.0)
        assert result.replan_triggered is False
        assert result.escalation_required is False
        assert result.feedback_type is FeedbackType.POSITIVE

    def test_eight_of_ten_is_partial(self, evaluator):
        expected = {f"k{i}": i for i in range(10)}
        actual = {f"k{i}": i for i in range(8)}  # 2 missing -> score 0
        result = evaluator.evaluate(
            goal_with(expected), actual, criteria=lenient_criteria(),
        )
        assert result.outcome is EvaluationOutcome.PARTIAL
        assert result.overall_score == pytest.approx(0.8)
        assert result.replan_triggered is False
        assert result.escalation_required is False
        assert result.feedback_type is FeedbackType.CORRECTIVE
        assert "Partial" in result.feedback

    def test_five_of_ten_is_off_track_with_replan(self, evaluator):
        expected = {f"k{i}": i for i in range(10)}
        actual = {f"k{i}": i for i in range(5)}
        result = evaluator.evaluate(
            goal_with(expected), actual, criteria=lenient_criteria(),
        )
        assert result.outcome is EvaluationOutcome.OFF_TRACK
        assert result.overall_score == pytest.approx(0.5)
        assert result.replan_triggered is True
        assert result.escalation_required is False
        assert result.feedback_type is FeedbackType.WARNING
        # a replan request must have been created
        replans = evaluator.get_replan_requests(goal_id="g1")
        assert len(replans) == 1
        assert replans[0].status == "pending"
        assert set(replans[0].failed_criteria) == {"k5", "k6", "k7", "k8", "k9"}
        assert replans[0].priority == 5

    def test_one_of_ten_is_failed_with_escalation(self, evaluator):
        expected = {f"k{i}": i for i in range(10)}
        actual = {"k0": 0}
        result = evaluator.evaluate(
            goal_with(expected), actual, criteria=lenient_criteria(),
        )
        assert result.outcome is EvaluationOutcome.FAILED
        assert result.is_failure
        assert result.replan_triggered is True
        assert result.escalation_required is True
        assert result.feedback_type is FeedbackType.CRITICAL
        replans = evaluator.get_replan_requests(goal_id="g1")
        assert replans[-1].priority == 10

    def test_custom_thresholds(self, evaluator):
        criteria = lenient_criteria(success_threshold=0.5)
        expected = {f"k{i}": i for i in range(10)}
        actual = {f"k{i}": i for i in range(6)}  # score 0.6
        result = evaluator.evaluate(goal_with(expected), actual, criteria=criteria)
        assert result.outcome is EvaluationOutcome.SUCCESS


# =====================================================================
# Value comparators
# =====================================================================

class TestComparators:
    def test_numeric_relative_tolerance(self, evaluator):
        result = evaluator.evaluate(
            goal_with({"accuracy": 100}), {"accuracy": 95},
            criteria=lenient_criteria(),
        )
        criterion = result.criteria_results[0]
        assert criterion.passed is True  # 5% deviation within 10%
        assert criterion.score == pytest.approx(0.95)

    def test_numeric_beyond_tolerance(self, evaluator):
        result = evaluator.evaluate(
            goal_with({"accuracy": 100}), {"accuracy": 85},
            criteria=lenient_criteria(),
        )
        criterion = result.criteria_results[0]
        assert criterion.passed is False
        assert criterion.score == pytest.approx(0.85)

    def test_numeric_zero_expected(self, evaluator):
        result = evaluator.evaluate(
            goal_with({"errors": 0}), {"errors": 0},
            criteria=lenient_criteria(),
        )
        assert result.criteria_results[0].passed is True
        result = evaluator.evaluate(
            goal_with({"errors": 0}), {"errors": 3},
            criteria=lenient_criteria(),
        )
        assert result.criteria_results[0].passed is False

    def test_string_case_insensitive(self, evaluator):
        result = evaluator.evaluate(
            goal_with({"status": "Done"}), {"status": "DONE"},
            criteria=lenient_criteria(),
        )
        assert result.criteria_results[0].passed is True

    def test_string_word_overlap(self, evaluator):
        result = evaluator.evaluate(
            goal_with({"summary": "deploy the service"}),
            {"summary": "deploy service"},
            criteria=lenient_criteria(),
        )
        criterion = result.criteria_results[0]
        assert criterion.passed is False  # 2/3 overlap < 0.8
        assert criterion.score == pytest.approx(2 / 3)

    def test_string_word_overlap_ignores_order(self, evaluator):
        result = evaluator.evaluate(
            goal_with({"summary": "deploy the service"}),
            {"summary": "service the deploy"},
            criteria=lenient_criteria(),
        )
        assert result.criteria_results[0].passed is True

    def test_list_overlap_threshold(self, evaluator):
        expected = ["a", "b", "c", "d", "e"]
        result = evaluator.evaluate(
            goal_with({"tags": expected}), {"tags": ["a", "b", "c", "d", "x"]},
            criteria=lenient_criteria(),
        )
        assert result.criteria_results[0].passed is True  # 4/5 = 0.8
        result = evaluator.evaluate(
            goal_with({"tags": expected}), {"tags": ["a", "b", "c", "x", "y"]},
            criteria=lenient_criteria(),
        )
        assert result.criteria_results[0].passed is False  # 3/5 = 0.6

    def test_dict_partial_match(self, evaluator):
        result = evaluator.evaluate(
            goal_with({"config": {"a": 1, "b": 2, "c": 3}}),
            {"config": {"a": 1, "b": 2, "c": 99}},
            criteria=lenient_criteria(),
        )
        criterion = result.criteria_results[0]
        assert criterion.passed is False
        assert criterion.score == pytest.approx(2 / 3)

    def test_missing_value_fails(self, evaluator):
        result = evaluator.evaluate(
            goal_with({"a": 1}), {}, criteria=lenient_criteria(),
        )
        criterion = result.criteria_results[0]
        assert criterion.passed is False
        assert criterion.score == 0.0
        assert criterion.actual is None

    def test_custom_weights(self, evaluator):
        criteria = lenient_criteria(custom_weights={"a": 3.0, "b": 1.0})
        result = evaluator.evaluate(
            goal_with({"a": 1, "b": 2}), {"a": 1}, criteria=criteria,
        )
        # (1.0*3 + 0.0*1) / 4 = 0.75 -> PARTIAL band
        assert result.overall_score == pytest.approx(0.75)
        assert result.outcome is EvaluationOutcome.PARTIAL

    def test_success_criteria_and_expected_outputs_merge(self, evaluator):
        intended = {
            "id": "g1",
            "expected_outputs": {"a": 1},
            "success_criteria": {"b": 2},
        }
        result = evaluator.evaluate(
            intended, {"a": 1, "b": 2}, criteria=lenient_criteria(),
        )
        assert {c.name for c in result.criteria_results} == {"a", "b"}
        assert result.outcome is EvaluationOutcome.SUCCESS


# =====================================================================
# Result metadata and histories
# =====================================================================

class TestResultAndHistories:
    def test_ids_and_correlation_propagated(self, evaluator):
        result = evaluator.evaluate(
            goal_with({"a": 1}, goal_id="goal-9"), {"a": 1},
            criteria=lenient_criteria(),
            correlation_id="corr-1", task_id="task-7",
        )
        assert result.goal_id == "goal-9"
        assert result.task_id == "task-7"
        assert result.correlation_id == "corr-1"

    def test_goal_id_generated_when_missing(self, evaluator):
        result = evaluator.evaluate(
            {"expected_outputs": {}}, {}, criteria=lenient_criteria(),
        )
        assert result.goal_id

    def test_evaluation_history_filters(self, evaluator):
        evaluator.evaluate(
            goal_with({"a": 1}, goal_id="g1"), {"a": 1}, criteria=lenient_criteria(),
        )
        evaluator.evaluate(
            goal_with({"a": 1, "b": 2}, goal_id="g2"), {"a": 1},
            criteria=lenient_criteria(),
        )
        assert len(evaluator.get_evaluation_history()) == 2
        assert len(evaluator.get_evaluation_history(goal_id="g1")) == 1
        assert len(evaluator.get_evaluation_history(
            outcome=EvaluationOutcome.SUCCESS)) == 1

    def test_feedback_entries_created_for_failures(self, evaluator):
        evaluator.evaluate(
            goal_with({"a": 1, "b": 2}, goal_id="g1"), {"a": 1},
            criteria=lenient_criteria(),
        )
        # 1 overall entry + 1 per failed criterion (b)
        feedback = evaluator.get_feedback_history(goal_id="g1")
        assert len(feedback) == 2
        corrective = [f for f in feedback if "Criterion 'b' failed" in f.message]
        assert len(corrective) == 1
        assert corrective[0].suggested_actions

    def test_apply_feedback(self, evaluator):
        evaluator.evaluate(
            goal_with({"a": 1}, goal_id="g1"), {"a": 1}, criteria=lenient_criteria(),
        )
        fb = evaluator.get_feedback_history(goal_id="g1")[0]
        assert fb.applied is False
        assert evaluator.apply_feedback(fb.id) is True
        assert fb.applied is True
        assert fb.applied_at is not None
        assert evaluator.get_feedback_history(unapplied_only=True) == []
        assert evaluator.apply_feedback("nope") is False

    def test_replan_lifecycle(self, evaluator):
        expected = {f"k{i}": i for i in range(10)}
        evaluator.evaluate(
            goal_with(expected), {}, criteria=lenient_criteria(),
        )
        replan = evaluator.get_replan_requests()[0]
        assert replan.status == "pending"
        assert evaluator.approve_replan(replan.id) is True
        assert replan.status == "approved"
        assert evaluator.execute_replan(replan.id) is True
        assert replan.status == "executed"
        assert replan.executed_at is not None
        assert evaluator.approve_replan("nope") is False
        assert evaluator.execute_replan("nope") is False

    def test_no_replan_on_success(self, evaluator):
        evaluator.evaluate(
            goal_with({"a": 1}, goal_id="g1"), {"a": 1}, criteria=lenient_criteria(),
        )
        assert evaluator.get_replan_requests() == []

    def test_stats(self, evaluator):
        evaluator.evaluate(
            goal_with({"a": 1}, goal_id="g1"), {"a": 1}, criteria=lenient_criteria(),
        )
        # 1 of 2 criteria met -> score 0.5 -> OFF_TRACK -> replan created
        evaluator.evaluate(
            goal_with({"a": 1, "b": 2}, goal_id="g2"), {"a": 1},
            criteria=lenient_criteria(),
        )
        stats = evaluator.stats()
        assert stats["total_evaluations"] == 2
        assert stats["by_outcome"]["success"] == 1
        assert stats["by_outcome"]["off_track"] == 1
        assert stats["total_replan_requests"] == 1
        assert stats["pending_replans"] == 1

    def test_history_capped_at_1000(self, evaluator):
        goal = goal_with({"a": 1})
        for _ in range(1001):
            evaluator.evaluate(goal, {"a": 1}, criteria=lenient_criteria())
        assert len(evaluator.get_evaluation_history(limit=2000)) == 1000

    def test_get_evaluator_singleton(self):
        assert get_evaluator() is get_evaluator()


# =====================================================================
# Defect-evidence tests (expected to FAIL until kernel fixed)
# Spec basis: Definition Lock section 112 (structured feedback
# success/partial/off_track/failed).
# =====================================================================

class TestDefects:
    def test_defect_default_criteria_allow_partial_outcome(self, evaluator):
        """DEFECT EV-1: default criteria make every criterion critical.

        With the default require_all_critical=True, _evaluate_criteria
        marks ALL criteria critical, so any single miss -- even at
        8/10 criteria met (score 0.8) -- returns FAILED with human
        escalation. The PARTIAL ("most criteria met, minor gaps") and
        OFF_TRACK outcomes declared in the module docstring are
        unreachable with default settings. Expected: a non-critical
        miss at score 0.8 yields PARTIAL, not FAILED.
        """
        expected = {f"k{i}": i for i in range(10)}
        actual = {f"k{i}": i for i in range(8)}
        result = evaluator.evaluate(goal_with(expected), actual)
        assert result.outcome is EvaluationOutcome.PARTIAL, (
            f"8/10 criteria met with default criteria yielded "
            f"{result.outcome.value} instead of PARTIAL"
        )

    def test_defect_empty_criteria_not_failed(self, evaluator):
        """DEFECT EV-2: a goal with no criteria evaluates as FAILED.

        With no expected_outputs/success_criteria, criterion_results is
        empty, overall_score is 0.0, and the outcome is FAILED with
        escalation_required=True -- even though nothing was violated.
        The Execution Kernel's Verifier treats the same situation as
        success ("no verification criteria"). Expected: an empty
        criteria set is trivially satisfied (SUCCESS), not FAILED.
        """
        result = evaluator.evaluate({"id": "g1"}, {})
        assert result.outcome is not EvaluationOutcome.FAILED, (
            "evaluation with no criteria escalated as FAILED"
        )

    def test_defect_named_critical_criterion_enforced(self, evaluator):
        """DEFECT EV-3: the "critical_" naming convention never works.

        _evaluate_criteria marks criteria named critical_* as critical,
        but _determine_outcome only consults the critical flag when
        criteria.require_all_critical is True:
            if criteria.require_all_critical and critical_failed: FAILED
        With require_all_critical=False (the only mode where the naming
        convention could matter), an explicitly critical criterion that
        fails does NOT force FAILED -- here a failed critical_safety
        yields OFF_TRACK (score 0.5) with no escalation. Expected: a
        criterion explicitly marked critical that fails yields FAILED
        with escalation regardless of require_all_critical.
        """
        result = evaluator.evaluate(
            goal_with({"critical_safety": True, "style": "ok"}),
            {"critical_safety": False, "style": "ok"},
            criteria=lenient_criteria(),
        )
        assert result.outcome is EvaluationOutcome.FAILED, (
            f"failed critical_ criterion yielded {result.outcome.value} "
            f"instead of FAILED"
        )
        assert result.escalation_required is True
