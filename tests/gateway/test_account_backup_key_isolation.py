"""账号备份的 Key 边界：成员 Key 随包，但只以口令保险箱密文。

安全口径：
1. 根账号全量导出带整棵子树的 Key 行，供换机后成员开箱即用；
2. 包内没有任何明文 API Key：Key 清单由「口令派生 KEK + 随机 DEK」包裹；
3. 模型主密钥（能解开所有 ``api_key_enc``）不出机，导入端用本机主密钥重加密；
4. 成员 ``scope=self`` 仍只带本人；导出默认只接受本机来源；
5. 导出与拒绝都写审计，回执字段如实。
"""

from __future__ import annotations

import json
import logging

import pytest
from fastapi.testclient import TestClient

PASSPHRASE = "backup-passphrase-123"
PASSWORD = "initpass123"
ROOT_KEY = "root-super-secret-key-0001"
MEMBER_KEY = "member-super-secret-key-0002"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    base = tmp_path
    values = {
        "LIUHAO_HUMAN_IDENTITIES_BACKEND": "file",
        "LIUHAO_HUMAN_IDENTITIES_FILE": str(base / "identities.json"),
        "LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY": "backup-key-isolation-test-key",
        "LIUHAO_AUTH_SECRETS_FILE": str(base / "auth_secrets.json"),
        "LIUHAO_ACCOUNT_SETTINGS_FILE": str(base / "account_settings.json"),
        "LIUHAO_ACCOUNT_BACKUP_DB": str(base / "account_backup.sqlite3"),
        "LIUHAO_ACCOUNT_MEMORY_DB": str(base / "account_memory.sqlite3"),
        "LIUHAO_ALERTS_STORE_PATH": str(base / "alerts.json"),
        "AUDIT_DB_PATH": str(base / "audit_store.db"),
        "AUDIT_DB_BACKUP_PATH": str(base / "audit_store.db.backup"),
        "LIUHAO_WORKSPACE_ROOT": str(base / "workspace"),
        "LIUHAO_MODEL_CONFIG_DB": str(base / "model_config.sqlite3"),
        "LIUHAO_MODEL_CONFIG_KEY_FILE": str(base / "model_config_key.bin"),
        "LIUHAO_JWT_REVOCATION_DB": str(base / "jwt_revocation.db"),
        "LIUHAO_DATA_DIR": str(base / "data"),
        "LIUHAO_ALLOW_REMOTE_BACKUP_EXPORT": "0",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    yield values


def _seed_root_and_member():
    from src.ai.model_config import ModelConfigStore
    from src.kernels.identity import get_identity_manager

    manager = get_identity_manager()
    manager.register_root_account("root1", permissions={"*"})
    manager.register_self_service_account("member1")

    store = ModelConfigStore()
    store.create(
        {"name": "root-key", "provider": "mock", "model": "mock-1", "api_key": ROOT_KEY},
        actor="root1",
        owner_principal="root1",
    )
    store.create(
        {"name": "member-key", "provider": "mock", "model": "mock-2", "api_key": MEMBER_KEY},
        actor="member1",
        owner_principal="member1",
    )
    return manager, store


def test_scope_all_includes_member_keys_only_in_vault(env, monkeypatch):
    """解包后 grep：成员 Key 只以不再可读的密文出现，主密钥为空。"""
    from src.gateway import account_backup
    from src.gateway.account_backup import export_bundle

    manager, store = _seed_root_and_member()
    # 主密钥确实存在（否则"没带主密钥"可能只是因为它本来就没有）。
    assert store.export_key_material()
    # 真实落两份凭据哈希：换机后根与成员都要能用原密码登录。
    from src.gateway.auth import rotate_credential

    assert rotate_credential("root1", PASSWORD) is True
    assert rotate_credential("member1", "memberpass123") is True

    audited = []
    original = manager.record_event

    def capture(**kwargs):
        audited.append(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(manager, "record_event", capture)

    exported = export_bundle(
        actor="root1",
        scope="all",
        passphrase=PASSPHRASE,
        subtree=manager.subtree_principals("root1"),
        is_root=True,
    )

    # 解包（用户口令）后才是真正的"包内内容"；grep 密文包没有意义。
    payload = account_backup._backup.decrypt_bundle(  # noqa: SLF001
        exported["bundle"], PASSPHRASE
    )
    text = json.dumps(payload, ensure_ascii=False)
    assert MEMBER_KEY not in text
    assert ROOT_KEY not in text
    # 凭据记录（PBKDF2 哈希，非明文）必须随包走：否则导入成功的新机没人
    # 能用原密码登录。口令明文与 API Key 明文均不可出现在包内。
    assert set(payload["credentials"]) == {"root1", "member1"}
    assert payload["credentials"]["root1"]["algo"] == "pbkdf2_hmac_sha256"
    assert PASSWORD not in text
    assert payload["models"]["key_material"] is None
    owners = {str(row.get("owner_principal") or "") for row in payload["models"]["rows"]}
    assert owners == {"root1", "member1"}
    assert len(payload["models"]["rows"]) == 2

    # 成员 Key 只存在于口令保险箱里：同一口令可解出，错口令解不开。
    vault = payload["models"]["key_vault"]
    ids = {
        str(row.get("name")): str(row.get("id"))
        for row in payload["models"]["rows"]
    }
    assert account_backup._open_model_secrets(  # noqa: SLF001
        vault, PASSPHRASE
    ) == {ids["root-key"]: ROOT_KEY, ids["member-key"]: MEMBER_KEY}
    assert account_backup._open_model_secrets(  # noqa: SLF001
        vault, "wrong-passphrase"
    ) == {}

    # 回执字段必须与包内事实一致。
    assert exported["manifest"]["key_scope"] == "subtree"
    assert exported["manifest"]["member_keys_included"] is True
    assert exported["manifest"]["key_material_included"] is False
    assert exported["member_keys_included"] is True
    assert exported["key_material_included"] is False
    assert "成员 Key 可直接使用" in exported["notice"]

    # 审计：谁、何时、范围、是否含密钥材料。
    entry = [item for item in audited if item.get("action") == "account.backup.export"][0]
    assert entry["actor"] == "root1"
    assert entry["details"]["scope"] == "all"
    assert entry["details"]["issued_at"]
    assert entry["details"]["key_material_included"] is False
    assert entry["details"]["member_keys_included"] is True


def test_export_rejects_non_loopback_then_allows_with_switch(env, monkeypatch, caplog):
    """非本机导出默认 403；显式开关后放行且审计标 remote。"""
    from src.gateway.auth import rotate_credential
    from src.gateway.main import get_app
    from src.kernels.identity import get_identity_manager

    manager = get_identity_manager()
    manager.register_root_account("root1", permissions={"*"})
    assert rotate_credential("root1", PASSWORD) is True

    audited = []
    original = manager.record_event

    def capture(**kwargs):
        audited.append(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(manager, "record_event", capture)

    with TestClient(get_app(), client=("203.0.113.9", 5555)) as client:
        login = client.post(
            "/v1/auth/login", json={"principal": "root1", "secret": PASSWORD}
        )
        assert login.status_code == 200, login.text
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        denied = client.post(
            "/v1/accounts/backup/export",
            json={"scope": "all", "passphrase": PASSPHRASE},
            headers=headers,
        )
        assert denied.status_code == 403, denied.text
        assert denied.json()["code"] == "ACCOUNT_BACKUP_EXPORT_LOCAL_ONLY"
        denied_audit = [
            item for item in audited
            if item.get("action") == "account.backup.export"
            and item.get("outcome") == "denied"
        ]
        assert denied_audit and denied_audit[0]["details"]["remote_source"] is True

        monkeypatch.setenv("LIUHAO_ALLOW_REMOTE_BACKUP_EXPORT", "1")
        allowed = client.post(
            "/v1/accounts/backup/export",
            json={"scope": "all", "passphrase": PASSPHRASE},
            headers=headers,
        )
        assert allowed.status_code == 200, allowed.text
        body = allowed.json()
        assert body["manifest"]["key_scope"] == "subtree"
        assert body["manifest"]["member_keys_included"] is False
        assert body["key_material_included"] is False
        assert "模型主密钥不随包离机" in body["notice"]
        allowed_audit = [
            item for item in audited
            if item.get("action") == "account.backup.export"
            and item.get("outcome") == "allowed"
        ]
        assert allowed_audit[0]["details"]["remote_source"] is True
        assert allowed_audit[0]["details"]["key_material_included"] is False

    # 启动日志：开关打开时必须明确记录"已允许非本机备份导出"。
    caplog.clear()
    caplog.set_level(logging.WARNING, logger="src.gateway.main")
    with TestClient(get_app()) as client:
        assert client.get("/v1/health").status_code == 200
    assert "已允许非本机备份导出" in caplog.text
