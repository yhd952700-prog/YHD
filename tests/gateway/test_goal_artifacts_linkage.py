"""Backend linkage test: a goal that writes a file records it as an artifact.

Honesty principle: this test proves REAL side effects through the real
production code path (``AIStateManager.create_and_execute_goal`` -> AgentRuntime
-> ExecutionEngine -> file_write). No mocks, no faked artifacts.

What it proves (and how):
  1. A natural-language goal that routes to ``file_write`` (via the deterministic
     decomposer, ``src/kernels/execution/__init__.py``) really writes a file into
     the workspace (proven by ``tests/integration/test_execution_loop_e2e.py``).
  2. The goal entry returned by ``create_and_execute_goal`` carries
     ``artifacts`` containing the produced file as a WORKSPACE-RELATIVE path.
  3. The same artifact is surfaced by ``GET /v1/goals/{id}`` (the real endpoint
     handler in ``src/gateway/ai_management.py``).
  4. A goal that writes NO file records an EMPTY ``artifacts`` list — fail-closed,
     never inventing a path.

This mirrors ``tests/integration/test_execution_loop_e2e.py``: a synchronous
``create_and_execute_goal(..., background=False)`` is driven directly, avoiding
the HTTP TestClient lifespan wiring (which can hang teardown via a thread leak).
The ``GET`` handler is exercised by calling the real FastAPI endpoint function
directly.
"""

from __future__ import annotations

import os
import tempfile

import pytest

from src.gateway.ai_management import AIStateManager, get_goal as get_goal_endpoint


@pytest.fixture(autouse=True)
def _isolate_workspace_and_audit(monkeypatch):
    """Redirect the workspace and the audit DB to throwaway temp paths."""
    ws = tempfile.mkdtemp(prefix="lh_ws_art_")
    aud_dir = tempfile.mkdtemp(prefix="lh_aud_art_")
    aud_path = os.path.join(aud_dir, "audit_store.db")

    monkeypatch.setenv("LIUHAO_WORKSPACE_ROOT", ws)
    monkeypatch.setenv("AUDIT_DB_PATH", aud_path)
    monkeypatch.setenv("HTTP_PROXY", "")
    monkeypatch.setenv("HTTPS_PROXY", "")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")

    # Force the singletons to rebuild against the redirected env.
    import src.kernels.audit as _audit_mod
    import src.gateway.ai_management as _aim_mod
    _audit_mod._audit_store = None
    _aim_mod.AIStateManager._instance = None

    yield {"workspace": ws, "audit_db": aud_path}

    # No cleanup of temp dirs on purpose: a passing run leaves the proof on disk.


def _mgr() -> AIStateManager:
    return AIStateManager()


def test_goal_that_writes_file_records_artifact_and_surfaces_via_api():
    """A file-producing goal records its artifact in the entry AND the GET API."""
    entry = _mgr().create_and_execute_goal(
        "write a file named hello.txt containing 'Hello World'",
        background=False,
    )

    # 1) Terminal state, non-empty task list (sanity, same as the e2e test).
    assert entry["state"] in ("completed", "failed"), entry
    assert isinstance(entry["tasks"], list) and len(entry["tasks"]) > 0, entry

    # 2) The entry carries the produced file as a workspace-relative artifact.
    #    "hello.txt" is the relative path; the real file sits at <ws>/hello.txt.
    artifacts = entry.get("artifacts", [])
    assert isinstance(artifacts, list), entry
    assert "hello.txt" in artifacts, (
        f"expected 'hello.txt' in artifacts, got {artifacts}; entry={entry}"
    )
    # No absolute paths leak into the artifact list (must be workspace-relative).
    assert all(not os.path.isabs(a) for a in artifacts), artifacts
    # The real file actually exists on disk.
    target = os.path.join(os.environ["LIUHAO_WORKSPACE_ROOT"], "hello.txt")
    assert os.path.exists(target), f"expected real file at {target}"

    # 3) GET /v1/goals/{id} surfaces the same artifact list.
    served = get_goal_endpoint(entry["goal_id"])
    served_artifacts = served.get("artifacts", [])
    assert "hello.txt" in served_artifacts, (
        f"GET /v1/goals/{entry['goal_id']} did not return the artifact; "
        f"got {served_artifacts}"
    )


def test_goal_that_writes_no_file_has_empty_artifacts():
    """A goal that produces no workspace file records an empty artifact list."""
    entry = _mgr().create_and_execute_goal("compute 2+2", background=False)

    assert isinstance(entry.get("artifacts", None), list), entry
    assert entry["artifacts"] == [], (
        f"a file-less goal must have empty artifacts, got {entry.get('artifacts')}"
    )

    # The GET API agrees.
    served = get_goal_endpoint(entry["goal_id"])
    assert served.get("artifacts", []) == [], served
