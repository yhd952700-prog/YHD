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
from src.automation.contracts import ActionRequest, Observation, TargetScope
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
def _open_session(tmp_path, guard: Guard):
    store = AuditStore(db_path=str(tmp_path / "audit.db"))
    store.initialize()
    return (
        AutomationSessionRegistry().open(
            principal="liuhao",
            principal_kind="human",
            backend=FakeAutomationBackend(),
            guard=guard,
            broker=SqliteConfirmationBroker(str(tmp_path / "tickets.db")),
            audit=AutomationAudit(store=store),
            estop=EmergencyStop(),
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
