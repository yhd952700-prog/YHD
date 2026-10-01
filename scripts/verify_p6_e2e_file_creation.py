"""P6 (+P4) end-to-end honesty check — production wiring path.

Wires the SAME real capability executor the gateway now injects
(`LCore(register_local_tools=True).capability_executor()`) into an
`AgentRuntime`, then runs a realistic "create file" goal and asserts the REAL
artifact exists on disk with the expected content -- not merely that the run
reported `completed`. It also proves P4's permission check is ENFORCED by
attempting a workspace-escaping write and asserting it is refused.

This is the measured evidence for P6 -> PASS and P4 -> PASS. It deliberately
avoids the HTTP layer (no server needed) but exercises the exact execution
pipeline the gateway uses: GoalDecomposer -> PlanBuilder -> ActionExecutor ->
ToolRouter -> local_file_write tool -> real disk write under a WorldInterface
default-DENY authorization + workspace containment.

Run from the repo root:
    python -B scripts/verify_p6_e2e_file_creation.py
"""
import os
import sys
import uuid

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

WS = os.environ["LIUHAO_WORKSPACE_ROOT"]
TARGET = os.path.join(WS, "report.txt")
EXPECTED = "hello world"

# Clean slate
if os.path.exists(TARGET):
    os.remove(TARGET)

from src.ai.lcore import LCore  # noqa: E402
from src.ai.agent_runtime import AgentRuntime, AgentRunState  # noqa: E402


def main() -> int:
    lcore = LCore(register_local_tools=True)
    rt = AgentRuntime(
        scope="L1",
        capability_executor=lcore.capability_executor(),
    )
    goal = (
        'create a file named report.txt containing "'
        + EXPECTED
        + '"'
    )
    result = rt.run_goal(goal, goal_id="p6-e2e-" + uuid.uuid4().hex[:6])

    print(f"state           = {result.state}")
    print(f"error           = {result.error}")
    print(f"file exists     = {os.path.exists(TARGET)}")
    content = None
    if os.path.exists(TARGET):
        with open(TARGET, "r", encoding="utf-8") as fh:
            content = fh.read()
    print(f"file content    = {content!r}")
    print(f"expected        = {EXPECTED!r}")

    checks = {
        "REAL_STATE_COMPLETED": result.state == AgentRunState.COMPLETED,
        "REAL_FILE_EXISTS": os.path.exists(TARGET),
        "REAL_CONTENT_MATCH": content == EXPECTED,
        "NO_FAKE_ERROR": result.error is None,
    }

    # Negative check: P4 enforcement proof. A path that tries to escape the
    # workspace must be refused (fail-closed), proving the permission check is
    # ENFORCED, not merely present.
    escape = lcore.capability_executor()(
        "file_write",
        {"path": "../../escape.txt", "content": "should not land"},
    )
    escape_refused = bool(escape) and escape.get("success") is False
    checks["P4_CONTAINMENT_REFUSED"] = escape_refused
    print(f"\nescape attempt success = {escape.get('success') if escape else None}")
    print(f"escape error           = {escape.get('error') if escape else None}")

    print()
    for k, v in checks.items():
        print(f"  {k}: {v}")
    ok = all(checks.values())
    print(f"\nRESULT: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
