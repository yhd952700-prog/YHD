"""Evaluation Kernel — Outcome Evaluation + Feedback Loop

The Evaluation Kernel evaluates execution outcomes against intended goals,
provides feedback for improvement, triggers replanning when needed,
and supports escalation to human operators.

依据 Definition Lock §112: Evaluation Kernel 必须能够
- Evaluate outcomes against success criteria
- Generate structured feedback (success/partial/off_track/failed)
- Trigger replanning when criteria not met
- Escalate to human when off-track or failed
- Track evaluation history for learning
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set
import uuid
import threading

from src.kernels._crosscutting import kernel_action


class EvaluationOutcome(str, Enum):
    """Evaluation outcome categories."""
    SUCCESS = "success"          # All criteria met
    PARTIAL = "partial"          # Most criteria met, minor gaps
    OFF_TRACK = "off_track"      # Significant deviation, needs replan
    FAILED = "failed"            # Critical failure, needs human
    NEEDS_HUMAN = "needs_human"  # Explicit human decision required


class EvaluationScope(str, Enum):
    """Evaluation scope L0-L7."""
    L0 = "L0"
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"
    L4 = "L4"
    L5 = "L5"
    L6 = "L6"
    L7 = "L7"


class FeedbackType(str, Enum):
    """Types of feedback."""
    POSITIVE = "positive"      # Reinforce behavior
    CORRECTIVE = "corrective"  # Adjust approach
    WARNING = "warning"        # Potential issue
    CRITICAL = "critical"      # Immediate action needed


@dataclass
class EvaluationCriteria:
    """Criteria for evaluating outcomes."""
    success_threshold: float = 0.9      # Score >= this = SUCCESS
    partial_threshold: float = 0.7      # Score >= this = PARTIAL
    off_track_threshold: float = 0.4    # Score >= this = OFF_TRACK
    # Below off_track_threshold = FAILED
    max_retries: int = 3
    require_all_critical: bool = True   # All critical criteria must pass
    custom_weights: Dict[str, float] = field(default_factory=dict)  # criterion -> weight


@dataclass
class CriterionResult:
    """Result for a single evaluation criterion."""
    name: str
    description: str
    expected: Any
    actual: Any
    passed: bool
    score: float  # 0.0 - 1.0
    weight: float = 1.0
    critical: bool = False
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class EvaluationResult:
    """Complete evaluation result."""
    evaluation_id: str
    goal_id: str
    task_id: Optional[str]
    outcome: EvaluationOutcome
    overall_score: float  # 0.0 - 1.0
    criteria_results: List[CriterionResult]
    feedback: str
    feedback_type: FeedbackType
    replan_triggered: bool = False
    escalation_required: bool = False
    human_checkpoint: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)
    evaluated_at: datetime = field(default_factory=datetime.utcnow)
    correlation_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    @property
    def is_success(self) -> bool:
        return self.outcome == EvaluationOutcome.SUCCESS

    @property
    def is_failure(self) -> bool:
        return self.outcome in (EvaluationOutcome.FAILED, EvaluationOutcome.NEEDS_HUMAN)


@dataclass
class FeedbackEntry:
    """Structured feedback for learning."""
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    evaluation_id: str = ""
    goal_id: str = ""
    task_id: Optional[str] = None
    feedback_type: FeedbackType = FeedbackType.CORRECTIVE
    message: str = ""
    suggested_actions: List[str] = field(default_factory=list)
    priority: int = 0  # Higher = more urgent
    applied: bool = False
    applied_at: Optional[datetime] = None
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class ReplanRequest:
    """Request for replanning."""
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    evaluation_id: str = ""
    goal_id: str = ""
    reason: str = ""
    failed_criteria: List[str] = field(default_factory=list)
    suggested_changes: List[str] = field(default_factory=list)
    priority: int = 0
    status: str = "pending"  # pending, approved, rejected, executed
    created_at: datetime = field(default_factory=datetime.utcnow)
    executed_at: Optional[datetime] = None


class Evaluator:
    """Evaluates outcomes against criteria."""

    def __init__(self, criteria: Optional[EvaluationCriteria] = None):
        self.criteria = criteria or EvaluationCriteria()
        self._evaluation_history: List[EvaluationResult] = []
        self._feedback_history: List[FeedbackEntry] = []
        self._replan_requests: List[ReplanRequest] = []
        self._lock = threading.RLock()

    @kernel_action("evaluation.evaluate")
    def evaluate(
        self,
        intended_goal: Dict[str, Any],
        actual_outcome: Dict[str, Any],
        criteria: Optional[EvaluationCriteria] = None,
        scope: str = "L1",
        correlation_id: Optional[str] = None,
        task_id: Optional[str] = None
    ) -> EvaluationResult:
        """Evaluate actual outcome against intended goal."""
        eval_criteria = criteria or self.criteria
        goal_id = intended_goal.get("id", str(uuid.uuid4())[:8])

        # Compute criterion results
        criterion_results = self._evaluate_criteria(
            intended_goal, actual_outcome, eval_criteria
        )

        # Calculate overall score
        overall_score = self._calculate_overall_score(criterion_results, eval_criteria)

        # Determine outcome
        outcome = self._determine_outcome(overall_score, criterion_results, eval_criteria)

        # Generate feedback
        feedback, feedback_type = self._generate_feedback(
            outcome, overall_score, criterion_results, eval_criteria
        )

        # Check for replan/escalation
        replan_triggered = outcome in (EvaluationOutcome.OFF_TRACK, EvaluationOutcome.FAILED)
        escalation_required = outcome in (EvaluationOutcome.FAILED, EvaluationOutcome.NEEDS_HUMAN)
        human_checkpoint = outcome == EvaluationOutcome.NEEDS_HUMAN or (
            outcome == EvaluationOutcome.FAILED and eval_criteria.require_all_critical
        )

        # Create result
        result = EvaluationResult(
            evaluation_id=str(uuid.uuid4())[:8],
            goal_id=goal_id,
            task_id=task_id,
            outcome=outcome,
            overall_score=overall_score,
            criteria_results=criterion_results,
            feedback=feedback,
            feedback_type=feedback_type,
            replan_triggered=replan_triggered,
            escalation_required=escalation_required,
            human_checkpoint=human_checkpoint,
            correlation_id=correlation_id or str(uuid.uuid4()),
        )

        # Store
        with self._lock:
            self._evaluation_history.append(result)
            # Keep last 1000 evaluations
            if len(self._evaluation_history) > 1000:
                self._evaluation_history = self._evaluation_history[-1000:]

        # Generate feedback entries
        self._create_feedback_entries(result, criterion_results)

        # Create replan request if needed
        if replan_triggered:
            self._create_replan_request(result, criterion_results)

        return result

    def _evaluate_criteria(
        self,
        intended: Dict[str, Any],
        actual: Dict[str, Any],
        criteria: EvaluationCriteria
    ) -> List[CriterionResult]:
        """Evaluate each criterion."""
        results = []

        # Default criteria from intended goal
        expected_outputs = intended.get("expected_outputs", {})
        success_criteria = intended.get("success_criteria", {})

        # Combine all criteria
        all_criteria = {**expected_outputs, **success_criteria}

        for name, expected in all_criteria.items():
            actual_value = actual.get(name)
            weight = criteria.custom_weights.get(name, 1.0)
            # Criticality is derived from the explicit "critical_" naming
            # convention, NOT from require_all_critical (which only governs
            # whether failed critical criteria force a FAILED outcome).
            critical = name.startswith("critical_")

            passed, score = self._compare_values(expected, actual_value)

            result = CriterionResult(
                name=name,
                description=f"Check {name}",
                expected=expected,
                actual=actual_value,
                passed=passed,
                score=score,
                weight=weight,
                critical=critical,
            )
            results.append(result)

        return results

    def _compare_values(self, expected: Any, actual: Any) -> tuple:
        """Compare expected vs actual values. Returns (passed, score)."""
        if actual is None:
            return False, 0.0

        try:
            if expected == actual:
                return True, 1.0

            # Numeric comparison
            if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
                if expected == 0:
                    return actual == 0, 1.0 if actual == 0 else 0.0
                diff = abs(expected - actual) / abs(expected)
                score = max(0.0, 1.0 - diff)
                return score >= 0.9, score

            # String comparison
            if isinstance(expected, str) and isinstance(actual, str):
                if expected.lower() == actual.lower():
                    return True, 1.0
                # Simple similarity
                expected_words = set(expected.lower().split())
                actual_words = set(actual.lower().split())
                if expected_words:
                    overlap = len(expected_words & actual_words) / len(expected_words)
                    return overlap >= 0.8, overlap

            # List/Set comparison
            if isinstance(expected, (list, set, tuple)) and isinstance(actual, (list, set, tuple)):
                expected_set = set(expected)
                actual_set = set(actual)
                if not expected_set:
                    return True, 1.0
                overlap = len(expected_set & actual_set) / len(expected_set)
                return overlap >= 0.8, overlap

            # Dict comparison
            if isinstance(expected, dict) and isinstance(actual, dict):
                matched = 0
                total = len(expected)
                for k, v in expected.items():
                    if k in actual:
                        passed, _ = self._compare_values(v, actual[k])
                        if passed:
                            matched += 1
                score = matched / total if total > 0 else 1.0
                return score >= 0.9, score

        except (TypeError, ValueError, ZeroDivisionError):
            pass

        return False, 0.0

    def _calculate_overall_score(
        self,
        criterion_results: List[CriterionResult],
        criteria: EvaluationCriteria
    ) -> float:
        """Calculate weighted overall score."""
        if not criterion_results:
            return 0.0

        total_weight = sum(c.weight for c in criterion_results)
        if total_weight == 0:
            return 0.0

        weighted_sum = sum(c.score * c.weight for c in criterion_results)
        return weighted_sum / total_weight

    def _determine_outcome(
        self,
        overall_score: float,
        criterion_results: List[CriterionResult],
        criteria: EvaluationCriteria
    ) -> EvaluationOutcome:
        """Determine evaluation outcome from score and criteria."""
        # An empty criteria set means nothing was violated: trivially SUCCESS.
        if not criterion_results:
            return EvaluationOutcome.SUCCESS

        # Check critical criteria
        critical_failed = any(
            c.critical and not c.passed
            for c in criterion_results
        )

        # Any explicitly-marked critical criterion that fails forces a FAILED
        # outcome with escalation, regardless of require_all_critical.
        if critical_failed:
            return EvaluationOutcome.FAILED

        if overall_score >= criteria.success_threshold:
            return EvaluationOutcome.SUCCESS
        elif overall_score >= criteria.partial_threshold:
            return EvaluationOutcome.PARTIAL
        elif overall_score >= criteria.off_track_threshold:
            return EvaluationOutcome.OFF_TRACK
        else:
            return EvaluationOutcome.FAILED

    def _generate_feedback(
        self,
        outcome: EvaluationOutcome,
        score: float,
        criterion_results: List[CriterionResult],
        criteria: EvaluationCriteria
    ) -> tuple:
        """Generate human-readable feedback."""
        passed = sum(1 for c in criterion_results if c.passed)
        total = len(criterion_results)
        failed_criteria = [c.name for c in criterion_results if not c.passed]

        if outcome == EvaluationOutcome.SUCCESS:
            feedback = f"✅ Success: {passed}/{total} criteria met (score: {score:.2f})"
            feedback_type = FeedbackType.POSITIVE
        elif outcome == EvaluationOutcome.PARTIAL:
            feedback = f"⚠️ Partial: {passed}/{total} criteria met (score: {score:.2f}). Failed: {', '.join(failed_criteria)}"
            feedback_type = FeedbackType.CORRECTIVE
        elif outcome == EvaluationOutcome.OFF_TRACK:
            feedback = f"🔄 Off-track: {passed}/{total} criteria met (score: {score:.2f}). Failed: {', '.join(failed_criteria)}. Replan recommended."
            feedback_type = FeedbackType.WARNING
        else:  # FAILED or NEEDS_HUMAN
            feedback = f"❌ Failed: {passed}/{total} criteria met (score: {score:.2f}). Failed: {', '.join(failed_criteria)}. Human intervention required."
            feedback_type = FeedbackType.CRITICAL

        return feedback, feedback_type

    def _create_feedback_entries(self, result: EvaluationResult, criterion_results: List[CriterionResult]) -> None:
        """Create structured feedback entries."""
        with self._lock:
            # Overall feedback
            fb = FeedbackEntry(
                evaluation_id=result.evaluation_id,
                goal_id=result.goal_id,
                task_id=result.task_id,
                feedback_type=result.feedback_type,
                message=result.feedback,
                priority=10 if result.escalation_required else (5 if result.replan_triggered else 1),
            )
            self._feedback_history.append(fb)

            # Per-criterion feedback for failures
            for c in criterion_results:
                if not c.passed:
                    fb = FeedbackEntry(
                        evaluation_id=result.evaluation_id,
                        goal_id=result.goal_id,
                        task_id=result.task_id,
                        feedback_type=FeedbackType.CORRECTIVE,
                        message=f"Criterion '{c.name}' failed: expected {c.expected}, got {c.actual}",
                        suggested_actions=[f"Adjust {c.name} to match expected value"],
                        priority=8 if c.critical else 3,
                    )
                    self._feedback_history.append(fb)

    def _create_replan_request(self, result: EvaluationResult, criterion_results: List[CriterionResult]) -> None:
        """Create replan request for off-track/failed evaluations."""
        with self._lock:
            failed = [c.name for c in criterion_results if not c.passed]

            replan = ReplanRequest(
                evaluation_id=result.evaluation_id,
                goal_id=result.goal_id,
                reason=f"Evaluation {result.outcome.value}: {result.feedback}",
                failed_criteria=failed,
                suggested_changes=self._suggest_changes(result, criterion_results),
                priority=10 if result.escalation_required else 5,
            )
            self._replan_requests.append(replan)

    def _suggest_changes(
        self,
        result: EvaluationResult,
        criterion_results: List[CriterionResult]
    ) -> List[str]:
        """Suggest changes for replanning."""
        suggestions = []

        if result.outcome == EvaluationOutcome.OFF_TRACK:
            suggestions.append("Review task decomposition - may need finer granularity")
            suggestions.append("Consider alternative capabilities for failed criteria")
            suggestions.append("Increase resource allocation for struggling tasks")

        if result.outcome == EvaluationOutcome.FAILED:
            suggestions.append("Escalate to human operator for decision")
            suggestions.append("Review goal feasibility - may need scope reduction")
            suggestions.append("Check capability availability and versions")

        return suggestions

    def get_evaluation_history(
        self,
        goal_id: Optional[str] = None,
        outcome: Optional[EvaluationOutcome] = None,
        limit: int = 100
    ) -> List[EvaluationResult]:
        """Get evaluation history with filters."""
        with self._lock:
            results = self._evaluation_history
            if goal_id:
                results = [r for r in results if r.goal_id == goal_id]
            if outcome:
                results = [r for r in results if r.outcome == outcome]
            return results[-limit:]

    def get_feedback_history(
        self,
        goal_id: Optional[str] = None,
        feedback_type: Optional[FeedbackType] = None,
        unapplied_only: bool = False,
        limit: int = 100
    ) -> List[FeedbackEntry]:
        """Get feedback history with filters."""
        with self._lock:
            feedback = self._feedback_history
            if goal_id:
                feedback = [f for f in feedback if f.goal_id == goal_id]
            if feedback_type:
                feedback = [f for f in feedback if f.feedback_type == feedback_type]
            if unapplied_only:
                feedback = [f for f in feedback if not f.applied]
            return feedback[-limit:]

    def get_replan_requests(
        self,
        goal_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100
    ) -> List[ReplanRequest]:
        """Get replan requests with filters."""
        with self._lock:
            requests = self._replan_requests
            if goal_id:
                requests = [r for r in requests if r.goal_id == goal_id]
            if status:
                requests = [r for r in requests if r.status == status]
            return requests[-limit:]

    @kernel_action("evaluation.apply_feedback")
    def apply_feedback(self, feedback_id: str) -> bool:
        """Mark feedback as applied."""
        with self._lock:
            for fb in self._feedback_history:
                if fb.id == feedback_id:
                    fb.applied = True
                    fb.applied_at = datetime.utcnow()
                    return True
        return False

    @kernel_action("evaluation.approve_replan")
    def approve_replan(self, replan_id: str) -> bool:
        """Approve a replan request."""
        with self._lock:
            for r in self._replan_requests:
                if r.id == replan_id:
                    r.status = "approved"
                    return True
        return False

    @kernel_action("evaluation.execute_replan")
    def execute_replan(self, replan_id: str) -> bool:
        """Mark replan as executed."""
        with self._lock:
            for r in self._replan_requests:
                if r.id == replan_id:
                    r.status = "executed"
                    r.executed_at = datetime.utcnow()
                    return True
        return False

    def stats(self) -> Dict[str, Any]:
        """Get evaluator statistics."""
        with self._lock:
            by_outcome = {}
            for r in self._evaluation_history:
                by_outcome[r.outcome.value] = by_outcome.get(r.outcome.value, 0) + 1

            return {
                "total_evaluations": len(self._evaluation_history),
                "by_outcome": by_outcome,
                "total_feedback": len(self._feedback_history),
                "unapplied_feedback": sum(1 for f in self._feedback_history if not f.applied),
                "total_replan_requests": len(self._replan_requests),
                "pending_replans": sum(1 for r in self._replan_requests if r.status == "pending"),
            }


# Global evaluator instance
_global_evaluator: Optional[Evaluator] = None
_global_lock = threading.Lock()


def get_evaluator(criteria: Optional[EvaluationCriteria] = None) -> Evaluator:
    """Get or create the global evaluator."""
    global _global_evaluator
    if _global_evaluator is None:
        with _global_lock:
            if _global_evaluator is None:
                _global_evaluator = Evaluator(criteria)
    return _global_evaluator


# Convenience functions
def evaluate_outcome(
    intended_goal: Dict[str, Any],
    actual_outcome: Dict[str, Any],
    criteria: Optional[EvaluationCriteria] = None,
    scope: str = "L1",
    correlation_id: Optional[str] = None,
    task_id: Optional[str] = None
) -> EvaluationResult:
    """Evaluate an outcome."""
    return get_evaluator(criteria).evaluate(
        intended_goal, actual_outcome, criteria, scope, correlation_id, task_id
    )


def create_evaluation_criteria(
    success_threshold: float = 0.9,
    partial_threshold: float = 0.7,
    off_track_threshold: float = 0.4,
    max_retries: int = 3,
    require_all_critical: bool = True,
    custom_weights: Optional[Dict[str, float]] = None
) -> EvaluationCriteria:
    """Create evaluation criteria."""
    return EvaluationCriteria(
        success_threshold=success_threshold,
        partial_threshold=partial_threshold,
        off_track_threshold=off_track_threshold,
        max_retries=max_retries,
        require_all_critical=require_all_critical,
        custom_weights=custom_weights or {},
    )


def get_evaluation_history(
    goal_id: Optional[str] = None,
    outcome: Optional[EvaluationOutcome] = None,
    limit: int = 100
) -> List[EvaluationResult]:
    """Get evaluation history."""
    return get_evaluator().get_evaluation_history(goal_id, outcome, limit)


def get_feedback_history(
    goal_id: Optional[str] = None,
    feedback_type: Optional[FeedbackType] = None,
    unapplied_only: bool = False,
    limit: int = 100
) -> List[FeedbackEntry]:
    """Get feedback history."""
    return get_evaluator().get_feedback_history(goal_id, feedback_type, unapplied_only, limit)