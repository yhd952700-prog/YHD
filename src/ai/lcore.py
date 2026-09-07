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
from typing import Any, Callable, Dict, Optional

from ..kernels.execution import (
    Goal,
    ExecutionPlan,
    GoalDecomposer,
    PlanBuilder,
    Verifier,
    ActionResult,
)
from ..kernels.context import create_context_kernel, ContextInput, ContextInputType
from .tool_registry import ToolRegistry, ToolRouter


class LCore:
    """Human-facing orchestrator (MASTER-SPEC §19-21)."""

    def __init__(
        self,
        tools: Optional[ToolRegistry] = None,
        scope: str = "L1",
        authorize: Optional[Callable[[Goal, ExecutionPlan], bool]] = None,
    ) -> None:
        self.scope = scope
        self.tools = tools or ToolRegistry()
        self.router = ToolRouter(self.tools)
        self.context_kernel = create_context_kernel(scope=scope)
        self.decomposer = GoalDecomposer()
        self.planner = PlanBuilder()
        self.verifier = Verifier()
        # Optional plan authorizer (policy gate). None = human-sovereignty
        # default allow (L-Core is the human's primary interface).
        self.authorize = authorize

    def register_tool(self, tool) -> str:
        """Register a tool and walk it to ACTIVE (validate -> approve -> activate)."""
        tool_id = self.tools.register(tool)
        self.tools.validate(tool_id)
        self.tools.approve(tool_id)
        self.tools.activate(tool_id)
        return tool_id

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
        verified = all(
            v.result.value in ("success", "partial") for v in verifications.values()
        )
        status = "completed" if (succeeded and verified) else "failed"
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
