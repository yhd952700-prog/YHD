"""LIUHAO X 内核标准化基座（Phase 2，增量引入）。

为全部 14 个 kernel 建立统一契约：
- ``KernelLifecycle``：标准生命周期状态机。
- ``KernelError`` 层次：标准异常族。
- ``Kernel`` (ABC)：可选基类，提供生命周期脚手架（opt-in，不改既有行为）。
- ``KernelInterface`` (Protocol)：结构性合规目标。

本模块**纯粹增量、导入安全**：不引入任何对既有 kernel 的运行时行为变更。
各 kernel 通过 ``from src.kernels._base import ...`` 选择性接入。所有注解均为惰性
（配合各 kernel 的 ``from __future__ import annotations``），不触发运行时求值。
"""
from __future__ import annotations

from enum import Enum
from typing import Protocol, runtime_checkable


class KernelLifecycle(Enum):
    """内核标准生命周期状态。"""

    UNINITIALIZED = "uninitialized"
    INITIALIZING = "initializing"
    READY = "ready"
    PAUSED = "paused"
    STOPPED = "stopped"
    ERROR = "error"


class KernelError(Exception):
    """所有内核级错误的基类。"""


class KernelNotInitializedError(KernelError):
    """在内核进入 READY 之前调用了需要就绪状态的操作。"""


class KernelStateError(KernelError):
    """非法生命周期状态转换。"""


class KernelPermissionError(KernelError):
    """动作被 policy / security 拒绝。"""


class KernelCapabilityError(KernelError):
    """缺少所需能力，或能力超出 scope。"""


class KernelConfigurationError(KernelError):
    """内核配置缺失或非法（env / store / 依赖）。"""


@runtime_checkable
class KernelInterface(Protocol):
    """每个内核管理器应当满足的结构性契约。

    规范文档 ``docs/kernel-spec/*.md`` 的「生命周期 / 错误处理 / 权限边界」章节
    必须与本 Protocol 对齐。
    """

    lifecycle: KernelLifecycle

    def initialize(self) -> None:
        """进入 READY（或幂等地确认已就绪）。"""
        ...

    def shutdown(self) -> None:
        """进入 STOPPED，释放资源。"""
        ...


class Kernel:
    """可选基类：提供标准化生命周期脚手架。

    既有 manager 类**可选择性**继承以免费获得 ``lifecycle`` 状态与
    no-op 的 ``initialize`` / ``shutdown`` / ``pause`` / ``resume`` 钩子。
    继承是可选的，且不改变既有公有方法签名与行为。
    """

    def __init__(self) -> None:
        self.lifecycle: KernelLifecycle = KernelLifecycle.UNINITIALIZED

    def initialize(self) -> None:
        self.lifecycle = KernelLifecycle.READY

    def shutdown(self) -> None:
        self.lifecycle = KernelLifecycle.STOPPED

    def pause(self) -> None:
        if self.lifecycle not in (KernelLifecycle.READY, KernelLifecycle.UNINITIALIZED):
            raise KernelStateError(f"cannot pause from {self.lifecycle}")
        self.lifecycle = KernelLifecycle.PAUSED

    def resume(self) -> None:
        if self.lifecycle is not KernelLifecycle.PAUSED:
            raise KernelStateError(f"cannot resume from {self.lifecycle}")
        self.lifecycle = KernelLifecycle.READY
