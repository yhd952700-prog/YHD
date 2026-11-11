"""G1–G5：完全授权模式的独立审计埋点（不依赖审批流程）。

为什么单独一个模块：审批被关掉以后，``HUMAN_SOVEREIGNTY_OVERRIDE`` 这类
「谁批准的」记录不再产生。为了让证据链仍然闭合，这五条事件必须由**动作本身**
或**配置变更本身**触发，而不是由审批流程顺带写出：

* G1 ``authority.mode_changed``      —— 模式切换（谁 / 何时 / 哪个通道 / 前后值）
* G2 ``authority.irreversible_allowed`` —— 模式下一条不可逆动作被放行
* G3 ``authority.confirm_disabled``  —— 用户把最后一次确认也关掉
* G4 ``authority.confirm_restored``  —— 确认被恢复
* G5 ``host_command.executed``       —— 主机命令真实执行（完整命令行，脱敏）
* ``authority.protected_path_touched`` —— AI 触碰敏感路径（审计库/日志/凭据/
  程序本体/模式配置）。**只记录，动作照常放行**；标准/全权一致。

约定：

* 一律使用显式的 :class:`AuditEventType` 成员，不借用 ``POLICY_EVAL`` 之类的
  通用类型，避免把「配置沿革」混进「动作结果」里查不出来。
* 写入失败**不吞掉**：调用方决定是 fail-closed 还是降级。G2/G5 属于「动作已经
  发生」之后必须能证明的事实，调用方（内核装饰器 / host-command broker）会把
  失败当作拒绝执行的理由；敏感路径触碰相反——只记录，失败不改变放行。
* 本模块**不依据模式做权限判断**（只把当前模式写进记录）。
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from typing import Any

#: 主权/配置层审计域（与 HUMAN_SOVEREIGNTY_OVERRIDE 一致）。
_SCOPE_SOVEREIGNTY = "L0"
#: 执行/动作层审计域。
_SCOPE_EXECUTION = "L5"

#: 从真审计模块按需导入，避免 import 期循环依赖。
def _audit():
    from src.kernels.audit import AuditEventType, AuditScope, log_event

    return AuditEventType, AuditScope, log_event


def _principal_of(actor: Any) -> str:
    """把各种 actor 形态收敛成审计主体字符串。"""
    if isinstance(actor, str):
        return actor or "unknown"
    if isinstance(actor, dict):
        for key in ("principal", "principal_id", "id", "name"):
            value = actor.get(key)
            if value:
                return str(value)
        return str(actor.get("kind") or "unknown")
    for attr in ("principal", "principal_id", "id"):
        value = getattr(actor, attr, None)
        if value:
            return str(value)
    return "unknown"


_SECRET_PATTERNS = (
    # --password=xxx / --token xxx / --api-key=xxx
    re.compile(r"(?i)(--?[a-z0-9_-]*(?:password|passwd|token|secret|api-?key)[a-z0-9_-]*)"
               r"(\s*=\s*|\s+)(\"[^\"]*\"|'[^']*'|\S+)"),
    # sk-xxxx / bearer xxxx
    re.compile(r"(?i)\b(sk-[A-Za-z0-9_\-]{6,})"),
    re.compile(r"(?i)\b(bearer\s+)([A-Za-z0-9._\-]{6,})"),
)


def redact_command(command: str) -> str:
    """保留完整命令行与参数，只把看起来是凭据的值替换成 ``***``。

    审计要求「记完整命令行（含参数）」，但不允许把 API Key / 口令写进日志。
    """
    text = command or ""
    for pattern in _SECRET_PATTERNS:
        if pattern.groups == 3:
            text = pattern.sub(lambda m: f"{m.group(1)}{m.group(2)}***", text)
        else:
            text = pattern.sub(lambda m: f"{m.group(1)}***", text)
    return text


def record_mode_changed(
    *,
    previous: str,
    current: str,
    actor: Any,
    source: str = "unknown",
    reason: str | None = None,
    correlation_id: str | None = None,
):
    """G1：完全授权模式切换。"""
    AuditEventType, AuditScope, log_event = _audit()
    return log_event(
        AuditEventType.AUTHORITY_MODE_CHANGED,
        _principal_of(actor),
        AuditScope[_SCOPE_SOVEREIGNTY],
        "success",
        {
            "event": "authority.mode_changed",
            "previous_mode": previous,
            "current_mode": current,
            "source_channel": source,
            "reason": reason,
            "pid": os.getpid(),
        },
        correlation_id,
    )


def record_irreversible_allowed(
    *,
    action: str,
    tier: str,
    actor: Any,
    correlation_id: str | None = None,
    target: str | None = None,
    confirm_state: str = "no_confirmation_channel",
    extra: dict[str, Any] | None = None,
):
    """G2：完全授权模式下一条不可逆动作被放行（在执行**之前**写）。"""
    AuditEventType, AuditScope, log_event = _audit()
    details: dict[str, Any] = {
        "event": "authority.irreversible_allowed",
        "action": action,
        "risk_tier": str(tier),
        "mode": "full",
        "confirm_state": confirm_state,
        "target": target,
    }
    if extra:
        details.update(extra)
    return log_event(
        AuditEventType.AUTHORITY_IRREVERSIBLE_ALLOWED,
        _principal_of(actor),
        AuditScope[_SCOPE_EXECUTION],
        "authorized",
        details,
        correlation_id,
    )


def record_confirm_disabled(
    *,
    categories: Iterable[str],
    actor: Any,
    source: str = "unknown",
    reason: str | None = None,
):
    """G3：用户关掉了「最后一次确认」（不可逆动作不再逐次问）。"""
    AuditEventType, AuditScope, log_event = _audit()
    return log_event(
        AuditEventType.AUTHORITY_CONFIRM_DISABLED,
        _principal_of(actor),
        AuditScope[_SCOPE_SOVEREIGNTY],
        "success",
        {
            "event": "authority.confirm_disabled",
            "categories": sorted(str(c) for c in categories),
            "source_channel": source,
            "reason": reason,
        },
    )


def record_confirm_restored(
    *,
    categories: Iterable[str],
    actor: Any,
    source: str = "unknown",
):
    """G4：确认被恢复。"""
    AuditEventType, AuditScope, log_event = _audit()
    return log_event(
        AuditEventType.AUTHORITY_CONFIRM_RESTORED,
        _principal_of(actor),
        AuditScope[_SCOPE_SOVEREIGNTY],
        "success",
        {
            "event": "authority.confirm_restored",
            "categories": sorted(str(c) for c in categories),
            "source_channel": source,
        },
    )


def record_host_command(
    *,
    command: str,
    cwd: str | None = None,
    exit_code: int | None = None,
    duration_ms: float | None = None,
    actor: Any = "host-command-broker",
    correlation_id: str | None = None,
    dangerous: bool = False,
    confirm_state: str = "not_required",
    use_shell: bool = False,
):
    """G5：主机命令真实执行。命令行脱敏后完整保留，含参数。"""
    AuditEventType, AuditScope, log_event = _audit()
    return log_event(
        AuditEventType.HOST_COMMAND_EXECUTED,
        _principal_of(actor),
        AuditScope[_SCOPE_EXECUTION],
        "success" if not exit_code else "failed",
        {
            "event": "host.command.executed",
            "command": redact_command(command),
            "cwd": cwd,
            "exit_code": exit_code,
            "duration_ms": duration_ms,
            "dangerous": bool(dangerous),
            "confirm_state": confirm_state,
            "use_shell": bool(use_shell),
            "mode": "full",
        },
        correlation_id,
    )


def _attribution_payload() -> dict[str, Any] | None:
    """当前任务归属（员工/task/goal）。取不到就返回 ``None``，不凭空编造。"""
    try:
        from src.kernels._crosscutting import _task_attribution_payload

        return _task_attribution_payload()
    except Exception:  # pragma: no cover - 归属信息缺失不应阻断审计
        return None


def _current_mode() -> str:
    """当前进程模式，供审计如实标注；读不到按更严的 standard 记。"""
    try:
        from src.security.authority_mode import current_mode

        return current_mode()
    except Exception:  # pragma: no cover - 模式模块不可用时按标准模式记
        return "standard"


def record_protected_touched(
    *,
    path: str | None,
    reason: str,
    actor: Any = "ai",
    correlation_id: str | None = None,
    command: str | None = None,
    channel: str = "filesystem",
    operation: str = "write",
):
    """AI 触碰敏感路径（**只记录，动作照常放行**）。

    2026-11-11 老板拍板：审计库 / 日志 / 凭据 / 程序本体 / 全权配置不再对 AI
    只读，标准模式与全权模式一致。这条记录回答「谁想动、动了哪里、属于哪一类」，
    不构成拒绝；写失败也不改变动作（由调用方决定是否降级）。
    """
    AuditEventType, AuditScope, log_event = _audit()
    details: dict[str, Any] = {
        "event": "authority.protected_path_touched",
        "authority_event": "protected_path_touched",
        "path": path,
        "reason": reason,
        "channel": channel,
        "operation": operation,
        "command": redact_command(command) if command else None,
        "mode": _current_mode(),
        "would_have_blocked": True,
        "task_attribution": _attribution_payload(),
    }
    return log_event(
        AuditEventType.AUTHORITY_PROTECTED_PATH_TOUCHED,
        _principal_of(actor),
        AuditScope[_SCOPE_EXECUTION],
        "allowed",
        details,
        correlation_id,
    )
