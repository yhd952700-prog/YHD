"""context — 十源 packages 层 facade.

上下文压缩 Kernel（十源 JARVIS）

复用 src/ 的真实实现（不重写，NO-FAKE）。
"""

from src.kernels.context import (
    ContextKernel,
    ContextInput,
    ContextCompression,
    AttentionMechanism,
)

__all__ = [
    "ContextKernel",
    "ContextInput",
    "ContextCompression",
    "AttentionMechanism",
]
