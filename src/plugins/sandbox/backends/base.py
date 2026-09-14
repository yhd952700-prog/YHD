"""
Sandbox Backend Abstract Interface for LiuHao AI OS

Defines the contract all sandbox backends must implement.
Supports: gVisor (runsc), Docker, subprocess fallback.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List
from enum import Enum


class SandboxBackendType(str, Enum):
    """Supported sandbox backend types."""
    GVISOR = "gvisor"        # Google gVisor (runsc)
    DOCKER = "docker"        # Docker containers
    SUBPROCESS = "subprocess"  # Process isolation fallback
    KATA = "kata"            # Kata Containers (future)
    # Rust 写的受限 Python 解释器（pydantic/monty），给 AI 生成的代码跑。
    # 保留 / S5 状态：**已登记但当前不可实例化** —— 详见 monty_backend.py 的探测逻辑。
    MONTY = "monty"
    # RestrictedPython：CPython 源码受限编译（ZPL-2.1）。见 restricted_python_backend.py。
    # 它提供的是**能力隔离**（无 import / open），**不是**资源隔离 —— 请勿对外宣称"沙箱"。
    RESTRICTED_PYTHON = "restricted_python"


class ExecutionStatus(str, Enum):
    """一次执行的**结果性质** —— `success: bool` 表达不了的那部分。

    `success` 只有两个值，于是「被拒绝」「不支持」「后端没装」全都被压成 False，
    与「真的跑了但失败」无法区分。鎏灏要的不是"执行失败"，而是"我们明确地、有
    理由地不执行" —— 这两件事的下游处理完全不同，压成一个布尔就是谎报。
    """
    SUCCESS = "success"              # 真的执行了，并成功
    FAILED = "failed"                # 真的执行了，但失败
    TIMEOUT = "timeout"              # 真的执行了，但超时
    REJECTED = "rejected"            # 判定为危险，拒绝执行（主动拦截）
    UNSUPPORTED = "unsupported"      # 这类操作本后端不做
    BACKEND_UNAVAILABLE = "backend_unavailable"  # 后端没装/不可用 —— 不是失败


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
        execution_time_limit: Optional[int] = None,  # seconds
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
        status: Optional["ExecutionStatus"] = None,
    ):
        self.execution_id = execution_id
        self.success = success
        # status 缺省由 success 推导，保持与历史调用点完全兼容；
        # 显式传入时以传入为准（例如拒绝/不可用这类"没发生执行"的结果）。
        if status is None:
            status = ExecutionStatus.SUCCESS if success else ExecutionStatus.FAILED
        elif isinstance(status, str):
            status = ExecutionStatus(status)
        self.status = status
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
            "status": self.status.value,
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
