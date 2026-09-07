"""memory — 十源 packages 层 facade.

多层记忆 + 压缩 + 作用域（十源 KAREN/ENOCH）

复用 src/ 的真实实现（不重写，NO-FAKE）。
"""

from src.kernels.memory import (
    MemoryKernel,
    MemoryEntry,
    MemoryTier,
    MemoryScope,
    MemoryCompression,
    get_memory_kernel,
)

__all__ = [
    "MemoryKernel",
    "MemoryEntry",
    "MemoryTier",
    "MemoryScope",
    "MemoryCompression",
    "get_memory_kernel",
]
