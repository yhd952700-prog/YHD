"""自动化模块的统一契约。

本模块只定义纯数据结构与协议，不包含任何真实键鼠注入、也不依赖第三方库。
设计约束见 ``docs/automation-design.md``（本轮地基阶段的硬性要求）：

* 后端按 ``BackendKind`` 区分；本轮只实现 ``fake``。
* 每个动作是一个 :class:`ActionRequest`，其指纹只由动作语义决定（动作名 /
  目标 / 参数 / 焦点），**不含** principal、会话或步号 —— 这些由确认票据另行绑定。
* 观察结果 :class:`Observation` 只保留可哈希摘要所需的最小字段，绝不携带
  屏幕截图明文或输入文本。
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Mapping, Optional, Protocol, Tuple


class BackendKind(str, Enum):
    """自动化后端类型。本轮仅 ``FAKE`` 可用，其余为下一轮预留。"""

    FAKE = "fake"
    NATIVE = "native"
    BROWSER = "browser"
    ANDROID = "android"


class TargetScope(str, Enum):
    """动作作用范围。"""

    SCREEN = "screen"
    WINDOW = "window"
    APPLICATION = "application"


class SessionStatus(str, Enum):
    """自动化会话状态。熔断后进入 ``FROZEN``，不可继续执行。"""

    OPEN = "open"
    CLOSED = "closed"
    FROZEN = "frozen"


class AutomationError(Exception):
    """自动化调用被拒绝时的统一异常，``reason`` 可被机器判定。"""

    def __init__(self, reason: str, message: str = "") -> None:
        self.reason = reason
        super().__init__(message or reason)


def canonical_json(value: Any) -> str:
    """稳定序列化（键排序、紧凑分隔符），用于指纹与摘要。"""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


@dataclass(frozen=True)
class ActionRequest:
    """一个待执行的自动化动作。

    指纹刻意不包含 ``session_id`` / ``step_no``：票据会把二者与指纹一起绑定，
    这样同一个动作在不同会话里指纹一致，而任何参数变化都会导致指纹变化。
    """

    session_id: str
    step_no: int
    action: str
    target: TargetScope = TargetScope.SCREEN
    target_ref: str = ""
    params: Mapping[str, Any] = field(default_factory=dict)
    expected_focus: Optional[str] = None

    def fingerprint(self) -> str:
        payload = {
            "action": self.action,
            "target": self.target.value,
            "target_ref": self.target_ref,
            "params": dict(self.params),
            "expected_focus": self.expected_focus,
        }
        return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Observation:
    """当前可观察状态（不含明文内容）。"""

    focus: Optional[str] = None
    scope: TargetScope = TargetScope.SCREEN
    captured_at: float = field(default_factory=time.time)
    summary: Mapping[str, Any] = field(default_factory=dict)

    def digest(self) -> str:
        payload = {
            "focus": self.focus,
            "scope": self.scope.value,
            "summary": dict(self.summary),
        }
        return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


@dataclass
class ActionResult:
    """动作执行结果。``status`` ∈ ``executed`` / ``rejected`` / ``failed``。"""

    status: str
    action: str
    ok: bool = False
    observation: Optional[Observation] = None
    error: Optional[str] = None
    detail: Dict[str, Any] = field(default_factory=dict)
    executed_at: float = field(default_factory=time.time)


class AutomationBackend(Protocol):
    """自动化后端协议。实现方只允许在明确授权范围内操作。"""

    kind: BackendKind

    def observe(self, scope: TargetScope = TargetScope.SCREEN) -> Observation:
        """读取当前可观察状态。"""

    def execute(self, request: ActionRequest) -> ActionResult:
        """执行动作，返回真实结果（失败必须如实返回 ``failed``）。"""


@dataclass(frozen=True)
class GuardDecision:
    """守卫判决。``allowed`` 为 False 时 ``reason`` 与 ``message`` 必须可解释。"""

    allowed: bool
    reason: str = ""
    message: str = ""
    detail: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ConfirmationTicket:
    """一次性确认票据（``ticket`` 为调用方持有的随机令牌）。"""

    ticket: str
    principal: str
    session_id: str
    step_no: int
    action_fingerprint: str
    preview: str
    expires_at: float


@dataclass(frozen=True)
class TicketConsumeResult:
    """票据消费结果。并发消费时只有一方 ``ok=True``。"""

    ok: bool
    reason: str = ""
    ticket: Optional[ConfirmationTicket] = None


class GuardProtocol(Protocol):
    """守卫协议：静态规则 + 频率 + 连续失败熔断。"""

    def check(
        self,
        request: ActionRequest,
        observation: Optional[Observation],
    ) -> GuardDecision:
        """执行动作前检查；不允许时必须给出 reason。"""

    def record_attempt(self, now: Optional[float] = None) -> None:
        """记录一次被放行、即将执行的尝试（用于频率上限）。"""

    def record_success(self) -> None:
        """记录一次成功，清零连续失败计数。"""

    def record_failure(self) -> None:
        """记录一次失败；达到阈值触发熔断。"""

    @property
    def tripped(self) -> bool:
        """连续失败达到阈值后为 True，会话必须冻结。"""


class ConfirmationBroker(Protocol):
    """确认票据协议：签发、单次消费、过期与绑定校验。"""

    def issue(
        self,
        *,
        principal: str,
        principal_kind: str,
        session_id: str,
        step_no: int,
        action_fingerprint: str,
        preview: str,
        ttl_seconds: float = 30.0,
    ) -> ConfirmationTicket:
        """签发票据；非人类主体必须拒绝。"""

    def consume(
        self,
        *,
        ticket: str,
        principal: str,
        session_id: str,
        step_no: int,
        action_fingerprint: str,
    ) -> TicketConsumeResult:
        """一次性消费票据；必须原子地校验 pending / 绑定 / 过期。"""


#: 阈值与上限的集中定义，守卫与会话共享，避免两处漂移。
RATE_LIMIT_MAX_ACTIONS = 30
RATE_LIMIT_WINDOW_SECONDS = 60.0
CONSECUTIVE_FAILURE_LIMIT = 3
MAX_CONFIRMATION_TTL_SECONDS = 30.0

FORBIDDEN_TARGET_MARKERS: Tuple[str, ...] = (
    # 凭据 / 密码
    "password", "passwd", "密码", "credential", "凭据",
    # 安全界面
    "windows security", "security center", "安全中心", "安全设置",
    "defender", "firewall", "防火墙", "杀毒",
    # 终端 / shell
    "terminal", "powershell", "cmd.exe", "command prompt", "命令提示符",
    "终端", "shell", "bash",
    # 系统设置 / 注册表
    "system settings", "control panel", "设置", "控制面板", "regedit", "注册表",
    # 任务管理器
    "task manager", "任务管理器", "taskmgr",
    # 卸载确认
    "uninstall", "卸载", "add or remove programs", "程序和功能",
    # 锁屏 / 安全注意序列
    "lock screen", "锁屏", "win+l", "ctrl+alt+del", "ctrl+alt+delete",
)
