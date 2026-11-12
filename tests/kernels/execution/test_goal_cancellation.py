"""Alignment regression: a human stop request must never be silently lost.

These tests exercise the REAL cooperative-cancellation loop in
``ExecutionEngine._execute_plan`` (the code path that honours a
:class:`threading.Event` stop flag between tasks and before each retry).

The alignment property under test:

    When a stop is requested while a goal is genuinely executing, the goal
    terminates as CANCELLED and the work is bounded at a task boundary -- it
    does NOT run every remaining task to completion and silently report
    success, nor does it launder a completed/failed goal into a cancellation.

No singletons, no disk, no audit_store writes. The capability executor is a
controlled slow double so the goal is observably "running" for seconds.
"""

import threading
import time
from types import SimpleNamespace

import pytest

from src.kernels.execution import (
    ExecutionContext,
    ExecutionEngine,
    ExecutionPlan,
    Goal,
    GoalCancelled,
    Task,
    TaskStatus,
)


# Allow any synthetic capability id so the slow double can "execute" it without
# tripping the production scope check (we are testing the cancellation loop, not
# capability authorisation -- that lives in alignment-control's approval tests).
def _allow_all_scope(*args, **kwargs):
    return SimpleNamespace(allowed=True, reason="test-allow")


@pytest.fixture(autouse=True)
def _allow_scopes(monkeypatch):
    import src.kernels.execution as ex

    monkeypatch.setattr(ex, "check_capability_scope", _allow_all_scope)


def _make_engine(sleep_per_task: float = 0.4):
    def slow_executor(capability_id, inputs):
        time.sleep(sleep_per_task)
        return {"success": True, "status": "executed"}

    return ExecutionEngine(scope="L1", capability_executor=slow_executor)


def _make_plan(n_tasks: int = 3, goal_id: str = "G-CANCEL"):
    # Chain the tasks (t[i] depends on t[i-1]) so the engine makes each one
    # ready only after the previous completes. This gives a clean per-task
    # boundary for the stop check to fire between tasks, rather than running
    # every independent task in one for-loop pass.
    tasks = [
        Task(
            id=f"t{i}",
            goal_id=goal_id,
            name=f"task-{i}",
            description=f"task {i}",
            capability_id="fake_cap",
            inputs={"i": i},
            dependencies=[f"t{i - 1}"] if i > 0 else [],
        )
        for i in range(n_tasks)
    ]
    plan = ExecutionPlan(id="p1", goal_id=goal_id, tasks=tasks)
    return plan


def _run_plan(engine, plan, goal_id, stop_event, capture):
    goal = Goal(id=goal_id, natural_language="cancel me", correlation_id="corr-1")
    ctx = ExecutionContext(
        goal=goal, plan=plan, context_kernel=engine.context_kernel
    )
    try:
        engine._execute_plan(ctx, None, stop_event)
        capture["raised"] = None
    except GoalCancelled:
        capture["raised"] = "GoalCancelled"
        capture["completed"] = len(ctx.completed_tasks)
        capture["ctx"] = ctx
    return ctx


def test_stop_before_any_task_runs_completes_nothing():
    """A stop set before the loop starts aborts before the first task attempt."""
    engine = _make_engine()
    plan = _make_plan()
    stop_event = threading.Event()
    stop_event.set()  # human aborts up front
    capture = {}
    ctx = _run_plan(engine, plan, "G0", stop_event, capture)

    assert capture["raised"] == "GoalCancelled"
    assert len(ctx.completed_tasks) == 0
    assert all(t.status == TaskStatus.PENDING for t in plan.tasks)


def test_stop_mid_run_bounds_work_at_task_boundary():
    """Stop during execution: completed tasks stay done, the rest stay PENDING.

    This is the core anti-silent-loss guarantee: the veto does not let every
    remaining task run to completion. The executor itself raises the stop flag
    the moment task-0 begins, so the test is deterministic (no thread-schedule
    race): task-0 still finishes, but task-1 / task-2 never start.
    """
    stop_event = threading.Event()

    def slow_executor(capability_id, inputs):
        stop_event.set()  # human abort fires as soon as the goal begins
        time.sleep(0.4)
        return {"success": True, "status": "executed"}

    engine = ExecutionEngine(scope="L1", capability_executor=slow_executor)
    plan = _make_plan(n_tasks=3)
    capture = {}

    t = threading.Thread(
        target=_run_plan, args=(engine, plan, "G1", stop_event, capture)
    )
    t.start()
    t.join(timeout=5.0)
    assert not t.is_alive(), "execution thread must observe the stop and return"

    assert capture["raised"] == "GoalCancelled"
    # Exactly one task completed; the other two never started.
    assert len(capture["ctx"].completed_tasks) == 1
    pending = [t_ for t_ in plan.tasks if t_.status == TaskStatus.PENDING]
    assert len(pending) == 2


def test_no_stop_runs_every_task_to_completion():
    """Control case: without a stop, all tasks really complete.

    Guards against the test passing trivially because the executor never runs.
    """
    engine = _make_engine(sleep_per_task=0.2)
    plan = _make_plan(n_tasks=3)
    stop_event = threading.Event()  # never set
    capture = {}
    ctx = _run_plan(engine, plan, "G2", stop_event, capture)

    assert capture["raised"] is None
    assert len(ctx.completed_tasks) == 3
    assert all(t.status == TaskStatus.COMPLETED for t in plan.tasks)


def test_stop_before_retry_is_honoured_within_a_long_task():
    """The intra-task check (before each retry) must also honour the stop.

    A failing executor that would otherwise burn its whole retry budget
    (max_retries=3 -> up to 4 attempts). The executor raises the stop flag on
    its very first call, so the test is deterministic: the stop is honoured
    before any retry, and far fewer than 4 attempts occur.
    """
    stop_event = threading.Event()
    attempts = {"n": 0}

    def flaky_executor(capability_id, inputs):
        attempts["n"] += 1
        stop_event.set()  # human abort fires as soon as the attempt starts
        time.sleep(0.15)
        return {"success": False, "error": "boom"}

    engine = ExecutionEngine(scope="L1", capability_executor=flaky_executor)
    plan = _make_plan(n_tasks=1, goal_id="G3")
    plan.tasks[0].max_retries = 3
    capture = {}

    t = threading.Thread(
        target=_run_plan, args=(engine, plan, "G3", stop_event, capture)
    )
    t.start()
    t.join(timeout=5.0)
    assert not t.is_alive()

    assert capture["raised"] == "GoalCancelled"
    # Without a stop the run would attempt up to 4 times; the stop is honoured
    # before the second attempt, so at most one attempt ran.
    assert attempts["n"] <= 1
