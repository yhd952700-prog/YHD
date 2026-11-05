"""鎏灏自动化模块（地基阶段）。

同进程模块，不做独立服务。默认三重关闭（装配闸 / 后端闸 / 主体闸），任一
未满足即不注册任何工具。本轮只提供 fake 后端与会话控制面，不接真实键鼠。
"""

from .audit import AutomationAudit, AutomationAuditError
from .confirm import SqliteConfirmationBroker
from .contracts import (
    ActionCapability,
    ActionRequest,
    ActionResult,
    AutomationBackend,
    AutomationError,
    BackendKind,
    ConfirmationTicket,
    ElementInfo,
    GuardDecision,
    Observation,
    Reversibility,
    SessionStatus,
    TargetScope,
    TicketConsumeResult,
    WindowInfo,
)
from .estop import EmergencyStop, HotkeyRegistrar, HotkeyRegistration, get_emergency_stop
from .gate import AutomationGate, GateDecision
from .guard import ActionRule, Guard, ParamConstraint, target_matches
from .hotkey_windows import WindowsHotkeyRegistrar, configure_from_env, hotkey_enabled
from .observe import (
    FakeWindowObserver,
    ObservationGate,
    ObservationPolicy,
    ObservationService,
    WindowObserver,
    build_observation_tools,
    get_observation_policy,
    observation_enabled,
)
from .replay import AuditReplay, ReplayStep, SessionReplay
from .sessions import AutomationSession, AutomationSessionRegistry, get_session_registry
from .tools import build_automation_tools
from .windows_observer import WindowsObserver

__all__ = [
    "ActionCapability",
    "ActionRequest",
    "ActionRule",
    "ActionResult",
    "AuditReplay",
    "AutomationAudit",
    "AutomationAuditError",
    "AutomationBackend",
    "AutomationError",
    "AutomationGate",
    "AutomationSession",
    "AutomationSessionRegistry",
    "BackendKind",
    "ConfirmationTicket",
    "ElementInfo",
    "EmergencyStop",
    "FakeWindowObserver",
    "GateDecision",
    "Guard",
    "GuardDecision",
    "HotkeyRegistrar",
    "HotkeyRegistration",
    "Observation",
    "ObservationGate",
    "ObservationPolicy",
    "ObservationService",
    "ParamConstraint",
    "Reversibility",
    "SessionReplay",
    "SessionStatus",
    "SqliteConfirmationBroker",
    "TargetScope",
    "TicketConsumeResult",
    "WindowInfo",
    "WindowObserver",
    "WindowsHotkeyRegistrar",
    "WindowsObserver",
    "build_automation_tools",
    "build_observation_tools",
    "configure_from_env",
    "get_emergency_stop",
    "get_observation_policy",
    "get_session_registry",
    "hotkey_enabled",
    "observation_enabled",
    "ReplayStep",
    "target_matches",
]
