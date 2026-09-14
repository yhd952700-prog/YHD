"""Evaluation Report — aggregate ``Evaluator`` history into metrics + a report (Phase 6).

The evaluation kernel already scores individual outcomes (``Evaluator.evaluate``)
and the Agent Runtime already auto-evaluates every run (Phase 3). What was
missing is a *signal layer*: turning a list of ``EvaluationResult`` into
measurable metrics and a human-readable report.

This module is deliberately **pure** and additive:
* ``build_evaluation_report`` is a pure function of a result list.
* The only side-effect entry point, ``generate_report``, merely *reads* the
  existing ``Evaluator`` history via ``get_evaluation_history``.

Prometheus emission / persistence across processes is a Phase 7 (productionisation)
concern; here we emit a plain metric dict plus markdown. See
``docs/EVALUATION-KERNEL-DESIGN.md``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src._time import utc_now
from src.kernels.evaluation import (
    EvaluationOutcome,
    EvaluationResult,
    get_evaluator,
)


def _as_dt(value: Any) -> Optional[datetime]:
    """Coerce datetime | ISO string | None into a tz-aware datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


@dataclass
class EvaluationReport:
    """Aggregated evaluation metrics over a window of results."""

    total: int = 0
    outcome_distribution: Dict[str, int] = field(default_factory=dict)
    success_rate: float = 0.0
    avg_score: float = 0.0
    replan_rate: float = 0.0
    escalation_rate: float = 0.0
    human_checkpoint_rate: float = 0.0
    feedback_by_type: Dict[str, int] = field(default_factory=dict)
    top_failures: List[Dict[str, Any]] = field(default_factory=list)
    by_goal: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    since: Optional[str] = None
    until: Optional[str] = None
    generated_at: str = field(default_factory=lambda: utc_now().isoformat())

    @property
    def is_healthy(self) -> bool:
        """A coarse health flag: mostly-successful and not escalation-heavy."""
        return self.total == 0 or (
            self.success_rate >= 0.8 and self.escalation_rate <= 0.2
        )


def build_evaluation_report(
    results: List[EvaluationResult],
    *,
    since: Any = None,
    until: Any = None,
    top_n: int = 5,
) -> EvaluationReport:
    """Pure aggregation of ``EvaluationResult`` list into an ``EvaluationReport``."""
    since_dt, until_dt = _as_dt(since), _as_dt(until)

    filtered: List[EvaluationResult] = []
    for r in results:
        ts = _as_dt(getattr(r, "evaluated_at", None))
        if since_dt is not None and ts is not None and ts < since_dt:
            continue
        if until_dt is not None and ts is not None and ts > until_dt:
            continue
        filtered.append(r)

    total = len(filtered)
    report = EvaluationReport(
        total=total,
        since=since_dt.isoformat() if since_dt else None,
        until=until_dt.isoformat() if until_dt else None,
    )
    if total == 0:
        return report

    outcomes: Dict[str, int] = {}
    feedback_by_type: Dict[str, int] = {}
    failure_feedback: Dict[str, int] = {}
    by_goal: Dict[str, Dict[str, Any]] = {}
    score_sum = 0.0
    success = 0
    replan = escalation = human = 0

    for r in filtered:
        outcomes[r.outcome.value] = outcomes.get(r.outcome.value, 0) + 1
        score_sum += float(r.overall_score)
        if r.outcome == EvaluationOutcome.SUCCESS:
            success += 1
        else:
            failure_feedback[r.feedback] = failure_feedback.get(r.feedback, 0) + 1

        ft = getattr(r.feedback_type, "value", str(r.feedback_type))
        feedback_by_type[ft] = feedback_by_type.get(ft, 0) + 1
        replan += 1 if r.replan_triggered else 0
        escalation += 1 if r.escalation_required else 0
        human += 1 if r.human_checkpoint else 0

        g = by_goal.setdefault(r.goal_id, {"total": 0, "success": 0, "score_sum": 0.0})
        g["total"] += 1
        g["success"] += 1 if r.outcome == EvaluationOutcome.SUCCESS else 0
        g["score_sum"] += float(r.overall_score)

    report.outcome_distribution = outcomes
    report.feedback_by_type = feedback_by_type
    report.success_rate = success / total
    report.avg_score = score_sum / total
    report.replan_rate = replan / total
    report.escalation_rate = escalation / total
    report.human_checkpoint_rate = human / total
    report.top_failures = sorted(
        ({"feedback": k, "count": v} for k, v in failure_feedback.items()),
        key=lambda d: d["count"],
        reverse=True,
    )[:top_n]
    report.by_goal = {
        gid: {
            "total": d["total"],
            "success": d["success"],
            "avg_score": d["score_sum"] / d["total"],
        }
        for gid, d in by_goal.items()
    }
    return report


def generate_report(
    evaluator: Optional[Any] = None,
    *,
    limit: int = 1000,
    since: Any = None,
    until: Any = None,
    top_n: int = 5,
) -> EvaluationReport:
    """Build a report from the (process-wide or injected) evaluator history."""
    ev = evaluator or get_evaluator()
    results = ev.get_evaluation_history(limit=limit)
    return build_evaluation_report(results, since=since, until=until, top_n=top_n)


def to_metrics(report: EvaluationReport) -> Dict[str, float]:
    """Flatten a report into gauge-friendly metrics (Phase 7 wires these to Prometheus)."""
    return {
        "evaluation_total": float(report.total),
        "evaluation_success_rate": report.success_rate,
        "evaluation_avg_score": report.avg_score,
        "evaluation_replan_rate": report.replan_rate,
        "evaluation_escalation_rate": report.escalation_rate,
        "evaluation_human_checkpoint_rate": report.human_checkpoint_rate,
    }


def render_report_markdown(report: EvaluationReport) -> str:
    """Human-readable Evaluation Report."""
    lines = [
        "# Evaluation Report",
        "",
        f"- generated_at: {report.generated_at}",
        f"- window: {report.since or '(all)'} → {report.until or '(now)'}",
        f"- total: {report.total}",
        f"- success_rate: {report.success_rate:.1%}",
        f"- avg_score: {report.avg_score:.3f}",
        f"- replan_rate: {report.replan_rate:.1%}  escalation_rate: {report.escalation_rate:.1%}"
        f"  human_checkpoint_rate: {report.human_checkpoint_rate:.1%}",
        f"- healthy: {report.is_healthy}",
        "",
        "## Outcomes",
    ]
    if report.outcome_distribution:
        for k, v in sorted(report.outcome_distribution.items()):
            lines.append(f"- {k}: {v}")
    else:
        lines.append("- (none)")
    lines += ["", "## Top failures"]
    if report.top_failures:
        for f in report.top_failures:
            lines.append(f"- [{f['count']}] {f['feedback']}")
    else:
        lines.append("- (none)")
    return "\n".join(lines)
