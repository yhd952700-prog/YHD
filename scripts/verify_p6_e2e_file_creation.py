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
import tempfile
import uuid

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

# 未设置 LIUHAO_WORKSPACE_ROOT 时自建临时工作区：原先直接 os.environ[...] 会在
# 变量缺失时抛 KeyError，令这个门禁**根本无法独立运行**（因而也无法被 CI 执行）。
# 与同族 verify_e2e_real_execution.py 自备考古临时工作区的行为对齐；
# 环境变量仍优先，便于需要时指向真实工作区取证。
WS = os.environ.get("LIUHAO_WORKSPACE_ROOT") or tempfile.mkdtemp(
    prefix="liuhao_p6_ws_"
)
os.makedirs(WS, exist_ok=True)

# 关键（根因）：只算出 WS 是不够的，必须**导出**给进程环境。
# file_write 的路径判定走 ``src/ai/workspace.py::workspace_root()``：
# 环境变量优先，为空时回落到 Windows 默认根 ``D:\LiuHao-Workspace``。
# 本脚本原先只算不导出，于是文件被真实写进**生产工作区**，而断言却盯着
# 临时目录 —— 门禁必然假失败，且顺带污染真实工作区。这里不改变任何产品
# 行为，只是让校验台把它断言的那个根声明出来。导出必须先于任何可能捕获
# 工作区根的 src 导入。
os.environ["LIUHAO_WORKSPACE_ROOT"] = WS

TARGET = os.path.join(WS, "report.txt")
EXPECTED = "hello world"

# Clean slate
if os.path.exists(TARGET):
    os.remove(TARGET)

from src.ai.lcore import LCore  # noqa: E402
from src.ai.agent_runtime import AgentRuntime, AgentRunState  # noqa: E402


# 刻意**不**绑定 acting principal。
#
# 曾一度以为必须 bind_acting_principal("liuhao-internal-service") 才能让
# capability.register 不被默认拒绝。A/B 探针证伪了这个判断：绑定与不绑定，
# 文件都真实落盘（state=completed / exists=True / content 正确）。原因是
# capability.register 的 deny 判决在 record-only 姿态下本就不阻断执行
# （见 src/kernels/_crosscutting.py 的 AUDIT TRUTHFULNESS 分支：body 照跑，
# 但审计诚实地记 outcome="denied" + policy_enforced=False）。
#
# 而且绑定会让审计 actor 记成 source="bound"，而生产启动路径并不绑定任何主体
# （生产是 service-default）—— 绑了反而让证据**偏离**生产。故保持不绑定。
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
