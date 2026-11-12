"""Alignment regression: ``replan_goal`` must not inflate ``replan_count``.

The historical bug: ``AIStateManager.replan_goal`` unconditionally did
``entry["replan_count"] = prior_entry.get("replan_count", 0) + 1``. When the
runtime *refuses* a replan (it hit ``max_replans`` and returned the prior
result unchanged), the gateway still counted one more replan that never
executed. The stored count feeds retry/quota/alerting logic, so an inflated
count is a quiet lie about how many times the goal actually re-ran.

The honest contract (locked here):
  * ``replan_goal`` threads the stored ``replan_count`` into the prior
    ``AgentRunResult`` so the runtime's own ``max_replans`` bound is enforced
    through the gateway. Without this, the runtime always sees 0 and can
    never refuse -- and a naive ``entry["replan_count"] = result.replan_count``
    would reset the count to 1 on every call after the first.
  * the returned ``replan_count`` is the runtime's AUTHORITATIVE count
    (``result.replan_count``), never an unconditional ``+ 1`` -- so a refused
    replan leaves the count untouched.

The runtime, audit chain, employee store and on-disk goals file are all
stubbed so no frozen evidence (audit_store.db) and no repo-tree artifact is
touched. The runtime's bound contract itself is locked separately by
``tests/ai/test_agent_runtime.py::test_replan_respects_max_bound``; the fake
here mirrors it.
"""

import pytest

from src.ai.agent_runtime import AgentRunResult, AgentRunState, ExecutionTrace
from src.gateway.ai_management import AIStateManager


@pytest.fixture
def fresh_manager(monkeypatch):
    # AIStateManager is a process-wide singleton. Save/restore it so this test
    # cannot contaminate other test modules.
    saved = AIStateManager._instance
    AIStateManager._instance = None
    mgr = AIStateManager()

    # No disk, no audit_store writes.
    monkeypatch.setattr(mgr, "_save_goals", lambda: None)
    monkeypatch.setattr(mgr, "_load_goals", lambda: None)
    monkeypatch.setattr(mgr, "_audit_goal_stop", lambda goal_id, was_running: None)

    yield mgr
    AIStateManager._instance = saved


class _BoundRuntime:
    """Mirrors ``AgentRuntime.replan``'s bound contract: return ``prior``
    unchanged when at the cap (a refused replan -- nothing re-ran), else a
    re-run result whose ``replan_count`` is + 1."""

    max_replans = 2

    def replan(self, prior, **kwargs):
        if prior.replan_count >= self.max_replans:
            return prior  # refused: no re-run actually happened
        return AgentRunResult(
            goal_id=prior.goal_id,
            correlation_id=prior.correlation_id,
            state=AgentRunState.COMPLETED,
            context=None,
            evaluation=None,
            trace=ExecutionTrace(correlation_id=prior.correlation_id),
            error=None,
            replan_count=prior.replan_count + 1,
        )


def _seed(mgr, goal_id, replan_count, state="failed"):
    with mgr._goal_lock:
        mgr._goals[goal_id] = {
            "goal_id": goal_id,
            "state": state,
            "natural_language": "do the thing",
            "scope": "L1",
            "error": None,
            "replan_count": replan_count,
            "replan_suggested": False,
            "trace": [],
            "tasks": [],
            "artifacts": [],
            "evaluation": None,
            "created_at": 0.0,
            "project_id": None,
        }


def test_replan_goal_does_not_increment_count_when_runtime_refuses(
    fresh_manager, monkeypatch
):
    mgr = fresh_manager
    _seed(mgr, "G-MAX", replan_count=2)  # already at the cap
    monkeypatch.setattr(mgr, "_ensure_runtime", lambda: _BoundRuntime())

    entry = mgr.replan_goal("G-MAX")

    # The runtime refused (returned prior unchanged) -> NO replan executed ->
    # the count must stay 2, not become 3. This fails on the un-fixed code
    # (which writes prior_entry["replan_count"] + 1 == 3).
    assert entry["replan_count"] == 2
    assert entry["state"] == "failed"


def test_replan_goal_increments_count_on_real_replan(fresh_manager, monkeypatch):
    mgr = fresh_manager
    _seed(mgr, "G-ONE", replan_count=0)  # below the cap -> a real re-run
    monkeypatch.setattr(mgr, "_ensure_runtime", lambda: _BoundRuntime())

    entry = mgr.replan_goal("G-ONE")

    # A real re-run happened -> count advances 0 -> 1.
    assert entry["replan_count"] == 1
    assert entry["state"] == "completed"
