"""
Docker Sandbox Backend for LiuHao AI OS

Uses Docker containers for plugin isolation.
"""

import subprocess
import shutil
import time
from typing import Dict, Any, Optional, List

from .base import (
    SandboxBackendBase,
    SandboxBackendType,
    SandboxBackendStatus,
    ResourceLimits,
    ExecutionResult,
)


class DockerBackend(SandboxBackendBase):
    """
    Docker container sandbox backend.

    Requirements:
    - Docker daemon running
    - 'docker' CLI in PATH
    """

    def __init__(self, image_name: str = "liuhao/sandbox:base", docker_cli: str = "docker"):
        self._docker_cli = shutil.which(docker_cli) or docker_cli
        self._image_name = image_name
        self._version_cache: Optional[str] = None

    @property
    def backend_type(self) -> SandboxBackendType:
        return SandboxBackendType.DOCKER

    @property
    def name(self) -> str:
        return "Docker"

    def is_available(self) -> bool:
        if not shutil.which(self._docker_cli):
            return False
        try:
            result = subprocess.run(
                [self._docker_cli, "info"],
                capture_output=True, text=True, timeout=5,
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return False

    def get_status(self) -> SandboxBackendStatus:
        if self.is_available():
            return SandboxBackendStatus.HEALTHY
        return SandboxBackendStatus.UNAVAILABLE

    def get_version(self) -> str:
        if self._version_cache:
            return self._version_cache
        try:
            result = subprocess.run(
                [self._docker_cli, "--version"],
                capture_output=True, text=True, timeout=5,
            )
            self._version_cache = result.stdout.strip()
            return self._version_cache
        except Exception:
            return "unknown"

    def get_backend_info(self) -> Dict[str, Any]:
        return {
            "type": self.backend_type.value,
            "name": self.name,
            "binary": self._docker_cli,
            "version": self.get_version(),
            "image": self._image_name,
            "available": self.is_available(),
            "status": self.get_status().value,
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
        start_time = time.time()
        limits = resource_limits or ResourceLimits()
        timeout = timeout or limits.execution_time_limit or 60

        container_name = f"liuhao_sandbox_{execution_id[:12]}"

        cmd = [
            self._docker_cli, "run",
            "--rm",
            "--name", container_name,
        ]

        # Network isolation
        if not limits.network_access:
            cmd.append("--network=none")

        # Read-only rootfs
        if limits.read_only_rootfs:
            cmd.append("--read-only")

        # Memory limit
        if limits.memory_limit:
            cmd.extend(["-m", str(limits.memory_limit)])

        # CPU limit
        if limits.cpu_limit:
            cmd.extend(["--cpus", str(limits.cpu_limit)])

        # PID limit
        if limits.max_pids:
            cmd.extend(["--pids-limit", str(limits.max_pids)])

        # Timeout
        cmd.extend(["--stop-timeout", str(timeout)])

        # Environment variables
        if environment:
            for k, v in environment.items():
                cmd.extend(["-e", f"{k}={v}"])

        # Volume mounts
        if volumes:
            for host_path, guest_path in volumes.items():
                cmd.extend(["-v", f"{host_path}:{guest_path}"])

        # Image and command
        cmd.append(self._image_name)
        cmd.extend(["--"])
        cmd.extend(command)

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout + 10,
                input=input_data,
            )
            elapsed = time.time() - start_time

            return ExecutionResult(
                execution_id=execution_id,
                success=proc.returncode == 0,
                exit_code=proc.returncode,
                stdout=proc.stdout,
                stderr=proc.stderr,
                execution_time=elapsed,
                resource_usage={
                    "execution_time": elapsed,
                    "exit_code": proc.returncode,
                },
                backend_info={
                    "backend": "docker",
                    "container": container_name,
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
                backend_info={"backend": "docker", "timeout": True},
            )
        except Exception as e:
            elapsed = time.time() - start_time
            return ExecutionResult(
                execution_id=execution_id,
                success=False,
                exit_code=-1,
                error=str(e),
                execution_time=elapsed,
                backend_info={"backend": "docker", "exception": type(e).__name__},
            )

    def cleanup(self, execution_id: str) -> bool:
        container_name = f"liuhao_sandbox_{execution_id[:12]}"
        try:
            subprocess.run(
                [self._docker_cli, "rm", "-f", container_name],
                capture_output=True, timeout=5,
            )
            return True
        except Exception:
            return False
