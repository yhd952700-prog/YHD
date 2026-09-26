"""U1 — host-command approval interface tests (human-sovereign escalation)."""

from src.ai.host_command.approval import ApprovalInterface
from src.ai.host_command.models import HostCommandRequest


def _req():
    return HostCommandRequest(command="rm -rf /data", use_shell=True)


def test_request_creates_pending():
    ai = ApprovalInterface()
    ar = ai.request(_req(), "destructive command needs human sign-off")
    assert ar.status == "pending"
    assert ar.approval_id in ai.pending()


def test_grant_then_is_granted():
    ai = ApprovalInterface()
    ar = ai.request(_req(), "reason")
    assert ai.is_granted(ar.approval_id) is False
    assert ai.grant(ar.approval_id) is True
    assert ai.is_granted(ar.approval_id) is True


def test_consume_is_one_shot():
    ai = ApprovalInterface()
    ar = ai.request(_req(), "reason")
    ai.grant(ar.approval_id)
    assert ai.consume(ar.approval_id) is True
    # Second consume fails (already used).
    assert ai.consume(ar.approval_id) is False


def test_deny_rejects():
    ai = ApprovalInterface()
    ar = ai.request(_req(), "reason")
    assert ai.deny(ar.approval_id) is True
    assert ai.is_granted(ar.approval_id) is False


def test_grant_unknown_returns_false():
    ai = ApprovalInterface()
    assert ai.grant("nope") is False
    assert ai.consume("nope") is False
