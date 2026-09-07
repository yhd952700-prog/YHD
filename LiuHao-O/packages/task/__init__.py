"""task — 十源 packages 层 facade.

任务模型 + 任务图节点

复用 src/ 的真实实现（不重写，NO-FAKE）。
"""

from src.ai.employee import (
    Task,
)

from src.ai.goal_task_graph import (
    TaskNode,
)

__all__ = [
    "Task",
    "TaskNode",
]
