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


def test_completed_execution_with_no_criteria_is_success():
    """Control: a goal whose task genuinely completes (no criteria) stays SUCCESS."""
    def ok_executor(capability_id, inputs):
        return {"success": True, "status": "executed"}

    rt = AgentRuntime(scope="L1", capability_executor=ok_executor)
    result = rt.run_goal(
        "write file plan.txt with hello", goal_id="GE2", persist=False
    )
    assert result.evaluation is not None
    assert result.evaluation.outcome is EvaluationOutcome.SUCCESS
