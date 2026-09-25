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
import shlex
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from ..kernels.execution import ActionResult
from .observability import observe as _observe
from .audit import audited
from .workspace import resolve_in_workspace


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
    """Read/write/list the local filesystem (locally testable).

    ⚠️ 路径约束默认**关闭**（``root=None``）
    --------------------------------------
    保持历史行为：既有调用方（``e2e_demo`` / ``hardening`` / ``vhl_benchmark``）
    各自在 ``WorldInterface`` 的 ``authorize`` 回调里圈地，且那套圈地是
    **「只约束写、读一律放行」**。因此 ``root=None`` 时 ``read``/``list`` 可及全盘。

    **任何由 LLM / 工具调用驱动的使用都必须显式传 ``root``**：传入后
    ``read`` / ``list`` / ``write`` 一律先过 :func:`resolve_in_workspace`，
    越界即拒绝（fail-closed）。工具层（``src/ai/tools.py``）即按此构造；
    新增调用方时不要依赖默认值，那等于把闸门留空。
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
    @audited("p15.world.execute", module="src.ai.world_interface")
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
