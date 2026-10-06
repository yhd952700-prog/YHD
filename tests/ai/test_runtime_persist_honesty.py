"""Alignment: memory persistence must never be silently lost.

Background
----------
``AgentRuntime._persist`` previously wrapped every ``memory.store`` call in a
single ``except Exception: pass``. A failed write was swallowed: the run still
reported COMPLETED/FAILED, ``memory_keys`` under-reported, and nothing signalled
that the agent had in fact *not* remembered the run. Over time that is silent
intent drift -- the agent loses the record of what failed and why, and the
next run cannot learn from it.

Fix contract (locked by this module)
------------------------------------
- a failing ``memory.store`` is surfaced in ``persist_errors`` (never silent);
- ``memory_keys`` contains ONLY keys that were truly written;
- ``run_goal`` forwards ``persist_errors`` onto the ``AgentRunResult`` so the
  gateway / dashboard can show the truth instead of a lossy "completed".

These tests run fully offline (NO real engine execution, no singletons): the
persistence path is exercised directly, and ``run_goal`` is stubbed at the
engine / evaluator / persist seams so the wiring can be proven without a run.
"""

from __future__ import annotations

import types
from typing import Any

from src.ai.agent_runtime import AgentRuntime


class _FailingMemory:
    """A memory kernel whose ``store`` always raises."""

    def store(self, key: str, value: Any, **kwargs: Any) -> None:
        raise RuntimeError(f"simulated store failure for {key!r}")

    def recall(self, key: str, **kwargs: Any) -> Any:  # pragma: no cover - unused
        return None


class _OkMemory:
    """A memory kernel that records what it was asked to store."""

    def __init__(self) -> None:
        self.stored: dict[str, Any] = {}

    def store(self, key: str, value: Any, **kwargs: Any) -> None:
        self.stored[key] = value

    def recall(self, key: str, **kwargs: Any) -> Any:  # pragma: no cover - unused
        return self.stored.get(key)


def _runtime(memory: Any) -> AgentRuntime:
    # Pass a sentinel evaluator so the constructor never reaches into the shared
    # global evaluator singleton.
    return AgentRuntime(scope="L1", memory=memory, evaluator=object())


def _goal() -> Any:
    return types.SimpleNamespace(id="g1", natural_language="do x", scope="L1")


def _ctx() -> Any:
    # _persist reads failed_tasks / completed_tasks; run_goal also calls
    # _needs_replan(ctx, ...) which reads verification_results.
    return types.SimpleNamespace(
        failed_tasks=[], completed_tasks=[], verification_results={}
    )


def test_persist_failure_is_surfaced_not_silenced():
    rt = _runtime(_FailingMemory())
    written, errors = rt._persist(_goal(), _ctx(), None, "cid")
    assert written == []
    assert errors, "a failed memory.store must be surfaced, not silently swallowed"


def test_persist_success_writes_key_and_no_errors():
    mem = _OkMemory()
    rt = _runtime(mem)
    written, errors = rt._persist(_goal(), _ctx(), None, "cid")
    assert errors == []
    assert written == ["episodic:run:g1"]
    assert "episodic:run:g1" in mem.stored


def test_run_goal_forwards_persist_errors(monkeypatch):
    """``run_goal`` must carry a persistence failure onto the result (honest)."""
    rt = _runtime(_FailingMemory())
    # Stub the heavy seams so no real engine execution happens.
    fake_ctx = _ctx()
    monkeypatch.setattr(rt.engine, "execute_goal", lambda *a, **k: fake_ctx)
    monkeypatch.setattr(rt, "_evaluate", lambda *a, **k: None)
    monkeypatch.setattr(rt, "_persist", lambda *a, **k: ([], ["boom: store failed"]))
    result = rt.run_goal("do x", goal_id="g1")
    assert result.persist_errors == ["boom: store failed"]
    assert result.memory_keys == []
