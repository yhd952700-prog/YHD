"""Policy C-4 — deployment-side kernel enforcement control (**default OFF**).

Rationale
---------
Turning "Policy Controlled" from *recorded* (L1) into *enforced* (L2) for the
43 kernel actions is a **deployment** decision, not a matter of editing 43
decorator call sites. Scattering ``enforce=True`` across the kernel layer would:

* make the rollout un-greppable and un-auditable (43 places to get right);
* make a partial rollout impossible to express;
* remove the single control point an operator needs to turn enforcement off
  during an incident.

So the 43 production sites keep ``enforce=False`` (the AST guard in
``scripts/verify_c2_enforcement.py`` still asserts this, and still should), and
this module provides the one explicit, reversible control point.

Selection syntax (env ``LIUHAO_KERNEL_POLICY_ENFORCE``)
-------------------------------------------------------
===========================  ==========================================
value                        effect
===========================  ==========================================
``""`` (unset)               nothing enforced -- record-only / L1 (default)
``CRITICAL``                 every CRITICAL action
``HIGH,CRITICAL``            every HIGH and CRITICAL action, minus exemptions
``capability.retire``        exactly that action
``CRITICAL,memory.auto_cleanup``  a mix of tiers and explicit action names
===========================  ==========================================

Audited exemptions (C-6)
------------------------
A tier name expands to every action of that tier *except* the ones listed in
:data:`EXEMPT_ACTIONS`. An exemption is not a policy judgement -- it is a
recorded **call-site measurement**: an action that production code actually
invokes on a normal path may not be armed, because arming it would convert a
working flow into a "wait for a human" (an availability regression, not a
security gain). Each exemption carries its evidence and the condition under
which it should be revisited. Naming an exempt action explicitly in the spec
raises (fail-loud) rather than silently arming or silently ignoring it.

Fail-loud, never fail-silent
----------------------------
A token that is neither a tier name nor a known kernel action raises
``ValueError`` at read time; a LOW/MEDIUM action name raises too (the C-3
channel never escalates those, so such an entry would be an inert entry that
*looks* like enforcement). A typo therefore breaks the request loudly instead
of leaving the system quietly unenforced.

Enforcement never weakens OD-010
--------------------------------
This switch only decides *whether* the C-2 gate is armed. The verdict itself
still comes from the policy engine, which re-verifies the human identity from
the Identity Kernel. With enforcement armed and no approval grant, a
HIGH/CRITICAL kernel action raises ``PolicyDeferredError`` ("wait for a human"),
not a silent allow.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Dict, FrozenSet, Optional, Tuple

logger = logging.getLogger("liuhao.kernel.enforcement")

#: Environment variable holding the enforcement selection.
ENV_VAR = "LIUHAO_KERNEL_POLICY_ENFORCE"

#: Actions that a tier expansion must NOT arm, with the measurement that put
#: them here. Keyed by kernel action name; the value is the recorded reason.
#:
#: The bar for an entry: production code (``src/``, excluding tests) reaches the
#: action on a path that runs during normal operation, so arming it would break
#: a working flow. Speculative future call sites do *not* qualify -- those are
#: caught by ``scripts/verify_armed_actions_are_inert.py`` in CI at the moment
#: they are written, which is the intended time to have the conversation.
EXEMPT_ACTIONS: Dict[str, str] = {
    "capability.register": (
        "Live startup call site: get_capability_registry() lazily calls "
        "_register_builtin_capabilities() -> CapabilityRegistry.register() to "
        "install the 12 built-in kernel capabilities. Arming it makes "
        "PolicyDeferredError escape the registry bootstrap, so every consumer of "
        "the capability kernel fails. Measured 2026-09-12 (Round 75) by arming "
        "it alone in a fresh process: probe 'capability.bootstrap' raises. "
        "Revisit if the bootstrap is given an exempt internal path."
    ),
}

_lock = threading.RLock()
_cache: Optional[Tuple[str, FrozenSet[str]]] = None


def parse_spec(spec: str) -> FrozenSet[str]:
    """Resolve a selection string to the concrete set of enforced actions.

    Args:
        spec: comma-separated tier names (``HIGH`` / ``CRITICAL``) and/or kernel
            action names. Empty/whitespace means "nothing enforced".

    Raises:
        ValueError: on an unknown token, an action name that is not
            enforcement-gated (LOW/MEDIUM), or an action name listed in
            :data:`EXEMPT_ACTIONS`.
    """
    from src.kernels._risk_classification import (
        ENFORCED_TIERS,
        KERNEL_ACTION_RISK,
        RiskTier,
    )

    text = (spec or "").strip()
    if not text:
        return frozenset()

    tier_by_name = {t.value.upper(): t for t in RiskTier}
    gated_tiers = {t for t in ENFORCED_TIERS}
    selected: set = set()

    for raw in text.split(","):
        token = raw.strip()
        if not token:
            continue
        upper = token.upper()
        if upper in tier_by_name:
            tier = tier_by_name[upper]
            if tier not in gated_tiers:
                raise ValueError(
                    f"{ENV_VAR}: tier {upper} is not enforcement-gated "
                    f"(only {sorted(t.value for t in gated_tiers)} can be enforced)"
                )
            selected.update(
                a
                for a, r in KERNEL_ACTION_RISK.items()
                if r.tier is tier and a not in EXEMPT_ACTIONS
            )
            continue
        if token in KERNEL_ACTION_RISK:
            if token in EXEMPT_ACTIONS:
                raise ValueError(
                    f"{ENV_VAR}: action {token!r} is exempt from enforcement "
                    f"({EXEMPT_ACTIONS[token]})"
                )
            tier = KERNEL_ACTION_RISK[token].tier
            if tier not in gated_tiers:
                raise ValueError(
                    f"{ENV_VAR}: action {token!r} is {tier.value}, which the C-3 "
                    "channel never escalates -- enforcing it would be inert"
                )
            selected.add(token)
            continue
        raise ValueError(f"{ENV_VAR}: unknown tier or kernel action {token!r}")

    return frozenset(selected)


def reload() -> None:
    """Drop the parsed-spec cache (call after changing the environment)."""
    global _cache
    with _lock:
        _cache = None


def enforced_actions() -> FrozenSet[str]:
    """The set of actions currently selected for enforcement (empty = off)."""
    global _cache
    spec = os.environ.get(ENV_VAR, "")
    with _lock:
        if _cache is not None and _cache[0] == spec:
            return _cache[1]
    resolved = parse_spec(spec)
    with _lock:
        _cache = (spec, resolved)
    return resolved


def is_enforced(action: str, tier: Optional[str] = None) -> bool:
    """True iff ``action`` is currently selected for kernel enforcement.

    ``tier`` is optional and only ever *narrows* the answer: LOW/MEDIUM can
    never be enforced, whatever the configuration says.
    """
    if tier is not None:
        from src.kernels._risk_classification import ENFORCED_TIERS, RiskTier

        try:
            if RiskTier(tier) not in ENFORCED_TIERS:
                return False
        except ValueError:
            return False
    return action in enforced_actions()


def describe() -> Dict[str, object]:
    """Operational snapshot of the enforcement configuration (audit/dashboard)."""
    spec = os.environ.get(ENV_VAR, "")
    try:
        actions = sorted(enforced_actions())
        error: Optional[str] = None
    except ValueError as exc:
        actions = []
        error = str(exc)
    return {
        "env_var": ENV_VAR,
        "spec": spec,
        "enabled": bool(actions),
        "enforced_actions": actions,
        "count": len(actions),
        "exempt_actions": sorted(EXEMPT_ACTIONS),
        "config_error": error,
    }
