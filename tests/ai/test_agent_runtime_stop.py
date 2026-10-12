"""Alignment regression: a human stop request must bind the REAL AgentRuntime.

``AgentRuntime.request_stop`` is the runtime half of the human-override ("human
veto") control surface -- the gateway's ``POST /v1/goals/{id}/stop`` calls it in
production (``src/gateway/ai_management.py``). The existing coverage drives that
control either with a *fake* runtime (``tests/gateway/test_goal_stop_honesty.py``)
or by calling ``ExecutionEngine._execute_plan`` directly
(``tests/kernels/execution/test_goal_cancellation.py``) -- neither exercises
``request_stop`` against the real runtime that production actually uses.

These tests close that gap. The alignment properties under test:

  * stop of a LIVE goal -> the goal terminates CANCELLED at a task boundary;
    remaining tasks never start, and no success is fabricated.
  * stop with no live goal -> honest ``False`` (never a phantom "stoppable").
  * a stop set before the run -> zero tasks execute.
  * after a run finishes, the per-goal stop flag is released -> ``request_stop``
    is ``False`` again (no leak that would misreport a *different* goal as
    stoppable).

Offline, no disk, no audit_store writes (``tests/conftest.py`` redirects
``AUDIT_DB_PATH``). Capability scope is allowed so the test isolates the
override loop rather than capability authorisation.
"""

import threading
from types import SimpleNamespace

import pytest

from src.ai.agent_runtime import AgentRunState, AgentRuntime


@pytest.fixture(autouse=True)
def _allow_scopes(monkeypatch):
    # The point under test is the override loop, not capability authorisation
    # (that lives in the approval tests). Allow any synthetic capability.
    import src.kernels.execution as ex

    monkeypatch.setattr(
        ex,
        "check_capability_scope",
        lambda *a, **k: SimpleNamespace(allowed=True, reason="test-allow"),
    )


# Two chained tasks (WriteFile -> WriteChecksum: the checksum task depends on
# the file task), so the engine iterates once per task and the cooperative stop
# has a clean task boundary to fire on.
_MULTI_TASK_GOAL = "write a file named out.txt containing done"


def test_request_stop_without_live_goal_is_false():
    rt = AgentRuntime(scope="L1")
    # No goal has ever been run under this id -> must not claim a live execution.
    assert rt.request_stop("never-started") is False


def test_human_stop_cancels_a_live_run_at_task_boundary():
    calls = []
    stop_returns = []
    holder = {}

    def executor(capability_id, inputs):
        calls.append(capability_id)
        # Fire the human stop while the FIRST task runs; the goal is live, so
        # the *real* request_stop must find it and report True.
        stop_returns.append(holder["rt"].request_stop("GS"))
        return {"success": True, "status": "executed"}

    rt = AgentRuntime(scope="L1", capability_executor=executor)
    holder["rt"] = rt

    result = rt.run_goal(_MULTI_TASK_GOAL, goal_id="GS")

    assert result.state == AgentRunState.CANCELLED
    assert stop_returns == [True], "the live goal must be found and stopped"
    # Bounded at the task boundary: only the first task ran; the follow-on task
    # never started, and the run is NOT reported as a success.
    assert len(calls) == 1
    assert result.context is None
    assert "cancel" in (result.error or "").lower()


def test_stop_before_run_executes_nothing():
    calls = []

    def executor(capability_id, inputs):
        calls.append(capability_id)
        return {"success": True, "status": "executed"}

    rt = AgentRuntime(scope="L1", capability_executor=executor)
    ev = threading.Event()
    ev.set()  # human aborts up front
    result = rt.run_goal(_MULTI_TASK_GOAL, goal_id="GP", stop_event=ev)

    assert result.state == AgentRunState.CANCELLED
    assert calls == [], "a pre-set stop must abort before any task runs"


def test_request_stop_after_completion_is_false_no_leak():
    def executor(capability_id, inputs):
        return {"success": True, "status": "executed"}

    rt = AgentRuntime(scope="L1", capability_executor=executor)
    done = rt.run_goal(_MULTI_TASK_GOAL, goal_id="GD")
    assert done.state == AgentRunState.COMPLETED

    # The per-goal stop flag is released in ``run_goal``'s ``finally``; without
    # that, a later stop POST for this finished goal would wrongly report that a
    # live execution was stopped.
    assert rt.request_stop("GD") is False
