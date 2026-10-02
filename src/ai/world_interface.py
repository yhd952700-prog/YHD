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

GENUINE enforcement (as of p36-wi-safety)
-----------------------------------------
* **Shell actions are NOT executed by a direct ``subprocess.run``.** They are
  routed through the ``HostCommandBroker`` pipeline, which applies the global
  default-DENY enablement gate (``LIUHAO_HOST_COMMAND_ENABLED``), the capability
  / policy, human approval escalation, and fail-closed audit *before* any command
  runs. This closes the gap where ``ShellAdapter.execute`` bypassed the safe
  pipeline.
* **Filesystem scope is bound fail-closed for the autonomous actor.** An
  ``actor="autonomous"`` interface using ``FilesystemAdapter(root=None)`` is
  denied (full-disk exposure); a bounded ``root`` (or a human/legacy actor) keeps
  the historical behaviour, with ``resolve_in_workspace`` enforcing the boundary.
* The executor fence wrapper around non-shell actions provides EXECUTOR-IDENTITY
  fencing (anti-replay / anti-stale) when ``LIUHAO_EXECUTOR_FENCE`` is armed. It
  is intentionally NOT a world-action capability gate (``capabilities=()``); world
  authorization is enforced by ``authorize`` / the broker, not the fence.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from ..kernels.execution import ActionResult
from .observability import observe as _observe
from .audit import audited
from .workspace import resolve_in_workspace

#: No-op audit sink for the WorldInterface-internal shell broker. The action's
#: audit record is produced by the WorldInterface ``@audited`` wrapper, so the
#: broker does not double-write. Callers who want broker-level fail-closed audit
#: can inject a broker with a real sink via ``host_command_broker=``.
def _noop_shell_sink(event: str, details: Dict[str, Any]) -> None:
    return None


class _ShellBrokerDelegatePolicy:
    """Decision policy for the WorldInterface-internal host-command broker.

    Delegates the allow/deny decision to the WorldInterface's injected
    ``authorize`` policy (a human-in-the-loop policy), preserving the actor model:
    an autonomous interface stays default-deny unless its policy explicitly permits
    the shell action; a human interface is default-allow. The global enablement gate
    (``LIUHAO_HOST_COMMAND_ENABLED``) is enforced by the broker BEFORE this policy is
    consulted, so this policy can never override the gate.
    """

    def __init__(self, world_interface: "WorldInterface") -> None:
        self._wi = world_interface

    def evaluate(self, req, catalog):
        from .host_command.models import (
            DecisionOutcome,
            HostCommandDecision,
            HostCommandRequest,
        )

        wr = WorldRequest(
            adapter="shell",
            action="run",
            params={
                "command": req.command,
                "shell": req.use_shell,
                "cwd": req.cwd,
                "capabilities_required": list(req.capabilities_required or []),
            },
            correlation_id=req.correlation_id,
            metadata=dict(req.metadata or {}),
        )
        allowed = (
            self._wi._authorize_fn(wr)
            if self._wi._authorize_fn is not None
            else self._wi._actor != "autonomous"
        )
        if allowed:
            return HostCommandDecision(
                DecisionOutcome.ALLOW, "world-interface shell policy allows", req
            )
        return HostCommandDecision(
            DecisionOutcome.DENY, "world-interface shell policy denies", req
        )


@dataclass
class WorldRequest:
    """A request to observe or act on the external world."""
    adapter: str  # e.g. "filesystem", "shell"
    action: str   # e.g. "read", "write", "list", "run"
    params: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    correlation_id: Optional[str] = None  # D19-D21: fences a single action attempt


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
    """Read/write/list the local filesystem (locally testable).

    ⚠️ 路径约束默认**关闭**（``root=None``）
    --------------------------------------
    保持历史行为：既有调用方（``e2e_demo`` / ``hardening`` / ``vhl_benchmark``）
    各自在 ``WorldInterface`` 的 ``authorize`` 回调里圈地，且那套圈地是
    **「只约束写、读一律放行」**。因此 ``root=None`` 时 ``read``/``list`` 可及全盘。

    **任何由 LLM / 工具调用驱动的使用都必须显式传 ``root``**：传入后
    ``read`` / ``list`` / ``write`` 一律先过 :func:`resolve_in_workspace`，
    越界即拒绝（fail-closed）。    工具层（``src/ai/tools.py``）即按此构造；
    新增调用方时不要依赖默认值，那等于把闸门留空。

    ⚠️  autonomous actor 的 fail-closed：自 p36-wi-safety 起，``actor="autonomous"``
    的 ``WorldInterface`` 若使用 ``FilesystemAdapter(root=None)``（不设限），
    ``WorldInterface.authorize`` 会直接拒绝其 ``read``/``list``/``write`` —— 否则
    自主智能体可读写整块磁盘。仅 ``human`` actor 或显式 ``root`` 保留历史行为。
    """

    name = "filesystem"
    SUPPORTED_ACTIONS = frozenset({"read", "write", "list"})

    def __init__(self, root: Optional[str] = None) -> None:
        self.root = root

    def _resolve(self, path: Any) -> str:
        """收敛入参路径；``root`` 未设时原样返回（历史行为，便于既有测试）。"""
        if self.root is None:
            return str(path)
        return resolve_in_workspace(str(path), self.root)

    def observe(self, request: WorldRequest) -> Any:
        action = request.action
        path = self._resolve(request.params.get("path"))
        if action == "read":
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        if action == "list":
            return os.listdir(path)
        raise ValueError(f"filesystem has no observe action {action!r}")

    @_observe("filesystem_adapter.execute")
    def execute(self, request: WorldRequest) -> Any:
        action = request.action
        path = self._resolve(request.params.get("path"))
        if action == "write":
            content = request.params.get("content", "")
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            return {"written": path}
        raise ValueError(f"filesystem has no execute action {action!r}")


class ShellAdapter(WorldAdapter):
    """Run a command against the outside world (locally testable).

    执行契约（2026-09-13 收紧）：**默认不经 shell**，入参字符串先走 ``shlex``
    分词再以 argv 列表执行，从而关闭命令注入面 —— 旧实现把整串command
    直接拼进 shell 命令行，而该字符串在 autonomous 场景下由上层决策产生，
    属于真实的命令注入面。

    若确实需要管道 / 重定向 / glob 等必须由 shell 解释的语法，调用方须显式
    传 ``params={"shell": True}``：这等于明确接管注入风险，因此在 autonomous
    路径上还应再过一层人工授权（见 ``WorldInterface`` 的 ``authorize`` 回调）。

    注意：``WorldInterface`` 自 p36-wi-safety 起不再直接调用本方法执行命令，而是
    经由 ``HostCommandBroker`` 管线（全局默认-DENY 的 ``LIUHAO_HOST_COMMAND_ENABLED``
    闸门 + 策略 + 人工审批 + fail-closed 审计）派发；本 ``ShellAdapter.execute`` 仅作为
    该管线内部 executor 的真实执行体。也就是说，裸 ``ShellAdapter.execute`` 仍会直接
    跑命令（单测可用），但任何经 ``WorldInterface`` 的 shell 动作都先过闸门。
    """

    name = "shell"
    SUPPORTED_ACTIONS = frozenset({"run"})

    def observe(self, request: WorldRequest) -> Any:
        raise ValueError("shell adapter is execute-only")

    def execute(self, request: WorldRequest) -> Any:
        if request.action != "run":
            raise ValueError(f"shell has no execute action {request.action!r}")
        command = request.params.get("command", "")
        use_shell = bool(request.params.get("shell", False))
        if use_shell:
            argv: Any = command
        else:
            argv = shlex.split(command)
            if not argv:
                raise ValueError("shell run requires a non-empty command")
        completed = subprocess.run(
            argv,
            # nosec B602  # shell=True 仅当调用方显式传 params={"shell": True} 时触发；
            # 默认走 shlex.split 分词、不经 shell，命令注入面已关闭。Autonomous
            # 路径还需经 authorize 回调授权（见 ShellAdapter 类 docstring 契约）。
            # 属有意为之的 opt-in 能力，非意外漏洞 —— 故精确抑制此单行，而非整类跳过。
            shell=use_shell,
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
        *,
        actor: str = "human",
        host_command_broker: Optional["HostCommandBroker"] = None,
    ) -> None:
        self.adapters: Dict[str, WorldAdapter] = {
            a.name: a for a in (adapters or [])
        }
        # Optional explicit host-command broker used for shell actions. When
        # provided, shell dispatch is delegated to it verbatim (caller owns the
        # gate/policy/audit wiring). When None, a default broker is built lazily
        # that enforces the global enablement gate + this interface's authorize
        # policy (see _ensure_shell_broker).
        self._host_command_broker = host_command_broker
        # §37 actor model. ``"human"`` keeps the historical default-allow
        # policy gate (human sovereignty). ``"autonomous"`` flips the default
        # to DENY and additionally blocks host-command (shell=True) execution
        # unless an explicit human-arming policy is injected (see authorize()).
        if actor not in ("human", "autonomous"):
            raise ValueError(
                f"actor must be 'human' or 'autonomous', got {actor!r}"
            )
        self._actor = actor
        # Policy gate. For a human actor the gate is default-allow (sovereignty);
        # for an autonomous actor it is default-deny and host-command
        # (shell=True) execution is blocked unless an explicit human-arming
        # policy allows it.
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
        """§37 policy gate — GENUINE, fail-closed where required.

        Shell actions (global default-DENY host-command gate)
        ------------------------------------------------------
        Shell requires the global enablement gate ``LIUHAO_HOST_COMMAND_ENABLED``
        to be armed — the SAME gate the ``HostCommandBroker`` uses. When the gate
        is OFF the shell action is denied regardless of actor. When ON, the
        decision follows the injected policy:
          * ``actor="autonomous"`` + ``shell=True`` (real shell, injection surface
            re-opened) still requires the policy to explicitly arm it — this keeps
            AND extends the prior block (the gate plus a human-in-the-loop
            ``authorize`` are both required).
          * otherwise the injected ``authorize`` policy decides; an autonomous
            interface with no policy is default-deny, a human interface is
            default-allow (human sovereignty).
        In ``WorldInterface.execute`` shell is routed through the broker, which
        re-applies this gate; the two layers agree (defense in depth).

        Filesystem scope (autonomous fail-closed)
        -----------------------------------------
        An ``actor="autonomous"`` interface may NOT reach an UNBOUNDED filesystem
        (``FilesystemAdapter(root=None)``): ``read``/``list``/``write`` would
        otherwise hit the entire disk. Such a request is DENIED fail-closed. A
        bounded ``root`` is itself the safety boundary, so it is allowed (and
        ``resolve_in_workspace`` enforces it). A human/legacy actor keeps the
        historical unbounded behaviour.

        Note: world-action authorization is enforced HERE (and by the broker for
        shell), NOT by the executor fence. The fence only provides executor-identity
        fencing — do not treat it as a capability gate.
        """
        # ---- Shell: global default-DENY host-command gate (same as broker) ----
        if request.adapter == "shell":
            from .host_command.enablement import is_enabled

            if not is_enabled():
                return False  # global default-DENY gate OFF => shell denied.
            if self._actor == "autonomous" and request.params.get("shell"):
                # autonomous raw-shell requires explicit human arming (keep+extend).
                if self._authorize_fn is None or not self._authorize_fn(request):
                    return False
                return True
            # any other shell action falls through to the injected policy below.

        # ---- Filesystem scope (autonomous fail-closed) ----
        if request.adapter == "filesystem":
            adapter = self.adapters.get(request.adapter)
            if isinstance(adapter, FilesystemAdapter):
                if adapter.root is None:
                    if self._actor == "autonomous":
                        # Full-disk exposure for an autonomous actor: deny.
                        return False
                    # human/legacy unbounded root: historical default-allow.
                else:
                    # A bounded root IS the safety boundary; allow unless a
                    # policy explicitly restricts it.
                    if self._authorize_fn is None:
                        return True

        # ---- General policy ----
        if self._authorize_fn is None:
            return self._actor != "autonomous"
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
    @audited("p15.world.execute", module="src.ai.world_interface")
    def execute(self, request: WorldRequest) -> ActionResult:
        """validate -> authorize -> [shell: HostCommandBroker] -> adapter.execute.

        Shell actions are routed through the ``HostCommandBroker`` pipeline (global
        default-DENY enablement gate + policy + approval + fail-closed audit), NOT
        run by a direct ``subprocess.run``. Filesystem/other adapters go through the
        executor fence below.

        The executor fence wrapper around non-shell actions provides EXECUTOR-IDENTITY
        fencing (anti-replay / anti-stale) when ``LIUHAO_EXECUTOR_FENCE`` is armed. It
        is intentionally NOT a world-action capability gate: ``capabilities=()`` is an
        empty grant, so the lease's capability check is a no-op (``set(()) ⊆ set(())``
        is always True). World-action authorization is enforced by ``authorize`` / the
        broker — NOT by this fence. The wrapper is kept so that when the fence gate is
        armed every action still acquires a valid executor identity (required by the
        central @kernel_action gate); it simply does not add capability authorization.
        """
        if not self.validate(request):
            return ActionResult(action_id=request.action, success=False,
                                error=f"unknown {request.adapter}.{request.action}")
        if request.adapter == "shell":
            return self._execute_via_host_command_broker(request)
        from src.kernels.execution.fence import (
            ExecutorFenceDenied,
            executor_session,
        )

        try:
            with executor_session(
                action=f"world.{request.adapter}.{request.action}",
                capabilities=(),
                ttl_sec=30.0,
            ):
                return self._execute_fenced(request)
        except ExecutorFenceDenied as exc:
            return ActionResult(action_id=request.action, success=False,
                                error=f"executor fence denied (fail-closed): {exc}")

    def _execute_fenced(self, request: WorldRequest) -> ActionResult:
        # D19-D21 defense-in-depth: if an executor fence context is bound on the
        # call stack, re-validate its IDENTITY (token / expiry / liveness / epoch /
        # replay). NOTE: this is executor-identity fencing only -- the capability
        # argument is () so the lease's capability check is a no-op. World-action
        # authorization is enforced by authorize() (called below), not by this fence.
        _fctx = None
        try:
            from src.kernels.execution.fence import (
                ExecutorFenceDenied,
                current_executor_fence,
                get_executor_fence,
            )

            _fctx = current_executor_fence()
            if _fctx is not None:
                get_executor_fence().enforce(
                    _fctx,
                    f"world.{request.adapter}.{request.action}",
                    (),
                    correlation_id=request.correlation_id or _fctx.executor_id,
                )
        except ExecutorFenceDenied as exc:
            return ActionResult(action_id=request.action, success=False,
                                error=f"executor fence denied: {exc}")
        if not self.authorize(request):
            return ActionResult(action_id=request.action, success=False,
                                error="denied by policy")
        try:
            output = self.adapters[request.adapter].execute(request)
            return ActionResult(action_id=request.action, success=True, output=output)
        except Exception as exc:  # noqa: BLE001 - surface adapter error
            return ActionResult(action_id=request.action, success=False, error=str(exc))

    # --------------------------------------------------------------------- #
    # Shell routing through the genuine HostCommandBroker pipeline
    # --------------------------------------------------------------------- #
    def _execute_via_host_command_broker(self, request: WorldRequest) -> ActionResult:
        """Route a shell action through the HostCommandBroker pipeline.

        The broker enforces the global default-DENY enablement gate
        (``LIUHAO_HOST_COMMAND_ENABLED``), the capability/policy, human approval
        escalation, and fail-closed audit BEFORE any command runs. This replaces
        the previous direct ``subprocess.run`` path in ``ShellAdapter`` that
        bypassed the safe pipeline. The action's audit record is produced by this
        ``WorldInterface``'s ``@audited`` wrapper; the broker's own audit sink is a
        no-op here to avoid double-writes (inject a broker with a real sink via
        ``host_command_broker=`` for broker-level fail-closed audit).
        """
        from .host_command.models import DecisionOutcome

        broker = self._ensure_shell_broker()
        hcr = self._world_to_host_cmd(request)
        decision = broker.submit(hcr)
        captured = getattr(broker, "_shell_captured", {})
        if decision.outcome is DecisionOutcome.ALLOW:
            return ActionResult(
                action_id=request.action, success=True,
                output=captured.get("result"),
            )
        return ActionResult(
            action_id=request.action, success=False, error=decision.reason
        )

    def _ensure_shell_broker(self):
        """Lazily build the default host-command broker for shell dispatch.

        Reuses the broker's real pipeline (gate + policy + approval + audit). The
        decision policy delegates to this interface's injected ``authorize`` (a
        human-in-the-loop policy), preserving the actor model: an autonomous
        interface stays default-deny unless its policy explicitly permits the
        shell action; a human interface is default-allow. If a broker was injected
        at construction it is used verbatim instead.
        """
        if self._host_command_broker is not None:
            return self._host_command_broker
        from .host_command.approval import ApprovalInterface
        from .host_command.broker import HostCommandBroker
        from .host_command.capability import CapabilityCatalog

        captured: dict = {}

        def _executor(req):
            result = self._shell_run(req)
            captured["result"] = result
            return result

        broker = HostCommandBroker(
            catalog=CapabilityCatalog(),
            policy=_ShellBrokerDelegatePolicy(self),
            approvals=ApprovalInterface(),
            executor=_executor,
            simulate=False,
            event_sink=_noop_shell_sink,
        )
        # Attribute used by _execute_via_host_command_broker to recover output.
        broker._shell_captured = captured  # type: ignore[attr-defined]
        self._host_command_broker = broker
        return broker

    @staticmethod
    def _shell_run(req) -> dict:
        """Real shell execution used by the broker's executor (subprocess)."""
        command = req.command
        use_shell = bool(req.use_shell)
        if use_shell:
            argv = command
        else:
            argv = shlex.split(command)
            if not argv:
                raise ValueError("shell run requires a non-empty command")
        completed = subprocess.run(
            argv,
            shell=use_shell,
            capture_output=True,
            text=True,
        )
        return {
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "returncode": completed.returncode,
        }

    def _world_to_host_cmd(self, request) -> "HostCommandRequest":
        from .host_command.models import HostCommandRequest

        return HostCommandRequest(
            command=request.params.get("command", ""),
            actor=self._actor,
            adapter="shell",
            use_shell=bool(request.params.get("shell", False)),
            cwd=request.params.get("cwd"),
            capabilities_required=list(
                request.params.get("capabilities_required", []) or []
            ),
            correlation_id=request.correlation_id,
            metadata=dict(request.metadata or {}),
        )

    def verify(self, result: ActionResult) -> bool:
        if self._verify_fn is not None:
            return self._verify_fn(result)
        return result.success
