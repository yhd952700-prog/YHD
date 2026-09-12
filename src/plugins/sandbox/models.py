"""SandBox backend models for liuhao AI OS plugins"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List
from enum import Enum
from datetime import datetime
from src._time import utc_now


class SandboxStatus(str, Enum):
    """Sandbox execution status"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class ResourceLimits:
    """Resource limits for sandbox execution."""

    def __init__(
        self,
        cpu_quota: Optional[int] = None,
        memory_limit: Optional[int] = None,
        pids_limit: Optional[int] = None,
        execution_time_limit: Optional[int] = None,
        network_access: bool = False,
    ):
        self.cpu_quota = cpu_quota
        self.memory_limit = memory_limit
        self.pids_limit = pids_limit
        self.execution_time_limit = execution_time_limit
        self.network_access = network_access


class SandboxExecutionContext:
    """Context for a sandbox execution."""

    def __init__(
        self,
        plugin_id: str,
        sandbox_id: str,
        limits: Optional[ResourceLimits] = None,
        metadata: Optional[Dict[str, Any]] = None,
        created_at: Optional[datetime] = None,
    ):
        self.plugin_id = plugin_id
        self.sandbox_id = sandbox_id
        self.limits = limits or ResourceLimits()
        self.metadata = metadata or {}
        self.created_at = created_at or utc_now()


class SandboxResult:
    """Result of a sandbox execution."""

    def __init__(
        self,
        sandbox_id: str,
        status: SandboxStatus,
        stdout: str = "",
        stderr: str = "",
        exit_code: int = 0,
        duration_ms: int = 0,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        self.sandbox_id = sandbox_id
        self.status = status
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code
        self.duration_ms = duration_ms
        self.metadata = metadata or {}


class SandboxBackend(ABC):
    """沙箱后端抽象基类"""

    @abstractmethod
    async def create_container(self, plugin_id: str, limits: Dict[str, Any]) -> str:
        """创建插件沙箱容器

        Args:
            plugin_id: 插件唯一标识
            limits: 资源限制字典 (cpu_quota, memory_limit, pids_limit 等)

        Returns:
            容器 ID
        """
        raise NotImplementedError

    @abstractmethod
    async def execute_command(self, container_id: str, command: List[str]) -> Dict[str, Any]:
        """在容器中执行命令

        Args:
            container_id: 容器 ID
            command: 要执行的命令列表

        Returns:
            执行结果 {stdout, stderr, exit_code}
        """
        raise NotImplementedError

    @abstractmethod
    async def destroy_container(self, container_id: str) -> None:
        """销毁沙箱容器"""
        raise NotImplementedError

    @abstractmethod
    async def get_resource_usage(self, container_id: str) -> Dict[str, Any]:
        """获取容器资源使用情况"""
        raise NotImplementedError


class gVisorBackend(SandboxBackend):
    """gVisor (runsc) 沙箱后端实现"""

    def __init__(self):
        self._containers: Dict[str, str] = {}  # plugin_id -> container_id

    async def create_container(self, plugin_id: str, limits: Dict[str, Any]) -> str:
        """使用 gVisor runsc 创建容器"""
        # In production: runsc --resource-limits="... create ..."
        container_id = f"gvisor-{plugin_id}-{id(self)}"
        self._containers[plugin_id] = container_id

        # Apply limits if available
        limits.get("cpu_quota", 50000)  # default 50% of 1 CPU
        limits.get("memory_limit", 256 * 1024 * 1024)  # 256MB default

        # Would run: runsc --cpu-quota=$cpu_quota --memory-limit=$memory_limit run ...
        # For now, just record the configuration
        return container_id

    async def execute_command(self, container_id: str, command: List[str]) -> Dict[str, Any]:
        """在 gVisor 容器中执行命令"""
        # Would run: runsc exec $container_id "$@"
        # For sandboxed execution, this would intercept syscalls
        return {
            "stdout": "",
            "stderr": "",
            "exit_code": 0
        }

    async def destroy_container(self, container_id: str) -> None:
        """销毁 gVisor 容器"""
        # Would run: runsc delete $container_id
        pass

    async def get_resource_usage(self, container_id: str) -> Dict[str, Any]:
        """获取 gVisor 容器资源使用情况"""
        return {"cpu_usage": 0, "memory_usage": 0, "pids_usage": 0}


class KataBackend(SandboxBackend):
    """Kata Containers 沙箱后端实现"""

    def __init__(self):
        self._containers: Dict[str, str] = {}

    async def create_container(self, plugin_id: str, limits: Dict[str, Any]) -> str:
        """使用 Kata Containers 创建 VM 隔离容器"""
        container_id = f"kata-{plugin_id}-{id(self)}"
        self._containers[plugin_id] = container_id
        return container_id

    async def execute_command(self, container_id: str, command: List[str]) -> Dict[str, Any]:
        """在 Kata Containers VM 中执行命令"""
        # Would use kata-runtime to execute
        return {
            "stdout": "",
            "stderr": "",
            "exit_code": 0
        }

    async def destroy_container(self, container_id: str) -> None:
        """销毁 Kata Containers VM"""
        pass

    async def get_resource_usage(self, container_id: str) -> Dict[str, Any]:
        """获取 Kata 容器资源使用情况"""
        return {"cpu_usage": 0, "memory_usage": 0, "pids_usage": 0}


# Factory function
def create_backend(backend_type: str = "gvisor") -> SandboxBackend:
    """创建指定类型的沙箱后端

    Args:
        backend_type: "gvisor" 或 "kata"

    Returns:
        SandboxBackend 实例
    """
    if backend_type == "kata":
        return KataBackend()
    return gVisorBackend()
