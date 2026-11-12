"""自动化会话：计划 → 确认 → 执行 → 校验 → 审计的闭环。

会话只持有后端、守卫、票据、审计与急停句柄；任何环节失败都必须给出可解释
原因，并且审计写失败会让动作直接失败（fail-closed）。
"""

from __future__ import annotations

import secrets
import threading
import time
from typing import Dict, Optional, Tuple

from .audit import AutomationAudit
from .confirm import SqliteConfirmationBroker
from .contracts import (
    ActionCapability,
    ActionRequest,
    ActionResult,
    AutomationError,
    ConfirmationTicket,
    Observation,
    Reversibility,
    SessionStatus,
    TargetScope,
    TicketConsumeResult,
)
from .estop import EmergencyStop
from .guard import Guard


class AutomationSession:
    """一个主体的自动化会话。串行执行，避免动作交错。"""

    def __init__(
        self,
        *,
        principal: str,
        principal_kind: str,
        backend: object,
        guard: Guard,
        broker: SqliteConfirmationBroker,
        audit: AutomationAudit,
        estop: EmergencyStop,
        session_id: Optional[str] = None,
    ) -> None:
        self.session_id = session_id or secrets.token_urlsafe(18)
        self.principal = str(principal)
        self.principal_kind = str(principal_kind or "unknown").strip().lower()
        self.backend = backend
        self.guard = guard
        self.broker = broker
        self.audit = audit
        self.estop = estop
        self.created_at = time.time()
        self.closed_at: Optional[float] = None
        self.freeze_reason: str = ""
        self._step_no = 0
        self._closed = False
        self._frozen = False
        self._lock = threading.RLock()
        # 已执行步骤 → (计划, 结果)，仅用于回滚校验。
        self._executed: Dict[int, Tuple[ActionRequest, ActionResult]] = {}
        self._rolled_back: set = set()

    # ---------------------------------------------------------------- 状态
    @property
    def status(self) -> SessionStatus:
        if self._closed:
            return SessionStatus.CLOSED
        if self._frozen or self.guard.tripped or self.estop.active:
            return SessionStatus.FROZEN
        return SessionStatus.OPEN

    @property
    def step_no(self) -> int:
        return self._step_no

    def status_dict(self) -> Dict[str, object]:
        return {
            "session_id": self.session_id,
            "principal": self.principal,
            "status": self.status.value,
            "step_no": self._step_no,
            "backend": getattr(self.backend, "kind", None).value
            if getattr(self.backend, "kind", None)
            else None,
            "consecutive_failures": self.guard.consecutive_failures,
            "freeze_reason": self.freeze_reason,
        }

    def close(self, reason: str = "") -> bool:
        with self._lock:
            if self._closed:
                return False
            self._closed = True
            self.closed_at = time.time()
            self.freeze_reason = str(reason)
            return True

    def freeze(self, reason: str) -> None:
        with self._lock:
            self._frozen = True
            self.freeze_reason = str(reason)

    # ---------------------------------------------------------------- 计划
    def plan(
        self,
        action: str,
        *,
        target: TargetScope = TargetScope.SCREEN,
        target_ref: str = "",
        params: Optional[Dict[str, object]] = None,
        expected_focus: Optional[str] = None,
    ) -> ActionRequest:
        with self._lock:
            if self.status is not SessionStatus.OPEN:
                raise AutomationError(
                    "session_not_open",
                    f"会话状态为 {self.status.value}，不能创建计划",
                )
            return ActionRequest(
                session_id=self.session_id,
                step_no=self._step_no + 1,
                action=str(action),
                target=target,
                target_ref=str(target_ref or ""),
                params=dict(params or {}),
                expected_focus=expected_focus,
            )

    def request_confirmation(
        self,
        plan: ActionRequest,
        *,
        preview: Optional[str] = None,
        ttl_seconds: float = 30.0,
    ) -> ConfirmationTicket:
        if plan.session_id != self.session_id:
            raise AutomationError("plan_session_mismatch")
        capability = self._capability(plan)
        text = preview if preview is not None else _default_preview(plan)
        if capability.reversibility is Reversibility.IRREVERSIBLE:
            # 不可逆动作必须在执行前告知人类，并且理由随票据一起留痕。
            text = (
                f"{text} [不可逆动作: {capability.reason or 'no_reason'}] "
                "执行后无法回滚"
            )
        return self.broker.issue(
            principal=self.principal,
            principal_kind=self.principal_kind,
            session_id=self.session_id,
            step_no=plan.step_no,
            action_fingerprint=plan.fingerprint(),
            preview=text,
            ttl_seconds=ttl_seconds,
        )

    def describe(self, plan: ActionRequest) -> Dict[str, object]:
        """执行前能力说明：是否可逆、为什么。"""

        if plan.session_id != self.session_id:
            raise AutomationError("plan_session_mismatch")
        return self._capability(plan).to_dict()

    # ---------------------------------------------------------------- 执行
    def execute(self, plan: ActionRequest, ticket: ConfirmationTicket) -> ActionResult:
        with self._lock:
            if plan.session_id != self.session_id:
                return self._reject(plan, "plan_session_mismatch")
            if self.status is not SessionStatus.OPEN:
                return self._reject(plan, f"session_{self.status.value}")
            if plan.step_no != self._step_no + 1:
                return self._reject(plan, "step_out_of_order")

            observation = self.backend.observe(plan.target)
            decision = self.guard.check(plan, observation)
            if not decision.allowed:
                return self._reject(
                    plan,
                    f"guard_{decision.reason}",
                    observation,
                    message=decision.message,
                    detail=dict(decision.detail),
                )

            consumed: TicketConsumeResult = self.broker.consume(
                ticket=ticket.ticket,
                principal=self.principal,
                session_id=self.session_id,
                step_no=plan.step_no,
                action_fingerprint=plan.fingerprint(),
            )
            if not consumed.ok:
                return self._reject(plan, f"ticket_{consumed.reason}", observation)

            self.guard.record_attempt()
            capability = self._capability(plan)
            # 执行前审计：写不进去就不执行（fail-closed）。
            self.audit.record_action(
                principal=self.principal,
                session_id=self.session_id,
                step_no=plan.step_no,
                action=plan.action,
                target=plan.target.value,
                target_ref=plan.target_ref,
                expected_focus=plan.expected_focus,
                params=plan.params,
                fingerprint=plan.fingerprint(),
                outcome="allow",
                stage="intent",
                reversibility=capability.reversibility.value,
            )

            result = self.backend.execute(plan)
            if result.ok:
                self.guard.record_success()
            else:
                self.guard.record_failure()
                if self.guard.tripped:
                    self.freeze_reason = "consecutive_failures"

            post = result.observation or observation
            self.audit.record_action(
                principal=self.principal,
                session_id=self.session_id,
                step_no=plan.step_no,
                action=plan.action,
                target=plan.target.value,
                target_ref=plan.target_ref,
                expected_focus=plan.expected_focus,
                params=plan.params,
                fingerprint=plan.fingerprint(),
                outcome="allow" if result.ok else "failed",
                stage="result",
                error=result.error,
                reversibility=capability.reversibility.value,
            )
            self._step_no = plan.step_no
            self._executed[plan.step_no] = (plan, result)
            result.observation = post
            result.detail.setdefault("reversibility", capability.reversibility.value)
            if not capability.reversible:
                result.detail["irreversible_reason"] = capability.reason
            return result

    def verify(self, plan: ActionRequest, result: ActionResult) -> bool:
        """执行后校验：结果必须成功且焦点符合预期。"""

        if not result.ok:
            return False
        observation: Optional[Observation] = result.observation
        if observation is None:
            observation = self.backend.observe(plan.target)
        if observation is None:
            return False
        if plan.expected_focus is not None:
            return observation.focus == plan.expected_focus
        return True

    # ---------------------------------------------------------------- 回滚
    def request_rollback_confirmation(
        self,
        plan: ActionRequest,
        result: ActionResult,
        *,
        preview: Optional[str] = None,
        ttl_seconds: float = 30.0,
    ) -> ConfirmationTicket:
        """为一次已执行的可逆动作申请回滚票据（新票据，不复用执行票据）。"""

        with self._lock:
            rollback_plan = self._rollback_plan(plan, result)
            text = preview if preview is not None else _default_rollback_preview(plan)
            return self.broker.issue(
                principal=self.principal,
                principal_kind=self.principal_kind,
                session_id=self.session_id,
                step_no=plan.step_no,
                action_fingerprint=rollback_plan.fingerprint(),
                preview=text,
                ttl_seconds=ttl_seconds,
            )

    def rollback(
        self,
        plan: ActionRequest,
        result: ActionResult,
        ticket: ConfirmationTicket,
    ) -> ActionResult:
        """执行回滚。失败时如实在 ``detail`` 报"回滚未完成"，绝不吞掉。"""

        with self._lock:
            try:
                rollback_plan = self._rollback_plan(plan, result)
            except AutomationError as exc:
                return self._rollback_reject(plan, exc.reason, str(exc))

            consumed: TicketConsumeResult = self.broker.consume(
                ticket=ticket.ticket,
                principal=self.principal,
                session_id=self.session_id,
                step_no=plan.step_no,
                action_fingerprint=rollback_plan.fingerprint(),
            )
            if not consumed.ok:
                return self._rollback_reject(
                    plan,
                    f"ticket_{consumed.reason}",
                    f"回滚票据无效: {consumed.reason}",
                )

            backend_rollback = getattr(self.backend, "rollback", None)
            if backend_rollback is None:
                return self._rollback_reject(
                    plan,
                    "rollback_unsupported_by_backend",
                    "后端没有回滚能力",
                )

            self.audit.record_action(
                principal=self.principal,
                session_id=self.session_id,
                step_no=plan.step_no,
                action=rollback_plan.action,
                target=plan.target.value,
                target_ref=plan.target_ref,
                expected_focus=plan.expected_focus,
                params=rollback_plan.params,
                fingerprint=rollback_plan.fingerprint(),
                outcome="allow",
                stage="rollback_intent",
                reversibility=Reversibility.REVERSIBLE.value,
            )

            try:
                rollback_result = backend_rollback(request=plan, result=result)
            except Exception as exc:  # noqa: BLE001 - 必须如实上报，不能吞
                self.audit.record_action(
                    principal=self.principal,
                    session_id=self.session_id,
                    step_no=plan.step_no,
                    action=rollback_plan.action,
                    target=plan.target.value,
                    target_ref=plan.target_ref,
                    expected_focus=plan.expected_focus,
                    params=rollback_plan.params,
                    fingerprint=rollback_plan.fingerprint(),
                    outcome="failed",
                    stage="rollback_result",
                    error=f"rollback_backend_exception:{type(exc).__name__}",
                    reversibility=Reversibility.REVERSIBLE.value,
                )
                return self._rollback_incomplete(
                    plan,
                    rollback_plan,
                    reason="rollback_backend_exception",
                    message=f"回滚未完成：{type(exc).__name__}",
                )

            ok = bool(getattr(rollback_result, "ok", False))
            error = getattr(rollback_result, "error", None) or (
                None if ok else "rollback_incomplete"
            )
            self.audit.record_action(
                principal=self.principal,
                session_id=self.session_id,
                step_no=plan.step_no,
                action=rollback_plan.action,
                target=plan.target.value,
                target_ref=plan.target_ref,
                expected_focus=plan.expected_focus,
                params=rollback_plan.params,
                fingerprint=rollback_plan.fingerprint(),
                outcome="allow" if ok else "failed",
                stage="rollback_result",
                error=error,
                reversibility=Reversibility.REVERSIBLE.value,
            )
            if not ok:
                return self._rollback_incomplete(
                    plan,
                    rollback_plan,
                    reason=str(error),
                    message=f"回滚未完成：{error}",
                    observation=getattr(rollback_result, "observation", None),
                )

            self._rolled_back.add(plan.step_no)
            return ActionResult(
                status="executed",
                action=rollback_plan.action,
                ok=True,
                observation=getattr(rollback_result, "observation", None),
                detail={
                    "rollback_completed": True,
                    "message": "回滚已完成",
                    "of_step": plan.step_no,
                },
            )

    # ---------------------------------------------------------------- 内部
    def _capability(self, plan: ActionRequest) -> ActionCapability:
        declare = getattr(self.backend, "capability", None)
        if declare is None:
            # 后端未声明能力时按不可逆处理：宁可不回滚，也不假装能撤。
            return ActionCapability(
                action=str(plan.action),
                reversibility=Reversibility.IRREVERSIBLE,
                reason="capability_unavailable",
            )
        return declare(plan)

    def _rollback_plan(
        self,
        plan: ActionRequest,
        result: ActionResult,
    ) -> ActionRequest:
        """校验回滚前置条件，返回用于票据绑定的回滚请求。"""

        if plan.session_id != self.session_id:
            raise AutomationError("plan_session_mismatch", "计划不属于当前会话")
        if self.status is not SessionStatus.OPEN:
            raise AutomationError(
                f"session_{self.status.value}",
                f"会话状态为 {self.status.value}，不能回滚",
            )
        recorded = self._executed.get(plan.step_no)
        if recorded is None or recorded[0].fingerprint() != plan.fingerprint():
            raise AutomationError(
                "rollback_target_not_executed",
                "该步骤没有可回滚的执行记录",
            )
        if plan.step_no in self._rolled_back:
            raise AutomationError(
                "rollback_already_performed",
                f"步骤 {plan.step_no} 已经回滚过，不能重复回滚",
            )
        if not result.ok:
            raise AutomationError(
                "rollback_of_failed_action",
                "只允许回滚已成功的动作",
            )
        capability = self._capability(plan)
        if not capability.reversible:
            raise AutomationError(
                "rollback_not_reversible",
                f"动作 {plan.action} 不可逆: {capability.reason or 'no_reason'}",
            )
        return ActionRequest(
            session_id=self.session_id,
            step_no=plan.step_no,
            action=f"rollback:{plan.action}",
            target=plan.target,
            target_ref=plan.target_ref,
            params={"of_step": plan.step_no},
            expected_focus=None,
        )

    def _rollback_reject(
        self,
        plan: ActionRequest,
        reason: str,
        message: str,
    ) -> ActionResult:
        self.audit.record_action(
            principal=self.principal,
            session_id=self.session_id,
            step_no=plan.step_no,
            action=f"rollback:{plan.action}",
            target=plan.target.value,
            target_ref=plan.target_ref,
            expected_focus=plan.expected_focus,
            params={"of_step": plan.step_no},
            fingerprint="",
            outcome="deny",
            stage="rollback_intent",
            error=reason,
        )
        return ActionResult(
            status="rejected",
            action=f"rollback:{plan.action}",
            ok=False,
            error=reason,
            detail={
                "rollback_completed": False,
                "message": message,
                "of_step": plan.step_no,
            },
        )

    def _rollback_incomplete(
        self,
        plan: ActionRequest,
        rollback_plan: ActionRequest,
        *,
        reason: str,
        message: str,
        observation: Optional[Observation] = None,
    ) -> ActionResult:
        return ActionResult(
            status="failed",
            action=rollback_plan.action,
            ok=False,
            observation=observation,
            error=reason,
            detail={
                "rollback_completed": False,
                "message": message,
                "of_step": plan.step_no,
            },
        )

    def _reject(
        self,
        plan: ActionRequest,
        reason: str,
        observation: Optional[Observation] = None,
        *,
        message: str = "",
        detail: Optional[Dict[str, object]] = None,
    ) -> ActionResult:
        self.audit.record_action(
            principal=self.principal,
            session_id=self.session_id,
            step_no=plan.step_no,
            action=plan.action,
            target=plan.target.value,
            target_ref=plan.target_ref,
            expected_focus=plan.expected_focus,
            params=plan.params,
            fingerprint=plan.fingerprint(),
            outcome="deny",
            stage="intent",
            error=reason,
        )
        payload: Dict[str, object] = {}
        if message:
            payload["message"] = message
        if detail:
            payload.update(detail)
        return ActionResult(
            status="rejected",
            action=plan.action,
            ok=False,
            observation=observation,
            error=reason,
            detail=payload,
        )


def _default_preview(plan: ActionRequest) -> str:
    target = plan.target_ref or plan.target.value
    return f"{plan.action} -> {target} (step {plan.step_no})"


def _default_rollback_preview(plan: ActionRequest) -> str:
    target = plan.target_ref or plan.target.value
    return f"回滚 {plan.action} -> {target} (step {plan.step_no})"


class AutomationSessionRegistry:
    """会话注册表（进程级）。"""

    def __init__(self) -> None:
        self._sessions: Dict[str, AutomationSession] = {}
        self._lock = threading.RLock()

    def open(
        self,
        *,
        principal: str,
        principal_kind: str,
        backend: object,
        guard: Guard,
        broker: SqliteConfirmationBroker,
        audit: AutomationAudit,
        estop: EmergencyStop,
    ) -> AutomationSession:
        session = AutomationSession(
            principal=principal,
            principal_kind=principal_kind,
            backend=backend,
            guard=guard,
            broker=broker,
            audit=audit,
            estop=estop,
        )
        with self._lock:
            self._sessions[session.session_id] = session
        return session

    def get(
        self,
        session_id: str,
        *,
        principal: Optional[str] = None,
    ) -> Optional[AutomationSession]:
        with self._lock:
            session = self._sessions.get(str(session_id))
        if session is None:
            return None
        if principal is not None and session.principal != str(principal):
            return None
        return session

    def close(self, session_id: str, *, principal: Optional[str] = None) -> bool:
        session = self.get(session_id, principal=principal)
        if session is None:
            return False
        return session.close("closed_by_request")

    def close_all(self, *, principal: str) -> int:
        with self._lock:
            targets = [s for s in self._sessions.values() if s.principal == str(principal)]
        return sum(1 for s in targets if s.close("closed_by_request"))

    def freeze_all(self, reason: str) -> int:
        with self._lock:
            targets = list(self._sessions.values())
        frozen = 0
        for session in targets:
            if session.status is not SessionStatus.CLOSED:
                session.freeze(str(reason))
                frozen += 1
        return frozen

    def status(self, *, principal: str) -> Dict[str, object]:
        with self._lock:
            targets = [s for s in self._sessions.values() if s.principal == str(principal)]
        return {
            "principal": str(principal),
            "sessions": [s.status_dict() for s in targets],
        }

    def purge_closed(self) -> int:
        with self._lock:
            closed = [sid for sid, s in self._sessions.items() if s.status is SessionStatus.CLOSED]
            for sid in closed:
                self._sessions.pop(sid, None)
        return len(closed)


_registry_lock = threading.Lock()
_registry: Optional[AutomationSessionRegistry] = None


def get_session_registry() -> AutomationSessionRegistry:
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = AutomationSessionRegistry()
    return _registry
