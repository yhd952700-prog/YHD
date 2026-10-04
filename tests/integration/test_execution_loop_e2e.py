"""End-to-end proof: a human goal really executes through the LIUHAO AI OS.

Honesty principle: this test proves REAL side effects. No mocks, no fakes.

What it proves (and how):
  1. A natural-language goal is decomposed into REAL tasks
     (src/kernels/execution/__init__.py: GoalDecomposer.decompose).
  2. A file-write task really writes a file into the workspace through the
     fail-closed ``resolve_in_workspace`` (src/ai/workspace.py:88), i.e. the
     REAL file-write tool at src/ai/tools_local.py:158 (def file_write).
  3. The goal reaches a terminal state and a REAL audit event is recorded in
     the hash-chained audit store (src/kernels/audit/__init__.py: AuditStore;
     query_events at line 1898, log_event at line 2215). The store path is
     redirected via the AUDIT_DB_PATH env override (line 387).
  4. The local compute capability (python_compute, src/ai/tools_local.py:85)
     computes for real without any LLM provider on the critical path.

How the loop is driven:
  We call ``AIStateManager().create_and_execute_goal(..., background=False)``
  DIRECTLY (src/gateway/ai_management.py:302). This runs the goal
  SYNCHRONOUSLY and returns the result dict (with ``state``/``tasks``/``error``).
  We avoid the HTTP TestClient on purpose: its lifespan/event-bus wiring can
  hang the test process at teardown (thread leak -> pytest exit 124). The
  synchronous entry point is the real production code path and is what we prove.

IMPORTANT HONEST FINDING (do not "fix" by mocking):
  The deterministic keyword decomposer (src/kernels/execution/__init__.py:
  313-326) recognises compute keywords ("compute", "计算", "求和" ...) and
  creates a ``python_compute`` task. BUT ``python_compute`` only accepts
  EXECUTABLE code (a ``code``/``expression`` input, or a goal that starts with a
  ``python:`` / ``code:`` directive — see _CODE_DIRECTIVE at
  src/ai/tools_local.py:46). The decomposer passes only the raw goal text, so a
  bare "compute 2+2" is REJECTED by the capability ("needs executable code").
  End-to-end natural-language compute therefore does NOT auto-run today — it
  requires a planner/LLM to turn the sentence into code. That is a REAL gap,
  reported honestly below; we still prove the capability itself computes ("4")
  with no LLM by invoking it directly with code.
"""

from __future__ import annotations

import os
import tempfile

import pytest

from src.gateway.ai_management import AIStateManager
from src.kernels.audit import get_audit_store


@pytest.fixture(autouse=True)
def _isolate_workspace_and_audit(monkeypatch):
    """Redirect the workspace and the audit DB to throwaway temp paths.

    Both are REDIR-able via env vars (LIUHAO_WORKSPACE_ROOT for the workspace,
    AUDIT_DB_PATH for the audit store — see src/kernels/audit/__init__.py:387).
    We also reset the two module-level singletons so a fresh run reads the new
    env (these are lazy globals created on first use).
    """
    ws = tempfile.mkdtemp(prefix="lh_ws_e2e_")
    aud_dir = tempfile.mkdtemp(prefix="lh_aud_e2e_")
    aud_path = os.path.join(aud_dir, "audit_store.db")

    monkeypatch.setenv("LIUHAO_WORKSPACE_ROOT", ws)
    monkeypatch.setenv("AUDIT_DB_PATH", aud_path)
    # Be explicit about a no-proxy environment so no network call is attempted.
    monkeypatch.setenv("HTTP_PROXY", "")
    monkeypatch.setenv("HTTPS_PROXY", "")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")

    # Force the singletons to rebuild against the redirected env.
    import src.kernels.audit as _audit_mod
    import src.gateway.ai_management as _aim_mod
    _audit_mod._audit_store = None
    _aim_mod.AIStateManager._instance = None

    yield {"workspace": ws, "audit_db": aud_path}

    # No cleanup of temp dirs on purpose: a passing run leaves the proof on
    # disk; an operator can inspect hello.txt and audit_store.db afterward.


def _mgr() -> AIStateManager:
    # AIStateManager is a singleton; the fixture already reset it once for the
    # session so it binds to the redirected workspace/audit env.
    return AIStateManager()


def test_goal_writes_real_file_into_workspace(_isolate_workspace_and_audit):
    """A human goal really writes a file on disk through resolve_in_workspace."""
    env = _isolate_workspace_and_audit
    ws = env["workspace"]

    # Quoted phrase so the decomposer regex (_FILE_WRITE_A at
    # src/kernels/execution/__init__.py:234) captures the FULL content. The
    # regex only captures a quoted phrase or a single bare token, so we quote
    # "Hello World" to keep it intact. This is still a plain natural-language
    # goal — not a code-shaped input.
    entry = _mgr().create_and_execute_goal(
        "write a file named hello.txt containing 'Hello World'",
        background=False,
    )

    # 3a. Terminal state + non-empty task list.
    assert entry["state"] in ("completed", "failed"), entry
    assert isinstance(entry["tasks"], list) and len(entry["tasks"]) > 0, entry

    # 3b. At least one task is the real file-write capability and completed.
    fw = [t for t in entry["tasks"]
          if t.get("capability_id") == "file_write" and t.get("status") == "completed"]
    assert fw, [ (t.get("capability_id"), t.get("status")) for t in entry["tasks"] ]

    # 4. THE REAL PROOF: the file exists on disk inside the temp workspace
    #    and contains the intended content. Read it straight from disk.
    target = os.path.join(ws, "hello.txt")
    assert os.path.exists(target), f"expected real file at {target}"
    content = open(target, encoding="utf-8").read()
    assert "Hello World" in content, repr(content)

    # 5. A REAL audit event was recorded for this goal.
    store = get_audit_store()
    by_goal = store.query_events(correlation_id=entry["correlation_id"])
    by_all = store.query_events()
    assert len(by_goal) >= 1 or len(by_all) >= 1, "no audit event recorded"
    # Prefer a goal-correlated event (the execution loop stamps each action
    # with the goal's correlation_id via kernel_action_correlation_id).
    assert len(by_goal) >= 1, (
        f"no audit event tied to goal correlation_id={entry['correlation_id']}; "
        f"total events={len(by_all)}"
    )


def test_goal_routes_to_python_compute_and_capability_really_computes():
    """Prove the compute capability is REAL (no LLM), and report the NL gap.

    Honest outcomes asserted here:
      * The loop ROUTES "compute 2+2" to a python_compute task (decomposition is
        real, not a stub).
      * The bare natural-language compute goal does NOT silently report success:
        python_compute requires executable code the keyword decomposer does not
        generate, so the task is rejected. We assert that real failure.
      * The python_compute capability ITSELF computes 2+2 == 4 with no LLM, by
        invoking it directly with code (the honest "it really works" proof).
      * A real audit event is recorded for the goal.
    """
    entry = _mgr().create_and_execute_goal("compute 2+2", background=False)

    assert isinstance(entry["tasks"], list) and len(entry["tasks"]) > 0, entry

    compute_tasks = [t for t in entry["tasks"]
                      if t.get("capability_id") == "python_compute"]
    assert compute_tasks, "decomposer did not route 'compute 2+2' to python_compute"

    # Honest: the bare NL goal does not auto-compute; the capability rejects it
    # for lack of generated code (proves no fake success).
    assert entry["state"] == "failed", entry
    assert "code" in (compute_tasks[0].get("error") or "").lower(), (
        compute_tasks[0].get("error")
    )

    # Real capability proof, no LLM: python_compute with explicit code.
    from src.ai.tools_local import python_compute
    res = python_compute(code="result = 2+2")
    assert res.get("success") is True, res
    payload = f"{res.get('result')} {res.get('stdout')}"
    assert "4" in payload, res

    # Audit: a real event was recorded for this goal.
    store = get_audit_store()
    by_goal = store.query_events(correlation_id=entry["correlation_id"])
    by_all = store.query_events()
    assert len(by_goal) >= 1 or len(by_all) >= 1, "no audit event recorded"
