"""G1–G5 独立埋点回归：审批不生效时，这些记录仍然必须查得到。

这组测试存在的理由（也是以后**不许被改松**的地方）：

* 审批（主权凭据）被关掉后，``HUMAN_SOVEREIGNTY_OVERRIDE`` 不再产生。若
  高风险动作的证据只挂在审批上，关掉审批就等于把证据链剪断。
* 因此 G1–G5 必须由「配置变更本身」或「动作本身」触发，而不是审批顺带写。
  下面每条都直接断言审计库里**有这一行**，不依赖任何审批状态。
"""

from __future__ import annotations

import pytest

from src.kernels._crosscutting import kernel_action
from src.kernels.audit import AuditEventType, get_audit_store
from src.security import authority_audit, authority_mode
from src.security.authority_audit import redact_command
from src.security.authority_mode import ENV_VAR


@pytest.fixture(autouse=True)
def _fresh_mode_snapshot():
    """每个用例都从干净的模式快照开始，避免环境变量串味。"""
    authority_mode.reload()
    yield
    authority_mode.reload()


def _rows(event_type: AuditEventType):
    return get_audit_store().query_events(event_type=event_type)


def test_g1_mode_changed_is_recorded():
    authority_audit.record_mode_changed(
        previous="standard",
        current="full",
        actor="liuhao",
        source="settings",
        reason="user enabled full authority",
    )

    rows = _rows(AuditEventType.AUTHORITY_MODE_CHANGED)
    assert len(rows) == 1
    details = rows[0]["details"]
    assert details["previous_mode"] == "standard"
    assert details["current_mode"] == "full"
    assert details["source_channel"] == "settings"
    assert rows[0]["principal_id"] == "liuhao"


def test_g2_only_in_full_mode_and_only_for_irreversible():
    @kernel_action("memory.delete_by_key_prefix")
    def purge(prefix: str) -> str:
        return f"purged:{prefix}"

    # 标准模式：即使动作跑了也不该写 G2（没有「全权放行」这回事）。
    purge("std")
    assert _rows(AuditEventType.AUTHORITY_IRREVERSIBLE_ALLOWED) == []

    # 完全授权模式：不可逆动作被放行 → 必须有一行。
    authority_mode.reload()
    import os

    os.environ[ENV_VAR] = "full"
    authority_mode.reload()
    try:
        purge("full")
    finally:
        del os.environ[ENV_VAR]
        authority_mode.reload()

    rows = _rows(AuditEventType.AUTHORITY_IRREVERSIBLE_ALLOWED)
    assert len(rows) == 1
    details = rows[0]["details"]
    assert details["action"] == "memory.delete_by_key_prefix"
    assert details["risk_tier"] == "HIGH"
    assert details["mode"] == "full"
    assert details["confirm_state"] == "no_confirmation_channel"


def test_g2_skips_non_irreversible_actions():
    """LOW 动作即使在全权模式下也不该产出「不可逆放行」记录。"""

    @kernel_action("policy.evaluate")
    def evaluate(payload: str) -> str:
        return payload

    import os

    os.environ[ENV_VAR] = "full"
    authority_mode.reload()
    try:
        evaluate("x")
    finally:
        del os.environ[ENV_VAR]
        authority_mode.reload()

    assert _rows(AuditEventType.AUTHORITY_IRREVERSIBLE_ALLOWED) == []


def test_g3_g4_confirm_toggle_is_recorded():
    authority_audit.record_confirm_disabled(
        categories=["bulk_delete", "command"], actor="liuhao", source="settings"
    )
    authority_audit.record_confirm_restored(
        categories=["bulk_delete", "command"], actor="liuhao", source="settings"
    )

    disabled = _rows(AuditEventType.AUTHORITY_CONFIRM_DISABLED)
    restored = _rows(AuditEventType.AUTHORITY_CONFIRM_RESTORED)
    assert len(disabled) == 1
    assert len(restored) == 1
    assert disabled[0]["details"]["categories"] == ["bulk_delete", "command"]
    assert disabled[0]["principal_id"] == "liuhao"


def test_g5_host_command_keeps_full_command_line_redacted():
    authority_audit.record_host_command(
        command='powershell -NoProfile -Command "Get-ChildItem -Recurse" --token=abcd1234efgh',
        cwd="D:\\LiuHao-Workspace",
        exit_code=0,
        duration_ms=12.5,
        actor="liuhao",
        dangerous=False,
    )

    rows = _rows(AuditEventType.HOST_COMMAND_EXECUTED)
    assert len(rows) == 1
    details = rows[0]["details"]
    # 参数必须完整保留（审计要求），只把凭据值打码。
    assert "Get-ChildItem -Recurse" in details["command"]
    assert "abcd1234efgh" not in details["command"]
    assert "***" in details["command"]
    assert details["exit_code"] == 0
    assert details["cwd"] == "D:\\LiuHao-Workspace"


def test_redact_keeps_plain_arguments():
    text = "copy a.txt b.txt --verbose"
    assert redact_command(text) == text
