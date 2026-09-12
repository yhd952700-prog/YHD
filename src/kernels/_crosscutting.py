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
   **Policy Controlled 的现状（2026-09-11 复核，Policy C-1 后）**：

   ``_adjudicate`` 以**内部 service 主体**调用 ``evaluate_policy_simple``：

   .. code-block:: python

      actor={"type": "service", "principal": "liuhao-internal-service"}

   ``PolicyEngine`` 会用 ``_is_verified_service()`` 从 Identity Kernel
   **重算** ``verified``（绝不信任调用方传入的 ``verified``），因此内置规则
   ``internal_service_allow``（precedence 900）只在下列**全部**成立时放行：

   1. actor 类型为 ``service``；
   2. 该主体在身份内核中**存在且 ACTIVE**，且 ``metadata["kind"] == "service"``；
   3. ``action.name`` 落在 ``INTERNAL_SERVICE_ALLOWED_ACTIONS`` 显式白名单内
      （查询/计算/簿记类动作，禁止通配）。

   白名单之外的 29 个内核动作（授权/破坏类）继续落回 ``default_deny``。
   于是**判决重新携带信息**（不再是常量）：白名单内 ``allow``、其余 ``deny``。

   **本条（C-1 阶段）仍然成立**：装饰器默认是 additive 的，判决从不产生执行效果。
   审计事件继续标注 ``policy_enforced`` 字段（未拦截时为 ``false``），并新增
   ``policy_rule`` 记录判决依据的规则 id，避免读审计者把 ``deny`` 误读为「动作被拒绝」。

   **Policy C-2（2026-09-11 Round 67，已实施机制）**：新增 ``enforce`` 开关 +
   :class:`PolicyDeniedError` + 仅 ``HIGH``/``CRITICAL`` 生效的拦截门 + fail-closed。
   但 ``enforce`` **默认 ``False``**，且 43 个生产装饰点**无一开启** —— 因此生产行为
   与 C-1 完全一致（仍记录型）。把某个 HIGH/CRITICAL 动作的 ``enforce`` 翻为 ``True``
   即把它从「记录」变为「真拦截」，但内部 service 主体对这些动作恒 ``deny``（不在 C-1
   白名单、须 human 主权，OD-010），若无 C-3 的「动态 human 主体」通道而直接翻转，会
   **自锁系统**（capability.retire / security.set_abac_rule 等再也无法执行）。该翻转
   是刻意、独立的决策，此处不做，留待用户主权裁决（C-3）。
"""

from __future__ import annotations

import logging
import time
import uuid
from functools import wraps
from typing import Any, Callable, Optional

from src.kernels._risk_classification import ENFORCED_TIERS, RiskTier, get_kernel_action_risk

logger = logging.getLogger("liuhao.kernel.crosscutting")


class PolicyDeniedError(PermissionError):
    """Raised by an *enforced* ``@kernel_action`` when the policy verdict is not allow.

    Carries the action name, the recorded verdict (``"deny"`` / ``"defer"`` /
    ``"error"``), and the rule id that produced it (when known) so callers and
    audit logs can trace *why* execution was blocked. It subclasses
    ``PermissionError`` so existing ``except PermissionError`` guards (e.g. in
    the capability layer) catch it transparently.

    Policy C-2: enforcement is opt-in via the decorator's ``enforce`` flag and
    applies only to ``HIGH``/``CRITICAL`` tiers. With ``enforce=False`` (the
    default) the decorator stays additive and this error is never raised -- the
    C-1 record-only contract is preserved byte-for-byte.
    """

    def __init__(self, action: str, verdict: str, rule_id: Optional[str] = None) -> None:
        self.action = action
        self.verdict = verdict
        self.rule_id = rule_id
        if rule_id:
            super().__init__(
                f"policy denied action={action!r} verdict={verdict} rule={rule_id}"
            )
        else:
            super().__init__(f"policy denied action={action!r} verdict={verdict}")


# Sentinel distinguishing "caller did not set risk_level" from an explicit
# "LOW" so the authoritative D8 registry is consulted only when appropriate.
_RISK_UNSET = object()

# Deferred imports to avoid any import-order coupling at module load time.
_AUDIT_IMPORT = ("src.kernels.audit", "log_event", "AuditEventType", "AuditScope")
_POLICY_IMPORT = ("src.kernels.policy", "evaluate_policy_simple")


def _call_audit(actor: str, action: str, outcome: str, decision: Optional[str],
                corr_id: str, duration_ms: float, rule_id: Optional[str] = None,
                risk_level: Optional[str] = None, enforced: bool = False) -> None:
    try:
        from src.kernels.audit import log_event, AuditEventType, AuditScope
        log_event(
            event_type=AuditEventType.STATE_CHANGE,
            principal_id=actor,
            scope=AuditScope.L0,
            outcome=outcome,
            details={
                "action": action,
                # D8: 把裁决时使用的真实风险等级一并记入审计，使分级可见、
                # 可端到端校验（之前 risk_level 恒为死参数 "LOW"）。
                "risk_level": risk_level or "LOW",
                "policy_decision": decision or "unadjudicated",
                # 判决依据的规则 id（如 "internal_service_allow" /
                # "default_deny"）。没有它，"为什么判 deny" 无从追溯。
                "policy_rule": rule_id or "unadjudicated",
                # 权威标注：本动作是否真的被策略拦截执行（C-2）。
                # enforce=False 时恒 False（additive 记录型）；
                # 被拦截时记 True，使「deny 是否真的阻止了动作」可审计、
                # 不可再被误读为「记录即阻止」。
                "policy_enforced": enforced,
                "duration_ms": round(duration_ms, 3),
            },
            correlation_id=corr_id,
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("kernel_action audit failed for %r: %s", action, exc)


def _adjudicate(action: str, risk_level: str) -> tuple[Optional[str], Optional[str]]:
    """Return ``(decision, rule_id)`` recorded for ``action``.

    The actor is the built-in **internal service principal** rather than an
    anonymous ``{"type": "system"}`` actor: the engine recomputes ``verified``
    from the Identity Kernel, so the verdict reflects the real allow-list in
    ``src.kernels.policy.INTERNAL_SERVICE_ALLOWED_ACTIONS`` instead of being a
    constant deny.

    ``(None, None)`` means adjudication was unavailable; the decorator is
    additive and must never break or delay the wrapped call on that account.
    """
    try:
        from src.kernels.identity import INTERNAL_SERVICE_PRINCIPAL
        from src.kernels.policy import evaluate_policy_simple

        decision = evaluate_policy_simple(
            actor={"type": "service", "principal": INTERNAL_SERVICE_PRINCIPAL},
            action={"name": action, "risk_level": risk_level},
            resource=None,
            scope=None,
        )
        if decision.is_allowed:
            verdict = "allow"
        elif decision.is_denied:
            verdict = "deny"
        else:
            verdict = "defer"

        rule_id: Optional[str] = None
        for entry in decision.traceability:
            # Traceability entries look like "RULE:<rule_id>:<action>".
            parts = entry.split(":")
            if len(parts) == 3 and parts[0] == "RULE":
                rule_id = parts[1]
                break
        return verdict, rule_id
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("kernel_action policy adjudication failed for %r: %s", action, exc)
        return None, None


def kernel_action(
    action: str,
    *,
    risk_level: Any = _RISK_UNSET,
    enforce: bool = False,
    audit: bool = True,
    policy: bool = True,
    observable: bool = True,
) -> Callable:
    """Decorate a kernel action method with cross-cutting DoD concerns.

    Args:
        action: logical action name (e.g. ``"event.publish"``).
        risk_level: one of LOW/MEDIUM/HIGH/CRITICAL, fed to the policy engine.
            When omitted (the normal case), the **authoritative** tier from
            ``src.kernels._risk_classification`` is used (D8) -- previously
            every call site fell back to the dead default ``"LOW"``, which
            made the ``risk_level`` parameter carry no signal and would have
            left the future C-2 "HIGH/CRITICAL only" gate permanently inert.
            An explicit ``risk_level`` always wins over the registry.
        enforce: (Policy C-2) when ``True`` **and** ``risk_level`` is
            ``HIGH``/``CRITICAL``, a non-allow verdict (``deny``/``defer``)
            blocks the wrapped call by raising :class:`PolicyDeniedError`
            (after writing an auditable ``policy_enforced=True`` event).
            Defaults to ``False`` so the decorator stays additive (record-only)
            -- turning this on for an action is the explicit, point-by-point
            decision that flips "Policy Controlled" from *recorded* to
            *enforced* for that action. See the module warning for why the
            internal-service principal cannot be the actor that flips it on
            for HIGH/CRITICAL without the C-3 human-actor path.
        audit: write a hash-chained audit event (default True).
        policy: adjudicate via the policy engine and record the decision
            (default True).
        observable: emit a structured log line (default True).
    """
    # D8: resolve the real tier once at decoration time unless the caller
    # pinned it. For the internal-service actor path this input is inert
    # (the only rule that reads risk_level requires actor.type == "human"),
    # so feeding the real tier changes what the engine is *told* without
    # changing any *verdict* -- zero enforcement risk.
    effective_risk = risk_level if risk_level is not _RISK_UNSET else get_kernel_action_risk(action)

    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            corr_id = uuid.uuid4().hex
            started = time.time()
            decision: Optional[str] = None
            rule_id: Optional[str] = None

            if policy:
                decision, rule_id = _adjudicate(action, effective_risk)

            # --- Policy C-2 enforcement gate ---------------------------------- #
            # Opt-in (enforce=True) and HIGH/CRITICAL only. With enforce=False
            # (the default for all 43 production call sites) this branch is
            # never taken and behaviour is byte-for-byte the pre-C-2 additive
            # contract. When enforced, a non-allow verdict blocks the wrapped
            # call AFTER writing an auditable "blocked" event.
            #
            # fail-closed: if adjudication was unavailable (decision is None)
            # we also block rather than silently execute -- enforcement must
            # never fail open. The internal-service principal is denied for
            # every HIGH/CRITICAL action (they are not in the C-1 allow-list
            # and require human sovereignty, OD-010), so flipping enforce=True
            # on those actions without the C-3 human-actor plumbing would
            # self-lock the system; that flip is a deliberate, separate
            # decision and is NOT made here.
            if enforce and policy:
                try:
                    tier = RiskTier(effective_risk)
                except ValueError:
                    tier = RiskTier.LOW
                if tier in ENFORCED_TIERS and (decision is None or decision != "allow"):
                    block_verdict = decision or "error"
                    block_ms = (time.time() - started) * 1000.0
                    if audit:
                        _call_audit(
                            "kernel", action, "blocked", block_verdict, corr_id,
                            block_ms, rule_id, effective_risk, enforced=True,
                        )
                    if observable:
                        logger.warning(
                            "POLICY ENFORCED kernel_action=%s verdict=%s risk=%s "
                            "rule=%s correlation_id=%s",
                            action, block_verdict, effective_risk,
                            rule_id or "?", corr_id,
                        )
                    raise PolicyDeniedError(action, block_verdict, rule_id)

            outcome = "success"
            try:
                result = fn(*args, **kwargs)
            except Exception:
                outcome = "failure"
                raise
            finally:
                duration_ms = (time.time() - started) * 1000.0
                if audit:
                    _call_audit("kernel", action, outcome, decision, corr_id,
                                duration_ms, rule_id, effective_risk)
                if observable:
                    logger.info(
                        "kernel_action=%s outcome=%s policy=%s risk=%s duration_ms=%.3f correlation_id=%s",
                        action, outcome, decision or "unadjudicated",
                        effective_risk, duration_ms, corr_id,
                    )

            return result

        return wrapper

    return decorator
