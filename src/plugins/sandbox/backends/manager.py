"""
Sandbox Backend Manager for LiuHao AI OS

Manages multiple sandbox backends with fallback chain.
Prioritizes gVisor > Docker > Subprocess.
"""

import logging
import time
from typing import Dict, Any, Optional, List

from .base import (
    ExecutionStatus,
    SandboxBackendBase,
    SandboxBackendType,
    SandboxBackendStatus,
    ResourceLimits,
    ExecutionResult,
)
from .gvisor import GVisorBackend
from .subprocess_backend import SubprocessBackend

logger = logging.getLogger(__name__)


class _BrokenMontyBackend(SandboxBackendBase):
    """Monty 后端**连构造都没完成**时占位的不可用后端。

    存在的唯一理由：让 manager 里始终有一个 MONTY 键，使
    `select_backend(MONTY, required=True)` 能报出"为什么不可用"，
    而不是"没有这个后端"。它保证：
      - is_available() 恒 False（绝不运行任何东西）
      - execute() 恒返回 BACKEND_UNAVAILABLE（绝不返回 success）
    """

    def __init__(
        self,
        reason: str,
        backend_type: SandboxBackendType = SandboxBackendType.MONTY,
    ) -> None:
        self._reason = f"backend failed to initialise: {reason}"
        # 类型必须由调用方传入。硬编码成 MONTY 会让坏掉的**其它**后端
        # 在 get_backend_info() 里谎报自己是 Monty —— 诊断信息一旦说谎，
        # 排查方向就被带偏了。
        self._backend_type = backend_type

    @property
    def backend_type(self) -> SandboxBackendType:  # type: ignore[override]
        return self._backend_type

    @property
    def name(self) -> str:
        return f"{self._backend_type.value} (broken)"

    def is_available(self) -> bool:
        return False

    def get_status(self) -> SandboxBackendStatus:
        return SandboxBackendStatus.UNAVAILABLE

    def get_backend_info(self) -> Dict[str, Any]:
        return {
            "backend": self._backend_type.value,
            "available": False,
            "reason": self._reason,
        }

    def execute(self, execution_id: str, *args: Any, **kwargs: Any) -> ExecutionResult:
        return ExecutionResult(
            execution_id=execution_id,
            success=False,
            error=self._reason,
            status=ExecutionStatus.BACKEND_UNAVAILABLE,
            backend_info=self.get_backend_info(),
        )

    def cleanup(self, execution_id: str) -> bool:
        return True

    def cleanup_all(self) -> None:
        return None


class SandboxBackendManager:
    """
    Manages sandbox backend lifecycle and execution routing.

    Features:
    - Backend auto-detection (gVisor, Docker, Subprocess)
    - Fallback chain when preferred backend is unavailable
    - Health monitoring for all backends
    - Resource limit enforcement
    - Execution result caching
    """

    def __init__(
        self,
        preferred_backends: Optional[List[SandboxBackendType]] = None,
        enable_docker: bool = True,
    ):
        """
        Args:
            preferred_backends: Ordered list of backend preference
            enable_docker: Whether to try Docker (if installed)
        """
        self._backends: Dict[SandboxBackendType, SandboxBackendBase] = {}
        self._active_backend: Optional[SandboxBackendBase] = None
        self._execution_history: Dict[str, ExecutionResult] = {}
        self._total_executions: int = 0
        self._start_times: Dict[str, float] = {}

        # Default preference order: gVisor > Docker > Subprocess
        if preferred_backends is None:
            self._preferred_order = [
                SandboxBackendType.GVISOR,
                SandboxBackendType.SUBPROCESS,
            ]
        else:
            self._preferred_order = list(preferred_backends)
            # Ensure subprocess is always available as fallback
            if SandboxBackendType.SUBPROCESS not in self._preferred_order:
                self._preferred_order.append(SandboxBackendType.SUBPROCESS)

        # Initialize backends
        self._init_backends(enable_docker)

    def _init_backends(self, enable_docker: bool) -> None:
        """Initialize available backends."""
        # gVisor
        gvisor = GVisorBackend()
        self._backends[SandboxBackendType.GVISOR] = gvisor

        # Subprocess fallback
        self._backends[SandboxBackendType.SUBPROCESS] = SubprocessBackend()

        # Docker (optional)
        if enable_docker:
            try:
                docker_backend = self._try_import_docker()
                if docker_backend:
                    self._backends[SandboxBackendType.DOCKER] = docker_backend
            except ImportError:
                pass  # Docker optional, skip if unavailable

        # Monty (optional, restricted execution surface for AI-generated code)
        # 与 docker 不同：这里注册**不会因为探测失败而跳过** —— MontyBackend 自身
        # 会把不可用原因存下来并由 is_available() 诚实回报，这样调用方能够拿到
        # "为什么没装"，而不是面对一个凭空消失的后端。
        self._backends[SandboxBackendType.MONTY] = self._try_import_monty()

        # RestrictedPython（可选，受限执行面）。同 Monty：注册失败也保留实例，
        # 由 is_available() 诚实回报原因。
        self._backends[SandboxBackendType.RESTRICTED_PYTHON] = (
            self._try_import_restricted_python()
        )

    def _try_import_restricted_python(self) -> Any:
        """构造 RestrictedPython 后端；失败也返回实例（携带原因），不返回 None。"""
        try:
            from .restricted_python_backend import RestrictedPythonBackend
            return RestrictedPythonBackend()
        except Exception as exc:  # noqa: BLE001 - 必须兜住，但把原因带出去
            logger.error("RestrictedPython backend failed to load: %s", exc)
            return _BrokenMontyBackend(
                str(exc), backend_type=SandboxBackendType.RESTRICTED_PYTHON
            )

    def _try_import_monty(self) -> Any:
        """构造 Monty 后端；任何失败都**包装成仍返回实例的不可用后端**，而非 None。

        返回 None 会让 select_backend 认为"压根没这个后端"，调用方从而无法区分
        "不存在" 与 "装了但坏着/没装" —— 信息就此丢失。
        """
        try:
            from .monty_backend import MontyBackend
            return MontyBackend()
        except Exception as exc:  # noqa: BLE001 - 必须兜住，但把原因带出去
            logger.error("Monty backend failed to load: %s", exc)
            return _BrokenMontyBackend(str(exc))

    def _try_import_docker(self):
        """Attempt to import and initialize Docker backend."""
        try:
            from .docker_backend import DockerBackend
            return DockerBackend()
        except (ImportError, Exception):
            return None

    def get_availability(self) -> Dict[str, Any]:
        """Get availability status of all backends."""
        result = {}
        for backend_type, backend in self._backends.items():
            result[backend_type.value] = {
                "name": backend.name,
                "available": backend.is_available(),
                "status": backend.get_status().value,
                "info": backend.get_backend_info(),
            }
        return result

    def select_backend(
        self,
        backend_type: Optional[SandboxBackendType] = None,
        required: bool = False,
    ) -> SandboxBackendBase:
        """
        Select the best available backend.

        Args:
            backend_type: If specified, only use this backend
            required: 为 True 且**指定后端不可用**时，宁可失败也**不降级**。
                默认 False 以保持历史行为 —— 但对执行 AI 生成代码的场景，
                调用方应当传 True，理由见下方 Warning。

        Returns:
            The selected (or active) backend

        Raises:
            RuntimeError: If no backend is available, or if `required` was set
                and the explicitly requested backend is not usable.

        ⚠️ 这里是本模块历史上最危险的一处（已修）：
        旧逻辑在"显式指定了某个后端但它不可用"时**没有任何 else / raise**，
        直接穿透到下面的 preferred_order 兜底 —— 于是
        `execute(..., backend_type=MONTY)` 在 monty 没装的情况下，会**静默落到
        subprocess 后端、以宿主完整权限执行本该受限的 AI 生成代码**，然后返回
        success=True。调用方唯一的线索是 backend_info 里的一行 backend 名字。
        把"我要隔离"说成"没隔离但我跑通了"，正是本项目清剿的谎报成功。
        """
        if backend_type is not None:
            if backend_type in self._backends:
                backend = self._backends[backend_type]
                if backend.is_available():
                    self._active_backend = backend
                    return backend

            if required:
                requested = backend_type.value
                if backend_type not in self._backends:
                    reason = "backend is not registered with this manager"
                else:
                    reason = ("backend is registered but reports itself unavailable "
                              "(see get_availability() for its reason)")
                raise RuntimeError(
                    f"Requested sandbox backend {requested!r} is unusable: {reason}. "
                    "Refusing to silently fall back to a different (potentially "
                    "less restrictive) backend because the caller asserted this "
                    "specific isolation guarantee. Pass required=False only if you "
                    "accept any backend."
                )

        # Try preferred backends in order
        for btype in self._preferred_order:
            if btype in self._backends:
                backend = self._backends[btype]
                if backend.is_available():
                    self._active_backend = backend
                    return backend

        # Fallback: any available backend
        for backend in self._backends.values():
            if backend.is_available():
                self._active_backend = backend
                return backend

        raise RuntimeError(
            "No sandbox backend available! "
            f"Attempted: {[b.value for b in self._preferred_order]}"
        )

    def get_active_backend(self) -> Optional[SandboxBackendBase]:
        """Get the currently active backend."""
        if self._active_backend is None:
            try:
                self._active_backend = self.select_backend()
            except RuntimeError:
                pass
        return self._active_backend

    def execute(
        self,
        execution_id: str,
        command: List[str],
        resource_limits: Optional[ResourceLimits] = None,
        environment: Optional[Dict[str, str]] = None,
        working_directory: Optional[str] = None,
        timeout: Optional[int] = None,
        input_data: Optional[str] = None,
        volumes: Optional[Dict[str, str]] = None,
        backend_type: Optional[SandboxBackendType] = None,
        required: bool = False,
    ) -> ExecutionResult:
        """
        Execute a command in the sandbox.

        Args:
            execution_id: Unique ID for tracking
            command: Command to execute as list of strings
            resource_limits: CPU/memory/time constraints
            environment: Env vars
            working_directory: Working dir
            timeout: Max execution time in seconds
            input_data: Stdin
            volumes: Volume mounts {host: guest}
            backend_type: Force specific backend (optional)
            required: True 时，指定的后端不可用则直接抛错，绝不静默换用别的后端
                      （语义见 select_backend 的 Warning）

        Returns:
            ExecutionResult
        """
        self._total_executions += 1
        self._start_times[execution_id] = time.time()

        backend = self.select_backend(backend_type, required=required)

        result = backend.execute(
            execution_id=execution_id,
            command=command,
            resource_limits=resource_limits,
            environment=environment,
            working_directory=working_directory,
            timeout=timeout,
            input_data=input_data,
            volumes=volumes,
        )

        # Cache result
        self._execution_history[execution_id] = result

        # Cleanup
        try:
            backend.cleanup(execution_id)
        except Exception:
            pass

        return result

    def get_execution_result(self, execution_id: str) -> Optional[ExecutionResult]:
        """Retrieve a cached execution result."""
        return self._execution_history.get(execution_id)

    def cleanup_all(self) -> None:
        """Clean up all backends."""
        for backend in self._backends.values():
            try:
                backend.cleanup_all()
            except Exception:
                pass

    def get_stats(self) -> Dict[str, Any]:
        """Get sandbox statistics."""
        backend_stats = {}
        for btype, backend in self._backends.items():
            status = backend.get_status()
            backend_stats[btype.value] = {
                "available": backend.is_available(),
                "status": status.value,
                "name": backend.name,
            }

        successful = sum(1 for r in self._execution_history.values() if r.success)
        failed = sum(1 for r in self._execution_history.values() if not r.success)

        return {
            "total_executions": self._total_executions,
            "cache_size": len(self._execution_history),
            "successful": successful,
            "failed": failed,
            "backends": backend_stats,
            "active_backend": (
                self._active_backend.backend_type.value
                if self._active_backend
                else None
            ),
        }


# Module-level singleton
_default_manager: Optional[SandboxBackendManager] = None


def get_sandbox_manager() -> SandboxBackendManager:
    """Get the default sandbox backend manager instance."""
    global _default_manager
    if _default_manager is None:
        _default_manager = SandboxBackendManager()
    return _default_manager
