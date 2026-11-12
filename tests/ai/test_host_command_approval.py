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


# =====================================================================
# What a grant is FOR: 授权时看到的目标 = 执行时的目标
#
# One-shot consumption (above) answers "may this run once". It does not
# answer "may THIS command, in THIS directory, with THESE capabilities, run
# once". The tests below pin the second question, which is the one the
# broker's DEFER branch actually asks via ``granted_id_for``.
#
# Every test issues its OWN approval. Reusing one registry across variants
# gives false readings: ``consume`` is one-shot, so the first variant spends
# the grant and every later variant looks like a refusal. (That mistake was
# made and corrected while measuring this defect.)
# =====================================================================

def _approved(**over):
    """A registry holding one grant for the reference request.

    The reference is "delete the report the agent just produced":
    ``rm report.txt`` in ``/home/alice/workspace`` requiring ``fs.delete``.
    """
    ai = ApprovalInterface()
    req = HostCommandRequest(
        **{
            "command": "rm report.txt",
            "actor": "autonomous",
            "adapter": "filesystem",
            "use_shell": False,
            "cwd": "/home/alice/workspace",
            "capabilities_required": ["fs.delete"],
            **over,
        }
    )
    ar = ai.request(req, "delete the produced report")
    ai.grant(ar.approval_id)
    return ai


def _replay(**over):
    """Re-submit a command against a fresh grant; True if it is let through."""
    ai = _approved()
    req = HostCommandRequest(
        **{
            "command": "rm report.txt",
            "actor": "autonomous",
            "adapter": "filesystem",
            "use_shell": False,
            "cwd": "/home/alice/workspace",
            "capabilities_required": ["fs.delete"],
            **over,
        }
    )
    return ai.granted_id_for(req) is not None


def test_identical_resubmission_is_allowed():
    """The control: the retry this method exists for must still work."""
    assert _replay() is True


def test_grant_does_not_transfer_to_another_directory():
    """Same command, different directory => a different object acted on."""
    assert _replay(cwd="/etc/liuhao") is False
    assert _replay(cwd="/") is False
    assert _replay(cwd=None) is False


def test_grant_does_not_extend_to_more_capability():
    """The sharp end: approving fs.delete must not authorise net.admin.

    Before the fix the fingerprint covered only command/adapter/use_shell/actor,
    so a command demanding MORE capability than the human approved was let
    through. The human authorised deleting one file; the system ran a command
    carrying an administrative capability nobody approved.
    """
    assert _replay(capabilities_required=["fs.delete", "net.admin"]) is False
    assert _replay(capabilities_required=["net.admin"]) is False


def test_narrower_capability_request_is_still_allowed():
    """Asking for LESS than the grant covers is the same approved action.

    This is why the capability check is a subset test and not equality: a
    strictly narrower re-submission of an approved command is legitimate, and
    refusing it would break the retry path for no safety gain.
    """
    assert _replay(capabilities_required=[]) is True
    assert _replay(capabilities_required=["fs.delete"]) is True


def test_grant_does_not_transfer_to_another_actor_or_adapter():
    """A different principal is a different decision."""
    assert _replay(actor="someone-else") is False
    assert _replay(adapter="shell") is False
    assert _replay(use_shell=True) is False
    assert _replay(command="rm -rf /") is False


def test_summary_records_every_behavioural_field():
    """The fingerprint must be auditable, not just enforced.

    An approval a reviewer cannot inspect is the "a log line is not a
    control" failure in its weakest form: the record does not contain the
    thing a human would need to check whether they approved it.
    """
    ai = _approved()
    summary = ai.pending()[next(iter(ai.pending()))].request_summary
    for field in ("command", "adapter", "use_shell", "actor", "cwd",
                  "capabilities_required"):
        assert field in summary, f"{field} missing from the approval fingerprint"
    assert summary["cwd"] == "/home/alice/workspace"
    assert list(summary["capabilities_required"]) == ["fs.delete"]
