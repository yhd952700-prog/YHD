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

from typing import Any, Callable, Dict, Optional

from .approval import ApprovalInterface
from .capability import CapabilityCatalog
from .enablement import ENV_VAR, is_enabled
from .models import (
    DecisionOutcome,
    HostCommandDecision,
    HostCommandRequest,
)
from .policy import CapabilityBasedPolicy, DenyAllPolicy, HostCommandPolicy

#: Executor: takes a request, returns an execution result dict.
CommandExecutor = Callable[["HostCommandRequest"], Dict[str, Any]]

#: Observability sink: event name -> details.
HostCommandEventSink = Callable[[str, Dict[str, Any]], None]


def _default_sink(event: str, details: Dict[str, Any]) -> None:
    try:
        from src.kernels.audit import AuditEventType, AuditScope, log_event

        log_event(
            AuditEventType.POLICY_EVAL,
            details.get("principal", "host-command-broker"),
            AuditScope.L5,
            details.get("outcome", "ok"),
            {**details, "host_command_event": event},
        )
    except Exception:
        pass  # observability must not fail the control path


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
    ) -> None:
        self.catalog = catalog or CapabilityCatalog()
        # When the global gate is OFF, force the deny-all policy regardless of
        # any injected policy, so the gate is the authoritative default-DENY.
        if not is_enabled():
            self.policy: HostCommandPolicy = DenyAllPolicy()
        else:
            self.policy = policy or CapabilityBasedPolicy()
        self.approvals = approvals or ApprovalInterface()
        self.executor = executor
        self.simulate = simulate
        self._sink = event_sink or _default_sink

    def submit(self, req: HostCommandRequest) -> HostCommandDecision:
        # 1) Global enablement gate (default DENY).
        if not is_enabled():
            decision = HostCommandDecision(
                DecisionOutcome.DENY,
                f"host-command disabled (env {ENV_VAR} unset); default DENY",
                req,
            )
            self._emit("host_command_decision", decision.as_dict())
            return decision

        # 2) Policy evaluation (capability-based).
        decision = self.policy.evaluate(req, self.catalog)

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
        try:
            result = self.executor(req)
            self._emit(
                "host_command_executed",
                {**decision.as_dict(), "result": result},
            )
            return decision
        except Exception as exc:  # noqa: BLE001 - surface executor failure as decision
            err = HostCommandDecision(
                DecisionOutcome.ERROR, f"executor error: {exc}", req,
                sandbox=decision.sandbox, approval_id=decision.approval_id,
            )
            self._emit("host_command_decision", err.as_dict())
            return err

    def _emit(self, event: str, details: Dict[str, Any]) -> None:
        try:
            self._sink(event, details)
        except Exception:
            pass
