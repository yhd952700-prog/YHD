"""Regression: ``_result_to_dict`` must not raise when the run has no context.

Why this test exists
--------------------
``AIStateManager._result_to_dict`` builds the goal-detail dict served over HTTP.
It collected the plan's task list into a local named ``plan_tasks`` **inside**
``if result.context:`` and then read that same name unconditionally further
down::

    tasks = []
    if result.context:                                  # <- only binding
        plan_tasks = getattr(getattr(ctx, "plan", None), "tasks", None)
        ...
    artifacts = AIStateManager._collect_artifacts(plan_tasks or [])   # <- read

When ``result.context`` is falsy the local was never bound and the read raised
``UnboundLocalError``. This was not theoretical: the crash was observed in
background goal threads during ``scripts/verify_employee_goal_binding.py`` and
``scripts/verify_http_full_closure.py``, and **both gates still exited 0**,
because they assert foreground state and never count a dead worker thread. A
green gate was covering a crash.

The fix binds ``plan_tasks`` before the branch. This test locks that in.
"""

from __future__ import annotations

import pytest

from src.ai.agent_runtime import AgentRunResult, AgentRunState, ExecutionTrace
from src.gateway.ai_management import AIStateManager


def _result(context) -> AgentRunResult:
    return AgentRunResult(
        goal_id="g-regression",
        correlation_id="cid-regression",
        state=AgentRunState.COMPLETED,
        context=context,
        evaluation=None,
        trace=ExecutionTrace(correlation_id="cid-regression"),
    )


@pytest.mark.parametrize(
    "context",
    [None, False, 0, ""],
    ids=["none", "false", "zero", "empty-string"],
)
def test_result_to_dict_survives_a_falsy_context(context) -> None:
    """A run without an execution context must still render a detail dict.

    ``artifacts`` must be an empty list -- no file was written, so inventing a
    path here would be exactly the "reports success while doing nothing"
    failure mode this project forbids.
    """
    entry = AIStateManager._result_to_dict(_result(context), "write a file", "L1")

    assert entry["goal_id"] == "g-regression"
    assert entry["state"] == "completed"
    assert entry["tasks"] == []
    assert entry["artifacts"] == []
    assert entry["error"] is None


def test_collect_artifacts_accepts_an_empty_task_list() -> None:
    """The empty input the branch above produces must be genuinely safe.

    ``[]`` is the contract (``_result_to_dict`` normalises with ``or []``);
    passing ``None`` is out of contract and is not asserted here.
    """
    assert AIStateManager._collect_artifacts([]) == []
