"""U1 — future enablement gate (default OFF / DENY).

Mirrors the kernel enforcement control point (``LIUHAO_KERNEL_POLICY_ENFORCE``):
a single, reversible, deployment-side switch. With it unset the host-command
subsystem is inert (the broker denies everything). Flipping it ON later is a
config/policy change, NOT a rebuild -- and even when ON, the capability model
stays default-DENY, so nothing executes until explicit capabilities are granted.

This module deliberately does NOT decide *whether* host-command should ever be
enabled; that is a sovereign decision. It only provides the safe, default-off
switch and a cache + ``reload()`` so config changes take effect without restart.
"""

from __future__ import annotations

import os
import threading
from typing import Optional

#: Environment variable controlling whether host-command execution is armed.
ENV_VAR = "LIUHAO_HOST_COMMAND_ENABLED"

_lock = threading.RLock()
_cache: tuple[tuple[str, bool], bool] | None = None

_TRUE_VALUES = {"true", "1", "yes", "on"}


def parse_enablement(spec: Optional[str]) -> bool:
    """True only for an explicit opt-in value; everything else (incl. unset) is OFF."""
    text = (spec or "").strip().lower()
    return text in _TRUE_VALUES


def _full_authority() -> bool:
    """完全授权模式下主机命令一并放开（老板 2026-11-11 的决定）。

    读取失败一律当作非全权：这是放宽方向的开关，失败必须更严。
    """
    try:
        from src.security.authority_mode import is_full

        return is_full()
    except Exception:  # pragma: no cover - 权限模块不可用时保持默认 DENY
        return False


def reload() -> None:
    """Drop the parsed-enablement cache (call after changing the environment)."""
    global _cache
    with _lock:
        _cache = None


def is_enabled() -> bool:
    """Whether host-command execution is currently armed.

    两个来源任一成立即为真：显式环境变量 ``LIUHAO_HOST_COMMAND_ENABLED``，或
    处于完全授权模式。后者**不改变**能力/审批管线的其余闸门 —— 全权模式下仍有
    提权拒绝、危险命令一次确认；敏感路径只记录、不拦截。
    """
    global _cache
    spec = os.environ.get(ENV_VAR, "")
    full = _full_authority()
    key = (spec, full)
    with _lock:
        if _cache is not None and _cache[0] == key:
            return _cache[1]
    enabled = parse_enablement(spec) or full
    with _lock:
        _cache = (key, enabled)
    return enabled


def describe() -> dict:
    """Operational snapshot of the enablement configuration (audit/dashboard)."""
    return {
        "env_var": ENV_VAR,
        "spec": os.environ.get(ENV_VAR, ""),
        "enabled": is_enabled(),
        "authority_mode_full": _full_authority(),
    }
