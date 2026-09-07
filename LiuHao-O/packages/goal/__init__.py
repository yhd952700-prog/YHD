"""goal — 十源 packages 层 facade.

目标 / 任务图

复用 src/ 的真实实现（不重写，NO-FAKE）。
"""

from src.ai.goal_task_graph import (
    GoalTaskGraph,
    GoalDefinition,
    TaskNode,
    decompose_goal,
)

__all__ = [
    "GoalTaskGraph",
    "GoalDefinition",
    "TaskNode",
    "decompose_goal",
]
