"""全权模式下主机命令的边界（第 3 步）回归钉。

2026-11-11 老板定的边界，后续不许被改松：

* **提权一律拒绝**：全权也不提权（不碰 UAC / 服务 / 计划任务 / HKLM）。
* **受保护路径**：审计库、密钥、程序本体、模式配置对 AI 任务只读；命令里点名即
  拒绝，拒绝要进审计并带员工/任务归属。用户本人（actor="human"）不受此限制。
* **危险命令一次确认**：默认开；设置里可关，关闭进审计（G3），恢复也留痕（G4）；
  关闭后执行仍必须写 G2（不可逆动作放行）。
* **主机命令可用**：全权模式下不是 0 可用，普通命令直接放行。

边界必须如实：路径保护在「本程序写路径」上是真拦截；在 host_command 上只是命令
字符串层扫描，拼接/编码可绕过。这里不假装它是文件系统级边界。
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
# 边界 2：受保护路径（命令层）+ 拒绝进审计，带归属
# --------------------------------------------------------------------------- #
def test_protected_path_command_is_denied_and_audited(tmp_path):
    broker, executor = _broker()
    data_root = tmp_path / "data"
    data_root.mkdir(parents=True, exist_ok=True)
    target = data_root / "audit_store.db"

    with task_attribution(employee="emp-9", task_id="t-9", goal_id="g-9"):
        decision = broker.submit(
            HostCommandRequest(command=f'del "{target}"', actor="autonomous")
        )

    assert decision.outcome is DecisionOutcome.DENY
    assert "protected path" in decision.reason
    assert executor.calls == []

    rows = _events(AuditEventType.AUTHORITY_PROTECTED_PATH_DENIED)
    assert rows, "受保护路径拒绝没有进审计"
    details = rows[-1]["details"]
    assert details["reason"] == "audit-or-key-material"
    assert details["channel"] == "host_command"
    assert "audit_store.db" in details["command"]
    attribution = details["task_attribution"]
    assert attribution["principal_class"] == "SYSTEM_PRINCIPAL"
    assert attribution["employee"] == "emp-9"
    assert attribution["task_id"] == "t-9"


def test_protected_path_command_is_allowed_for_human_actor(tmp_path):
    """用户本人手动操作不受这条限制（主体由调用方标注为 human）。"""
    broker, executor = _broker()
    data_root = tmp_path / "data"
    data_root.mkdir(parents=True, exist_ok=True)

    decision = broker.submit(
        HostCommandRequest(
            command=f'del "{data_root / "audit_store.db"}"', actor="human"
        )
    )

    assert decision.outcome is DecisionOutcome.ALLOW
    assert len(executor.calls) == 1


# --------------------------------------------------------------------------- #
# 边界 2（本程序写路径）：真拦截
# --------------------------------------------------------------------------- #
def test_guard_write_blocks_audit_db_and_records(tmp_path):
    data_root = tmp_path / "data"
    data_root.mkdir(parents=True, exist_ok=True)
    target = data_root / "audit_store.db"

    with pytest.raises(protected_paths.ProtectedPathError):
        protected_paths.guard_write(str(target), actor="emp-9", correlation_id="c-1")

    rows = _events(AuditEventType.AUTHORITY_PROTECTED_PATH_DENIED)
    assert rows
    assert rows[-1]["details"]["channel"] == "filesystem"
    assert rows[-1]["correlation_id"] == "c-1"


def test_guard_write_blocks_overwriting_install_exe():
    target = _REPO_ROOT / "dist" / "freeze-0.2.2" / "LiuHao.exe"
    with pytest.raises(protected_paths.ProtectedPathError):
        protected_paths.guard_write(str(target))


def test_guard_write_allows_user_documents(tmp_path):
    """不是一刀切：普通用户文档的写入/删除不受这条规则影响。"""
    target = tmp_path / "Documents" / "notes.txt"
    protected_paths.guard_write(str(target))  # 不抛异常即为允许

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
