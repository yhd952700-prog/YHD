"""改密接口的真实 HTTP 回归（QA-003）。

`POST /v1/accounts/{principal}/password` 此前全仓零用例引用。本文件把它变成
可执行契约：

1. 管理者重置成员密码：旧密码立即失效、新密码能登录；
2. 本人自助改密：必须同时提交当前密码，成功后本人旧令牌立即失效；
3. 当前密码错误被明确拒绝（400，且不会误伤会话）；
4. 越权改别人密码与"账号不存在"返回**同形 404**；
5. 过短 / 纯空白密码在入口被拒（422）。

使用独立临时数据目录 + 真实 FastAPI 应用，不触碰真实用户数据。
"""

from __future__ import annotations

from typing import Dict

import pytest
from fastapi.testclient import TestClient

PASSWORD = "password123"
NEW_PASSWORD = "newpassword456"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    base = tmp_path
    values = {
        "LIUHAO_HUMAN_IDENTITIES_BACKEND": "file",
        "LIUHAO_HUMAN_IDENTITIES_FILE": str(base / "identities.json"),
        "LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY": "account-password-change-key",
        "LIUHAO_AUTH_SECRETS_FILE": str(base / "auth_secrets.json"),
        "LIUHAO_ACCOUNT_SETTINGS_FILE": str(base / "account_settings.json"),
        "LIUHAO_ACCOUNT_MEMORY_DB": str(base / "account_memory.sqlite3"),
        "LIUHAO_ACCOUNT_USAGE_DB": str(base / "account_usage.sqlite3"),
        "LIUHAO_ALERTS_STORE_PATH": str(base / "alerts.json"),
        "AUDIT_DB_PATH": str(base / "audit_store.db"),
        "AUDIT_DB_BACKUP_PATH": str(base / "audit_store.db.backup"),
        "LIUHAO_WORKSPACE_ROOT": str(base / "workspace"),
        "LIUHAO_MODEL_CONFIG_DB": str(base / "model_config.sqlite3"),
        "LIUHAO_MODEL_CONFIG_KEY_FILE": str(base / "model_config_key.bin"),
        "LIUHAO_JWT_REVOCATION_DB": str(base / "jwt_revocation.db"),
        "LIUHAO_DATA_DIR": str(base / "data"),
        "LIUHAO_ACCOUNT_MAX_DEPTH": "3",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)

    import src.ai.conversation_store as conversation_store
    import src.kernels.identity as identity_module
    from src.ai.model_config import reset_model_config_store
    from src.gateway.ai_management import AIStateManager
    from src.gateway.projects import ProjectStore

    monkeypatch.setattr(identity_module, "_global_manager", None)
    monkeypatch.setattr(AIStateManager, "_instance", None)
    monkeypatch.setattr(ProjectStore, "_instance", None)
    monkeypatch.setattr(
        conversation_store,
        "_conversation_store",
        conversation_store.ConversationStore(str(base / "conversation_store.db")),
    )
    reset_model_config_store()
    yield values
    reset_model_config_store()


@pytest.fixture()
def client(env) -> TestClient:
    from src.gateway.main import get_app

    with TestClient(get_app()) as test_client:
        yield test_client


def _login(client: TestClient, principal: str, password: str = PASSWORD):
    return client.post(
        "/v1/auth/login", json={"principal": principal, "secret": password}
    )


def _bearer(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _register_root(client: TestClient, principal: str = "root1") -> Dict[str, str]:
    response = client.post(
        "/v1/accounts/register",
        json={"principal": principal, "password": PASSWORD},
    )
    assert response.status_code == 200, response.text
    token = response.json().get("access_token")
    if not token:
        token = _login(client, principal).json()["access_token"]
    return _bearer(token)


def _create_child(client: TestClient, root_headers: Dict[str, str], principal: str) -> None:
    created = client.post("/v1/accounts", json={"principal": principal}, headers=root_headers)
    assert created.status_code == 200, created.text
    activated = client.post(
        "/v1/accounts/activate",
        json={
            "principal": principal,
            "code": created.json()["activation_code"],
            "password": PASSWORD,
        },
    )
    assert activated.status_code == 200, activated.text


def test_admin_reset_member_password_rotates_credential(client):
    root = _register_root(client)
    _create_child(client, root, "child1")

    changed = client.post(
        "/v1/accounts/child1/password",
        json={"password": NEW_PASSWORD},
        headers=root,
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["ok"] is True

    # 旧密码立即失效，新密码能建立会话并真实访问接口。
    assert _login(client, "child1").status_code == 401
    fresh = _login(client, "child1", NEW_PASSWORD)
    assert fresh.status_code == 200, fresh.text
    token = fresh.json()["access_token"]
    assert client.get("/v1/accounts", headers=_bearer(token)).status_code == 200


def test_self_change_requires_current_password_and_old_token_dies(client):
    root = _register_root(client)

    changed = client.post(
        "/v1/accounts/root1/password",
        json={"password": NEW_PASSWORD, "current_password": PASSWORD},
        headers=root,
    )
    assert changed.status_code == 200, changed.text
    body = changed.json()
    assert body["ok"] is True
    assert body["requires_reauth"] is True
    assert "重新登录" in body["notice"]

    # 旧令牌与旧密码都必须失效；新密码能登录。
    assert client.get("/v1/accounts", headers=root).status_code == 401
    assert _login(client, "root1").status_code == 401
    fresh = _login(client, "root1", NEW_PASSWORD)
    assert fresh.status_code == 200, fresh.text


def test_wrong_current_password_is_rejected_without_killing_session(client):
    root = _register_root(client)

    rejected = client.post(
        "/v1/accounts/root1/password",
        json={"password": NEW_PASSWORD, "current_password": "wrong-password"},
        headers=root,
    )
    assert rejected.status_code == 400, rejected.text
    assert rejected.json()["code"] == "ACCOUNT_CURRENT_PASSWORD_REJECTED"

    # 拒绝不能误伤现有会话，也不能改动凭据。
    assert client.get("/v1/accounts", headers=root).status_code == 200
    assert _login(client, "root1").status_code == 200


def test_cross_account_change_is_identical_to_missing_account(client):
    root = _register_root(client)
    _create_child(client, root, "child1")
    _create_child(client, root, "child2")
    child1 = _login(client, "child1").json()["access_token"]

    sibling = client.post(
        "/v1/accounts/child2/password",
        json={"password": NEW_PASSWORD},
        headers=_bearer(child1),
    )
    missing = client.post(
        "/v1/accounts/ghost/password",
        json={"password": NEW_PASSWORD},
        headers=_bearer(child1),
    )

    assert sibling.status_code == 404, sibling.text
    assert missing.status_code == 404, missing.text
    # 越权与不存在必须同形：不能让人用状态/文案探测账号是否存在。
    assert sibling.json() == missing.json()


def test_short_and_blank_passwords_are_rejected(client):
    root = _register_root(client)

    short = client.post(
        "/v1/accounts/root1/password",
        json={"password": "short", "current_password": PASSWORD},
        headers=root,
    )
    blank = client.post(
        "/v1/accounts/root1/password",
        json={"password": "        ", "current_password": PASSWORD},
        headers=root,
    )

    assert short.status_code == 422, short.text
    assert blank.status_code == 422, blank.text
    # 被拒的请求不能已经改掉密码。
    assert _login(client, "root1").status_code == 200


def _break_audit(monkeypatch):
    """让审计写入必然失败，但走真实失败路径（先记失败计数，再抛异常）。"""
    import src.kernels.audit as audit_mod

    def _boom(*args, **kwargs):
        audit_mod.record_audit_failure()
        raise RuntimeError("audit store is read-only / disk full (test)")

    monkeypatch.setattr(audit_mod, "log_event", _boom)


def _assert_audit_notice(payload: Dict, caplog) -> None:
    assert payload["audit_written"] is False
    assert "审计未写入" in payload["audit_warning"]
    assert any("审计未写入" in record.getMessage() for record in caplog.records)


def test_login_audit_failure_is_visible_but_still_issues_token(client, monkeypatch, caplog):
    _register_root(client)
    _break_audit(monkeypatch)

    response = _login(client, "root1")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["access_token"]
    _assert_audit_notice(payload, caplog)


def test_password_reset_audit_failure_is_visible_but_not_blocking(
    client, monkeypatch, caplog
):
    root = _register_root(client)
    _create_child(client, root, "child1")
    _break_audit(monkeypatch)

    changed = client.post(
        "/v1/accounts/child1/password",
        json={"password": NEW_PASSWORD},
        headers=root,
    )

    assert changed.status_code == 200, changed.text
    assert changed.json()["ok"] is True
    _assert_audit_notice(changed.json(), caplog)
    # 操作未被阻断：新密码确实生效。
    assert _login(client, "child1", NEW_PASSWORD).status_code == 200


def test_create_member_audit_failure_is_visible_but_not_blocking(
    client, monkeypatch, caplog
):
    root = _register_root(client)
    _break_audit(monkeypatch)

    created = client.post(
        "/v1/accounts", json={"principal": "child2"}, headers=root
    )

    assert created.status_code == 200, created.text
    assert created.json()["activation_code"]
    _assert_audit_notice(created.json(), caplog)


def test_delete_account_audit_failure_is_visible_but_not_blocking(
    client, monkeypatch, caplog
):
    root = _register_root(client)
    _create_child(client, root, "child1")
    _break_audit(monkeypatch)

    deleted = client.patch(
        "/v1/accounts/child1/status", json={"status": "deleted"}, headers=root
    )

    assert deleted.status_code == 200, deleted.text
    _assert_audit_notice(deleted.json(), caplog)
