"""
Compute extraction: make `python_compute` actually reachable.

The defect this closes
----------------------
`GoalDecomposer.decompose` emitted a `python_compute` task whenever the goal
merely mentioned "compute"/"calculate"/"计算", and passed the WHOLE SENTENCE as
the task inputs::

    inputs={"goal": goal.natural_language}

But the tool contract (`src/ai/tools_local.py::_resolve_code`) accepts exactly
three shapes — `code`, `expression`, or a `goal` carrying a `python:`/`code:`
directive — and *deliberately refuses to guess Python from prose*. So the
sentence never matched any accepted shape and the task failed 100% of the time
with an honest "needs executable code" rejection. Measured before this change:
every compute-shaped goal ended `failed`.

The fix extracts tool-shaped input, symmetrically with what `file_write` already
did (it has always extracted `path`/`content` rather than forwarding prose).
Both sides now agree on the same contract: explicit shapes are executed, vague
prose yields NO task instead of a task guaranteed to fail.

The honesty property that matters
---------------------------------
A vague goal ("run some code for me") must produce NO compute task. Inventing an
expression from prose is exactly the "reports success without knowing what to
do" failure mode. These tests therefore assert BOTH directions: the specific
goals compute the right answer, and the vague ones refuse.

No LLM, no network. Real AgentRuntime + real RestrictedPython backend; the
workspace is a tmp dir so the frozen HC-01 evidence is never touched.
"""
import os

import pytest

from src.ai.agent_runtime import AgentRuntime
from src.ai.lcore import LCore
from src.kernels.execution import GoalDecomposer


# ── extraction: explicit shapes become tool-shaped input ────────────────────
@pytest.mark.parametrize("goal,expected", [
    ("compute 2+3", {"expression": "2+3"}),
    ("calculate the sum of 2 and 3 using python", {"expression": "(2+3)"}),
    ("sum of 2 and 3", {"expression": "(2+3)"}),
    ("what is the difference of 10 and 4", {"expression": "(10-4)"}),
    ("product of 6 and 7", {"expression": "(6*7)"}),
    ("divide 10 by 2", {"expression": "(10/2)"}),
    # Average is (a+b)/2 — asserting this guards a real bug: treating it as a
    # sum returns a confidently WRONG number, which is worse than refusing.
    ("average of 4 and 8", {"expression": "((4+8)/2)"}),
    ("计算 2*3 的值", {"expression": "2*3"}),
    ("python: result=sum(range(10))", {"code": "result=sum(range(10))"}),
    ("code: result=6*7", {"code": "result=6*7"}),
])
def test_extracts_tool_shaped_input(goal, expected):
    assert GoalDecomposer._extract_compute(goal) == expected


# ── extraction: vague prose yields NOTHING (no invented task) ──────────────
@pytest.mark.parametrize("goal", [
    "run some code for me",
    "transform the data somehow",
    "write a Python script for the report",
    "create a file named report.txt containing hello",
    "remember the meeting time",
    # two numbers + "and", but no arithmetic request -> not an expression
    "2 and 3 are in the report file",
    "search for docs and 4 and 7 items",
])
def test_refuses_to_invent_an_expression(goal):
    """Prose must NOT become a fabricated expression.

    This is the guard that keeps the fix from becoming a new honesty defect:
    a task that cannot be built is not built at all.
    """
    assert GoalDecomposer._extract_compute(goal) is None, (
        f"invented an expression from prose: {goal!r}")


def test_no_compute_task_for_vague_goal():
    """A vague goal still ROUTES compute, with the old honest-failure shape.

    This is deliberately NOT "no task". Narrowing the keyword contract would
    silently break goals that need a human/LLM to supply the code, and the
    existing decomposer contract requires a stated computation request to
    route. The improvement here is only about the INPUTS: a goal that yields an
    expression now really computes, instead of always failing.
    """
    from src.kernels.execution import Goal
    tasks = GoalDecomposer().decompose(
        Goal(id="v", natural_language="run some code for me"))
    compute = [t for t in tasks if t.capability_id == "python_compute"]
    assert len(compute) == 1, "the keyword contract must still route"
    # No expression was invented — it falls back to the honest-failure shape.
    assert compute[0].inputs == {"goal": "run some code for me"}
    assert "expression" not in compute[0].inputs
    assert "code" not in compute[0].inputs


def test_no_fabricated_expression_when_request_has_no_number():
    """'calculate the thing' must not acquire an invented expression."""
    from src.kernels.execution import Goal
    tasks = GoalDecomposer().decompose(
        Goal(id="v2", natural_language="please calculate the thing"))
    compute = [t for t in tasks if t.capability_id == "python_compute"]
    assert len(compute) == 1
    assert compute[0].inputs == {"goal": "please calculate the thing"}


# ── end to end: the capability REALLY runs and returns the RIGHT answer ─────
@pytest.mark.parametrize("goal,expected", [
    ("calculate the sum of 2 and 3 using python", "5"),
    ("what is the difference of 10 and 4", "6"),
    ("product of 6 and 7", "42"),
    ("divide 10 by 2", "5.0"),
    ("average of 4 and 8", "6.0"),
    ("compute 2+3", "5"),
    ("计算 2*3 的值", "6"),
])
def test_compute_runs_end_to_end_and_is_correct(tmp_path, goal, expected):
    """The real proof: RestrictedPython actually executes and the value is right.

    Checking only the extracted expression would prove nothing — this asserts
    the produced RESULT, so a regression that silently returns a wrong number
    fails here.
    """
    ws = os.path.join(str(tmp_path), "ws")
    os.makedirs(ws, exist_ok=True)
    prev = os.environ.get("LIUHAO_WORKSPACE_ROOT")
    os.environ["LIUHAO_WORKSPACE_ROOT"] = ws
    try:
        lcore = LCore(scope="L1", register_local_tools=True)
        rt = AgentRuntime(scope="L1",
                          capability_executor=lcore.capability_executor())
        r = rt.run_goal(goal, goal_id="cc")
        assert r.state.value == "completed", (
            f"{goal!r} must complete; tasks="
            f"{[(t.name, t.status.value, t.error) for t in r.context.plan.tasks]}")

        compute = [t for t in r.context.plan.tasks
                   if t.capability_id == "python_compute"]
        assert len(compute) == 1
        out = compute[0].result
        assert isinstance(out, dict) and out.get("success") is True, out
        assert str(out.get("result", "")).strip() == expected, (
            f"{goal!r} produced {out!r}, expected result {expected!r}")
    finally:
        if prev is None:
            os.environ.pop("LIUHAO_WORKSPACE_ROOT", None)
        else:
            os.environ["LIUHAO_WORKSPACE_ROOT"] = prev


def test_unsafe_code_is_still_rejected(tmp_path):
    """Adding extraction must not weaken the sandbox: `import` stays refused.

    Asserts the TASK fails, which is the observable contract. (A failed task
    carries no ``result`` payload at all, so the check must be on status —
    reaching for ``task.result`` here would itself be the bug.)
    """
    ws = os.path.join(str(tmp_path), "ws2")
    os.makedirs(ws, exist_ok=True)
    prev = os.environ.get("LIUHAO_WORKSPACE_ROOT")
    os.environ["LIUHAO_WORKSPACE_ROOT"] = ws
    try:
        lcore = LCore(scope="L1", register_local_tools=True)
        rt = AgentRuntime(scope="L1",
                          capability_executor=lcore.capability_executor())
        r = rt.run_goal("python: import os; result=os.getcwd()", goal_id="unsafe")
        compute = [t for t in r.context.plan.tasks
                   if t.capability_id == "python_compute"]
        assert compute, "the directive shape must still produce a task"
        assert compute[0].status.value == "failed", (
            f"unsafe code must NOT run: status={compute[0].status.value} "
            f"result={compute[0].result!r}")
        assert r.state.value != "completed", (
            "a goal whose only task was rejected must not report completion")
    finally:
        if prev is None:
            os.environ.pop("LIUHAO_WORKSPACE_ROOT", None)
        else:
            os.environ["LIUHAO_WORKSPACE_ROOT"] = prev


def test_file_write_regression(tmp_path):
    """The file_write path must be untouched by the compute change."""
    ws = os.path.join(str(tmp_path), "ws3")
    os.makedirs(ws, exist_ok=True)
    prev = os.environ.get("LIUHAO_WORKSPACE_ROOT")
    os.environ["LIUHAO_WORKSPACE_ROOT"] = ws
    try:
        lcore = LCore(scope="L1", register_local_tools=True)
        rt = AgentRuntime(scope="L1",
                          capability_executor=lcore.capability_executor())
        r = rt.run_goal('create a file named report.txt containing "hello"',
                        goal_id="fw")
        assert r.state.value == "completed"
        assert os.path.exists(os.path.join(ws, "report.txt"))
        with open(os.path.join(ws, "report.txt"), encoding="utf-8") as fh:
            assert fh.read().strip() == "hello"
    finally:
        if prev is None:
            os.environ.pop("LIUHAO_WORKSPACE_ROOT", None)
        else:
            os.environ["LIUHAO_WORKSPACE_ROOT"] = prev
