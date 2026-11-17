"""Shell 命令闸（0.4.6，5-3 电脑操控一期·安全版）。

三条硬规则：

1. **默认关闭**：环境变量 ``LIUHAO_AUTOMATION_SHELL_ENABLED`` 显式为真
   才开；不开时任何 shell 命令都拒绝，不分是谁。
2. **仅 Owner 可开**：开着也不等于谁都能用 —— 调用方必须是账号树根
   （Owner）。非 Owner 一律拒绝，哪怕全权模式也不放行（全权放的是
   「审批」，不是「身份」）。
3. **危险命令单独确认进审批中心**：提权命令一律拒绝（复用
   ``host_command.danger.classify`` 的提权规则，全权也不提权）；危险
   命令（格式化、删库、关机等）不直接执行，先在审批中心建一条待办，
   Joseph 点了才执行；没点之前命令只停在「待确认」状态。

本模块只做「判定」，不执行命令、不建审批（建审批由调用方经
``src/gateway/decision_approval.py`` 走工作流审批库，保证待办真的
出现在审批中心里）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

SHELL_ENV = "LIUHAO_AUTOMATION_SHELL_ENABLED"

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def is_shell_enabled(env: Optional[dict] = None) -> bool:
    """Shell 通道是否被显式打开。默认关闭。"""
    raw = (env or os.environ).get(SHELL_ENV, "")
    return str(raw or "").strip().lower() in _TRUTHY


def describe_shell() -> dict:
    return {
        "env_var": SHELL_ENV,
        "enabled": is_shell_enabled(),
        "owner_only": True,
        "dangerous_requires_approval": True,
    }


@dataclass(frozen=True)
class ShellDecision:
    """Shell 判定：``allowed`` 为 True 才能执行；``deferred`` 为 True 表示
    已（或应）进审批中心等 Joseph 点确认；其余一律拒绝。"""

    allowed: bool
    deferred: bool
    reason: str
    dangerous: Optional[str] = None
    escalation: Optional[str] = None

    def as_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "deferred": self.deferred,
            "reason": self.reason,
            "dangerous": self.dangerous,
            "escalation": self.escalation,
        }


def evaluate_shell(
    command: str,
    *,
    is_owner: bool,
    shell_enabled: Optional[bool] = None,
) -> ShellDecision:
    """判定一条 shell 命令。顺序：通道 → 身份 → 提权 → 危险 → 放行。"""
    if shell_enabled is None:
        shell_enabled = is_shell_enabled()
    if not shell_enabled:
        return ShellDecision(False, False, "shell_disabled")
    if not command or not str(command).strip():
        return ShellDecision(False, False, "empty_command")
    if not is_owner:
        return ShellDecision(False, False, "shell_owner_only")

    from ..ai.host_command.danger import classify

    report = classify(str(command))
    if report.is_escalation:
        return ShellDecision(
            False, False, "escalation_refused",
            escalation=report.escalation,
        )
    if report.is_dangerous:
        return ShellDecision(
            False, True, "dangerous_requires_approval",
            dangerous=report.dangerous,
        )
    return ShellDecision(True, False, "ok")


__all__ = ["SHELL_ENV", "ShellDecision", "describe_shell", "evaluate_shell", "is_shell_enabled"]
