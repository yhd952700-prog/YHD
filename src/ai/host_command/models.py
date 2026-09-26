"""U1 — host-command shared models.

Data structures for the agent host-command capability: the request the agent
makes, the decision the broker reaches, and the sandbox constraints under which
execution (if any) is permitted. Keep these dependency-free so the broker,
policy engine, and tests can all import them without pulling in the executor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class DecisionOutcome(str, Enum):
    """Verdict of the authorization pipeline."""

    ALLOW = "allow"    #: permitted; may execute
    DENY = "deny"      #: forbidden (default-deny baseline)
    DEFER = "defer"    #: needs human-sovereign approval before it may proceed
    ERROR = "error"    #: pipeline/internal failure


@dataclass
class SandboxSpec:
    """Containment constraints recorded for an execution.

    This is the *intent* the broker enforces at the policy layer; actual OS-level
    sandboxing is performed by the executor (e.g. RestrictedPython / container),
    not by this dataclass. Recording the spec makes the containment decision
    auditable even when the low-level mechanism differs per deployment.
    """

    cwd: Optional[str] = None
    network: bool = False
    max_cpu_seconds: Optional[float] = None
    max_memory_bytes: Optional[int] = None
    allowed_binaries: Optional[List[str]] = None
    readonly_fs: bool = False


@dataclass
class HostCommandRequest:
    """A request from the agent to run a host command."""

    command: str
    actor: str = "autonomous"
    adapter: str = "shell"  # mirrors WorldInterface adapters ("shell", "filesystem", ...)
    use_shell: bool = False  # True => raw shell; requires explicit arming
    cwd: Optional[str] = None
    capabilities_required: List[str] = field(default_factory=list)
    correlation_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class HostCommandDecision:
    """The broker's verdict on a request, with the rationale and any sandbox."""

    outcome: DecisionOutcome
    reason: str
    request: HostCommandRequest
    sandbox: Optional[SandboxSpec] = None
    correlation_id: Optional[str] = None
    approval_id: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "reason": self.reason,
            "command": self.request.command,
            "actor": self.request.actor,
            "use_shell": self.request.use_shell,
            "sandbox": (self.sandbox.__dict__ if self.sandbox else None),
            "approval_id": self.approval_id,
        }
