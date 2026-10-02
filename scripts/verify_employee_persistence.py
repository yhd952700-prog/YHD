#!/usr/bin/env python3
"""Employee persistence verification (LIUHAO AI-OS).

Proves (with NO network call) that the new durable ``EmployeeStore`` survives a
process restart for employees / agents / tasks:

  A. ``create_employee`` seeds a fresh Employee on an empty store, and SAVES it.
  B. Adding a task + completing it persists correctly.
  C. A brand-new ``EmployeeStore`` instance (simulating a restart) ``load``s the
     same employee back: agent count, task count and task status all recover.
  D. ``list_employees`` includes the name and ``remove`` returns True.

The employee store + workspace are REDIRECTED to a temp dir so nothing writes
to the real workspace and the frozen HC-01 live store is NEVER touched.

Exit code 0 = PASS, 1 = FAIL.
"""

import os
import sys
import tempfile

# --- isolate from the real workspace: REDIRECT employee store + workspace -----
_TMP = tempfile.mkdtemp(prefix="liuhao_employee_persist_")
os.environ["LIUHAO_WORKSPACE_ROOT"] = _TMP

from src.ai.employee_store import EmployeeStore
from src.ai.employee import TaskStatus


def _check(label, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {label}{(' -- ' + detail) if detail else ''}")
    return cond


def main():
    failures = 0

    print("=== A. create_employee seeds + saves on empty store ===")
    store = EmployeeStore()
    emp = store.create_employee("verify-emp", agent_count=2, enable_observability=False)
    failures += 0 if _check("employee created",
                             emp is not None and emp.name == "verify-emp") else 1
    failures += 0 if _check("agent count == 2",
                             len(emp.agents) == 2,
                             f"agents={len(emp.agents)}") else 1
    # It should have written the store file immediately on seed.
    failures += 0 if _check("store file written on seed",
                             os.path.exists(store.path), store.path) else 1

    print("\n=== B. add + complete a task, then save ===")
    task_id = emp.add_task("do something")
    # Mark completed manually (no network / no provider execution needed).
    emp.tasks[task_id].status = TaskStatus.COMPLETED
    emp.completed_tasks.append(task_id)
    emp.total_tasks_completed += 1
    written = store.save(emp)
    failures += 0 if _check("save() returned path",
                             written == store.path, written) else 1
    failures += 0 if _check("1 task present before restart",
                             len(emp.tasks) == 1) else 1

    print("\n=== C. restart recovery: new EmployeeStore loads it back ===")
    store2 = EmployeeStore()
    emp2 = store2.load("verify-emp")
    failures += 0 if _check("employee recovered after restart",
                             emp2 is not None) else 1
    if emp2 is not None:
        failures += 0 if _check("agent count recovered (==2)",
                                 len(emp2.agents) == 2,
                                 f"agents={len(emp2.agents)}") else 1
        failures += 0 if _check("task count recovered (==1)",
                                 len(emp2.tasks) == 1,
                                 f"tasks={len(emp2.tasks)}") else 1
        recovered_task = emp2.tasks.get(task_id)
        failures += 0 if _check("task id recovered",
                                 recovered_task is not None,
                                 f"task_id={task_id}") else 1
        if recovered_task is not None:
            failures += 0 if _check(
                "task status recovered (COMPLETED)",
                recovered_task.status == TaskStatus.COMPLETED,
                f"status={recovered_task.status}") else 1
        failures += 0 if _check(
            "aggregate counter recovered (total_completed==1)",
            emp2.total_tasks_completed == 1,
            f"completed={emp2.total_tasks_completed}") else 1
        failures += 0 if _check(
            "completed_tasks list recovered",
            task_id in emp2.completed_tasks,
            f"completed_tasks={emp2.completed_tasks}") else 1

    print("\n=== D. list_employees + remove ===")
    names = store2.list_employees()
    failures += 0 if _check("list_employees includes 'verify-emp'",
                             "verify-emp" in names,
                             f"names={names}") else 1
    removed = store2.remove("verify-emp")
    failures += 0 if _check("remove('verify-emp') returns True",
                             removed is True) else 1
    failures += 0 if _check("employee gone after remove",
                             store2.load("verify-emp") is None) else 1

    print()
    if failures:
        print(f"RESULT: FAIL ({failures} check(s) failed)")
        return 1
    print("RESULT: PASS -- employee/agent/task state persists across restart")
    print(f"  (employee store isolated at {store.path}; HC-01 frozen store untouched)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
