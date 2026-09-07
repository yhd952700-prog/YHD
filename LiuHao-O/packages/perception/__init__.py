"""perception — 十源 packages 层 facade.

感知：观察 + 感知器 + 世界模型

复用 src/ 的真实实现（不重写，NO-FAKE）。
"""

from src.ai.perception import (
    Observation,
    Perceiver,
    TextPerceiver,
    WorldModel,
    StubPerceiver,
)

__all__ = [
    "Observation",
    "Perceiver",
    "TextPerceiver",
    "WorldModel",
    "StubPerceiver",
]
