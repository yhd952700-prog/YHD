"""runtime — 十源 packages 层 facade.

Agent Runtime 生命周期 + 持续运行循环

复用 src/ 的真实实现（不重写，NO-FAKE）。
"""

from src.ai.agent_factory import (
    AgentRuntimeService,
)

from src.ai.runtime_loop import (
    RuntimeLoop,
)

__all__ = [
    "AgentRuntimeService",
    "RuntimeLoop",
]
