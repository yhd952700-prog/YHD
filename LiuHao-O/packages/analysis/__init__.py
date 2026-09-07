"""analysis — 十源 packages 层 facade.

计算 / 统计分析引擎（十源 ADA）

复用 src/ 的真实实现（不重写，NO-FAKE）。
"""

from src.ai.ada import (
    ComputeEngine,
)

__all__ = [
    "ComputeEngine",
]
