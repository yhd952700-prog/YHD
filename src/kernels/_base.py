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


class KernelNotServableError(KernelStateError):
    """内核处于 PAUSED / STOPPED，某个受门禁保护的操作被拒绝。

    与 :class:`KernelStateError` 同族（现有 409 映射因此继续成立），但单独成类
    是为了让「生命周期门禁拒绝」与「非法状态迁移」在日志与测试里可区分：前者是
    门禁**按设计生效**，后者是调用方**用错了状态**。
    """


#: 只有这两个状态真正关门。``UNINITIALIZED`` **不**关门 —— 见
#: :func:`is_servable` 的说明。
_NON_SERVABLE = (KernelLifecycle.PAUSED, KernelLifecycle.STOPPED)


def is_servable(kernel: object) -> bool:
    """该内核此刻是否应当接受受门禁保护的操作。

    只有 ``PAUSED`` / ``STOPPED`` 关门。``UNINITIALIZED`` **有意不关门**：
    仓库里大量代码直接 ``SecurityEngine()`` / ``AuditStore()`` 构造实例而从不调
    ``initialize()``（``tests/`` 有 100+ 处，生产也有 ``src/ai/network_gateway.py``
    这类直接构造），若把 ``UNINITIALIZED`` 也关掉，这些路径会在**从未被 pause 过**
    的情况下直接失败 —— 那是把门禁变成停产事故，而不是收紧语义。

    这个取舍是刻意的：``UNINITIALIZED`` 的诚实性由 ``/v1/kernels`` 的
    ``uninitialized_but_present`` 负责暴露（可观测），而门禁只负责
    ``PAUSED``/``STOPPED`` 这两个**有人明确下达**的状态（可执行）。
    """
    return getattr(kernel, "lifecycle", None) not in _NON_SERVABLE


def assert_servable(kernel: object, operation: str) -> None:
    """门禁：``PAUSED`` / ``STOPPED`` 时抛 :class:`KernelNotServableError`。

    供返回值形状无法承载「拒绝」语义的入口使用（写操作、无返回值操作）。

    **不要**在「返回值本身能表达拒绝」的入口用它 —— 那种场合应返回拒绝值并
    记录原因，否则调用方若宽泛地 catch 异常，会把「拒绝」变成「无主异常」，
    在 security / policy 这类内核上**方向恰好是 fail-open**。见
    ``SecurityEngine.check_rbac``（返回 DENY）与 ``NetworkBus.route``
    （标 FAILED）两种正确写法。
    """
    if not is_servable(kernel):
        raise KernelNotServableError(
            f"{type(kernel).__name__}.{operation} refused: kernel is "
            f"{getattr(kernel, 'lifecycle', None)}"
        )


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
