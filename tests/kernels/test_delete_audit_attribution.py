"""Audit-subject attribution for deletion / privilege-change kernel actions.

Regression contract:
* a request-side human is recorded as the initiator of a delete/privilege
  change (``initiated_by`` + ``actor_kind="human"``), not as the internal
  service;
* a background path with no human and no enclosing agent is recorded as
  ``system`` explicitly;
* historical audit rows (written before these keys existed) remain readable;
* binding a human never changes the policy verdict.
"""

from __future__ import annotations

from src.kernels._crosscutting import (
    bind_request_principal,
    kernel_action,
)


def _newest_event(action_name: str):
    """Return the newest audit row for ``action_name`` (event dict)."""
    from src.kernels.audit import audit_query

    for event in audit_query(limit=200, reverse=True):
        details = event.get("details") or {}
        if details.get("action") == action_name:
            return event
    raise AssertionError(f"no audit row found for {action_name!r}")


def test_delete_audit_names_the_request_human() -> None:
    @kernel_action("memory.delete_by_key")
    def delete_one() -> bool:
        return True

    with bind_request_principal("alice"):
        assert delete_one() is True

    event = _newest_event("memory.delete_by_key")
    details = event["details"]
    assert event["principal_id"] == "alice"
    assert details["actor_kind"] == "human"
    assert details["actor_source"] == "http-request"
    assert details["initiated_by"] == "alice"
    assert details["initiator_kind"] == "human"
    assert details["initiator_source"] == "http-request"


def test_human_attribution_does_not_change_the_verdict() -> None:
    """``identity.grant_permission`` is denied for the service actor.

    Binding a human must only add attribution: the decision stays ``deny`` and,
    because the action is record-only here, it still runs and the audit keeps
    ``policy_enforced=False`` (no new permission was granted).
    """

    @kernel_action("identity.grant_permission")
    def privileged() -> str:
        return "ran"

    with bind_request_principal("alice"):
        assert privileged() == "ran"

    event = _newest_event("identity.grant_permission")
    details = event["details"]
    assert details["policy_decision"] == "deny"
    assert details["policy_enforced"] is False
    assert details["initiated_by"] == "alice"


def test_background_delete_without_human_is_recorded_as_system() -> None:
    @kernel_action("memory.delete_by_key", system_actor=True)
    def cleanup() -> bool:
        return True

    assert cleanup() is True

    event = _newest_event("memory.delete_by_key")
    details = event["details"]
    assert event["principal_id"] == "system"
    assert details["actor_kind"] == "system"
    assert details["actor_source"] == "scheduled"
    assert details["initiated_by"] == "system"
    assert details["initiator_kind"] == "system"
    assert details["initiator_source"] == "scheduled"


def test_real_kernel_delete_action_defaults_to_system() -> None:
    """A bare kernel delete (no HTTP binding, no enclosing agent) must record
    ``system`` -- never the internal service default."""
    from src.kernels.memory import MemoryKernel

    kernel = MemoryKernel(db_path=":memory:")
    assert kernel.delete_by_key("profile:u:fact:missing") == 0

    event = _newest_event("memory.delete_by_key")
    assert event["principal_id"] == "system"
    assert event["details"]["actor_kind"] == "system"


def test_historical_rows_without_initiator_keys_stay_readable() -> None:
    """Old rows have no ``initiated_by``/``initiator_*`` keys -- reading them
    must not raise, and the absence must be visible as ``None``."""
    from src.kernels.audit import (
        AuditEventType,
        AuditScope,
        audit_query,
        log_event,
    )

    log_event(
        event_type=AuditEventType.STATE_CHANGE,
        principal_id="legacy-row",
        scope=AuditScope.L0,
        outcome="success",
        details={"action": "memory.delete_by_key", "risk_level": "HIGH"},
    )

    rows = [r for r in audit_query(limit=200, reverse=True)
            if r.get("principal_id") == "legacy-row"]
    assert rows, "historical audit row was not readable"
    details = rows[0]["details"]
    assert details["action"] == "memory.delete_by_key"
    assert details.get("initiated_by") is None
    assert details.get("initiator_kind") is None
    assert details.get("initiator_source") is None


def test_personal_context_threads_human_and_system_attribution(tmp_path) -> None:
    from src.ai.personal_context import PersonalContextManager
    from src.kernels._crosscutting import acting_principal

    class _CapturingMemory:
        def __init__(self) -> None:
            self.seen = []

        def delete_by_key_prefix(self, prefix: str) -> int:
            self.seen.append(acting_principal())
            return 1

    fake = _CapturingMemory()
    manager = PersonalContextManager(
        db_path=str(tmp_path / "profile.db"), memory_kernel=fake
    )

    manager.clear("user-alice", requested_by="alice")
    assert fake.seen[-1] == {
        "kind": "human",
        "principal": "alice",
        "source": "http-request",
    }

    manager.clear("user-alice")
    assert fake.seen[-1] == {
        "kind": "system",
        "principal": "system",
        "source": "background",
    }


def test_profile_delete_routes_pass_the_token_principal(monkeypatch) -> None:
    from src.gateway import profile as profile_module

    class _CapturingPCM:
        def __init__(self) -> None:
            self.calls = []

        def clear(self, pid, **kwargs):
            self.calls.append(("clear", pid, kwargs.get("requested_by")))
            return 1

        def has_profile(self, pid):
            return False

        def delete_fact(self, pid, key, **kwargs):
            self.calls.append(("delete_fact", pid, key, kwargs.get("requested_by")))
            return True

        def delete_preference(self, pid, key, **kwargs):
            self.calls.append(
                ("delete_preference", pid, key, kwargs.get("requested_by"))
            )
            return True

    pcm = _CapturingPCM()
    monkeypatch.setattr(profile_module, "get_personal_context", lambda: pcm)

    profile_module.clear_profile(principal="alice")
    profile_module.delete_profile_fact("city", principal="alice")
    profile_module.delete_profile_preference("tone", principal="alice")

    assert pcm.calls == [
        ("clear", "user-alice", "alice"),
        ("delete_fact", "user-alice", "city", "alice"),
        ("delete_preference", "user-alice", "tone", "alice"),
    ]
