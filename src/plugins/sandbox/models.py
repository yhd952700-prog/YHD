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


#: exit_code 语义码 —— 表示「什么也没执行」。
#: 用 127（shell 的 "command not found"）是因为它准确传达「这次调用没有找到一个
#: 真正能执行命令的执行面」。旧实现此处返回 0（成功）属谎报成功：
#: 调用方拿到 exit_code=0 会认为命令跑完了，实际一个字节都没跑。
NOT_EXECUTED_EXIT_CODE = 127


def _not_executed(cls_name: str) -> Dict[str, Any]:
    """占位后端统一的「诚实失败」返回值。

    这两个后端（gVisor / Kata）在本仓库里**从未真正实现过**：没有 runsc、
    没有 kata-runtime，也不做任何 syscall 拦截。旧代码返回空成功的 dict，
    让整条 `PluginManager.execute_plugin_action()` 链路永远报告成功。
    """
    return {
        "stdout": "",
        "stderr": (
            f"{cls_name}.execute_command is a placeholder: nothing was executed. "
            "It does not spawn runsc/kata-runtime and performs no syscall interception. "
            "Use a real execution surface instead — see the Monty / Subprocess / "
            "gVisor implementations in src/plugins/sandbox/backends/."
        ),
        "exit_code": NOT_EXECUTED_EXIT_CODE,
        "executed": False,
    }


class gVisorBackend(SandboxBackend):
    """gVisor (runsc) 沙箱后端 —— **占位实现，不可用于真实隔离**。

    注意：本类不提供任何隔离。真正的、可被选择的 gVisor 后端在
    `src/plugins/sandbox/backends/gvisor.py`（那是 `SandboxBackendManager`
    会按可用性挑选的那一套）。保留本类仅为兼容旧调用点。
    """

    def __init__(self):
        self._containers: Dict[str, str] = {}  # plugin_id -> container_id

    async def create_container(self, plugin_id: str, limits: Dict[str, Any]) -> str:
        """使用 gVisor runsc 创建容器"""
        # In production: runsc --resource-limits="... create ..."
        container_id = f"gvisor-{plugin_id}-{id(self)}"
        self._containers[plugin_id] = container_id

        # NOTE: 下面两行是**死代码** —— 取值后立即丢弃，resource limits 从未被应用。
        # 保留原样 + 注释记录，因为删掉会改变行为却得不到新能力；真正的限制能力请走
        # backends/gvisor.py 那套。
        limits.get("cpu_quota", 50000)  # noqa: B018  (documented no-op)
        limits.get("memory_limit", 256 * 1024 * 1024)  # noqa: B018  (documented no-op)

        # Would run: runsc --cpu-quota=$cpu_quota --memory-limit=$memory_limit run ...
        # For now, just record the configuration
        return container_id

    async def execute_command(self, container_id: str, command: List[str]) -> Dict[str, Any]:
        """在 gVisor 容器中执行命令 —— 占位实现，实际不执行任何东西。"""
        return _not_executed(self.__class__.__name__)

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
        """在 Kata Containers VM 中执行命令 —— 占位实现，实际不执行任何东西。"""
        return _not_executed(self.__class__.__name__)

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

    Raise:
        ValueError: backend_type 不是受支持的取值。

    注：旧实现把任何非 "kata" 的字符串**静默**映射成 gVisorBackend()
    （拼错的 backend_type 也会拿到一个"看起来能用"的后端），且不做可用性校验。
    """
    if backend_type == "kata":
        return KataBackend()
    if backend_type == "gvisor":
        return gVisorBackend()
    raise ValueError(
        f"unknown sandbox backend type {backend_type!r}; "
        "supported values are 'gvisor' and 'kata'"
    )
