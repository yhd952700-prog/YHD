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
    ExecutionJournal,
    IDEMPOTENCY_UNKNOWN_MESSAGE,
    capability_is_reentrant,
    evaluate_ledger_gate,
    ledger_unavailable_error,
    make_action_id,
    replay_from_journal,
    settle_idempotency,
)
from ..kernels.context import create_context_kernel, ContextInput, ContextInputType
from ..kernels.event import publish_event, EventScope, EventPriority
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
        journal: Optional[ExecutionJournal] = None,
    ) -> None:
        self.scope = scope
        self.tools = tools or ToolRegistry()
        self.router = ToolRouter(self.tools)
        # 幂等账本：与 ExecutionEngine 同一道闸门（``evaluate_ledger_gate``）。
        # None = 没接账本，``handle_intent`` 直接路由工具，所以必须在这里也
        # fail-closed 硬副作用，否则就绕过了执行内核的拒绝逻辑。
        self.journal = journal
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
        action_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Run the full §21 pipeline for a single human intent.

        ``action_id``（调用方给的稳定幂等键）
        ----------------------------------
        传入时，它被当作这次意图的稳定标识：goal id 直接用它，每个任务的账本
        键由 ``make_action_id(goal_id, task_name)`` 派生（与 ExecutionEngine 同一
        个函数）。于是「同一意图被投递两次 / 重试 / 重启后续跑」会命中同一个
        占坑：第二次不再执行副作用，而是回放首次结果（输出带 ``replayed_from``）。

        调用方只在**确认是同一个意图**时才复用同一个值；两次真实不同的请求
        必须给不同值，否则会被当成重复而只执行一次（这是幂等键的定义，不是
        缺陷）。

        ``action_id`` 缺省（``None``）时行为与以前一致：每次生成新的 goal id，
        **不复用、不占坑**，因此跨调用没有重复保护（只保留了「账本缺席时硬
        副作用拒绝」这一道静态闸门）。脚本/演示入口属于这一类，若要重复保护
        请显式传 ``action_id``。
        """
        # 1. Context
        self.context_kernel.add_input(ContextInput(
            type=ContextInputType.GOAL,
            source="lcore",
            data={"intent": intent},
            scope=self.scope,
        ))

        # 2. Goal
        # 有调用方给的稳定键时，goal id 由它决定（= 跨调用幂等的前提）；
        # 否则保持旧行为：随机 id、不复用。
        stable_key = (action_id or "").strip()
        goal = Goal(
            id=stable_key or str(uuid.uuid4())[:8],
            natural_language=intent,
            scope=self.scope,
        )

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
        # 与 ExecutionEngine 共用同一道闸门：账本缺席时硬副作用拒绝、只读放行，
        # 否则 ``router.execute`` 就是绕过执行内核的那扇常开的门。
        results: Dict[str, ActionResult] = {}
        for task in plan.tasks:
            gate = evaluate_ledger_gate(
                task.capability_id, journal_available=self.journal is not None
            )
            if not gate.allowed:
                results[task.id] = self._refused_result(
                    goal, task, gate.status, gate.error
                )
                continue

            idem_key = self._idempotency_key(goal, task, reuse=bool(stable_key))
            if idem_key is not None:
                try:
                    claimed = self.journal.claim_idempotency(
                        idem_key, goal.id, task_name=task.name
                    )
                except Exception as exc:
                    # 账本写不进去：与内核一致，不可重入动作 fail-closed。
                    self._log.warning(
                        "evt=idempotency_ledger_write_failed entry=lcore "
                        "capability=%s task_id=%s error=%s",
                        task.capability_id,
                        task.id,
                        exc,
                    )
                    results[task.id] = self._refused_result(
                        goal,
                        task,
                        "idempotency_ledger_unavailable",
                        ledger_unavailable_error(task.capability_id),
                    )
                    continue
                if not claimed:
                    results[task.id] = self._replayed_result(goal, task, idem_key)
                    continue

            result = self.router.execute(task.capability_id, task.inputs)
            settle_idempotency(
                self.journal,
                idem_key,
                ok=bool(result.success),
                output=result.output,
                raw=result.output if isinstance(result.output, dict) else None,
            )
            results[task.id] = result

        # 6. Verify
        verifications = {}
        for task in plan.tasks:
            verifications[task.id] = self.verifier.verify(
                task, results[task.id], verification_criteria
            )

        # 7. Response
        return self._synthesize(goal, plan, results, verifications)

    def _idempotency_key(self, goal: Goal, task, *, reuse: bool) -> Optional[str]:
        """任务的稳定账本键；``None`` = 这条任务不走占坑/回放。

        只对不可重入能力建键（与内核的 ``_action_idempotency_key`` 同口径）：
        给天然幂等能力建键只会把一次无害重试误报成「重放」。``reuse`` 为 False
        表示调用方没给稳定 ``action_id``，此时派生出来的键每次都不同，建了也
        没有意义，只是往账本里堆垃圾行。
        """
        if self.journal is None or not reuse:
            return None
        if capability_is_reentrant(task.capability_id):
            return None
        return make_action_id(goal.id, task.name)

    def _refused_result(self, goal: Goal, task, status: str, error: str) -> ActionResult:
        """执行前被闸门拒绝：发布事件并返回失败结果（不发成功、不留白）。"""
        self._log.warning(
            "evt=idempotency_hard_side_effect_refused entry=lcore "
            "capability=%s task_id=%s status=%s",
            task.capability_id,
            task.id,
            status,
        )
        publish_event(
            type="action_failed",
            source="lcore",
            data={
                "task_id": task.id,
                "capability_id": task.capability_id,
                "status": status,
                "error": error,
            },
            correlation_id=goal.correlation_id,
            scope=EventScope(self.scope),
            priority=EventPriority.HIGH,
        )
        return ActionResult(
            action_id="",
            success=False,
            output={
                "success": False,
                "status": status,
                "executed": False,
                "capability": task.capability_id,
            },
            error=error,
        )

    def _replayed_result(self, goal: Goal, task, idem_key: str) -> ActionResult:
        """已占坑：回放首次结果，绝不再执行一次副作用。"""
        output, unknown = replay_from_journal(self.journal, idem_key)
        if unknown:
            publish_event(
                type="action_replayed_unknown",
                source="lcore",
                data={"action_id": idem_key, "task_id": task.id},
                correlation_id=goal.correlation_id,
                scope=EventScope(self.scope),
                priority=EventPriority.HIGH,
            )
            return ActionResult(
                action_id=idem_key,
                success=False,
                output={
                    "success": False,
                    "status": "replayed_unknown",
                    "replayed_from": idem_key,
                    "capability": task.capability_id,
                },
                error=IDEMPOTENCY_UNKNOWN_MESSAGE,
                replayed=True,
            )
        output["replayed_from"] = idem_key
        output.setdefault("success", True)
        output["replayed"] = True
        publish_event(
            type="action_replayed",
            source="lcore",
            data={"action_id": idem_key, "task_id": task.id},
            correlation_id=goal.correlation_id,
            scope=EventScope(self.scope),
        )
        return ActionResult(
            action_id=idem_key,
            success=bool(output.get("success", True)),
            output=output,
            replayed=True,
        )

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
                tid: {
                    "success": r.success,
                    "output": r.output,
                    "error": r.error,
                    # 重复投递被账本拦下时这里为 True，界面上必须能区分
                    # 「复用首次结果」和「这次真的执行了」。
                    "replayed": bool(getattr(r, "replayed", False)),
                }
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
