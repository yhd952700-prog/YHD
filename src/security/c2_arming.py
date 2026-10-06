"""C-2 arming self-check: decide whether the kernel-policy gate is REALLY armed.

The gap this closes
-------------------
C-2 (the blocking gate for HIGH/CRITICAL kernel actions) is armed by exactly
one control point: the ``LIUHAO_KERNEL_POLICY_ENFORCE`` environment variable
(``docker-compose.prod.yml`` sets it to ``CRITICAL`` per D24). Every one of the
~43 production decoration points deliberately passes ``enforce=False`` -- that
is the C-4 "single control point" design, and it is correct.

The cost of that design is that **nothing inside a running process asserts the
gate is actually armed**. If a deployment overrides the variable to empty, or
deletes the compose line, the sovereignty defence silently degrades from
"block" to "record only" while every other health signal stays green -- a
silent fail-open.

Why this module is pure
-----------------------
The verdict is computed from an *already-collected snapshot* rather than by
touching the environment itself. That keeps it:

* **pure** -- same snapshot in, same verdict out; no I/O, no globals, no clock;
* **total**  -- an unknown snapshot degrades to the safe (visible) answer rather
  than raising inside a health probe;
* **cheap to test** -- the security decision can be exhaustively truth-tabled
  without standing up the gateway.

:func:`c2_arming_verdict_from_env` is the thin collector that feeds the live
``_enforcement.describe()`` snapshot in. Keeping collection and decision apart
is what lets the readiness wiring (owned by another domain) stay a handful of
lines that merely *report* this verdict.

Honest scope
------------
This is **observability**, not enforcement: it does not arm anything and
cannot make an unarmed gate armed. It only guarantees that "unarmed" is
*visible* instead of silent. An unarmed production gate must never read
healthy here -- that is the entire point.

State precedence (first match wins)
-----------------------------------
1. :attr:`C2ArmingState.INVALID_SPEC` -- the spec is malformed, so the operator
   *intended* to arm something and the parser produced zero armed actions. This
   outranks everything: it is the most dangerous shape, because the deployment
   looks configured while enforcing nothing. Degrades in ANY posture.
2. :attr:`C2ArmingState.UNARMED_PRODUCTION` -- production posture (which per D24
   should arm CRITICAL) resolved to nothing armed. Degrades.
3. :attr:`C2ArmingState.ARMED` -- at least one action is enforced. Healthy.
4. :attr:`C2ArmingState.UNARMED_NON_PRODUCTION` -- unarmed outside production is
   a deliberate configuration choice (dev/CI/test), not a fault. Healthy, with
   an informational note. This guard exists so the check cannot train operators
   to ignore it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Mapping, Optional, Tuple

__all__ = [
    "C2ArmingState",
    "C2ArmingVerdict",
    "evaluate_c2_arming",
    "c2_arming_verdict_from_env",
]


class C2ArmingState(str, Enum):
    """Outcome of the arming self-check."""

    #: At least one action is genuinely enforced by the runtime gate.
    ARMED = "armed"
    #: Production posture, but nothing is armed -> silent fail-open.
    UNARMED_PRODUCTION = "unarmed_production"
    #: The spec exists but does not parse -> zero armed actions, looks configured.
    INVALID_SPEC = "invalid_spec"
    #: Unarmed outside production -> deliberate configuration choice.
    UNARMED_NON_PRODUCTION = "unarmed_non_production"


#: States that must make the caller degrade. Anything not in here is a
#: configuration choice and must NOT be reported as a fault.
ALERTING_STATES = frozenset(
    {C2ArmingState.UNARMED_PRODUCTION, C2ArmingState.INVALID_SPEC}
)


@dataclass(frozen=True)
class C2ArmingVerdict:
    """A total, JSON-friendly arming verdict."""

    state: C2ArmingState
    enabled: bool
    enforced_actions: Tuple[str, ...]
    config_error: Optional[str]
    error: Optional[str]
    note: Optional[str]

    @property
    def alerting(self) -> bool:
        """True when the caller must surface this as degraded."""
        return self.state in ALERTING_STATES

    @property
    def status(self) -> str:
        """"degraded" when alerting, else "healthy"."""
        return "degraded" if self.alerting else "healthy"

    @property
    def enforced_action_count(self) -> int:
        return len(self.enforced_actions)

    def to_check(self) -> Dict[str, Any]:
        """Projection consumed by a readiness probe's ``checks`` mapping.

        Key names are part of the contract with the operator surface, so they are
        asserted in tests rather than left implicit.
        """
        check: Dict[str, Any] = {
            "status": self.status,
            "state": self.state.value,
            "enabled": self.enabled,
            "enforced_action_count": self.enforced_action_count,
            "enforced_actions": list(self.enforced_actions),
        }
        if self.config_error:
            check["config_error"] = self.config_error
        if self.error:
            check["error"] = self.error
        if self.note:
            check["note"] = self.note
        return check

    def to_error_line(self) -> Optional[str]:
        """One operator-facing line for a readiness ``errors`` list."""
        return f"Kernel Policy Enforcement: {self.error}" if self.error else None


def _actions_of(snapshot: Mapping[str, Any]) -> Tuple[str, ...]:
    raw = snapshot.get("enforced_actions") or ()
    if isinstance(raw, str):  # a bare string is a malformed snapshot, not an action
        return ()
    try:
        return tuple(str(a) for a in raw)
    except TypeError:  # not iterable at all
        return ()


def evaluate_c2_arming(
    snapshot: Mapping[str, Any], *, production: bool
) -> C2ArmingVerdict:
    """Pure: turn an enforcement snapshot into an arming verdict.

    :param snapshot: the mapping returned by ``_enforcement.describe()``.
    :param production: whether this deployment is production (``posture.is_production()``).
    :returns: a :class:`C2ArmingVerdict`. Never raises for a malformed snapshot.
    """
    if not isinstance(snapshot, Mapping):  # total: degrade loudly, do not explode
        return C2ArmingVerdict(
            state=C2ArmingState.INVALID_SPEC,
            enabled=False,
            enforced_actions=(),
            config_error=None,
            error=(
                "enforcement snapshot is not a mapping "
                f"(got {type(snapshot).__name__}) -- cannot confirm the gate is armed"
            ),
            note=None,
        )

    actions = _actions_of(snapshot)
    enabled = bool(actions)
    raw_error = snapshot.get("config_error")
    config_error = str(raw_error) if raw_error else None

    # 1. A malformed spec is the most dangerous shape: it looks configured while
    #    arming nothing. It outranks posture -- it degrades everywhere.
    if config_error:
        return C2ArmingVerdict(
            state=C2ArmingState.INVALID_SPEC,
            enabled=False,
            enforced_actions=(),
            config_error=config_error,
            error=(
                f"{snapshot.get('env_var', 'LIUHAO_KERNEL_POLICY_ENFORCE')} is invalid "
                f"({config_error}) -- it resolved to ZERO armed actions, so HIGH/CRITICAL "
                "kernel actions are RECORD-ONLY, not blocked"
            ),
            note=None,
        )

    # 2. Production expects CRITICAL armed (D24); anything less is fail-open.
    if production and not enabled:
        return C2ArmingVerdict(
            state=C2ArmingState.UNARMED_PRODUCTION,
            enabled=False,
            enforced_actions=(),
            config_error=None,
            error=(
                f"{snapshot.get('env_var', 'LIUHAO_KERNEL_POLICY_ENFORCE')} resolved to "
                "nothing enforced: HIGH/CRITICAL kernel actions are RECORD-ONLY, not "
                "blocked. This deployment declares production, which is expected to arm "
                "at least CRITICAL (D24)"
            ),
            note=None,
        )

    # 3. Genuinely armed.
    if enabled:
        return C2ArmingVerdict(
            state=C2ArmingState.ARMED,
            enabled=True,
            enforced_actions=actions,
            config_error=None,
            error=None,
            note=None,
        )

    # 4. Unarmed outside production: a configuration choice, not a fault. It must
    #    stay healthy or the check becomes noise operators learn to ignore.
    return C2ArmingVerdict(
        state=C2ArmingState.UNARMED_NON_PRODUCTION,
        enabled=False,
        enforced_actions=(),
        config_error=None,
        error=None,
        note=(
            f"{snapshot.get('env_var', 'LIUHAO_KERNEL_POLICY_ENFORCE')} is unset/empty and "
            "this deployment does not declare production: kernel actions are record-only "
            "by configuration choice. Set it (e.g. CRITICAL) to arm the blocking gate"
        ),
    )


def c2_arming_verdict_from_env() -> C2ArmingVerdict:
    """Collect the live snapshot and evaluate it. Convenience for wiring."""
    from ..kernels._enforcement import describe  # local import: avoids an import cycle
    from .posture import is_production  # local import: keeps this module pure

    return evaluate_c2_arming(describe(), production=is_production())
