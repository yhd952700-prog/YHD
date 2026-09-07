"""sandbox — 十源 packages 层 facade.

受控执行（超时 + 内存限制，子进程）

复用 src/ 的真实实现（不重写，NO-FAKE）。
"""

from src.ai.ada import (
    ComputeEngine,
)

__all__ = [
    "ComputeEngine",
]
