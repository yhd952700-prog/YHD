"""governance — 十源 packages 层 facade.

威胁模型 + 安全链 + 应急控制 + 声誉

复用 src/ 的真实实现（不重写，NO-FAKE）。
"""

from src.ai.governance import (
    ThreatDetector,
    SecurityChain,
    EmergencyControl,
    ReputationEngine,
    ThreatFinding,
    get_emergency_control,
)

__all__ = [
    "ThreatDetector",
    "SecurityChain",
    "EmergencyControl",
    "ReputationEngine",
    "ThreatFinding",
    "get_emergency_control",
]
