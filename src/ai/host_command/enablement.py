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
from typing import Optional, Tuple

#: Environment variable controlling whether host-command execution is armed.
ENV_VAR = "LIUHAO_HOST_COMMAND_ENABLED"

_lock = threading.RLock()
_cache: Optional[Tuple[str, bool]] = None

_TRUE_VALUES = {"true", "1", "yes", "on"}


def parse_enablement(spec: Optional[str]) -> bool:
    """True only for an explicit opt-in value; everything else (incl. unset) is OFF."""
    text = (spec or "").strip().lower()
    return text in _TRUE_VALUES


def reload() -> None:
    """Drop the parsed-enablement cache (call after changing the environment)."""
    global _cache
    with _lock:
        _cache = None


def is_enabled() -> bool:
    """Whether host-command execution is currently armed (default: False)."""
    global _cache
    spec = os.environ.get(ENV_VAR, "")
    with _lock:
        if _cache is not None and _cache[0] == spec:
            return _cache[1]
    enabled = parse_enablement(spec)
    with _lock:
        _cache = (spec, enabled)
    return enabled


def describe() -> dict:
    """Operational snapshot of the enablement configuration (audit/dashboard)."""
    return {
        "env_var": ENV_VAR,
        "spec": os.environ.get(ENV_VAR, ""),
        "enabled": is_enabled(),
    }
