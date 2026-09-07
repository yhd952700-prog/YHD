"""execution — 十源 packages 层 facade.

Goal→Task→Plan→Action→Verify（十源 ULTRON/JARVIS）

复用 src/ 的真实实现（不重写，NO-FAKE）。
"""

from src.kernels.execution import (
    ExecutionEngine,
    GoalDecomposer,
    PlanBuilder,
    ActionExecutor,
    Verifier,
    ExecutionPlan,
    Goal,
    Task,
)

__all__ = [
    "ExecutionEngine",
    "GoalDecomposer",
    "PlanBuilder",
    "ActionExecutor",
    "Verifier",
    "ExecutionPlan",
    "Goal",
    "Task",
]
