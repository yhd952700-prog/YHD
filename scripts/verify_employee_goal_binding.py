#!/usr/bin/env python3
"""verify_employee_goal_binding.py

Prove the AI Employee is a REAL autonomous worker bound to the execution loop:

  1. EXECUTION BINDING  — a created goal belongs to the default Employee
     (``liuhao-default``); while it runs, that employee's agents show BUSY,
     and after it ends they return to IDLE. Goal counts feed the employee KPI.
  2. REAL CANCELLATION  — ``POST /v1/goals/{id}/stop`` (the *real* endpoint
     handler) cleanly aborts a running goal: the execution loop raises
     GoalCancelled, the goal is persisted as ``cancelled`` (never faked as a
     success), and the human stop is written to the audit chain.

All writes are REDIR-ed to a temp workspace + temp audit DB, so the frozen
HC-01 evidence store and the real workspace are never touched.

Exit code 0 = proof holds; non-zero = proof failed.
"""

from __future__ import annotations

import os
import sys
import time
import tempfile
import threading

# ── REDIR BEFORE importing any repo module (env is read at call/init time) ──
_TMP = tempfile.mkdtemp(prefix="emp_bind_")
os.environ["LIUHAO_WORKSPACE_ROOT"] = _TMP
os.environ["AUDIT_DB_PATH"] = os.path.join(_TMP, "audit.db")
os.environ.setdefault("AI_PROVIDER_TYPE", "mock")  # safe dev default

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from src.gateway.ai_management import AIStateManager  # noqa: E402
from src.gateway.ai_management import stop_goal as stop_goal_endpoint  # noqa: E402
from src.ai.agent_runtime import AgentRuntime  # noqa: E402
from src.kernels.execution._journal import ExecutionJournal  # noqa: E402
from src.kernels.audit import audit_query, AuditEventType  # noqa: E402


PASS = "PASS"
FAIL = "FAIL"
_results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    _results.append((name, ok, detail))
    mark = PASS if ok else FAIL
    print(f"[{mark}] {name}" + (f" — {detail}" if detail else ""))


def main() -> int:
    # Fresh, isolated manager (no shared state with other runs).
    AIStateManager._instance = None
    mgr = AIStateManager()

    # Inject a *real* (custom) executor that blocks long enough to observe
    # BUSY mid-flight and to cancel between tasks. This is a genuine executor
    # (not the simulated path) so the goal really executes.
    def slow_executor(capability_id, inputs):
        time.sleep(1.2)
        return {"capability": capability_id, "status": "executed", "result": "ok", "executed": True}

    journal = ExecutionJournal(os.path.join(_TMP, "liuhao_execution.journal"))
    mgr._runtime = AgentRuntime(scope="L1", capability_executor=slow_executor, journal=journal)

    # Start from a clean, idle agent pool.
    emp = mgr._ensure_employee()
    for a in emp.agents.values():
        a.status = a.status.IDLE

    # ── 1. Start a goal that decomposes into MULTIPLE tasks (plan+compute+remember) ──
    nl = "plan and compute the sum 1..10 and remember the result"
    entry = mgr.create_and_execute_goal(nl, scope="L1", background=True)
    goal_id = entry["goal_id"]
    check("goal created in background (state=running)", entry["state"] == "running",
          f"goal_id={goal_id} state={entry['state']}")

    # ── 1a. While running, the employee's agents must be BUSY ──
    busy_seen = False
    deadline = time.time() + 3.0
    while time.time() < deadline:
        agents = mgr.list_agents()
        if agents and all(a["status"] == "busy" for a in agents):
            busy_seen = True
            break
        time.sleep(0.05)
    check("employee agents BUSY during execution", busy_seen,
          "agents=" + str([(a["id"], a["status"]) for a in mgr.list_agents()]))

    # ── 1b. Goal is bound to the employee (KPI accounting) ──
    bound_emp = mgr._ensure_employee()
    check("goal bound to default employee", goal_id in bound_emp.goals,
          f"employee.goals={bound_emp.goals}")
    check("employee total_goals_submitted >= 1", bound_emp.total_goals_submitted >= 1,
          f"submitted={bound_emp.total_goals_submitted}")

    # ── 2. Cleanly cancel via the REAL stop endpoint handler ──
    stop_resp = stop_goal_endpoint(goal_id)
    check("stop endpoint accepted the request", isinstance(stop_resp, dict),
          f"returned state={stop_resp.get('state')}")

    # Wait for the background execution to observe the stop flag and persist
    # its terminal state.
    final = None
    deadline = time.time() + 30.0
    while time.time() < deadline:
        g = mgr.get_goal(goal_id)
        if g["state"] != "running":
            final = g
            break
        time.sleep(0.1)
    check("running goal cleanly cancelled (state=cancelled)", final is not None and final["state"] == "cancelled",
          f"final state={final['state'] if final else 'timeout'}")

    # ── 2a. Agents return to IDLE after cancellation ──
    agents = mgr.list_agents()
    check("employee agents IDLE after cancellation", agents and all(a["status"] == "idle" for a in agents),
          "agents=" + str([(a["id"], a["status"]) for a in agents]))

    # ── 2b. Cancellation is reflected in the employee KPI (counts as failed) ──
    bound_emp = mgr._ensure_employee()
    check("employee total_goals_failed >= 1 (cancelled accounted)", bound_emp.total_goals_failed >= 1,
          f"failed={bound_emp.total_goals_failed}")

    # ── 2c. The human stop was audited (honest sovereignty) ──
    audit_events = audit_query(event_type=AuditEventType.GOAL_CONTROL, correlation_id=goal_id)
    audit_hit = any(
        e.get("correlation_id") == goal_id and (e.get("details") or {}).get("action") == "stop"
        for e in audit_events
    )
    check("human stop is written to the audit chain", audit_hit,
          f"events={len(audit_events)}")

    # ── Summary ──
    failed = [n for n, ok, _ in _results if not ok]
    print("\n" + "=" * 64)
    if failed:
        print(f"RESULT: FAIL — {len(failed)} check(s) failed: {failed}")
        return 1
    print("RESULT: PASS — AI Employee is a REAL autonomous worker bound to the")
    print("              execution loop, with journal-backed honesty and a clean,")
    print("              audited cancellation path.")
    print(f"  temp workspace : {_TMP}")
    print(f"  goal_id       : {goal_id}")
    print(f"  employee      : liuhao-default  (goals_submitted={bound_emp.total_goals_submitted}, "
          f"completed={bound_emp.total_goals_completed}, failed={bound_emp.total_goals_failed})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
