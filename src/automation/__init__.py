"""鎏灏自动化模块（地基阶段）。

同进程模块，不做独立服务。默认三重关闭（装配闸 / 后端闸 / 主体闸），任一
未满足即不注册任何工具。本轮只提供 fake 后端与会话控制面，不接真实键鼠。
"""

from .audit import AutomationAudit, AutomationAuditError
from .confirm import SqliteConfirmationBroker
from .contracts import (
    ActionRequest,
    ActionResult,
    AutomationBackend,
    AutomationError,
    BackendKind,
    ConfirmationTicket,
    GuardDecision,
    Observation,
    SessionStatus,
    TargetScope,
    TicketConsumeResult,
)
from .estop import EmergencyStop, get_emergency_stop
from .gate import AutomationGate, GateDecision
from .guard import Guard
from .sessions import AutomationSession, AutomationSessionRegistry, get_session_registry
from .tools import build_automation_tools

__all__ = [
    "ActionRequest",
    "ActionResult",
    "AutomationAudit",
    "AutomationAuditError",
    "AutomationBackend",
    "AutomationError",
    "AutomationGate",
    "AutomationSession",
    "AutomationSessionRegistry",
    "BackendKind",
    "ConfirmationTicket",
    "EmergencyStop",
    "GateDecision",
    "Guard",
    "GuardDecision",
    "Observation",
    "SessionStatus",
    "SqliteConfirmationBroker",
    "TargetScope",
    "TicketConsumeResult",
    "build_automation_tools",
    "get_emergency_stop",
    "get_session_registry",
]
