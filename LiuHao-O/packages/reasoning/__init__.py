"""reasoning — 十源 packages 层 facade.

确定性演绎推理（forward-chaining）+ 可选生成式推理。

复用 src/ai/reasoning.py 的真实实现（不重写，NO-FAKE）。
"""

from src.ai.reasoning import Reasoner, ReasoningStep

__all__ = ["Reasoner", "ReasoningStep"]
