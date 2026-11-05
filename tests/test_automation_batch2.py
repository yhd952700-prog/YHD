"""第 2 批 · A：动作级白名单（动作 + 对象 + 参数范围）。

覆盖四件事：
1. 不同动作可以分别授权（click 放行、type 拒绝）；
2. 同一动作可以限定目标；
3. 参数类型 / 必填 / 未知 / 范围 / 取值逐项拒绝；
4. 每一次拒绝都带可读 ``message``，且能在会话结果里拿到。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3

import pytest

from src.automation import hotkey_windows
from src.automation.audit import AutomationAudit
from src.automation.backends.fake import FakeAutomationBackend
from src.automation.confirm import SqliteConfirmationBroker
from src.automation.contracts import (
    ActionCapability,
    ActionRequest,
    ActionResult,
    AutomationError,
    Observation,
    Reversibility,
    TargetScope,
)
from src.automation.estop import EmergencyStop
from src.automation.gate import AutomationGate
from src.automation.guard import ActionRule, Guard, ParamConstraint
from src.automation.replay import AuditReplay
from src.automation.sessions import AutomationSessionRegistry
from src.automation.tools import build_automation_tools
from src.kernels.audit import AuditEventType, AuditStore


def _request(action: str, target_ref: str = "notepad", **params) -> ActionRequest:
    return ActionRequest(
        session_id="s",
        step_no=1,
        action=action,
        target=TargetScope.APPLICATION,
        target_ref=target_ref,
        params=params,
        expected_focus=target_ref,
    )


def _guard(*rules: ActionRule, allowlist=("notepad",)) -> Guard:
    return Guard(allowlist=allowlist, action_rules=rules)


CLICK = ActionRule(
    action="click",
    targets=("notepad",),
    params={
        "x": ParamConstraint(required=True, kind="number", min=0, max=1920),
        "y": ParamConstraint(required=True, kind="number", min=0, max=1080),
    },
)
TYPE_INTO = ActionRule(
    action="type",
    targets=("notepad",),
    params={"text": ParamConstraint(required=True, kind="string", max_length=20)},
)


# ---------------------------------------------------------------- 动作分别授权
def test_click_is_authorized_but_type_is_not():
    guard = _guard(CLICK)
    allowed = guard.check(_request("click", x=100, y=200), Observation(focus="notepad"))
    assert allowed.allowed is True

    denied = guard.check(_request("type", text="hi"), Observation(focus="notepad"))
    assert denied.allowed is False
    assert denied.reason == "action_not_allowed"
    assert "type" in denied.message


def test_action_is_limited_to_declared_targets():
    guard = _guard(ActionRule(action="click", targets=("editor",)))
    decision = guard.check(_request("click", target_ref="notepad"), Observation(focus="notepad"))
    assert decision.allowed is False
    assert decision.reason == "action_target_not_allowed"
    assert "notepad" in decision.message


def test_target_prefix_wildcard_matches():
    guard = _guard(ActionRule(action="click", targets=("edit*",)), allowlist=("editor-1",))
    decision = guard.check(_request("click", target_ref="editor-1"), Observation(focus="editor-1"))
    assert decision.allowed is True


def test_empty_targets_means_any_allowed_target():
    guard = _guard(ActionRule(action="click", params={}), allowlist=("calculator",))
    decision = guard.check(
        _request("click", target_ref="calculator"),
        Observation(focus="calculator"),
    )
    assert decision.allowed is True


def test_without_action_rules_target_level_allowlist_still_works():
    """向后兼容：未配置动作规则时行为与第 1 批一致。"""

    guard = Guard(allowlist=("notepad",))
    decision = guard.check(_request("bogus_action"), Observation(focus="notepad"))
    assert decision.allowed is True


# ---------------------------------------------------------------- 参数范围
@pytest.mark.parametrize(
    ("params", "reason", "fragment"),
    [
        ({"x": -1, "y": 10}, "param_below_min", "x=-1.0"),
        ({"x": 5000, "y": 10}, "param_above_max", "5000.0"),
        ({"x": 10, "y": 2000}, "param_above_max", "y=2000.0"),
        ({"x": 10}, "param_missing", "y"),
        ({"x": 10, "y": 10, "z": 1}, "param_not_allowed", "z"),
        ({"x": True, "y": 10}, "param_wrong_type", "x"),
        ({"x": "10", "y": 10}, "param_wrong_type", "x"),
    ],
)
def test_click_param_constraints_reject_with_readable_reason(params, reason, fragment):
    guard = _guard(CLICK)
    decision = guard.check(_request("click", **params), Observation(focus="notepad"))
    assert decision.allowed is False
    assert decision.reason == reason
    assert fragment in decision.message


def test_type_text_length_limits():
    guard = _guard(TYPE_INTO)
    assert guard.check(_request("type", text="ok"), Observation(focus="notepad")).allowed is True

    too_long = guard.check(_request("type", text="x" * 21), Observation(focus="notepad"))
    assert too_long.reason == "param_too_long"
    assert "21" in too_long.message and "20" in too_long.message


def test_type_rejects_non_string_text():
    guard = _guard(TYPE_INTO)
    decision = guard.check(_request("type", text=123), Observation(focus="notepad"))
    assert decision.reason == "param_wrong_type"


def test_allowed_values_are_enforced():
    guard = _guard(
        ActionRule(
            action="key",
            targets=("notepad",),
            params={
                "key": ParamConstraint(
                    required=True,
                    kind="string",
                    allowed_values=("enter",),
                )
            },
        )
    )
    ok = guard.check(_request("key", key="enter"), Observation(focus="notepad"))
    assert ok.allowed is True

    denied = guard.check(_request("key", key="ctrl+c"), Observation(focus="notepad"))
    assert denied.reason == "param_value_not_allowed"
    assert "key" in denied.message


def test_unknown_kind_is_reported_not_silently_ignored():
    guard = _guard(
        ActionRule(
            action="click",
            params={"x": ParamConstraint(kind="integer")},
        ),
        allowlist=("notepad",),
    )
    decision = guard.check(_request("click", x=1), Observation(focus="notepad"))
    assert decision.reason == "param_constraint_unsupported"


def test_multiple_rules_for_same_action_any_match_wins():
    guard = _guard(
        ActionRule(
            action="click",
            targets=("notepad",),
            params={"x": ParamConstraint(kind="number", max=100)},
        ),
        ActionRule(
            action="click",
            targets=("notepad",),
            params={"x": ParamConstraint(kind="number", max=2000)},
        ),
    )
    assert guard.check(_request("click", x=1500), Observation(focus="notepad")).allowed is True


# ---------------------------------------------------------------- 会话产物
def _open_session(tmp_path, guard: Guard, backend=None, audit=None, estop=None):
    store = AuditStore(db_path=str(tmp_path / "audit.db"))
    store.initialize()
    return (
        AutomationSessionRegistry().open(
            principal="liuhao",
            principal_kind="human",
            backend=backend or FakeAutomationBackend(),
            guard=guard,
            broker=SqliteConfirmationBroker(str(tmp_path / "tickets.db")),
            audit=audit or AutomationAudit(store=store),
            estop=estop or EmergencyStop(),
        ),
        store,
    )


def test_session_surfaces_guard_message_and_detail(tmp_path):
    session, store = _open_session(tmp_path, _guard(CLICK))
    plan = session.plan(
        "click",
        target=TargetScope.APPLICATION,
        target_ref="notepad",
        expected_focus="notepad",
        params={"x": 3000, "y": 10},
    )
    ticket = session.request_confirmation(plan)
    result = session.execute(plan, ticket)

    assert result.status == "rejected"
    assert result.error == "guard_param_above_max"
    assert result.detail["message"]
    assert result.detail["param"] == "x"
    assert session.backend.observe().summary["revision"] == 0

    events = store.query_events(event_type=AuditEventType.AUTOMATION_ACTION)
    assert len(events) == 1
    assert events[0]["outcome"] == "deny"
    error_digest = events[0]["details"]["error"]
    assert error_digest["len"] == len("guard_param_above_max")
    assert error_digest["sha256"]


def test_session_denies_action_not_in_rules(tmp_path):
    session, _store = _open_session(tmp_path, _guard(CLICK))
    plan = session.plan(
        "type",
        target=TargetScope.APPLICATION,
        target_ref="notepad",
        expected_focus="notepad",
        params={"text": "hello"},
    )
    ticket = session.request_confirmation(plan)
    result = session.execute(plan, ticket)

    assert result.error == "guard_action_not_allowed"
    assert "type" in result.detail["message"]


# ---------------------------------------------------------------- B 回滚
class _IrreversibleClickBackend(FakeAutomationBackend):
    """模拟"策略声明某动作不可逆"的后端。"""

    def capability(self, request: ActionRequest) -> ActionCapability:
        return ActionCapability(
            action=str(request.action),
            reversibility=Reversibility.IRREVERSIBLE,
            reason="policy_marks_irreversible",
        )


class _FailingRollbackBackend(FakeAutomationBackend):
    def rollback(self, *, request: ActionRequest, result: ActionResult) -> ActionResult:
        return ActionResult(
            status="failed",
            action=request.action,
            ok=False,
            error="snapshot_corrupted",
        )


class _ExplodingRollbackBackend(FakeAutomationBackend):
    def rollback(self, *, request: ActionRequest, result: ActionResult) -> ActionResult:
        raise RuntimeError("rollback crashed")


def test_capability_marks_known_actions_reversible_and_unknown_irreversible():
    backend = FakeAutomationBackend()
    click = backend.capability(_request("click"))
    assert click.reversible is True
    assert click.to_dict()["reversibility"] == "reversible"

    delete = backend.capability(_request("delete"))
    assert delete.reversible is False
    assert delete.reason == "destructive_action"

    unknown = backend.capability(_request("some_future_action"))
    assert unknown.reversible is False
    assert unknown.reason == "unknown_action"


def test_irreversible_action_is_announced_before_execution(tmp_path):
    session, _store = _open_session(
        tmp_path,
        Guard(allowlist=("notepad",)),
        backend=_IrreversibleClickBackend(),
    )
    plan = session.plan("click", target_ref="notepad")
    assert session.describe(plan)["reversible"] is False
    ticket = session.request_confirmation(plan)
    assert "不可逆动作" in ticket.preview
    assert "policy_marks_irreversible" in ticket.preview

    result = session.execute(plan, ticket)
    assert result.ok is True
    assert result.detail["reversibility"] == "irreversible"
    assert result.detail["irreversible_reason"] == "policy_marks_irreversible"


def test_irreversible_action_cannot_be_rolled_back(tmp_path):
    session, _store = _open_session(
        tmp_path,
        Guard(allowlist=("notepad",)),
        backend=_IrreversibleClickBackend(),
    )
    plan = session.plan("click", target_ref="notepad")
    ticket = session.request_confirmation(plan)
    result = session.execute(plan, ticket)
    assert result.ok is True

    with pytest.raises(AutomationError) as excinfo:
        session.request_rollback_confirmation(plan, result)
    assert excinfo.value.reason == "rollback_not_reversible"
    assert "policy_marks_irreversible" in str(excinfo.value)


def test_rollback_restores_snapshot_and_is_audited(tmp_path):
    session, store = _open_session(
        tmp_path,
        Guard(allowlist=("notepad", "calculator")),
        backend=FakeAutomationBackend(initial_focus="notepad"),
    )
    plan = session.plan("focus", target_ref="calculator", params={"app": "calculator"})
    ticket = session.request_confirmation(plan)
    result = session.execute(plan, ticket)
    assert result.ok is True
    assert session.backend.focus == "calculator"

    rollback_ticket = session.request_rollback_confirmation(plan, result)
    rolled = session.rollback(plan, result, rollback_ticket)
    assert rolled.ok is True
    assert rolled.detail["rollback_completed"] is True
    assert session.backend.focus == "notepad"

    ok, total = store.verify_integrity()
    assert ok is True
    assert total == 4  # intent / result / rollback_intent / rollback_result

    stages = [
        event["details"]["stage"]
        for event in store.query_events(event_type=AuditEventType.AUTOMATION_ACTION)
    ]
    assert stages == ["intent", "result", "rollback_intent", "rollback_result"]


def test_rollback_requires_its_own_ticket(tmp_path):
    session, _store = _open_session(tmp_path, Guard(allowlist=("notepad",)))
    plan = session.plan("click", target_ref="notepad")
    execute_ticket = session.request_confirmation(plan)
    result = session.execute(plan, execute_ticket)
    assert result.ok is True

    rolled = session.rollback(plan, result, execute_ticket)
    assert rolled.ok is False
    assert rolled.error == "ticket_binding_mismatch"
    assert rolled.detail["rollback_completed"] is False
    assert "回滚票据无效" in rolled.detail["message"]


def test_duplicate_rollback_is_rejected(tmp_path):
    session, _store = _open_session(tmp_path, Guard(allowlist=("notepad",)))
    plan = session.plan("click", target_ref="notepad")
    result = session.execute(plan, session.request_confirmation(plan))
    ticket = session.request_rollback_confirmation(plan, result)
    assert session.rollback(plan, result, ticket).ok is True

    with pytest.raises(AutomationError) as excinfo:
        session.request_rollback_confirmation(plan, result)
    assert excinfo.value.reason == "rollback_already_performed"

    again = session.rollback(plan, result, ticket)
    assert again.ok is False
    assert again.error == "rollback_already_performed"
    assert again.detail["rollback_completed"] is False


def test_rollback_of_failed_action_is_rejected(tmp_path):
    session, _store = _open_session(tmp_path, Guard(allowlist=("notepad",)))
    plan = session.plan("future_action", target_ref="notepad")
    result = session.execute(plan, session.request_confirmation(plan))
    assert result.ok is False

    rolled = session.rollback(plan, result, session.request_confirmation(plan))
    assert rolled.ok is False
    assert rolled.error == "rollback_of_failed_action"
    assert rolled.detail["rollback_completed"] is False


def test_rollback_of_unexecuted_step_is_rejected(tmp_path):
    session, _store = _open_session(tmp_path, Guard(allowlist=("notepad",)))
    first = session.plan("click", target_ref="notepad")
    result = session.execute(first, session.request_confirmation(first))
    assert result.ok is True

    never_planned = session.plan("click", target_ref="notepad")
    assert never_planned.step_no == 2
    rolled = session.rollback(never_planned, result, session.request_confirmation(first))
    assert rolled.ok is False
    assert rolled.error == "rollback_target_not_executed"


def test_rollback_failure_reports_incomplete_without_swallowing(tmp_path):
    session, store = _open_session(
        tmp_path,
        Guard(allowlist=("notepad",)),
        backend=_FailingRollbackBackend(),
    )
    plan = session.plan("click", target_ref="notepad")
    result = session.execute(plan, session.request_confirmation(plan))
    ticket = session.request_rollback_confirmation(plan, result)

    rolled = session.rollback(plan, result, ticket)
    assert rolled.status == "failed"
    assert rolled.error == "snapshot_corrupted"
    assert rolled.detail["rollback_completed"] is False
    assert "回滚未完成" in rolled.detail["message"]

    events = store.query_events(event_type=AuditEventType.AUTOMATION_ACTION)
    assert [event["details"]["stage"] for event in events] == [
        "intent",
        "result",
        "rollback_intent",
        "rollback_result",
    ]
    assert events[-1]["outcome"] == "failed"


def test_rollback_backend_exception_is_reported(tmp_path):
    session, _store = _open_session(
        tmp_path,
        Guard(allowlist=("notepad",)),
        backend=_ExplodingRollbackBackend(),
    )
    plan = session.plan("click", target_ref="notepad")
    result = session.execute(plan, session.request_confirmation(plan))
    ticket = session.request_rollback_confirmation(plan, result)

    rolled = session.rollback(plan, result, ticket)
    assert rolled.ok is False
    assert rolled.error == "rollback_backend_exception"
    assert rolled.detail["rollback_completed"] is False
    assert "RuntimeError" in rolled.detail["message"]


# ---------------------------------------------------------------- C 审计回放
def _run_two_step_session(tmp_path, *, backend=None):
    session, store = _open_session(
        tmp_path,
        Guard(allowlist=("notepad", "calculator")),
        backend=backend or FakeAutomationBackend(initial_focus="notepad"),
    )
    first = session.plan(
        "focus",
        target_ref="calculator",
        params={"app": "calculator"},
    )
    first_result = session.execute(first, session.request_confirmation(first))
    assert first_result.ok is True
    rollback_ticket = session.request_rollback_confirmation(first, first_result)
    assert session.rollback(first, first_result, rollback_ticket).ok is True

    second = session.plan("future_action", target_ref="notepad")
    second_result = session.execute(second, session.request_confirmation(second))
    assert second_result.ok is False
    return session, store, first, second


def test_replay_rebuilds_session_from_cold_store(tmp_path):
    session, store, first, second = _run_two_step_session(tmp_path)
    db_path = str(tmp_path / "audit.db")
    store.shutdown()

    replay = AuditReplay(db_path=db_path).rebuild(
        principal="liuhao",
        session_id=session.session_id,
    )

    assert replay.principal == "liuhao"
    assert replay.chain_verified is True
    assert replay.truncated is False
    assert replay.events == 6
    assert [step.step_no for step in replay.steps] == [1, 2]

    step_one = replay.steps[0]
    assert step_one.action == "focus"
    assert step_one.target == "screen"
    assert step_one.target_ref_sha256 == hashlib.sha256(b"calculator").hexdigest()
    assert step_one.action_fingerprint == first.fingerprint()
    assert step_one.result == "rolled_back"
    assert step_one.stages == (
        "intent",
        "result",
        "rollback_intent",
        "rollback_result",
    )

    step_two = replay.steps[1]
    assert step_two.action == "future_action"
    assert step_two.action_fingerprint == second.fingerprint()
    assert step_two.result == "failed"
    assert step_two.reversibility == "irreversible"

    state = replay.derive_state()
    assert state["steps"] == 2
    assert state["failed"] == 1
    assert state["rolled_back"] == 1
    assert state["frozen"] is False
    assert replay.first_seen_at <= replay.last_seen_at


def test_replay_unknown_session_returns_empty_trace(tmp_path):
    _session, store, _first, _second = _run_two_step_session(tmp_path)
    store.shutdown()

    replay = AuditReplay(db_path=str(tmp_path / "audit.db")).rebuild(
        principal="liuhao",
        session_id="not-a-real-session",
    )
    assert replay.events == 0
    assert replay.steps == []
    assert replay.warnings == []


def test_replay_marks_truncated_queries(tmp_path):
    session, store, _first, _second = _run_two_step_session(tmp_path)
    store.shutdown()

    replay = AuditReplay(db_path=str(tmp_path / "audit.db")).rebuild(
        principal="liuhao",
        session_id=session.session_id,
        limit=1,
    )
    assert replay.truncated is True
    assert replay.events == 1
    assert "audit_query_truncated" in replay.warnings


def test_replay_reports_broken_chain_honestly(tmp_path):
    session, store, _first, _second = _run_two_step_session(tmp_path)
    db_path = str(tmp_path / "audit.db")
    store.shutdown()

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT event_id, details FROM audit_events ORDER BY seq LIMIT 1"
        ).fetchone()
        details = json.loads(row[1])
        details["tampered"] = True
        conn.execute(
            "UPDATE audit_events SET details = ? WHERE event_id = ?",
            (json.dumps(details), row[0]),
        )
        conn.commit()
    finally:
        conn.close()

    replay = AuditReplay(db_path=db_path).rebuild(
        principal="liuhao",
        session_id=session.session_id,
    )
    assert replay.chain_verified is False
    assert "audit_hash_chain_verification_failed" in replay.warnings
    assert replay.steps  # 仍然给出轨迹，但明确标注链已不可信


# ---------------------------------------------------------------- D 热键注册接缝
class _ObjectRegistrar:
    """实现 HotkeyRegistrar 接口的替身。"""

    def __init__(self, outcomes=None, exc=None):
        self.outcomes = list(outcomes or [])
        self.exc = exc
        self.calls = []
        self.register_calls = 0
        self.unregister_calls = 0

    def register(self, combo) -> bool:
        self.calls.append(tuple(combo))
        self.register_calls += 1
        if self.exc is not None:
            raise self.exc
        if not self.outcomes:
            return True
        return bool(self.outcomes.pop(0))

    def unregister(self) -> None:
        self.unregister_calls += 1


class _FakeWin32Backend:
    def __init__(self, *, fail_with=None, messages=None):
        self.fail_with = fail_with
        self.messages = list(messages or [])
        self.register_calls = 0
        self.unregister_calls = 0

    def register(self, hotkey_id, modifiers, virtual_key):
        self.register_calls += 1
        if self.fail_with is not None:
            raise self.fail_with

    def unregister(self, hotkey_id):
        self.unregister_calls += 1

    def peek(self):
        return self.messages.pop(0) if self.messages else None


def test_hotkeys_are_disabled_by_default(monkeypatch):
    estop = EmergencyStop()
    assert hotkey_windows.hotkey_enabled({}) is False
    assert hotkey_windows.hotkey_enabled({"LIUHAO_AUTOMATION_HOTKEY_ENABLED": "0"}) is False
    assert hotkey_windows.hotkey_enabled({"LIUHAO_AUTOMATION_HOTKEY_ENABLED": "yes"}) is True

    assert hotkey_windows.configure_from_env(estop, env={}) is None
    assert estop.registration is None
    assert estop._registrar is None  # noqa: SLF001 - 明确断言没有注入任何注册器


def test_object_registrar_interface_and_fallback(monkeypatch):
    registrar = _ObjectRegistrar(outcomes=[False, True])
    estop = EmergencyStop(
        registrar=registrar,
        hotkeys=[("ctrl", "alt", "esc"), ("ctrl", "shift", "esc")],
    )
    registration = estop.register()
    assert registration.registered is True
    assert registration.combo == "ctrl+shift+esc"
    assert registration.attempts[0]["ok"] is False
    assert "error" not in registration.attempts[0]

    again = estop.register()
    assert again == registration
    assert registrar.register_calls == 2  # 已注册后不再重复尝试

    released = estop.unregister()
    assert released["unregistered"] is True
    assert registrar.unregister_calls == 1
    assert estop.registration is None


def test_registrar_failure_is_reported_not_hidden():
    registrar = _ObjectRegistrar(exc=RuntimeError("hotkey occupied by another app"))
    estop = EmergencyStop(registrar=registrar, hotkeys=[("ctrl", "alt", "esc")])
    registration = estop.register()
    assert registration.registered is False
    assert registration.reason == "all_combinations_unavailable"
    assert "RuntimeError" in registration.attempts[0]["error"]
    assert "hotkey occupied" in registration.attempts[0]["error"]


def test_set_registrar_refuses_to_replace_active_registration():
    first = _ObjectRegistrar()
    second = _ObjectRegistrar()
    estop = EmergencyStop(registrar=first, hotkeys=[("ctrl", "alt", "esc")])
    assert estop.register().registered is True
    assert estop.set_registrar(second) is False
    assert estop.set_registrar(first) is True
    assert estop.set_registrar(second, replace=True) is True
    assert estop.registration is None


def test_parse_combo():
    modifiers, vk = hotkey_windows.parse_combo(("ctrl", "alt", "esc"))
    assert modifiers == hotkey_windows.MOD_CONTROL | hotkey_windows.MOD_ALT
    assert vk == 0x1B

    with pytest.raises(ValueError, match="hotkey_combo_requires_exactly_one_key"):
        hotkey_windows.parse_combo(("ctrl", "alt"))
    with pytest.raises(ValueError, match="hotkey_requires_modifier"):
        hotkey_windows.parse_combo(("esc",))
    with pytest.raises(ValueError, match="unsupported_hotkey_key"):
        hotkey_windows.parse_combo(("ctrl", "alt", "volume-up"))


def test_windows_registrar_reports_occupied_hotkey():
    backend = _FakeWin32Backend(fail_with=RuntimeError("occupied:1409"))
    registrar = hotkey_windows.WindowsHotkeyRegistrar(backend=backend, ready_timeout=2.0)
    assert registrar.register(("ctrl", "alt", "esc")) is False
    assert registrar.registered is False
    assert "occupied" in registrar.last_error
    assert backend.unregister_calls == 1  # 失败后仍然清理，不留半注册状态


def test_windows_registrar_success_and_unregister_without_touching_real_hotkeys():
    backend = _FakeWin32Backend()
    registrar = hotkey_windows.WindowsHotkeyRegistrar(backend=backend, ready_timeout=2.0)
    assert registrar.register(("ctrl", "alt", "esc")) is True
    assert registrar.registered is True

    registrar.unregister()
    assert registrar.registered is False
    assert backend.unregister_calls == 1


def test_configure_from_env_uses_registrar_only_when_enabled(monkeypatch):
    class _AutoRegistrar(_ObjectRegistrar):
        def __init__(self, **_kwargs):
            super().__init__()

    monkeypatch.setattr(hotkey_windows, "WindowsHotkeyRegistrar", _AutoRegistrar)
    estop = EmergencyStop()
    registration = hotkey_windows.configure_from_env(
        estop,
        env={"LIUHAO_AUTOMATION_HOTKEY_ENABLED": "1"},
    )
    assert registration.registered is True
    assert estop.registration.registered is True
    assert isinstance(estop._registrar, _AutoRegistrar)  # noqa: SLF001


def test_tools_do_not_touch_hotkeys_when_gate_is_closed(monkeypatch):
    calls = []
    monkeypatch.setattr(
        hotkey_windows,
        "configure_from_env",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    estop = EmergencyStop()
    tools = build_automation_tools(
        "liuhao",
        gate=AutomationGate(env={}),
        estop=estop,
    )
    assert tools == []
    assert calls == []
    assert estop.registration is None



