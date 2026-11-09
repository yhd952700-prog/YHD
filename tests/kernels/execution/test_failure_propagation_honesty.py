"""Failure-propagation honesty: a REAL executor failure must surface as failure.

Companion to ``test_real_execution_wired.py``, which covers the first half of
the defect ("no executor wired => must not claim success"). This file covers the
second half: with a real executor **wired**, when that executor fails the goal
path must report failure *with the root cause* -- never swallow the exception
and report success.

Structure:
  * Cases 1-2 are honest behaviours that hold today. They are LOCKED here so a
    future refactor cannot quietly regress them into false success.
  * Cases 3 is a RESIDUAL GAP that has since been FIXED in the execution kernel
    (non-dict executor returns now fail closed as ``unconfirmed``). The former
    ``xfail`` was removed so it is a live regression lock -- if the old
    "wrap non-dict as executed" branch comes back, this test goes red.
  * Cases 4-5 are still RESIDUAL GAPS. They are documented with **strict**
    ``xfail`` so they cannot be silently green: if the defect is ever fixed the
    run goes red (XPASS(strict)) and forces the marker to be removed. Each
    reason names the exact responsible code site -- see the accompanying
    inventory report to team-lead.

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


def test_injected_python_zero_division_fails_honestly():
    """Soak incident 2026-11-07 14:10:58: ``goal 55b0b679 / task ce59f916``.

    The soak harness injects ``python: result = 1/0`` every 30th goal to
    exercise the real failure path. The ZeroDivisionError is raised by the
    GOAL'S OWN CODE in the sandbox, not by runtime arithmetic. Regression lock:
    it must fail the goal with the root cause preserved, must not crash the
    runtime, and must not be retried into a false success. (Four alerts per
    action id are the 1 initial + 3 retry attempts of that same action.)
    """
    from src.ai.tools_local import python_compute

    res = AgentRuntime(
        scope="L1", capability_executor=lambda cap, inputs: python_compute(**inputs)
    ).run_goal("python: result = 1/0", goal_id="fp-zd-soak", persist=False)

    assert res.state == AgentRunState.FAILED
    assert "ZeroDivisionError: division by zero" in _task_reasons(res)
    assert res.evaluation is not None
    assert res.evaluation.outcome.value == "failed"


def test_no_artifact_claim_is_not_reported_as_verified_success():
    """CONTROL (was ``..._is_not_rejected_by_the_verifier_TODAY``): an unproven
    success claim is NOT graded as a verified pass.

    History: this test originally asserted the verifier returned 0.0, but
    measurement showed it returned 1.0 -- the ``execution/__init__.py`` "no
    criteria -> assume success" branch laundered an executor that wrote nothing
    into a fully-verified pass. That branch has since been fixed: with no
    criteria the verifier now returns UNVERIFIED / 0.0. This test is the
    regression control for that fix.
    """

    def claims_success_without_writing(capability_id, inputs):
        return {"success": True, "status": "executed"}

    res = AgentRuntime(scope="L1",
                       capability_executor=claims_success_without_writing).run_goal(
        GOAL, goal_id="fp-verifier", persist=False
    )

    assert res.context.verification_results, "no verification was performed"
    # The verifier must not certify a success it cannot prove.
    assert all(v.score < 1.0 for v in res.context.verification_results.values()), (
        "verifier graded an unproven success claim as fully verified "
        "(score 1.0) -- the 'no criteria -> assume success' regression is back"
    )
    assert not os.path.exists(_file("boom.txt"))


# ---------------------------------------------------------------------------
# RESIDUAL GAPS (documented, not yet fixed)
#
# Strict xfail: a future fix makes XPASS a *failure*, which forces whoever fixed
# it to delete the marker. Non-strict xfail let both "still broken" and "already
# fixed" look the same (green) -- that is the假绿 this file used to have.
# ---------------------------------------------------------------------------
def test_executor_returning_none_is_not_reported_as_success():
    """GAP A (FIXED 2026-11-08): a wired executor returning ``None`` did nothing.

    The old defect wrapped a non-dict return as ``{'status': 'executed'}`` with
    ok=True, so the goal reported success. The execution kernel now fails
    closed (``status: unconfirmed``, ok=False) -- see
    ``src/kernels/execution/__init__.py``. Kept as a regression lock.
    """
    res = AgentRuntime(scope="L1", capability_executor=lambda c, i: None).run_goal(
        GOAL, goal_id="fp-none", persist=False
    )
    assert res.state == AgentRunState.FAILED
    assert not os.path.exists(_file("boom.txt"))


@pytest.mark.xfail(
    strict=True,
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
    strict=True,
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
