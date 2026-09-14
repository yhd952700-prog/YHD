"""Agent Runtime — Goal→Plan→Execute→Observe→Evaluate→Memory orchestration.

This is an ADDITIVE orchestration layer on top of the existing, verified kernels.
It does NOT replace or modify :class:`ExecutionEngine` (the SSOT execution
orchestrator in ``src/kernels/execution/__init__.py``). It composes:

* ``ExecutionEngine`` — execution kernel, SSOT for task execution (reused, not forked)
* ``Evaluator``       — evaluation kernel; previously an orphan, wired in here
* ``MemoryKernel``    — memory kernel; persistence of runs / learnings
* ``EventBus``        — event kernel; synchronous, inline dispatch

Design constraints (project mandate):
  * No existing public API is removed or renamed.
  * All changes are rollbackable (one new file + one 3-line guard in the engine).
  * Reuse, don't duplicate: ``src/ai/goal_task_graph.py`` stays off-path (non-SSOT).

See ``docs/AGENT-RUNTIME-DESIGN.md`` for the full design (Phase 3).
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from src.kernels.execution import (
    ExecutionEngine,
    ExecutionContext,
    Goal,
    CapabilityExecutor,
)
from src.kernels.evaluation import (
    EvaluationResult,
    get_evaluator,
)
from src.kernels.memory import (
    MemoryKernel,
    MemoryScope,
    MemoryTier,
    get_memory_kernel,
)
from src.kernels.event import (
    Event,
    EventScope,
    subscribe_event,
)

# Engine-published event types we observe to build the Execution Trace.
_OBSERVED_EVENTS = (
    "execution_started",
    "task_started",
    "task_completed",
    "task_retrying",
    "task_failed",
    "execution_completed",
    "execution_resumed",
)


class AgentRunState(str, Enum):
    """Goal-level lifecycle state machine (user's 7-state requirement).

    Individual task states still live in ``TaskStatus`` (PENDING/PLANNED/RUNNING/
    COMPLETED/FAILED/...). ``WAITING`` and ``VERIFYING`` are runtime-derived
    observations, intentionally NOT added to ``TaskStatus`` so no existing API
    is touched.
    """

    CREATED = "created"
    PLANNING = "planning"
    EXECUTING = "executing"
    WAITING = "waiting"        # derived: dependency not yet satisfied
    VERIFYING = "verifying"    # derived: during Evaluator.evaluate
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class TraceEntry:
    """A single observed event in the execution trace."""

    task_id: Optional[str]
    event_type: str
    ts: datetime
    capability: Optional[str] = None
    decision: Optional[str] = None
    tool_call: Optional[Dict[str, Any]] = None
    model_call: Optional[Dict[str, Any]] = None
    output: Any = None
    error: Optional[str] = None
    correlation_id: Optional[str] = None


@dataclass
class ExecutionTrace:
    """Ordered, correlation-scoped record of what the agent actually did."""

    correlation_id: str
    entries: List[TraceEntry] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def add(self, entry: TraceEntry) -> None:
        with self._lock:
            self.entries.append(entry)

    def summary(self) -> Dict[str, Any]:
        with self._lock:
            by_type: Dict[str, int] = {}
            for e in self.entries:
                by_type[e.event_type] = by_type.get(e.event_type, 0) + 1
            return {
                "correlation_id": self.correlation_id,
                "event_count": len(self.entries),
                "by_type": by_type,
                "errors": [e.error for e in self.entries if e.error],
            }


class AgentObserver:
    """Subscribes to EventBus for one correlation_id and builds an ExecutionTrace.

    The handler is intentionally defensive: ``EventBus.publish`` runs handlers
    *synchronously and swallows exceptions into DeadLetter* (see
    ``src/kernels/event/__init__.py:150``), so a raising handler would silently
    lose the event. We never raise.
    """

    def __init__(self, correlation_id: str) -> None:
        self.correlation_id = correlation_id
        self.trace = ExecutionTrace(correlation_id=correlation_id)
        self._sub_ids: List[str] = []

    def subscribe(self) -> None:
        # Subscribe at the widest scope (L7) so we receive every event
        # regardless of the goal's own scope. EventBus filters
        # `subscription_scope < event_scope` (see src/kernels/event/__init__.py:102),
        # so a narrow L0 subscription would silently drop L1+ events. The
        # correlation_id filter already restricts us to this goal's chain.
        for event_type in _OBSERVED_EVENTS:
            sid = subscribe_event(
                event_type,
                self._on_event,
                scope=EventScope.L7,
                correlation_id=self.correlation_id,
            )
            self._sub_ids.append(sid)

    def unsubscribe(self) -> None:
        # Best-effort cleanup; EventBus is process-global.
        try:
            from src.kernels.event import get_event_bus

            bus = get_event_bus()
            for sid in self._sub_ids:
                try:
                    bus.unsubscribe(sid)
                except Exception:
                    pass
        except Exception:
            pass
        self._sub_ids.clear()

    def _on_event(self, event: Event) -> None:
        try:
            data = event.data or {}
            entry = TraceEntry(
                task_id=data.get("task_id"),
                event_type=event.type,
                ts=event.timestamp,
                capability=data.get("name") or data.get("capability_id"),
                decision=event.type,
                output=data,
                correlation_id=event.correlation_id,
            )
            if event.type == "task_failed":
                entry.error = data.get("error")
            self.trace.add(entry)
        except Exception:
            # Swallow: never break the synchronous EventBus dispatch.
            pass


@dataclass
class AgentRunResult:
    """Outcome of one ``run_goal`` (or ``replan``/``recover``) call."""

    goal_id: str
    correlation_id: str
    state: AgentRunState
    context: Optional[ExecutionContext]
    evaluation: Optional[EvaluationResult]
    trace: ExecutionTrace
    memory_keys: List[str] = field(default_factory=list)
    replan_count: int = 0
    replan_suggested: bool = False
    error: Optional[str] = None


class AgentRuntime:
    """Orchestrates a full agent run by composing the existing kernels.

    The engine remains the SSOT for execution; this class adds the Observer,
    Evaluator wiring, MemoryUpdate, and the replan/rollback hooks that were
    previously no-ops or orphans.
    """

    def __init__(
        self,
        scope: str = "L1",
        capability_executor: Optional[CapabilityExecutor] = None,
        journal: Any = None,
        evaluator: Optional[Any] = None,
        memory: Optional[MemoryKernel] = None,
        max_replans: int = 2,
    ) -> None:
        self.scope = scope
        self.engine = ExecutionEngine(
            scope=scope, capability_executor=capability_executor, journal=journal
        )
        self.evaluator = evaluator or get_evaluator()
        self.memory = memory or get_memory_kernel()
        self.max_replans = max_replans

    # ------------------------------------------------------------------ #
    # Public entry point
    # ------------------------------------------------------------------ #
    def run_goal(
        self,
        goal_text: str,
        *,
        goal_id: Optional[str] = None,
        scope: Optional[str] = None,
        plan_mode: str = "auto",
        verification_criteria: Optional[Dict[str, Any]] = None,
        persist: bool = True,
        correlation_id: Optional[str] = None,
    ) -> AgentRunResult:
        """Run one goal end-to-end: plan → execute → evaluate → (persist).

        Failure recovery is *reported* via ``replan_suggested``; call
        :meth:`replan` explicitly to act on it (avoids hidden recursion / loops).
        """
        scope = scope or self.scope
        goal_id = goal_id or str(uuid.uuid4())[:8]
        correlation_id = correlation_id or str(uuid.uuid4())

        observer = AgentObserver(correlation_id)
        observer.subscribe()
        state = AgentRunState.CREATED
        try:
            state = AgentRunState.PLANNING
            goal = Goal(
                id=goal_id,
                natural_language=goal_text,
                scope=scope,
                correlation_id=correlation_id,
            )

            state = AgentRunState.EXECUTING
            ctx = self.engine.execute_goal(
                goal, plan_mode=plan_mode, verification_criteria=verification_criteria
            )

            state = AgentRunState.VERIFYING
            evaluation = self._evaluate(goal, ctx, correlation_id)
            memory_keys = self._persist(goal, ctx, evaluation, correlation_id) if persist else []

            replan_suggested = self._needs_replan(ctx, evaluation)
            state = (
                AgentRunState.COMPLETED
                if not ctx.failed_tasks
                else AgentRunState.FAILED
            )
            return AgentRunResult(
                goal_id=goal_id,
                correlation_id=correlation_id,
                state=state,
                context=ctx,
                evaluation=evaluation,
                trace=observer.trace,
                memory_keys=memory_keys,
                replan_suggested=replan_suggested,
            )
        except Exception as exc:  # graceful failure, never crash the caller
            return AgentRunResult(
                goal_id=goal_id,
                correlation_id=correlation_id,
                state=AgentRunState.FAILED,
                context=None,
                evaluation=None,
                trace=observer.trace,
                error=str(exc),
            )
        finally:
            observer.unsubscribe()

    # ------------------------------------------------------------------ #
    # Failure recovery hooks
    # ------------------------------------------------------------------ #
    def replan(
        self,
        prior: AgentRunResult,
        *,
        extra_criteria: Optional[Dict[str, Any]] = None,
        max_replans: Optional[int] = None,
    ) -> AgentRunResult:
        """Re-run a goal after a failed/partial run (fills the engine's L811 no-op).

        Idempotent: ``ExecutionEngine`` resumes already-completed tasks by name
        via its journal (``src/kernels/execution/__init__.py:714``), so side
        effects are not repeated. Bounded by ``max_replans`` to avoid loops.
        """
        max_replans = max_replans or self.max_replans
        if prior.replan_count >= max_replans:
            return prior
        goal_text = prior.context.goal.natural_language if prior.context else ""
        result = self.run_goal(
            goal_text,
            goal_id=prior.goal_id,
            correlation_id=prior.correlation_id,
            plan_mode="auto",
            verification_criteria=extra_criteria,
            persist=True,
        )
        result.replan_count = prior.replan_count + 1
        return result

    def recover(self, goal: Goal) -> AgentRunResult:
        """Idempotent replay of a goal (rollforward / crash recovery).

        Same goal id + description → ``GoalDecomposer`` produces the same task
        *names* → engine resumes completed tasks, replays the rest.
        """
        return self.run_goal(
            goal.natural_language,
            goal_id=goal.id,
            correlation_id=goal.correlation_id,
            persist=True,
        )

    # ------------------------------------------------------------------ #
    # Internal wiring
    # ------------------------------------------------------------------ #
    def _evaluate(
        self, goal: Goal, ctx: ExecutionContext, correlation_id: str
    ) -> Optional[EvaluationResult]:
        intended: Dict[str, Any] = {
            "id": goal.id,
            "natural_language": goal.natural_language,
            "scope": goal.scope,
        }
        actual: Dict[str, Any] = {
            "goal_id": goal.id,
            "completed": sorted(ctx.completed_tasks),
            "failed": sorted(ctx.failed_tasks),
            "task_results": {
                tid: (r.output if r is not None else None)
                for tid, r in ctx.task_results.items()
            },
            "verification": {
                tid: (v.result.value, v.score)
                for tid, v in ctx.verification_results.items()
            },
        }
        try:
            return self.evaluator.evaluate(
                intended, actual, scope=goal.scope, correlation_id=correlation_id
            )
        except Exception:
            return None

    def _needs_replan(
        self, ctx: ExecutionContext, evaluation: Optional[EvaluationResult]
    ) -> bool:
        if any(v.replan_required for v in ctx.verification_results.values()):
            return True
        if evaluation is not None and evaluation.replan_triggered and ctx.failed_tasks:
            return True
        return False

    def _persist(
        self,
        goal: Goal,
        ctx: ExecutionContext,
        evaluation: Optional[EvaluationResult],
        correlation_id: str,
    ) -> List[str]:
        keys: List[str] = []
        try:
            scope = MemoryScope(goal.scope)
            episodic = {
                "goal_id": goal.id,
                "goal": goal.natural_language,
                "state": "failed" if ctx.failed_tasks else "completed",
                "completed": len(ctx.completed_tasks),
                "failed": len(ctx.failed_tasks),
                "score": evaluation.overall_score if evaluation is not None else None,
                "correlation_id": correlation_id,
            }
            key = f"episodic:run:{goal.id}"
            self.memory.store(
                key, episodic, tier=MemoryTier.MID_TERM, scope=scope,
                tags={"episodic", "agent_run"},
            )
            keys.append(key)

            if evaluation is not None and evaluation.replan_triggered:
                learn = {
                    "goal_id": goal.id,
                    "feedback": evaluation.feedback,
                    "replan": True,
                }
                k2 = f"semantic:learning:{goal.id}"
                self.memory.store(
                    k2, learn, tier=MemoryTier.MID_TERM, scope=scope,
                    tags={"semantic", "learning"},
                )
                keys.append(k2)
        except Exception:
            # Persistence must never fail a run.
            pass
        return keys


# ---------------------------------------------------------------------- #
# Process-wide singleton (same philosophy as get_evaluator / get_memory_kernel)
# ---------------------------------------------------------------------- #
_global_runtime: Optional[AgentRuntime] = None
_global_runtime_lock = threading.RLock()


def get_agent_runtime(
    scope: str = "L1",
    capability_executor: Optional[CapabilityExecutor] = None,
    journal: Any = None,
    evaluator: Optional[Any] = None,
    memory: Optional[MemoryKernel] = None,
) -> AgentRuntime:
    """Get or create the process-wide AgentRuntime."""
    global _global_runtime
    if _global_runtime is None:
        with _global_runtime_lock:
            if _global_runtime is None:
                _global_runtime = AgentRuntime(
                    scope=scope,
                    capability_executor=capability_executor,
                    journal=journal,
                    evaluator=evaluator,
                    memory=memory,
                )
    return _global_runtime
