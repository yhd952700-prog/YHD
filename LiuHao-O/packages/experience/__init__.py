"""experience — 十源 packages 层 facade.

经验引擎（复用 MemoryKernel）

复用 src/ 的真实实现（不重写，NO-FAKE）。
"""

from src.ai.verification import (
    ExperienceEngine,
    ExperienceEntry,
)

__all__ = [
    "ExperienceEngine",
    "ExperienceEntry",
]
