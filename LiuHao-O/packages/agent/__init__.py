"""agent — 十源 packages 层 facade.

Agent 工厂 + 员工模型（十源 ULTRON/VISION）

复用 src/ 的真实实现（不重写，NO-FAKE）。
"""

from src.ai.agent_factory import (
    AgentSpec,
    AgentRuntimeService,
    AgentMemory,
    AgentPolicy,
    AgentFactory,
)

from src.ai.employee import (
    Agent,
    AgentStatus,
    Employee,
    AgentPool,
    Task,
)

__all__ = [
    "AgentSpec",
    "AgentRuntimeService",
    "AgentMemory",
    "AgentPolicy",
    "AgentFactory",
    "Agent",
    "AgentStatus",
    "Employee",
    "AgentPool",
    "Task",
]
