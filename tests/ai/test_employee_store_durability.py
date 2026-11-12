"""
Cross-restart durability test for the EmployeeStore (P1 residual).

The P1 readiness verdict notes that EmployeeStore durability is "by-design via
atomic JSON writes, but not separately exercised across a process restart in CI".
This test closes that gap: it writes an employee (with a completed task and a
paused agent) through one EmployeeStore instance, then opens a SECOND instance
pointing at the SAME file -- simulating a process restart -- and asserts the
employee, its agents, task status and aggregate counters all survived.

No browser, no LLM key, no gateway. Pure persistence round-trip against a tmp
path so the frozen HC-01 evidence is never touched.
"""

import os

from src.ai.employee import AgentStatus, Employee, TaskStatus
from src.ai.employee_store import EmployeeStore


def _make_store(tmp_path):
    path = os.path.join(str(tmp_path), "liuhao_employees.json")
    return EmployeeStore(path=path)


def _build_employee_with_history(store: EmployeeStore) -> Employee:
    """Create an employee, complete one task, and pause one agent; then persist."""
    emp = store.create_employee("durab-alice", agent_count=2, enable_observability=False)
    # Add + complete a real task so task-status + completion counters persist.
    task_id = emp.add_task("write the quarterly report", task_type="file_write", priority=1)
    task = emp.tasks[task_id]
    task.status = TaskStatus.COMPLETED
    task.assigned_agent = emp.agents["durab-alice-a0"].id
    task.completed_at = task.created_at + 1.0
    emp.completed_tasks.append(task_id)
    emp.total_tasks_completed += 1
    # Pause an agent to prove lifecycle state survives restart.
    emp.agents["durab-alice-a1"].pause()
    store.save(emp)
    return emp


def test_employee_persists_across_restart(tmp_path):
    # First "process": build + save.
    store1 = _make_store(tmp_path)
    original = _build_employee_with_history(store1)
    assert store1.list_employees() == ["durab-alice"]

    # Second "process" (restart): brand-new store, same file on disk.
    store2 = _make_store(tmp_path)
    assert store2.list_employees() == ["durab-alice"]

    reloaded = store2.load("durab-alice")
    assert reloaded is not None
    # Agents survive with the same globally-unique ids.
    assert list(reloaded.agents.keys()) == list(original.agents.keys())
    assert set(reloaded.agents.keys()) == {"durab-alice-a0", "durab-alice-a1"}
    # The paused agent is STILL paused after restart.
    assert reloaded.agents["durab-alice-a1"].status == AgentStatus.PAUSED
    assert reloaded.agents["durab-alice-a0"].status == AgentStatus.IDLE
    # Task survives with the right terminal status + assignment.
    assert "task_0" in reloaded.tasks
    assert reloaded.tasks["task_0"].status == TaskStatus.COMPLETED
    assert reloaded.tasks["task_0"].assigned_agent == "durab-alice-a0"
    # Aggregate counters survive.
    assert reloaded.completed_tasks == ["task_0"]
    assert reloaded.total_tasks_completed == 1
    assert reloaded.total_tasks_submitted == 1


def test_employee_remove_persists_across_restart(tmp_path):
    store1 = _make_store(tmp_path)
    store1.create_employee("durab-bob", agent_count=1, enable_observability=False)
    assert "durab-bob" in store1.list_employees()
    assert store1.remove("durab-bob") is True

    store2 = _make_store(tmp_path)
    assert "durab-bob" not in store2.list_employees()
    assert store2.load("durab-bob") is None
    # Removing a non-existent employee is honest (False, not an exception).
    assert store2.remove("durab-bob") is False


def test_employee_create_is_idempotent_across_restart(tmp_path):
    store1 = _make_store(tmp_path)
    store1.create_employee("durab-carol", agent_count=3, enable_observability=False)
    # Open a new store against the same file and "create" again -- must LOAD,
    # not overwrite (so the previously-persisted agent pool is preserved).
    store2 = _make_store(tmp_path)
    emp = store2.create_employee("durab-carol", agent_count=99, enable_observability=False)
    assert len(emp.agents) == 3  # original pool, not the new count=99
    assert list(emp.agents.keys()) == [
        "durab-carol-a0",
        "durab-carol-a1",
        "durab-carol-a2",
    ]
