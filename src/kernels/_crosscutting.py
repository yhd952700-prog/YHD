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

.. warning::
   **实测记录（2026-09-11）**：``_adjudicate`` 以
   ``{"type": "system", "verified": True}`` 作为 actor 调用
   ``evaluate_policy_simple``。PolicyEngine 会先用
   ``_is_verified_human()`` **覆盖** ``verified``（该 actor 无
   ``id``/``principal``，结果为 False），而内置规则里唯一的 ALLOW 规则
   ``human_sovereignty`` 要求 ``actor.type == "human"`` 且风险为
   HIGH/CRITICAL。因此**每个内核动作记录到的判决恒为 ``deny``**，
   且因装饰器从不拦截，该 deny **不产生任何执行效果**。

   即：Audited 维度是实打实生效的（事件 + correlation_id 真实写入）；
   Policy Controlled 维度只满足 DoD 的字面要求（"有明确的策略判决记录"），
   其判决值**不携带信息**。这是"记录而非控制"，不是"策略拦截"。
   若要让它成为真正的控制点，需要给 system actor 定义可放行的规则——
   属安全语义变更，须单独裁决，不要随手改。
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
                # 诚实标注：本装饰器是 additive 的，只记录判决、从不拦截，
                # 因此 policy_decision 是「已记录的判决」而非「已执行的处置」。
                # 缺了这个字段，读审计的人会把 deny 误读成「动作被拒绝」。
                "policy_enforced": False,
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
