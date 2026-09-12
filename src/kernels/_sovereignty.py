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
    principal and every production call site stays at ``enforce=False``, so the
    kernel layer remains record-only exactly as it was in C-1/C-2.
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

#: Principal recorded on grant lifecycle audit events.
SOVEREIGNTY_AUDIT_PRINCIPAL = "liuhao.sovereignty"


@dataclass(frozen=True)
class ActiveSovereignty:
    """A verified human's delegated authority for a set of kernel actions."""

    principal: str
    actions: FrozenSet[str]
    granted_at: float
    #: C-4: the grant this window was opened from, when there was one.
    grant_id: Optional[str] = None
    reason: str = ""


@dataclass(frozen=True)
class SovereigntyGrant:
    """An auditable, expiring, revocable authorization for kernel actions.

    Deliberately *not* usable as a capability: possession of a grant object
    proves nothing to the policy engine, which always re-verifies the human
    identity from the Identity Kernel. The grant's value is accountability --
    it answers "who authorized this, for what, and until when".
    """

    grant_id: str
    principal: str
    actions: FrozenSet[str]
    reason: str
    issued_by: str
    granted_at: float
    expires_at: float
    revoked_at: Optional[float] = None
    revoke_reason: str = ""

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
        """JSON-friendly projection (used by the approval API)."""
        return {
            "grant_id": self.grant_id,
            "principal": self.principal,
            "actions": sorted(self.actions),
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
            trace an allowed HIGH/CRITICAL action back to its authoriser.
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
        reason: str = "",
    ) -> None:
        self._sov = ActiveSovereignty(
            principal=principal,
            actions=frozenset(actions),
            granted_at=granted_at if granted_at is not None else time.time(),
            grant_id=grant_id,
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


def issue_grant(
    principal: str,
    actions: Iterable[str],
    *,
    reason: str = "",
    ttl_seconds: float = DEFAULT_GRANT_TTL_SECONDS,
    issued_by: str = "",
) -> SovereigntyGrant:
    """Record an audited authorization for ``actions`` held by ``principal``.

    Raises:
        ValueError: empty/unknown/not-gated actions, a bad TTL, or a principal
            that is unknown, inactive, or a service identity.

    The returned grant is *evidence of authorisation*, not a runtime capability:
    the policy engine still re-verifies the human at adjudication time.
    """
    pid = _validated_principal(principal)
    names = _validated_actions(actions)

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
        },
    )
    return revoked


def get_grant(grant_id: str) -> Optional[SovereigntyGrant]:
    """Return the grant with ``grant_id`` (even if expired/revoked), or ``None``."""
    with _grants_lock:
        return _grants.get(grant_id)


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


def grant_window(grant: SovereigntyGrant) -> human_sovereign:
    """Open a delegation window bound to an **active** grant.

    Raises:
        ValueError: the grant is revoked or expired -- approval is not a
            permanent key, so a stale grant must not silently work.
    """
    if grant.is_revoked:
        raise ValueError(f"sovereignty grant is revoked: {grant.grant_id}")
    if grant.is_expired():
        raise ValueError(f"sovereignty grant is expired: {grant.grant_id}")
    return human_sovereign(
        grant.principal,
        grant.actions,
        granted_at=grant.granted_at,
        grant_id=grant.grant_id,
        reason=grant.reason,
    )


def clear_grants() -> None:
    """Drop all grants from the in-process registry (test/teardown helper)."""
    with _grants_lock:
        _grants.clear()
