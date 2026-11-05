"""自动化地基测试：闸门、票据、守卫、急停、审计与 fake 后端闭环。

所有断言都基于真实对象与真实 SQLite，不使用"描述即可通过"的空测试。
"""

from __future__ import annotations

import json
import threading
from typing import List

import pytest

from src.automation.audit import AutomationAudit, AutomationAuditError
from src.automation.backends.fake import FakeAutomationBackend
from src.automation.confirm import SqliteConfirmationBroker
from src.automation.contracts import (
    ActionRequest,
    AutomationError,
    Observation,
    SessionStatus,
    TargetScope,
)
from src.automation.estop import EmergencyStop
from src.automation.gate import AutomationGate
from src.automation.guard import Guard
from src.automation.sessions import AutomationSessionRegistry
from src.automation.tools import build_automation_tools
from src.kernels.audit import AuditEventType, AuditStore


def _audit(tmp_path) -> tuple[AutomationAudit, AuditStore]:
    store = AuditStore(db_path=str(tmp_path / "automation_audit.db"))
    store.initialize()
    return AutomationAudit(store=store), store


def _broker(tmp_path, **kwargs) -> SqliteConfirmationBroker:
    return SqliteConfirmationBroker(str(tmp_path / "automation_tickets.db"), **kwargs)


def _open_session(
    *,
    principal: str = "liuhao",
    principal_kind: str = "human",
    allowlist=("notepad",),
    guard: Guard | None = None,
    broker: SqliteConfirmationBroker | None = None,
    audit: AutomationAudit | None = None,
    estop: EmergencyStop | None = None,
    registry: AutomationSessionRegistry | None = None,
):
    return (registry or AutomationSessionRegistry()).open(
        principal=principal,
        principal_kind=principal_kind,
        backend=FakeAutomationBackend(),
        guard=guard or Guard(allowlist=allowlist),
        broker=broker,
        audit=audit,
        estop=estop or EmergencyStop(),
    )


def _run(session, action: str, **plan_kwargs):
    plan = session.plan(action, **plan_kwargs)
    ticket = session.request_confirmation(plan)
    return plan, session.execute(plan, ticket)


# ---------------------------------------------------------------- 闸门
def test_default_gate_registers_zero_tools(monkeypatch):
    monkeypatch.delenv("LIUHAO_AUTOMATION_ENABLED", raising=False)
    monkeypatch.delenv("LIUHAO_AUTOMATION_BACKEND", raising=False)
    tools = build_automation_tools("liuhao", metadata={"kind": "human"})
    assert tools == []


@pytest.mark.parametrize(
    "env",
    [
        {"LIUHAO_AUTOMATION_ENABLED": "0", "LIUHAO_AUTOMATION_BACKEND": "fake"},
        {"LIUHAO_AUTOMATION_ENABLED": "1"},
        {"LIUHAO_AUTOMATION_BACKEND": "fake"},
        {"LIUHAO_AUTOMATION_ENABLED": "1", "LIUHAO_AUTOMATION_BACKEND": "native"},
    ],
)
def test_each_gate_closed_keeps_tools_empty(env):
    gate = AutomationGate(env=env)
    tools = build_automation_tools("liuhao", metadata={"kind": "human"}, gate=gate)
    assert tools == []


def test_service_identity_keeps_tools_empty():
    gate = AutomationGate(
        env={"LIUHAO_AUTOMATION_ENABLED": "1", "LIUHAO_AUTOMATION_BACKEND": "fake"}
    )
    tools = build_automation_tools("svc", metadata={"kind": "service"}, gate=gate)
    assert tools == []


def test_all_gates_open_registers_only_control_tools(tmp_path):
    gate = AutomationGate(
        env={"LIUHAO_AUTOMATION_ENABLED": "1", "LIUHAO_AUTOMATION_BACKEND": "fake"}
    )
    tools = build_automation_tools(
        "liuhao",
        metadata={"kind": "human"},
        gate=gate,
        broker=_broker(tmp_path),
        audit=_audit(tmp_path)[0],
        registry=AutomationSessionRegistry(),
    )
    names = [t.name for t in tools]
    assert names == [
        "automation.session.open",
        "automation.session.close",
        "automation.session.status",
        "automation.emergency_stop",
    ]
    assert all(t.capability.startswith("automation.") for t in tools)


def test_make_tools_does_not_register_automation_when_default_closed(monkeypatch):
    monkeypatch.delenv("LIUHAO_AUTOMATION_ENABLED", raising=False)
    monkeypatch.delenv("LIUHAO_AUTOMATION_BACKEND", raising=False)

    from src.ai.tools.kernel_tools import make_tools

    names = [t.name for t in make_tools("liuhao")]
    assert not any(name.startswith("automation.") for name in names)


# ---------------------------------------------------------------- 票据
def test_service_identity_cannot_issue_ticket(tmp_path):
    broker = _broker(tmp_path)
    with pytest.raises(AutomationError) as excinfo:
        broker.issue(
            principal="svc",
            principal_kind="service",
            session_id="s-1",
            step_no=1,
            action_fingerprint="fp",
            preview="demo",
        )
    assert excinfo.value.reason == "service_identity"


def test_ticket_expiry_blocks_execution(tmp_path):
    now = [1000.0]
    broker = _broker(tmp_path, clock=lambda: now[0])
    ticket = broker.issue(
        principal="liuhao",
        principal_kind="human",
        session_id="s-1",
        step_no=1,
        action_fingerprint="fp-1",
        preview="demo",
        ttl_seconds=5.0,
    )
    now[0] += 6.0
    result = broker.consume(
        ticket=ticket.ticket,
        principal="liuhao",
        session_id="s-1",
        step_no=1,
        action_fingerprint="fp-1",
    )
    assert result.ok is False
    assert result.reason == "expired"


def test_ticket_concurrent_consumption_has_exactly_one_winner(tmp_path):
    broker = _broker(tmp_path)
    ticket = broker.issue(
        principal="liuhao",
        principal_kind="human",
        session_id="s-1",
        step_no=1,
        action_fingerprint="fp-1",
        preview="demo",
    )
    barrier = threading.Barrier(8)
    outcomes: List[bool] = []
    lock = threading.Lock()

    def _worker() -> None:
        barrier.wait()
        res = broker.consume(
            ticket=ticket.ticket,
            principal="liuhao",
            session_id="s-1",
            step_no=1,
            action_fingerprint="fp-1",
        )
        with lock:
            outcomes.append(res.ok)

    threads = [threading.Thread(target=_worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert outcomes.count(True) == 1
    assert outcomes.count(False) == 7


def test_fingerprint_change_requires_new_confirmation(tmp_path):
    broker = _broker(tmp_path)
    ticket = broker.issue(
        principal="liuhao",
        principal_kind="human",
        session_id="s-1",
        step_no=1,
        action_fingerprint="fp-original",
        preview="demo",
    )
    changed = broker.consume(
        ticket=ticket.ticket,
        principal="liuhao",
        session_id="s-1",
        step_no=1,
        action_fingerprint="fp-changed",
    )
    assert changed.ok is False
    assert changed.reason == "binding_mismatch"

    original = broker.consume(
        ticket=ticket.ticket,
        principal="liuhao",
        session_id="s-1",
        step_no=1,
        action_fingerprint="fp-original",
    )
    assert original.ok is True


def test_params_change_changes_fingerprint():
    first = ActionRequest(
        session_id="s", step_no=1, action="type", target=TargetScope.SCREEN,
        target_ref="notepad", params={"text": "a"},
    )
    second = ActionRequest(
        session_id="s", step_no=1, action="type", target=TargetScope.SCREEN,
        target_ref="notepad", params={"text": "a "},
    )
    assert first.fingerprint() != second.fingerprint()


# ---------------------------------------------------------------- 守卫
def test_guard_default_allowlist_is_empty_and_denies():
    decision = Guard().check(
        ActionRequest(session_id="s", step_no=1, action="click", target_ref="notepad"),
        Observation(focus="notepad"),
    )
    assert decision.allowed is False
    assert decision.reason == "allowlist_empty"


def test_guard_denies_forbidden_targets():
    guard = Guard(allowlist={"password_manager"})
    decision = guard.check(
        ActionRequest(
            session_id="s",
            step_no=1,
            action="click",
            target_ref="password_manager",
        ),
        Observation(focus="password_manager"),
    )
    assert decision.allowed is False
    assert decision.reason == "forbidden_target"


def test_guard_focus_mismatch_denies():
    guard = Guard(allowlist={"notepad"})
    request = ActionRequest(
        session_id="s",
        step_no=1,
        action="click",
        target=TargetScope.APPLICATION,
        target_ref="notepad",
    )
    decision = guard.check(request, Observation(focus="calculator"))
    assert decision.allowed is False
    assert decision.reason == "focus_mismatch"


def test_guard_rate_limit_thirty_actions_per_minute():
    now = [100.0]
    guard = Guard(allowlist={"notepad"}, clock=lambda: now[0])
    request = ActionRequest(session_id="s", step_no=1, action="click", target_ref="notepad")
    for _ in range(30):
        guard.record_attempt()
    decision = guard.check(request, Observation(focus="notepad"))
    assert decision.allowed is False
    assert decision.reason == "rate_limited"


def test_circuit_breaker_freezes_session(tmp_path):
    audit, _store = _audit(tmp_path)
    session = _open_session(
        broker=_broker(tmp_path), audit=audit, registry=AutomationSessionRegistry()
    )
    for _ in range(3):
        plan, result = _run(session, "bogus_action", target_ref="notepad")
        assert result.ok is False
    assert session.guard.tripped is True
    assert session.status is SessionStatus.FROZEN
    with pytest.raises(AutomationError) as excinfo:
        session.plan("click", target_ref="notepad")
    assert excinfo.value.reason == "session_not_open"


# ---------------------------------------------------------------- 急停
def test_estop_hotkey_fallback_and_modifier_cleanup():
    registrar_calls: List[str] = []

    def _registrar(combo):
        registrar_calls.append("+".join(combo))
        return combo == ("ctrl", "alt", "f12")

    def _releaser(key):
        if key == "win":
            raise RuntimeError("win key stuck")

    estop = EmergencyStop(registrar=_registrar, modifier_releaser=_releaser)
    registration = estop.register()
    assert registration.registered is True
    assert registration.combo == "ctrl+alt+f12"
    assert len(registration.attempts) == 3
    assert registrar_calls == ["ctrl+alt+esc", "ctrl+shift+esc", "ctrl+alt+f12"]

    cleanup = estop.clear_modifiers()
    assert cleanup["supported"] is True
    assert cleanup["released"] == ["ctrl", "alt", "shift"]
    assert cleanup["failures"][0]["key"] == "win"


def test_estop_without_registrar_reports_honestly():
    registration = EmergencyStop().register()
    assert registration.registered is False
    assert registration.reason == "no_hotkey_registrar_configured"


def test_emergency_stop_freezes_session(tmp_path):
    audit, _store = _audit(tmp_path)
    estop = EmergencyStop()
    session = _open_session(
        broker=_broker(tmp_path),
        audit=audit,
        estop=estop,
        registry=AutomationSessionRegistry(),
    )
    plan = session.plan("click", target_ref="notepad")
    ticket = session.request_confirmation(plan)
    estop.trigger("manual")
    assert session.status is SessionStatus.FROZEN
    result = session.execute(plan, ticket)
    assert result.ok is False
    assert result.error == "session_frozen"


# ---------------------------------------------------------------- 闭环与审计
def test_fake_backend_full_loop_and_audit_chain(tmp_path):
    audit, store = _audit(tmp_path)
    registry = AutomationSessionRegistry()
    session = _open_session(
        broker=_broker(tmp_path),
        audit=audit,
        registry=registry,
    )

    focus_plan, focus_result = _run(
        session,
        "focus",
        target_ref="notepad",
        params={"app": "notepad"},
    )
    assert focus_result.ok is True
    assert session.verify(focus_plan, focus_result) is True
    assert session.backend.focus == "notepad"

    type_plan, type_result = _run(
        session,
        "type",
        target=TargetScope.APPLICATION,
        target_ref="notepad",
        expected_focus="notepad",
        params={"text": "secret-value"},
    )
    assert type_result.ok is True
    assert session.verify(type_plan, type_result) is True

    ok, total = store.verify_integrity()
    assert ok is True
    assert total == 4  # 两个动作 × (intent + result)

    events = store.query_events(event_type=AuditEventType.AUTOMATION_ACTION)
    serialized = json.dumps(events, ensure_ascii=False, default=str)
    assert "secret-value" not in serialized
    assert "notepad" not in serialized
    assert all("sha256" in event["details"]["session"] for event in events)


def test_audit_failure_fails_action_closed(tmp_path):
    class _BrokenStore:
        def log_event(self, **kwargs):
            raise RuntimeError("audit disk full")

    session = _open_session(
        broker=_broker(tmp_path),
        audit=AutomationAudit(store=_BrokenStore()),
        registry=AutomationSessionRegistry(),
    )
    plan = session.plan("focus", target_ref="notepad", params={"app": "notepad"})
    ticket = session.request_confirmation(plan)
    with pytest.raises(AutomationAuditError):
        session.execute(plan, ticket)
    assert session.backend.focus is None
    assert session.backend.observe().summary["revision"] == 0


def test_closed_session_rejects_plan_and_execute(tmp_path):
    audit, _store = _audit(tmp_path)
    session = _open_session(
        broker=_broker(tmp_path), audit=audit, registry=AutomationSessionRegistry()
    )
    plan = session.plan("click", target_ref="notepad")
    ticket = session.request_confirmation(plan)
    assert session.close("test") is True
    result = session.execute(plan, ticket)
    assert result.ok is False
    assert result.error == "session_closed"


def test_pending_ticket_cannot_replay_second_step(tmp_path):
    audit, _store = _audit(tmp_path)
    session = _open_session(
        broker=_broker(tmp_path), audit=audit, registry=AutomationSessionRegistry()
    )
    plan, result = _run(session, "click", target_ref="notepad")
    assert result.ok is True
    replay = session.execute(plan, session.request_confirmation(plan))
    assert replay.ok is False
    assert replay.error == "step_out_of_order"
    # 第二次执行必须先计划下一步，不能复用旧计划/旧票据
    plan2 = session.plan("click", target_ref="notepad")
    ticket2 = session.request_confirmation(plan2)
    result2 = session.execute(plan2, ticket2)
    assert result2.ok is True
    assert session.step_no == 2
