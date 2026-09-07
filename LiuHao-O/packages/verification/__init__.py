"""verification — 十源 packages 层 facade.

验证 + 经验引擎（复用 execution.Verifier）

复用 src/ 的真实实现（不重写，NO-FAKE）。
"""

from src.ai.verification import (
    VerificationEngine,
    Verdict,
    ExperienceEngine,
    ExperienceEntry,
)

from src.kernels.evaluation import (
    Evaluator,
    EvaluationResult,
    EvaluationCriteria,
)

__all__ = [
    "VerificationEngine",
    "Verdict",
    "ExperienceEngine",
    "ExperienceEntry",
    "Evaluator",
    "EvaluationResult",
    "EvaluationCriteria",
]
