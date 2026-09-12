"""Policy C-3 — Dynamic human-principal channel (resolves the C-2 self-lock).

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

OD-010 is never weakened:
    The actor's ``verified`` flag is recomputed by the policy engine from the
    Identity Kernel (``_compute_verified`` / ``_is_verified_human``), which
    now also rejects a ``service``-kind identity. So a spoofed principal
    (non-existent, inactive, or a service identity masquerading as human) is
    rejected and the action falls back to the service deny. The channel only
    *narrows* which actions may be adjudicated as human; it cannot invent an
    allow where the engine would deny.

Zero production behaviour change by default:
    The channel is OFF unless some caller explicitly opens it via
    :func:`human_sovereign` / :func:`set_active_sovereignty`. With no active
    sovereignty, ``_adjudicate`` keeps using the internal service principal and
    every production call site stays at ``enforce=False``, so the kernel layer
    remains record-only exactly as it was in C-1/C-2.
"""

from __future__ import annotations

import contextvars
import time
from dataclasses import dataclass
from typing import FrozenSet, Iterable, Optional


@dataclass(frozen=True)
class ActiveSovereignty:
    """A verified human's delegated authority for a set of kernel actions."""

    principal: str
    actions: FrozenSet[str]
    granted_at: float


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

    Example::

        with human_sovereign(human_id, ["capability.retire", "security.set_abac_rule"]):
            kernel.capability_retire(...)   # adjudicated as the human -> allowed
    """

    def __init__(self, principal: str, actions: Iterable[str]) -> None:
        self._sov = ActiveSovereignty(
            principal=principal,
            actions=frozenset(actions),
            granted_at=time.time(),
        )
        self._token: Optional[contextvars.Token] = None

    def __enter__(self) -> ActiveSovereignty:
        self._token = _active.set(self._sov)
        return self._sov

    def __exit__(self, *exc: object) -> None:
        if self._token is not None:
            _active.reset(self._token)
