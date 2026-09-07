"""resource — 十源 packages 层 facade.

CPU/Mem/Storage/Token/Time/Cost 配额（十源 ULTRON）

复用 src/ 的真实实现（不重写，NO-FAKE）。
"""

from src.kernels.resource import (
    ResourceQuotaManager,
    Quota,
    Allocation,
    ResourceType,
    get_resource_manager,
)

__all__ = [
    "ResourceQuotaManager",
    "Quota",
    "Allocation",
    "ResourceType",
    "get_resource_manager",
]
