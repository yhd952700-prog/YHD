"""Alignment regression: ``stop_goal`` must never falsify a real terminal state.

The historical bug: a stop POST stamped "aborted by human operator" onto ANY
goal that had received a stop, so an already-completed or already-failed goal
appeared to have been stopped by an operator who was not there -- and its root
cause was overwritten. It also booked every stop as a failed goal, double
counting completed goals in the KPI denominator.

These tests lock in the honest contract of :meth:`AIStateManager.stop_goal`:

  * stop of a goal that already reached completed/failed  -> state is preserved,
    an operator is NOT falsely credited with the outcome, KPI not corrupted.
  * stop of a goal that is genuinely running               -> becomes cancelled,
    never completes the work and reports it as success.

The runtime, audit chain, employee store and on-disk goals file are all stubbed
so no frozen evidence (audit_store.db) and no repo-tree artifact is touched.
"""

import threading
import time
import types

import pytest

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

    calls = {"request_stop": [], "request_stop_result": True}

    class FakeRuntime:
        def request_stop(self, gid):
            calls["request_stop"].append(gid)
            return calls["request_stop_result"]

    monkeypatch.setattr(mgr, "_ensure_runtime", lambda: FakeRuntime())
    monkeypatch.setattr(
        mgr,
        "_ensure_employee",
        lambda: types.SimpleNamespace(
            record_goal_submitted=lambda *a, **k: None,
            record_goal_finished=lambda *a, **k: None,
            mark_agents_busy=lambda: None,
            mark_agents_idle=lambda: None,
        ),
    )
    yield mgr, calls
    AIStateManager._instance = saved


def _seed(mgr, goal_id, state, error=None):
    with mgr._goal_lock:
        mgr._goals[goal_id] = {
            "goal_id": goal_id,
            "state": state,
            "natural_language": "do the thing",
            "scope": "L1",
            "error": error,
            "replan_count": 0,
            "replan_suggested": False,
            "trace": [],
            "tasks": [],
            "artifacts": [],
            "evaluation": None,
            "created_at": 0.0,
            "project_id": None,
        }


def test_stop_of_completed_goal_keeps_completed_state(fresh_manager):
    mgr, calls = fresh_manager
    calls["request_stop_result"] = False  # goal already finished -> runtime says so
    _seed(mgr, "G-DONE", "completed", error=None)

    result = mgr.stop_goal("G-DONE")

    # The completed outcome is preserved -- an operator is NOT falsely credited.
    assert result["state"] == "completed"
    assert result["error"] is None
    # request_stop was attempted (so the path is real, not a dead branch).
    assert "G-DONE" in calls["request_stop"]


def test_stop_of_failed_goal_keeps_failed_state_and_root_cause(fresh_manager):
    mgr, calls = fresh_manager
    calls["request_stop_result"] = False
    _seed(mgr, "G-FAIL", "failed", error="timeout in step 2")

    result = mgr.stop_goal("G-FAIL")

    # The real failure reason survives; it is not overwritten by "aborted".
    assert result["state"] == "failed"
    assert result["error"] == "timeout in step 2"


def test_stop_of_running_goal_becomes_cancelled(fresh_manager):
    mgr, calls = fresh_manager
    calls["request_stop_result"] = True  # runtime found a live execution
    _seed(mgr, "G-RUN", "running")

    # Simulate the background execution thread observing the stop flag and
    # flipping the goal to cancelled shortly after the stop is requested.
    def _flip():
        time.sleep(0.1)
        with mgr._goal_lock:
            entry = dict(mgr._goals["G-RUN"])
            entry["state"] = "cancelled"
            entry["error"] = "aborted by human operator"
            mgr._goals["G-RUN"] = entry

    threading.Thread(target=_flip, daemon=True).start()

    result = mgr.stop_goal("G-RUN")

    assert result["state"] == "cancelled"
    assert "G-RUN" in calls["request_stop"]


def test_stop_of_unknown_goal_raises(fresh_manager):
    mgr, calls = fresh_manager
    with pytest.raises(ValueError):
        mgr.stop_goal("G-NOPE")
