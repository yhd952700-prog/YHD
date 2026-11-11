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
   但 ``enforce`` **默认 ``False``**，且 43 个生产装饰点**无一开启** —— 因此**库的默认
   行为**与 C-1 一致（记录型）。⚠️ 这是**库默认**，不等于部署姿态：生产清单
   ``docker-compose.prod.yml:69`` 以
   ``LIUHAO_KERNEL_POLICY_ENFORCE=${LIUHAO_KERNEL_POLICY_ENFORCE:-CRITICAL}``
   武装，被武装的动作上判决**会**产生执行效果（见
   ``scripts/verify_armed_actions_are_inert.py``）。把某个 HIGH/CRITICAL 动作的
   ``enforce`` 翻为 ``True``
   即把它从「记录」变为「真拦截」，但内部 service 主体对这些动作恒 ``deny``（不在 C-1
   白名单、须 human 主权，OD-010），若无 C-3 的「动态 human 主体」通道而直接翻转，会
   **自锁系统**（capability.retire / security.set_abac_rule 等再也无法执行）。该翻转
   是刻意、独立的决策，此处不做，留待用户主权裁决（C-3）。

   **Policy C-3（2026-09-12 Round 68，已实施机制）**：引入「动态 human 主体通道」
   （``src.kernels._sovereignty`` 的 :class:`human_sovereign` 上下文管理器）+
   :class:`PolicyDeferredError` （``PolicyDeniedError`` 子类，verdict 固定为
   ``"defer"``）+ ``PolicyEffect.DEFER``。当某个 HIGH/CRITICAL 动作被**已核验
   human**（OD-010，经身份内核核验的 ACTIVE 非 service 身份）显式授权、且处于该
   授权范围内时，裁决 actor 由 service 切为 human，经 ``human_sovereignty`` 规则
   （precedence 1000）放行 —— 这正是解除 C-2 自锁的钥匙：翻 ``enforce=True`` 后，
   无人类授权的 HIGH/CRITICAL 动作抛 ``PolicyDeferredError``（待人工审批），有人类
   授权则正常执行。机制默认关闭（无 sovereignty 上下文时行为与 C-1/C-2 完全一致）；
   43 个生产点仍 ``enforce=False`` —— 即**装饰点参数未变**，但"部署是否武装"是另一
   回事（见上）。本句**不得**读作"生产零执行效果"。伪造主体（含以 ``type:"human"``
   引用 service 身份）被 ``_is_verified_human`` 的 kind 校验拒绝，落回 deny。

   **Policy C-4（2026-09-12 Round 71，已实施）**：把"开窗口"这件事本身变成
   **可审计、有时限、可撤销、且动作集受限**的凭据 —— ``src.kernels._sovereignty``
   新增 :class:`SovereigntyGrant` 与 ``issue_grant`` / ``revoke_grant`` /
   ``list_grants`` / ``grant_window``，签发与撤销各写一条
   ``HUMAN_SOVEREIGNTY_OVERRIDE`` 审计事件。同时新增**单一执行开关**
   ``src.kernels._enforcement``（env ``LIUHAO_KERNEL_POLICY_ENFORCE``，默认空
   = 不开启），使"生产开启真拦截"变成**一处可回滚的运维决策**，而不是改 43 个
   调用点。本装饰器据此把拦截门条件改为
   ``enforce or is_enforced(action, risk_level)``，并在审计 ``details`` 中新增
   ``sovereignty_grant`` 字段，闭合「内核动作 ← 审批凭据 ← 授权人」因果链。
   **默认配置下行为与 C-1/C-2/C-3 逐字节一致。**
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from typing import Any, Callable, Dict, Iterator, Optional

from src.kernels._enforcement import is_enforced
from src.kernels._risk_classification import ENFORCED_TIERS, RiskTier, get_kernel_action_risk

logger = logging.getLogger("liuhao.kernel.crosscutting")


# --------------------------------------------------------------------------- #
# PHASE 3.6 / A2 — who actually performed the action
# --------------------------------------------------------------------------- #
#
# Every ``@kernel_action`` recorded the **literal string** ``"kernel"`` as the
# audit subject: ``_call_audit("kernel", ...)`` at both call sites. Measured
# consequence: the authoritative audit chain could not answer "which agent did
# this", because the constant was identical for all 43 production call sites and
# for every agent that ever ran through them. Note that this is true with **one**
# agent -- it is not a scale problem, which is why it is tracked separately from
# the scale findings.
#
# The aggravating detail is that the right answer was *already computed and then
# discarded*: ``_adjudicate`` resolves the acting principal (``sov.principal``
# when a verified human holds the window) and the audit path threw it away while
# writing the constant.
#
# Containment has two halves:
#
#   1. the acting principal is resolved once and threaded through to the audit
#      event, together with its ``identity_fingerprint`` -- the canonical
#      identifier the authorization side also uses. Before this, audit indexed
#      identities by a unique **principal name** while authorization indexed them
#      by a **32-bit id**: two key spaces in different domains, so no cross-check
#      between them was even expressible and a 32-bit collision was unfalsifiable.
#   2. ``bind_acting_principal`` lets a caller that *knows* which agent is acting
#      say so for the duration of a block, instead of leaving the service default
#      to stand in for it.
#
# When nothing is bound, the recorded subject is the internal service principal --
# which is *true*: kernel code, not an agent, performed the action. What is no
# longer true is that the record **cannot** name an agent.
_ACTING_PRINCIPAL: ContextVar[Optional[Dict[str, str]]] = ContextVar(
    "liuhao_acting_principal", default=None
)


@contextmanager
def bind_acting_principal(
    principal: str, *, kind: str = "agent", source: str = "bound"
) -> Iterator[None]:
    """Attribute kernel actions performed inside this block to ``principal``.

    Nesting restores the previous binding on exit, so a caller can tighten
    attribution for a sub-operation without having to reason about what was
    bound outside.

    ``source`` is written verbatim to the audit record's ``actor_source`` and
    ``initiator_source``. It defaults to ``"bound"`` so existing callers keep
    their historical record shape exactly.
    """
    token = _ACTING_PRINCIPAL.set(
        {"kind": kind, "principal": principal, "source": source}
    )
    try:
        yield
    finally:
        _ACTING_PRINCIPAL.reset(token)


SYSTEM_PRINCIPAL = "system"


@contextmanager
def bind_system_principal(*, source: str = "background") -> Iterator[None]:
    """Attribute a deletion/privileged action to the **system**, explicitly.

    Background jobs and scheduled cleanup have no request-side human. They must
    not fall through to the internal *service* principal: that made them look
    like a service call that might have been triggered by a human, which is the
    exact ambiguity this work removes. ``kind="system"`` is written verbatim to
    the audit record so the absence of a human is a stated fact, not a default.
    """
    with bind_acting_principal(SYSTEM_PRINCIPAL, kind="system", source=source):
        yield


@contextmanager
def bind_request_principal(
    principal: str, *, kind: str = "human", source: str = "http-request"
) -> Iterator[None]:
    """Attribute kernel actions to the **human principal that made the request**.

    This is *attribution*, not authority. It changes only who the audit event
    names as the initiator; the policy engine still adjudicates as the internal
    service (or as the sovereign human when a real approval window is open), so
    no caller gains a permission it did not already have.

    Used by the memory-deletion service layer: without it, "a user deleted their
    own memory" and "a service batch-deleted someone else's memory" were written
    with the same service subject and could not be told apart after the fact.
    """
    with bind_acting_principal(principal, kind=kind, source=source):
        yield


def acting_principal() -> Optional[Dict[str, str]]:
    """The currently bound actor, or ``None`` when nothing is bound."""
    bound = _ACTING_PRINCIPAL.get()
    return dict(bound) if bound is not None else None


# --------------------------------------------------------------------------- #
# Audit traceability — bind a goal/operation correlation id onto kernel actions
# --------------------------------------------------------------------------- #
#
# Previously every ``@kernel_action`` wrote a FRESH random correlation id
# (``uuid.uuid4().hex``) into its hash-chained audit event. The consequence was
# a traceability gap: the audit chain could prove "an action executed" but could
# NOT prove "this goal executed it". The goal -> action link existed only on the
# event bus (``execution_started`` / ``execution_completed`` carry
# ``goal.correlation_id``), NOT in the immutable hash-chain audit. That undermines
# audit credibility ("审计真实可信"): an auditor reading only the hash-chain could
# not bind an action back to the goal that ran it.
#
# Fix: let the active operation/goal publish its correlation id through this
# :class:`~contextvars.ContextVar`. When set, the ``kernel_action`` wrapper uses
# it for the audit event's ``correlation_id``; when unset (e.g. a direct action
# outside any goal), it still falls back to a per-action random id, so behaviour
# is backward compatible.
KERNEL_ACTION_CORRELATION_ID: ContextVar[Optional[str]] = ContextVar(
    "liuhao_kernel_action_correlation_id", default=None
)


@contextmanager
def kernel_action_correlation_id(cid: str) -> Iterator[None]:
    """Bind ``cid`` as the correlation id for every kernel action in this block.

    Within the ``with`` block, any ``@kernel_action``-decorated call stamps
    ``cid`` (instead of a fresh random id) onto its hash-chained audit event, so
    the audit chain can tie the action back to the operation/goal that ran it.

    Nesting restores the previous binding on exit, so a sub-operation can tighten
    attribution without disturbing what was bound outside (mirrors
    :func:`bind_acting_principal`).
    """
    token = KERNEL_ACTION_CORRELATION_ID.set(cid)
    try:
        yield
    finally:
        KERNEL_ACTION_CORRELATION_ID.reset(token)


#: Set by :func:`mark_action_denied` to tell the decorator that the wrapped call
#: is refusing by *returning* rather than raising.
_DENIED_SIGNAL: ContextVar[Optional[str]] = ContextVar(
    "liuhao_action_denial", default=None
)


def mark_action_denied(reason: str) -> None:
    """Declare that the enclosing kernel action is refusing.

    A method that reports a refusal by returning ``None``/``False`` is recorded
    by the decorator as ``outcome="success"``, because from the decorator's point
    of view the call simply completed. That is how an impersonation attempt ends
    up reading, in the only durable record, as a *successful identity creation*
    (PHASE 3.6 / A1; U-1). A method that refuses without raising should say so:

        mark_action_denied("principal uses the reserved identity prefix")
        return None

    Opt-in on purpose. Inferring "denied" from a falsy return value would silently
    change the meaning of ``outcome`` for all 43 existing call sites, several of
    which legitimately return ``None``/``False`` for non-security reasons.
    """
    _DENIED_SIGNAL.set(reason)


def _consume_denial() -> Optional[str]:
    """Read and clear the denial signal, so it cannot leak into a later call."""
    reason = _DENIED_SIGNAL.get()
    if reason is not None:
        _DENIED_SIGNAL.set(None)
    return reason


#: Set by :func:`mark_action_failed` to tell the decorator that the wrapped call
#: *ran* but did NOT complete successfully (e.g. a routing delivery failure), so
#: the audit must record ``outcome="failed"`` rather than masquerading it as
#: ``outcome="success"``. Parallel to :data:`_DENIED_SIGNAL`.
_FAILED_SIGNAL: ContextVar[Optional[str]] = ContextVar(
    "liuhao_action_failure", default=None
)


def mark_action_failed(reason: str) -> None:
    """Declare that the enclosing kernel action ran but did NOT complete.

    A method that returns a "tried but failed" result (e.g. ``route()`` where the
    matched adapter's ``send()`` returned ``False``) is recorded by the decorator
    as ``outcome="success"`` because from the decorator's point of view the call
    simply completed. That is how a message that was never delivered ends up
    reading, in the only durable record, as a *successful* routing:

        mark_action_failed("adapter internal send() returned False")
        return message  # already stamped FAILED

    Opt-in on purpose. Inferring "failed" from a falsy/``False`` return would
    silently change the meaning of ``outcome`` for all 43 existing call sites,
    several of which legitimately return ``False`` for non-failure reasons.
    """
    _FAILED_SIGNAL.set(reason)


def _consume_failure() -> Optional[str]:
    """Read and clear the failure signal, so it cannot leak into a later call."""
    reason = _FAILED_SIGNAL.get()
    if reason is not None:
        _FAILED_SIGNAL.set(None)
    return reason


def _service_actor() -> Dict[str, str]:
    try:
        from src.kernels.identity import INTERNAL_SERVICE_PRINCIPAL

        return {"kind": "service", "principal": INTERNAL_SERVICE_PRINCIPAL}
    except Exception:  # pragma: no cover - defensive
        return {"kind": "service", "principal": "liuhao-internal-service"}


def _service_actor_policy_shape() -> Dict[str, str]:
    """The service actor in the shape the policy engine expects."""
    return {"type": "service", "principal": _service_actor()["principal"]}


def _resolve_actor_identity(principal: str) -> Dict[str, Optional[str]]:
    """``{"identity_id", "fingerprint"}`` for ``principal`` via the Identity Kernel.

    Both come from the same object, which is the point: audit and authorization
    now resolve through one identity model instead of two key spaces that could
    never be compared. Unregistered principals yield ``None`` for both -- an
    absent attribution must be visibly absent, not a plausible-looking string.
    """
    try:
        from src.kernels.identity import get_identity_manager

        manager = get_identity_manager()
        identity = manager.get_identity_by_principal(
            principal
        ) or manager.get_identity(principal)
        if identity is None:
            return {"identity_id": None, "fingerprint": None}
        return {"identity_id": identity.id, "fingerprint": identity.fingerprint}
    except Exception:  # pragma: no cover - defensive
        return {"identity_id": None, "fingerprint": None}


def _same_human(a: str, b: str) -> bool:
    """True iff two principal spellings resolve to the same identity.

    ``human_sovereign`` accepts either the identity id (``human:bob``) or the
    plain principal name (``bob``), and a grant records the spelling it was
    issued with -- so the window and the grant can legitimately name one human
    two ways. A raw string comparison would refuse that window, i.e. withdraw a
    human's authority over spelling, so both sides are canonicalised through the
    Identity Kernel (the same resolution the audit writer uses) first.

    When either side cannot be resolved the raw answer is kept: an unresolvable
    principal cannot be *shown* to be the same human, and refusing is the safe
    direction for an authorisation check.
    """
    if a == b:
        return True
    left = _resolve_actor_identity(a)["identity_id"]
    right = _resolve_actor_identity(b)["identity_id"]
    return left is not None and left == right


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


class PolicyDeferredError(PolicyDeniedError):
    """Policy C-3 — an enforced HIGH/CRITICAL action is blocked pending human
    sovereignty (OD-010).

    Unlike a hard :class:`PolicyDeniedError` (raised on fail-closed when the
    engine itself is unavailable), a *defer* means the policy engine *answered*
    with a non-allow verdict for a HIGH/CRITICAL action and the correct remedy
    is for a verified human to grant authority for it (via the C-3 sovereignty
    channel) -- not that the action is permanently forbidden.

    Subclasses ``PolicyDeniedError`` (and therefore ``PermissionError``) so
    existing ``except PermissionError`` / ``except PolicyDeniedError`` guards
    still catch a deferred action transparently.
    """

    def __init__(self, action: str, rule_id: Optional[str] = None) -> None:
        super().__init__(action, "defer", rule_id)


# Sentinel distinguishing "caller did not set risk_level" from an explicit
# "LOW" so the authoritative D8 registry is consulted only when appropriate.
_RISK_UNSET = object()

# Deferred imports to avoid any import-order coupling at module load time.
_AUDIT_IMPORT = ("src.kernels.audit", "log_event", "AuditEventType", "AuditScope")
_POLICY_IMPORT = ("src.kernels.policy", "evaluate_policy_simple")


def _resolve_audit_actor(
    policy_actor: Dict[str, str], *, system_actor: bool = False
) -> Dict[str, str]:
    """The subject written to the audit event, and why it is that subject.

    Order of precedence (PHASE 3.6 / A2):
    1. an explicitly bound acting principal -- a caller that knows which agent is
       acting outranks any inference;
    2. otherwise the actor the policy engine adjudicated as (a verified human when
       a sovereignty window covers the action, else the internal service);
    3. ``actor_source`` records which of the two it was, so a reader of the record
       can tell "an agent did this" from "the kernel did this" from "we do not
       know, and here is the default we fell back to".
    """
    bound = _ACTING_PRINCIPAL.get()
    if bound is None and system_actor:
        # No request-side human and the action is not agent-driven: name the
        # system explicitly. This is a *fallback only* -- an enclosing agent or
        # human binding still wins below, so a chat-triggered delete keeps its
        # real actor.
        bound = {"kind": "system", "principal": SYSTEM_PRINCIPAL,
                 "source": "scheduled"}
    if bound is not None:
        # F33/D-3: an explicit binding still outranks any inference (order
        # unchanged), but it must not *discard* the refusal reason the
        # adjudication produced. Returning early used to drop
        # ``sovereignty_claim``, so a denied HIGH/CRITICAL action under a
        # revoked grant looked, in the durable record, exactly like one whose
        # window was never opened at all.
        out = {
            "kind": bound["kind"],
            "principal": bound["principal"],
            "source": bound.get("source", "bound"),
        }
        if policy_actor.get("sovereignty_claim"):
            out["sovereignty_claim"] = policy_actor["sovereignty_claim"]
        if policy_actor.get("authority_mode"):
            # 完全授权模式下的按任务继承：不是主权窗口，也没有 grant。
            out["authority_mode"] = policy_actor["authority_mode"]
        return out
    actor_type = policy_actor.get("type", "service")
    claim = policy_actor.get("sovereignty_claim")
    if claim:
        # F33: the window's claim was re-checked at the decision point and did
        # not hold (revoked / expired / out of scope), so the actor actually
        # adjudicated with is the service default. Record exactly that -- a
        # reader must never see ``sovereignty-window`` + ``human`` for a claim
        # we refused, because that is a false human approval in the only
        # durable record.
        return {
            "kind": actor_type,
            "principal": policy_actor["principal"],
            "source": "sovereignty-window-rejected",
            "sovereignty_claim": claim,
        }
    return {
        "kind": actor_type,
        "principal": policy_actor["principal"],
        "source": (
            "authority-mode-full"
            if actor_type == "service" and policy_actor.get("authority_mode")
            else ("sovereignty-window" if actor_type == "human" else "service-default")
        ),
        **(
            {"authority_mode": policy_actor["authority_mode"]}
            if policy_actor.get("authority_mode")
            else {}
        ),
    }


def _call_audit(actor: Dict[str, str], action: str, outcome: str,
                decision: Optional[str],
                corr_id: str, duration_ms: float, rule_id: Optional[str] = None,
                risk_level: Optional[str] = None, enforced: bool = False,
                grant_id: Optional[str] = None,
                denial_reason: Optional[str] = None,
                reraise: bool = False,
                initiator: Optional[Dict[str, str]] = None) -> None:
    # Audit linkage (D19-D21): when an executor fence context is bound on the
    # call stack, stamp the executor identity / fence token / lease epoch into the
    # audit record. Because ``details`` feeds the hash chain's prev_hash, this
    # linkage is tamper-evident and answers "which executor did this".
    try:
        from src.kernels.execution.fence import current_executor_fence

        _fctx = current_executor_fence()
    except Exception:  # pragma: no cover - fence import must never break audit
        _fctx = None
    try:
        from src.kernels.audit import AuditEventType, AuditScope, log_event

        # A2: resolve the actor through the Identity Kernel so the audit record
        # and the authorization decision name the same identity, in the same key
        # space. Unregistered principals resolve to None for both fields -- an
        # absent attribution must be visibly absent.
        identity = _resolve_actor_identity(actor["principal"])
        # F26: write ONE canonical principal form. The authorization side indexes
        # by ``identity.id`` (e.g. ``human:bob``); writing the raw window string
        # here (e.g. ``bob``) put the two key spaces back in different domains,
        # so the convergence assertion below held only for whichever spelling
        # happened to equal the id. An unregistered principal keeps its raw name
        # (``identity_id`` is None) -- an absent attribution must stay visibly
        # absent rather than be invented.
        principal_id = identity["identity_id"] or actor["principal"]
        log_event(
            event_type=AuditEventType.STATE_CHANGE,
            principal_id=principal_id,
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
                # C-4: 若本动作是在某个人工审批授权窗口内执行的，记下凭据 id。
                # 没有它，一次被放行的 HIGH/CRITICAL 动作无法回答"谁批的"。
                # D23: sovereignty_grant 已在包装器里被强制为 None（deny/error/
                # defer 或 denied/blocked 路径），此处只可能是 None 或真实
                # grant id ——「deny 路径绝不带 grant」因此独立于 claim_held
                # 控制流，未来防护 F26/F33。
                "sovereignty_grant": grant_id,
                # A2: 真实行动主体的种类、身份 id 与规范指纹。这三个字段让
                # 「哪个 agent 做的」可答，并让审计侧与授权侧收敛到同一原语
                # （授权侧按 32-bit id 索引，此前审计侧按唯一主名索引 —— 两个
                # 键空间无法交叉校验）。
                "actor_kind": actor.get("kind", "unknown"),
                "actor_source": actor.get("source", "unknown"),
                "actor_identity_id": identity["identity_id"],
                "actor_fingerprint": identity["fingerprint"],
                # Request-side initiator (who *asked for* the deletion or
                # privilege change), kept separate from actor_* (who was
                # *authorised* to perform it). ``initiated_by`` is the raw
                # request principal; actor_* keeps the canonical identity id.
                # Additive only: historical rows have no such keys and remain
                # parseable; API readers see ``None`` for them, so no old row
                # becomes unreadable and no schema migration is needed.
                "initiated_by": (initiator or {}).get("principal"),
                "initiator_kind": (initiator or {}).get("kind"),
                "initiator_source": (initiator or {}).get("source"),
                # F33: when the recorded actor is *not* the human the window
                # claimed, say why. Without it a denied HIGH/CRITICAL action
                # under a revoked grant is indistinguishable from one that never
                # claimed human authority at all.
                "sovereignty_claim": actor.get("sovereignty_claim"),
                # 完全授权模式的放行依据。与 sovereignty_grant 分开记：这条
                # 通道没有审批凭据，只有「当前进程处于 full 模式」。
                "authority_mode": actor.get("authority_mode"),
                # 全量审计（老板 2026-11-11 要求）：全权模式下员工/定时任务
                # 自己触发的动作，必须能追到「哪个员工、哪个任务、哪条 goal」。
                # 没绑定任务上下文时整块为 None，绝不猜。
                "task_attribution": _task_attribution_payload(),
                # A1/U-1: why a call that *returned* is nonetheless a refusal.
                # Without it, a refused impersonation reads as a successful
                # creation in the authoritative chain.
                "denial_reason": denial_reason,
                "duration_ms": round(duration_ms, 3),
                # D19-D21: executor fence linkage (tamper-evident via the chain).
                # Absent when no executor fence context is bound (legacy path).
                "executor_id": _fctx.executor_id if _fctx is not None else None,
                "fence_token": _fctx.token if _fctx is not None else None,
                "lease_epoch": _fctx.epoch if _fctx is not None else None,
            },
            correlation_id=corr_id,
        )
    except Exception as exc:  # defensive
        if reraise:
            # PHASE 3.6 / CRIT-1C Layer 2 (D17 Option C, ACCEPTED): the caller is
            # a mandatory-evidence action and MUST fail-closed when the evidence
            # channel is unavailable. Do NOT swallow here -- propagate so the
            # gate can convert this into a hard deny. Re-raising is the whole
            # point: "audit failure blocks the action" must never become
            # "audit failure, action continues" via this branch.
            raise
        logger.warning("kernel_action audit failed for %r: %s", action, exc)


def _active_grant_id() -> Optional[str]:
    """The approval-grant id of the currently open sovereignty window, if any."""
    try:
        from src.kernels._sovereignty import get_active_sovereignty

        sov = get_active_sovereignty()
        return sov.grant_id if sov is not None else None
    except Exception:  # pragma: no cover - defensive
        return None


def _sovereignty_claim_state(sov: Any, action: str) -> Optional[str]:
    """Re-check a sovereignty window's authority **at the decision point**.

    Returns ``None`` when the window may still be honoured, else a short reason.

    Refusal reasons, in the order they are checked: ``grant-not-found``,
    ``grant-principal-mismatch``, ``grant-revoked``, ``grant-expired``,
    ``grant-does-not-cover-action``, and ``claim-lookup-error`` for any failure
    to answer the question at all.

    F33 read-path discipline: a grant being valid when the window *opened* does
    not make it valid *now*. The window carries only a ``grant_id`` snapshot, so
    the authoritative answer is re-resolved from the grant registry on every
    adjudication -- a grant revoked (or allowed to expire) after the window
    opened must stop authorising, not keep working because the frozen dataclass
    the caller happened to hold still looks active.

    A window with no ``grant_id`` is the bare C-3 channel; its legitimacy is
    recomputed by the policy engine from the Identity Kernel, so there is
    nothing here to re-check and it is left alone.

    **This function never raises.** It is called from inside :func:`_adjudicate`,
    whose blanket handler answers an exception with ``return None, None,
    _service_actor_policy_shape()`` -- i.e. *adjudication unavailable*. In the
    record-only configuration (``enforce`` defaults to ``False``) that answer
    still lets the wrapped call run, so
    an exception raised here would silently convert "claim refused" into "no
    adjudication was performed at all" and drop the refusal into the audit as
    ``unadjudicated``. Measured on c669bc03: a caller-supplied unhashable
    ``grant_id`` reached ``_grants.get()``, raised ``TypeError``, and the action
    executed with the bad value stamped as ``sovereignty_grant``. Any failure to
    *answer* the question is therefore reported as a refusal
    (``claim-lookup-error``), which keeps the actor on the service default and
    keeps a grant id off the audit record.
    """
    if sov is None or sov.grant_id is None:
        return None
    try:
        from src.kernels._sovereignty import get_grant

        grant = get_grant(sov.grant_id)
        if grant is None:
            return "grant-not-found"
        # F33/D-2: the grant authorises *its own* human, not whoever happens to
        # be holding the window. Without this, a window opened for principal B
        # while carrying A's grant id is re-checked as "live, unrevoked, covers
        # the action" and escalates B to a human allow that, on the allow path,
        # no enforcement tier can refuse -- A's approval would be spent by B.
        # Compared canonically, not by string: the same human may be spelled
        # ``human:bob`` in the grant and ``bob`` in the window.
        if not _same_human(grant.principal, sov.principal):
            return "grant-principal-mismatch"
        if grant.is_revoked:
            return "grant-revoked"
        if grant.is_expired():
            return "grant-expired"
        if action not in grant.actions:
            return "grant-does-not-cover-action"
    except Exception as exc:  # refusal, never a raise -- see the docstring
        logger.warning(
            "sovereignty claim re-check failed for grant_id=%r: %s", sov.grant_id, exc
        )
        return "claim-lookup-error"
    return None


def _adjudicate(
    action: str, risk_level: str
) -> tuple[Optional[str], Optional[str], Dict[str, str]]:
    """Return ``(decision, rule_id, actor)`` recorded for ``action``.

    The actor is the built-in **internal service principal** rather than an
    anonymous ``{"type": "system"}`` actor: the engine recomputes ``verified``
    from the Identity Kernel, so the verdict reflects the real allow-list in
    ``src.kernels.policy.INTERNAL_SERVICE_ALLOWED_ACTIONS`` instead of being a
    constant deny.

    The actor is returned rather than discarded (PHASE 3.6 / A2): it is the best
    available answer to "who did this", and the audit path used to throw it away
    while writing the constant ``"kernel"``.

    ``(None, None, actor)`` means adjudication was unavailable; the decorator is
    additive and must never break or delay the wrapped call on that account.
    """
    try:
        from src.kernels._risk_classification import ENFORCED_TIERS, RiskTier
        from src.kernels._sovereignty import get_active_sovereignty
        from src.kernels.identity import INTERNAL_SERVICE_PRINCIPAL
        from src.kernels.policy import evaluate_policy_simple

        # Policy C-3: if a verified human has delegated authority for THIS
        # action (least-privilege -- only the enumerated actions, and only
        # when the tier is enforcement-gated), adjudicate as that human so the
        # human_sovereignty rule (precedence 1000, OD-010) can allow it.
        # LOW actions stay on the service actor (they are allow-listed there);
        # MEDIUM is never enforcement-gated, so it is never escalated either.
        actor = {"type": "service", "principal": INTERNAL_SERVICE_PRINCIPAL}
        sov = get_active_sovereignty()
        #: True only while the window's claim has been honoured *and* we intend
        #: to record it as the human. Flipped back in the verdict check below.
        claim_held = False
        if sov is not None and action in sov.actions:
            try:
                tier = RiskTier(risk_level)
            except ValueError:
                tier = RiskTier.LOW
            if tier in ENFORCED_TIERS:
                # F33: the window is not authority by itself -- re-check the
                # grant it was opened from against the registry *now*.
                claim = _sovereignty_claim_state(sov, action)
                if claim is None:
                    actor = {"type": "human", "principal": sov.principal}
                    claim_held = True
                else:
                    # Fail-closed: do NOT escalate to the human. The actor stays
                    # the service default (denied for HIGH/CRITICAL), and the
                    # rejection reason rides along so the audit record can say
                    # the sovereignty claim was refused instead of silently
                    # looking like a window that was never opened.
                    actor = {
                        "type": "service",
                        "principal": INTERNAL_SERVICE_PRINCIPAL,
                        "sovereignty_claim": claim,
                    }

        # Request-side human attribution is deliberately NOT an input here.
        # "alice asked for this" is an attribution fact, not an authorization
        # claim: feeding it as ``actor`` would let the caller's identity change
        # the verdict (P10 / human_sovereignty reads actor.type). The initiator
        # is attached only when the audit event is written (_call_audit), so
        # every permission decision stays byte-for-byte unchanged.
        decision = evaluate_policy_simple(
            actor={"type": actor["type"], "principal": actor["principal"]},
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

        # F33/C: human-sovereignty attribution is conditional on the claim
        # actually holding *as adjudicated*. The check above re-verifies the
        # grant; the policy engine independently recomputes ``verified`` from the
        # Identity Kernel (C-7/P10). When that recompute refuses -- the human was
        # suspended/removed after the window opened, or the principal was never a
        # human -- the engine returns deny even though ``actor.type`` said
        # "human". Recording that actor would write ``actor_kind='human'`` +
        # ``actor_source='sovereignty-window'``, and stamp a grant id, for a
        # claim that never held: exactly the false-human record F26/F33 exist to
        # prevent. So the human *shape* is kept only for a verdict it actually
        # produced. The verdict itself is NOT touched -- this decides what is
        # *recorded*, never whether the body runs.
        #
        # ``principal`` deliberately stays ``sov.principal``: the claim is what
        # was refused, and the record must still name who made it (A2) -- only
        # the authority-bearing shape is withdrawn. ``sovereignty_claim`` makes
        # ``_resolve_audit_actor`` mark the row as refused rather than as a live
        # window, and keeps ``grant_id`` off the audit event.
        if claim_held and verdict != "allow":
            actor = {
                "type": "service",
                "principal": sov.principal,
                "sovereignty_claim": "claim-human-not-verified",
            }

        rule_id: Optional[str] = None
        for entry in decision.traceability:
            # Traceability entries look like "RULE:<rule_id>:<action>".
            parts = entry.split(":")
            if len(parts) == 3 and parts[0] == "RULE":
                rule_id = parts[1]
                break

        # --- 完全授权模式：所有执行主体一律放行（2026-11-11 老板拍板） ------- #
        # 覆盖用户手动派活、AI 员工、定时触发、自主接活、多级委派 —— 不再区分
        # 调用来源，也不要求 correlation_id。审计不降级：``policy_rule`` 写明
        # 放行依据，归属（哪个员工/哪个任务）由 ``task_attribution`` 一并落库。
        # 收回是全权模式唯一的例外：设置页总闸走 ``revoke_now``，立即生效。
        if verdict != "allow":
            try:
                from src.security.authority_mode import is_full

                if is_full():
                    verdict = "allow"
                    rule_id = "authority_mode_full"
                    actor = {**actor, "authority_mode": "full"}
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning(
                    "authority-mode check failed for %r: %s", action, exc
                )

        return verdict, rule_id, actor
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("kernel_action policy adjudication failed for %r: %s", action, exc)
        return None, None, _service_actor_policy_shape()


def _evidence_mandatory(effective_risk: Any) -> bool:
    """PHASE 3.6 / CRIT-1C Layer 2 (D17 Option C, ACCEPTED): is the authoritative
    audit Evidence MANDATORY for this action -- i.e. a failure to produce it must
    fail-closed (action MUST NOT proceed)?

    Grounded on the authoritative risk tier (D8): only HIGH/CRITICAL require
    mandatory evidence. LOW/MEDIUM keep the Layer-1 degraded-continue path and
    are explicitly NOT blocked when evidence is unavailable (the boss's grading:
    LOW -> degraded continue; HIGH -> no evidence, no execute; CRITICAL /
    sovereignty-sensitive -> fail-closed). The CRITICAL / sovereignty-sensitive
    actions (``capability.retire``, ``security.set_abac_rule``, trust
    establish/revoke, permissions ...) are all classified HIGH/CRITICAL, so this
    single tier check captures them without inventing a second classification.
    """
    try:
        tier = RiskTier(effective_risk)
    except ValueError:
        return False
    return tier in ENFORCED_TIERS


# --------------------------------------------------------------------------- #
# Agent-Safety Execution Fence (P0 / D19-D21) — default-DENY autonomous gate
# --------------------------------------------------------------------------- #
# The fence is opt-in so the existing 400+ tests keep their behaviour, exactly
# like the audit single-writer fence was rolled out. It is ARMED by the
# deployment (LIUHAO_EXECUTOR_FENCE=on) or by an explicit action allow-list.
# When armed, EVERY fenced action WITHOUT a valid executor fence context is
# denied (default-deny). See src/kernels/execution/fence.py + UBX-001.
_FENCE_ACTIONS: set = set()


# --------------------------------------------------------------------------- #
# 完全授权模式（G2）—— 独立于审批的「不可逆动作放行」埋点
# --------------------------------------------------------------------------- #
# G2 必须由动作本身触发，而不是由审批流程顺带写出：审批被关掉以后，
# HUMAN_SOVEREIGNTY_OVERRIDE 不再产生，但「这条不可逆动作被放行了」这件事
# 仍然必须可查。故此处直接读模式快照 + 风险表，写独立的审计事件。
#: 本次调用是否经过确认（step 3 由确认层写入）：granted / disabled_by_user /
#: no_confirmation_channel。默认 None = 还没有确认通道，按最保守文案记录。
_G2_CONFIRM_STATE: ContextVar[str | None] = ContextVar(
    "authority_confirm_state", default=None
)


def authority_confirm_state():
    """为调用方提供「声明本次确认状态」的上下文（G2 读取）。

    只影响审计文案，不影响权限判定 —— 判定在策略层，证据在这里。
    """

    @contextmanager
    def _ctx(state: str) -> Iterator[None]:
        token = _G2_CONFIRM_STATE.set(state)
        try:
            yield
        finally:
            _G2_CONFIRM_STATE.reset(token)

    return _ctx


@dataclass(frozen=True)
class _TaskAttribution:
    """「这条动作是谁在哪个任务里做的」。缺失即 ``None``，绝不猜。"""

    employee: str | None
    task_id: str | None
    goal_id: str | None


_TASK_ATTR: ContextVar[_TaskAttribution | None] = ContextVar(
    "kernel_action_task_attribution", default=None
)


@contextmanager
def task_attribution(
    *,
    employee: str | None = None,
    task_id: str | None = None,
    goal_id: str | None = None,
) -> Iterator[None]:
    """给任务内产生的动作打上归属（全权模式下的全量审计需要）。

    由执行内核在每条任务开始时绑定；``@kernel_action`` 把它写进审计的
    ``details.task_attribution``，于是「员工自己触发」的动作也能回答：
    哪个员工、哪个任务、哪条 goal。

    ``principal_class`` 固定为 ``SYSTEM_PRINCIPAL``：员工/后台是以系统主体
    身份执行，不是用户的身份。这样事后看记录不会把员工的越权误记成用户本人。
    """
    token = _TASK_ATTR.set(
        _TaskAttribution(
            employee=str(employee) if employee else None,
            task_id=str(task_id) if task_id else None,
            goal_id=str(goal_id) if goal_id else None,
        )
    )
    try:
        yield
    finally:
        _TASK_ATTR.reset(token)


def _task_attribution_payload() -> Dict[str, str | None] | None:
    attr = _TASK_ATTR.get()
    if attr is None:
        return None
    return {
        "principal_class": "SYSTEM_PRINCIPAL",
        "employee": attr.employee,
        "task_id": attr.task_id,
        "goal_id": attr.goal_id,
    }


def _authority_mode_is_full() -> bool:
    """当前进程是否处于完全授权模式（启动期快照，不热切）。"""
    try:
        from src.security.authority_mode import is_full

        return bool(is_full())
    except Exception:  # pragma: no cover - 读取失败按标准模式（更严）
        return False


def _is_irreversible_action(action: str) -> bool:
    """不可逆动作 = 破坏性 OR 改权限 OR CRITICAL。

    判定取自 KERNEL_ACTION_RISK（单一事实来源），不在装饰器里另立一张表，
    避免两套名单漂移。
    """
    try:
        from src.kernels._risk_classification import get_action_risk

        risk = get_action_risk(action)
        if risk is None:
            return False
        return bool(
            risk.destructive
            or risk.authority_changing
            or risk.tier is RiskTier.CRITICAL
        )
    except Exception:  # pragma: no cover
        return False



def _fence_enabled_for(action: str) -> bool:
    env = os.environ.get("LIUHAO_EXECUTOR_FENCE", "").strip().lower()
    if env in ("on", "1", "true", "yes"):
        return True
    return action in _FENCE_ACTIONS


def _enforce_executor_fence_at_gate(action: str, corr_id: str) -> None:
    """Raise PolicyDeniedError (fail-closed) unless a valid fence context is
    bound for this action. Called from the @kernel_action wrapper when the fence
    is armed for ``action``."""
    from src.kernels.execution.fence import (
        ExecutorFenceDenied,
        current_executor_fence,
        get_executor_fence,
    )

    fence_ctx = current_executor_fence()
    try:
        get_executor_fence().enforce(fence_ctx, action, (), correlation_id=corr_id)
    except ExecutorFenceDenied as exc:
        # Hard deny -- never defer an autonomous-execution authorization failure.
        raise PolicyDeniedError(action, "executor-fence", None) from exc


def kernel_action(
    action: str,
    *,
    risk_level: Any = _RISK_UNSET,
    enforce: bool = False,
    audit: bool = True,
    policy: bool = True,
    observable: bool = True,
    system_actor: bool = False,
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
        system_actor: for actions with no request-side human and no enclosing
            agent binding (scheduled cleanup, background retention), record the
            audit subject as ``kind="system"`` instead of falling through to the
            internal service. Attribution only -- it never changes the verdict.
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
            # Traceability fix (audit goal->action link): prefer the correlation
            # id published by the enclosing goal/operation; fall back to a fresh
            # random id when nothing is bound (backward compatible).
            corr_id = KERNEL_ACTION_CORRELATION_ID.get() or uuid.uuid4().hex
            started = time.time()
            decision: Optional[str] = None
            rule_id: Optional[str] = None
            grant_id: Optional[str] = None
            # Who asked for this action. A request route binds the verified
            # human; an enclosing agent keeps its own binding; a background /
            # scheduled action with neither is named "system" explicitly --
            # never the internal service default.
            initiator = _ACTING_PRINCIPAL.get()
            if initiator is None and system_actor:
                initiator = {"kind": "system", "principal": SYSTEM_PRINCIPAL,
                             "source": "scheduled"}

            # A2: the fallback actor, used both when adjudication is disabled and
            # when it failed. ``_adjudicate`` returns the actor it actually used,
            # so a sovereignty-window escalation is recorded as the human rather
            # than as the service.
            policy_actor = _service_actor_policy_shape()
            if policy:
                decision, rule_id, policy_actor = _adjudicate(action, effective_risk)
                # C-4: remember which approval grant (if any) the verdict was
                # reached under, so the audit event closes the chain
                # action <- grant <- authorising human. F33: only when a human
                # escalation actually held -- a rejected window, *and equally* a
                # window that was never even evaluated for this action, must not
                # leave a grant id that reads as the authorisation for the
                # action. "No refusal reason" is not the same as "the human did
                # this": when the action is outside ``sov.actions`` no claim is
                # ever re-checked, so the key is absent while the actor is still
                # the service default. Require the human shape too.
                if (
                    policy_actor.get("type") == "human"
                    and not policy_actor.get("sovereignty_claim")
                ):
                    grant_id = _active_grant_id()
            # The subject written to the audit event: an explicitly bound acting
            # principal outranks the adjudicated actor; neither of them is the
            # constant "kernel" that used to be written here.
            audit_actor = _resolve_audit_actor(
                policy_actor, system_actor=system_actor
            )

            # --- Agent-Safety Execution Fence (P0 / D19-D21) ----------------- #
            # Default-DENY autonomous execution gate. When armed for this action
            # (LIUHAO_EXECUTOR_FENCE=on or an explicit allow-list), an executor
            # fence context MUST be bound or the action is denied. Any fail-closed
            # condition (missing identity, unknown/stale/expired lease, replay,
            # capability escalation, audit unavailable) hard-denies here.
            if _fence_enabled_for(action):
                _enforce_executor_fence_at_gate(action, corr_id)

            # D23: defensive harden — a sovereignty grant is stamped ONLY on the
            # allow path. Any non-allow verdict (deny/error/defer) or a
            # blocked/denied outcome must never carry a grant id, INDEPENDENT of
            # the ``claim_held`` control flow above. This closes the
            # false-human-authorisation class (F26/F33) even if the escalation
            # logic is later refactored, and makes "deny path never gets a grant"
            # a property of the audit writer rather than a side effect of the
            # adjudication branch.
            def _grant_for_audit(audit_decision: Optional[str],
                                 audit_outcome: str) -> Optional[str]:
                if audit_decision in ("deny", "error", "defer") or \
                        audit_outcome in ("denied", "blocked",
                                          "unauthorized_success", "failed"):
                    return None
                return grant_id

            # D23 (optional re-check): before stamping a real grant id, confirm
            # it is still live. A grant revoked/expired after the window opened
            # must not be recorded as live authorisation for this action. Runs
            # only when a real grant id was actually resolved.
            if grant_id is not None:
                try:
                    from src.kernels._sovereignty import get_grant

                    _live = get_grant(grant_id)
                    if _live is None or not _live.is_active():
                        grant_id = None
                except Exception:  # pragma: no cover - defensive
                    grant_id = None

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
            # and require human sovereignty, OD-010), so arming the gate
            # without a human-approval channel would self-lock the system; the
            # C-3 channel plus the C-4 audited grants are what make arming safe.
            #
            # C-4: arming is a *deployment* decision with ONE control point --
            # ``src.kernels._enforcement`` (env ``LIUHAO_KERNEL_POLICY_ENFORCE``).
            # Two separate facts, which must not be conflated:
            #   * no production call site passes ``enforce=True`` -- that is what
            #     the AST guard in ``scripts/verify_c2_enforcement.py`` asserts;
            #   * whether the gate is armed at *runtime* is a deployment choice.
            #     The production manifest arms ``CRITICAL`` by default
            #     (``docker-compose.prod.yml:69``), and
            #     ``scripts/verify_armed_actions_are_inert.py`` keeps CI red if a
            #     change makes an armed action reachable.
            should_enforce = enforce or is_enforced(action, effective_risk)
            try:
                tier = RiskTier(effective_risk)
            except ValueError:
                tier = RiskTier.LOW
            # Value surfaced on every policy INFO line as ``policy_enforced``.
            # It answers "is the cut-line armed for THIS action/tier right now?",
            # which is exactly the record-only-vs-real-block distinction an
            # operator needs mid-incident. (The audit field of the same name has
            # the narrower meaning "was this call actually blocked"; see
            # _call_audit.) LOW/MEDIUM can never be armed -- is_enforced() already
            # narrows on the tier, and the explicit check keeps that contract even
            # when a caller passes enforce=True on a LOW action.
            enforcement_armed = bool(
                policy and should_enforce and tier in ENFORCED_TIERS
            )
            if should_enforce and policy:
                if tier in ENFORCED_TIERS and (decision is None or decision != "allow"):
                    block_ms = (time.time() - started) * 1000.0
                    if audit:
                        _call_audit(
                            audit_actor, action, "blocked", decision or "error",
                            corr_id, block_ms, rule_id, effective_risk,
                            enforced=True,
                            grant_id=_grant_for_audit(decision or "error", "blocked"),
                            initiator=initiator,
                        )
                    if observable:
                        logger.warning(
                            "POLICY ENFORCED kernel_action=%s verdict=%s risk=%s "
                            "rule=%s correlation_id=%s policy_enforced=%s",
                            action, decision or "error", effective_risk,
                            rule_id or "?", corr_id, True,
                        )
                        # D24 (false-positive alert): a STRUCTURED alert so a
                        # legitimately-blocked action is visible and reviewable
                        # WITHOUT parsing prose. False positives are NOT fixed by
                        # flipping the global switch -- a verified human grants
                        # authority via POST /policy/approvals, which opens a
                        # sovereignty window; the blocked action then adjudicates
                        # as that human and is allowed. (Hence the deployment
                        # default stays CRITICAL-only, not a global ON.)
                        logger.warning(
                            "POLICY BLOCK STRUCTURED ALERT: a HIGH/CRITICAL "
                            "action was deferred pending human sovereignty",
                            extra={
                                "alert": {
                                    "kind": "policy_block_false_positive",
                                    "action": action,
                                    "tier": effective_risk,
                                    "verdict": decision or "error",
                                    "rule_id": rule_id,
                                    "correlation_id": corr_id,
                                }
                            },
                        )
                    if decision is None:
                        # fail-closed: the engine was unavailable, so we cannot
                        # even know the verdict. Hard-deny (never defer) -- we
                        # must not wait on a human when we don't know the state.
                        raise PolicyDeniedError(action, "error", rule_id)
                    # The engine *answered* with a non-allow verdict for this
                    # HIGH/CRITICAL action: it requires human sovereignty
                    # (OD-010). Defer it (Policy C-3) so a verified human can
                    # grant authority via the sovereignty channel, instead of
                    # permanently forbidding the action.
                    raise PolicyDeferredError(action, rule_id)

            # --- PHASE 3.6 / CRIT-1C Layer 2 (D17 Option C, ACCEPTED) -------- #
            # For HIGH/CRITICAL actions the authoritative audit Evidence is
            # MANDATORY: if it cannot be produced, the action MUST NOT proceed
            # (fail-closed). We prove the evidence channel is live with a real
            # pre-execution write; if it fails for ANY reason (backend down,
            # timeout, partial write, txn rollback, network/db unavailable,
            # shutdown, retry exhaustion ...) we block -- the action never runs.
            # This is the inverse of the Layer-1 LOW/MEDIUM degraded-continue
            # path and itself never fails open: any failure -> hard deny.
            #
            # NOTE: this gate is independent of ``should_enforce`` -- evidence
            # mandatoriness follows the action's risk tier, not whether the
            # enforcement cut-line is armed. A record-only HIGH/CRITICAL action
            # with no evidence channel is equally unsafe.
            if _evidence_mandatory(effective_risk):
                if not audit:
                    logger.error(
                        "MANDATORY-EVIDENCE action without audit channel: "
                        "action=%s risk=%s -- fail-closed", action, effective_risk,
                    )
                    raise PolicyDeniedError(action, "error", rule_id)
                try:
                    # Real pre-execution write. ``reraise=True`` so a backend
                    # failure propagates instead of being swallowed by the
                    # defensive except in _call_audit (which would silently turn
                    # "block" into "ran anyway" -- the exact fail-open we forbid).
                    _call_audit(
                        audit_actor, action, "intent", decision or "unadjudicated",
                        corr_id, 0.0, rule_id, effective_risk,
                        grant_id=_grant_for_audit(
                            decision or "unadjudicated", "intent"
                        ),
                        reraise=True,
                        initiator=initiator,
                    )
                except PolicyDeniedError:
                    raise
                except Exception:
                    logger.error(
                        "AUDIT EVIDENCE UNAVAILABLE for mandatory-evidence "
                        "action=%s risk=%s correlation_id=%s -- fail-closed",
                        action, effective_risk, corr_id, exc_info=True,
                    )
                    raise PolicyDeniedError(action, "error", rule_id)

            # --- AUDIT TRUTHFULNESS (Phase 3.6, owner directive) -------------- #
            # A definitive policy verdict of "deny" (or "defer") must NEVER be
            # recorded as outcome="success". Previously the record-only (C-1)
            # posture executed the wrapped function anyway and labelled the
            # event "success", silently hiding the refusal: a reviewer seeing
            # outcome="success" could not tell the action had been policy-denied.
            # The body is still permitted to *proceed* under the record-only
            # posture (so the operational loop is never self-locked and no
            # existing authority feature regresses), but the audit now honestly
            # surfaces the refusal by recording outcome="denied" alongside
            # policy_decision="deny" + policy_enforced=False (see the finally
            # block below). Arming the C-2 enforcement gate to actually *block*
            # such actions is a separate, owner-approved OD-010 deployment
            # decision; this change fixes only the content lie in the audit.
            # (mark_action_denied() still sets "denied" for an in-body refusal;
            # the new branch additionally catches the policy verdict.)

            # U-1: start from a clean slate, so a denial declared by an earlier
            # (or nested) call can never be attributed to this one.
            _DENIED_SIGNAL.set(None)
            _FAILED_SIGNAL.set(None)
            outcome = "success"
            denial_reason: Optional[str] = None
            try:
                result = fn(*args, **kwargs)
            except Exception:
                outcome = "failure"
                raise
            finally:
                if outcome == "success":
                    # A refusal reported by returning is still a refusal. Only an
                    # explicit mark_action_denied() counts -- inferring it from a
                    # falsy return would silently redefine ``outcome`` for every
                    # existing call site.
                    denial_reason = _consume_denial()
                    if denial_reason is not None:
                        # Genuine refusal declared by the body (e.g. a permission
                        # grant the kernel rejected, or a route with no adapter).
                        # The action did NOT happen -> "denied".
                        outcome = "denied"
                    elif policy and decision in ("deny", "defer"):
                        # AUDIT TRUTHFULNESS (HC-01 residual / mirror distortion):
                        # the policy engine returned a definitive non-allow verdict
                        # for this actor, yet the record-only (C-1) posture let the
                        # body run. Do NOT masquerade that as "success" AND do NOT
                        # call it "denied" (which would falsely imply the action
                        # was blocked). Record it as ``unauthorized_success`` -- the
                        # action executed but was never authorised. This makes the
                        # mirror distortion visible on the chain instead of hiding
                        # it behind a clean-looking "denied" that a reviewer would
                        # trust as "this was refused".
                        verdict_label = "deny" if decision == "deny" else "defer"
                        outcome = "unauthorized_success"
                        denial_reason = f"policy:{verdict_label}(rule={rule_id})"
                    else:
                        failure_reason = _consume_failure()
                        if failure_reason is not None:
                            # The body ran but did not complete successfully
                            # (e.g. a routing delivery failure where the matched
                            # adapter's send() returned False). Not a refusal and
                            # not a success -> "failed".
                            outcome = "failed"
                            denial_reason = failure_reason
                duration_ms = (time.time() - started) * 1000.0
                if audit:
                    _call_audit(audit_actor, action, outcome, decision, corr_id,
                                duration_ms, rule_id, effective_risk,
                                grant_id=_grant_for_audit(
                                    decision or "unadjudicated", outcome
                                ),
                                denial_reason=denial_reason,
                                initiator=initiator)
                # --- G2: 完全授权模式下的不可逆动作放行 ------------------- #
                # 写在动作结果审计之后：只有动作真的跑完（success /
                # unauthorized_success）才记「放行」，避免把被拒的动作谎报成
                # allowed。这条记录**不经过审批流程**：审批关掉后它照样写，
                # 这正是「放开权限不能把证据链弄断」的落点。
                if (
                    audit
                    and outcome in ("success", "unauthorized_success")
                    and _authority_mode_is_full()
                    and _is_irreversible_action(action)
                ):
                    try:
                        from src.security.authority_audit import (
                            record_irreversible_allowed,
                        )

                        record_irreversible_allowed(
                            action=action,
                            tier=effective_risk,
                            actor=audit_actor,
                            correlation_id=corr_id,
                            confirm_state=(
                                _G2_CONFIRM_STATE.get()
                                or "no_confirmation_channel"
                            ),
                        )
                    except Exception:
                        # 动作已经发生，无法回滚；但绝不允许静默 —— 记错误日志
                        # 让运维从日志侧补证，且这条失败本身会在下一轮巡检暴露。
                        logger.error(
                            "authority G2 audit write failed action=%s "
                            "correlation_id=%s", action, corr_id, exc_info=True,
                        )
                if observable:
                    logger.info(
                        "kernel_action=%s outcome=%s policy=%s risk=%s "
                        "duration_ms=%.3f correlation_id=%s policy_enforced=%s",
                        action, outcome, decision or "unadjudicated",
                        effective_risk, duration_ms, corr_id,
                        enforcement_armed,
                    )

            return result

        return wrapper

    return decorator
