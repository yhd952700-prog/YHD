"""policy — 十源 packages 层 facade.

权限边界 + 策略引擎（十源 JOCaSTA）

复用 src/ 的真实实现（不重写，NO-FAKE）。
"""

from src.kernels.policy import (
    PolicyEngine,
    PolicyRule,
    PolicyCondition,
    PolicyDecision,
    PolicySet,
    PolicyAction,
    PolicyEffect,
    PolicyScope,
    get_policy_engine,
)

__all__ = [
    "PolicyEngine",
    "PolicyRule",
    "PolicyCondition",
    "PolicyDecision",
    "PolicySet",
    "PolicyAction",
    "PolicyEffect",
    "PolicyScope",
    "get_policy_engine",
]
