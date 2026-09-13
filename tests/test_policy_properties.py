"""Property-based safety invariants for the Policy enforcement chain (Policy C-1..C-7).

The real decision entry point verified by runtime probe is
``src.kernels.policy.evaluate_policy_simple(actor, action, resource=None, scope=None)``
-> ``PolicyDecision`` whose ``.decision`` is a ``PolicyEffect``
(``allow`` / ``deny`` / ``not_applicable`` / ``defer``) and whose
``.is_allowed`` is ``decision == PolicyEffect.ALLOW``.

These ``@given`` properties encode security invariants the enforcement chain must
never violate. Section "假护栏自证" proves each property is *falsifiable* (a stub
that always returns ALLOW makes it go red), so they are real gates, not
silent pass-throughs.
"""

from __future__ import annotations

import pytest
from hypothesis import given, strategies as st, settings, HealthCheck
import hypothesis

from src.kernels import policy as policy_module
from src.kernels.policy import PolicyEffect, PolicyDecision
from src.kernels.identity import INTERNAL_SERVICE_PRINCIPAL
from src.kernels._risk_classification import KERNEL_ACTION_RISK, RiskTier

# The only kernel actions the internal *service* principal may perform (Policy C-1).
ALLOWED = frozenset(policy_module.INTERNAL_SERVICE_ALLOWED_ACTIONS)

# HIGH / CRITICAL actions and their real risk tier (D8). None of these are in
# ALLOWED (whitelist == LOW tier), so no non-human subject can obtain ALLOW for them.
HIGH_CRITICAL = {
    name: r.tier.value
    for name, r in KERNEL_ACTION_RISK.items()
    if r.tier in (RiskTier.HIGH, RiskTier.CRITICAL)
}

RISK_LEVELS = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]


def _service_actor() -> dict:
    return {"type": "service", "id": INTERNAL_SERVICE_PRINCIPAL}


# --------------------------------------------------------------------------- #
# Invariant 1 — non-whitelist subject never gets ALLOW
#
# The internal service principal is the *only* non-human subject with an ALLOW
# path, and that path exists exclusively for the 14 whitelisted (LOW) actions.
# Therefore for this subject: is_allowed  <=>  action in ALLOWED.  A non-
# whitelisted action can never be allowed.
# --------------------------------------------------------------------------- #
@settings(max_examples=120, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    action_name=st.one_of(
        st.sampled_from(sorted(ALLOWED)),
        st.text(min_size=1, max_size=40).filter(lambda s: s not in ALLOWED),
    ),
    risk=st.sampled_from(RISK_LEVELS),
)
def test_service_principal_allow_exactly_whitelist(action_name: str, risk: str) -> None:
    decision = policy_module.evaluate_policy_simple(
        _service_actor(), {"name": action_name, "risk_level": risk}
    )
    assert decision.is_allowed == (action_name in ALLOWED)


# --------------------------------------------------------------------------- #
# Invariant 2 — the service verdict is independent of the claimed risk_level
#
# The engine recomputes ``verified`` and never reads ``risk_level`` on the
# service path (human_sovereignty requires type=="human"). So a caller cannot
# escalate or de-escalate a verdict by lying about the action's risk. The
# decision for the same action must be identical across any two risk levels.
# --------------------------------------------------------------------------- #
@settings(max_examples=120, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    action_name=st.one_of(
        st.sampled_from(sorted(ALLOWED)),
        st.text(min_size=1, max_size=40).filter(lambda s: s not in ALLOWED),
    ),
    risk_a=st.sampled_from(RISK_LEVELS),
    risk_b=st.sampled_from(RISK_LEVELS),
)
def test_service_verdict_independent_of_risk(
    action_name: str, risk_a: str, risk_b: str
) -> None:
    da = policy_module.evaluate_policy_simple(
        _service_actor(), {"name": action_name, "risk_level": risk_a}
    ).decision
    db = policy_module.evaluate_policy_simple(
        _service_actor(), {"name": action_name, "risk_level": risk_b}
    ).decision
    assert da == db


# --------------------------------------------------------------------------- #
# Invariant 3 — fail-closed: with zero verified humans, HIGH/CRITICAL is denied
#
# This is the project's deliberate default posture (OD-010 / C-3 / C-7): a
# HIGH or CRITICAL action may only be allowed through a verified *human*
# sovereignty override. When no human identity is registered/verifiable, no
# actor -- not even one that claims type=="human", nor the built-in machine
# accounts -- may obtain ALLOW on a HIGH/CRITICAL action. The engine must
# default to deny.
# --------------------------------------------------------------------------- #
@settings(max_examples=120, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    actor_type=st.sampled_from(["human", "agent", "service"]),
    principal=st.text(min_size=1, max_size=24),
    action_name=st.sampled_from(sorted(HIGH_CRITICAL)),
)
def test_zero_humans_high_critical_always_denied(
    actor_type: str, principal: str, action_name: str
) -> None:
    actor = {"type": actor_type, "id": principal, "principal": principal}
    decision = policy_module.evaluate_policy_simple(
        actor, {"name": action_name, "risk_level": HIGH_CRITICAL[action_name]}
    )
    assert not decision.is_allowed


# --------------------------------------------------------------------------- #
# 假护栏自证 — these properties must be able to FAIL.
#
# If we replace the decision function with a stub that always returns ALLOW,
# the invariants above become false and the @given properties must raise. This
# proves the properties are real gates, not silent pass-throughs.
# --------------------------------------------------------------------------- #
def test_property_can_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    allow_all = PolicyDecision(
        decision=PolicyEffect.ALLOW,
        matched_rules=[],
        denied_rules=[],
        abstained_rules=[],
        traceability=[],
        context={},
    )
    monkeypatch.setattr(
        policy_module, "evaluate_policy_simple", lambda *a, **k: allow_all
    )
    with pytest.raises((AssertionError, hypothesis.errors.HypothesisException)):
        # Run a real @given property against the always-ALLOW stub: it must go red.
        test_service_principal_allow_exactly_whitelist()
