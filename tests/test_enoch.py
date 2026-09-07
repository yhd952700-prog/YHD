"""ENOCH Long-Running Mission Engine — MASTER-SPEC Phase 13 (§58-60) tests.

Focus: behaviour, not existence. Covers the mission lifecycle state machine,
cross-process-restart persistence, checkpoint/resume, the §60 capability
scheduler, and the deterministic MissionRunner loop.
"""
import uuid

import pytest

from src.ai.enoch import (
    MissionStatus,
    Mission,
    MissionStore,
    Scheduler,
    MissionRunner,
    create_mission,
)


def _new_mission(description="demo mission", capability="search"):
    return create_mission(description=description, required_capability=capability)


def _always_ok(_result):
    return True


def _always_fail(_result):
    return False


def _echo_execute(observation):
    return {"observation": observation, "ok": True}


class _CapAgent:
    """Lightweight agent double advertising a capability (no provider needed)."""

    def __init__(self, agent_id, capability):
        self.id = agent_id
        self.capability = capability


# ---------------------------------------------------------------------------
# Mission model & status enum
# ---------------------------------------------------------------------------
class TestMissionModel:
    def test_status_enum_values(self):
        assert MissionStatus.CREATED.value == "created"
        assert MissionStatus.RUNNING.value == "running"
        assert MissionStatus.CHECKPOINTED.value == "checkpointed"
        assert MissionStatus.NEEDS_REPLAN.value == "needs_replan"
        assert MissionStatus.COMPLETED.value == "completed"
        assert MissionStatus.FAILED.value == "failed"

    def test_mission_defaults(self):
        m = _new_mission()
        assert m.status == MissionStatus.CREATED
        assert m.attempts == 0
        assert m.checkpoint is None
        assert m.required_capability == "search"
        assert isinstance(m.id, str) and m.id


# ---------------------------------------------------------------------------
# MissionStore persistence (the key §59 invariant)
# ---------------------------------------------------------------------------
class TestMissionStorePersistence:
    def test_save_then_load_roundtrip(self, tmp_path):
        store = MissionStore(str(tmp_path))
        m = _new_mission()
        store.save(m)
        loaded = store.load(m.id)
        assert loaded is not None
        assert loaded.id == m.id
        assert loaded.description == m.description
        assert loaded.attempts == 0
        assert loaded.status == MissionStatus.CREATED

    def test_load_missing_returns_none(self, tmp_path):
        store = MissionStore(str(tmp_path))
        assert store.load("does-not-exist") is None

    def test_cross_instance_restart_recovery(self, tmp_path):
        """New store pointed at the SAME dir reads a previously saved mission."""
        store_a = MissionStore(str(tmp_path))
        m = _new_mission("persist me")
        m.attempts = 2
        m.status = MissionStatus.NEEDS_REPLAN
        store_a.save(m)

        # Simulate a process restart: brand-new store, same directory.
        store_b = MissionStore(str(tmp_path))
        recovered = store_b.load(m.id)
        assert recovered is not None
        assert recovered.id == m.id
        assert recovered.description == "persist me"
        assert recovered.attempts == 2
        assert recovered.status == MissionStatus.NEEDS_REPLAN

    def test_list_missions(self, tmp_path):
        store = MissionStore(str(tmp_path))
        m1 = _new_mission("one")
        m2 = _new_mission("two")
        store.save(m1)
        store.save(m2)
        ids = {m.id for m in store.list_missions()}
        assert ids == {m1.id, m2.id}

    def test_delete(self, tmp_path):
        store = MissionStore(str(tmp_path))
        m = _new_mission()
        store.save(m)
        assert store.delete(m.id) is True
        assert store.load(m.id) is None
        assert store.delete(m.id) is False


# ---------------------------------------------------------------------------
# Checkpoint / resume
# ---------------------------------------------------------------------------
class TestCheckpointResume:
    def test_runner_records_checkpoint_and_persists(self, tmp_path):
        store = MissionStore(str(tmp_path))
        m = _new_mission()
        store.save(m)
        runner = MissionRunner(store, _echo_execute, _always_ok)
        summary = runner.run(m.id, observation={"tick": 1})

        assert summary["completed"] is True
        assert summary["checkpoint_saved"] is True

        # A fresh store (restart) sees the completed mission with its checkpoint.
        store_b = MissionStore(str(tmp_path))
        recovered = store_b.load(m.id)
        assert recovered.status == MissionStatus.COMPLETED
        assert recovered.checkpoint is not None
        assert recovered.checkpoint["stage"] == "completed"

    def test_runner_unknown_mission_reports_error(self, tmp_path):
        store = MissionStore(str(tmp_path))
        runner = MissionRunner(store, _echo_execute, _always_ok)
        summary = runner.run("ghost", observation={})
        assert summary["error"] == "not_found"
        assert summary["completed"] is False


# ---------------------------------------------------------------------------
# §60 Scheduler — capability assignment
# ---------------------------------------------------------------------------
class TestScheduler:
    def test_assigns_by_capability(self):
        scheduler = Scheduler()
        m = _new_mission(capability="search")
        agent = _CapAgent("agent-1", "search")
        assignments = scheduler.schedule([m], [agent])
        assert assignments == {m.id: "agent-1"}

    def test_no_assignment_when_no_matching_agent(self):
        scheduler = Scheduler()
        m = _new_mission(capability="search")
        agent = _CapAgent("agent-1", "write")
        assignments = scheduler.schedule([m], [agent])
        assert assignments == {}

    def test_multiple_missions_matched_to_capable_agents(self):
        scheduler = Scheduler()
        search_m = _new_mission("find", capability="search")
        write_m = _new_mission("save", capability="write")
        search_agent = _CapAgent("a-search", "search")
        write_agent = _CapAgent("a-write", "write")
        assignments = scheduler.schedule(
            [search_m, write_m], [search_agent, write_agent]
        )
        assert assignments == {search_m.id: "a-search", write_m.id: "a-write"}

    def test_each_agent_used_at_most_once_first_fit(self):
        scheduler = Scheduler()
        m1 = _new_mission("s1", capability="search")
        m2 = _new_mission("s2", capability="search")
        agent = _CapAgent("only-search", "search")
        assignments = scheduler.schedule([m1, m2], [agent])
        # Only one agent -> only one mission assigned.
        assert len(assignments) == 1
        assert list(assignments.values()) == ["only-search"]

    def test_mission_without_required_capability_unassigned(self):
        scheduler = Scheduler()
        m = _new_mission(capability=None)
        agent = _CapAgent("a", "search")
        assert scheduler.schedule([m], [agent]) == {}


# ---------------------------------------------------------------------------
# MissionRunner loop — verify behaviour
# ---------------------------------------------------------------------------
class TestMissionRunnerLoop:
    def test_success_path_sets_completed(self, tmp_path):
        store = MissionStore(str(tmp_path))
        m = _new_mission()
        store.save(m)
        runner = MissionRunner(store, _echo_execute, _always_ok)
        summary = runner.run(m.id, observation={"x": 1})

        assert summary["verify_passed"] is True
        assert summary["completed"] is True
        assert summary["needs_replan"] is False
        assert summary["attempts"] == 0  # unchanged on success
        assert store.load(m.id).status == MissionStatus.COMPLETED

    def test_verify_false_increments_attempts_and_needs_replan(self, tmp_path):
        store = MissionStore(str(tmp_path))
        m = _new_mission()
        store.save(m)
        runner = MissionRunner(store, _echo_execute, _always_fail)
        summary = runner.run(m.id, observation={"x": 1})

        assert summary["verify_passed"] is False
        assert summary["completed"] is False
        assert summary["needs_replan"] is True
        assert summary["attempts"] == 1
        loaded = store.load(m.id)
        assert loaded.status == MissionStatus.NEEDS_REPLAN
        assert loaded.attempts == 1
        assert loaded.status != MissionStatus.COMPLETED

    def test_repeated_failure_eventually_fails(self, tmp_path):
        store = MissionStore(str(tmp_path))
        m = _new_mission()
        store.save(m)
        runner = MissionRunner(store, _echo_execute, _always_fail, max_attempts=2)
        first = runner.run(m.id, observation={})
        assert first["status"] == MissionStatus.NEEDS_REPLAN.value
        second = runner.run(m.id, observation={})
        assert second["attempts"] == 2
        assert second["status"] == MissionStatus.FAILED.value
        assert second["completed"] is False

    def test_replan_checkpoint_records_failure_context(self, tmp_path):
        store = MissionStore(str(tmp_path))
        m = _new_mission("decompose me", capability="search")
        store.save(m)
        runner = MissionRunner(store, _echo_execute, _always_fail)
        runner.run(m.id, observation={"q": "hello"})
        loaded = store.load(m.id)
        cp = loaded.checkpoint
        assert cp["stage"] == "replan"
        assert cp["verify_passed"] is False
        # honest heuristic replan reuses the real GoalDecomposer
        assert cp["replan"]["strategy"] in ("heuristic_decomposition", "NOT_IMPLEMENTED")
        assert cp["replan"]["attempts"] == 1
