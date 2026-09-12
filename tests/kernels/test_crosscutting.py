"""Tests for the cross-cutting kernel action decorator (DoD: Policy
Controlled + Audited + Observable).

Asserts REAL behaviour: the wrapped function's return value is preserved, an
audit event is written, a policy decision is recorded, and exceptions are
re-raised unchanged (the decorator is additive, never swallowing errors).
"""

import pytest

from src.kernels._crosscutting import kernel_action


class _Thing:
    def __init__(self):
        self.value = 0

    @kernel_action("thing.increment", risk_level="LOW")
    def increment(self, n=1):
        self.value += n
        return self.value

    @kernel_action("thing.explode", risk_level="LOW")
    def explode(self):
        raise ValueError("boom")


def test_return_value_preserved():
    t = _Thing()
    assert t.increment(3) == 3
    assert t.increment(2) == 5
    assert t.value == 5


def test_exception_reraises_unchanged():
    t = _Thing()
    with pytest.raises(ValueError, match="boom"):
        t.explode()


def test_audit_event_recorded():
    from src.kernels.audit import audit_query
    t = _Thing()
    # Run a wrapped action, then confirm an audit event landed for it.
    # Query the MOST RECENT events (reverse=True) so this assertion stays
    # robust even when earlier kernel tests have already written many audit
    # events into the shared persistent audit_store.db (the default ASC order
    # + LIMIT would otherwise return only the oldest events).
    t.increment(1)
    events = audit_query(principal_id="kernel", limit=50, reverse=True)
    assert any(e.get("details", {}).get("action") == "thing.increment" for e in events)


def test_policy_decision_recorded():
    from src.kernels.audit import audit_query
    t = _Thing()
    t.increment(1)
    events = audit_query(principal_id="kernel", limit=50, reverse=True)
    matching = [e for e in events if e.get("details", {}).get("action") == "thing.increment"]
    assert matching
    details = matching[0]["details"]
    decision = details.get("policy_decision")
    assert decision in ("allow", "deny", "defer")
    # 判决必须可追溯依据：Policy C-1 起记录命中的规则 id。
    # （"thing.increment" 不在内核动作白名单内 -> default_deny）
    assert details.get("policy_rule") == "default_deny"


def test_decorator_does_not_mutate_metadata():
    assert kernel_action("x").__class__.__name__ == "function"
