"""完全授权模式（A 步）与「所有执行主体一律继承」（B 步）的回归钉。

2026-11-11 的决定（老板拍板，后续不许被改松的地方）：

* **A**：全权是一个**被识别的运行模式**，不是"把 ``LIUHAO_KERNEL_POLICY_ENFORCE``
  清空"。清空会让 ``/v1/ready`` 判 ``UNARMED_PRODUCTION`` 并 503，托盘一直报错。
* **B**：全权模式下**所有执行主体**都继承 —— 用户手动派活、AI 员工、定时触发、
  自主接活、多级委派。不再有"仅手动派活才继承"的限制（旧的
  "没有 correlation_id 就不继承"用例因此被明确作废，见文末注释）。
* **总闸**：收回全权**立即生效、不重启**；开启全权才需要重启。
* **全量审计**：员工/定时任务自己触发的动作，也要能追到"哪个员工、哪个任务、
  哪条 goal"。
"""

from __future__ import annotations

import pytest

from src.kernels._crosscutting import PolicyDeferredError, kernel_action, task_attribution
from src.kernels.audit import AuditEventType, get_audit_store
from src.security import authority_mode
from src.security.authority_mode import ENV_VAR, SETTINGS_FILE_ENV

ACTION = "memory.delete_by_key_prefix"


@pytest.fixture(autouse=True)
def _isolated_mode(tmp_path, monkeypatch):
    """每个用例独立的环境变量 + 落盘文件 + 干净快照。"""
    monkeypatch.delenv(ENV_VAR, raising=False)
    monkeypatch.setenv(SETTINGS_FILE_ENV, str(tmp_path / "authority_mode.json"))
    authority_mode.reload()
    yield
    authority_mode.reload()


def _purge() -> str:
    """一个真实存在的 HIGH 风险内核动作（内存按前缀删除）。"""

    @kernel_action(ACTION)
    def _run(prefix: str) -> str:
        return f"purged:{prefix}"

    return _run("liuhao")


def _arm(monkeypatch, spec: str = "HIGH") -> None:
    monkeypatch.setenv("LIUHAO_KERNEL_POLICY_ENFORCE", spec)


# --------------------------------------------------------------------------- #
# A：模式可识别、切换语义
# --------------------------------------------------------------------------- #
def test_env_full_is_recognised_and_snapshot_does_not_hot_switch(monkeypatch):
    monkeypatch.setenv(ENV_VAR, "full")
    authority_mode.reload()
    assert authority_mode.is_full() is True

    # 改了环境变量也不能在本进程里生效 —— 放宽必须重启。
    monkeypatch.setenv(ENV_VAR, "standard")
    assert authority_mode.current_mode() == "full"
    assert authority_mode.describe()["restart_required"] is True


def test_enabling_full_writes_pending_and_needs_restart():
    assert authority_mode.is_full() is False

    info = authority_mode.set_stored_mode("full", updated_by="liuhao")
    assert info["mode"] == "standard"  # 本进程没变
    assert info["next_mode"] == "full"
    assert info["restart_required"] is True

    authority_mode.reload()  # 模拟重启
    assert authority_mode.is_full() is True


def test_unknown_mode_falls_back_to_standard(monkeypatch):
    monkeypatch.setenv(ENV_VAR, "FULL-ISH")
    authority_mode.reload()
    assert authority_mode.current_mode() == "standard"


# --------------------------------------------------------------------------- #
# 总闸：收回立即生效，不需要重启
# --------------------------------------------------------------------------- #
def test_revoke_takes_effect_immediately_without_restart(monkeypatch):
    monkeypatch.setenv(ENV_VAR, "full")
    _arm(monkeypatch)
    authority_mode.reload()
    assert _purge() == "purged:liuhao"  # 全权下可执行

    authority_mode.revoke_now(reason="test kill switch")
    assert authority_mode.is_full() is False
    assert authority_mode.current_mode() == "standard"

    with pytest.raises(PolicyDeferredError):
        _purge()


# --------------------------------------------------------------------------- #
# B：全权模式下所有执行主体一律继承
# --------------------------------------------------------------------------- #
def test_full_mode_employee_inherits_even_without_correlation_id(monkeypatch):
    """员工触发的动作：没有 correlation_id 也继承（旧规则已被明确作废）。"""
    monkeypatch.setenv(ENV_VAR, "full")
    _arm(monkeypatch)
    authority_mode.reload()

    with task_attribution(employee="emp-7", task_id="t-1", goal_id="g-1"):
        assert _purge() == "purged:liuhao"


def test_full_mode_scheduled_task_inherits(monkeypatch):
    """定时触发/自主接活：没有任何人工派活痕迹，也继承。"""
    monkeypatch.setenv(ENV_VAR, "full")
    _arm(monkeypatch)
    authority_mode.reload()

    with task_attribution(employee="scheduler", task_id="t-9", goal_id="g-9"):
        assert _purge() == "purged:liuhao"


def test_standard_mode_still_defers_the_same_action(monkeypatch):
    """标准模式必须保持原样：同一个动作照旧被拦住。"""
    _arm(monkeypatch)
    assert authority_mode.current_mode() == "standard"

    with pytest.raises(PolicyDeferredError):
        _purge()


def test_full_mode_leaves_employee_attribution_in_the_audit_chain(monkeypatch):
    """全量审计：员工自己触发的放行，必须能追到员工/任务/goal。"""
    monkeypatch.setenv(ENV_VAR, "full")
    _arm(monkeypatch)
    authority_mode.reload()

    with task_attribution(employee="emp-7", task_id="t-1", goal_id="g-1"):
        assert _purge() == "purged:liuhao"

    rows = [
        row
        for row in get_audit_store().query_events(event_type=AuditEventType.STATE_CHANGE)
        if (row.get("details") or {}).get("action") == ACTION
        and row.get("outcome") == "success"
    ]
    assert rows, "全权模式下放行的高风险动作没有留下成功审计"
    details = rows[-1]["details"]
    assert details["policy_rule"] == "authority_mode_full"
    assert details["authority_mode"] == "full"
    attribution = details["task_attribution"]
    assert attribution["principal_class"] == "SYSTEM_PRINCIPAL"
    assert attribution["employee"] == "emp-7"
    assert attribution["task_id"] == "t-1"
    assert attribution["goal_id"] == "g-1"


# --------------------------------------------------------------------------- #
# 设置页接口：切换写审计 + 收回立即生效
# --------------------------------------------------------------------------- #
def test_settings_endpoint_records_the_switch_and_revokes_for_standard(monkeypatch):
    from src.gateway.policy import AuthorityModeRequest, set_authority_mode

    monkeypatch.setenv(ENV_VAR, "full")
    authority_mode.reload()

    result = set_authority_mode(
        AuthorityModeRequest(mode="standard", reason="test: 收回全权"), principal="liuhao"
    )
    assert result["mode"] == "standard"
    assert result["effective"] == "now"
    assert result["restart_required"] is False

    rows = get_audit_store().query_events(
        event_type=AuditEventType.AUTHORITY_MODE_CHANGED
    )
    assert len(rows) == 1
    details = rows[0]["details"]
    assert details["current_mode"] == "standard"
    assert details["previous_mode"] == "full"
    assert rows[0]["principal_id"] == "liuhao"


def test_health_check_recognises_full_mode_as_healthy():
    """/v1/ready 的 c2_arming 检查必须认得 full，且不因此降级。"""
    from src.security.c2_arming import evaluate_c2_arming

    verdict = evaluate_c2_arming(
        {"enforced_actions": ["memory.delete_by_key_prefix"]},
        production=True,
        authority_mode="full",
    )
    check = verdict.to_check()
    assert check["status"] == "healthy"
    assert check["state"] == "armed"
    assert check["authority_mode"] == "full"
    assert check["full_authority"] is True

    # 对照：没有模式信息时行为不变。
    plain = evaluate_c2_arming(
        {"enforced_actions": ["memory.delete_by_key_prefix"]}, production=True
    ).to_check()
    assert "authority_mode" not in plain


# 说明（不要"恢复"成继承测试）：旧版这里有一条
# ``test_call_without_correlation_id_does_not_inherit_even_in_full_mode``。
# 2026-11-11 老板明确改为「员工/定时/自主接活全部继承」，该用例的前提不再成立，
# 已由 test_full_mode_employee_inherits_even_without_correlation_id 取代。
# 仍然成立的是：**标准模式**下这条动作必须被拦住（见上一条回归）。
