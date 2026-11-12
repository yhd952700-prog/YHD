"""Lifecycle gate regression: PAUSED / STOPPED must actually refuse work.

Why this file exists
--------------------
The 14-kernel lifecycle protocol was fully plumbed -- ``_base.py`` defines the
state machine, ``_registry.py`` drives it, the gateway lifespan calls
``initialize_all()``, and ``POST /v1/kernels/{name}/pause`` lets a sovereign
flip any kernel to ``PAUSED``. But every kernel's ``pause()`` only assigned a
field, and **no business method ever read it**. Measured before the fix: a
``PAUSED`` ``EventBus`` accepted ``publish()`` and delivered to subscribers; a
``STOPPED`` one did the same. The indicator light lied on a live, human-gated
control path.

``NetworkBus.route()`` was the sole kernel that genuinely gated (fixed in
``690273e0``). These tests pin that same semantics for event / security /
policy and pin the properties that make a gate *correct* rather than merely
present:

* **Refusal direction.** For security and policy the refusal is a returned
  DENY, never a raised exception. Raising would let a broad ``except`` in a
  caller turn "denied" into an unhandled path whose effective direction is
  fail-**open** -- the worst possible failure direction for these two kernels.
* **READY baseline is a real ALLOW.** Otherwise "PAUSED denies" proves nothing,
  since it might have denied anyway.
* **resume() restores service.** A gate must not be a one-way guillotine.
* **Counter-proof.** ``test_gate_absence_is_detectable`` shows the assertions
  actually depend on the gate rather than passing vacuously.

Mirrors ``scripts/verify_kernel_lifecycle_gate.py``; that script is the CI gate,
this file is the regression net.
"""
from __future__ import annotations

import pytest

from src.kernels._base import (
    KernelLifecycle,
    KernelNotServableError,
    KernelStateError,
    assert_servable,
    is_servable,
)
from src.kernels.event import Event, EventBus
from src.kernels.policy import PolicyEffect, PolicyEngine
from src.kernels.security import AccessDecision, RBACRole, RBACRule, SecurityEngine


# --------------------------------------------------------------------- base
class TestIsServableContract:
    """The gate's state matrix, including the deliberate UNINITIALIZED hole."""

    @pytest.mark.parametrize(
        "state,expected",
        [
            (KernelLifecycle.READY, True),
            (KernelLifecycle.UNINITIALIZED, True),
            (KernelLifecycle.INITIALIZING, True),
            (KernelLifecycle.PAUSED, False),
            (KernelLifecycle.STOPPED, False),
        ],
    )
    def test_matrix(self, state, expected):
        kernel = type("K", (), {"lifecycle": state})()
        assert is_servable(kernel) is expected

    def test_uninitialized_is_deliberately_servable(self):
        """UNINITIALIZED must NOT close the gate.

        The repo constructs kernels directly and never calls ``initialize()``
        in >100 test sites plus production paths (``src/ai/network_gateway.py``
        builds its own ``NetworkBus``). Closing on UNINITIALIZED would fail
        kernels that were never paused -- a production outage dressed up as a
        tightening. UNINITIALIZED honesty is the observability surface's job
        (``/v1/kernels`` -> ``uninitialized_but_present``).
        """
        assert is_servable(type("K", (), {"lifecycle": KernelLifecycle.UNINITIALIZED})())

    def test_assert_servable_raises_with_operation_name(self):
        kernel = type("K", (), {"lifecycle": KernelLifecycle.PAUSED})()
        with pytest.raises(KernelNotServableError) as exc:
            assert_servable(kernel, "publish")
        assert "publish" in str(exc.value)

    def test_assert_servable_is_silent_when_servable(self):
        kernel = type("K", (), {"lifecycle": KernelLifecycle.READY})()
        assert_servable(kernel, "publish")  # must not raise

    def test_not_servable_is_a_kernel_state_error(self):
        """Stays in the existing family so the gateway's 409 mapping holds."""
        assert issubclass(KernelNotServableError, KernelStateError)


# --------------------------------------------------------------------- event
class TestEventBusGates:
    def test_ready_publish_delivers(self):
        bus = EventBus()
        bus.initialize()
        seen = []
        bus.subscribe("t", seen.append)
        bus.publish(Event(type="t", source="probe"))
        assert len(seen) == 1, "READY baseline must deliver, else the rest proves nothing"

    def test_paused_publish_is_refused(self):
        bus = EventBus()
        bus.initialize()
        bus.pause()
        assert bus.lifecycle is KernelLifecycle.PAUSED
        with pytest.raises(KernelNotServableError):
            bus.publish(Event(type="t", source="probe"))

    def test_paused_does_not_deliver_to_subscribers(self):
        """The real defect: the flag flipped, delivery carried on."""
        bus = EventBus()
        bus.initialize()
        seen = []
        bus.subscribe("t", seen.append)
        bus.publish(Event(type="t", source="probe"))
        assert len(seen) == 1

        bus.pause()
        with pytest.raises(KernelNotServableError):
            bus.publish(Event(type="t", source="probe"))
        assert len(seen) == 1, "PAUSED bus delivered to a subscriber"

    def test_stopped_does_not_deliver_to_subscribers(self):
        bus = EventBus()
        bus.initialize()
        seen = []
        bus.subscribe("t", seen.append)
        bus.publish(Event(type="t", source="probe"))
        bus.shutdown()
        with pytest.raises(KernelNotServableError):
            bus.publish(Event(type="t", source="probe"))
        assert len(seen) == 1, "STOPPED bus delivered to a subscriber"

    def test_refusal_is_recorded_as_dead_letter(self):
        """A refusal must be visible, not silently dropped."""
        bus = EventBus()
        bus.initialize()
        bus.pause()
        with pytest.raises(KernelNotServableError):
            bus.publish(Event(type="t", source="probe"))
        assert len(bus._dead_letters) == 1
        assert bus.stats()["dead_letters_total"] == 1

    def test_resume_restores_delivery(self):
        bus = EventBus()
        bus.initialize()
        seen = []
        bus.subscribe("t", seen.append)
        bus.publish(Event(type="t", source="probe"))
        bus.pause()
        with pytest.raises(KernelNotServableError):
            bus.publish(Event(type="t", source="probe"))
        bus.resume()
        bus.publish(Event(type="t", source="probe"))
        assert len(seen) == 2, "resume() did not restore delivery"


# ------------------------------------------------------------------ security
def _security_with_registered_rule() -> SecurityEngine:
    """A security engine where ``probe:read`` genuinely ALLOWs.

    No colon permissions are seeded by default (deliberate fail-closed, see
    ``_seed_default_rules``), so without registering one the READY baseline is
    DENY and "PAUSED denies" would be vacuous.
    """
    sec = SecurityEngine()
    sec.initialize()
    sec._rbac_rules["probe:read"] = RBACRule(
        id="probe-rule", role=RBACRole.ADMIN, permission="probe:read"
    )
    sec.grant_rbac_role("alice", RBACRole.ADMIN, reason="probe")
    return sec


class TestSecurityGates:
    def test_ready_allows_registered_permission(self):
        sec = _security_with_registered_rule()
        assert sec.check_rbac("alice", "probe:read", "L1") == AccessDecision.ALLOW

    def test_paused_returns_deny(self):
        sec = _security_with_registered_rule()
        sec.pause()
        assert sec.check_rbac("alice", "probe:read", "L1") == AccessDecision.DENY

    def test_paused_returns_a_value_and_never_raises(self):
        """Refusal must be a VALUE here.

        A raised exception caught by a broad ``except`` upstream would leave
        the caller with no decision, and the effective direction of "no
        decision" in an authorization path is fail-OPEN.
        """
        sec = _security_with_registered_rule()
        sec.pause()
        decision = sec.check_rbac("alice", "probe:read", "L1")
        assert isinstance(decision, AccessDecision)
        assert decision is AccessDecision.DENY

    def test_paused_check_access_denies(self):
        sec = _security_with_registered_rule()
        sec.pause()
        assert sec.check_access("alice", "probe:read", "L1") == AccessDecision.DENY

    def test_paused_decide_access_denies(self):
        sec = _security_with_registered_rule()
        sec.pause()
        assert sec.decide_access("alice", "probe:read", "L1")["decision"] == "deny"

    def test_paused_refusal_is_audited(self):
        sec = _security_with_registered_rule()
        sec.pause()
        sec.check_rbac("alice", "probe:read", "L1")
        assert sec.audit_trail(), "lifecycle refusal left no audit trace"

    def test_resume_restores_allow(self):
        sec = _security_with_registered_rule()
        sec.pause()
        assert sec.check_rbac("alice", "probe:read", "L1") == AccessDecision.DENY
        sec.resume()
        assert sec.check_rbac("alice", "probe:read", "L1") == AccessDecision.ALLOW


# -------------------------------------------------------------------- policy
class TestPolicyGates:
    def test_paused_returns_deny(self):
        engine = PolicyEngine()
        engine.initialize()
        engine.pause()
        decision = engine.evaluate({"actor": {"type": "human", "id": "p"}})
        assert decision.decision is PolicyEffect.DENY

    def test_paused_denial_is_traceable(self):
        """A refusal must say WHY -- otherwise it reads as a policy denial."""
        engine = PolicyEngine()
        engine.initialize()
        engine.pause()
        decision = engine.evaluate({"actor": {"type": "human", "id": "p"}})
        assert any("lifecycle-gate" in t for t in decision.traceability)

    def test_paused_denial_is_not_silently_allowed(self):
        engine = PolicyEngine()
        engine.initialize()
        engine.pause()
        decision = engine.evaluate({"actor": {"type": "human", "id": "p"}})
        assert decision.is_denied
        assert not decision.is_allowed

    def test_resume_restores_evaluation(self):
        engine = PolicyEngine()
        engine.initialize()
        engine.pause()
        assert engine.evaluate({}).decision is PolicyEffect.DENY
        engine.resume()
        assert engine.evaluate({}).decision is not PolicyEffect.DENY


# ------------------------------------------------------------- counter-proof
def test_gate_absence_is_detectable(monkeypatch):
    """Neutralise the gate; the refusal assertions MUST start failing.

    Without this, every test above could pass for reasons unrelated to the
    gate. This is the anti-rubber-stamp guarantee.
    """
    import src.kernels.event as ev_mod
    import src.kernels.security as sec_mod

    monkeypatch.setattr(ev_mod, "is_servable", lambda kernel: True)
    monkeypatch.setattr(sec_mod, "is_servable", lambda kernel: True)

    bus = EventBus()
    bus.initialize()
    seen = []
    bus.subscribe("t", seen.append)
    bus.pause()
    bus.publish(Event(type="t", source="probe"))
    assert len(seen) == 1, (
        "with the gate removed the PAUSED bus delivered again -- the delivery "
        "assertions above would have been vacuous"
    )

    sec = _security_with_registered_rule()
    sec.pause()
    assert sec.check_rbac("alice", "probe:read", "L1") == AccessDecision.ALLOW, (
        "with the gate removed the PAUSED engine still allows -- the DENY "
        "assertions above would have been vacuous"
    )
