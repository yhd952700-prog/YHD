"""
World Interface — MASTER-SPEC Phase 15 / §36-37 (EDITH).

The interface through which agents observe and act on the external world.
Implements the §37 WORLD ACTION CONTRACT — ``observe / validate / authorize /
execute / verify`` — over pluggable adapters (Browser, Computer, Filesystem,
Shell, Git, HTTP, ...). This first increment ships the locally-testable
adapters (Filesystem, Shell); network adapters build on the Network Kernel's
protocol adapters in a later phase.

``execute`` returns the Execution Kernel's ``ActionResult`` so results flow
into the same verify/audit path the rest of the system uses.
"""

from __future__ import annotations

import os
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from ..kernels.execution import ActionResult
from .observability import observe as _observe


@dataclass
class WorldRequest:
    """A request to observe or act on the external world."""
    adapter: str  # e.g. "filesystem", "shell"
    action: str   # e.g. "read", "write", "list", "run"
    params: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)


class WorldAdapter(ABC):
    """Base class for a world adapter (MASTER-SPEC §36)."""

    name: str = "adapter"
    SUPPORTED_ACTIONS: frozenset = frozenset()

    def supports(self, action: str) -> bool:
        return action in self.SUPPORTED_ACTIONS

    @abstractmethod
    def observe(self, request: WorldRequest) -> Any:
        """Read/observe the external world (no side effect)."""

    @abstractmethod
    def execute(self, request: WorldRequest) -> Any:
        """Act on the external world (side effect)."""


class FilesystemAdapter(WorldAdapter):
    """Read/write/list the local filesystem (locally testable)."""

    name = "filesystem"
    SUPPORTED_ACTIONS = frozenset({"read", "write", "list"})

    def observe(self, request: WorldRequest) -> Any:
        action = request.action
        path = request.params.get("path")
        if action == "read":
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        if action == "list":
            return os.listdir(path)
        raise ValueError(f"filesystem has no observe action {action!r}")

    @_observe("filesystem_adapter.execute")
    def execute(self, request: WorldRequest) -> Any:
        action = request.action
        path = request.params.get("path")
        if action == "write":
            content = request.params.get("content", "")
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            return {"written": path}
        raise ValueError(f"filesystem has no execute action {action!r}")


class ShellAdapter(WorldAdapter):
    """Run a shell command (locally testable)."""

    name = "shell"
    SUPPORTED_ACTIONS = frozenset({"run"})

    def observe(self, request: WorldRequest) -> Any:
        raise ValueError("shell adapter is execute-only")

    def execute(self, request: WorldRequest) -> Any:
        if request.action != "run":
            raise ValueError(f"shell has no execute action {request.action!r}")
        completed = subprocess.run(
            request.params.get("command", ""),
            shell=True,
            capture_output=True,
            text=True,
        )
        return {
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "returncode": completed.returncode,
        }


class WorldInterface:
    """§37 WORLD ACTION CONTRACT over registered adapters."""

    def __init__(
        self,
        adapters: Optional[List[WorldAdapter]] = None,
        authorize: Optional[Callable[[WorldRequest], bool]] = None,
        verify: Optional[Callable[[ActionResult], bool]] = None,
    ) -> None:
        self.adapters: Dict[str, WorldAdapter] = {
            a.name: a for a in (adapters or [])
        }
        # Optional policy gate (default allow) and result verifier (default: success).
        self._authorize_fn = authorize
        self._verify_fn = verify

    def register_adapter(self, adapter: WorldAdapter) -> None:
        self.adapters[adapter.name] = adapter

    # ------------------------------------------------------------- §37 contract
    def validate(self, request: WorldRequest) -> bool:
        """Validate the request: known adapter + supported action."""
        adapter = self.adapters.get(request.adapter)
        if adapter is None:
            return False
        return adapter.supports(request.action)

    def authorize(self, request: WorldRequest) -> bool:
        if self._authorize_fn is None:
            return True  # default allow (governed by injected policy otherwise)
        return self._authorize_fn(request)

    @_observe("world_interface.observe")
    def observe(self, request: WorldRequest) -> Dict[str, Any]:
        """validate -> authorize -> adapter.observe."""
        if not self.validate(request):
            return {"status": "invalid",
                    "error": f"unknown {request.adapter}.{request.action}"}
        if not self.authorize(request):
            return {"status": "denied"}
        try:
            data = self.adapters[request.adapter].observe(request)
            return {"status": "observed", "data": data}
        except Exception as exc:  # noqa: BLE001 - surface adapter error
            return {"status": "error", "error": str(exc)}

    @_observe("world_interface.execute")
    def execute(self, request: WorldRequest) -> ActionResult:
        """validate -> authorize -> adapter.execute -> ActionResult."""
        if not self.validate(request):
            return ActionResult(action_id=request.action, success=False,
                                error=f"unknown {request.adapter}.{request.action}")
        if not self.authorize(request):
            return ActionResult(action_id=request.action, success=False,
                                error="denied by policy")
        try:
            output = self.adapters[request.adapter].execute(request)
            return ActionResult(action_id=request.action, success=True, output=output)
        except Exception as exc:  # noqa: BLE001 - surface adapter error
            return ActionResult(action_id=request.action, success=False, error=str(exc))

    def verify(self, result: ActionResult) -> bool:
        if self._verify_fn is not None:
            return self._verify_fn(result)
        return result.success
