"""End-to-end proof: a goal whose tasks FAIL to execute is NOT reported SUCCESS
by the evaluation, even with no declared success criteria.

This exercises the real ``AgentRuntime`` -> ``Evaluator`` path (not just the
``Evaluator`` unit) so the reality-check in ``Evaluator.evaluate`` is confirmed
to take effect in production wiring. Data paths are REDIR'd to temp so the
frozen HC-01 audit_store.db is never touched.
"""
import os
import tempfile
import types

import pytest

_TMP = tempfile.mkdtemp(prefix="liuhao_eval_reality_")
os.environ.setdefault("LIUHAO_WORKSPACE_ROOT", _TMP)
os.environ.setdefault("AUDIT_DB_PATH", os.path.join(_TMP, "audit_store.db"))

from src.ai.agent_runtime import AgentRuntime  # noqa: E402
from src.kernels.evaluation import EvaluationOutcome  # noqa: E402
from src.kernels.execution import ExecutionJournal  # noqa: E402


def _allow_all_scope(*args, **kwargs):
    return types.SimpleNamespace(allowed=True, reason="test-allow")


@pytest.fixture(autouse=True)
def _allow_scopes(monkeypatch):
    import src.kernels.execution as ex

    monkeypatch.setattr(ex, "check_capability_scope", _allow_all_scope)


def test_failed_execution_is_not_evaluated_success():
    """A goal with no success criteria whose task fails to execute must yield a
    FAILED (not SUCCESS) evaluation verdict through the real runtime.
    """
    def fail_executor(capability_id, inputs):
        return {"success": False, "error": "boom"}

    rt = AgentRuntime(scope="L1", capability_executor=fail_executor)
    result = rt.run_goal(
        "write file plan.txt with hello", goal_id="GE1", persist=False
    )
    assert result.evaluation is not None, "runtime must produce an evaluation"
    assert result.evaluation.outcome != EvaluationOutcome.SUCCESS, (
        f"failed-execution goal evaluated as {result.evaluation.outcome.value}; "
        f"must not read as success"
    )
    assert result.evaluation.outcome is EvaluationOutcome.FAILED


def test_completed_execution_without_artifact_is_not_success():
    """产物目标跑完但没有产物路径，不能判成功。

    原断言 ``test_completed_execution_with_no_criteria_is_success`` 把
    「执行结束」当成了「交付成功」，固化了本次修复的缺陷；这里改成断言
    「不是成功」。没有 file_write 能力时会在计划期直接 FAILED，有执行器但
    没写出文件时降为 PARTIAL —— 两者都不得是 SUCCESS。
    """
    def ok_executor(capability_id, inputs):
        return {"success": True, "status": "executed"}

    rt = AgentRuntime(scope="L1", capability_executor=ok_executor)
    result = rt.run_goal(
        "把计划写入 plan.txt 文件", goal_id="GE2", persist=False
    )
    assert result.evaluation is not None
    assert result.evaluation.outcome is not EvaluationOutcome.SUCCESS
    assert result.evaluation.outcome in (
        EvaluationOutcome.PARTIAL,
        EvaluationOutcome.FAILED,
    )


def test_prose_goal_without_artifact_request_is_still_success():
    """反向保护：不是所有目标都要文件，纯文案目标不能被一刀切降级。"""
    def ok_executor(capability_id, inputs):
        return {"success": True, "status": "executed"}

    # 纯文案目标会拆出 execution_pipeline（属于硬副作用能力）；账本缺失时执行
    # 内核按 fail-closed 拒绝，所以这里按**网关真实装配**注入 journal。
    rt = AgentRuntime(
        scope="L1",
        capability_executor=ok_executor,
        journal=ExecutionJournal(os.path.join(_TMP, "journal_eval_reality.db")),
    )
    result = rt.run_goal(
        "总结一下今天的工作重点", goal_id="GE3", persist=False
    )
    assert result.evaluation is not None
    assert result.evaluation.outcome is EvaluationOutcome.SUCCESS
