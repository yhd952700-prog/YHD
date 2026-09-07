"""
Sandbox Backend Manager for LiuHao AI OS

Manages multiple sandbox backends with fallback chain.
Prioritizes gVisor > Docker > Subprocess.
"""

import time
from typing import Dict, Any, Optional, List
from pathlib import Path

from .base import (
    SandboxBackendBase,
    SandboxBackendType,
    SandboxBackendStatus,
    ResourceLimits,
    ExecutionResult,
)
from .gvisor import GVisorBackend
from .subprocess_backend import SubprocessBackend


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
    ) -> SandboxBackendBase:
        """
        Select the best available backend.

        Args:
            backend_type: If specified, only use this backend

        Returns:
            The selected (or active) backend

        Raises:
            RuntimeError: If no backend is available
        """
        if backend_type is not None:
            if backend_type in self._backends:
                backend = self._backends[backend_type]
                if backend.is_available():
                    self._active_backend = backend
                    return backend

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

        Returns:
            ExecutionResult
        """
        self._total_executions += 1
        self._start_times[execution_id] = time.time()

        backend = self.select_backend(backend_type)

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
