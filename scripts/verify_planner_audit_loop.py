#!/usr/bin/env python3
"""Planner audit-loop + durable-state verification (LIUHAO AI-OS).

Proves (with NO network call) that:

  A. The planner's real task-execution loop now runs through the audited
     ``@kernel_action`` path (action ``ai.execute_planner_task``). Each executed
     task produces an honest audit event with ``outcome == "success"`` and
     ``policy_decision == "allow"`` (the action is classified ALLOWED for the
     internal service principal -- see ``src/kernels/policy/__init__.py``).

  B. Defense-in-depth regression: across ALL audit events written during the run,
     no event records a ``policy_decision == "deny"`` as ``outcome == "success"``
     -- the exact defect the audit-truthfulness fix closed in
     ``src/kernels/_crosscutting.py``.

  C. Durable persistence round-trips: ``save_state`` -> ``load_state`` recovers
     the goal id, task ids, statuses and dependency edges (restart recovery).

Audit DB + workspace are REDIRECTED to a temp dir so the frozen HC-01 live store
is NEVER touched.

Exit code 0 = PASS, 1 = FAIL.
"""

import os
import sys
import tempfile

# --- isolate from the frozen HC-01 live store: REDIRECT audit + workspace -----
_TMP = tempfile.mkdtemp(prefix="liuhao_planner_audit_")
AUDIT_DB = os.path.join(_TMP, "audit_store.db")
os.environ["AUDIT_DB_PATH"] = AUDIT_DB
os.environ["LIUHAO_WORKSPACE_ROOT"] = _TMP
os.environ["LIUHAO_AUDIT_SYNCHRONOUS"] = "FULL"

from src.ai.goal_task_graph import (
    GoalTaskGraph,
    GoalDefinition,
    TaskNode,
    GoalStatus,
    TaskStatus,
)
from src.kernels.audit import audit_query


def _check(label, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {label}{(' -- ' + detail) if detail else ''}")
    return cond


def _find_action_events(action_name):
    out = []
    for ev in audit_query(limit=2000, reverse=True):
        det = ev.get("details") or {}
        if det.get("action") == action_name:
            out.append((ev, det))
    return out


def main():
    failures = 0

    print("=== A. route planner task execution through @kernel_action ===")
    # enable_observability=False -> no memory manager, no network.
    graph = GoalTaskGraph(enable_observability=False)
    goal = GoalDefinition(id="goal_p1", description="demo planning goal")
    graph.set_goal(goal)

    # Inject tasks directly (no AI call) -- a small acyclic chain of 4 tasks.
    tasks = [
        TaskNode(id="t1", description="Analyse requirements", task_type="analysis"),
        TaskNode(id="t2", description="Design module", task_type="design", depends_on=["t1"]),
        TaskNode(id="t3", description="Implement module", task_type="implementation", depends_on=["t2"]),
        TaskNode(id="t4", description="Verify module", task_type="testing", depends_on=["t3"]),
    ]
    graph._store_tasks(goal.id, tasks)

    def agent_executor(agent_id, description):
        return f"ok: {description}"

    # execute_graph goes through execute_task -> _execute_task_audited (the
    # @kernel_action-wrapped path). max_parallel=1 keeps audit writes
    # serialized while still exercising the real threaded executor loop.
    result = graph.execute_graph(goal.id, agent_executor, max_parallel=1)

    failures += 0 if _check("execute_graph returned 'completed'",
                             result.get("status") == "completed",
                             f"status={result.get('status')!r}") else 1
    failures += 0 if _check("all 4 tasks completed",
                             len(graph.completed_tasks) == 4,
                             f"completed={len(graph.completed_tasks)}") else 1

    events = _find_action_events("ai.execute_planner_task")
    failures += 0 if _check("audit events for ai.execute_planner_task exist",
                             len(events) >= 4,
                             f"count={len(events)}") else 1
    for ev, det in events:
        oc = ev.get("outcome")
        pd = det.get("policy_decision")
        if oc != "success" or pd != "allow":
            failures += 1
            _check("planner event is truthful allow",
                   False, f"outcome={oc!r} decision={pd!r}")
    if events:
        failures += 0 if _check(
            "every planner event: outcome='success' AND policy_decision='allow'",
            all(e.get("outcome") == "success" and d.get("policy_decision") == "allow"
                for e, d in events)) else 1

    print("\n=== B. regression: no deny recorded as success anywhere ===")
    bad = []
    for ev in audit_query(limit=2000, reverse=True):
        det = ev.get("details") or {}
        if det.get("policy_decision") == "deny" and ev.get("outcome") == "success":
            bad.append(det.get("action"))
    failures += 0 if _check("no event has (decision=deny AND outcome=success)",
                             not bad, f"bad={bad}") else 1

    print("\n=== C. persistence round-trip (save -> load) ===")
    state_path = os.path.join(_TMP, "planner_state.json")
    written = graph.save_state(state_path)
    failures += 0 if _check("save_state wrote file",
                             os.path.exists(written), written) else 1

    graph2 = GoalTaskGraph.load_state(state_path, enable_observability=False)
    failures += 0 if _check("goal id recovered",
                             graph2.goal is not None and graph2.goal.id == goal.id,
                             f"goal_id={getattr(graph2.goal, 'id', None)!r}") else 1
    failures += 0 if _check("task ids recovered",
                             set(graph2.tasks.keys()) == set(graph.tasks.keys()),
                             f"before={set(graph.tasks.keys())} after={set(graph2.tasks.keys())}") else 1
    statuses_match = all(
        graph2.tasks[tid].status == graph.tasks[tid].status for tid in graph.tasks
    )
    failures += 0 if _check("task statuses recovered", statuses_match) else 1
    edges_ok = all(
        set(graph2.tasks[tid].depends_on) == set(graph.tasks[tid].depends_on)
        for tid in graph.tasks
    )
    failures += 0 if _check("dependency edges recovered", edges_ok) else 1

    print()
    if failures:
        print(f"RESULT: FAIL ({failures} check(s) failed)")
        return 1
    print("RESULT: PASS -- planner loop is audited honestly + state persists")
    print(f"  (audit DB isolated at {AUDIT_DB}; HC-01 frozen store untouched)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
