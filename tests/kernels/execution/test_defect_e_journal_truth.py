"""Defect E: a journaled "completed" claim must be PROVEN, not assumed.

The defect: ``_adopt_journaled_progress`` stamped any name match as COMPLETED
with a synthetic ``success=True`` ActionResult and zero evidence. "This task
finished at some point" was treated as "its side effect is still here" -- so an
artifact that had since been deleted was reported as done, and the goal declared
complete over work that was not on disk.

Boundary vs ARM1: ARM1 governs WHO performed the work (attribution); Defect E
governs WHETHER the work is still there (persistence). They are separate
judgements and must not be merged into one predicate.

Uses forensic fixtures only (temp journal DB + temp workspace). No real store.
"""
from __future__ import annotations

import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="liuhao_defect_e_")
os.environ["LIUHAO_WORKSPACE_ROOT"] = _TMP
os.environ.setdefault("AUDIT_DB_PATH", os.path.join(_TMP, "audit_store.db"))
os.environ.setdefault("LIUHAO_AUDIT_DB_PATH", os.path.join(_TMP, "audit_store.db"))

from src.kernels.execution import (  # noqa: E402
    ActionResult,
    ExecutionContext,
    ExecutionEngine,
    ExecutionJournal,
    ExecutionPlan,
    Goal,
    Task,
    TaskStatus,
    artifact_currently_exists,
    artifact_path_from_output,
)


def _goal(gid: str = "G-E") -> Goal:
    return Goal(id=gid, natural_language="write a file", scope="L1")


def _write_task(name: str = "WriteFile", path: str = None) -> Task:
    p = path or os.path.join(_TMP, "artifact.txt")
    return Task(
        id="t1",
        goal_id="G-E",
        name=name,
        description="write",
        capability_id="file_write",
        inputs={"path": p, "content": "body"},
    )


def _ctx(tasks) -> ExecutionContext:
    """Minimal context: recovery only touches plan.tasks / completed / results."""
    return ExecutionContext(
        goal=_goal(),
        plan=ExecutionPlan(id="P-E", goal_id="G-E", tasks=list(tasks)),
        context_kernel=None,
    )


def _engine(db_path, executor=None) -> ExecutionEngine:
    eng = ExecutionEngine(
        scope="L1",
        capability_executor=executor or (lambda c, i: {"success": True}),
        journal=ExecutionJournal(db_path),
    )
    eng._execution_id = "G-E"
    return eng


def _claim_complete(eng, task, artifact_path):
    """Record a completion the way ``_execute_task`` now does."""
    eng._journal_event(
        task,
        "task_completed",
        task_id=task.id,
        goal_id=task.goal_id,
        artifact_path=artifact_path,
    )


# ---------------------------------------------------------------------------
# unit: the proof primitive itself
# ---------------------------------------------------------------------------
def test_artifact_proof_never_confuses_unprovable_with_verified():
    """None (nothing declared) must never read as True (verified)."""
    assert artifact_path_from_output({"ok": True}) is None
    assert artifact_path_from_output({"path": "/x/y.txt"}) == "/x/y.txt"
    assert artifact_path_from_output("not-a-dict") is None

    real = os.path.join(_TMP, "real.txt")
    with open(real, "w") as f:
        f.write("data")
    empty = os.path.join(_TMP, "empty.txt")
    open(empty, "w").close()

    assert artifact_currently_exists(real) is True       # proven present
    assert artifact_currently_exists(empty) is False     # empty -> not proven
    assert artifact_currently_exists("/no/such/file") is False  # absent
    assert artifact_currently_exists(None) is None       # nothing declared


# ---------------------------------------------------------------------------
# 1. artifact deleted externally -> resumed run must NOT mark it COMPLETED
# ---------------------------------------------------------------------------
def test_deleted_artifact_is_not_reclaimed_as_completed():
    """A journal claim whose artifact is gone must be refused, not reclaimed."""
    db = os.path.join(_TMP, "j_deleted.db")
    target = os.path.join(_TMP, "gone.txt")

    def writer(capability_id, inputs):
        with open(inputs["path"], "w") as f:
            f.write("payload")
        return {"success": True, "path": inputs["path"]}

    eng = _engine(db, writer)
    task = _write_task(path=target)

    # First run really writes it, so the journal records an artifact path.
    with open(target, "w") as f:
        f.write("payload")
    _claim_complete(eng, task, target)
    assert eng.journal.completed_task_artifacts("G-E") == {"WriteFile": target}

    # The artifact is now deleted by something outside the engine.
    os.remove(target)
    assert not os.path.exists(target)

    ctx = _ctx([_write_task(path=target)])
    resumed = eng._adopt_journaled_progress(ctx)

    assert resumed == 0, "a claim with a missing artifact must not be reclaimed"
    t = ctx.plan.tasks[0]
    assert t.status == TaskStatus.PENDING, (
        "the task must stay PENDING so it re-executes and restores the artifact"
    )
    assert t.id not in ctx.completed_tasks
    # And the refusal is recorded as evidence, not just silently skipped.
    kinds = [e.event_type for e in eng.journal.events("G-E")]
    assert "journal_claim_unverified" in kinds


# ---------------------------------------------------------------------------
# 2. artifact genuinely present -> normal reclaim still works
# ---------------------------------------------------------------------------
def test_present_artifact_is_reclaimed_and_marked_verified():
    """Control: when the artifact is really there, resume must still work."""
    db = os.path.join(_TMP, "j_present.db")
    target = os.path.join(_TMP, "here.txt")
    with open(target, "w") as f:
        f.write("payload")

    eng = _engine(db)
    task = _write_task(path=target)
    _claim_complete(eng, task, target)

    ctx = _ctx([_write_task(path=target)])
    resumed = eng._adopt_journaled_progress(ctx)

    assert resumed == 1
    t = ctx.plan.tasks[0]
    assert t.status == TaskStatus.COMPLETED
    assert t.id in ctx.completed_tasks
    result = ctx.task_results[t.id]
    assert isinstance(result, ActionResult)
    assert result.success is True
    # Provenance: accepted back because the artifact was VERIFIED, not assumed.
    assert result.output["resume_basis"] == "artifact_verified"
    assert result.output["artifact_path"] == target


# ---------------------------------------------------------------------------
# 3. journal-injected names go through the SAME verification path
# ---------------------------------------------------------------------------
def test_injected_journal_row_cannot_bypass_verification():
    """Forensic fixture: forge a completion row for a file that never existed.

    If name-matching alone were enough, this forged row would be reclaimed. It
    must not be -- the same artifact check applies to injected rows, because the
    journal is a shared,Writable surface and not a trusted oracle.
    """
    db = os.path.join(_TMP, "j_forged.db")
    forged = os.path.join(_TMP, "never_written.txt")
    assert not os.path.exists(forged)

    eng = _engine(db)
    task = _write_task(name="Forged", path=forged)
    # Inject the claim directly into the journal (no execution ever happened).
    eng.journal.record(
        "G-E", "task_completed", task_name="Forged",
        task_id=task.id, goal_id="G-E", artifact_path=forged,
    )
    assert "Forged" in eng.journal.completed_task_names("G-E")
    assert eng.journal.completed_task_artifacts("G-E") == {"Forged": forged}

    ctx = _ctx([task])
    resumed = eng._adopt_journaled_progress(ctx)

    assert resumed == 0, "a forged completion row must not be honoured"
    assert task.status == TaskStatus.PENDING
    kinds = [e.event_type for e in eng.journal.events("G-E")]
    assert "journal_claim_unverified" in kinds


def test_task_without_declared_artifact_is_not_blocked():
    """A task that declared no artifact has nothing that could have gone away,
    so it is still reclaimable -- but recorded as UNVERIFIABLE, never 'verified'.
    """
    db = os.path.join(_TMP, "j_noartifact.db")
    eng = _engine(db)
    task = _write_task(name="Compute")
    _claim_complete(eng, task, None)

    ctx = _ctx([task])
    resumed = eng._adopt_journaled_progress(ctx)

    assert resumed == 1
    assert task.status == TaskStatus.COMPLETED
    # Honest label: nothing was declared, so nothing was verified.
    assert ctx.task_results[task.id].output["resume_basis"] == "no_artifact_declared"
    assert ctx.task_results[task.id].output["artifact_path"] is None
