"""Verify the audit goal->action correlation traceability fix (p36-audit-corr).

Proves that the hash-chain audit events for kernel actions emitted during a goal
execution now carry the GOAL's correlation id (NOT a fresh random one), closing
the traceability gap where the audit chain could prove "an action executed" but
not "this goal executed it".

The fix:
  * ``src/kernels/_crosscutting.py`` adds a module-level
    ``KERNEL_ACTION_CORRELATION_ID`` ContextVar and a
    ``kernel_action_correlation_id`` context manager.
  * The ``kernel_action`` wrapper now uses
    ``corr_id = KERNEL_ACTION_CORRELATION_ID.get() or uuid.uuid4().hex``.
  * ``ExecutionEngine.execute_goal`` wraps its whole body in
    ``kernel_action_correlation_id(goal.correlation_id)``.

What this script proves:
  1. Run a goal (with an injected REAL file_write + python_compute executor,
     reusing the pattern from verify_e2e_real_execution.py) through
     ``ExecutionEngine.execute_goal`` with a KNOWN correlation id.
  2. Read the hash-chain audit store and assert the ``execution.execute`` audit
     events carry ``correlation_id == <goal's correlation id>`` -- the CORE proof
     that an action's audit event now ties back to the goal.
  3. Assert the action actually executed (real artifact on disk + real result).
  4. BACKWARD COMPAT: when the context var is NOT set (a direct kernel action
     outside any goal), the correlation id is still a non-empty string (and is a
     fresh id, independent of the goal's).

Persistence is redirected to temp (LIUHAO_WORKSPACE_ROOT + AUDIT_DB_PATH) so the
production HC-01 audit evidence (audit_store.db real path) is never touched.

Exits non-zero on any failure (prints PASS/FAIL per check).
"""

import os
import sys
import tempfile
import uuid

# --- Redirect persistence to temp BEFORE importing anything that reads it. ---
_TMP_WS = tempfile.mkdtemp(prefix="liuhao_audit_corr_ws_")
_TMP_AUDIT_DIR = tempfile.mkdtemp(prefix="liuhao_audit_corr_audit_")
_TMP_AUDIT_DB = os.path.join(_TMP_AUDIT_DIR, "audit_store.db")
os.environ["LIUHAO_WORKSPACE_ROOT"] = _TMP_WS
os.environ["AUDIT_DB_PATH"] = _TMP_AUDIT_DB
# Ensure the simulated path is OFF so the fail-closed contract is respected.
os.environ.pop("LIUHAO_ALLOW_SIMULATED_EXECUTION", None)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from src.kernels.execution import (  # noqa: E402
    ExecutionEngine,
    Goal,
    Task,
    VerifyResult,
)
from src.kernels.audit import get_audit_store  # noqa: E402
from src.kernels._crosscutting import (  # noqa: E402
    kernel_action,
    KERNEL_ACTION_CORRELATION_ID,
)
from src.ai.workspace import resolve_in_workspace  # noqa: E402


WORKSPACE = os.environ["LIUHAO_WORKSPACE_ROOT"]
AUDIT_DB = os.environ["AUDIT_DB_PATH"]


def real_executor_factory(workspace_root):
    """A REAL capability executor (touches the filesystem / runs Python for real)."""
    def executor(capability_id, inputs):
        if capability_id == "file_write":
            path = inputs.get("path")
            content = inputs.get("content", "")
            abs_path = resolve_in_workspace(path, workspace_root)
            os.makedirs(os.path.dirname(abs_path) or ".", exist_ok=True)
            with open(abs_path, "w", encoding="utf-8") as f:
                f.write(content)
            return {"success": True, "written": abs_path, "status": "executed"}
        if capability_id == "python_compute":
            n = int(inputs.get("n", 10))
            return {"success": True, "result": sum(range(1, n + 1)), "status": "executed"}
        raise ValueError(f"no REAL executor registered for capability {capability_id!r}")
    return executor


results = []


def check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    results.append((status, label, detail))
    print(f"[{status}] {label}" + (f" -- {detail}" if detail else ""))


def make_goal(correlation_id, scope="L1"):
    return Goal(
        id="G_" + uuid.uuid4().hex[:8],
        natural_language="write a file named deliverable.txt containing HELLO; compute sum 1..10",
        scope=scope,
        correlation_id=correlation_id,
    )


def make_file_write_task(goal):
    return Task(
        id="T_fw_" + uuid.uuid4().hex[:8],
        goal_id=goal.id,
        name="WriteFile",
        description="Write a file inside the workspace (REAL)",
        capability_id="file_write",
        capability_namespace="kernel",
        inputs={"path": "deliverable.txt", "content": "HELLO"},
        expected_outputs={"content": "HELLO"},
        scope=goal.scope,
    )


def make_python_compute_task(goal):
    return Task(
        id="T_pc_" + uuid.uuid4().hex[:8],
        goal_id=goal.id,
        name="Compute",
        description="Run a local computation (REAL)",
        capability_id="python_compute",
        capability_namespace="kernel",
        inputs={"n": 10},
        expected_outputs={"result": 55},  # sum(1..10) == 55
        scope=goal.scope,
    )


def read_text(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def run_goal_chain(known_corr):
    """Drive a REAL goal execution and return (ctx, goal)."""
    engine = ExecutionEngine(scope="L1")
    engine.set_capability_executor(real_executor_factory(WORKSPACE))

    goal = make_goal(known_corr)
    fw_task = make_file_write_task(goal)
    pc_task = make_python_compute_task(goal)

    # Bypass the (deterministic, non-LLM) decomposer with pre-built tasks.
    engine.decomposer.decompose = lambda g: [fw_task, pc_task]

    ctx = engine.execute_goal(goal, plan_mode="sequential")
    return ctx, goal, fw_task, pc_task


def main():
    # Force the audit store to be constructed against our temp path.
    import src.kernels.audit as _audit_mod  # noqa: E402
    _audit_mod._audit_store = None

    known_corr = "GOAL-CORR-" + uuid.uuid4().hex[:16]
    print(f"Known goal correlation_id: {known_corr}")

    ctx, goal, fw_task, pc_task = run_goal_chain(known_corr)

    # --- Prove the actions actually ran (real side effects) ---
    fw_result = ctx.task_results.get(fw_task.id)
    check("file_write ActionResult.success is True",
          fw_result is not None and fw_result.success,
          detail=f"success={getattr(fw_result, 'success', None)}")
    written = fw_result.output.get("written") if isinstance(fw_result.output, dict) else None
    check("file_write created the artifact on disk (independent check)",
          written is not None and read_text(written) == "HELLO",
          detail=f"path={written}")
    pc_result = ctx.task_results.get(pc_task.id)
    pc_real = (pc_result is not None and pc_result.success
               and isinstance(pc_result.output, dict)
               and pc_result.output.get("result") == 55)
    check("python_compute ActionResult.success and real result==55",
          pc_real, detail=f"output={getattr(pc_result, 'output', None)}")

    # --- CORE PROOF: audit event carries the GOAL's correlation id ---
    audit = get_audit_store()
    all_events = audit.query_events()
    exec_events = [e for e in all_events
                   if (e.get("details") or {}).get("action") == "execution.execute"]
    corr_matches_goal = [e for e in exec_events
                         if e.get("correlation_id") == known_corr]
    check("hash-chain audit recorded >=2 execution.execute events",
          len(exec_events) >= 2,
          detail=f"execution.execute events={len(exec_events)}")
    check(f"execution.execute audit events carry the GOAL's correlation_id ({known_corr})",
          len(corr_matches_goal) >= 2,
          detail=f"matched={len(corr_matches_goal)}/{len(exec_events)}")
    # Hard negative: no execution.execute event should carry a stray/random id.
    stray = [e for e in exec_events if e.get("correlation_id") != known_corr]
    check("no execution.execute audit event carries a stray/random correlation_id",
          len(stray) == 0, detail=f"stray={len(stray)}")
    # Sanity: the goal's correlation id is genuinely present in the audit chain.
    goal_present_anywhere = any(e.get("correlation_id") == known_corr for e in all_events)
    check("the goal's correlation_id is present in the hash-chain audit",
          goal_present_anywhere)

    # --- BACKWARD COMPAT: direct action (context var NOT set) still non-empty ---
    # After execute_goal returns, its `with` block has reset the context var.
    # Explicitly clear it to be certain, then emit a direct kernel action.
    KERNEL_ACTION_CORRELATION_ID.set(None)

    @kernel_action("audit_corr_probe", audit=True, policy=False, observable=False)
    def _probe():
        return "ok"

    _probe()
    audit_after = get_audit_store()
    probe_events = [e for e in audit_after.query_events()
                    if (e.get("details") or {}).get("action") == "audit_corr_probe"]
    probe_corr = probe_events[0].get("correlation_id") if probe_events else None
    check("direct kernel action (no goal) STILL writes a non-empty correlation_id",
          len(probe_events) == 1
          and isinstance(probe_corr, str) and len(probe_corr) > 0,
          detail=f"corr={probe_corr}")
    # And it must be a fresh id, independent of the goal's correlation id.
    check("direct kernel action correlation_id is independent of the goal's",
          not probe_events or probe_corr != known_corr,
          detail=f"probe_corr={probe_corr}, goal_corr={known_corr}")

    failed = [r for r in results if r[0] == "FAIL"]
    print()
    print(f"=== SUMMARY: {len(results) - len(failed)}/{len(results)} passed ===")
    print(f"    workspace (temp): {WORKSPACE}")
    print(f"    audit db (temp):  {AUDIT_DB}")
    if failed:
        for status, label, detail in failed:
            print(f"  FAILED: {label} {detail}")
        return 1
    print("ALL PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
