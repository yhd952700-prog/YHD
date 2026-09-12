"""
Tool Registry & Router — MASTER-SPEC Phase 9 (L-Core) / §40-41.

Bridges the Execution Kernel's ``capability_id``-based planning to *real* tool
execution. The Execution Kernel's ``ActionExecutor`` simulates capability calls
(``_simulate_capability``); this module supplies the missing piece — a registry
of concrete tools (each bound to a ``capability``) with a lifecycle, and a
router that maps a ``capability_id`` to an active tool and executes it.

§40 lifecycle: REGISTER -> VALIDATE -> APPROVE -> ACTIVE -> SUSPENDED -> REVOKED.
Only ACTIVE tools are executable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from ..kernels.execution import ActionResult
from .observability import observe
from .audit import audited


class ToolStatus(Enum):
    """§40 tool lifecycle states."""
    REGISTERED = "registered"
    VALIDATED = "validated"
    APPROVED = "approved"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    REVOKED = "revoked"


@dataclass
class Tool:
    """A concrete, callable tool (MASTER-SPEC §40 Tool)."""

    tool_id: str
    name: str
    version: str
    description: str
    capability: str  # capability_id this tool satisfies (Execution Kernel namespace)
    schema: Dict[str, Any]  # input parameter schema
    fn: Callable[..., Any]  # real execution function
    risk: str = "LOW"
    permission: Optional[str] = None
    dependencies: List[str] = field(default_factory=list)
    sandbox_policy: Optional[str] = None
    health: str = "UNKNOWN"
    audit_policy: Optional[str] = None


class ToolRegistry:
    """§40 registry enforcing the tool lifecycle."""

    def __init__(self) -> None:
        self._tools: Dict[str, Tool] = {}
        self._status: Dict[str, ToolStatus] = {}

    # -------------------------------------------------------------- lifecycle
    @observe("tool_registry.register")
    @audited("p9.tool.register", module="src.ai.tool_registry")
    def register(self, tool: Tool) -> str:
        self._tools[tool.tool_id] = tool
        self._status[tool.tool_id] = ToolStatus.REGISTERED
        return tool.tool_id

    def validate(self, tool_id: str) -> bool:
        if self._status.get(tool_id) != ToolStatus.REGISTERED:
            return False
        tool = self._tools[tool_id]
        if not (tool.name and tool.description and tool.capability and callable(tool.fn)):
            return False
        self._status[tool_id] = ToolStatus.VALIDATED
        return True

    @audited("p9.tool.approve", module="src.ai.tool_registry")
    def approve(self, tool_id: str) -> bool:
        if self._status.get(tool_id) != ToolStatus.VALIDATED:
            return False
        self._status[tool_id] = ToolStatus.APPROVED
        return True

    @audited("p9.tool.activate", module="src.ai.tool_registry")
    def activate(self, tool_id: str) -> bool:
        if self._status.get(tool_id) != ToolStatus.APPROVED:
            return False
        self._status[tool_id] = ToolStatus.ACTIVE
        return True

    @audited("p9.tool.suspend", module="src.ai.tool_registry")
    def suspend(self, tool_id: str) -> bool:
        if tool_id not in self._tools:
            return False
        self._status[tool_id] = ToolStatus.SUSPENDED
        return True

    @audited("p9.tool.revoke", module="src.ai.tool_registry")
    def revoke(self, tool_id: str) -> bool:
        if tool_id not in self._tools:
            return False
        self._status[tool_id] = ToolStatus.REVOKED
        return True

    # ---------------------------------------------------------------- queries
    def get(self, tool_id: str) -> Optional[Tool]:
        return self._tools.get(tool_id)

    def status(self, tool_id: str) -> Optional[ToolStatus]:
        return self._status.get(tool_id)

    def list_active_by_capability(self, capability: str) -> List[Tool]:
        return [
            t for t in self._tools.values()
            if t.capability == capability and self._status[t.tool_id] == ToolStatus.ACTIVE
        ]

    # -------------------------------------------------------------- execution
    @observe("tool_registry.execute")
    def execute(self, tool_id: str, inputs: Dict[str, Any]) -> ActionResult:
        """Execute a tool (only when ACTIVE). Returns an ``ActionResult``."""
        tool = self._tools.get(tool_id)
        if tool is None:
            return ActionResult(action_id=tool_id, success=False,
                                error=f"unknown tool {tool_id}")
        if self._status[tool_id] != ToolStatus.ACTIVE:
            return ActionResult(action_id=tool_id, success=False,
                                error=f"tool {tool_id} is not active")
        try:
            output = tool.fn(**inputs)
            return ActionResult(action_id=tool_id, success=True, output=output)
        except Exception as exc:  # noqa: BLE001 - surface as failed action
            return ActionResult(action_id=tool_id, success=False, error=str(exc))


class ToolRouter:
    """Maps a ``capability_id`` to an active tool and executes it."""

    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    def route(self, capability_id: str) -> Optional[Tool]:
        tools = self.registry.list_active_by_capability(capability_id)
        return tools[0] if tools else None

    @observe("tool_router.execute")
    def execute(self, capability_id: str, inputs: Dict[str, Any]) -> ActionResult:
        tool = self.route(capability_id)
        if tool is None:
            return ActionResult(action_id="", success=False,
                                error=f"no active tool for capability {capability_id}")
        return self.registry.execute(tool.tool_id, inputs)
