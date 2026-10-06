"""Regression: the planner contract's SECOND layer — required-inputs.

Layer 1 (capability existence, commit ffb298a7) refuses a plan whose capability
has no executor. Layer 2 (this file) refuses a plan whose capability HAS an
executor but whose *task supplies none of the inputs that executor requires*.

The concrete case: the deterministic decomposer injects a ``python_compute``
task whenever the goal text contains "run" — even for a pure file-write goal —
and gives it only the raw goal (``inputs={'goal': ...}``). ``python_compute``
does NOT guess Python from prose and rejects such a task. Without layer 2 the
run writes the file, the doomed Compute fails, and the goal is reported failed
*after* a real side effect happened. With layer 2 the goal is refused at PLAN
time, workspace empty, with a message naming the capability and the inputs it
needed.

Falsifiable: on planner_contract.py WITHOUT layer 2, every ``..._refused`` test
below goes RED (no ``PlannerInputError`` is raised); with layer 2 they GREEN.
"""
from __future__ import annotations

import os

import pytest

from src.ai.agent_runtime import AgentRuntime
from src.ai.lcore import LCore
from src.ai.planner_contract import (
    ExecutableAwareDecomposer,
    PlannerContractError,
    PlannerInputError,
)
from src.kernels.execution import Goal, GoalDecomposer

DOOMED_GOAL = 'create a file named beta.txt containing "second run"'
LEGIT_COMPUTE_GOAL = "calculate the sum of 2 and 3 using python"


def _wrapped(lcore, **kw):
    return ExecutableAwareDecomposer(
        GoalDecomposer(), capability_provider=lcore.executable_capabilities(), **kw
    )


@pytest.fixture()
def lcore(tmp_path, monkeypatch):
    # Redirect every side-effecting path into tmp_path: workspace (file_write
    # binds its root at registration), memory DB, audit DB. Keeps the repo
    # clean and the test hermetic.
    monkeypatch.setenv("LIUHAO_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("LIUHAO_MEMORY_DB_PATH", str(tmp_path / "mem.db"))
    monkeypatch.setenv("AUDIT_DB_PATH", str(tmp_path / "audit.db"))
    return LCore(scope="L1", register_local_tools=True)


def test_doomed_compute_task_refused_at_plan_time(lcore):
    """A python_compute task with no code/expression is refused (named inputs)."""
    with pytest.raises(PlannerInputError) as ei:
        _wrapped(lcore).decompose(Goal(id="g1", natural_language=DOOMED_GOAL, scope="L1"))
    msg = str(ei.value)
    assert "python_compute" in msg
    assert "code" in msg and "expression" in msg


def test_legit_compute_goal_still_allowed(lcore):
    """A compute goal the decomposer gave an `expression` must NOT be refused."""
    tasks = _wrapped(lcore).decompose(
        Goal(id="g2", natural_language=LEGIT_COMPUTE_GOAL, scope="L1")
    )
    caps = [t.capability_id for t in tasks]
    assert "python_compute" in caps


def test_input_layer_is_the_gate_not_something_else(lcore):
    """With input_requirements={} the doomed task passes (proves layer 2 is the cause)."""
    tasks = _wrapped(lcore, input_requirements={}).decompose(
        Goal(id="g3", natural_language=DOOMED_GOAL, scope="L1")
    )
    assert any(t.capability_id == "python_compute" for t in tasks)


def test_missing_capability_still_raises_base_error(lcore):
    """Layer 1 unaffected: a truly missing capability raises the base error, not input error."""
    with pytest.raises(PlannerContractError) as ei:
        _wrapped(lcore).decompose(
            Goal(id="g4", natural_language="search for information about quantum", scope="L1")
        )
    assert not isinstance(ei.value, PlannerInputError)
    assert "network_bus" in str(ei.value)


def test_input_error_is_a_contract_error(lcore):
    """PlannerInputError subclasses PlannerContractError (generic handling still works)."""
    with pytest.raises(PlannerContractError):
        _wrapped(lcore).decompose(Goal(id="g5", natural_language=DOOMED_GOAL, scope="L1"))


# --------------------------------------------------------------------------- #
# End-to-end through the real run path: refused at plan time, NO side effect.
# --------------------------------------------------------------------------- #
def test_end_to_end_run_keyword_goal_has_no_partial_side_effect(lcore, tmp_path):
    """The whole point: the 'run' file-write goal is refused BEFORE writing beta.txt."""
    rt = AgentRuntime(
        scope="L1",
        capability_executor=lcore.capability_executor(),
        executable_capabilities=lcore.executable_capabilities(),
    )
    result = rt.run_goal(DOOMED_GOAL, goal_id="g6")
    assert result.state.value == "failed"
    # The honest reason, not a mid-run crash.
    assert result.error is not None
    assert "required inputs" in result.error or "python_compute" in result.error
    # No file was produced: refusal happened at plan time, before any task ran.
    assert not (tmp_path / "beta.txt").exists()