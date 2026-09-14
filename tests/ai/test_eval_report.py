"""Phase 6 — Evaluation Report 测试（离线、纯聚合）。

覆盖：空集、成功率/均值、三率、Top 失败、窗口过滤、按 goal 汇总、markdown、从 Evaluator 读回。
"""

from datetime import datetime, timedelta, timezone

from src.ai.eval_report import (
    build_evaluation_report,
    generate_report,
    render_report_markdown,
    to_metrics,
)
from src.kernels.evaluation import (
    EvaluationOutcome,
    EvaluationResult,
    Evaluator,
    FeedbackType,
)


def _mk(
    goal_id="g1",
    outcome=EvaluationOutcome.SUCCESS,
    score=1.0,
    feedback="ok",
    feedback_type=FeedbackType.CORRECTIVE,
    evaluated_at=None,
    replan=False,
    escalation=False,
    human=False,
):
    return EvaluationResult(
        evaluation_id="e" + str(abs(hash((goal_id, outcome.value, score, feedback))) % 10000),
        goal_id=goal_id,
        task_id=None,
        outcome=outcome,
        overall_score=score,
        criteria_results=[],
        feedback=feedback,
        feedback_type=feedback_type,
        replan_triggered=replan,
        escalation_required=escalation,
        human_checkpoint=human,
        **({"evaluated_at": evaluated_at} if evaluated_at else {}),
    )


def test_empty_report_no_div_by_zero():
    r = build_evaluation_report([])
    assert r.total == 0
    assert r.success_rate == 0.0
    assert r.avg_score == 0.0
    assert r.is_healthy is True


def test_success_rate_and_average():
    results = [
        _mk(outcome=EvaluationOutcome.SUCCESS, score=1.0),
        _mk(outcome=EvaluationOutcome.SUCCESS, score=0.8),
        _mk(outcome=EvaluationOutcome.FAILED, score=0.2, feedback="bad"),
        _mk(outcome=EvaluationOutcome.OFF_TRACK, score=0.5, feedback="drift"),
    ]
    r = build_evaluation_report(results)
    assert r.total == 4
    assert abs(r.success_rate - 0.5) < 1e-9
    assert abs(r.avg_score - 0.625) < 1e-9
    assert r.outcome_distribution == {"success": 2, "failed": 1, "off_track": 1}


def test_rates():
    results = [
        _mk(replan=True),
        _mk(escalation=True),
        _mk(human=True),
        _mk(),
    ]
    r = build_evaluation_report(results)
    assert abs(r.replan_rate - 0.25) < 1e-9
    assert abs(r.escalation_rate - 0.25) < 1e-9
    assert abs(r.human_checkpoint_rate - 0.25) < 1e-9


def test_top_failures_ordering():
    results = [
        _mk(outcome=EvaluationOutcome.FAILED, feedback="a"),
        _mk(outcome=EvaluationOutcome.FAILED, feedback="a"),
        _mk(outcome=EvaluationOutcome.OFF_TRACK, feedback="b"),
        _mk(outcome=EvaluationOutcome.SUCCESS, feedback="ok"),
    ]
    r = build_evaluation_report(results, top_n=5)
    assert r.top_failures[0] == {"feedback": "a", "count": 2}
    assert r.top_failures[1] == {"feedback": "b", "count": 1}


def test_window_filter():
    base = datetime(2026, 9, 14, tzinfo=timezone.utc)
    results = [
        _mk(evaluated_at=base - timedelta(days=2)),
        _mk(evaluated_at=base),
        _mk(evaluated_at=base + timedelta(days=2)),
    ]
    r = build_evaluation_report(results, since=base - timedelta(hours=1), until=base + timedelta(hours=1))
    assert r.total == 1


def test_by_goal_rollup():
    results = [
        _mk(goal_id="g1", outcome=EvaluationOutcome.SUCCESS, score=1.0),
        _mk(goal_id="g1", outcome=EvaluationOutcome.FAILED, score=0.0, feedback="x"),
        _mk(goal_id="g2", outcome=EvaluationOutcome.SUCCESS, score=0.9),
    ]
    r = build_evaluation_report(results)
    assert r.by_goal["g1"]["total"] == 2 and r.by_goal["g1"]["success"] == 1
    assert abs(r.by_goal["g1"]["avg_score"] - 0.5) < 1e-9
    assert r.by_goal["g2"]["total"] == 1


def test_render_markdown_and_metrics():
    r = build_evaluation_report([_mk(outcome=EvaluationOutcome.SUCCESS, score=1.0)])
    md = render_report_markdown(r)
    assert "Evaluation Report" in md
    assert "success_rate" in md
    m = to_metrics(r)
    assert m["evaluation_total"] == 1.0
    assert m["evaluation_success_rate"] == 1.0


def test_generate_report_from_evaluator():
    ev = Evaluator()
    ev.evaluate({"id": "gx", "natural_language": "do x"}, {"completed": ["t1"], "failed": []})
    r = generate_report(ev)
    assert r.total >= 1
    assert r.generated_at
