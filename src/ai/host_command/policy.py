"""U1 — host-command policy engine.

The policy engine turns a :class:`~src.ai.host_command.models.HostCommandRequest`
and a :class:`~src.ai.host_command.capability.CapabilityCatalog` into a
:class:`~src.ai.host_command.models.HostCommandDecision`.

* :class:`DenyAllPolicy` -- the baseline; denies everything (used when the global
  enablement gate is OFF).
* :class:`CapabilityBasedPolicy` -- allows only when a non-approval capability
  matches; escalates (DEFER) when the only matches require human approval.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from .capability import CapabilityCatalog
from .models import (
    DecisionOutcome,
    HostCommandDecision,
    HostCommandRequest,
    SandboxSpec,
)


class HostCommandPolicy(ABC):
    """Policy-neutral decision interface for host-command authorization."""

    @abstractmethod
    def evaluate(
        self, req: HostCommandRequest, catalog: CapabilityCatalog
    ) -> HostCommandDecision:
        raise NotImplementedError


class DenyAllPolicy(HostCommandPolicy):
    """Default-deny baseline. Nothing is permitted."""

    def evaluate(
        self, req: HostCommandRequest, catalog: CapabilityCatalog
    ) -> HostCommandDecision:
        return HostCommandDecision(
            DecisionOutcome.DENY,
            "default deny: host-command policy is deny-all",
            req,
        )


class CapabilityBasedPolicy(HostCommandPolicy):
    """Allow only when a matching, non-approval capability exists."""

    def evaluate(
        self, req: HostCommandRequest, catalog: CapabilityCatalog
    ) -> HostCommandDecision:
        matched = catalog.match(req)
        if not matched:
            return HostCommandDecision(
                DecisionOutcome.DENY,
                "no matching capability grant",
                req,
            )
        approval_required = [c for c in matched if c.requires_approval]
        if approval_required:
            return HostCommandDecision(
                DecisionOutcome.DEFER,
                "matching capability requires human-sovereign approval",
                req,
                sandbox=self._pick_sandbox(approval_required),
            )
        sandbox = self._pick_sandbox(matched)
        return HostCommandDecision(
            DecisionOutcome.ALLOW,
            "capability match (no approval required)",
            req,
            sandbox=sandbox,
        )

    @staticmethod
    def _pick_sandbox(caps) -> Optional[SandboxSpec]:
        for c in caps:
            if c.sandbox is not None:
                return c.sandbox
        return None
