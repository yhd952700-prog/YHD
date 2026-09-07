"""
Sandbox Backend Abstract Interface for LiuHao AI OS

Defines the contract all sandbox backends must implement.
Supports: gVisor (runsc), Docker, subprocess fallback.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List
from datetime import datetime
from enum import Enum


class SandboxBackendType(str, Enum):
    """Supported sandbox backend types."""
    GVISOR = "gvisor"        # Google gVisor (runsc)
    DOCKER = "docker"        # Docker containers
    SUBPROCESS = "subprocess"  # Process isolation fallback
    KATA = "kata"            # Kata Containers (future)


class SandboxBackendStatus(str, Enum):
    """Backend health status."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class ResourceLimits:
    """Resource limits for sandbox execution."""

    def __init__(
        self,
        cpu_limit: Optional[float] = None,        # CPU cores
        memory_limit: Optional[int] = None,        # bytes
        execution_time_limit: Optional[int] = None, # seconds
        network_access: bool = False,
        file_system_access: bool = False,
        max_output_size: Optional[int] = None,     # bytes
        max_pids: Optional[int] = None,            # max processes
        tmpfs_size: Optional[int] = None,          # tmpfs in bytes
        read_only_rootfs: bool = True,
        allowed_syscalls: Optional[List[str]] = None,  # seccomp whitelist
    ):
        self.cpu_limit = cpu_limit
        self.memory_limit = memory_limit
        self.execution_time_limit = execution_time_limit
        self.network_access = network_access
        self.file_system_access = file_system_access
        self.max_output_size = max_output_size
        self.max_pids = max_pids
        self.tmpfs_size = tmpfs_size
        self.read_only_rootfs = read_only_rootfs
        self.allowed_syscalls = allowed_syscalls

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cpu_limit": self.cpu_limit,
            "memory_limit": self.memory_limit,
            "execution_time_limit": self.execution_time_limit,
            "network_access": self.network_access,
            "file_system_access": self.file_system_access,
            "max_output_size": self.max_output_size,
            "max_pids": self.max_pids,
            "tmpfs_size": self.tmpfs_size,
            "read_only_rootfs": self.read_only_rootfs,
            "allowed_syscalls": self.allowed_syscalls,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ResourceLimits":
        return cls(**{k: v for k, v in data.items() if k in cls.__init__.__code__.co_varnames})


class ExecutionResult:
    """Result of a sandbox execution."""

    def __init__(
        self,
        execution_id: str,
        success: bool,
        exit_code: Optional[int] = None,
        stdout: Optional[str] = None,
        stderr: Optional[str] = None,
        output: Optional[str] = None,
        error: Optional[str] = None,
        execution_time: Optional[float] = None,
        resource_usage: Optional[Dict[str, Any]] = None,
        backend_info: Optional[Dict[str, Any]] = None,
    ):
        self.execution_id = execution_id
        self.success = success
        self.exit_code = exit_code
        self.stdout = stdout or ""
        self.stderr = stderr or ""
        self.output = output or stdout or ""
        self.error = error
        self.execution_time = execution_time
        self.resource_usage = resource_usage or {}
        self.backend_info = backend_info or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "success": self.success,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "output": self.output,
            "error": self.error,
            "execution_time": self.execution_time,
            "resource_usage": self.resource_usage,
            "backend_info": self.backend_info,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ExecutionResult":
        return cls(**{k: v for k, v in data.items() if k in cls.__init__.__code__.co_varnames})


class SandboxBackendBase(ABC):
    """
    Abstract base class for all sandbox backends.

    Every backend must implement:
    - execute: Run a command in the sandbox
    - is_available: Check if the backend is usable
    - get_backend_info: Return metadata about this backend
    - cleanup: Tear down resources
    """

    @property
    @abstractmethod
    def backend_type(self) -> SandboxBackendType:
        """Return the backend type identifier."""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable backend name."""
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """
        Check if this backend is available on the current system.
        e.g., gVisor checks if 'runsc' binary exists and is executable.
        """
        ...

    @abstractmethod
    def get_status(self) -> SandboxBackendStatus:
        """Return health status of this backend."""
        ...

    @abstractmethod
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
        Execute a command inside the sandbox.

        Args:
            execution_id: Unique identifier for this execution
            command: Command to run as list of strings
            resource_limits: CPU/memory/time constraints
            environment: Environment variables
            working_directory: Working dir inside sandbox
            timeout: Max execution time in seconds
            input_data: Stdin data
            volumes: Host->Guest volume mounts {host_path: guest_path}

        Returns:
            ExecutionResult with stdout/stderr/status
        """
        ...

    @abstractmethod
    def cleanup(self, execution_id: str) -> bool:
        """
        Clean up resources for a completed execution.
        Returns True if cleanup succeeded.
        """
        ...

    @abstractmethod
    def get_backend_info(self) -> Dict[str, Any]:
        """
        Return metadata about this backend.
        Example: {"binary": "/usr/bin/runsc", "version": "1.0.0", "pid": 1234}
        """
        ...

    @abstractmethod
    def cleanup_all(self) -> None:
        """Clean up all resources."""
        ...

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} type={self.backend_type.value} name={self.name}>"
