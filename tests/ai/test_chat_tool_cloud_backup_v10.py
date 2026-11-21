"""V10 云备份快照的聊天通路（精而简审计）：一句话能查、能备、能恢复。

直接调用工具函数（无模型 Key 可测层）：无口令时先要口令、恢复/删除
无确认不开工、带口令真做得出快照；分档登记为 B 档默认关，与
backup_now 同口径。分工在工具描述里写死：同机回滚用 backup_now，
云备份快照是口令加密可带走的那份。
"""

from __future__ import annotations

import json

import pytest

from src.ai.tools.kernel_tools import make_tools

PASSPHRASE = "cloud-backup-pass-123"


@pytest.fixture()
def _env(monkeypatch, tmp_path):
    import src.ai.conversation_store as conversation_module
    import src.kernels.identity as identity_module

    base = tmp_path / "inst"
    base.mkdir(parents=True, exist_ok=True)
    values = {
        "LIUHAO_HUMAN_IDENTITIES_BACKEND": "file",
        "LIUHAO_HUMAN_IDENTITIES_FILE": str(base / "identities.json"),
        "LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY": "chat-integrity-key",
        "LIUHAO_AUTH_SECRETS_FILE": str(base / "auth_secrets.json"),
        "LIUHAO_ACCOUNT_SETTINGS_FILE": str(base / "account_settings.json"),
        "LIUHAO_ACCOUNT_BACKUP_DB": str(base / "account_backup.sqlite3"),
        "LIUHAO_ACCOUNT_MEMORY_DB": str(base / "account_memory.sqlite3"),
        "LIUHAO_ACCOUNT_USAGE_DB": str(base / "account_usage.sqlite3"),
        "CONVERSATION_DB_PATH": str(base / "conversation_store.db"),
        "LIUHAO_WORKSPACE_ROOT": str(base / "workspace"),
        "LIUHAO_DATA_DIR": str(base),
        "LIUHAO_MODEL_CONFIG_DB": str(base / "model_config.sqlite3"),
        "LIUHAO_MODEL_CONFIG_KEY_FILE": str(base / "model_config_key.bin"),
        "MEMORY_DB_PATH": str(base / "kernel_memory.db"),
        "PERSONAL_CONTEXT_DB_PATH": str(base / "personal_context.db"),
        "LIUHAO_KNOWLEDGE_DB": str(base / "knowledge_vectors.sqlite3"),
        "LIUHAO_WORKBENCH_DB": str(base / "workbench.sqlite3"),
        "LIUHAO_KNOWLEDGE_ENTERPRISE_DB": str(base / "knowledge_enterprise.sqlite3"),
        "LIUHAO_KNOWLEDGE_MEMORY_DB": str(base / "knowledge_memory.sqlite3"),
        "LIUHAO_KNOWLEDGE_FILES_DIR": str(base / "knowledge_files"),
        "LIUHAO_CLOUD_BACKUP_DIR": str(tmp_path / "cloud-backups"),
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(identity_module, "_global_manager", None)
    monkeypatch.setattr(
        conversation_module,
        "_conversation_store",
        conversation_module.ConversationStore(values["CONVERSATION_DB_PATH"]),
    )

    from src.kernels.identity import get_identity_manager

    get_identity_manager().register_root_account("root1", permissions={"*"})
    return values


@pytest.fixture()
def tool(_env):
    built = {t.name: t for t in make_tools("user-root1", include_disabled=True)}
    return built["cloud_backup"]


def _call(tool, **kwargs):
    return json.loads(tool.fn(**kwargs))


def test_grade_registered_default_off():
    from src.gateway.chat_tool_settings import grade_for, known_tool_ids

    assert "liuhao.backup.cloud" in known_tool_ids()
    grade = grade_for("liuhao.backup.cloud")
    assert grade["grade"] == "B"
    assert grade["default_enabled"] is False


def test_create_needs_passphrase_then_works(tool):
    out = _call(tool, action="create")
    assert out["status"] == "needs_passphrase"

    out = _call(tool, action="create", passphrase=PASSPHRASE, label="聊天里做的")
    assert out["status"] == "ok"
    assert out["snapshot_id"].startswith("snap-")

    out = _call(tool, action="status")
    assert out["status"] == "ok"
    assert out["count"] == 1
    assert out["cloud_upload"]["enabled"] is False
    assert out["snapshots"][0]["label"] == "聊天里做的"

    out = _call(tool, action="list")
    assert len(out["snapshots"]) == 1
    assert out["retention"] == 5


def test_restore_and_delete_need_confirm(tool):
    created = _call(tool, action="create", passphrase=PASSPHRASE)
    snapshot_id = created["snapshot_id"]

    out = _call(tool, action="restore", snapshot_id=snapshot_id, passphrase=PASSPHRASE)
    assert out["status"] == "refused"

    out = _call(
        tool, action="restore", snapshot_id=snapshot_id,
        passphrase=PASSPHRASE, confirm=True,
    )
    assert out["status"] == "ok"
    assert out["restore_mode"] == "existing_account_merge"

    out = _call(tool, action="delete", snapshot_id=snapshot_id)
    assert out["status"] == "refused"
    out = _call(tool, action="delete", snapshot_id=snapshot_id, confirm=True)
    assert out["status"] == "ok"
    assert _call(tool, action="status")["count"] == 0


def test_set_retention_via_chat(tool):
    out = _call(tool, action="set_retention", retention=3)
    assert out["status"] == "ok"
    assert out["retention"] == 3
    assert _call(tool, action="status")["retention"] == 3


def test_unknown_action_fails_honestly(tool):
    assert _call(tool, action="nope")["status"] == "failed"
