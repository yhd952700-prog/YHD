"""LIUHAO X 14 内核生命周期注册表（只读探测 + 显式驱动，零行为变更）。

本模块是「生命周期协议」缺失的驱动方：它让 ``lifecycle`` 指示灯变成**可观测、
可被真实驱动**的，而不是一个永远停在 ``UNINITIALIZED`` 的谎言。

三条硬约束（对应任务要求）在这里被严格满足：

1. **绝不急切实例化内核**。注册表只*读取*各 kernel 模块里的模块级私有全局
   变量（例如 ``security._global_security``），**绝不**调用会顺手造出实例的
   ``get_*()`` getter。这些私有变量在模块导入时默认是 ``None``，只有在别处的
   代码（通常是请求路径上第一次 ``get_*()``）真正构造了实例后才会非空。因此
   注册表报告的永远是「已经存在」的真相，查询它**不会**改变任何运行时状态。

2. **对 12 个单例，必须「不创建地」探测是否存在**。读取的是模块私有状态
   （``getattr(module, "_global_security", None)``），不是会创建实例的 getter。
   下面的 ``_PRIVATE_VAR``    清单里每个变量名都经过
   ``grep -n "^_[a-z_]*: Optional\\[" src/kernels/<k>/__init__.py`` 实测核实。

3. **诚实表示「没有规范实例」**。**context** / **execution** 只有工厂
   （``create_context_kernel`` / ``create_execution_engine``），**没有**规范单例。
   注册表里它们的状态被显式表示为「无规范实例」（``lifecycle=None`` + ``reason``），
   **不伪造成** ``UNINITIALIZED`` / ``READY``。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from src.kernels._base import KernelLifecycle

# 每个内核模块的导入：仅为了读取其模块级私有状态与类名，**不会**在导入时构造
# 实例（各 ``_global_*`` / ``_audit_store`` 默认是 ``None``）。
import src.kernels.audit as _audit
import src.kernels.capability as _capability
import src.kernels.context as _context
import src.kernels.evaluation as _evaluation
import src.kernels.event as _event
import src.kernels.execution as _execution
import src.kernels.identity as _identity
import src.kernels.memory as _memory
import src.kernels.network as _network
import src.kernels.plugin as _plugin
import src.kernels.policy as _policy
import src.kernels.resource as _resource
import src.kernels.security as _security
import src.kernels.trust as _trust


@dataclass(frozen=True)
class KernelEntry:
    """单个内核在注册表里的元数据。

    ``accessor`` 必须返回「当前已存在的实例」，不存在时返回 ``None``，**绝不能**
    调用会创建实例的 getter。它读取的是模块私有全局变量，不是工厂。

    ``module`` / ``var_name`` 仅用于测试能确定性地把私有变量重置回干净状态
    （设为 ``None``）；对 context / execution 为 ``None``，因为它们没有规范单例。
    """

    name: str
    target_class: type
    module: Any  # 持有私有全局变量的 kernel 模块；无规范实例时为 None
    var_name: Optional[str]  # 模块级私有全局变量名；无规范实例时为 None
    accessor: Callable[[], Any]  # 返回实例或 None，不得创建
    reason: str = ""  # 仅当「无规范实例」时填写（context / execution）


def _read_private(module: Any, varname: str) -> Any:
    """读取模块级私有全局变量——「不创建地探测」的核心。

    直接 ``getattr`` 模块上的私有变量。该变量只有在别处真正构造过实例后才非
    ``None``；调用它本身**不会**触发任何构造逻辑，因此不会改变运行时行为。
    """
    return getattr(module, varname, None)


# 12 个惰性单例：协定的私有全局变量名（实测核实）。
# 注意 ``memory`` 模块里还有一个 ``_tier_manager``，但规范单例是 ``_global_kernel``，
# 这里只读后者。
_PRIVATE_VAR = {
    "audit": ("_audit_store",),
    "capability": ("_global_registry",),
    "evaluation": ("_global_evaluator",),
    "event": ("_global_bus",),
    "identity": ("_global_manager",),
    "memory": ("_global_kernel",),
    "network": ("_global_bus",),
    "plugin": ("_global_plugin_registry",),
    "policy": ("_global_engine",),
    "resource": ("_global_manager",),
    "security": ("_global_security",),
    "trust": ("_global_manager",),
}

# 14 项注册表（顺序即内核名顺序，稳定可测）。
KERNEL_ENTRIES: List[KernelEntry] = [
    KernelEntry(
        "audit",
        _audit.AuditStore,
        _audit,
        "_audit_store",
        lambda: _read_private(_audit, "_audit_store"),
    ),
    KernelEntry(
        "capability",
        _capability.CapabilityRegistry,
        _capability,
        "_global_registry",
        lambda: _read_private(_capability, "_global_registry"),
    ),
    KernelEntry(
        "evaluation",
        _evaluation.Evaluator,
        _evaluation,
        "_global_evaluator",
        lambda: _read_private(_evaluation, "_global_evaluator"),
    ),
    KernelEntry(
        "event",
        _event.EventBus,
        _event,
        "_global_bus",
        lambda: _read_private(_event, "_global_bus"),
    ),
    KernelEntry(
        "identity",
        _identity.IdentityManager,
        _identity,
        "_global_manager",
        lambda: _read_private(_identity, "_global_manager"),
    ),
    KernelEntry(
        "memory",
        _memory.MemoryKernel,
        _memory,
        "_global_kernel",
        lambda: _read_private(_memory, "_global_kernel"),
    ),
    KernelEntry(
        "network",
        _network.NetworkBus,
        _network,
        "_global_bus",
        lambda: _read_private(_network, "_global_bus"),
    ),
    KernelEntry(
        "plugin",
        _plugin.PluginRegistry,
        _plugin,
        "_global_plugin_registry",
        lambda: _read_private(_plugin, "_global_plugin_registry"),
    ),
    KernelEntry(
        "policy",
        _policy.PolicyEngine,
        _policy,
        "_global_engine",
        lambda: _read_private(_policy, "_global_engine"),
    ),
    KernelEntry(
        "resource",
        _resource.ResourceQuotaManager,
        _resource,
        "_global_manager",
        lambda: _read_private(_resource, "_global_manager"),
    ),
    KernelEntry(
        "security",
        _security.SecurityEngine,
        _security,
        "_global_security",
        lambda: _read_private(_security, "_global_security"),
    ),
    KernelEntry(
        "trust",
        _trust.TrustManager,
        _trust,
        "_global_manager",
        lambda: _read_private(_trust, "_global_manager"),
    ),
    # context / execution：只有工厂，没有规范单例。accessor 恒返回 None，并诚实
    # 注明原因——绝不伪造成 UNINITIALIZED / READY。
    KernelEntry(
        "context",
        _context.ContextKernel,
        None,
        None,
        lambda: None,
        reason=(
            "factory-only: 只有 create_context_kernel(...) 工厂，没有进程级规范"
            "单例；且不应在启动时批量实例化（会触发其构造副作用）。"
        ),
    ),
    KernelEntry(
        "execution",
        _execution.ExecutionEngine,
        None,
        None,
        lambda: None,
        reason=(
            "factory-only: 只有 create_execution_engine(...) 工厂，没有进程级规范"
            "单例；且不应在启动时批量实例化（会触发其构造副作用）。"
        ),
    ),
]

# 反向索引，便于按名查找。
_ENTRY_BY_NAME: Dict[str, KernelEntry] = {e.name: e for e in KERNEL_ENTRIES}


def get_entry(name: str) -> Optional[KernelEntry]:
    """按内核名取元数据；不存在返回 ``None``。"""
    return _ENTRY_BY_NAME.get(name)


def _lifecycle_value(inst: Any) -> Optional[str]:
    """把一个实例的 ``lifecycle`` 规整成可 JSON 化的字符串，未知时返回 ``None``。"""
    lc = getattr(inst, "lifecycle", None)
    if lc is None:
        return None
    if isinstance(lc, KernelLifecycle):
        return lc.value
    return str(lc)


def snapshot() -> Dict[str, Dict[str, Any]]:
    """返回每个内核实时的生命周期状态（只读，不创建任何实例）。

    - 实例存在：报告其 ``lifecycle``（``has_instance=True``）。
    - 实例不存在（12 个惰性单例尚未被使用 / context / execution 无规范单例）：
      ``lifecycle=None``，并给出 ``reason``。对于 12 个单例，reason 说明「实例尚
      未被创建」；对于 context / execution，reason 说明「没有规范单例」。
    """
    out: Dict[str, Dict[str, Any]] = {}
    for entry in KERNEL_ENTRIES:
        inst = entry.accessor()  # 不创建
        if inst is None:
            reason = (
                entry.reason
                or "实例尚未被创建（惰性单例，首次使用时才由 get_*() 构造）"
            )
            out[entry.name] = {
                "name": entry.name,
                "target_class": entry.target_class.__name__,
                "has_instance": False,
                "lifecycle": None,
                "reason": reason,
            }
        else:
            out[entry.name] = {
                "name": entry.name,
                "target_class": entry.target_class.__name__,
                "has_instance": True,
                "lifecycle": _lifecycle_value(inst),
                "reason": "",
            }
    return out


def initialize_all() -> Dict[str, Any]:
    """对**已存在**的实例调用 ``initialize()``（不创建任何实例）。

    返回「做了什么 / 跳过了什么」的报告：
    - ``initialized``：被真正初始化的内核名。
    - ``skipped_not_present``：惰性单例，目前还没有实例（下次出现时才会被驱动）。
    - ``no_canonical_instance``：context / execution，设计上就没有规范单例。
    - ``errors``：驱动时抛出的异常（如实记录，不吞掉）。
    """
    initialized: List[str] = []
    skipped_not_present: List[str] = []
    no_canonical: List[str] = []
    errors: List[Dict[str, str]] = []

    for entry in KERNEL_ENTRIES:
        inst = entry.accessor()  # 不创建
        if inst is None:
            if entry.reason:
                no_canonical.append(entry.name)
            else:
                skipped_not_present.append(entry.name)
            continue
        try:
            inst.initialize()
            initialized.append(entry.name)
        except Exception as exc:  # 如实记录，不静默
            errors.append({"name": entry.name, "error": str(exc)})

    return {
        "initialized": initialized,
        "skipped_not_present": skipped_not_present,
        "no_canonical_instance": no_canonical,
        "errors": errors,
    }


def shutdown_all() -> Dict[str, Any]:
    """对称地 ``shutdown()`` 已存在的实例（不创建任何实例）。"""
    shut_down: List[str] = []
    skipped_not_present: List[str] = []
    no_canonical: List[str] = []
    errors: List[Dict[str, str]] = []

    for entry in KERNEL_ENTRIES:
        inst = entry.accessor()  # 不创建
        if inst is None:
            if entry.reason:
                no_canonical.append(entry.name)
            else:
                skipped_not_present.append(entry.name)
            continue
        try:
            inst.shutdown()
            shut_down.append(entry.name)
        except Exception as exc:  # 如实记录，不静默
            errors.append({"name": entry.name, "error": str(exc)})

    return {
        "shutdown": shut_down,
        "skipped_not_present": skipped_not_present,
        "no_canonical_instance": no_canonical,
        "errors": errors,
    }


def uninitialized_but_present() -> List[str]:
    """把「实例存在但 ``lifecycle`` 还是 ``UNINITIALIZED``」这个真实缺口报出来。

    这是本项目「可观测」的硬性要求：指示灯撒谎的前兆就是「明明在服务，却永远
    UNINITIALIZED」。返回这些内核名，供运维 / 主权主体决定是否驱动 ``initialize_all()``。
    """
    gap: List[str] = []
    for entry in KERNEL_ENTRIES:
        inst = entry.accessor()  # 不创建
        if inst is None:
            continue
        lc = getattr(inst, "lifecycle", None)
        if lc is None or lc is KernelLifecycle.UNINITIALIZED:
            gap.append(entry.name)
    return gap
