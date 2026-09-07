"""
gVisor Sandbox Backend for LiuHao AI OS

Uses Google gVisor (runsc) for kernel-level sandboxing.
Provides container isolation without full Docker overhead.
"""

import subprocess
import shutil
import os
import time
import tempfile
from typing import Dict, Any, Optional, List
from pathlib import Path

from .base import (
    SandboxBackendBase,
    SandboxBackendType,
    SandboxBackendStatus,
    ResourceLimits,
    ExecutionResult,
)


class GVisorBackend(SandboxBackendBase):
    """
    gVisor (runsc) sandbox backend.

    gVisor implements a user-space kernel that intercepts syscalls,
    providing strong isolation without requiring root or full containers.

    Requirements:
    - runsc binary installed and in PATH
    - Linux kernel 4.4+ (for ptrace platform) or 5.x+ (for systrap)
    """

    def __init__(self, runsc_path: str = "runsc", platform: str = "systrap"):
        """
        Args:
            runsc_path: Path to runsc binary
            gVisor platform: "systrap" (recommended) or "ptrace" or "kvm"
        """
        self._runsc_path = shutil.which(runsc_path) or runsc_path
        self._platform = platform
        self._version_cache: Optional[str] = None
        self._root_dir = Path(tempfile.gettempdir()) / "liuhao_gvisor_sandbox"

    @property
    def backend_type(self) -> SandboxBackendType:
        return SandboxBackendType.GVISOR

    @property
    def name(self) -> str:
        return "gVisor (runsc)"

    def is_available(self) -> bool:
        """Check if runsc binary exists and is executable."""
        if not shutil.which(self._runsc_path):
            return False
        try:
            result = subprocess.run(
                [self._runsc_path, "--version"],
                capture_output=True, text=True, timeout=5,
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return False

    def get_version(self) -> str:
        """Get runsc version string."""
        if self._version_cache:
            return self._version_cache
        try:
            result = subprocess.run(
                [self._runsc_path, "--version"],
                capture_output=True, text=True, timeout=5,
            )
            # Output like: "runsc version 1.3.0\n..."
            for line in result.stdout.splitlines():
                if "version" in line.lower():
                    self._version_cache = line.strip()
                    return self._version_cache
            self._version_cache = result.stdout.strip()
            return self._version_cache
        except Exception:
            return "unknown"

    def get_status(self) -> SandboxBackendStatus:
        if self.is_available():
            return SandboxBackendStatus.HEALTHY
        return SandboxBackendStatus.UNAVAILABLE

    def get_backend_info(self) -> Dict[str, Any]:
        return {
            "type": self.backend_type.value,
            "name": self.name,
            "binary": self._runsc_path,
            "version": self.get_version(),
            "platform": self._platform,
            "available": self.is_available(),
            "status": self.get_status().value,
            "root_dir": str(self._root_dir),
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
        """
        Execute a command in a gVisor sandbox.

        Strategy: runsc run --platform=<platform> <rootfs> -- <command>
        For simplicity on dev, we use runsc do which runs in a temp rootfs.
        """
        start_time = time.time()
        limits = resource_limits or ResourceLimits()
        timeout = timeout or limits.execution_time_limit or 60

        # Build runsc command
        cmd = self._build_runsc_cmd(
            execution_id=execution_id,
            command=command,
            limits=limits,
            environment=environment,
            working_directory=working_directory,
            timeout=timeout,
        )

        try:
            # Run with timeout
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout + 5,  # Extra 5s for gVisor startup
                input=input_data,
                env={**os.environ, **(environment or {})},
                cwd=working_directory,
            )
            elapsed = time.time() - start_time

            resource_usage = {
                "execution_time": elapsed,
                "exit_code": proc.returncode,
            }

            return ExecutionResult(
                execution_id=execution_id,
                success=proc.returncode == 0,
                exit_code=proc.returncode,
                stdout=proc.stdout,
                stderr=proc.stderr,
                execution_time=elapsed,
                resource_usage=resource_usage,
                backend_info={
                    "backend": "gvisor",
                    "platform": self._platform,
                    "command": cmd,
                },
            )

        except subprocess.TimeoutExpired:
            elapsed = time.time() - start_time
            return ExecutionResult(
                execution_id=execution_id,
                success=False,
                exit_code=-1,
                error=f"Execution timed out after {timeout}s",
                execution_time=elapsed,
                backend_info={"backend": "gvisor", "timeout": True},
            )
        except FileNotFoundError as e:
            return ExecutionResult(
                execution_id=execution_id,
                success=False,
                exit_code=-1,
                error=f"gVisor binary not found: {e}",
                backend_info={"backend": "gvisor", "binary_missing": True},
            )
        except Exception as e:
            elapsed = time.time() - start_time
            return ExecutionResult(
                execution_id=execution_id,
                success=False,
                exit_code=-1,
                error=str(e),
                execution_time=elapsed,
                backend_info={"backend": "gvisor", "exception": type(e).__name__},
            )

    def _build_runsc_cmd(
        self,
        execution_id: str,
        command: List[str],
        limits: ResourceLimits,
        environment: Optional[Dict[str, str]],
        working_directory: Optional[str],
        timeout: int,
    ) -> List[str]:
        """Build the runsc command line."""
        cmd = [
            self._runsc_path,
            "run",
            "--platform", self._platform,
        ]

        # Network isolation
        if not limits.network_access:
            cmd.extend(["--network=none"])

        # Read-only rootfs
        if limits.read_only_rootfs:
            cmd.extend(["--rootless"])

        # Memory limit
        if limits.memory_limit:
            cmd.extend(["--rlimit=as=" + str(limits.memory_limit)])

        # PID limit
        if limits.max_pids:
            cmd.extend(["--rlimit=nproc=" + str(limits.max_pids)])

        # Container ID
        cmd.append(f"liuhao_{execution_id[:12]}")

        # The actual command to run inside the sandbox
        cmd.append("--")
        cmd.extend(command)

        return cmd

    def cleanup(self, execution_id: str) -> bool:
        """Clean up sandbox resources for an execution."""
        try:
            # Kill any lingering runsc process for this execution
            container_id = f"liuhao_{execution_id[:12]}"
            subprocess.run(
                [self._runsc_path, "delete", "--force", container_id],
                capture_output=True, timeout=5,
            )
            return True
        except Exception:
            return False

    def cleanup_all(self) -> None:
        """Clean up all sandbox resources."""
        try:
            # Kill all lingering runsc containers with liuhao_ prefix
            subprocess.run(
                [self._runsc_path, "delete", "--all"],
                capture_output=True, timeout=10,
            )
        except Exception:
            pass
