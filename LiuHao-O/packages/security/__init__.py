"""security — 十源 packages 层 facade.

RBAC + ABAC + 审计（十源 JOCaSTA）

复用 src/ 的真实实现（不重写，NO-FAKE）。
"""

from src.kernels.security import (
    SecurityEngine,
    SecurityPrincipal,
    RBACRule,
    ABACRule,
    AccessDecision,
    RBACRole,
)

__all__ = [
    "SecurityEngine",
    "SecurityPrincipal",
    "RBACRule",
    "ABACRule",
    "AccessDecision",
    "RBACRole",
]
