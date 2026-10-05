"""OPEN-DOMAIN CONTRACT — the planner must not emit capabilities with no executor.

Why this gate exists
--------------------
Measured with ``verify_agent_causality_coverage.py``: of six realistic goal
shapes only two complete. The other four fail, all for the *same* root cause --
the deterministic ``GoalDecomposer`` matches keywords and emits a
``capability_id`` without ever checking whether anything can execute it::

    search  -> network_bus         (no executor)
    memory  -> multi_tier_memory   (no executor)
    plan    -> execution_pipeline  (no executor)
    multi_shape -> search(file) + file_write  (the search half has no executor)

Worse than "fails": ``multi_shape`` *writes the file first and only then fails*,
because the doomed Search task runs alongside the executable Write task. So a
goal that was never completable produces a real side effect on disk.

``src/ai/planner_contract.py`` fixes this at the source: when an
``executable_capabilities`` provider is wired into ``AgentRuntime`` (the gateway
does this via ``LCore.executable_capabilities()``), the wrapped decomposer
validates, *before any task runs*, that every planned capability has a real
executor. If one is missing it raises ``PlannerContractError`` naming it -- the
goal fails fast with an actionable reason and **no partial side effects**.

This script proves the contract's observable effect end-to-end through the real
``AgentRuntime.run_goal`` path:

    * ``file_write`` and ``python_compute`` STILL complete (real artifacts / work).
    * ``search`` / ``memory`` / ``plan`` / ``multi_shape`` are REFUSED at plan
      time with a message naming the missing capability, and leave the workspace
      EMPTY (the contract refuses the WHOLE goal -- it does not run the
      executable half and fail the rest).

That last point is the key honesty guarantee: the workspace for ``multi_shape``
must contain NO ``notes.txt``, proving the file-write half never ran.

Run:
    CODEBUDDY_SAFE_DELETE_ENABLED=0 .venv/Scripts/python.exe \\
        scripts/verify_open_domain_contract.py

Discipline:
    * All state goes to a fresh temp dir outside the repo -- the repo-root
      ``audit_store.db`` (-wal/-shm) is never touched.
    * The contract is OPT-IN: if ``executable_capabilities`` is not supplied,
      behaviour is byte-for-byte unchanged. This gate wires it explicitly.
"""
from __future__ import annotations

import hashlib
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = tempfile.mkdtemp(prefix="verify_open_domain_contract_")
os.environ["AUDIT_DB_PATH"] = os.path.join(ROOT, "audit.db")
os.environ["LIUHAO_MEMORY_DB_PATH"] = os.path.join(ROOT, "memory.db")

from src.ai.agent_runtime import AgentRuntime  # noqa: E402
from src.ai.lcore import LCore  # noqa: E402

# Same six shapes the causality-coverage probe uses, with the capability the
# deterministic decomposer is EXPECTED to emit and whether it should now
# COMPLETE or be REFUSED.
GOAL_SHAPES = [
    # (label, goal text, expected_capability, expect)
    ("file_write", 'create a file named report.txt containing "hello"',
     "file_write", "complete"),
    ("compute", "calculate the sum of 2 and 3 using python",
     "python_compute", "complete"),
    ("search", "search for information about quantum computing",
     "network_bus", "refuse"),
    ("memory", "remember this note for later",
     "multi_tier_memory", "refuse"),
    ("plan", "plan the quarterly schedule",
     "execution_pipeline", "refuse"),
    ("multi_shape",
     'search for information and create a file named notes.txt containing "x"',
     "network_bus", "refuse"),
]

CONTRACT_MESSAGE = "no executor is registered for capability"


def _disk(ws: str) -> dict:
    out = {}
    for r, _d, files in os.walk(ws):
        for fn in files:
            p = os.path.join(r, fn)
            try:
                with open(p, "rb") as fh:
                    out[os.path.relpath(p, ws)] = hashlib.sha256(fh.read()).hexdigest()[:12]
            except OSError:
                out[os.path.relpath(p, ws)] = "<unreadable>"
    return out


def _run(label: str, text: str) -> dict:
    ws = os.path.join(ROOT, label)
    os.makedirs(ws, exist_ok=True)
    # The file-write tool binds its root at registration time, so the workspace
    # env MUST be set before LCore is constructed.
    os.environ["LIUHAO_WORKSPACE_ROOT"] = ws
    lcore = LCore(scope="L1", register_local_tools=True)
    rt = AgentRuntime(
        scope="L1",
        capability_executor=lcore.capability_executor(),
        executable_capabilities=lcore.executable_capabilities(),
    )
    r = rt.run_goal(text, goal_id=f"g_{label}")
    return {
        "state": r.state.value,
        "error": r.error,
        "artifacts": _disk(ws),
    }


def main() -> int:
    print("=" * 78)
    print("OPEN-DOMAIN CONTRACT: planner refuses capabilities with no executor")
    print("=" * 78)
    print(f"tmp = {ROOT}\n")

    failures = []
    for label, text, cap, expect in GOAL_SHAPES:
        res = _run(label, text)
        state, error, artifacts = res["state"], res["error"], res["artifacts"]
        print(f"-- {label:<12} expect={expect:<8} state={state} "
              f"artifacts={sorted(artifacts)}")

        if expect == "complete":
            if state != "completed":
                failures.append(f"{label}: expected COMPLETE, got state={state} "
                                f"error={error!r}")
                continue
            # file_write must have produced a real artifact containing the text.
            if cap == "file_write":
                if "report.txt" not in artifacts:
                    failures.append(f"{label}: completed but report.txt not on disk")
                    continue
                try:
                    with open(os.path.join(ROOT, label, "report.txt"), "rb") as fh:
                        body = fh.read()
                except OSError:
                    failures.append(f"{label}: report.txt unreadable")
                    continue
                if b"hello" not in body:
                    failures.append(f"{label}: report.txt exists but lacks 'hello'")
                    continue
            print(f"     OK: completed with real work (cap {cap})")

        else:  # expect == "refuse"
            if state != "failed":
                failures.append(f"{label}: expected REFUSE (plan-stage fail), "
                                f"got state={state}")
                continue
            if error is None or CONTRACT_MESSAGE not in error or cap not in error:
                failures.append(f"{label}: refused but message did not name "
                                f"missing capability {cap}: error={error!r}")
                continue
            if artifacts:
                failures.append(f"{label}: REFUSED yet workspace is NOT empty "
                                f"(partial side effect!): {sorted(artifacts)}")
                continue
            print(f"     OK: refused at plan stage, names '{cap}', workspace empty")

    print()
    print("=" * 78)
    print("VERDICT")
    print("=" * 78)
    if failures:
        for f in failures:
            print(f"  FAIL: {f}")
        print(f"\n  {len(failures)} assertion(s) failed.")
        return 1

    completed = sum(1 for _l, _t, _c, e in GOAL_SHAPES if e == "complete")
    refused = sum(1 for _l, _t, _c, e in GOAL_SHAPES if e == "refuse")
    print(f"  completed (real work)         : {completed}/6  (file_write, compute)")
    print(f"  honestly refused (plan stage) : {refused}/6  (search, memory, plan, multi_shape)")
    print("  partial side effects          : 0  (refused goals leave workspace empty)")
    print("\n  CONTRACT HOLDS: 2/6 complete + 4/6 honest refusal, no fake success.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
