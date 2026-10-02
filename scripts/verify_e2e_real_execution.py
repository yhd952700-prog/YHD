"""HONEST end-to-end acceptance test for the real Planner->Execution->Verify->Audit chain.

What this proves (and what it does NOT):
  * REAL: a real ``file_write`` capability (actually writes to disk) and a real
    ``python_compute`` capability (actually runs arithmetic) are executed by an
    *injected* real capability executor. The action result + the on-disk artifact
    + the independent Verifier + the hash-chain audit record are all checked.
  * HONEST: when NO real executor is injected, the kernel does NOT claim a real
    execution. It either fails closed (``status="unwired"``, success=False) or,
    only when the operator explicitly opts in via LIUHAO_ALLOW_SIMULATED_EXECUTION=1,
    returns an honestly-labeled ``status="simulated"`` (``simulated=True``) with no
    real side effect on disk.

Deliberate, documented honesty note about the "planner":
  The system's "planner" is ``GoalDecomposer`` (src/kernels/execution/__init__.py:267).
  It is a deterministic KEYWORD + REGEX decomposer, NOT an LLM. To keep this test
  independent of any mock/real LLM we BYPASS the decomposer by supplying pre-built
  Task objects and overriding ``engine.decomposer.decompose``. We additionally
  ASSERT that the real decomposer turns a natural-language goal into a file_write
  task, documenting that the planner is real-but-deterministic (not an LLM).

Persistence is redirected to temp (LIUHAO_WORKSPACE_ROOT + AUDIT_DB_PATH) so the
production HC-01 audit evidence is never touched.
"""

import os
import sys
import tempfile
import uuid

# --- Redirect persistence to temp BEFORE importing anything that reads it. ---
_TMP_WS = tempfile.mkdtemp(prefix="liuhao_e2e_ws_")
_TMP_AUDIT_DIR = tempfile.mkdtemp(prefix="liuhao_e2e_audit_")
_TMP_AUDIT_DB = os.path.join(_TMP_AUDIT_DIR, "audit_store.db")
os.environ["LIUHAO_WORKSPACE_ROOT"] = _TMP_WS
os.environ["AUDIT_DB_PATH"] = _TMP_AUDIT_DB
# Ensure the simulated path is OFF by default so the fail-closed contract is tested.
os.environ.pop("LIUHAO_ALLOW_SIMULATED_EXECUTION", None)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from src.kernels.execution import (  # noqa: E402
    Action,
    ActionResult,
    ExecutionEngine,
    Goal,
    GoalDecomposer,
    Task,
    VerifyResult,
)
from src.kernels.audit import get_audit_store  # noqa: E402
from src.ai.workspace import resolve_in_workspace  # noqa: E402


WORKSPACE = os.environ["LIUHAO_WORKSPACE_ROOT"]
AUDIT_DB = os.environ["AUDIT_DB_PATH"]


def real_executor_factory(workspace_root):
    """A REAL capability executor. Touches the filesystem / runs Python for real.

    Returns a dict that always carries an explicit ``success`` key so the kernel's
    honesty contract (explicit verdict honored) is exercised.
    """
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
            # Real arithmetic executed by Python (no eval of foreign code).
            n = int(inputs.get("n", 10))
            result = sum(range(1, n + 1))
            return {"success": True, "result": result, "status": "executed"}
        raise ValueError(f"no REAL executor registered for capability {capability_id!r}")
    return executor


results = []


def check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    results.append((status, label, detail))
    print(f"[{status}] {label}" + (f" -- {detail}" if detail else ""))


def make_goal(scope="L1"):
    return Goal(
        id="G_" + uuid.uuid4().hex[:8],
        natural_language="write a file named deliverable.txt containing HELLO; compute sum 1..10",
        scope=scope,
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


def run_real_chain():
    """Drive the REAL execution/verify/audit chain with an injected real executor."""
    print("\n=== Scenario A: REAL execution (injected real capability executor) ===")
    engine = ExecutionEngine(scope="L1")
    engine.set_capability_executor(real_executor_factory(WORKSPACE))

    goal = make_goal()
    fw_task = make_file_write_task(goal)
    pc_task = make_python_compute_task(goal)

    # Deliberately bypass the (deterministic, non-LLM) decomposer with pre-built
    # tasks -- documented in the module docstring.
    engine.decomposer.decompose = lambda g: [fw_task, pc_task]

    ctx = engine.execute_goal(goal, plan_mode="sequential")

    # --- file_write: success + real artifact on disk + Verifier agrees ---
    fw_result = ctx.task_results.get(fw_task.id)
    check("file_write ActionResult.success is True",
          fw_result is not None and fw_result.success,
          detail=f"success={getattr(fw_result, 'success', None)}")
    written_path = None
    if fw_result is not None and isinstance(fw_result.output, dict):
        written_path = fw_result.output.get("written")
    file_exists = written_path is not None and os.path.exists(written_path)
    content_ok = file_exists and _read_text(written_path) == "HELLO"
    check("file_write actually created the artifact on disk (independent check)",
          file_exists and content_ok,
          detail=f"path={written_path}, content_ok={content_ok}")
    fw_verify = ctx.verification_results.get(fw_task.id)
    check("Verifier agrees the REAL file_write SUCCEEDED",
          fw_verify is not None and fw_verify.result is VerifyResult.SUCCESS,
          detail=f"result={getattr(fw_verify, 'result', None)}")

    # --- python_compute: success + real computation + Verifier agrees ---
    pc_result = ctx.task_results.get(pc_task.id)
    pc_real = pc_result is not None and pc_result.success and isinstance(pc_result.output, dict) \
        and pc_result.output.get("result") == 55
    check("python_compute ActionResult.success and real result==55",
          pc_real, detail=f"output={getattr(pc_result, 'output', None)}")
    pc_verify = ctx.verification_results.get(pc_task.id)
    check("Verifier agrees the REAL python_compute SUCCEEDED",
          pc_verify is not None and pc_verify.result is VerifyResult.SUCCESS,
          detail=f"result={getattr(pc_verify, 'result', None)}")

    # --- audit chain captured the REAL action execution ---
    audit = get_audit_store()
    # NOTE (honest caveat): @kernel_action stamps a FRESH random correlation_id
    # for each kernel action (src/kernels/_crosscutting.py:745), so the audit
    # chain does NOT carry the goal's correlation_id. The goal->action linkage
    # lives on the event bus (execution_started/goal_decomposed/...), not the
    # hash-chain audit. We therefore query ALL events and look for the kernel
    # action actually executed.
    all_events = audit.query_events()
    exec_events = [e for e in all_events
                   if (e.get("details") or {}).get("action") == "execution.execute"]
    exec_success = [e for e in exec_events if e.get("outcome") == "success"]
    event_types = sorted({e["event_type"] for e in all_events})
    actions = sorted({(e.get("details") or {}).get("action") for e in all_events})
    check("audit chain recorded execution.execute for the REAL actions (>=2)",
          len(exec_events) >= 2,
          detail=f"execution.execute events={len(exec_events)}, "
                 f"success={len(exec_success)}")
    check("audit outcome for the REAL executed actions is 'success' (not simulated/failure)",
          len(exec_success) >= 2,
          detail=f"success_count={len(exec_success)}")
    # Honest caveat (reported, not a hard fail): no audit event carries the goal's
    # correlation_id, so the audit chain alone cannot prove 'this goal executed'.
    linked_to_goal = any(e["correlation_id"] == goal.correlation_id for e in all_events)
    print(f"    audit event types found: {event_types}")
    print(f"    audit action details recorded: {actions}")
    print(f"    HONEST CAVEAT: goal correlation_id present in audit chain? "
          f"{linked_to_goal} (the @kernel_action uses a per-action random id; "
          f"goal->action linkage is on the event bus, not the hash-chain audit)")

    return ctx, goal


def run_honesty_no_executor():
    """Prove the kernel does NOT mislabel a non-executing capability as real."""
    print("\n=== Scenario B: NO real executor injected -- honesty contract ===")

    # (B1) Fail-closed default: LIUHAO_ALLOW_SIMULATED_EXECUTION unset.
    os.environ.pop("LIUHAO_ALLOW_SIMULATED_EXECUTION", None)
    engine = ExecutionEngine(scope="L1")  # no capability_executor -> None
    action = Action(
        id="a_unwired", task_id="t_unwired", capability_id="file_write",
        capability_namespace="kernel", inputs={"path": "nope.txt", "content": "x"},
        correlation_id="corr_unwired", scope="L1", timeout_seconds=10,
    )
    res = engine.executor.execute(action)
    status = res.output.get("status") if isinstance(res.output, dict) else None
    check("no executor (default): success=False and status='unwired' (NOT 'executed')",
          res.success is False and status == "unwired",
          detail=f"success={res.success}, status={status}")
    check("no executor (default): not mislabeled as real execution",
          status != "executed",
          detail=f"status={status}")

    # (B2) Opt-in simulated path: LIUHAO_ALLOW_SIMULATED_EXECUTION=1.
    os.environ["LIUHAO_ALLOW_SIMULATED_EXECUTION"] = "1"
    sim_path = os.path.join(WORKSPACE, "simulated_only.txt")
    if os.path.exists(sim_path):
        os.remove(sim_path)
    action2 = Action(
        id="a_sim", task_id="t_sim", capability_id="file_write",
        capability_namespace="kernel", inputs={"path": "simulated_only.txt", "content": "x"},
        correlation_id="corr_sim", scope="L1", timeout_seconds=10,
    )
    res2 = engine.executor.execute(action2)
    status2 = res2.output.get("status") if isinstance(res2.output, dict) else None
    simulated_flag = res2.output.get("simulated") if isinstance(res2.output, dict) else None
    check("opt-in simulated: status='simulated' and simulated=True (honestly labeled)",
          status2 == "simulated" and simulated_flag is True,
          detail=f"status={status2}, simulated={simulated_flag}, success={res2.success}")
    check("opt-in simulated: NOT labeled as real ('executed')",
          status2 != "executed", detail=f"status={status2}")
    check("opt-in simulated: NO real artifact written to disk",
          not os.path.exists(sim_path), detail=f"exists={os.path.exists(sim_path)}")
    os.environ.pop("LIUHAO_ALLOW_SIMULATED_EXECUTION", None)


def run_decomposer_finding():
    """Document that the planner is deterministic keyword/regex, NOT an LLM."""
    print("\n=== Scenario C: GROUND TRUTH on task generation (planner) ===")
    text = "Please write a file named report.txt containing SUMMARY"
    goal = Goal(id="G_dec", natural_language=text, scope="L1")
    tasks = GoalDecomposer().decompose(goal)
    fw = [t for t in tasks if t.capability_id == "file_write"]
    check("GoalDecomposer turns NL 'write a file named X containing Y' into a file_write task",
          len(fw) == 1 and fw[0].inputs.get("path") == "report.txt"
          and fw[0].inputs.get("content") == "SUMMARY",
          detail=f"inputs={fw[0].inputs if fw else None}")
    print("    FINDING: task generation is keyword/regex-based (src/kernels/execution/"
          "__init__.py:267 GoalDecomposer.decompose), NOT LLM-driven. Comment at "
          "line 269: 'in production would use LLM'.")


def _read_text(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def main():
    # Make sure the audit store is constructed against our temp path.
    import src.kernels.audit as _audit_mod  # noqa: E402
    _audit_mod._audit_store = None  # force re-init against AUDIT_DB_PATH

    run_decomposer_finding()
    run_real_chain()
    run_honesty_no_executor()

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
