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
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from src.kernels.execution import (
    ExecutionEngine,
    ExecutionContext,
    Goal,
    CapabilityExecutor,
    GoalCancelled,
)
from src.kernels.evaluation import (
    EvaluationOutcome,
    EvaluationResult,
    FeedbackType,
    get_evaluator,
)
from .llm_support import (
    compose_execution_summary,
    default_generate,
    failure_summary,
    goal_requires_artifact,
    task_output_facts,
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
    CANCELLED = "cancelled"


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


def _record_employee(
    task: Any,
    resolver: Optional[Callable[[str], Optional[str]]],
    pinned_agent: Optional[str],
) -> Optional[str]:
    """Who really did this task, as recordable evidence.

    On a pinned (user-named) run the decomposer may already have stamped the
    task with *someone else's* name; the pinned agent resolved against the live
    roster is the only truthful source. No resolver => no name, never a guess.
    """
    if pinned_agent:
        if not callable(resolver):
            return None
        try:
            return resolver(str(pinned_agent)) or None
        except Exception:  # noqa: BLE001 - unresolvable claim => no name
            return None
    employee = getattr(task, "assigned_employee", None)
    agent_id = getattr(task, "assigned_agent", None)
    if not employee and agent_id and callable(resolver):
        try:
            employee = resolver(str(agent_id))
        except Exception:  # noqa: BLE001 - unresolvable claim => no name
            employee = None
    return employee


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
    persist_errors: List[str] = field(default_factory=list)
    replan_count: int = 0
    replan_suggested: bool = False
    error: Optional[str] = None
    #: 派活前的能力边界提示（例如"需要写文件但 file_write 不可用"）。这些
    #: 提示在执行开始前就产生并上报，不能等跑完再解释。
    preflight_notices: List[str] = field(default_factory=list)


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
        executable_capabilities: Optional[Any] = None,
        artifact_index: Any = None,
        enable_model_planning: bool = False,
        model_generator: Optional[Callable[[str], str]] = None,
    ) -> None:
        self.scope = scope
        self.engine = ExecutionEngine(
            scope=scope,
            capability_executor=capability_executor,
            journal=journal,
            artifact_index=artifact_index,
        )
        # Capability provider for both the planner contract and the LLM planner.
        provider = None
        if executable_capabilities is not None:
            provider = (
                executable_capabilities
                if callable(executable_capabilities)
                else lambda: executable_capabilities
            )
        # Kept for the *preflight* check: before a goal runs we can already tell
        # the user "this needs file_write / network_bus, and it is not available".
        self._capability_provider: Optional[Callable[[], Any]] = provider

        # Model-first decomposition (opt-in). It sits INSIDE the executable-aware
        # wrapper below, so a model plan is still validated against the real
        # capability registry before any task runs. Every failure inside the
        # LLM planner degrades to the deterministic keyword plan.
        self.enable_model_planning = bool(enable_model_planning)
        self._model_generator: Optional[Callable[[str], str]] = (
            model_generator
            if model_generator is not None
            else (default_generate if enable_model_planning else None)
        )
        self._llm_decomposer = None
        if enable_model_planning:
            from .llm_decomposer import LLMTaskDecomposer

            self._llm_decomposer = LLMTaskDecomposer(
                self.engine.decomposer,
                generator=self._model_generator,
                capability_provider=provider,
            )
            self.engine.decomposer = self._llm_decomposer

        # Planner ↔ executor contract (2026-10-04). Opt-in: a set of capability
        # ids, or a zero-arg callable returning them. When supplied, the plan is
        # validated BEFORE any task runs, so a goal needing an unregistered
        # capability fails fast with a named reason instead of executing part of
        # the work and failing afterwards. Unsupplied => unchanged behaviour.
        if provider is not None:
            from .planner_contract import ExecutableAwareDecomposer

            self.engine.decomposer = ExecutableAwareDecomposer(
                self.engine.decomposer, capability_provider=provider
            )
        self.evaluator = evaluator or get_evaluator()
        self.memory = memory or get_memory_kernel()
        self.max_replans = max_replans
        #: agent id → 员工名。网关注入后，汇总才能写清"谁做了什么"；未注入时
        #: 汇总只用 agent id（不编造员工名）。
        self.agent_employee_resolver: Optional[Callable[[str], Optional[str]]] = None
        # Remember the goal text for each goal_id so ``replan``/``recover`` can
        # actually re-run the *same* goal (deterministic task names -> the
        # engine's journal resumes completed tasks). Guarded by no lock: a
        # runtime drives one goal at a time through ``run_goal``.
        self._goal_texts: Dict[str, str] = {}
        # Per-goal stop flags (threading.Event) for cooperative cancellation.
        # Keyed by goal_id; set by :meth:`request_stop` so the execution loop
        # can abort a running goal cleanly.
        self._stop_events: Dict[str, threading.Event] = {}
        # Route the evaluation kernel's ``execute_replan`` action to a real
        # re-execution instead of a status flip. The evaluation kernel stays
        # layering-clean: it only holds a callable.
        try:
            self.evaluator.set_replan_executor(self._replan_for_evaluator)
        except Exception:
            # A non-Evaluator (e.g. a test double) may not implement the hook.
            pass

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
        stop_event: Optional[threading.Event] = None,
        on_task_progress: Optional[Callable[[List[Dict[str, Any]]], None]] = None,
        pinned_agent: Optional[str] = None,
        only_task_name: Optional[str] = None,
        only_task_names: Optional[Sequence[str]] = None,
        pinned_tasks: Optional[Sequence[Dict[str, Any]]] = None,
        on_notice: Optional[Callable[[str], None]] = None,
    ) -> AgentRunResult:
        """Run one goal end-to-end: plan → execute → evaluate → (persist).

        Failure recovery is *reported* via ``replan_suggested``; call
        :meth:`replan` explicitly to act on it (avoids hidden recursion / loops).

        ``stop_event`` is an optional :class:`threading.Event` (keyed by
        ``goal_id``); when it is set, the execution loop raises
        :class:`GoalCancelled` and this returns a CANCELLED result -- a real,
        cooperative abort rather than a faked stop.
        """
        scope = scope or self.scope
        goal_id = goal_id or str(uuid.uuid4())[:8]
        correlation_id = correlation_id or str(uuid.uuid4())
        self._goal_texts[goal_id] = goal_text
        if stop_event is None:
            stop_event = threading.Event()
        self._stop_events[goal_id] = stop_event

        observer = AgentObserver(correlation_id)
        observer.subscribe()
        # 能力边界前置：在拆解/执行之前就把"做不到的事"说清楚。跑完再解释
        # 会被用户当成事后找借口，而且会让人误以为已经做了。
        preflight_notices = self._preflight_capability_notices(goal_text)
        if callable(on_notice):
            for notice in preflight_notices:
                try:
                    on_notice(notice)
                except Exception:  # noqa: BLE001 - 通知失败不能拖垮目标
                    pass
        state = AgentRunState.CREATED
        try:
            state = AgentRunState.PLANNING
            goal = Goal(
                id=goal_id,
                natural_language=goal_text,
                scope=scope,
                correlation_id=correlation_id,
            )

            # --- MEMORY RECALL: apply learned directives before planning ---
            # This is what makes memory CAUSAL, not just a write-only log.
            # Learned directives (e.g. "NEVER write files") can change the
            # outcome of a run. Without this step, memory is decorative.
            blocked_reason = self._check_learning_blockers(goal, scope)
            if blocked_reason is not None:
                # The run is blocked by a learned directive. Return a
                # FAILED result honestly — the memory changed the outcome.
                state = AgentRunState.FAILED
                blocked_error = f"blocked by learned directive: {blocked_reason}"
                return AgentRunResult(
                    goal_id=goal_id,
                    correlation_id=correlation_id,
                    state=state,
                    context=None,
                    evaluation=self._failed_evaluation(
                        goal_id,
                        correlation_id,
                        "未完成：本次执行被已记住的规则阻止。"
                        "本次没有生成任何文件，也没有任何可交付的产物路径。"
                        "请调整目标或修改该规则后重试。",
                        blocked_error,
                    ),
                    trace=observer.trace,
                    memory_keys=[],
                    persist_errors=[],
                    error=blocked_error,
                    preflight_notices=preflight_notices,
                )

            state = AgentRunState.EXECUTING
            ctx = self.engine.execute_goal(
                goal,
                plan_mode=plan_mode,
                verification_criteria=verification_criteria,
                stop_event=stop_event,
                on_task_progress=on_task_progress,
                pinned_agent=pinned_agent,
                only_task_name=only_task_name,
                only_task_names=only_task_names,
                pinned_tasks=pinned_tasks,
            )

            state = AgentRunState.VERIFYING
            evaluation = self._evaluate(
                goal,
                ctx,
                correlation_id,
                pinned_agent=pinned_agent,
                preflight_notices=preflight_notices,
            )
            memory_keys, persist_errors = (
                self._persist(goal, ctx, evaluation, correlation_id)
                if persist
                else ([], [])
            )

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
                persist_errors=persist_errors,
                replan_suggested=replan_suggested,
                preflight_notices=preflight_notices,
            )
        except GoalCancelled as exc:
            # Cooperative cancellation: the goal was aborted by a stop request.
            # Report it honestly as CANCELLED -- never as success.
            return AgentRunResult(
                goal_id=goal_id,
                correlation_id=correlation_id,
                state=AgentRunState.CANCELLED,
                context=None,
                evaluation=self._failed_evaluation(
                    goal_id,
                    correlation_id,
                    "已取消：本次执行被用户中止。"
                    "本次没有生成任何文件，也没有任何可交付的产物路径。"
                    "需要结果时请重新派活。",
                    f"goal {goal_id} cancelled: {exc}",
                ),
                trace=observer.trace,
                error=f"goal {goal_id} cancelled: {exc}",
                preflight_notices=preflight_notices,
            )
        except Exception as exc:  # graceful failure, never crash the caller
            return AgentRunResult(
                goal_id=goal_id,
                correlation_id=correlation_id,
                state=AgentRunState.FAILED,
                context=None,
                evaluation=self._failed_evaluation(
                    goal_id, correlation_id, failure_summary(exc), str(exc)
                ),
                trace=observer.trace,
                error=str(exc),
                preflight_notices=preflight_notices,
            )
        finally:
            observer.unsubscribe()
            self._stop_events.pop(goal_id, None)

    def set_employee_provider(
        self, provider: Optional[Callable[[], Sequence[Dict[str, Any]]]]
    ) -> None:
        """注入真实员工花名册（拆解时由管家挑人；模型规划关闭时无效果）。"""
        decomposer = getattr(self, "_llm_decomposer", None)
        if decomposer is None:
            return
        decomposer.set_employee_provider(provider)

    #: 目标里表示"要联网"的说法（粗判，用于执行前的边界提示，不阻断执行）。
    _NET_INTENT_MARKERS = (
        "联网", "上网", "在线", "实时", "最新", "今天的", "搜索", "检索",
        "查一下", "查查", "调研", "热榜", "热搜",
    )

    def _preflight_capability_notices(self, goal_text: str) -> List[str]:
        """Say what this goal cannot do **before** anything runs.

        Only evidence is used: the goal text plus the *registered* capability
        ids. No notice is produced when the registry is unknown, so this never
        invents a limitation either.
        """
        provider = getattr(self, "_capability_provider", None)
        if not callable(provider):
            return []
        try:
            available = {str(item) for item in (provider() or [])}
        except Exception:  # noqa: BLE001 - unknown registry => no claim
            return []
        if not available:
            return []
        text = str(goal_text or "")
        notices: List[str] = []
        if goal_requires_artifact(text) and "file_write" not in available:
            notices.append(
                "派活前提示：目标要求产出文件，但当前没有可用的 file_write 能力；"
                "本次不会向工作区写入任何文件，也不会有可交付的产物路径。"
            )
        if (
            any(marker in text for marker in self._NET_INTENT_MARKERS)
            and "network_bus" not in available
        ):
            notices.append(
                "派活前提示：目标需要联网检索，但当前没有可用的 network_bus 能力；"
                "本次拿不到真实来源，结论不会附任何链接。"
            )
        return notices

    def _failed_evaluation(
        self,
        goal_id: str,
        correlation_id: str,
        summary: str,
        error: str,
    ) -> EvaluationResult:
        """Build an honest terminal evaluation for runs that died early.

        Without this, a model outage left ``evaluation=None``: the page said
        "未完成" with no reason and no evidence-only summary. The summary is
        generated upstream from the error class and never names a file.
        """
        return EvaluationResult(
            evaluation_id=str(uuid.uuid4()),
            goal_id=goal_id,
            task_id=None,
            outcome=EvaluationOutcome.FAILED,
            overall_score=0.0,
            criteria_results=[],
            feedback=str(error or ""),
            feedback_type=FeedbackType.CRITICAL,
            summary=summary,
            correlation_id=correlation_id,
            metadata={"terminal": "failed", "reason": "model_or_runtime_failure"},
        )

    def request_stop(self, goal_id: str) -> bool:
        """Signal a running goal to abort (cooperative cancellation).

        Returns ``True`` if a live execution for ``goal_id`` was found and its
        stop flag set; ``False`` if the goal is not currently running (already
        finished, or never started). The execution loop observes the flag and
        terminates as CANCELLED.
        """
        event = self._stop_events.get(goal_id)
        if event is None:
            return False
        event.set()
        return True

    # ------------------------------------------------------------------ #
    # Failure recovery hooks
    # ------------------------------------------------------------------ #
    def replan(
        self,
        prior: AgentRunResult,
        *,
        extra_criteria: Optional[Dict[str, Any]] = None,
        max_replans: Optional[int] = None,
        goal_text: Optional[str] = None,
    ) -> AgentRunResult:
        """Re-run a goal after a failed/partial run (fills the engine's L811 no-op).

        Idempotent: ``ExecutionEngine`` resumes already-completed tasks by name
        via its journal (``src/kernels/execution/__init__.py:714``), so side
        effects are not repeated. Bounded by ``max_replans`` to avoid loops.

        ``goal_text`` lets callers (e.g. the gateway, whose stored ``prior`` has
        no live ``context``) supply the original goal text; it falls back to
        ``prior.context.goal.natural_language`` when available.
        """
        # A caller-supplied bound must be honoured verbatim. ``None`` is the
        # sentinel for "caller did not set it" and falls back to the instance
        # default; any explicit integer -- crucially ``0`` -- is respected.
        #
        # The previous form was ``max_replans or self.max_replans``, which
        # treated an explicit ``0`` as falsy and silently substituted the
        # (permissive) instance default. A caller that passed ``max_replans=0``
        # to FORBID re-execution instead got ``self.max_replans`` runs, so the
        # very control that bounds autonomous replanning failed open -- the
        # "the bound is not the bound you asked for" class this hook exists to
        # prevent.
        effective_max_replans = self.max_replans if max_replans is None else max_replans
        if prior.replan_count >= effective_max_replans:
            return prior
        if goal_text is None:
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

    def _replan_for_evaluator(self, goal_id: str) -> AgentRunResult:
        """Callback the evaluation kernel uses to actually re-run a goal.

        Looks up the original goal text recorded in ``run_goal`` and re-executes
        it (journal-driven resume makes this idempotent). Returns a FAILED
        result — which makes ``Evaluator.execute_replan`` report ``False``
        honestly — when this runtime has no record of the goal.
        """
        text = self._goal_texts.get(goal_id)
        if text is None:
            return AgentRunResult(
                goal_id=goal_id,
                correlation_id="",
                state=AgentRunState.FAILED,
                context=None,
                evaluation=None,
                trace=ExecutionTrace(correlation_id=""),
                error="goal text unavailable for replan (not executed by this runtime)",
            )
        return self.run_goal(text, goal_id=goal_id, persist=True)

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
        self,
        goal: Goal,
        ctx: ExecutionContext,
        correlation_id: str,
        pinned_agent: Optional[str] = None,
        preflight_notices: Optional[Sequence[str]] = None,
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
            evaluation = self.evaluator.evaluate(
                intended, actual, scope=goal.scope, correlation_id=correlation_id
            )
        except Exception:
            return None
        self._attach_summary(
            goal,
            ctx,
            evaluation,
            pinned_agent=pinned_agent,
            preflight_notices=preflight_notices,
        )
        return evaluation

    def _attach_summary(
        self,
        goal: Goal,
        ctx: ExecutionContext,
        evaluation: Optional[EvaluationResult],
        pinned_agent: Optional[str] = None,
        preflight_notices: Optional[Sequence[str]] = None,
    ) -> None:
        """Synthesize the user-facing conclusion from REAL task outputs.

        The evaluator scores criteria; it does not write prose. Previously
        ``EvaluationResult`` had no ``summary`` field at all, so the gateway
        serialized ``""`` and the UI could only count tasks. Here the summary is
        produced from the actual per-task outputs (model first, deterministic
        evidence-only fallback), and the outcome is reconciled with the real
        task result set so a failed task can never be labelled SUCCESS.
        """
        if evaluation is None or getattr(ctx, "plan", None) is None:
            return
        resolver = getattr(self, "agent_employee_resolver", None)
        records: List[Dict[str, Any]] = []
        for task in ctx.plan.tasks:
            result = ctx.task_results.get(task.id)
            agent_id = getattr(task, "assigned_agent", None)
            employee = _record_employee(task, resolver, pinned_agent)
            reason = getattr(task, "assignment_reason", None)
            if pinned_agent:
                # 用户点名指派：理由是可核对的事实，不需要模型编；也不能
                # 沿用拆解器给"它原本想派的人"写的理由（会张冠李戴）。
                reason = "用户点名指派"
            output = result.output if result is not None else None
            produced, artifacts = task_output_facts(output)
            # 把"是否真有产出"钉在任务上，网关序列化时照原样上屏，
            # 避免 100% 进度 + 没有产物 的自相矛盾。
            task.produced = produced
            task.artifacts = list(artifacts)
            records.append(
                {
                    "name": task.name,
                    "capability": task.capability_id,
                    "status": getattr(task.status, "value", str(task.status)),
                    "result": output,
                    "error": task.error,
                    "assigned_agent": agent_id,
                    "assigned_employee": employee,
                    "assignment_reason": reason,
                    "produced": produced,
                    "artifacts": list(artifacts),
                }
            )
        try:
            summary = compose_execution_summary(
                goal.natural_language, records, self._model_generator
            )
            notices = [str(n) for n in (preflight_notices or []) if str(n).strip()]
            if notices:
                # 边界提示永远排在最前面：它是"派活时"就说过的话，
                # 不能埋在结论后面变成事后解释。
                summary = "\n".join(notices) + ("\n\n" + summary if summary else "")
            evaluation.summary = summary
        except Exception:
            # Summary generation must never take down a completed goal.
            evaluation.summary = ""
        if preflight_notices:
            evaluation.metadata = dict(evaluation.metadata or {})
            evaluation.metadata["preflight_notices"] = list(preflight_notices)
        if ctx.failed_tasks:
            completed_count = len(ctx.completed_tasks)
            evaluation.outcome = (
                EvaluationOutcome.PARTIAL if completed_count else EvaluationOutcome.FAILED
            )
            evaluation.metadata = dict(evaluation.metadata or {})
            evaluation.metadata["failed_task_count"] = len(ctx.failed_tasks)
            evaluation.metadata["completed_task_count"] = completed_count

    def _needs_replan(
        self, ctx: ExecutionContext, evaluation: Optional[EvaluationResult]
    ) -> bool:
        if any(v.replan_required for v in ctx.verification_results.values()):
            return True
        if evaluation is not None and evaluation.replan_triggered and ctx.failed_tasks:
            return True
        return False

    def _check_learning_blockers(self, goal: Goal, scope: str) -> Optional[str]:
        """Check if any learned directives block this goal.

        This is the READ side of memory — without this, memory is write-only
        and the agent cannot learn from experience. We scan semantic/learning
        entries for directives that constrain what the agent may do.

        Returns a blocker reason string if the goal should be refused,
        or ``None`` if it may proceed.
        """
        try:
            mem_scope = MemoryScope(scope)
        except Exception:
            return None  # invalid scope => no blocking (fail-open for malformed input)

        # Collect all semantic/learning memories at this scope and higher.
        try:
            all_entries = self.memory.scope_filter(mem_scope)
        except Exception:
            return None  # memory unavailable => don't block (availability)

        learning_entries = [
            e for e in all_entries
            if "learning" in e.tags and e.tier == MemoryTier.LONG_TERM
        ]

        if not learning_entries:
            return None

        goal_lower = goal.natural_language.lower()

        for entry in learning_entries:
            value = entry.value if isinstance(entry.value, dict) else {}
            directive = value.get("directive", "")
            if not directive:
                continue

            directive_lower = directive.lower()

            # --- Recognised directive patterns ---
            # "NEVER write files" / "refuse every write"
            if ("never" in directive_lower and "write" in directive_lower
                    and ("file" in directive_lower or "write" in directive_lower)):
                # Check if this goal involves file writing
                file_keywords = ("write", "create", "save", "make", "generate", "file")
                if any(kw in goal_lower for kw in file_keywords):
                    # Also check for a filename pattern (e.g. "report.txt")
                    import re
                    if re.search(r"[\w./\\-]+\.[A-Za-z0-9]+", goal_lower):
                        return f"learned directive prohibits file writes: {directive}"

            # "NEVER run code" / "refuse execution"
            if ("never" in directive_lower and ("code" in directive_lower
                                                or "execute" in directive_lower
                                                or "run" in directive_lower)):
                code_keywords = ("run", "execute", "code", "python", "compute", "calculate")
                if any(kw in goal_lower for kw in code_keywords):
                    return f"learned directive prohibits code execution: {directive}"

        return None

    def _persist(
        self,
        goal: Goal,
        ctx: ExecutionContext,
        evaluation: Optional[EvaluationResult],
        correlation_id: str,
    ) -> Tuple[List[str], List[str]]:
        """Persist run memory. Returns ``(written_keys, errors)``.

        Honesty contract (alignment / intent-preservation): a memory write that
        fails must NEVER be swallowed into silence. Each failure is captured in
        ``errors`` so the caller (``run_goal``) can surface it on the
        ``AgentRunResult``. The run still completes -- persistence must not crash
        a run (availability) -- but a consumer reading the result can tell that
        the agent did NOT actually remember this run, instead of believing a
        silent, lossy "completed". ``written_keys`` holds only keys truly stored.
        """
        written: List[str] = []
        errors: List[str] = []
        try:
            scope = MemoryScope(goal.scope)
        except Exception as exc:
            errors.append(f"memory scope invalid for goal {goal.id}: {exc!r}")
            return written, errors
        try:
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
            written.append(key)
        except Exception as exc:
            errors.append(f"episodic store failed for goal {goal.id}: {exc!r}")

        if evaluation is not None and getattr(evaluation, "replan_triggered", False):
            try:
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
                written.append(k2)
            except Exception as exc:
                errors.append(f"learning store failed for goal {goal.id}: {exc!r}")
        return written, errors


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
