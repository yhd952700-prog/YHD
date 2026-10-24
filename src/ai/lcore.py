"""
L-Core — MASTER-SPEC Phase 9 / §19-21.

The human-facing intelligence interface. Implements the §21 L-CORE CONTRACT:

    Intent -> Context -> Goal -> Plan -> Policy -> Execution -> Verify -> Response

It reuses the Execution Kernel's public building blocks (``Goal``,
``GoalDecomposer``, ``PlanBuilder``, ``Verifier``, ``ActionResult``) and the
Context Kernel's typed inputs, and routes *real* tool execution through
``ToolRouter`` — closing the gap left by the Execution Kernel's simulated
``ActionExecutor``.
"""

from __future__ import annotations

import uuid
from typing import Any, Callable, Dict, Optional, Set

from ..kernels.execution import (
    Goal,
    ExecutionPlan,
    GoalDecomposer,
    PlanBuilder,
    Verifier,
    ActionResult,
    VerifyResult,
)
from ..kernels.context import create_context_kernel, ContextInput, ContextInputType
from .tool_registry import ToolRegistry, ToolRouter, ToolStatus
from .observability import observe, get_logger


class LCore:
    """Human-facing orchestrator (MASTER-SPEC §19-21)."""

    def __init__(
        self,
        tools: Optional[ToolRegistry] = None,
        scope: str = "L1",
        authorize: Optional[Callable[[Goal, ExecutionPlan], bool]] = None,
        register_local_tools: bool = False,
    ) -> None:
        self.scope = scope
        self.tools = tools or ToolRegistry()
        self.router = ToolRouter(self.tools)
        # Opt-in: wire the real, locally-executable tools (RestrictedPython-backed
        # python_compute, etc.) into this Core's registry. Off by default so existing
        # callers/tests that depend on an empty registry keep behaving as before.
        if register_local_tools:
            from .tools_local import register_default_local_tools
            register_default_local_tools(self.tools)
        self.context_kernel = create_context_kernel(scope=scope)
        self.decomposer = GoalDecomposer()
        self.planner = PlanBuilder()
        self.verifier = Verifier()
        # Optional plan authorizer (policy gate). None = human-sovereignty
        # default allow (L-Core is the human's primary interface).
        self.authorize = authorize
        self._log = get_logger("lcore")

    def register_tool(self, tool) -> str:
        """Register a tool and walk it to ACTIVE (validate -> approve -> activate)."""
        tool_id = self.tools.register(tool)
        self.tools.validate(tool_id)
        self.tools.approve(tool_id)
        self.tools.activate(tool_id)
        return tool_id

    def capability_executor(self) -> Callable[[str, Dict[str, Any]], Any]:
        """Bridge this Core's tool registry into the Execution Kernel.

        The Execution Kernel cannot import this layer (strict layering), so the
        junction is an injected callable. Wiring both subsystems to one registry:

            engine = ExecutionEngine(capability_executor=lcore.capability_executor())

        Without this, ``ExecutionEngine`` keeps taking its simulated path.
        """
        return self.router.as_capability_executor()

    def executable_capabilities(self) -> Callable[[], Set[str]]:
        """Zero-arg provider of the capability ids this Core can really execute.

        Deliberately a *provider*, not a snapshot: tools can be registered after
        wiring, and a frozen set taken too early would reject work that later
        became executable. Intended for the planner contract::

            AgentRuntime(capability_executor=lcore.capability_executor(),
                         executable_capabilities=lcore.executable_capabilities())

        so a goal is rejected at PLAN time when it needs a capability that has
        no executor, instead of running part of the work and failing afterwards.
        """
        def _provide() -> Set[str]:
            try:
                # The capability namespace the planner emits (e.g. ``file_write``,
                # ``python_compute``) is the tool's ``capability`` field, NOT its
                # ``tool_id`` (``local_file_write`` / ``local_python_compute``).
                # The router routes by ``capability``, so the contract must check
                # against the same namespace -- otherwise the two real tools
                # would be rejected as "missing". Only ACTIVE tools are
                # executable (a SUSPENDED/REVOKED tool must not count as
                # available), matching ``ToolRouter.route``.
                return {
                    t.capability
                    for t in self.tools._tools.values()
                    if self.tools._status.get(t.tool_id) == ToolStatus.ACTIVE
                }
            except Exception:
                # A broken introspection must not be read as "nothing is
                # executable" (that would reject every goal). Fail open on
                # introspection only; the real executor still fails closed at
                # execution time.
                return set()
        return _provide

    @observe("LCore.handle_intent")
    def handle_intent(
        self,
        intent: str,
        verification_criteria: Optional[Dict[str, Any]] = None,
        plan_mode: str = "parallel",
    ) -> Dict[str, Any]:
        """Run the full §21 pipeline for a single human intent."""
        # 1. Context
        self.context_kernel.add_input(ContextInput(
            type=ContextInputType.GOAL,
            source="lcore",
            data={"intent": intent},
            scope=self.scope,
        ))

        # 2. Goal
        goal = Goal(id=str(uuid.uuid4())[:8], natural_language=intent, scope=self.scope)

        # 3. Plan
        tasks = self.decomposer.decompose(goal)
        plan = self.planner.build(goal, tasks, mode=plan_mode)

        # 4. Policy
        if not self._authorize(goal, plan):
            return {
                "status": "policy_denied",
                "goal": goal.natural_language,
                "plan_id": plan.id,
                "task_count": len(plan.tasks),
                "results": {},
                "verifications": {},
            }

        # 5. Execution — route each task's capability to a real tool.
        results: Dict[str, ActionResult] = {}
        for task in plan.tasks:
            results[task.id] = self.router.execute(task.capability_id, task.inputs)

        # 6. Verify
        verifications = {}
        for task in plan.tasks:
            verifications[task.id] = self.verifier.verify(
                task, results[task.id], verification_criteria
            )

        # 7. Response
        return self._synthesize(goal, plan, results, verifications)

    def _authorize(self, goal: Goal, plan: ExecutionPlan) -> bool:
        if self.authorize is None:
            return True  # human-sovereignty default allow
        return self.authorize(goal, plan)

    @staticmethod
    def _synthesize(goal, plan, results, verifications) -> Dict[str, Any]:
        succeeded = all(r.success for r in results.values())
        # ``UNVERIFIED`` means "the action ran and reported success, but nothing
        # here PROVES it" -- a first-class honesty state, NOT a failure (see
        # ``VerifyResult`` and ``Verifier.verify``, which deliberately does not
        # fail a task when no criteria exist). Treating it as failure reported
        # every default L-Core intent (no verification criteria) as ``failed``
        # even though the tools really ran and succeeded. Only the states that
        # say the outcome is wrong block completion; the verifications payload
        # still surfaces ``unverified`` so the gap stays visible.
        _blocking = (
            VerifyResult.FAILED,
            VerifyResult.OFF_TRACK,
            VerifyResult.NEEDS_HUMAN,
        )
        no_failed_verification = all(
            v.result not in _blocking for v in verifications.values()
        )
        status = "completed" if (succeeded and no_failed_verification) else "failed"
        return {
            "status": status,
            "goal": goal.natural_language,
            "plan_id": plan.id,
            "task_count": len(plan.tasks),
            "results": {
                tid: {"success": r.success, "output": r.output, "error": r.error}
                for tid, r in results.items()
            },
            "verifications": {
                tid: {"result": v.result.value, "score": v.score}
                for tid, v in verifications.items()
            },
            "response": [
                r.output for r in results.values() if r.success
            ],
        }
