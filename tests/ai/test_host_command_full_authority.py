"""全权模式下主机命令的边界（第 3 步）回归钉。

2026-11-11 老板定的边界，后续不许被改松：

* **提权一律拒绝**：全权也不提权（不碰 UAC / 服务 / 计划任务 / HKLM）。
* **敏感路径只记不拦**（2026-11-11 第二轮）：审计库、日志、密钥、程序本体、模式
  配置不再对 AI 只读；标准模式与全权模式一致，命中先写审计再照常执行，命令层与
  文件层都不再拒绝。用户本人（actor="human"）不写这条记录。
* **危险命令一次确认**：默认开；设置里可关，关闭进审计（G3），恢复也留痕（G4）；
  关闭后执行仍必须写 G2（不可逆动作放行）。
* **主机命令可用**：全权模式下不是 0 可用，普通命令直接放行。

审计事件用 ``POLICY_EVAL`` 承载，靠 ``details.event ==
"authority.protected_path_touched"`` 区分（独立枚举在 kernels/audit，本轮文件域
不含）。边界必须如实：命令层只是字符串包含判断，拼接/编码可绕过。这里不假装它
是文件系统级边界。
"""

from __future__ import annotations

import pytest

from src.ai.host_command import confirm, enablement
from src.ai.host_command.approval import ApprovalInterface
from src.ai.host_command.broker import HostCommandBroker
from src.ai.host_command.harness import FakeExecutor
from src.ai.host_command.models import DecisionOutcome, HostCommandRequest
from src.kernels._crosscutting import task_attribution
from src.kernels.audit import AuditEventType, get_audit_store
from src.security import authority_mode, protected_paths

_REPO_ROOT = protected_paths._REPO_ROOT


@pytest.fixture(autouse=True)
def _full_mode(tmp_path, monkeypatch):
    """每个用例：全权模式 + 独立的数据目录/确认配置 + 干净快照。"""
    monkeypatch.delenv(enablement.ENV_VAR, raising=False)
    monkeypatch.delenv(confirm.ENV_VAR, raising=False)
    monkeypatch.setenv(authority_mode.ENV_VAR, "full")
    monkeypatch.setenv(
        authority_mode.SETTINGS_FILE_ENV, str(tmp_path / "authority_mode.json")
    )
    monkeypatch.setenv(
        confirm.SETTINGS_FILE_ENV, str(tmp_path / "host_command_confirm.json")
    )
    monkeypatch.setenv("LIUHAO_DATA_DIR", str(tmp_path / "data"))
    authority_mode.reload()
    enablement.reload()
    confirm.reload()
    yield
    authority_mode.reload()
    enablement.reload()
    confirm.reload()


def _broker() -> tuple[HostCommandBroker, FakeExecutor]:
    executor = FakeExecutor()
    return (
        HostCommandBroker(approvals=ApprovalInterface(), executor=executor),
        executor,
    )


def _events(event_type: AuditEventType) -> list[dict]:
    return list(get_audit_store().query_events(event_type=event_type))


def _touches() -> list[dict]:
    """敏感路径触碰记录（承载在 POLICY_EVAL 里，用 details.event 区分）。"""
    return [
        row
        for row in _events(AuditEventType.POLICY_EVAL)
        if (row.get("details") or {}).get("event")
        == "authority.protected_path_touched"
    ]


# --------------------------------------------------------------------------- #
# 主机命令可用（不是 0 可用）
# --------------------------------------------------------------------------- #
def test_full_mode_enablement_is_on_without_env_var():
    assert enablement.is_enabled() is True
    assert enablement.describe()["authority_mode_full"] is True


def test_full_mode_allows_an_ordinary_command_without_capabilities():
    broker, executor = _broker()
    decision = broker.submit(HostCommandRequest(command="echo hello"))

    assert decision.outcome is DecisionOutcome.ALLOW
    assert [c.command for c in executor.calls] == ["echo hello"]


# --------------------------------------------------------------------------- #
# 边界 1：提权一律拒绝（与模式无关）
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "command",
    [
        "runas /user:Administrator cmd",
        "powershell Start-Process cmd -Verb RunAs",
        "sc create evil binPath= C:\\temp\\evil.exe",
        "reg add HKLM\\Software\\Evil /v X /d 1",
        "schtasks /create /tn evil /tr calc.exe /sc onlogon",
    ],
)
def test_escalation_is_denied_even_in_full_mode(command):
    broker, executor = _broker()
    decision = broker.submit(HostCommandRequest(command=command))

    assert decision.outcome is DecisionOutcome.DENY
    assert "escalation" in decision.reason.lower()
    assert executor.calls == []


# --------------------------------------------------------------------------- #
# 边界 2：敏感路径（命令层）—— 只记录，不拒绝，带归属
# --------------------------------------------------------------------------- #
def test_sensitive_path_command_is_allowed_and_audited(tmp_path):
    broker, executor = _broker()
    data_root = tmp_path / "data"
    data_root.mkdir(parents=True, exist_ok=True)
    target = data_root / "audit_store.db"

    with task_attribution(employee="emp-9", task_id="t-9", goal_id="g-9"):
        decision = broker.submit(
            HostCommandRequest(command=f'del "{target}"', actor="autonomous")
        )

    assert decision.outcome is DecisionOutcome.ALLOW
    assert [c.command for c in executor.calls] == [f'del "{target}"']

    rows = _touches()
    assert rows, "敏感路径触碰没有进审计"
    details = rows[-1]["details"]
    assert details["reason"] == "audit-or-key-material"
    assert details["channel"] == "host_command"
    assert details["operation"] == "command"
    assert details["mode"] == "full"
    assert details["would_have_blocked"] is True
    assert "audit_store.db" in details["command"]
    attribution = details["task_attribution"]
    assert attribution["principal_class"] == "SYSTEM_PRINCIPAL"
    assert attribution["employee"] == "emp-9"
    assert attribution["task_id"] == "t-9"


def test_sensitive_path_command_writes_no_record_for_human_actor(tmp_path):
    """用户本人的手动操作不写「AI 触碰」记录，命令照常执行。"""
    broker, executor = _broker()
    data_root = tmp_path / "data"
    data_root.mkdir(parents=True, exist_ok=True)
    before = len(_touches())

    decision = broker.submit(
        HostCommandRequest(
            command=f'del "{data_root / "audit_store.db"}"', actor="human"
        )
    )

    assert decision.outcome is DecisionOutcome.ALLOW
    assert len(executor.calls) == 1
    assert len(_touches()) == before


# --------------------------------------------------------------------------- #
# 边界 2（本程序写路径）：只记录，不抛异常
# --------------------------------------------------------------------------- #
def test_sensitive_write_is_allowed_and_audited(tmp_path):
    data_root = tmp_path / "data"
    data_root.mkdir(parents=True, exist_ok=True)
    target = data_root / "audit_store.db"

    reason = protected_paths.note_write(
        str(target), actor="emp-9", correlation_id="c-1"
    )

    assert reason == "audit-or-key-material"
    assert not target.exists(), "note_write 只记录，不替调用方落盘"

    touches = _touches()
    assert touches
    assert touches[-1]["details"]["channel"] == "filesystem"
    assert touches[-1]["details"]["operation"] == "write"
    assert touches[-1]["correlation_id"] == "c-1"


def test_sensitive_write_overwriting_install_exe_is_allowed():
    target = _REPO_ROOT / "dist" / "freeze-0.2.2" / "LiuHao.exe"
    assert protected_paths.note_write(str(target)) == "application-body"


def test_sensitive_write_in_standard_mode_is_also_record_only(tmp_path, monkeypatch):
    """标准模式与全权一致：只记录，不拒绝。"""
    monkeypatch.setenv(authority_mode.ENV_VAR, "standard")
    authority_mode.reload()
    data_root = tmp_path / "data"
    data_root.mkdir(parents=True, exist_ok=True)

    reason = protected_paths.note_write(
        str(data_root / "auth_secrets.json"), actor="emp-9"
    )

    assert reason == "audit-or-key-material"
    details = _touches()[-1]["details"]
    assert details["mode"] == "standard"
    assert details["would_have_blocked"] is True


def test_sensitive_write_never_raises_when_audit_fails(tmp_path, monkeypatch):
    """审计写失败也不能把「放行」翻成「拒绝」。"""
    from src.security import authority_audit

    def _boom(**_kwargs):
        raise RuntimeError("audit store down")

    monkeypatch.setattr(authority_audit, "record_protected_touched", _boom)
    target = tmp_path / "data" / "audit_store.db"
    target.parent.mkdir(parents=True, exist_ok=True)

    assert (
        protected_paths.note_write(str(target), actor="emp-9")
        == "audit-or-key-material"
    )


def test_sensitive_write_allows_user_documents(tmp_path):
    """不是一刀切：普通用户文档不产生敏感记录。"""
    target = tmp_path / "Documents" / "notes.txt"
    before = len(_touches())
    assert protected_paths.note_write(str(target)) is None

    assert len(_touches()) == before
    assert protected_paths.protected_reason(str(target)) is None


# --------------------------------------------------------------------------- #
# 边界 2：危险命令一次确认（默认开 → DEFER；关掉 → 放行但写 G2）
# --------------------------------------------------------------------------- #
def test_dangerous_command_defers_when_confirmation_enabled():
    assert confirm.is_confirmation_enabled() is True
    broker, executor = _broker()

    decision = broker.submit(HostCommandRequest(command="format c: /q"))

    assert decision.outcome is DecisionOutcome.DEFER
    assert broker.approvals.pending()  # escalated to human
    assert executor.calls == []


def test_dangerous_command_runs_when_confirmation_disabled_and_is_audited():
    confirm.set_confirmation_enabled(False, updated_by="liuhao")
    assert confirm.is_confirmation_enabled() is False

    broker, executor = _broker()
    decision = broker.submit(HostCommandRequest(command="diskpart /s wipe.txt"))

    assert decision.outcome is DecisionOutcome.ALLOW
    assert executor.calls
    assert executor.calls[0].metadata["confirm_state"] == "disabled_by_setting"

    allowed = _events(AuditEventType.AUTHORITY_IRREVERSIBLE_ALLOWED)
    assert allowed, "关闭确认后放行危险命令，但没有写 G2"
    assert allowed[-1]["details"]["confirm_state"] == "disabled_by_setting"
    assert "diskpart" in allowed[-1]["details"]["command"]

    executed = _events(AuditEventType.HOST_COMMAND_EXECUTED)
    assert executed
    assert "diskpart" in executed[-1]["details"]["command"]


def test_normal_delete_is_allowed():
    """普通文件删除不是危险命令，也不命中受保护路径。"""
    broker, executor = _broker()
    decision = broker.submit(
        HostCommandRequest(command=r"del C:\Users\Public\notes.txt")
    )

    assert decision.outcome is DecisionOutcome.ALLOW
    assert len(executor.calls) == 1


# --------------------------------------------------------------------------- #
# G3/G4：关闭/恢复确认必须留痕
# --------------------------------------------------------------------------- #
def test_settings_endpoint_records_confirm_disabled_and_restored():
    from src.gateway.policy import (
        HostCommandConfirmRequest,
        set_host_command_confirmation,
    )

    set_host_command_confirmation(
        HostCommandConfirmRequest(enabled=False, reason="测试关闭"), principal="liuhao"
    )
    assert confirm.is_confirmation_enabled() is False
    disabled = _events(AuditEventType.AUTHORITY_CONFIRM_DISABLED)
    assert len(disabled) == 1
    assert disabled[0]["principal_id"] == "liuhao"
    assert disabled[0]["details"]["reason"] == "测试关闭"

    set_host_command_confirmation(
        HostCommandConfirmRequest(enabled=True), principal="liuhao"
    )
    assert confirm.is_confirmation_enabled() is True
    restored = _events(AuditEventType.AUTHORITY_CONFIRM_RESTORED)
    assert len(restored) == 1
    assert restored[0]["principal_id"] == "liuhao"
