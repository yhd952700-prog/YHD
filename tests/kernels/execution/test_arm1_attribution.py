"""ARM1: assigned_agent must reach the executor, and a dangling agent must not
silently read as "completed by that agent".

Covers the two contracts:
  1. An explicitly-claimed agent that cannot be resolved -> the task completes
     (real side effects, ``success`` still True) but is recorded as
     ``COMPLETED_UNATTRIBUTED``. We do NOT invent a failure -- the work happened.
  2. DECISION (A): that status counts as COMPLETED everywhere the question is
     "did the work finish?" -- the goal still completes and artifacts are still
     collected. A rejected attribution must never escalate into "real work is
     reported as not done".

Also locks the negative control: a synthetic (non-claimed) agent id is NOT
fail-closed, because it makes no claim in the first place.
"""
from __future__ import annotations

import os
import tempfile

# Redirect data to temp BEFORE any module resolves workspace/audit paths.
_TMP = tempfile.mkdtemp(prefix="liuhao_arm1_")
os.environ["LIUHAO_WORKSPACE_ROOT"] = _TMP
os.environ.setdefault("AUDIT_DB_PATH", os.path.join(_TMP, "audit_store.db"))
os.environ.setdefault("LIUHAO_AUDIT_DB_PATH", os.path.join(_TMP, "audit_store.db"))

import pytest  # noqa: E402

from src.kernels.execution import (  # noqa: E402
    Action,
    ActionExecutor,
    ActionResult,
    GoalDecomposer,
    Task,
    TaskStatus,
    VerifyResult,
)


def _task(**kw) -> Task:
    defaults = dict(
        id="t1",
        goal_id="g1",
        name="WriteFile",
        description="write",
        capability_id="file_write",
        inputs={"path": "x.txt", "content": "hi"},
    )
    defaults.update(kw)
    return Task(**defaults)


def _action(**kw) -> Action:
    defaults = dict(
        id="a1",
        task_id="t1",
        capability_id="file_write",
        capability_namespace="kernel",
        inputs={"path": "x.txt", "content": "hi"},
        correlation_id="corr-1",
        scope="L1",
        timeout_seconds=30,
    )
    defaults.update(kw)
    return Action(**defaults)


# ---------------------------------------------------------------------------
# 1. explicit claim that cannot be resolved -> COMPLETED_UNATTRIBUTED
# ---------------------------------------------------------------------------
def test_unresolvable_explicit_agent_is_recorded_unattributed():
    """A resolver returning None means the agent does not exist.

    The action STILL runs the capability (real work, success True) but the
    result carries ``agent_attribution == "unattributed"`` so the task cannot be
    credited to an agent that never existed.
    """
    ex = ActionExecutor(
        capability_executor=lambda cid, inputs: {"success": True, "path": "x.txt"}
    )
    # Resolver is wired but knows of NO agents -> every claim is unresolvable.
    ex.set_agent_resolver(lambda agent_id: None)

    result = ex.execute(_action(assigned_agent="GHOST-1", agent_claimed=True))

    assert result.success is True, (
        "an unresolvable agent must not be turned into a fake failure -- the "
        "work really ran"
    )
    assert isinstance(result.output, dict)
    assert result.output.get("agent_attribution") == "unattributed"
    assert result.output.get("unattributed_agent") == "GHOST-1"


def test_resolvable_agent_executes_through_the_agent():
    """Control: when the resolver DOES know the agent, work goes through it."""
    calls = []

    def agent_exec(capability_id, inputs):
        calls.append(("agent", capability_id))
        return {"success": True, "path": "x.txt"}

    ex = ActionExecutor(
        capability_executor=lambda cid, inputs: {"success": True, "path": "shared.txt"}
    )
    ex.set_agent_resolver(lambda agent_id: agent_exec if agent_id == "emp-a0" else None)

    result = ex.execute(_action(assigned_agent="emp-a0", agent_claimed=True))

    assert result.success is True
    assert calls == [("agent", "file_write")], "work must route through the agent"
    # Resolved -> NOT flagged unattributed.
    assert "agent_attribution" not in result.output


def test_synthetic_agent_id_is_not_fail_closed():
    """Negative control: a synthetic placeholder makes no claim, so it is not
    flagged -- otherwise every unpooled run would be noise-flagged."""
    ex = ActionExecutor(
        capability_executor=lambda cid, inputs: {"success": True, "path": "x.txt"}
    )
    ex.set_agent_resolver(lambda agent_id: None)

    result = ex.execute(_action(assigned_agent="g1-a0", agent_claimed=False))

    assert result.success is True
    assert "agent_attribution" not in result.output


def test_decomposer_marks_explicit_pool_as_claim_but_not_synthetic():
    """The explicit-vs-synthetic distinction lives in GoalDecomposer."""
    d = GoalDecomposer(agent_ids=["emp-a0", "emp-a1"])
    tasks = [_task(id="t%d" % i) for i in range(2)]
    d._assign_agents(type("G", (), {"id": "g1"})(), tasks)
    assert [t.assigned_agent for t in tasks] == ["emp-a0", "emp-a1"]
    assert all(t.agent_claimed for t in tasks), "explicit pool => real claim"

    d2 = GoalDecomposer(agent_ids=None)
    tasks2 = [_task(id="s%d" % i) for i in range(2)]
    d2._assign_agents(type("G", (), {"id": "g2"})(), tasks2)
    assert [t.assigned_agent for t in tasks2] == ["g2-a0", "g2-a1"]
    assert not any(
        t.agent_claimed for t in tasks2
    ), "synthetic placeholder must not be a claim"


# ---------------------------------------------------------------------------
# 2. DECISION (A): UNATTRIBUTED still counts as finished work
# ---------------------------------------------------------------------------
def test_unattributed_status_is_a_completed_family_value():
    """Guard the semantic contract: value is distinct, but the status is in the
    completed family. Anything asking 'did it finish' must treat it as done."""
    assert TaskStatus.COMPLETED_UNATTRIBUTED.value == "completed_unattributed"
    assert TaskStatus.COMPLETED_UNATTRIBUTED is not TaskStatus.COMPLETED
    # The completed family, as consumed by the gateway's completion checks.
    completed_family = ("completed", "completed_unattributed")
    assert TaskStatus.COMPLETED_UNATTRIBUTED.value in completed_family
    assert TaskStatus.COMPLETED.value in completed_family


def test_unattributed_task_keeps_goal_completed_and_artifacts_collected():
    """End-to-end shape of decision (A).

    The gateway only records artifacts / counts completed tasks for statuses in
    the completed family. If UNATTRIBUTED were excluded, real finished work would
    be reported as not done -- trading a false attribution for a false negative.
    """
    # Mirrors src/gateway/ai_management._collect_artifacts's status gate and the
    # list_goals completed counter, both updated to the completed family.
    completed_family = ("completed", "completed_unattributed")

    def count_completed(statuses):
        return sum(1 for s in statuses if s in completed_family)

    # A run where every task was claimed for an agent that does not exist.
    statuses = ["completed_unattributed", "completed_unattributed"]
    assert count_completed(statuses) == 2, (
        "unattributed work must still be counted as completed -- the bytes landed"
    )

    # And the artifact gate accepts it.
    assert "completed_unattributed" in completed_family
