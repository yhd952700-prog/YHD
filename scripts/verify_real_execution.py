"""Reproducible verification for real execution + honest failure (WS1).

Run:  .venv/Scripts/python.exe scripts/verify_real_execution.py
Exit code 0 = all assertions green.

What this proves:
  1. A real capability genuinely EXECUTES: a natural-language goal routed to
     ``python_compute`` returns the *actual* computed value (not "simulated").
  2. The sandbox REJECTS unsafe code (import/open/eval) -- and that rejection is
     NOT masked as success by the Execution Kernel.
  3. An unwired capability fails LOUDLY ("no active tool for capability ..."),
     never as a silent simulated success.
  4. The kernel honours an injected executor's explicit ``success: False``
     (defense in depth against the "silent false success" pattern).
"""

from __future__ import annotations

import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.ai.lcore import LCore  # noqa: E402
from src.kernels.execution import Action, ActionExecutor, ExecutionEngine, Goal  # noqa: E402

RESULTS = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((ok, name, detail))
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name}" + (f" -- {detail}" if detail else ""))


def _action(code: str) -> Action:
    return Action(
        id="probe",
        task_id="t1",
        capability_id="python_compute",
        capability_namespace="kernel",
        inputs={"code": code},
        correlation_id="verify-real-execution",
        scope="L1",
        timeout_seconds=15,
    )


def main() -> int:
    # (1) Real execution, end-to-end through the goal pipeline.
    lcore = LCore(register_local_tools=True)
    engine = ExecutionEngine(capability_executor=lcore.capability_executor())
    ctx = engine.execute_goal(
        Goal(
            id="g-real",
            natural_language="python: result = sum(i * i for i in range(1, 11))",
            scope="L1",
        )
    )
    outputs = [r.output for r in ctx.task_results.values() if isinstance(r.output, dict)]
    real = any(o.get("status") == "executed" and o.get("result") == "385" for o in outputs)
    check("real compute executes end-to-end (result=385, status=executed)", real, str(outputs)[:120])

    # (2) Dangerous code is rejected at the sandbox, and the rejection is honest.
    rejected = ActionExecutor(capability_executor=lcore.capability_executor()).execute(
        _action("import os")
    )
    check(
        "unsafe code rejected (not masked as success)",
        rejected.success is False and "ImportError" in (rejected.error or ""),
        f"success={rejected.success} error={rejected.error}",
    )

    # (3) No tool wired -> loud failure, never a simulated success.
    notool = ActionExecutor(capability_executor=LCore().capability_executor()).execute(
        _action("result = 1 + 1")
    )
    check(
        "unwired capability fails loudly",
        notool.success is False and "no active tool for capability" in (notool.error or ""),
        f"success={notool.success} error={notool.error}",
    )

    # (4) Kernel honours an injected executor's explicit success=False.
    def failing_executor(capability_id, inputs):
        return {"success": False, "status": "rejected", "error": "sandbox refused"}

    masked = ActionExecutor(capability_executor=failing_executor).execute(_action("result = 1"))
    check(
        "explicit success=False is not masked by the kernel",
        masked.success is False and "sandbox refused" in (masked.error or ""),
        f"success={masked.success} error={masked.error}",
    )

    failed = [r for r in RESULTS if not r[0]]
    print()
    if failed:
        print(f"RESULT: {len(failed)} FAILED / {len(RESULTS)} total")
        for ok, name, detail in failed:
            print(f"  - {name} :: {detail}")
        return 1
    print(f"RESULT: ALL GREEN ({len(RESULTS)} checks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
