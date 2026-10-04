"""REAL kernel-level crash recovery proof (not a stub).

Sets LIUHAO_WORKSPACE_ROOT + AUDIT_DB_PATH to temp so no production data
(and never the frozen HC-01 audit_store.db) is touched.

What this proves:
  * ``ExecutionJournal`` + ``_adopt_journaled_progress`` genuinely resume
    completed tasks by NAME after a simulated crash/restart, and the real
    ``capability_executor`` (workspace-contained file writes) is NOT invoked a
    second time — i.e. side effects are not repeated.
  * ``Evaluator.execute_replan`` now RE-EXECUTES the goal through the wired
    runtime (previously a status-flip stub) and reports success/failure honestly.
"""
from __future__ import annotations

import os
import tempfile

# REDIR all data to temp BEFORE any module resolves workspace/audit paths.
_TMP = tempfile.mkdtemp(prefix="liuhao_recovery_")
os.environ["LIUHAO_WORKSPACE_ROOT"] = _TMP
os.environ["AUDIT_DB_PATH"] = os.path.join(_TMP, "audit_store.db")

from src.kernels.execution import ExecutionEngine, Goal, Task  # noqa: E402
from src.kernels.execution._journal import ExecutionJournal  # noqa: E402
from src.ai.lcore import LCore  # noqa: E402


class _TwoFileDecomposer:
    """Deterministic decomposer yielding >=2 file-write tasks with stable names.

    Names are fixed strings (not uuids) so the journal can match them across a
    simulated process restart -- exactly the property ``_adopt_journaled_progress``
    relies on.
    """

    def decompose(self, goal: Goal):
        return [
            Task(
                id="t1", goal_id=goal.id, name="WriteFile:report.txt",
                description="write report", capability_id="file_write",
                capability_namespace="kernel",
                inputs={"path": "report.txt", "content": "report-body"},
                scope=goal.scope,
            ),
            Task(
                id="t2", goal_id=goal.id, name="WriteFile:data.txt",
                description="write data", capability_id="file_write",
                capability_namespace="kernel",
                inputs={"path": "data.txt", "content": "data-body"},
                scope=goal.scope,
            ),
        ]


def _counted_lcore() -> "tuple":
    # Bind the file-write tool's workspace root to _TMP EXPLICITLY: workspace_root()
    # is cached process-wide, so relying on the LIUHAO_WORKSPACE_ROOT env would use
    # a stale root if another test imported it first. Passing root= keeps the test
    # hermetic (and never touches the frozen HC-01 audit_store.db).
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


def _goal(gid: str) -> Goal:
    return Goal(id=gid, natural_language="write two files", scope="L1",
                correlation_id="corr-" + gid)


def _file(n: str) -> str:
    return os.path.join(_TMP, n)


def test_crash_recovery_resumes_completed_tasks_no_repeat():
    """Simulate a crash/restart: a NEW engine + journal with the SAME goal id
    must resume completed tasks and NOT rewrite the files."""
    journal_path = os.path.join(_TMP, "crash_journal.db")

    # --- First run (process #1) ---
    counted, calls = _counted_lcore()
    journal1 = ExecutionJournal(journal_path)
    engine1 = ExecutionEngine(scope="L1", capability_executor=counted, journal=journal1)
    engine1.decomposer = _TwoFileDecomposer()

    ctx1 = engine1.execute_goal(_goal("G1"))
    assert os.path.exists(_file("report.txt")), "report.txt must be written"
    assert os.path.exists(_file("data.txt")), "data.txt must be written"
    assert len(ctx1.completed_tasks) == 2, "both tasks must complete"
    assert calls["file_write"] == 2, "file_write invoked exactly twice on first run"
    journal1.close()

    # --- Crash + restart (process #2): brand new engine + journal instance,
    #     same db file, same goal id -> journal matches by task NAME. ---
    counted2, calls2 = _counted_lcore()
    journal2 = ExecutionJournal(journal_path)
    engine2 = ExecutionEngine(scope="L1", capability_executor=counted2, journal=journal2)
    engine2.decomposer = _TwoFileDecomposer()

    ctx2 = engine2.execute_goal(_goal("G1"))

    # The journal must report both task names as completed.
    completed = journal2.completed_task_names("G1")
    assert completed == {"WriteFile:report.txt", "WriteFile:data.txt"}, completed

    # KEY ASSERTION: completed tasks are resumed, the executor is NOT called
    # again, so the real file-write side effect does not happen a second time.
    assert calls2["file_write"] == 0, "file_write must NOT be re-invoked on resume"
    assert len(ctx2.completed_tasks) == 2, "resume must still yield 2 completed tasks"
    assert ctx2.plan.status.value == "completed"

    # Side effects unchanged: files still present, original content intact.
    assert os.path.exists(_file("report.txt"))
    assert os.path.exists(_file("data.txt"))
    with open(_file("report.txt")) as f:
        assert f.read() == "report-body"
    with open(_file("data.txt")) as f:
        assert f.read() == "data-body"
    journal2.close()


def test_execute_replan_re_executes_via_runtime():
    """execute_replan must actually re-run the goal (not just flip status)."""
    from src.kernels.evaluation import Evaluator, ReplanRequest
    from src.ai.agent_runtime import AgentRuntime

    journal_path = os.path.join(_TMP, "replan_journal.db")
    counted, _ = _counted_lcore()
    journal = ExecutionJournal(journal_path)
    runtime = AgentRuntime(scope="L1", capability_executor=counted, journal=journal)

    # real re-executor: re-run the original goal text through the runtime.
    def executor(gid: str):
        return runtime.run_goal(
            "write a file named replan.txt containing done", goal_id=gid, persist=False
        )
    ev = Evaluator(replan_executor=executor)
    req = ReplanRequest(goal_id="replan-gid")
    ev._replan_requests.append(req)

    ok = ev.execute_replan(req.id)
    assert ok is True, "execute_replan must return True when re-execution succeeds"
    assert req.status == "executed"
    assert os.path.exists(_file("replan.txt")), "re-execution must write the file"


def test_execute_replan_false_when_unrecoverable():
    """execute_replan must NEVER return True while failing."""
    from types import SimpleNamespace
    from src.kernels.evaluation import Evaluator, ReplanRequest
    from src.ai.agent_runtime import AgentRunState

    # Case 1: re-execution fails -> honest False.
    # BOTH shapes are exercised on purpose. The bare string is what the test
    # double used to return; ``AgentRunState.FAILED`` is what the real runtime
    # actually returns, and it is a str-mixin Enum whose ``str()`` is
    # "AgentRunState.FAILED". A check written as ``str(state) == "failed"``
    # therefore rejected the double and waved the real failure through --
    # execute_replan returned True for a goal that failed again and did no
    # work. Passing only the string kept that invisible.
    for label, state in (("bare string", "failed"),
                         ("real AgentRunState", AgentRunState.FAILED),
                         ("cancelled by human", AgentRunState.CANCELLED)):
        ev = Evaluator(replan_executor=lambda gid, _s=state: SimpleNamespace(state=_s))
        req = ReplanRequest(goal_id="fail-gid")
        ev._replan_requests.append(req)
        assert ev.execute_replan(req.id) is False, f"{label} must report failure"
        assert req.status != "executed", f"{label} must not mark executed"
        assert req.executed_at is None, f"{label} must not stamp executed_at"

    # Case 2: no runtime wired -> cannot re-execute -> honest False.
    ev2 = Evaluator()  # no executor
    req2 = ReplanRequest(goal_id="nowire-gid")
    ev2._replan_requests.append(req2)
    assert ev2.execute_replan(req2.id) is False
    assert req2.status != "executed"

    # Case 3: an unrecognised state must fail closed rather than be waved
    # through as success -- "not FAILED" is not the same claim as "succeeded".
    ev3 = Evaluator(replan_executor=lambda gid: SimpleNamespace(state="some-new-state"))
    req3 = ReplanRequest(goal_id="unknown-gid")
    ev3._replan_requests.append(req3)
    assert ev3.execute_replan(req3.id) is False
    assert req3.status != "executed"


def test_execute_replan_real_runtime_failure_is_not_success():
    """End-to-end: a goal that FAILS for real must not be reported as replanned.

    This is the regression test for the P0 "reports success, did nothing"
    defect. It wires the genuine ``AgentRuntime`` and drives it with a
    decomposer whose task can never succeed (the write escapes the workspace
    root and is refused by the filesystem adapter), so the runtime really
    returns ``AgentRunState.FAILED``. No test double is involved anywhere on
    the path that decides the verdict -- which is exactly why the double-based
    test above missed the defect.
    """
    from src.kernels.evaluation import Evaluator, ReplanRequest
    from src.kernels.execution import Task
    from src.ai.agent_runtime import AgentRuntime, AgentRunState

    class _ImpossibleDecomposer:
        def decompose(self, goal):
            return [
                Task(
                    id="t1", goal_id=goal.id, name="WriteFile:escape.txt",
                    description="write outside the workspace root",
                    capability_id="file_write", capability_namespace="kernel",
                    inputs={"path": "../escaped.txt", "content": "x"},
                    scope=goal.scope,
                )
            ]

    counted, _ = _counted_lcore()
    runtime = AgentRuntime(
        scope="L1", capability_executor=counted,
        journal=ExecutionJournal(os.path.join(_TMP, "replan_fail_journal.db")),
    )
    runtime.engine.decomposer = _ImpossibleDecomposer()

    # The real run genuinely fails.
    first = runtime.run_goal("do the impossible", goal_id="G-FAIL", persist=False)
    assert first.state == AgentRunState.FAILED, first.state
    assert first.context is not None and first.context.failed_tasks

    ev = Evaluator()
    ev.set_replan_executor(runtime._replan_for_evaluator)
    req = ReplanRequest(goal_id="G-FAIL")
    ev._replan_requests.append(req)

    assert ev.execute_replan(req.id) is False, (
        "a re-run that FAILED again must not be reported as a successful replan"
    )
    assert req.status != "executed"
    assert req.executed_at is None
    # And nothing was written: the refusal was real, not just reported.
    assert not os.path.exists(os.path.join(os.path.dirname(_TMP), "escaped.txt"))


def test_retry_dead_letter_honest():
    """retry_dead_letter must NOT claim success when the handler fails again."""
    from src.kernels.event import EventBus, Event, EventScope

    bus = EventBus()

    def boom(event: Event) -> None:
        raise RuntimeError("kaboom")

    bus.subscribe("fail.evt", boom, scope=EventScope.L7)
    bus.publish(Event(type="fail.evt", source="t", scope=EventScope.L7))
    assert len(bus.get_dead_letters()) == 1

    # Handler still fails on re-dispatch -> must report False (never True-while-failing).
    assert bus.retry_dead_letter(0) is False

    # Now fix the handler: re-dispatch succeeds -> True, and it is marked resolved.
    bus._subscriptions["fail.evt"][0].handler = lambda e: None
    assert bus.retry_dead_letter(0) is True
    assert bus.get_dead_letters(unresolved_only=False)[0].resolved is True
