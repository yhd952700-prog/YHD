"""Sandbox isolation assurance (UBX-002 / U39) -- fail-closed, risk-tiered.

The :class:`SandboxBackendManager` already refuses to *silently* downgrade a
specific requested backend to host ``subprocess`` when ``required=True`` is
passed. What it does NOT do is assert a *genuine* isolation guarantee before
executing **untrusted / AI-generated code**: on a host with no gVisor / Docker /
Monty / RestrictedPython available, the default ``execute()`` path falls through
to ``SUBPROCESS`` -- i.e. the AI-generated code runs on the host with full
privileges and no enforced resource limits (U39). That is a fail-open.

This module closes the gap with a single default-DENY-aware gate:

  * A "genuine" isolation backend is one that actually constrains what the code
    can do -- ``gvisor``, ``docker``, ``monty`` (Rust restricted interpreter),
    ``restricted_python`` (capability isolation). ``subprocess`` (host) is NOT
    isolation.
  * ``assure_sandbox_isolation(manager, risk_tier=..., enforce=...)`` returns the
    backend to use. When the action requires untrusted-code isolation and NO
    genuine backend is present:
      - if ``enforce`` is True (and the action is HIGH/CRITICAL) it RAISES
        ``SandboxIsolationUnavailable`` -- fail-closed, the code is NOT run on
        the host;
      - otherwise (default) it EMITS A LOUD WARNING and continues on host
        subprocess (degraded-continue), so local development on a machine with no
        sandbox does not break. Flipping the posture to fail-closed is a
        *deployment decision* (``LIUHAO_SANDBOX_ENFORCE=on``), never the
        default, exactly like the executor fence.

The posture is intentionally opt-in: arming it globally would deny every
sandbox execution on hosts lacking a real sandbox (e.g. a Windows dev box with
no gVisor), which is a human / governance decision, not something to impose
silently. See docs/blueprint-upgrades/UBX-002-sandbox-fail-closed/ADR.md.
"""

from __future__ import annotations

import logging
import os
from typing import Optional, Set

from .backends.base import SandboxBackendType
from .backends.manager import SandboxBackendManager

logger = logging.getLogger(__name__)

# Backends that provide a REAL isolation / restriction guarantee for untrusted
# code. ``SUBPROCESS`` is deliberately excluded: it runs on the host with no
# enforced resource limits and no capability restriction -> not isolation.
GENUINE_ISOLATION_BACKENDS: Set[SandboxBackendType] = {
    SandboxBackendType.GVISOR,
    SandboxBackendType.DOCKER,
    SandboxBackendType.MONTY,
    SandboxBackendType.RESTRICTED_PYTHON,
}

# Tiers for which running untrusted code without isolation is treated as a
# hard deny when enforcement is armed. LOW/MEDIUM keep the degraded-continue
# (warn) path even when armed, mirroring the audit evidence gate's tier split.
_ENFORCED_TIERS = {"HIGH", "CRITICAL"}


class SandboxIsolationUnavailable(RuntimeError):
    """Fail-closed signal: untrusted code was about to run with NO isolation.

    Raised only when enforcement is armed for a HIGH/CRITICAL action and no
    genuine isolation backend is available. The caller MUST NOT fall back to
    host execution.
    """


def _enforce_default() -> bool:
    return os.environ.get("LIUHAO_SANDBOX_ENFORCE", "").strip().lower() in (
        "on", "1", "true", "yes",
    )


def genuine_isolation_available(manager: SandboxBackendManager) -> bool:
    """True iff at least one genuine isolation backend reports available."""
    for btype in GENUINE_ISOLATION_BACKENDS:
        backend = manager._backends.get(btype)  # noqa: SLF001 - introspection API
        if backend is not None and backend.is_available():
            return True
    return False


def available_isolation_backends(manager: SandboxBackendManager) -> Set[str]:
    """Names of currently-available genuine isolation backends (diagnostics)."""
    out: Set[str] = set()
    for btype in GENUINE_ISOLATION_BACKENDS:
        backend = manager._backends.get(btype)  # noqa: SLF001
        if backend is not None and backend.is_available():
            out.add(btype.value)
    return out


def assure_sandbox_isolation(
    manager: SandboxBackendManager,
    *,
    risk_tier: str = "LOW",
    requires_untrusted_code: bool = True,
    enforce: Optional[bool] = None,
) -> SandboxBackendManager:
    """Resolve the backend to use for an action, fail-closed when armed.

    Args:
        manager: the sandbox backend manager.
        risk_tier: "LOW" / "MEDIUM" / "HIGH" / "CRITICAL".
        requires_untrusted_code: whether this action executes untrusted /
            AI-generated code (the thing that MUST be isolated).
        enforce: force the fail-closed posture. Defaults to the
            ``LIUHAO_SANDBOX_ENFORCE`` env var.

    Returns:
        The manager (so callers can chain ``.execute(...)``), after the posture
        check has passed or been deliberately degraded.

    Raises:
        SandboxIsolationUnavailable: when untrusted code requires isolation, no
            genuine backend exists, enforcement is armed, and the tier is
            HIGH/CRITICAL.
    """
    if not requires_untrusted_code:
        # Trusted / first-party code is out of scope for U39; the caller decides.
        return manager

    if genuine_isolation_available(manager):
        return manager

    # No genuine isolation backend -> the only fallback is host subprocess.
    effective_enforce = _enforce_default() if enforce is None else enforce
    tier = (risk_tier or "LOW").upper()
    available = available_isolation_backends(manager)

    if effective_enforce and tier in _ENFORCED_TIERS:
        raise SandboxIsolationUnavailable(
            f"refusing to execute untrusted code on the host: no genuine "
            f"isolation backend available (tried "
            f"{[b.value for b in GENUINE_ISOLATION_BACKENDS]}); present backends "
            f"with isolation={sorted(available) or 'none'}. Set LIUHAO_SANDBOX_ENFORCE "
            f"to a non-armed value to degrade-continue on host (development only)."
        )

    logger.warning(
        "SANDBOX DEGRADED-CONTINUE: executing untrusted code on the HOST "
        "(subprocess) because no genuine isolation backend is available "
        "(available isolation=%s). This is a fail-open for tier=%s; arm "
        "LIUHAO_SANDBOX_ENFORCE=on to fail-closed for HIGH/CRITICAL actions.",
        sorted(available) or "none", tier,
    )
    return manager
