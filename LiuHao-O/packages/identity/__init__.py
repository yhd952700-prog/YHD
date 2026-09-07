"""identity — 十源 packages 层 facade.

Agent 身份 + 权限（十源 JARVIS/JOCaSTA）

复用 src/ 的真实实现（不重写，NO-FAKE）。
"""

from src.kernels.identity import (
    IdentityManager,
    AgentIdentity,
    IdentityStatus,
    get_identity_manager,
)

__all__ = [
    "IdentityManager",
    "AgentIdentity",
    "IdentityStatus",
    "get_identity_manager",
]
