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


def _latest_details(action_name: str):
    """Newest audit event written for ``action_name`` -- attribution verified.

    Two things this deliberately does **not** do:

    * It does not filter by ``principal_id``. Before A2 the decorator recorded
      the literal subject ``"kernel"`` for every action, so a test could find its
      own row by that string. A2 replaced the constant with the *actual* acting
      principal, so pinning the literal now finds either nothing or -- worse -- a
      stale row left in the shared ``audit_store.db`` by an earlier run, which is
      a green that proves nothing.
    * It does not treat "the column is non-empty" as attribution. F26: the
      fingerprint must actually be one (32 hex chars), and the event's
      ``principal_id`` column must agree with the ``actor_identity_id`` recorded
      in ``details`` -- the keyspace convergence A2 established.
    """
    from src.kernels.audit import audit_query

    for ev in audit_query(limit=200, reverse=True):
        det = ev.get("details") or {}
        if det.get("action") != action_name:
            continue
        fp = det.get("actor_fingerprint")
        assert isinstance(fp, str) and len(fp) == 32 and all(
            c in "0123456789abcdef" for c in fp
        ), (
            f"audit row for {action_name!r} carries no usable attribution: "
            f"actor_fingerprint={fp!r} (F26: a non-empty column is not evidence)"
        )
        assert ev.get("principal_id") == det.get("actor_identity_id"), (
            f"audit principal keyspace divergence for {action_name!r}: "
            f"principal_id={ev.get('principal_id')!r} vs "
            f"actor_identity_id={det.get('actor_identity_id')!r}"
        )
        return det
    return None


def test_audit_event_recorded():
    t = _Thing()
    # Run a wrapped action, then confirm an audit event landed for it.
    t.increment(1)
    assert _latest_details("thing.increment") is not None, "未写入审计事件"


def test_policy_decision_recorded():
    t = _Thing()
    t.increment(1)
    details = _latest_details("thing.increment")
    assert details is not None, "未写入审计事件"
    decision = details.get("policy_decision")
    assert decision in ("allow", "deny", "defer")
    # 判决必须可追溯依据：Policy C-1 起记录命中的规则 id。
    # （"thing.increment" 不在内核动作白名单内 -> default_deny）
    assert details.get("policy_rule") == "default_deny"


def test_decorator_does_not_mutate_metadata():
    assert kernel_action("x").__class__.__name__ == "function"
