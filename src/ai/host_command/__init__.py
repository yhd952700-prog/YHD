"""U1 — agent host-command infrastructure (policy-neutral, default-DENY).

Builds the host-command capability as a safe, config-gated subsystem:

  * production default is DENY at every layer (gate OFF, empty catalog, no grants);
  * the global enablement gate (``LIUHAO_HOST_COMMAND_ENABLED``) is OFF by
    default, mirroring the kernel enforcement control point -- flipping it ON later
    is a safe config/policy change, not a rebuild;
  * even when ON, the capability model stays default-DENY: only explicitly granted
    capabilities permit execution, and approval-required capabilities escalate to a
    human (sovereignty) before they may run.

Public API
----------
* :class:`HostCommandRequest`, :class:`HostCommandDecision`, :class:`DecisionOutcome`,
  :class:`SandboxSpec` -- shared models.
* :class:`HostCommandCapability`, :class:`CapabilityCatalog` -- capability model
  (default empty => deny-all).
* :class:`HostCommandPolicy`, :class:`DenyAllPolicy`, :class:`CapabilityBasedPolicy`
  -- policy engine.
* :class:`ApprovalInterface`, :class:`ApprovalRequest` -- human-sovereign approval.
* :func:`is_enabled`, :func:`reload`, :func:`describe`, :data:`ENV_VAR` --
  future enablement gate (default OFF).
* :class:`HostCommandBroker` -- authorization pipeline + execution.
* :mod:`src.ai.host_command.harness` -- test harness + ``WorldInterface`` adapter.

The final decision to enable host-command in production is a sovereign decision and
is NOT taken here; only the safe, default-DENY machinery is provided.
"""

from __future__ import annotations

from .approval import ApprovalInterface, ApprovalRequest
from .broker import CommandExecutor, HostCommandBroker
from .capability import CapabilityCatalog, HostCommandCapability
from .enablement import ENV_VAR, describe, is_enabled, parse_enablement, reload
from .models import (
    DecisionOutcome,
    HostCommandDecision,
    HostCommandRequest,
    SandboxSpec,
)
from .policy import (
    CapabilityBasedPolicy,
    DenyAllPolicy,
    HostCommandPolicy,
)

__all__ = [
    "HostCommandRequest",
    "HostCommandDecision",
    "DecisionOutcome",
    "SandboxSpec",
    "HostCommandCapability",
    "CapabilityCatalog",
    "HostCommandPolicy",
    "DenyAllPolicy",
    "CapabilityBasedPolicy",
    "ApprovalInterface",
    "ApprovalRequest",
    "ENV_VAR",
    "is_enabled",
    "parse_enablement",
    "reload",
    "describe",
    "HostCommandBroker",
    "CommandExecutor",
]
