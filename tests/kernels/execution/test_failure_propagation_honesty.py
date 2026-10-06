"""Failure-propagation honesty: a REAL executor failure must surface as failure.

Companion to ``test_real_execution_wired.py``, which covers the first half of
the defect ("no executor wired => must not claim success"). This file covers the
second half: with a real executor **wired**, when that executor fails the goal
path must report failure *with the root cause* -- never swallow the exception
and report success.

Structure:
  * Cases 1-2 are honest behaviours that hold today. They are LOCKED here so a
    future refactor cannot quietly regress them into false success.
  * Cases 3-5 are RESIDUAL GAPS found by this audit. They are documented with
    ``xfail`` (non-strict) so the gap is machine-checkable and cannot be
    forgotten: if it is ever fixed the test reports XPASS rather than silently
    staying green. Each reason names the exact responsible code site -- see the
    accompanying inventory report to team-lead.

Data is redirected to temp dirs; the frozen HC-01 audit_store.db is untouched.
"""
from __future__ import annotations

import os
import tempfile

import pytest

# REDIR all data to temp BEFORE any module resolves workspace/audit paths.
_TMP = tempfile.mkdtemp(prefix="liuhao_failprop_")
os.environ["LIUHAO_WORKSPACE_ROOT"] = _TMP
os.environ.setdefault("AUDIT_DB_PATH", os.path.join(_TMP, "audit_store.db"))
os.environ.setdefault("LIUHAO_AUDIT_DB_PATH", os.path.join(_TMP, "audit_store.db"))

from src.ai.agent_runtime import AgentRuntime, AgentRunState  # noqa: E402

GOAL = "Create a file named boom.txt containing 'nope'"


def _file(name: str) -> str:
    return os.path.join(_TMP, name)


def _task_reasons(res) -> str:
    return " ".join(t.error or "" for t in res.context.plan.tasks)


# ---------------------------------------------------------------------------
# LOCKED: honest failure propagation (these hold today)
# ---------------------------------------------------------------------------
def test_raising_executor_is_reported_as_failure_with_root_cause():
    """An executor that RAISES must fail the goal and preserve the reason."""

    def boom(capability_id, inputs):
        raise RuntimeError("tool backend exploded")

    res = AgentRuntime(scope="L1", capability_executor=boom).run_goal(
        GOAL, goal_id="fp-raise", persist=False
    )

    assert res.state == AgentRunState.FAILED, "a raising executor must not be 'completed'"
    # Nothing was executed, so no artifact may exist.
    assert not os.path.exists(_file("boom.txt"))
    # The root cause must survive -- not be flattened into "Max retries exceeded".
    assert "tool backend exploded" in _task_reasons(res)
    # The evaluation must agree it failed.
    assert res.evaluation is not None
    assert res.evaluation.outcome.value == "failed"


def test_explicit_failure_dict_is_reported_as_failure_with_root_cause():
    """An executor returning ``success: False`` must fail, not be masked."""

    def refused(capability_id, inputs):
        return {"success": False, "status": "rejected",
                "error": "sandbox refused the write"}

    res = AgentRuntime(scope="L1", capability_executor=refused).run_goal(
        GOAL, goal_id="fp-explicit", persist=False
    )

    assert res.state == AgentRunState.FAILED
    assert not os.path.exists(_file("boom.txt"))
    assert "sandbox refused the write" in _task_reasons(res)


def test_no_artifact_claim_is_not_rejected_by_the_verifier_TODAY():
    """The verifier is NOT a control here: a success claim with no artifact and
    no verification criteria is scored as a PASS (1.0).

    This test LOCKS IN THE TRUE CURRENT BEHAVIOUR and is deliberately named
    ``..._TODAY`` so it can never be mistaken for a passing control. Measured
    with ``Verifier.verify``: when a capability returns
    ``{"success": True}`` and the task defines NO ``expected_outputs``, the
    verifier reaches the "no criteria - assume success if action succeeded"
    branch (src/kernels/execution/__init__.py:859-866) and returns
    ``score=1.0`` / ``VerifyResult.SUCCESS``.

    CONSEQUENCE: the verifier launders an unproven success claim into a pass.
    That is a real honesty defect (worse than GAP B below, which assumed the
    verifier scored 0.0 and only that the verdict was dropped). Fixing it means
    changing execution-kernel behaviour -- OWNED OUTSIDE THIS FILE'S SCOPE,
    pending team-lead authorisation. Until then this test keeps the fact
    visible and machine-checkable instead of silently documenting a falsehood.
    """

    def claims_success_without_writing(capability_id, inputs):
        return {"success": True, "status": "executed"}

    res = AgentRuntime(scope="L1",
                       capability_executor=claims_success_without_writing).run_goal(
        GOAL, goal_id="fp-verifier", persist=False
    )

    assert res.context.verification_results, "no verification was performed"
    scores = {v.score for v in res.context.verification_results.values()}
    # TRUTH (measured, not assumed): the verifier scores this 1.0 -- it does NOT
    # catch the missing artifact. If a future fix makes this 0.0, this test
    # fails loudly and must be renamed back to a control assertion.
    assert scores == {1.0}, (
        "verifier behaviour changed: an unproven success claim no longer scores "
        "1.0. If this is now caught (score 0.0), rename this test and promote it "
        "to a real control."
    )
    assert not os.path.exists(_file("boom.txt"))


# ---------------------------------------------------------------------------
# RESIDUAL GAPS (documented, not yet fixed)
# ---------------------------------------------------------------------------
@pytest.mark.xfail(
    strict=False,
    reason=(
        "GAP A: ActionExecutor.execute treats a NON-DICT executor return as "
        "success -- the else-branch wraps it as {'status': 'executed', "
        "'result': raw} with ok=True (src/kernels/execution/__init__.py). A "
        "wired executor that returns None therefore reports completed although "
        "nothing ran. Fixing it changes execution-kernel behaviour: pending "
        "team-lead authorisation."
    ),
)
def test_executor_returning_none_is_not_reported_as_success():
    """A wired executor returning ``None`` did nothing -- must not be success."""
    res = AgentRuntime(scope="L1", capability_executor=lambda c, i: None).run_goal(
        GOAL, goal_id="fp-none", persist=False
    )
    assert res.state == AgentRunState.FAILED
    assert not os.path.exists(_file("boom.txt"))


@pytest.mark.xfail(
    strict=False,
    reason=(
        "GAP B: AgentRuntime.run_goal derives state from ctx.failed_tasks ONLY "
        "(src/ai/agent_runtime.py:326-330), ignoring verification results. A "
        "task whose action 'succeeded' but whose VERIFICATION did not prove the "
        "artifact therefore yields state=completed. (Corrected 2026-10-06: an "
        "earlier draft of this file claimed the verifier scored 0.0 here; the "
        "measured truth is the verifier scores 1.0 -- see "
        "test_no_artifact_claim_is_not_rejected_by_the_verifier_TODAY. So the "
        "goal completes on an UNPROVEN success.)"
    ),
)
def test_verification_failure_is_not_reported_as_completed():
    """An unproven 'success' must not be laundered into a completed goal."""

    def claims_success_without_writing(capability_id, inputs):
        return {"success": True, "status": "executed"}

    res = AgentRuntime(scope="L1",
                       capability_executor=claims_success_without_writing).run_goal(
        GOAL, goal_id="fp-verdict", persist=False
    )
    assert res.state == AgentRunState.FAILED


@pytest.mark.xfail(
    strict=False,
    reason=(
        "GAP C: Evaluator reports EvaluationOutcome.SUCCESS with "
        "overall_score=0.0. _determine_outcome returns trivially SUCCESS for an "
        "empty criterion set, and the 'alignment reality-check' only consults "
        "failed/completed TASK counts -- never verification scores "
        "(src/kernels/evaluation/__init__.py:238-244). So a goal that was never "
        "actually proven (verification scored 1.0 on an unproven claim, overall "
        "0.0) is still evaluated as SUCCESS -- a self-contradictory verdict."
    ),
)
def test_evaluation_does_not_report_success_at_score_zero():
    """SUCCESS with a 0.0 score is a self-contradictory verdict."""

    def claims_success_without_writing(capability_id, inputs):
        return {"success": True, "status": "executed"}

    res = AgentRuntime(scope="L1",
                       capability_executor=claims_success_without_writing).run_goal(
        GOAL, goal_id="fp-eval", persist=False
    )
    assert res.evaluation is not None
    assert res.evaluation.outcome.value != "success", (
        f"evaluation reported {res.evaluation.outcome.value} at score "
        f"{res.evaluation.overall_score}"
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
