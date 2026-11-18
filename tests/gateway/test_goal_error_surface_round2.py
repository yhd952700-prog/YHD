"""目标失败原因上屏回归（完善第二轮 D 轮走查发现）。

走查实测：无 Key 时 POST /v1/goals 建目标，执行失败后目标详情
error/technical_detail 全空，原因只藏在任务里——列表只显示「失败」
不给原因。根因：_result_to_dict 只读 result.error，而运行时把失败
报在任务上时 result.error 为空。重试路径早有 blocker 回落口径，
初次执行路径缺了同一回落。本测试锁住回落行为。
"""

from __future__ import annotations

from types import SimpleNamespace

from src.gateway.ai_management import AIStateManager


def _task(status: str, error: str | None) -> SimpleNamespace:
    return SimpleNamespace(
        id="t1", name="Execute", description="Execute goal", status=status,
        capability_id="execution_pipeline", assigned_agent="agent-1",
        assignment_reason=None, produced=False, artifacts=[], inputs={},
        dependencies=[], max_retries=3, timeout_seconds=300, result=None,
        error=error, started_at=None, completed_at=None,
    )


def _result(state: str, error, tasks) -> SimpleNamespace:
    plan = SimpleNamespace(tasks=tasks)
    ctx = SimpleNamespace(plan=plan, verification_results={})
    return SimpleNamespace(
        goal_id="g1", state=state, error=error, context=ctx, trace=None,
        evaluation=None, correlation_id="c1", replan_count=0,
        replan_suggested=True, memory_keys=[], persist_errors=[],
        preflight_notices=[], decomposition_source="keyword",
    )


def test_failed_goal_surfaces_task_error_when_result_error_empty():
    result = _result("failed", None, [_task("failed", "Max retries (3) exceeded: boom")])
    out = AIStateManager._result_to_dict(result, "做短视频", "L1")
    assert out["state"] == "failed"
    assert out["error"], "目标层 error 不许为空：要回落到失败任务的原因"
    assert out["technical_detail"] and "Max retries" in out["technical_detail"]


def test_completed_goal_keeps_error_empty():
    result = _result("completed", None, [_task("completed", None)])
    out = AIStateManager._result_to_dict(result, "做短视频", "L1")
    assert out["error"] is None


def test_result_error_takes_precedence_over_task_error():
    result = _result("failed", "Max retries (3) exceeded: top-level",
                     [_task("failed", "Max retries (3) exceeded: task-level")])
    out = AIStateManager._result_to_dict(result, "做短视频", "L1")
    assert "top-level" in (out["technical_detail"] or "")
