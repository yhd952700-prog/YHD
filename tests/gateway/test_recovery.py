"""Product-level recovery proof for ``replan_goal``.

Sets LIUHAO_WORKSPACE_ROOT + AUDIT_DB_PATH to temp so no production data
(and never the frozen HC-01 audit_store.db) is touched.

Proves: ``AIStateManager.replan_goal`` re-runs the FAILED goal through the real
runtime/engine (journal-wired), resumes completed tasks by name, does NOT
duplicate the file-write side effect, and honestly reports the outcome.
"""
from __future__ import annotations

import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="liuhao_gw_recovery_")
os.environ["LIUHAO_WORKSPACE_ROOT"] = _TMP
os.environ["AUDIT_DB_PATH"] = os.path.join(_TMP, "audit_store.db")

from src.gateway.ai_management import AIStateManager  # noqa: E402
from src.ai.agent_runtime import AgentRuntime  # noqa: E402
from src.ai.lcore import LCore  # noqa: E402
from src.kernels.execution import ExecutionJournal  # noqa: E402


def _counted_lcore():
    # Bind the file-write tool's workspace root to _TMP EXPLICITLY (workspace_root()
    # is cached process-wide). This keeps the test hermetic.
    lcore = LCore(scope="L1")
    from src.ai.tools_local import register_default_local_tools

    register_default_local_tools(lcore.tools, root=_TMP)
    calls = {"file_write": 0}
    real = lcore.capability_executor()

    def counted(cap_id: str, inputs: dict):
        if cap_id == "file_write":
            calls["file_write"] += 1
        return real(cap_id, inputs)

    return counted, calls


def _file(n: str) -> str:
    return os.path.join(_TMP, n)


def test_replan_goal_resumes_completed_tasks():
    journal_path = os.path.join(_TMP, "gw_journal.db")

    # Runtime #1: initial (pre-crash) execution of the goal.
    counted1, calls1 = _counted_lcore()
    journal1 = ExecutionJournal(journal_path)
    runtime1 = AgentRuntime(scope="L1", capability_executor=counted1, journal=journal1)

    mgr = AIStateManager()
    mgr._runtime = runtime1
    mgr._goals = {}

    GID = "gw-replan-gid"
    GOAL_TEXT = "write a file named gw1.txt containing hello"
    runtime1.run_goal(GOAL_TEXT, goal_id=GID, persist=False)
    assert calls1["file_write"] == 1, "initial run must write the file"
    assert os.path.exists(_file("gw1.txt"))
    journal1.close()

    # Simulate the goal having FAILED and being persisted as failed.
    mgr._goals[GID] = {
        "goal_id": GID,
        "natural_language": GOAL_TEXT,
        "scope": "L1",
        "state": "failed",
        "correlation_id": "corr-" + GID,
        "error": "simulated prior failure",
        "replan_count": 0,
        "created_at": 0.0,
    }

    # Restart: brand new runtime + journal instance on the SAME db file.
    counted2, calls2 = _counted_lcore()
    journal2 = ExecutionJournal(journal_path)
    runtime2 = AgentRuntime(scope="L1", capability_executor=counted2, journal=journal2)
    mgr._runtime = runtime2

    entry = mgr.replan_goal(GID)

    # The completed task is resumed by the journal: the file-write side effect
    # is NOT repeated, and the file content is intact.
    assert calls2["file_write"] == 0, "replan must not re-run the completed file write"
    assert os.path.exists(_file("gw1.txt"))
    with open(_file("gw1.txt")) as f:
        assert f.read() == "hello"

    # Honest reporting: replan was attempted and counted.
    assert entry["replan_count"] == 1
    assert entry["goal_id"] == GID
    journal2.close()
