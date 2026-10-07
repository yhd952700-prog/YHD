"""Policy C-3 / C-4 — dynamic human-principal channel + audited approval grants.

When a HIGH/CRITICAL ``@kernel_action`` is enforced, the internal *service*
principal is always denied: it is not in the C-1 allow-list and those actions
require human sovereignty (OD-010). To perform such an action the system must
therefore act *under a verified human's authority*. This module provides a
*contextual* channel for exactly that.

A human operator (whose identity is verified by the Identity Kernel) may
delegate authority for a **specific, enumerated** set of kernel actions. While
that delegation is active, those actions are adjudicated as the human actor and
so pass the ``human_sovereignty`` rule (precedence 1000). The delegation is
least-privilege by construction: any action *outside* the enumerated set is
adjudicated as the internal service principal and stays denied.

C-4 — why a grant object, not just a context manager?
    :class:`human_sovereign` alone only proves that the *named* principal is a
    registered ACTIVE non-service identity. It does **not** prove that the
    caller *is* that human: any code path that can name a valid human id could
    open a window. The remedy is to make the authorization itself a
    first-class, auditable object -- :class:`SovereigntyGrant` -- recording
    *who* authorized *which* actions, *why*, *until when*, and, on revocation,
    *by whom*. Opening a window from a grant (:func:`grant_window`) then yields
    a complete causal chain in the audit log:

        kernel action  <-  sovereignty grant  <-  the human who issued it

    The grant is bounded (TTL), revocable, restricted to actions that the
    enforcement gate actually governs (HIGH/CRITICAL), and refuses unknown
    action names -- so it can never be a blank cheque.

Target binding (why a grant names a resource, not just an action)
    The unit of authorisation is the triple **(principal, action, target)**.
    Without a target, an approval "to retire a capability" could be spent on
    *any* capability: the grant would look perfectly normal on the chain (a
    real issuer, a real reason, a real TTL) while quietly covering more than
    was reviewed. ``target`` is therefore mandatory with no default, the only
    expression of "all targets" is the explicit :data:`WILDCARD_TARGET` (which
    needs secondary confirmation), and :func:`grant_window` refuses -- and
    audits -- a use against a target the grant does not cover. A missing field
    must never widen authority.

    This is an independent, stricter namespace than RBAC's ``Permission.target``
    (which is ``Optional`` and treats ``None`` as a wildcard) and ABAC's
    ``ABACResource.resource_id``. Sovereignty is the highest-precedence boundary
    and may only be stricter than the layers below it, never inherit their
    looser semantics.

Issuing a grant still does not, by itself, grant anything at runtime: the
policy engine recomputes ``verified`` from the Identity Kernel at adjudication
time. The grant layer narrows *and records*; it cannot manufacture an allow
the engine would deny.

OD-010 is never weakened:
    The actor's ``verified`` flag is recomputed by the policy engine from the
    Identity Kernel (``_compute_verified`` / ``_is_verified_human``), which
    also rejects a ``service``-kind identity. So a spoofed principal
    (non-existent, inactive, or a service identity masquerading as human) is
    rejected and the action falls back to the service deny. The channel only
    *narrows* which actions may be adjudicated as human; it cannot invent an
    allow where the engine would deny.

Zero production behaviour change by default:
    The channel is OFF unless some caller explicitly opens it via
    :func:`human_sovereign` / :func:`set_active_sovereignty` / :func:`grant_window`.
    With no active sovereignty, ``_adjudicate`` keeps using the internal service
    principal and every production call site stays at ``enforce=False``. That is
    the *library* default and is all this claims: the production manifest arms
    ``CRITICAL`` (``docker-compose.prod.yml:69``), so "record-only" must not
    be read as a deployment fact.
"""

from __future__ import annotations

import contextvars
import logging
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Dict, FrozenSet, Iterable, List, Optional

logger = logging.getLogger("liuhao.kernel.sovereignty")

#: Default lifetime of an approval grant (seconds).
DEFAULT_GRANT_TTL_SECONDS = 300.0

#: Hard ceiling for a single grant -- an approval must not be open-ended.
MAX_GRANT_TTL_SECONDS = 3600.0

#: The ONLY legal expression of "all targets". A grant must name a concrete
#: target; wildcard authority must be written explicitly, never inferred from a
#: missing field. See :func:`_validated_target`.
WILDCARD_TARGET = "*"

#: Principal recorded on grant lifecycle audit events.
SOVEREIGNTY_AUDIT_PRINCIPAL = "liuhao.sovereignty"


class SovereigntyDeniedError(PermissionError):
    """A sovereignty authorization was REFUSED.

    Deliberately a ``PermissionError`` (like ``PolicyDeniedError``) so an appeal
    handler maps it to "denied" rather than letting it fall through to a generic
    500 -- turning "you are not authorised for this target" into "the service is
    broken" would hide the refusal, which is exactly the semantics governance
    forbids. It is also a refusal of an authorization, not a transport fault.
    """


class SovereigntyTargetMismatchError(SovereigntyDeniedError):
    """A grant was used against a target it does not cover.

    Raised by :func:`grant_window` when the requested target is neither the
    grant's target nor covered by an explicit wildcard grant. The attempt is
    audited before this is raised: an actor probing which targets they hold
    authority over is doing something worth recording, not merely a mistake to
    swallow.
    """

    def __init__(self, grant_target: str, requested_target: str, grant_id: str = ""):
        self.grant_target = grant_target
        self.requested_target = requested_target
        self.grant_id = grant_id
        super().__init__(
            f"sovereignty grant {grant_id or '<unknown>'} authorises target "
            f"{grant_target!r}, not {requested_target!r} -- cross-target use "
            "refused (delegation is per (principal, action, target))"
        )


@dataclass(frozen=True)
class ActiveSovereignty:
    """A verified human's delegated authority for a set of kernel actions."""

    principal: str
    actions: FrozenSet[str]
    granted_at: float
    #: C-4: the grant this window was opened from, when there was one.
    grant_id: Optional[str] = None
    #: The concrete target this delegation applies to. ``None`` only for the bare
    #: C-3 channel (no grant); a window opened from a grant always carries the
    #: grant's target (or the concrete target a wildcard grant was used for).
    target: Optional[str] = None
    reason: str = ""


@dataclass(frozen=True)
class SovereigntyGrant:
    """An auditable, expiring, revocable authorization for kernel actions.

    Deliberately *not* usable as a capability: possession of a grant object
    proves nothing to the policy engine, which always re-verifies the human
    identity from the Identity Kernel. The grant's value is accountability --
    it answers "who authorized this, for what, and until when".

    The unit of authorisation is the triple **(principal, action, target)**.
    ``target`` is mandatory and has no default: a grant that omits it would be a
    grant over every target, and an omitted field silently widening authority is
    the classic fail-open shape. The only way to authorise every target is to
    write :data:`WILDCARD_TARGET` explicitly, which is itself a higher
    sovereignty act (see :func:`issue_grant`).
    """

    grant_id: str
    principal: str
    actions: FrozenSet[str]
    #: The concrete resource this grant applies to, or :data:`WILDCARD_TARGET`.
    target: str
    reason: str
    issued_by: str
    granted_at: float
    expires_at: float
    revoked_at: Optional[float] = None
    revoke_reason: str = ""

    @property
    def is_wildcard(self) -> bool:
        """True when this grant covers every target."""
        return self.target == WILDCARD_TARGET

    def covers_target(self, target: str) -> bool:
        """True iff this grant authorises action on ``target``.

        A wildcard grant covers everything; otherwise the target must match
        exactly. There is no prefix/globbing: "prod-*" would invite a matching
        bug where a broader string than intended passes, so it is not offered.
        """
        return self.is_wildcard or self.target == target

    def is_expired(self, now: Optional[float] = None) -> bool:
        """True once the grant's TTL has elapsed."""
        return (now if now is not None else time.time()) >= self.expires_at

    @property
    def is_revoked(self) -> bool:
        """True once the grant has been explicitly revoked."""
        return self.revoked_at is not None

    def is_active(self, now: Optional[float] = None) -> bool:
        """True iff not revoked and not expired."""
        return not self.is_revoked and not self.is_expired(now)

    def remaining_seconds(self, now: Optional[float] = None) -> float:
        """Seconds left before expiry (0.0 once expired)."""
        return max(0.0, self.expires_at - (now if now is not None else time.time()))

    def to_dict(self) -> Dict[str, object]:
        """JSON-friendly projection (used by the approval API).

        ``target`` and ``is_wildcard`` are part of the projection on purpose: an
        API that hides the target would let a reviewer read a narrow approval as
        if it were broader (or vice versa), so the authorised scope must be
        visible wherever the grant is.
        """
        return {
            "grant_id": self.grant_id,
            "principal": self.principal,
            "actions": sorted(self.actions),
            "target": self.target,
            "is_wildcard": self.is_wildcard,
            "reason": self.reason,
            "issued_by": self.issued_by,
            "granted_at": self.granted_at,
            "expires_at": self.expires_at,
            "revoked_at": self.revoked_at,
            "revoke_reason": self.revoke_reason,
            "is_active": self.is_active(),
            "remaining_seconds": round(self.remaining_seconds(), 3),
        }


# --------------------------------------------------------------------------- #
# Context-local delegation (C-3)
# --------------------------------------------------------------------------- #

# Context-local: the delegation lives only for the dynamic extent (e.g. one
# user-approved operation) and is invisible to other concurrent tasks.
_active: contextvars.ContextVar[Optional[ActiveSovereignty]] = contextvars.ContextVar(
    "liuhao_sovereignty", default=None
)


def get_active_sovereignty() -> Optional[ActiveSovereignty]:
    """Return the currently active delegation, or ``None`` if none is open."""
    return _active.get()


def set_active_sovereignty(sov: Optional[ActiveSovereignty]) -> None:
    """Set (or clear, with ``None``) the active delegation for this context."""
    _active.set(sov)


def clear_active_sovereignty() -> None:
    """Clear any active delegation in this context."""
    _active.set(None)


class human_sovereign:
    """Context manager: run the block under a verified human's delegated authority.

    Args:
        principal: the human identity id / principal that has authorized the
            actions. It must reference an **ACTIVE, non-service** identity
            (verified lazily by the policy engine); otherwise the actions are
            denied. The engine is the source of truth -- this manager performs
            no verification of its own.
        actions: the specific kernel actions the human has authorized. Any
            action outside this set is adjudicated as the internal service
            principal (denied) -- delegation is least-privilege by design.
        granted_at: when the delegation was authorised (defaults to now). Pass
            ``grant.granted_at`` when opening from a :class:`SovereigntyGrant`
            so the window reports the grant's real authorisation time.
        grant_id: (C-4) the approval grant this window was opened from, if any.
            Propagated into the kernel-action audit event so a reviewer can
            trace an allowed HIGH/CRITICAL action back to its authoriser. Must be
            a ``str`` (or ``None`` for the bare C-3 channel): the adjudication
            path uses it as a registry key, so a non-string value could only ever
            break the lookup that decides whether the claim still holds.
        target: the concrete resource this delegation applies to. ``None`` only
            for the bare C-3 channel (no grant). A window opened from a grant
            always carries a concrete target -- see :func:`grant_window`.
        reason: free-text justification, propagated into audit records.

    Example::

        with human_sovereign(human_id, ["capability.retire"]):
            kernel.capability_retire(...)   # adjudicated as the human -> allowed
    """

    def __init__(
        self,
        principal: str,
        actions: Iterable[str],
        *,
        granted_at: Optional[float] = None,
        grant_id: Optional[str] = None,
        target: Optional[str] = None,
        reason: str = "",
    ) -> None:
        # Boundary guard (F33/A): ``grant_id`` is used downstream as a
        # ``_grants`` dict key. An unhashable value (e.g. a JSON list or object
        # echoed straight from a request) raised ``TypeError`` inside the
        # adjudication-time re-check, and the surrounding handler turned that
        # into "adjudication unavailable" rather than "refused". Refusing it here
        # means the trigger cannot be constructed in the first place.
        if grant_id is not None and not isinstance(grant_id, str):
            raise ValueError(
                f"grant_id must be a str or None, got {type(grant_id).__name__}"
            )
        self._sov = ActiveSovereignty(
            principal=principal,
            actions=frozenset(actions),
            granted_at=granted_at if granted_at is not None else time.time(),
            grant_id=grant_id,
            target=target,
            reason=reason,
        )
        self._token: Optional[contextvars.Token] = None

    def __enter__(self) -> ActiveSovereignty:
        self._token = _active.set(self._sov)
        return self._sov

    def __exit__(self, *exc: object) -> None:
        if self._token is not None:
            _active.reset(self._token)


# --------------------------------------------------------------------------- #
# Approval grants (C-4)
# --------------------------------------------------------------------------- #

_grants: Dict[str, SovereigntyGrant] = {}
_grants_lock = threading.RLock()


def _audit(outcome: str, principal: str, details: Dict[str, object]) -> None:
    """Record a grant lifecycle event; audit failure must not break the flow."""
    try:
        from src.kernels.audit import AuditEventType, AuditScope, log_event

        log_event(
            event_type=AuditEventType.HUMAN_SOVEREIGNTY_OVERRIDE,
            principal_id=principal or SOVEREIGNTY_AUDIT_PRINCIPAL,
            scope=AuditScope.L0,
            outcome=outcome,
            details=details,
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("sovereignty audit failed (%s): %s", outcome, exc)


def _validated_principal(principal: str) -> str:
    """Fail fast on a principal that could never pass ``human_sovereignty``."""
    pid = (principal or "").strip()
    if not pid:
        raise ValueError("sovereignty grant requires a principal")
    from src.kernels.identity import IdentityStatus, get_identity_manager

    mgr = get_identity_manager()
    ident = mgr.get_identity(pid) or mgr.get_identity_by_principal(pid)
    if ident is None:
        raise ValueError(f"unknown principal for sovereignty grant: {pid!r}")
    if ident.status != IdentityStatus.ACTIVE:
        raise ValueError(f"principal is not ACTIVE: {pid!r}")
    # Positive allowlist (Policy C-7): only an identity registered as a human
    # may hold human sovereignty. The old reverse exclusion ("kind is not
    # service") failed open for any identity lacking the marker -- notably the
    # built-in ``system`` account -- so a machine could be recorded as the
    # approver. Register humans with
    # ``IdentityManager.create_human_identity(...)`` or
    # ``scripts/register_human_identity.py``.
    from src.kernels.identity import HUMAN_KIND, METADATA_KIND_KEY, is_human_identity
    if not is_human_identity(ident):
        metadata = ident.metadata if isinstance(ident.metadata, dict) else {}
        kind = metadata.get(METADATA_KIND_KEY)
        # Name the actual reason: "you are a service" and "you are not
        # registered as a human" are different problems with different fixes,
        # and an operator staring at a 400 deserves to know which.
        if kind == "service":
            raise ValueError(f"service identity may not hold human sovereignty: {pid!r}")
        raise ValueError(
            f"principal is not a registered human "
            f"(metadata.{METADATA_KIND_KEY} must be {HUMAN_KIND!r}, got {kind!r}), "
            f"so it may not hold human sovereignty: {pid!r}"
        )
    return pid


def _validated_actions(actions: Iterable[str]) -> FrozenSet[str]:
    """Restrict a grant to known, enforcement-gated (HIGH/CRITICAL) actions.

    Two refusals, both deliberate:

    * an **unknown** action name means the caller is confused (or probing) --
      reject rather than store an inert entry;
    * a **LOW/MEDIUM** action is never escalated by the C-3 channel, so a grant
      covering it would be a no-op that nonetheless *looks* like authority.
      Silently dropping it would hide the misunderstanding, so we reject.
    """
    from src.kernels._risk_classification import (
        ALL_CLASSIFIED_ACTIONS,
        ENFORCED_TIERS,
        RiskTier,
        get_kernel_action_risk,
    )

    names = frozenset(a for a in actions if a)
    if not names:
        raise ValueError("sovereignty grant requires at least one action")

    unknown = sorted(names - ALL_CLASSIFIED_ACTIONS)
    if unknown:
        raise ValueError(
            "unknown kernel action(s) in sovereignty grant: " + ", ".join(unknown)
        )

    not_gated = sorted(
        a for a in names if RiskTier(get_kernel_action_risk(a)) not in ENFORCED_TIERS
    )
    if not_gated:
        raise ValueError(
            "sovereignty grant only governs enforcement-gated actions "
            f"({', '.join(t.value for t in sorted(ENFORCED_TIERS, key=lambda t: t.rank))}); "
            "not eligible: " + ", ".join(not_gated)
        )
    return names


def get_kernel_action_risk_safe(action: str) -> str:
    """``risk_level`` lookup that never raises (used for audit payloads)."""
    try:
        from src.kernels._risk_classification import get_kernel_action_risk

        return get_kernel_action_risk(action)
    except Exception:  # pragma: no cover - defensive
        return "UNKNOWN"


def _validated_target(target: Optional[str], *, allow_wildcard: bool = False) -> str:
    """Validate a grant/window target. Fail closed; never infer a default.

    Refusals, all deliberate:

    * ``None`` / non-``str`` / blank -- a missing target must NEVER be read as
      "all targets". That inference is the fail-open shape this whole field
      exists to remove, so it is rejected at the boundary rather than coerced.
    * :data:`WILDCARD_TARGET` without ``allow_wildcard=True`` -- a grant over
      every target is a higher sovereignty act than a grant over one resource
      and must be requested explicitly, not stumbled into.
    """
    if target is None:
        raise ValueError(
            "sovereignty grant requires an explicit target; pass the concrete "
            "resource the authorisation applies to, or the explicit wildcard "
            f"{WILDCARD_TARGET!r} with allow_wildcard=True. A missing target is "
            "refused rather than treated as 'all targets'."
        )
    if not isinstance(target, str):
        raise ValueError(f"target must be a str, got {type(target).__name__}")
    t = target.strip()
    if not t:
        raise ValueError("target must be a non-empty string")
    if t == WILDCARD_TARGET and not allow_wildcard:
        raise ValueError(
            f"target {WILDCARD_TARGET!r} authorises EVERY target and is a "
            "higher-sovereignty act: it requires explicit secondary "
            "confirmation (allow_wildcard=True). Prefer a concrete target."
        )
    return t


def issue_grant(
    principal: str,
    actions: Iterable[str],
    *,
    target: str,
    reason: str = "",
    ttl_seconds: float = DEFAULT_GRANT_TTL_SECONDS,
    issued_by: str = "",
    allow_wildcard: bool = False,
) -> SovereigntyGrant:
    """Record an audited authorization for ``actions`` on ``target``.

    ``target`` is mandatory and keyword-only, with NO default. This is
    deliberate: the unit of authorisation is (principal, action, target), and a
    defaulted/omitted target would silently mean "every target" -- letting one
    approval cover resources it was never reviewed for. To authorise every
    target you must pass ``target=WILDCARD_TARGET, allow_wildcard=True``, which
    is recorded distinctly on the audit chain.

    Raises:
        ValueError: empty/unknown/not-gated actions, a bad TTL, a principal
            that is unknown, inactive, or a service identity, or a missing /
            blank / unconfirmed-wildcard target.

    The returned grant is *evidence of authorisation*, not a runtime capability:
    the policy engine still re-verifies the human at adjudication time.
    """
    pid = _validated_principal(principal)
    names = _validated_actions(actions)
    tgt = _validated_target(target, allow_wildcard=allow_wildcard)

    ttl = float(ttl_seconds)
    if not (0 < ttl <= MAX_GRANT_TTL_SECONDS):
        raise ValueError(
            f"ttl_seconds must be in (0, {MAX_GRANT_TTL_SECONDS}], got {ttl_seconds!r}"
        )

    now = time.time()
    grant = SovereigntyGrant(
        grant_id=uuid.uuid4().hex,
        principal=pid,
        actions=names,
        target=tgt,
        reason=reason or "",
        issued_by=issued_by or pid,
        granted_at=now,
        expires_at=now + ttl,
    )
    with _grants_lock:
        _grants[grant.grant_id] = grant

    _audit(
        "granted",
        pid,
        {
            "grant_id": grant.grant_id,
            "principal": pid,
            "issued_by": grant.issued_by,
            "actions": sorted(names),
            "target": tgt,
            # Wildcard ISSUANCE is recorded as its own visible fact, so "this is
            # an all-targets grant" is never something a reader has to infer
            # from a missing field.
            "is_wildcard": grant.is_wildcard,
            "risk_levels": {
                a: get_kernel_action_risk_safe(a) for a in sorted(names)
            },
            "reason": grant.reason,
            "ttl_seconds": ttl,
            "expires_at": grant.expires_at,
        },
    )
    return grant


def revoke_grant(grant_id: str, *, revoked_by: str = "", reason: str = "") -> SovereigntyGrant:
    """Revoke a grant. Idempotent: re-revoking returns the stored grant.

    Raises:
        KeyError: no grant with that id.
    """
    with _grants_lock:
        grant = _grants.get(grant_id)
        if grant is None:
            raise KeyError(f"unknown sovereignty grant: {grant_id!r}")
        if grant.is_revoked:
            return grant
        revoked = SovereigntyGrant(
            grant_id=grant.grant_id,
            principal=grant.principal,
            actions=grant.actions,
            # MUST carry the target through: a rebuild that dropped it would
            # silently widen a scoped approval to "all targets" on its way to
            # the revoked state. See the revoke-fidelity regression test.
            target=grant.target,
            reason=grant.reason,
            issued_by=grant.issued_by,
            granted_at=grant.granted_at,
            expires_at=grant.expires_at,
            revoked_at=time.time(),
            revoke_reason=reason or "",
        )
        _grants[grant_id] = revoked

    _audit(
        "revoked",
        revoked.principal,
        {
            "grant_id": revoked.grant_id,
            "principal": revoked.principal,
            "revoked_by": revoked_by or revoked.principal,
            "reason": revoked.revoke_reason,
            "actions": sorted(revoked.actions),
            "target": revoked.target,
            "is_wildcard": revoked.is_wildcard,
        },
    )
    return revoked


def get_grant(grant_id: str) -> Optional[SovereigntyGrant]:
    """Return the grant with ``grant_id`` (even if expired/revoked), or ``None``."""
    with _grants_lock:
        return _grants.get(grant_id)


def revoke_grants_for_principal(principal: str, *, reason: str = "") -> int:
    """Revoke every outstanding grant held by ``principal``; return the count.

    The single-grant :func:`revoke_grant` needs a ``grant_id``, which an
    operator suspending a human does not have: they know the *person*, not the
    identifiers of the approvals that person happens to hold. Without this,
    suspending someone could leave live grants behind, and those grants are
    what actually authorise a HIGH/CRITICAL action at adjudication time.

    Covers grants in any state that is still ``is_active`` -- i.e. un-revoked
    and un-expired. Already-revoked and already-expired grants are left alone
    so the count reflects what this call actually took away.

    The principal is compared as recorded on the grant (``grant.principal``).
    A caller spelling the human ``human:bob`` where the grant says ``bob``
    still matches: identity code normalises both forms, and a missed match
    here would mean an approval surviving the suspension of its owner.
    """
    from src.kernels.identity import normalize_principal

    wanted = normalize_principal(principal).strip()
    revoked = 0
    with _grants_lock:
        for grant_id, grant in list(_grants.items()):
            if normalize_principal(grant.principal).strip() != wanted:
                continue
            if not grant.is_active():
                continue
            _grants[grant_id] = SovereigntyGrant(
                grant_id=grant.grant_id,
                principal=grant.principal,
                actions=grant.actions,
                # Same revoke-fidelity rule as revoke_grant: preserve the target,
                # or recalling a person's approval silently widens its scope.
                target=grant.target,
                reason=grant.reason,
                issued_by=grant.issued_by,
                granted_at=grant.granted_at,
                expires_at=grant.expires_at,
                revoked_at=time.time(),
                revoke_reason=reason or "principal suspended",
            )
            revoked += 1
    if revoked:
        _audit(
            "revoked",
            principal,
            {
                "principal": principal,
                "revoked_by": principal,
                "reason": reason or "principal suspended",
                "grants_recalled": revoked,
            },
        )
    return revoked


def list_grants(
    *, include_expired: bool = False, include_revoked: bool = False
) -> List[SovereigntyGrant]:
    """List grants, newest first. Expired/revoked ones are hidden by default."""
    now = time.time()
    with _grants_lock:
        grants = list(_grants.values())
    if not include_expired:
        grants = [g for g in grants if not g.is_expired(now)]
    if not include_revoked:
        grants = [g for g in grants if not g.is_revoked]
    return sorted(grants, key=lambda g: g.granted_at, reverse=True)


def grant_window(grant: SovereigntyGrant, *, target: str) -> human_sovereign:
    """Open a delegation window bound to an **active** grant, for ``target``.

    The requested ``target`` must be the one the grant was issued for. A grant
    for target A cannot be used to act on target B: that cross-target reuse is
    exactly the hole this parameter closes, and it is refused AND audited, so an
    attempt to probe which targets an approval covers leaves a record rather
    than disappearing.

    A wildcard grant (:data:`WILDCARD_TARGET`) covers any concrete ``target``;
    the wildcard is only permitted on the *issue* side (with explicit
    confirmation), never as the target of a use -- you act on a specific
    resource, so a use must name it.

    Raises:
        ValueError: the grant is revoked or expired -- approval is not a
            permanent key, so a stale grant must not silently work; or the
            requested target is missing/blank/wildcard.
        SovereigntyTargetMismatchError: the requested target is not covered by
            the grant. A ``PermissionError`` subclass, so the refusal reads as
            "denied", not as an internal fault.
    """
    if grant.is_revoked:
        raise ValueError(f"sovereignty grant is revoked: {grant.grant_id}")
    if grant.is_expired():
        raise ValueError(f"sovereignty grant is expired: {grant.grant_id}")

    # The USE side never accepts a wildcard: a concrete target must be named so
    # the audit records what was actually acted upon.
    requested = _validated_target(target, allow_wildcard=False)

    if not grant.covers_target(requested):
        # Record the attempt itself: "used a target-A grant to touch target B"
        # is the security-relevant event, and it is what makes enumeration
        # attempts visible instead of free.
        _audit(
            "denied",
            grant.principal,
            {
                "grant_id": grant.grant_id,
                "principal": grant.principal,
                "reason": "target mismatch",
                "grant_target": grant.target,
                "requested_target": requested,
                "actions": sorted(grant.actions),
            },
        )
        raise SovereigntyTargetMismatchError(
            grant.target, requested, grant_id=grant.grant_id
        )

    return human_sovereign(
        grant.principal,
        grant.actions,
        granted_at=grant.granted_at,
        grant_id=grant.grant_id,
        target=requested,
        reason=grant.reason,
    )


def clear_grants() -> None:
    """Drop all grants from the in-process registry (test/teardown helper)."""
    with _grants_lock:
        _grants.clear()
