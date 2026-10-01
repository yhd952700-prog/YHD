"""P6 honesty verification — real execution vs fail-closed + policy/resource truth.

Reproduces the cycle-2 evidence behind PRODUCT-READINESS.md P6 / P5. It never
reads the frozen HC-01 evidence store: AUDIT_DB_PATH is redirected to a temp
file, and LIUHAO_WORKSPACE_ROOT to temp workspaces.

Checks:
  1. REAL_EXECUTION_OK      — real executor -> goal actually writes the file.
  2. FAIL_CLOSED_OK         — no executor -> fail-closed (status=unwired, no file).
  3. POLICY_SENTINEL_PROTECTED — default_deny / human_sovereignty unregister refused.
  4. RESOURCE_ALLOCATE_DENIED_RECORATED — resource.allocate denial -> outcome=denied.
  5. DECORATOR_MECHANISM_OK — kernel_action + mark_action_denied three-state.

Run from the repo root:
    python -B scripts/verify_p6_real_execution.py
"""
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

WS1 = tempfile.mkdtemp(prefix="liuhao_ws1_")
WS2 = tempfile.mkdtemp(prefix="liuhao_ws2_")  # separate workspace for no-executor run
AUDIT_DIR = tempfile.mkdtemp(prefix="liuhao_audit_")
AUDIT = os.path.join(AUDIT_DIR, "audit_store.db")
os.environ["LIUHAO_WORKSPACE_ROOT"] = WS1
os.environ["AUDIT_DB_PATH"] = AUDIT
os.environ.setdefault("LIUHAO_AUDIT_SYNCHRONOUS", "FULL")

import src.kernels._crosscutting as xc  # noqa: E402

# ---- audit capture helper (bypass real db; safe) ----
_captured = []


def _capture(actor, action, outcome, decision, corr, dur, rule, risk, **kw):
    _captured.append((action, outcome))


def with_capture(fn):
    global _captured
    _captured = []
    orig = xc._call_audit
    xc._call_audit = _capture
    try:
        return fn()
    finally:
        xc._call_audit = orig


GOAL = 'create a file named report.txt containing "Hello World from LIUHAO real execution"'

# ===== 1 + 2: real execution vs fail-closed =====
from src.ai.lcore import LCore  # noqa: E402
from src.ai.agent_runtime import AgentRuntime  # noqa: E402

# Run 1: real executor
os.environ["LIUHAO_WORKSPACE_ROOT"] = WS1
lcore = LCore(scope="L1", register_local_tools=True)
rt = AgentRuntime(scope="L1", capability_executor=lcore.capability_executor())
r1 = rt.run_goal(goal_text=GOAL, goal_id="real1", scope="L1", persist=False)
c1 = r1.context
real_file = os.path.join(WS1, "report.txt")
real_ok = (
    os.path.exists(real_file)
    and open(real_file).read() == "Hello World from LIUHAO real execution"
    and any((r.output or {}).get("status") == "executed" for r in (c1.task_results or {}).values())
    and (r1.state.value if hasattr(r1.state, "value") else r1.state) == "completed"
)

# Run 2: NO executor, separate workspace -> must NOT write a file
os.environ["LIUHAO_WORKSPACE_ROOT"] = WS2
rt2 = AgentRuntime(scope="L1")  # no executor
r2 = rt2.run_goal(goal_text=GOAL, goal_id="noexec1", scope="L1", persist=False)
c2 = r2.context
noexec_file = os.path.join(WS2, "report.txt")
fail_closed_ok = (
    not os.path.exists(noexec_file)
    and any((r.output or {}).get("status") == "unwired" for r in (c2.task_results or {}).values())
    and (r2.state.value if hasattr(r2.state, "value") else r2.state) == "failed"
)

print("1) REAL_EXECUTION_OK:", real_ok)
print("2) FAIL_CLOSED_OK:", fail_closed_ok)

# ===== 3: policy sentinel protection =====
from src.kernels.policy import PolicyEngine  # noqa: E402

pe = PolicyEngine()
g1 = pe.unregister_rule("default_deny")
g2 = pe.unregister_rule("human_sovereignty")
still_present = pe.get_rule("default_deny") is not None and pe.get_rule("human_sovereignty") is not None
policy_ok = (g1 is False) and (g2 is False) and still_present
print("3) POLICY_SENTINEL_PROTECTED:", policy_ok)

# ===== 4: resource.allocate denial recorded as denied =====
from src.kernels.resource import get_resource_manager  # noqa: E402

rm = get_resource_manager()
res = with_capture(lambda: rm.allocate(resource_type=None, amount=-5, scope="L1", owner="tester"))
alloc_denied_ok = (res is None) and any(
    a == "resource.allocate" and o == "denied" for (a, o) in _captured
)
print("4) RESOURCE_ALLOCATE_DENIED_RECORDED:", alloc_denied_ok)


# ===== 5: decorator mechanism (denied / success / failure) =====
class _Demo:
    @xc.kernel_action("test.demo_denied")
    def do_deny(self):
        xc.mark_action_denied("rejected")
        return None

    @xc.kernel_action("test.demo_ok")
    def do_ok(self):
        return 1

    @xc.kernel_action("test.demo_fail")
    def do_fail(self):
        raise ValueError("boom")


with_capture(lambda: _Demo().do_deny())
d_out = [o for (a, o) in _captured]
with_capture(lambda: _Demo().do_ok())
s_out = [o for (a, o) in _captured]
try:
    with_capture(lambda: _Demo().do_fail())
except ValueError:
    pass
f_out = [o for (a, o) in _captured]
mech_ok = ("denied" in d_out) and ("success" in s_out) and ("failure" in f_out)
print("5) DECORATOR_MECHANISM_OK:", mech_ok, dict(denied=d_out, success=s_out, failure=f_out))

overall = all([real_ok, fail_closed_ok, policy_ok, alloc_denied_ok, mech_ok])
print("\nRESULT:", "PASS" if overall else "FAIL")
sys.exit(0 if overall else 1)
