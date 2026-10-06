"""C-2 arming must be observable — a silently-unarmed sovereignty gate is a fail-open.

The gap this closes
-------------------
C-2 (the blocking gate for HIGH/CRITICAL kernel actions) is armed by exactly one
control point: ``LIUHAO_KERNEL_POLICY_ENFORCE`` (``docker-compose.prod.yml`` sets
it to ``CRITICAL`` per D24), while the ~43 production decoration points
deliberately pass ``enforce=False`` (pinned by ``verify_c2_enforcement.py``).
That is the C-4 "single control point" design and it is correct.

The cost: **nothing inside a running process asserts the gate is actually
armed**. If a deployment overrides the variable to empty or drops the compose
line, the sovereignty defence silently degrades from "block" to "record only"
while every other health signal stays green — a silent fail-open.

Design constraints (do not break these to make tests pass)
----------------------------------------------------------
* Being unarmed is a **configuration state**, not a fault. Only a *mismatch*
  between what the deployment declares (production ⇒ D24 says arm CRITICAL) and
  what actually got armed is a degradation.
* Unarmed outside production must stay healthy + carry an informational note,
  behaviourally identical to before.
* An **armed** gate must never be reported as degraded — a false alarm trains
  operators to ignore the real one.

Scope note (ownership)
---------------------
The decision logic lives in :mod:`src.security.c2_arming` (security domain, this
file's owner). The readiness *wiring* lives in ``src/gateway/health.py``, which
belongs to the reliability domain. So these tests exercise the decision function
directly, and :class:`TestWiringContract` separately pins that the wiring
eventually consumes this single source of truth rather than re-deriving it —
that test skips (rather than fails) until the owner lands the wiring.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.security.c2_arming import (
    ALERTING_STATES,
    C2ArmingState,
    c2_arming_verdict_from_env,
    evaluate_c2_arming,
)

ENV_VAR = "LIUHAO_KERNEL_POLICY_ENFORCE"


def _snapshot(**over):
    """A well-formed, fully-armed snapshot (the control case)."""
    base = {
        "env_var": ENV_VAR,
        "spec": "CRITICAL",
        "enabled": True,
        "enforced_actions": ["kernel.critical.one", "kernel.critical.two"],
        "count": 2,
        "exempt_actions": [],
        "config_error": None,
    }
    base.update(over)
    return base


# --------------------------------------------------------------------------- #
# 1. Negative control: production but unarmed -> MUST be visible
# --------------------------------------------------------------------------- #
class TestProductionWithoutArmingIsVisible:
    def test_unarmed_production_degrades(self):
        verdict = evaluate_c2_arming(
            _snapshot(enabled=False, enforced_actions=[], spec=""),
            production=True,
        )
        assert verdict.state is C2ArmingState.UNARMED_PRODUCTION
        assert verdict.enabled is False
        assert verdict.status == "degraded", (
            "production posture with nothing armed is exactly the silent "
            "fail-open this check exists to close"
        )
        assert verdict.alerting

    def test_degradation_names_the_gate_for_operators(self):
        verdict = evaluate_c2_arming(
            _snapshot(enabled=False, enforced_actions=[]), production=True
        )
        line = verdict.to_error_line()
        assert line and "Kernel Policy Enforcement" in line, (
            "a generic 'degraded' tells an operator nothing about which gate failed"
        )
        assert ENV_VAR in line

    def test_empty_override_is_caught_too(self):
        """Explicitly overriding the var to '' is not the same as deleting it."""
        unset = evaluate_c2_arming(
            _snapshot(enabled=False, enforced_actions=[], spec=""), production=True
        )
        empty = evaluate_c2_arming(
            _snapshot(enabled=False, enforced_actions=[], spec=","), production=True
        )
        assert unset.alerting and empty.alerting


# --------------------------------------------------------------------------- #
# 2. Negative control: a malformed spec is the most dangerous shape
# --------------------------------------------------------------------------- #
class TestInvalidSpecIsVisible:
    def test_malformed_spec_degrades_in_any_posture(self):
        """Even outside production: it looks configured while arming nothing."""
        verdict = evaluate_c2_arming(
            _snapshot(enabled=False, enforced_actions=[], config_error="unknown tier"),
            production=False,
        )
        assert verdict.state is C2ArmingState.INVALID_SPEC
        assert verdict.config_error == "unknown tier"
        assert verdict.status == "degraded"
        assert verdict.alerting

    def test_invalid_spec_outranks_the_non_production_exemption(self):
        """Precedence guard: config_error must win over the config-choice escape."""
        verdict = evaluate_c2_arming(
            _snapshot(enabled=False, enforced_actions=[], config_error="boom"),
            production=False,
        )
        assert verdict.state is C2ArmingState.INVALID_SPEC, (
            "a broken spec took the benign non-production path -- precedence is wrong"
        )
        assert verdict.alerting, "a broken spec must not use the benign non-prod path"


# --------------------------------------------------------------------------- #
# 3. Positive control: a correctly armed gate must NOT be reported as a fault
# --------------------------------------------------------------------------- #
class TestArmedGateIsHealthy:
    def test_armed_is_healthy_in_production(self):
        verdict = evaluate_c2_arming(_snapshot(), production=True)
        assert verdict.state is C2ArmingState.ARMED
        assert verdict.enabled is True
        assert verdict.enforced_action_count == 2
        assert verdict.status == "healthy"
        assert not verdict.alerting
        assert verdict.error is None
        assert verdict.to_error_line() is None

    def test_armed_is_healthy_outside_production_too(self):
        verdict = evaluate_c2_arming(_snapshot(), production=False)
        assert verdict.status == "healthy"

    def test_armed_carries_no_error_text(self):
        """An armed gate that still shows an error would be self-contradictory."""
        verdict = evaluate_c2_arming(_snapshot(), production=True)
        assert "error" not in verdict.to_check()
        assert "config_error" not in verdict.to_check()


# --------------------------------------------------------------------------- #
# 4. Regression guard: legitimate non-production unarmed behaviour is UNCHANGED
# --------------------------------------------------------------------------- #
class TestUnarmedNonProductionIsUnchanged:
    def test_stays_healthy_with_an_informational_note(self):
        verdict = evaluate_c2_arming(
            _snapshot(enabled=False, enforced_actions=[], spec=""), production=False
        )
        assert verdict.state is C2ArmingState.UNARMED_NON_PRODUCTION
        assert verdict.status == "healthy", (
            "a deliberate non-production configuration was reported as a fault"
        )
        assert verdict.note, "the exemption must stay explainable to a human"
        assert verdict.to_error_line() is None

    def test_note_reaches_the_operator_check_payload(self):
        verdict = evaluate_c2_arming(
            _snapshot(enabled=False, enforced_actions=[]), production=False
        )
        assert "note" in verdict.to_check()


# --------------------------------------------------------------------------- #
# 5. Totality: a malformed snapshot degrades loudly instead of raising
# --------------------------------------------------------------------------- #
class TestVerdictIsTotal:
    @pytest.mark.parametrize(
        "junk",
        [None, [], "armed", 42, object()],
        ids=["none", "list", "str", "int", "object"],
    )
    def test_non_mapping_snapshot_never_raises(self, junk):
        verdict = evaluate_c2_arming(junk, production=True)
        assert verdict.status == "degraded"
        assert verdict.enabled is False

    def test_bare_string_actions_are_not_iterated_as_characters(self):
        verdict = evaluate_c2_arming(
            {"enforced_actions": "kernel.x", "config_error": None}, production=True
        )
        assert verdict.enforced_actions == ()
        assert verdict.enabled is False


# --------------------------------------------------------------------------- #
# 6. Wiring contract — owned by the reliability domain (src/gateway/health.py)
# --------------------------------------------------------------------------- #
class TestWiringContract:
    """Pin that the readiness wiring CONSUMES this module rather than re-deriving.

    Skips until the owner lands the wiring; fails loudly once it exists and
    drifts (e.g. someone re-implements the verdict inline).
    """

    HEALTH = Path(__file__).resolve().parents[2] / "src" / "gateway" / "health.py"

    def test_readiness_probe_does_not_reimplement_the_verdict(self):
        source = self.HEALTH.read_text(encoding="utf-8")
        if "kernel_policy_enforcement" not in source:
            pytest.skip(
                "readiness wiring for C-2 not landed yet (health.py is owned by "
                "the reliability domain; security provides src/security/c2_arming.py)"
            )
        assert "c2_arming" in source, (
            "health.py surfaces C-2 arming but does not consume the shared "
            "decision module -- two truths will drift"
        )

    def test_alerting_set_matches_the_degrading_states_exactly(self):
        """The alerting set and the state set must not drift apart.

        If a new state were added without being classified here, it would either
        fail open (silently healthy) or fail closed (a new false alarm).
        """
        assert ALERTING_STATES == {
            C2ArmingState.UNARMED_PRODUCTION,
            C2ArmingState.INVALID_SPEC,
        }
        for state in C2ArmingState:
            verdict = evaluate_c2_arming(
                _snapshot(enabled=state is C2ArmingState.ARMED,
                          enforced_actions=["a.b"] if state is C2ArmingState.ARMED else []),
                production=True,
            )
            assert verdict.alerting is (verdict.state in ALERTING_STATES)
            assert verdict.status == ("degraded" if verdict.alerting else "healthy")

    def test_live_collector_reads_the_real_enforcement_snapshot(self, monkeypatch):
        """c2_arming_verdict_from_env must see the real env, not a stub."""
        from src.kernels import _enforcement as enforcement_module

        monkeypatch.setenv(ENV_VAR, "CRITICAL")
        monkeypatch.setenv("LIUHAO_ENV", "production")
        monkeypatch.delenv("ENVIRONMENT", raising=False)
        monkeypatch.delenv("APP_ENV", raising=False)
        enforcement_module.reload()
        try:
            verdict = c2_arming_verdict_from_env()
            assert verdict.state is C2ArmingState.ARMED
            assert verdict.enforced_action_count > 0, "CRITICAL must arm something"
        finally:
            enforcement_module.reload()
