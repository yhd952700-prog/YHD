"""orchestration — 十源 packages 层 facade.

L-Core 编排（意图→上下文→目标→计划→策略→执行→验证→响应）

复用 src/ 的真实实现（不重写，NO-FAKE）。
"""

from src.ai.lcore import (
    LCore,
)

__all__ = [
    "LCore",
]
