"""Policy guardian integrity tests (root-cause fix for the silent/mutable guardian).

These tests pin the behaviour the audit flagged as missing in
``src/kernels/policy/__init__.py``:

* An authorization decision (ALLOW/DENY) must be durable in the authoritative
  audit chain -- not observable only in memory.
* The fail-closed foundation (``default_deny``, ``human_sovereignty``) must be
  unremovable, and a removal attempt must be recorded as a *blocked* mutation.
* Every non-protected rule removal must be recorded, so a rule can never vanish
  without a trace.

Every test points the global audit store at a per-test temp file. It NEVER
touches the repo-root ``audit_store.db`` (its ``-wal``/``-shm`` sidecars).

Why this file exists (anti-rubber-stamp)
----------------------------------------
These assertions are chosen so the file FAILS against the unfixed code:

* Before the fix, ``unregister_rule("default_deny")`` deleted the rule and
  returned ``True`` (silent fail-open). Here it must return ``False`` and the
  rule must still DENY.
* Before the fix, ``evaluate`` returned a ``PolicyDecision`` that was never
  written to any audit chain. Here the DENY must be queryable from the store.

Run with the safe-delete shim disabled and a fresh basetemp, e.g.::

    CODEBUDDY_SAFE_DELETE_ENABLED=0 <venv>/python.exe -m pytest \\
        tests/kernels/policy/test_policy_guardian_integrity.py \\
        --basetemp=/tmp/pg-int
"""
from __future__ import annotations

import pytest

from src.kernels.audit import audit_query
from src.kernels.policy import (
    PolicyAction,
    PolicyCondition,
    PolicyEngine,
    PolicyOperator,
    PolicyRule,
)


@pytest.fixture
def audit_db(tmp_path, monkeypatch):
    """Point the global audit store at a per-test temp file (never repo-root)."""
    import src.kernels.audit as audit_module

    db = tmp_path / "policy_guardian_audit.db"
    # Hard guard: this must be an isolated file, never the shared store.
    assert "audit_store.db" not in str(db), "must not use the repo-root store"
    monkeypatch.setenv("AUDIT_DB_PATH", str(db))
    audit_module._audit_store = None
    yield db
    # Force any later test to rebuild against its own (restored) env path.
    audit_module._audit_store = None


def _newest_event_for(action_name: str):
    """Return the newest audit event whose details.action == ``action_name``."""
    for ev in audit_query(limit=5000, reverse=True):
        det = ev.get("details") or {}
        if det.get("action") == action_name:
            return ev
    return None


def _count_events_for(action_name: str) -> int:
    return sum(
        1 for ev in audit_query(limit=5000)
        if (ev.get("details") or {}).get("action") == action_name
    )


# --------------------------------------------------------------------------- #
# 1. Protected rules cannot be removed, and still govern afterwards
# --------------------------------------------------------------------------- #


class TestProtectedRulesAreImmutable:
    def test_default_deny_cannot_be_unregistered(self, audit_db):
        engine = PolicyEngine()
        assert engine.unregister_rule("default_deny") is False

    def test_human_sovereignty_cannot_be_unregistered(self, audit_db):
        engine = PolicyEngine()
        assert engine.unregister_rule("human_sovereignty") is False

    def test_protected_rule_still_governs_after_refusal(self, audit_db):
        engine = PolicyEngine()
        assert engine.unregister_rule("default_deny") is False
        # The rule is still installed and still fails the action closed.
        assert engine.get_rule("default_deny") is not None
        decision = engine.evaluate({"action": {"name": "nothing.allowed"}})
        assert decision.is_denied, "default_deny must still deny an unmatched action"
        assert "default_deny" in [r.id for r in decision.denied_rules]

    def test_refusal_is_recorded_as_blocked_mutation(self, audit_db):
        engine = PolicyEngine()
        engine.unregister_rule("default_deny")
        ev = _newest_event_for("policy.unregister_rule")
        assert ev is not None, "rule-mutation must be durable in the audit chain"
        det = ev["details"]
        assert det["policy_rule"] == "default_deny"
        assert det["rule_removed"] is False
        assert det["rule_mutation_reason"] == "protected_rule"
        assert ev["outcome"] == "blocked"


# --------------------------------------------------------------------------- #
# 2. A DENY decision is durable in the audit store
# --------------------------------------------------------------------------- #


class TestDecisionIsAudited:
    def test_deny_decision_written_to_audit_store(self, audit_db):
        engine = PolicyEngine()
        decision = engine.evaluate({"action": {"name": "completely.unregistered"}})
        assert decision.is_denied

        ev = _newest_event_for("policy.evaluate")
        assert ev is not None, "DENY decision must land in the audit chain"
        det = ev["details"]
        assert det["policy_decision"] == "deny"
        assert det["policy_rule"] == "default_deny"
        # The decisive rule must be present in the ordered traceability
        # (format is "RULE:<rule_id>:<action>"), proving "why" is recorded.
        assert any("default_deny" in t for t in det["policy_traceability"])
        assert det["denial_reason"] == "policy_deny:default_deny"
        assert ev["outcome"] == "denied"

        # Durable: a second independent query still finds it.
        again = _newest_event_for("policy.evaluate")
        assert again is not None
        assert again["event_id"] == ev["event_id"]

    def test_allow_decision_is_also_recorded(self, audit_db):
        engine = PolicyEngine()
        # Register an explicit allow so the decision is ALLOW (not NOT_APPLICABLE).
        rule = PolicyRule(
            id="allow_probe",
            name="allow probe",
            description="d",
            conditions=[PolicyCondition("action.name", PolicyOperator.EQ, "probe.allow")],
            action=PolicyAction.ALLOW,
            precedence=500,
        )
        assert engine.register_rule(rule) is True
        decision = engine.evaluate({"action": {"name": "probe.allow"}})
        assert decision.is_allowed

        ev = _newest_event_for("policy.evaluate")
        assert ev is not None
        det = ev["details"]
        assert det["policy_decision"] == "allow"
        assert det["policy_rule"] == "allow_probe"


# --------------------------------------------------------------------------- #
# 3. Non-protected removal still works and is audited
# --------------------------------------------------------------------------- #


class TestUnprotectedRemovalAudited:
    def test_unregister_non_protected_works(self, audit_db):
        engine = PolicyEngine()
        rule = PolicyRule(
            id="temp_rule",
            name="temp",
            description="d",
            conditions=[PolicyCondition("action.name", PolicyOperator.EQ, "temp.do")],
            action=PolicyAction.ALLOW,
        )
        assert engine.register_rule(rule) is True
        assert engine.get_rule("temp_rule") is not None

        assert engine.unregister_rule("temp_rule") is True
        assert engine.get_rule("temp_rule") is None

    def test_unregister_non_protected_is_recorded(self, audit_db):
        engine = PolicyEngine()
        rule = PolicyRule(
            id="temp_rule2",
            name="temp2",
            description="d",
            conditions=[PolicyCondition("action.name", PolicyOperator.EQ, "temp2.do")],
            action=PolicyAction.ALLOW,
        )
        engine.register_rule(rule)
        engine.unregister_rule("temp_rule2")

        ev = _newest_event_for("policy.unregister_rule")
        assert ev is not None
        det = ev["details"]
        assert det["policy_rule"] == "temp_rule2"
        assert det["rule_removed"] is True
        assert ev["outcome"] == "success"

    def test_missing_rule_removal_is_noop_not_recorded(self, audit_db):
        engine = PolicyEngine()
        before = _count_events_for("policy.unregister_rule")
        # Nothing to remove: no state change, no attack signal -> no row.
        assert engine.unregister_rule("does.not.exist") is False
        after = _count_events_for("policy.unregister_rule")
        assert after == before


# --------------------------------------------------------------------------- #
# 4. No write amplification / recursion from the audit write
# --------------------------------------------------------------------------- #


class TestNoAmplification:
    def test_single_evaluate_writes_exactly_one_decision_record(self, audit_db):
        engine = PolicyEngine()
        before = _count_events_for("policy.evaluate")
        engine.evaluate({"action": {"name": "amplification.check"}})
        after = _count_events_for("policy.evaluate")
        assert after - before == 1, (
            f"exactly one policy.evaluate record expected, got {after - before}"
        )


# --------------------------------------------------------------------------- #
# 5. The enabled=False bypass is closed for protected rules
# --------------------------------------------------------------------------- #


class TestDisabledFlagCannotNeutraliseFoundation:
    def test_protected_rule_applies_even_when_enabled_false(self, audit_db):
        engine = PolicyEngine()
        rule = engine.get_rule("default_deny")
        rule.enabled = False
        try:
            decision = engine.evaluate({"action": {"name": "should.be.denied"}})
            assert decision.is_denied, (
                "default_deny must still apply even when its enabled flag is False"
            )
            assert "default_deny" in [r.id for r in decision.denied_rules]
        finally:
            rule.enabled = True


# --------------------------------------------------------------------------- #
# 6. Privacy: request payloads (secrets) are never written to the audit store
# --------------------------------------------------------------------------- #


class TestPayloadNotLeaked:
    def test_secrets_absent_from_audit_details(self, audit_db):
        engine = PolicyEngine()
        ctx = {
            "action": {
                "name": "secret.action",
                "api_key": "SUPER_SECRET_VALUE_12345",
            },
            "actor": {"type": "human"},
            "resource": {"token": "ALSO_SECRET_VALUE_67890"},
        }
        engine.evaluate(ctx)

        ev = _newest_event_for("policy.evaluate")
        assert ev is not None
        blob = __import__("json").dumps(ev["details"], default=str)
        assert "SUPER_SECRET_VALUE_12345" not in blob
        assert "ALSO_SECRET_VALUE_67890" not in blob
        # Only the whitelisted action shape is recorded, not the whole payload.
        assert ev["details"]["judged_action"] == {"name": "secret.action"}
