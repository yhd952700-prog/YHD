"""realtime — 十源 packages 层 facade.

实时消息总线（多 agent 协作）

复用 src/ 的真实实现（不重写，NO-FAKE）。
"""

from src.ai.collaboration import (
    MessageBus,
    AgentMessage,
    MessageKind,
    Role,
    MultiAgentTeam,
    Collaborator,
)

__all__ = [
    "MessageBus",
    "AgentMessage",
    "MessageKind",
    "Role",
    "MultiAgentTeam",
    "Collaborator",
]
