"""evolution — 十源 packages 层 facade.

受守卫的自改进流水线

复用 src/ 的真实实现（不重写，NO-FAKE）。
"""

from src.ai.evolution import (
    EvolutionEngine,
    Experiment,
    ExperimentStatus,
)

__all__ = [
    "EvolutionEngine",
    "Experiment",
    "ExperimentStatus",
]
