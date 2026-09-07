"""capability — 十源 packages 层 facade.

能力注册表（十源 ALL）

复用 src/ 的真实实现（不重写，NO-FAKE）。
"""

from src.kernels.capability import (
    CapabilityRegistry,
    CapabilityEntry,
    CapabilityStatus,
    CapabilityScope,
    get_capability_registry,
)

__all__ = [
    "CapabilityRegistry",
    "CapabilityEntry",
    "CapabilityStatus",
    "CapabilityScope",
    "get_capability_registry",
]
