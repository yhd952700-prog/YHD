"""
Subprocess Sandbox Backend for LiuHao AI OS

Fallback sandbox using Python subprocess with resource limits.
Provides basic isolation when gVisor/Docker are unavailable.
"""

import subprocess
import os
import time
from typing import Dict, Any, Optional, List

# Cross-platform resource limit support
# 'resource' module is Unix-only; Windows uses psutil/process limits via subprocess
try:
    import resource as _unix_resource
    IS_UNIX = True
except ImportError:
    IS_UNIX = False
    _unix_resource = None

from .base import (
    SandboxBackendBase,
    SandboxBackendType,
    SandboxBackendStatus,
    ResourceLimits,
    ExecutionResult,
)


class SubprocessBackend(SandboxBackendBase):
    """
    Subprocess-based sandbox backend (fallback).

    Uses Python subprocess with resource limits and timeout.
    Provides basic isolation but NOT kernel-level security.
    Suitable for development and trusted code only.
    """

    def __init__(self):
        self._executions: Dict[str, subprocess.Popen] = {}

    @property
    def backend_type(self) -> SandboxBackendType:
        return SandboxBackendType.SUBPROCESS

    @property
    def name(self) -> str:
        return "Subprocess (fallback)"

    def is_available(self) -> bool:
        return True  # Always available (Python stdlib)

    def get_status(self) -> SandboxBackendStatus:
        return SandboxBackendStatus.HEALTHY

    def get_backend_info(self) -> Dict[str, Any]:
        return {
            "type": self.backend_type.value,
            "name": self.name,
            "available": True,
            "status": "healthy",
            "warning": "No kernel-level isolation. Use gVisor or Docker for production.",
            "active_executions": len(self._executions),
        }

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
    ) -> ExecutionResult:
        """Execute a command via subprocess with resource limits."""
        start_time = time.time()
        limits = resource_limits or ResourceLimits()
        timeout = timeout or limits.execution_time_limit or 60

        # Prepare environment
        env = os.environ.copy()
        if environment:
            env.update(environment)

        # Build resource limit function for preexec
        def _set_limits():
            # Only apply Unix resource limits (Unix-only module)
            if not IS_UNIX:
                return
            resource = _unix_resource
            # Memory limit (RLIMIT_AS)
            if limits.memory_limit:
                resource.setrlimit(
                    resource.RLIMIT_AS,
                    (limits.memory_limit, limits.memory_limit),
                )
            # CPU time limit (RLIMIT_CPU)
            if limits.execution_time_limit:
                resource.setrlimit(
                    resource.RLIMIT_CPU,
                    (limits.execution_time_limit, limits.execution_time_limit),
                )
            # PID limit (RLIMIT_NPROC)
            if limits.max_pids:
                resource.setrlimit(
                    resource.RLIMIT_NPROC,
                    (limits.max_pids, limits.max_pids),
                )
            # File size limit (RLIMIT_FSIZE) - cap output
            if limits.max_output_size:
                resource.setrlimit(
                    resource.RLIMIT_FSIZE,
                    (limits.max_output_size, limits.max_output_size),
                )

        try:
            proc = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.PIPE if input_data else None,
                text=True,
                cwd=working_directory,
                env=env,
                preexec_fn=_set_limits if os.name != 'nt' else None,
            )

            self._executions[execution_id] = proc

            try:
                stdout, stderr = proc.communicate(
                    input=input_data,
                    timeout=timeout,
                )
                elapsed = time.time() - start_time

                resource_usage = {
                    "execution_time": elapsed,
                    "exit_code": proc.returncode,
                }

                # Try to get memory usage (Unix only)
                try:
                    if IS_UNIX and _unix_resource:
                        usage = _unix_resource.getrusage(_unix_resource.RUSAGE_CHILDREN)
                        resource_usage["max_rss"] = usage.ru_maxrss
                except Exception:
                    pass

                return ExecutionResult(
                    execution_id=execution_id,
                    success=proc.returncode == 0,
                    exit_code=proc.returncode,
                    stdout=stdout,
                    stderr=stderr,
                    execution_time=elapsed,
                    resource_usage=resource_usage,
                    backend_info={
                        "backend": "subprocess",
                        "pid": proc.pid,
                    },
                )

            except subprocess.TimeoutExpired:
                proc.kill()
                stdout, stderr = proc.communicate()
                elapsed = time.time() - start_time
                return ExecutionResult(
                    execution_id=execution_id,
                    success=False,
                    exit_code=-1,
                    stdout=stdout or "",
                    stderr=stderr or "",
                    error=f"Execution timed out after {timeout}s",
                    execution_time=elapsed,
                    backend_info={"backend": "subprocess", "timeout": True},
                )
            finally:
                self._executions.pop(execution_id, None)

        except Exception as e:
            elapsed = time.time() - start_time
            return ExecutionResult(
                execution_id=execution_id,
                success=False,
                exit_code=-1,
                error=str(e),
                execution_time=elapsed,
                backend_info={"backend": "subprocess", "exception": type(e).__name__},
            )

    def cleanup(self, execution_id: str) -> bool:
        """Kill a running execution."""
        proc = self._executions.pop(execution_id, None)
        if proc and proc.poll() is None:
            try:
                proc.kill()
                proc.wait(timeout=5)
                return True
            except Exception:
                return False
        return True

    def cleanup_all(self) -> None:
        """Kill all running executions."""
        for execution_id in list(self._executions.keys()):
            try:
                self.cleanup(execution_id)
            except Exception:
                pass
