"""第 2 批 · A：动作级白名单（动作 + 对象 + 参数范围）。

覆盖四件事：
1. 不同动作可以分别授权（click 放行、type 拒绝）；
2. 同一动作可以限定目标；
3. 参数类型 / 必填 / 未知 / 范围 / 取值逐项拒绝；
4. 每一次拒绝都带可读 ``message``，且能在会话结果里拿到。
"""

from __future__ import annotations

import pytest

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
from src.automation.guard import ActionRule, Guard, ParamConstraint
from src.automation.sessions import AutomationSessionRegistry
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

