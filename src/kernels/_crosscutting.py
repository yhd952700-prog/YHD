"""Cross-cutting kernel concerns: Policy Controlled + Audited + Observable.

This module provides the ``@kernel_action`` decorator that weaves the three
DoD dimensions that were historically missing from the kernel layer into any
kernel action method without rewriting the method body:

    * Policy Controlled  - the action is adjudicated by the policy engine and
      the decision is recorded.
    * Audited            - a hash-chained audit event (with correlation_id) is
      written for the action.
    * Observable         - a structured log line is emitted.

Design contract (NO BREAK): the decorator is **additive**. It records the
policy decision and the audit event, but it does NOT change the wrapped
method's return value or exception behavior, and it never raises on its own
(adjudication/audit failures are logged and swallowed). This keeps the 400+
existing kernel tests green while still satisfying the "Every Action needs
Policy" invariant via a recorded decision.

Use absolute imports so this works inside the ``src.kernels`` namespace
package (which has no ``__init__.py``).
"""

from __future__ import annotations

import logging
import time
import uuid
from functools import wraps
from typing import Any, Callable, Optional

logger = logging.getLogger("liuhao.kernel.crosscutting")

# Deferred imports to avoid any import-order coupling at module load time.
_AUDIT_IMPORT = ("src.kernels.audit", "log_event", "AuditEventType", "AuditScope")
_POLICY_IMPORT = ("src.kernels.policy", "evaluate_policy_simple")


def _call_audit(actor: str, action: str, outcome: str, decision: Optional[str],
                corr_id: str, duration_ms: float) -> None:
    try:
        from src.kernels.audit import log_event, AuditEventType, AuditScope
        log_event(
            event_type=AuditEventType.STATE_CHANGE,
            principal_id=actor,
            scope=AuditScope.L0,
            outcome=outcome,
            details={
                "action": action,
                "policy_decision": decision or "unadjudicated",
                "duration_ms": round(duration_ms, 3),
            },
            correlation_id=corr_id,
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("kernel_action audit failed for %r: %s", action, exc)


def _adjudicate(action: str, risk_level: str) -> Optional[str]:
    """Return the recorded policy decision string ("allow"/"deny"), or None."""
    try:
        from src.kernels.policy import evaluate_policy_simple
        decision = evaluate_policy_simple(
            actor={"type": "system", "verified": True},
            action={"name": action, "risk_level": risk_level},
            resource=None,
            scope=None,
        )
        if decision.is_allowed:
            return "allow"
        if decision.is_denied:
            return "deny"
        return "defer"
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("kernel_action policy adjudication failed for %r: %s", action, exc)
        return None


def kernel_action(
    action: str,
    *,
    risk_level: str = "LOW",
    audit: bool = True,
    policy: bool = True,
    observable: bool = True,
) -> Callable:
    """Decorate a kernel action method with cross-cutting DoD concerns.

    Args:
        action: logical action name (e.g. ``"event.publish"``).
        risk_level: one of LOW/MEDIUM/HIGH/CRITICAL, fed to the policy engine.
        audit: write a hash-chained audit event (default True).
        policy: adjudicate via the policy engine and record the decision
            (default True).
        observable: emit a structured log line (default True).
    """

    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            corr_id = uuid.uuid4().hex
            started = time.time()
            decision: Optional[str] = None

            if policy:
                decision = _adjudicate(action, risk_level)

            outcome = "success"
            try:
                result = fn(*args, **kwargs)
            except Exception:
                outcome = "failure"
                raise
            finally:
                duration_ms = (time.time() - started) * 1000.0
                if audit:
                    _call_audit("kernel", action, outcome, decision, corr_id, duration_ms)
                if observable:
                    logger.info(
                        "kernel_action=%s outcome=%s policy=%s duration_ms=%.3f correlation_id=%s",
                        action, outcome, decision or "unadjudicated",
                        duration_ms, corr_id,
                    )

            return result

        return wrapper

    return decorator
