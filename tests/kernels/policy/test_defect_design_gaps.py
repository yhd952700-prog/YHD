"""Policy Kernel design-gap defect evidence tests.

Design-level gap ruled as a defect by the project director (2026-09-06,
batch 2). Intentionally RED until the kernel is fixed.

P10: the human_sovereignty builtin rule grants HIGH/CRISK actions based
     solely on the caller-supplied context["actor"]["type"] == "human".
     An unverified (self-declared) human claim must not trigger the
     human sovereignty override.
"""
import pytest

from src.kernels.policy import PolicyEngine, PolicyScope


@pytest.fixture
def engine() -> PolicyEngine:
    return PolicyEngine()


class TestDesignGapP10:
    def test_defect_unverified_human_claim_does_not_trigger_override(self, engine):
        """P10: caller-declared actor.type == "human" must not trigger
        the human sovereignty override.

        The builtin human_sovereignty rule (precedence 1000) allows any
        HIGH/CRITICAL action when the EVALUATION CONTEXT simply says
        actor.type == "human". Any agent can put that string in its
        context and bypass every deny policy. Expected: an unverified
        human declaration does not produce ALLOW.
        """
        decision = engine.evaluate_simple(
            actor={"type": "human"},  # self-declared, no verification
            action={"name": "wipe_production_db", "risk_level": "CRITICAL"},
        )
        assert not decision.is_allowed, (
            "unverified actor.type='human' claim triggered the human "
            "sovereignty override and allowed a CRITICAL action"
        )

    def test_defect_unverified_human_claim_at_high_risk(self, engine):
        """P10 (variant): same gap at HIGH risk level."""
        decision = engine.evaluate_simple(
            actor={"type": "human"},
            action={"name": "deploy_to_prod", "risk_level": "HIGH"},
        )
        assert not decision.is_allowed, (
            "unverified human claim allowed a HIGH-risk action"
        )

    def test_positive_control_agent_high_risk_still_denied(self, engine):
        """Positive control: an agent actor at HIGH risk is denied by
        default (must stay green before AND after the P10 fix).
        """
        decision = engine.evaluate_simple(
            actor={"type": "agent"},
            action={"name": "deploy_to_prod", "risk_level": "HIGH"},
        )
        assert decision.is_denied

    def test_positive_control_human_low_risk_not_affected(self, engine):
        """Positive control: low-risk actions do not match the
        human_sovereignty rule regardless of actor type (condition
        requires risk_level in [HIGH, CRITICAL]).
        """
        decision = engine.evaluate_simple(
            actor={"type": "human"},
            action={"name": "read_doc", "risk_level": "LOW"},
        )
        assert not any(r.id == "human_sovereignty"
                       for r in decision.matched_rules)
