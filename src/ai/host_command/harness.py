"""U1 — test harness and executor adapters for host-command.

Helpers for tests and for wiring a concrete world adapter (e.g. ``WorldInterface``)
into the broker without coupling the broker to any specific executor.

The broker takes a ``CommandExecutor`` (request -> result dict). ``WorldInterface``
is the natural production executor; :func:`world_interface_executor` adapts it.
Tests use :class:`FakeExecutor` and :func:`build_test_broker` to stay hermetic.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from .approval import ApprovalInterface
from .broker import CommandExecutor, HostCommandBroker
from .capability import CapabilityCatalog, HostCommandCapability
from .models import HostCommandRequest
from .policy import CapabilityBasedPolicy


class FakeExecutor:
    """Records calls, returns a scripted result. Hermetic stand-in for a real executor."""

    def __init__(self, result: Optional[Dict[str, Any]] = None) -> None:
        self.calls: List[HostCommandRequest] = []
        self.result: Dict[str, Any] = result if result is not None else {
            "stdout": "", "stderr": "", "returncode": 0,
        }

    def __call__(self, req: HostCommandRequest) -> Dict[str, Any]:
        self.calls.append(req)
        return self.result


def build_test_broker(
    *,
    enabled: bool = True,
    capabilities: tuple = (),
    approvals: Optional[ApprovalInterface] = None,
    executor: Optional[CommandExecutor] = None,
    simulate: bool = False,
    event_sink: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    fail_closed_audit: bool = True,
) -> HostCommandBroker:
    """Build a broker for tests with explicit capabilities (bypasses env gate)."""
    catalog = CapabilityCatalog()
    for cap in capabilities:
        catalog.register(cap)
    # Force the capability policy so tests don't depend on the env var.
    policy = CapabilityBasedPolicy() if enabled else None
    broker = HostCommandBroker(
        catalog=catalog,
        policy=policy,
        approvals=approvals,
        executor=executor,
        simulate=simulate,
        event_sink=event_sink,
        fail_closed_audit=fail_closed_audit,
    )
    return broker


def world_interface_executor(wi: "object") -> CommandExecutor:
    """Adapt a ``WorldInterface`` instance into a ``CommandExecutor``.

    Converts a :class:`~src.ai.host_command.models.HostCommandRequest` to a
    ``WorldRequest`` and forwards it through ``WorldInterface.execute``. The
    broker's pipeline (gate + policy + approval) runs *before* this executor is
    ever called, so ``WorldInterface`` only sees already-authorized requests.
    """
    from ..world_interface import WorldRequest

    def _exec(req: HostCommandRequest) -> Dict[str, Any]:
        world_req = WorldRequest(
            adapter=req.adapter,
            action="run" if req.adapter == "shell" else (req.metadata.get("action", "read")),
            params={
                "command": req.command,
                "shell": req.use_shell,
                "path": req.cwd or req.metadata.get("path"),
            },
            metadata=req.metadata,
        )
        result = wi.execute(world_req)
        # ActionResult -> plain dict for uniform broker handling.
        if hasattr(result, "to_dict"):
            return result.to_dict()
        return {"output": result, "success": True}

    return _exec
