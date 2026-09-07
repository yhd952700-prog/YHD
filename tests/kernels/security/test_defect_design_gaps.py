"""Security Kernel design-gap defect evidence tests.

Design-level gaps ruled as defects by the project director (2026-09-06,
batch 2). These are intentionally RED until the kernel is fixed; each
docstring carries the ruling ID (S3 / S4).

S3: request-supplied attributes must not escalate stored principal
    security attributes.
S4: the scope parameter of check_rbac / check_abac / decide_access
    must participate in the decision (L0-L7 enforcement, invalid scope
    rejected).
"""
import pytest

from src.kernels.security import (
    ABATCondition,
    AccessDecision,
    RBACRole,
    SecurityEngine,
)


@pytest.fixture
def engine() -> SecurityEngine:
    return SecurityEngine()


class TestDesignGapS3:
    def test_defect_request_attributes_cannot_escalate_stored(self, engine):
        """S3: inline attributes must not override stored security
        attributes.

        A principal with a STORED clearance of 0.0 passes a
        clearance > 0.5 rule by supplying attributes={"clearance": 0.99}
        in the request. The caller can fabricate any security attribute
        value. Expected: the stored value wins; the decision is DENY.
        """
        engine.set_abac_rule("vault:open", "clearance",
                             ABATCondition.GREATER_THAN, 0.5)
        engine.set_principal_attributes("p1", {"clearance": 0.0})
        decision = engine.check_abac(
            "p1", "vault:open", attributes={"clearance": 0.99}
        )
        assert decision is AccessDecision.DENY, (
            "request-supplied attribute escalated stored clearance 0.0 to 0.99"
        )

    def test_defect_request_attributes_cannot_escalate_trust(self, engine):
        """S3 (variant): inline attributes must not fabricate trust.

        Stored trust_score 0.1 must not be overridden by a
        request-supplied 0.95.
        """
        engine.set_abac_rule("doc:read", "trust_score",
                             ABATCondition.GREATER_THAN, 0.5)
        engine.set_principal_attributes("p1", {"trust_score": 0.1})
        decision = engine.check_abac(
            "p1", "doc:read", attributes={"trust_score": 0.95}
        )
        assert decision is AccessDecision.DENY, (
            "request-supplied attribute escalated stored trust_score"
        )

    def test_positive_control_stored_low_value_denies(self, engine):
        """Positive control: without inline escalation the stored low
        value denies (this must stay green before AND after the S3 fix).
        """
        engine.set_abac_rule("vault:open", "clearance",
                             ABATCondition.GREATER_THAN, 0.5)
        engine.set_principal_attributes("p1", {"clearance": 0.0})
        assert engine.check_abac("p1", "vault:open") is AccessDecision.DENY


class TestDesignGapS4:
    def test_defect_invalid_scope_rejected_by_check_rbac(self, engine):
        """S4: scope must participate in RBAC decisions.

        The module docstring promises "Scope enforcement L0-L7", but the
        scope parameter is only written into audit text. A request with
        an INVALID scope value ("L9", "global") is currently treated the
        same as a valid one. Expected: invalid scope values are
        rejected with DENY.
        """
        engine.grant_rbac_role("p1", RBACRole.VIEWER)
        for bad_scope in ("L9", "global", ""):
            decision = engine.check_rbac("p1", "context:read", scope=bad_scope)
            assert decision is AccessDecision.DENY, (
                f"invalid scope {bad_scope!r} not rejected by check_rbac"
            )

    def test_defect_invalid_scope_rejected_by_check_abac(self, engine):
        """S4 (variant): invalid scope rejected by check_abac."""
        engine.set_abac_rule("doc:read", "department",
                             ABATCondition.EQUALS, "eng")
        engine.set_principal_attributes("p1", {"department": "eng"})
        decision = engine.check_abac("p1", "doc:read", scope="L9")
        assert decision is AccessDecision.DENY, (
            "invalid scope 'L9' not rejected by check_abac"
        )

    def test_defect_invalid_scope_rejected_by_decide_access(self, engine):
        """S4 (variant): invalid scope rejected by decide_access."""
        engine.grant_rbac_role("p1", RBACRole.VIEWER)
        result = engine.decide_access("p1", "context:read", scope="L9")
        assert result["decision"] != "allow", (
            "invalid scope 'L9' still produced an allow decision"
        )

    def test_positive_control_valid_scopes_still_work(self, engine):
        """Positive control: valid L0-L7 scope values keep working
        (must stay green before AND after the S4 fix).
        """
        engine.grant_rbac_role("p1", RBACRole.VIEWER)
        for scope in ("L0", "L1", "L2", "L3", "L4", "L5", "L6", "L7"):
            decision = engine.check_rbac("p1", "context:read", scope=scope)
            assert decision is AccessDecision.ALLOW, scope
