"""U1 — command broker (authorization pipeline + enablement gate + execution).

The broker is the single dispatch point for agent host commands. Its pipeline:

    1. enablement gate  -- if ``LIUHAO_HOST_COMMAND_ENABLED`` is OFF, DENY
                           (global default-DENY; no exception).
    2. policy evaluate  -- capability-based decision (ALLOW / DENY / DEFER).
    3. approval         -- DEFER -> escalate to human; allow only if granted.
    4. execute          -- run via the injected executor, unless in simulation
                           mode (dry-run: decision computed, nothing executed).

Design guarantees:
  * Production default is DENY at every layer (gate OFF, empty catalog, no grants).
  * Flipping the gate ON is a config change; it grants nothing by itself.
  * The executor is injected, so the broker stays testable and decoupled from any
    concrete world adapter (e.g. ``WorldInterface``).

Observability is fail-soft: audit/event emission never breaks the decision path.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, Optional

from .approval import ApprovalInterface
from .capability import CapabilityCatalog
from .confirm import is_confirmation_enabled
from .danger import classify
from .enablement import ENV_VAR, is_enabled
from .models import (
    DecisionOutcome,
    HostCommandDecision,
    HostCommandRequest,
)
from .policy import (
    CapabilityBasedPolicy,
    DenyAllPolicy,
    FullAuthorityPolicy,
    HostCommandPolicy,
)

#: Executor: takes a request, returns an execution result dict.
CommandExecutor = Callable[["HostCommandRequest"], Dict[str, Any]]

#: Observability sink: event name -> details.
HostCommandEventSink = Callable[[str, Dict[str, Any]], None]


def _default_sink(event: str, details: Dict[str, Any]) -> None:
    """Write the event to the audit kernel.

    Failures are **raised**, not swallowed, so callers can choose the policy:
    telemetry paths go through :meth:`HostCommandBroker._emit` (fail-soft), while
    the pre-execution authorization record treats a failure as fatal by default.
    (Previously this swallowed every error, so an executed host command could
    leave no audit record at all -- unacceptable for a sovereignty boundary.)
    """
    # G5：真实执行记录落成独立事件类型，而不是混进 POLICY_EVAL。
    # 这样「审批被关掉后」仍然能按 host_command_executed 精确检索到完整命令行。
    if event == "host_command_executed_audit":
        from src.security.authority_audit import record_host_command

        record_host_command(
            command=details.get("command", ""),
            cwd=details.get("cwd"),
            exit_code=details.get("exit_code"),
            duration_ms=details.get("duration_ms"),
            actor=details.get("actor", "host-command-broker"),
            correlation_id=details.get("correlation_id"),
            dangerous=bool(details.get("dangerous")),
            confirm_state=str(details.get("confirm_state") or "not_required"),
            use_shell=bool(details.get("use_shell")),
        )
        return

    from src.kernels.audit import AuditEventType, AuditScope, log_event

    log_event(
        AuditEventType.POLICY_EVAL,
        details.get("principal", "host-command-broker"),
        AuditScope.L5,
        details.get("outcome", "ok"),
        {**details, "host_command_event": event},
    )


def _full_authority() -> bool:
    """当前进程是否处于完全授权模式（读不到时按更严的 standard 处理）。"""
    try:
        from src.security.authority_mode import is_full

        return is_full()
    except Exception:  # pragma: no cover - 权限模块不可用时保持默认 DENY
        return False


class HostCommandBroker:
    """Authorizes and (conditionally) executes agent host commands."""

    def __init__(
        self,
        catalog: Optional[CapabilityCatalog] = None,
        policy: Optional[HostCommandPolicy] = None,
        approvals: Optional[ApprovalInterface] = None,
        executor: Optional[CommandExecutor] = None,
        *,
        simulate: bool = False,
        event_sink: Optional[HostCommandEventSink] = None,
        fail_closed_audit: bool = True,
    ) -> None:
        # Accountability guarantee: an executed host command must leave an audit
        # record. If the audit trail cannot be written, refuse to execute
        # (fail-closed) rather than running silently. Set False only for
        # deliberate, documented degradation.
        self.fail_closed_audit = fail_closed_audit
        # Consumer-side signal for audit-backend health. This COMPLEMENTS -- never
        # substitutes -- the root-cause "append-lock hold duration" metric that
        # belongs inside AuditStore (not ours to edit). A rising audit_failures /
        # long last_audit_write_ms here is the symptom of lock contention there.
        self.audit_failures: int = 0
        self.last_audit_error: Optional[str] = None
        self.last_audit_write_ms: Optional[float] = None
        self.catalog = catalog or CapabilityCatalog()
        # When the global gate is OFF, force the deny-all policy regardless of
        # any injected policy, so the gate is the authoritative default-DENY.
        if not is_enabled():
            self.policy: HostCommandPolicy = DenyAllPolicy()
        elif policy is not None:
            self.policy = policy
        elif _full_authority():
            # 全权模式：策略层放行（提权/受保护路径/危险确认仍在管线里生效）。
            self.policy = FullAuthorityPolicy()
        else:
            self.policy = CapabilityBasedPolicy()
        self.approvals = approvals or ApprovalInterface()
        self.executor = executor
        self.simulate = simulate
        self._sink = event_sink or _default_sink

    def submit(self, req: HostCommandRequest) -> HostCommandDecision:
        # Executor-identity fence wrapper (NOT a capability gate). When the executor
        # fence gate (LIUHAO_EXECUTOR_FENCE) is ARMED, this wraps the command in an
        # executor lease so every action has a valid, non-stale, non-replayed
        # executor identity. The lease is granted ``capabilities=()`` -- an EMPTY grant --
        # so the lease's capability check is a deliberate no-op (``set(()) ⊆ set(())`` is
        # always True). Host-command authorization is enforced by THIS pipeline (the
        # enablement gate + policy + approval below), NOT by the executor fence. The
        # wrapper is kept only so that when the fence gate is armed actions still
        # acquire an identity; it does not add or imply capability authorization.
        # When the gate is NOT armed this is a pass-through. Fail-closed: if armed and
        # we cannot acquire a lease, refuse the command.
        from src.kernels.execution.fence import (
            ExecutorFenceDenied,
            executor_session,
        )

        try:
            with executor_session(
                action="host_command.submit", capabilities=(), ttl_sec=30.0
            ):
                return self._submit_fenced(req)
        except ExecutorFenceDenied as exc:
            err = HostCommandDecision(
                DecisionOutcome.ERROR,
                f"executor fence denied (fail-closed): {exc}",
                req,
            )
            self._emit("host_command_fence_denied", err.as_dict())
            return err

    def _submit_fenced(self, req: HostCommandRequest) -> HostCommandDecision:
        # 0) D19-D21 defense-in-depth: if an executor fence context is bound on
        # the call stack, re-validate it. A forged/stale/expired/unknown executor
        # is refused fail-closed BEFORE any policy evaluation or execution.
        try:
            from src.kernels.execution.fence import (
                ExecutorFenceDenied,
                current_executor_fence,
                get_executor_fence,
            )

            _fctx = current_executor_fence()
            if _fctx is not None:
                get_executor_fence().enforce(_fctx, "host_command.submit", ())
        except ExecutorFenceDenied as exc:
            err = HostCommandDecision(
                DecisionOutcome.ERROR,
                f"executor fence denied (fail-closed): {exc}",
                req,
            )
            self._emit("host_command_fence_denied", err.as_dict())
            return err

        # 1) Global enablement gate (default DENY).
        if not is_enabled():
            decision = HostCommandDecision(
                DecisionOutcome.DENY,
                f"host-command disabled (env {ENV_VAR} unset); default DENY",
                req,
            )
            self._emit("host_command_decision", decision.as_dict())
            return decision

        # 1b) 提权一律拒绝：全权模式也不提权（老板 2026-11-11 的边界 1）。
        # 始终以当前 Windows 用户权限运行，不碰 UAC / 服务 / 计划任务 / HKLM。
        report = classify(req.command)
        req.metadata["dangerous"] = report.is_dangerous
        if report.is_escalation:
            decision = HostCommandDecision(
                DecisionOutcome.DENY,
                f"escalation refused ({report.escalation}): full authority never "
                f"raises privileges; running as the current user is the hard boundary",
                req,
            )
            self._emit("host_command_decision", decision.as_dict())
            return decision

        # 1c) 敏感路径：**只记录，不拒绝**（2026-11-11 老板第二轮决定，标准/全权
        # 一致）。AI 命令点名审计库/密钥/程序本体/模式配置时先写一条审计，然后
        # 继续走策略与执行管线；用户本人（actor="human"）不写这条记录。
        full_mode = _full_authority()
        if (req.actor or "").strip().lower() != "human":
            from src.security.protected_paths import scan_command

            reason = scan_command(req.command)
            if reason:
                self._record_sensitive_touch(req, reason)

        # 2) Policy evaluation (capability-based / full-authority).
        decision = self.policy.evaluate(req, self.catalog)

        # 2b) 危险命令的一次确认（仅全权模式；标准模式本来就要审批或拒绝）。
        # 设置里可以关掉这一层，但关闭本身进审计（G3），且执行前仍会写 G2。
        if full_mode and report.is_dangerous:
            if is_confirmation_enabled():
                req.metadata["confirm_state"] = "required"
                if decision.outcome is DecisionOutcome.ALLOW:
                    decision = HostCommandDecision(
                        DecisionOutcome.DEFER,
                        f"dangerous command requires one-time confirmation "
                        f"({report.dangerous})",
                        req,
                        sandbox=decision.sandbox,
                    )
            else:
                req.metadata["confirm_state"] = "disabled_by_setting"

        # 3) Human-sovereign approval escalation.
        if decision.outcome is DecisionOutcome.DEFER:
            # If a human has already granted THIS command, allow it (one-shot).
            granted_id = self.approvals.granted_id_for(req)
            if granted_id is not None:
                decision = HostCommandDecision(
                    DecisionOutcome.ALLOW,
                    "approved by human sovereignty",
                    req,
                    sandbox=decision.sandbox,
                    approval_id=granted_id,
                )
                self.approvals.consume(granted_id)
            else:
                # Otherwise escalate: create a fresh escalation and defer.
                ar = self.approvals.request(req, decision.reason)
                self._emit(
                    "host_command_decision",
                    {**decision.as_dict(), "approval_id": ar.approval_id},
                )
                return decision

        # 3b) G2：全权模式下放行一条不可逆（危险）命令前写审计。
        # 独立于审批：审批被关掉时这条仍然必须存在。
        if (
            full_mode
            and report.is_dangerous
            and decision.outcome is DecisionOutcome.ALLOW
        ):
            try:
                from src.security.authority_audit import (
                    record_irreversible_allowed,
                    redact_command,
                )

                record_irreversible_allowed(
                    action="host_command",
                    tier="CRITICAL",
                    actor=req.actor,
                    correlation_id=req.correlation_id,
                    target=req.command,
                    confirm_state=str(
                        req.metadata.get("confirm_state") or "not_required"
                    ),
                    extra={
                        "command": redact_command(req.command),
                        "cwd": req.cwd,
                        "dangerous": report.dangerous,
                    },
                )
            except Exception as exc:  # noqa: BLE001 - audit failure is a decision input
                self.audit_failures += 1
                self.last_audit_error = str(exc)
                if self.fail_closed_audit:
                    err = HostCommandDecision(
                        DecisionOutcome.ERROR,
                        f"irreversible-action audit unavailable; refusing to "
                        f"execute (fail-closed): {exc}",
                        req,
                        sandbox=decision.sandbox,
                        approval_id=decision.approval_id,
                    )
                    self._emit("host_command_audit_failure", {**err.as_dict(), "error": str(exc)})
                    return err

        # 4) Execute (unless simulation, or not ALLOW).
        if decision.outcome is not DecisionOutcome.ALLOW:
            self._emit("host_command_decision", decision.as_dict())
            return decision
        if self.simulate:
            self._emit(
                "host_command_simulated",
                {**decision.as_dict(), "note": "simulation mode: not executed"},
            )
            return decision
        if self.executor is None:
            err = HostCommandDecision(
                DecisionOutcome.ERROR, "no executor configured", req,
                sandbox=decision.sandbox, approval_id=decision.approval_id,
            )
            self._emit("host_command_decision", err.as_dict())
            return err
        # 4b) Accountability: record the authorization BEFORE executing. An
        # executed host command with no audit record breaks sovereignty, so a
        # failed audit write is fatal by default (fail-closed).
        started = time.time()
        try:
            self._sink("host_command_authorized", decision.as_dict())
        except Exception as exc:  # noqa: BLE001 - audit failure is a decision input
            self.audit_failures += 1
            self.last_audit_error = str(exc)
            if self.fail_closed_audit:
                err = HostCommandDecision(
                    DecisionOutcome.ERROR,
                    f"audit trail unavailable; refusing to execute "
                    f"(fail-closed): {exc}",
                    req,
                    sandbox=decision.sandbox,
                    approval_id=decision.approval_id,
                )
                self._emit("host_command_audit_failure", {**err.as_dict(), "error": str(exc)})
                return err
        finally:
            # Runs on both paths (success and fail-closed refusal).
            self.last_audit_write_ms = (time.time() - started) * 1000.0
        try:
            result = self.executor(req)
            # G5: 主机命令真实执行（完整命令行，脱敏）。这条记录独立于审批：
            # 审批关掉后仍必须能查到「跑了什么、退出码多少」。
            exit_code = result.get("exit_code") if isinstance(result, dict) else None
            self._record_executed(
                req, exit_code, (time.time() - started) * 1000.0
            )
            self._emit(
                "host_command_executed",
                {**decision.as_dict(), "result": result},
            )
            return decision
        except Exception as exc:  # noqa: BLE001 - surface executor failure as decision
            self._record_executed(req, None, (time.time() - started) * 1000.0)
            err = HostCommandDecision(
                DecisionOutcome.ERROR, f"executor error: {exc}", req,
                sandbox=decision.sandbox, approval_id=decision.approval_id,
            )
            self._emit("host_command_decision", err.as_dict())
            return err

    def _record_sensitive_touch(self, req: HostCommandRequest, reason: str) -> None:
        """敏感路径触碰的审计（**只记录**）。写失败只计数，不改变放行结论。"""
        try:
            from src.security.authority_audit import record_protected_touched

            record_protected_touched(
                path=None,
                reason=reason,
                actor=req.actor,
                correlation_id=req.correlation_id,
                command=req.command,
                channel="host_command",
                operation="command",
            )
        except Exception as exc:  # noqa: BLE001 - 审计故障不改变放行结论
            self.audit_failures += 1
            self.last_audit_error = str(exc)

    def _record_executed(
        self,
        req: HostCommandRequest,
        exit_code: int | None,
        duration_ms: float,
    ) -> None:
        """G5：把「命令真的执行了」写进审计（完整命令行，脱敏）。

        与 ``host_command_authorized`` 的分工：那条回答「谁批准了」，这条回答
        「实际跑的是什么、结果如何」。审批被关掉后只有这条还在，因此它不能依赖
        审批状态。走 sink 而不是直接写库，是为了保持 broker 可注入、可测试；
        默认 sink 会把它落成独立的 ``HOST_COMMAND_EXECUTED`` 事件类型。
        """
        try:
            self._sink(
                "host_command_executed_audit",
                {
                    "command": req.command,
                    "cwd": req.cwd,
                    "exit_code": exit_code,
                    "duration_ms": duration_ms,
                    "actor": req.actor,
                    "correlation_id": req.correlation_id,
                    "dangerous": bool(req.metadata.get("dangerous")),
                    "confirm_state": str(
                        req.metadata.get("confirm_state") or "not_required"
                    ),
                    "use_shell": req.use_shell,
                },
            )
        except Exception as exc:  # noqa: BLE001 - 审计后端故障要计数但不吞掉
            self.audit_failures += 1
            self.last_audit_error = str(exc)

    def _emit(self, event: str, details: Dict[str, Any]) -> None:
        try:
            self._sink(event, details)
        except Exception:
            pass
