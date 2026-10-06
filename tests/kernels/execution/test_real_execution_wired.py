"""PROVES the production goal path executes for real -- no more simulated success.

This is the regression suite for the single most damaging defect: the production
goal path reported ``state=completed`` / ``evaluation=SUCCESS`` while doing
NOTHING (the capability executor was never wired into the gateway's
``AgentRuntime``, so the kernel took its simulated path and called it success).

Three honest assertions, each tied to a root cause:

1. WIRED (gateway/production path): a goal that writes a file produces a REAL file
   on disk with the exact content. We assert the file EXISTS and its CONTENT
   matches -- never just ``state == "completed"``. This is what the defect
   failed to do.

2. UNWIRED: the same goal, with NO capability executor, must FAIL and must NOT be
   reported completed/SUCCESS. This assertion is written so it FAILS against the
   pre-fix code (which returned a simulated success). That is the P0 regression
   guard.

3. UNBACKED: a goal needing a capability that has no executor (``network_bus``)
   fails honestly with a named reason -- never a false success.

All data is redirected to temp dirs via ``LIUHAO_WORKSPACE_ROOT`` /
``AUDIT_DB_PATH``; the frozen HC-01 ``audit_store.db`` is never touched.

Note on the gateway path: ``src/gateway/__init__`` currently fails to import in
this environment because of an UNRELATED pre-existing policy-kernel WIP breakage
(``NameError: kernel_action`` in an uncommitted change to
``src/kernels/policy/__init__.py``). That is out of scope for this fix. To still
exercise the *real* production function the endpoint calls
(``AIStateManager.create_and_execute_goal``), we load ``ai_management`` as a
standalone module so we skip the broken package ``__init__``. The execution
machinery it drives (``AgentRuntime`` + ``LCore`` with ``register_local_tools``)
is exactly what the endpoint uses.
"""
from __future__ import annotations

import importlib.util
import os
import tempfile

# REDIR all data to temp BEFORE any module resolves workspace/audit paths.
_TMP = tempfile.mkdtemp(prefix="liuhao_wired_")
os.environ["LIUHAO_WORKSPACE_ROOT"] = _TMP
os.environ.setdefault("AUDIT_DB_PATH", os.path.join(_TMP, "audit_store.db"))
os.environ.setdefault("LIUHAO_AUDIT_DB_PATH", os.path.join(_TMP, "audit_store.db"))

from src.ai.lcore import LCore  # noqa: E402
from src.ai.tools_local import register_default_local_tools  # noqa: E402
from src.ai.agent_runtime import AgentRuntime, AgentRunState  # noqa: E402


def _load_gateway_manager():
    """Load ``AIStateManager`` standalone, skipping the broken gateway package.

    Returns a fresh manager (singleton reset) whose runtime writes into ``_TMP``.
    The workspace/audit env is (re)pinned here so the test is hermetic even when
    other test modules have overwritten ``LIUHAO_WORKSPACE_ROOT`` at import time.
    """
    os.environ["LIUHAO_WORKSPACE_ROOT"] = _TMP
    os.environ["AUDIT_DB_PATH"] = os.path.join(_TMP, "audit_store.db")
    os.environ["LIUHAO_AUDIT_DB_PATH"] = os.path.join(_TMP, "audit_store.db")

    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    path = os.path.join(here, "src", "gateway", "ai_management.py")
    spec = importlib.util.spec_from_file_location("ai_management_standalone", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Fresh singleton so the workspace root resolved at construction == _TMP.
    module.AIStateManager._instance = None
    return module.AIStateManager()


def _wired_runtime():
    """The production execution machinery the gateway wires: LCore local tools."""
    lcore = LCore(scope="L1")
    register_default_local_tools(lcore.tools, root=_TMP)
    return AgentRuntime(
        scope="L1",
        capability_executor=lcore.capability_executor(),
        executable_capabilities=lcore.executable_capabilities(),
    )


def _file(name: str) -> str:
    return os.path.join(_TMP, name)


# ---------------------------------------------------------------------------
# 1. WIRED (gateway / production path) -- the file must really exist on disk.
# ---------------------------------------------------------------------------
def test_gateway_production_path_writes_real_file():
    """POST /v1/goals -> create_and_execute_goal must produce a REAL artifact.

    The defect: the same call returned ``completed`` / ``SUCCESS`` while writing
    nothing. Here we assert the file EXISTS with the exact content -- the only
    acceptable proof that the goal executed.
    """
    manager = _load_gateway_manager()
    res = manager.create_and_execute_goal(
        "Create a file named gw_hello.txt containing 'hello gateway'",
        scope="L1",
    )

    # 1a. The state machine must genuinely complete (not error out).
    assert res["state"] == "completed", res.get("error")

    # 1b. THE PROOF: the file is on disk, with the exact requested content.
    #     Asserting only ``state == "completed"`` would let the defect pass.
    path = _file("gw_hello.txt")
    assert os.path.exists(path), (
        "DEFECT REOPENED: gateway reported success but wrote no file -- "
        f"{path} does not exist"
    )
    with open(path, encoding="utf-8") as fh:
        assert fh.read() == "hello gateway", "file content does not match the goal"

    # 1c. The integrity sidecar is also real (decomposer appends WriteChecksum).
    assert os.path.exists(_file("gw_hello.txt.sha256")), "integrity sidecar missing"

    # 1d. The response honestly records what was produced.
    assert "gw_hello.txt" in res.get("artifacts", []), "artifacts not recorded"


# ---------------------------------------------------------------------------
# 2. UNWIRED -- must FAIL, never report success. Regression guard for P0.
# ---------------------------------------------------------------------------
def test_unwired_goal_fails_and_does_not_report_success():
    """No capability executor => fail closed, NO file, NO completed/SUCCESS.

    Against the pre-fix code this FAILS: an unwired ``AgentRuntime(scope="L1")``
    returned ``state=completed`` with a simulated action. The fix makes the
    kernel refuse (``no capability executor wired``), so the goal is failed.
    """
    rt = AgentRuntime(scope="L1")  # deliberately unwired
    res = rt.run_goal(
        "Create a file named orphan.txt containing 'should not be written'",
        goal_id="unwired-1",
        persist=False,
    )

    # The file must NOT exist -- execution did nothing.
    assert not os.path.exists(_file("orphan.txt")), (
        "FALSE SUCCESS: a file was written though nothing was wired"
    )

    # The run must NOT be reported as success/completed.
    assert res.state.value != "completed", (
        "FALSE SUCCESS: an unwired kernel must not report completed"
    )
    assert res.state == AgentRunState.FAILED, f"expected FAILED, got {res.state}"

    # At least one task must be recorded as failed, with an explicit root cause.
    assert res.context is not None and res.context.failed_tasks, "no failed task recorded"
    reasons = " ".join(
        t.error or "" for t in res.context.plan.tasks if t.error
    )
    assert "no capability executor wired" in reasons, (
        f"root cause not surfaced: {reasons!r}"
    )


# ---------------------------------------------------------------------------
# 3. UNBACKED capability -- honest failure, never false success.
# ---------------------------------------------------------------------------
def test_unbacked_capability_fails_honestly():
    """A goal needing a capability with no executor fails with a named reason.

    ``search the web`` maps to ``network_bus``, which has no local tool. With the
    planner<->executor contract (``executable_capabilities``), the run fails fast
    at plan time and names the missing capability. It must never report success.
    """
    rt = _wired_runtime()
    res = rt.run_goal(
        "search the web for the latest AI news",
        goal_id="unbacked-1",
        persist=False,
    )
    assert res.state == AgentRunState.FAILED, (
        f"unbacked capability must not be reported as success; got {res.state}"
    )
    assert res.error is not None and "network_bus" in res.error, (
        f"missing capability not named: {res.error!r}"
    )


# ---------------------------------------------------------------------------
# 4. Bonus: a real compute goal returns the ACTUAL computed value (5050).
# ---------------------------------------------------------------------------
def test_real_compute_returns_actual_value():
    """The flagship local tool really computes -- not a simulation flag."""
    rt = _wired_runtime()
    res = rt.run_goal(
        "python: result = sum(range(1,101))",
        goal_id="compute-1",
        persist=False,
    )
    assert res.state == AgentRunState.COMPLETED
    assert res.context is not None
    computed = [
        t.result for t in res.context.plan.tasks
        if t.capability_id == "python_compute"
    ]
    assert computed, "no compute task executed"
    out = computed[0]
    assert isinstance(out, dict) and str(out.get("result")) == "5050", (
        f"compute did not return the real answer; got {out!r}"
    )


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
