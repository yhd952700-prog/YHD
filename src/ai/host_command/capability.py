"""U1 — host-command capability model (default-empty => deny-all).

A :class:`HostCommandCapability` describes a *grant*: a scoped permission to run a
class of host commands. The catalog starts **empty**, so with no grants the
policy engine denies everything. Flipping the global enablement gate ON does not
grant anything -- capabilities must be registered explicitly. This is the
default-DENY guarantee at the capability layer.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .models import HostCommandRequest, SandboxSpec


@dataclass(frozen=True)
class HostCommandCapability:
    """A scoped grant to run a class of host commands."""

    capability_id: str
    description: str = ""
    # Matching rules (all must match for the capability to apply):
    command_glob: Optional[str] = None  # e.g. "git *", "ls *"
    adapter: Optional[str] = None       # restrict to an adapter ("shell", ...)
    require_shell: Optional[bool] = None  # True/False/None(any)
    sandbox: Optional[SandboxSpec] = None
    # If True, even a match must be escalated to a human for approval (sovereignty).
    requires_approval: bool = False


class CapabilityCatalog:
    """Registry of granted capabilities. Empty by default => deny-all."""

    def __init__(self) -> None:
        self._caps: Dict[str, HostCommandCapability] = {}

    def register(self, cap: HostCommandCapability) -> None:
        self._caps[cap.capability_id] = cap

    def revoke(self, capability_id: str) -> bool:
        return self._caps.pop(capability_id, None) is not None

    def all(self) -> List[HostCommandCapability]:
        return list(self._caps.values())

    def match(self, req: HostCommandRequest) -> List[HostCommandCapability]:
        """Return capabilities whose rules all match ``req`` (order-independent)."""
        return [c for c in self._caps.values() if self._matches(c, req)]

    def _matches(self, cap: HostCommandCapability, req: HostCommandRequest) -> bool:
        if cap.adapter is not None and cap.adapter != req.adapter:
            return False
        if cap.require_shell is not None and cap.require_shell != req.use_shell:
            return False
        if cap.command_glob is not None:
            if not fnmatch.fnmatch(req.command, cap.command_glob):
                return False
        return True
