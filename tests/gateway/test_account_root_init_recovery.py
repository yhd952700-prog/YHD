"""首启初始化与恢复码顺序的回归测试（P0-1 / P1-6）。

两条真实缺陷：

* **P0-1**：``/v1/accounts/register`` 旧实现先落身份、后写凭据。凭据写失败时
  接口 500，但根已经落盘 → ``/v1/auth/config`` 报"已初始化"、再注册 409、
  登录 401，用户既进不去也重开不了。
* **P1-6**：``/recovery/reset`` 旧实现先消费恢复码、后改密。改密失败时恢复码
  已作废且不能重发，用户彻底锁死。

这里全部走真实 HTTP（TestClient），并在**同一进程实例内**验证失败后可以原样
重试成功。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

PASSWORD = "initpass123"
NEW_PASSWORD = "resetpass456"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    base = tmp_path
    values = {
        "LIUHAO_HUMAN_IDENTITIES_BACKEND": "file",
        "LIUHAO_HUMAN_IDENTITIES_FILE": str(base / "identities.json"),
        "LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY": "root-init-test-key",
        "LIUHAO_AUTH_SECRETS_FILE": str(base / "auth_secrets.json"),
        "LIUHAO_ACCOUNT_SETTINGS_FILE": str(base / "account_settings.json"),
        "LIUHAO_ALERTS_STORE_PATH": str(base / "alerts.json"),
        "AUDIT_DB_PATH": str(base / "audit_store.db"),
        "AUDIT_DB_BACKUP_PATH": str(base / "audit_store.db.backup"),
        "LIUHAO_WORKSPACE_ROOT": str(base / "workspace"),
        "LIUHAO_MODEL_CONFIG_DB": str(base / "model_config.sqlite3"),
        "LIUHAO_MODEL_CONFIG_KEY_FILE": str(base / "model_config_key.bin"),
        "LIUHAO_JWT_REVOCATION_DB": str(base / "jwt_revocation.db"),
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    yield values


def _register(client: TestClient, principal: str = "alice", password: str = PASSWORD):
    return client.post(
        "/v1/accounts/register",
        json={"principal": principal, "password": password},
    )


def _login(client: TestClient, principal: str, password: str):
    return client.post(
        "/v1/auth/login", json={"principal": principal, "secret": password}
    )


def test_failed_credential_write_persists_nothing_and_retry_completes(env, monkeypatch):
    """凭据写失败 → 500 → 仓库一个字节都没多 → 同一实例重试成功。"""
    from src.gateway import accounts as accounts_module
    from src.gateway.main import get_app
    from src.kernels.identity import get_identity_manager

    real_rotate = accounts_module.rotate_credential

    def broken(principal: str, secret: str) -> bool:
        raise RuntimeError("模拟凭据落盘失败")

    monkeypatch.setattr(accounts_module, "rotate_credential", broken)

    with TestClient(get_app()) as client:
        response = _register(client)
        assert response.status_code == 500, response.text
        assert response.json()["code"] == "ACCOUNT_CREDENTIAL_WRITE_FAILED"

        # 半套初始化必须**不存在**：账号树空、身份库文件未创建、凭据库未创建。
        manager = get_identity_manager()
        assert manager.account_root() is None
        assert manager.get_identity_by_principal("alice") is None
        assert not Path(env["LIUHAO_HUMAN_IDENTITIES_FILE"]).exists()
        assert not Path(env["LIUHAO_AUTH_SECRETS_FILE"]).exists()

        # /v1/auth/config 必须仍允许初始化（旧实现这里会报已初始化）。
        config = client.get("/v1/auth/config").json()
        assert config["registration_open"] is True
        assert config["root_account_exists"] is False
        assert config["initialization"] == {
            "state": "pending",
            "recoverable": False,
            "principal": None,
        }

        # 修复故障后，同一个请求在同一个实例上原样重试成功。
        monkeypatch.setattr(accounts_module, "rotate_credential", real_rotate)
        retried = _register(client)
        assert retried.status_code == 200, retried.text
        assert retried.json()["recovery_code"]
        assert get_identity_manager() is manager, "必须仍是同一个实例"

        login = _login(client, "alice", PASSWORD)
        assert login.status_code == 200, login.text
        config = client.get("/v1/auth/config").json()
        assert config["registration_open"] is False
        assert config["initialization"]["state"] == "complete"


def test_legacy_half_initialized_root_is_recoverable(env):
    """旧版本留下的"根已落盘、凭据没落盘"必须能补全，而不是 409/401 锁死。"""
    from src.gateway.main import get_app
    from src.kernels.identity import get_identity_manager

    # 复现旧实现留下的现场：只建根（含恢复码），不写凭据。
    manager = get_identity_manager()
    manager.register_root_account("alice", display_name="Alice")

    with TestClient(get_app()) as client:
        config = client.get("/v1/auth/config").json()
        assert config["root_account_exists"] is True
        assert config["registration_open"] is True
        assert config["initialization"]["state"] == "incomplete"
        assert config["initialization"]["recoverable"] is True
        assert config["initialization"]["principal"] == "alice"
        assert "初始化未完成" in config["message"]

        # 用别的名字提交不会悄悄接管该账号，而是明确告诉该用哪个名称。
        wrong = _register(client, principal="bobby")
        assert wrong.status_code == 409, wrong.text
        assert wrong.json()["code"] == "ACCOUNT_INITIALIZATION_INCOMPLETE"
        assert wrong.json()["principal"] == "alice"

        # 用同一个名称补全初始化：拿到新恢复码、可以登录。
        resumed = _register(client)
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["resumed"] is True
        assert resumed.json()["recovery_code"]

        assert _login(client, "alice", PASSWORD).status_code == 200
        assert _login(client, "alice", "wrongpass123").status_code == 401

        # 补全之后初始化入口重新关闭（不得变成永久可重复注册）。
        assert client.get("/v1/auth/config").json()["registration_open"] is False
        again = _register(client)
        assert again.status_code == 409, again.text
        assert again.json()["code"] == "ACCOUNT_ROOT_REGISTRATION_CLOSED"


def test_recovery_code_survives_failed_password_rotation(env, monkeypatch):
    """改密失败时恢复码必须仍然有效；改密成功后才作废（P1-6）。"""
    from src.gateway import accounts as accounts_module
    from src.gateway.main import get_app
    from src.kernels.identity import get_identity_manager

    real_rotate = accounts_module.rotate_credential

    with TestClient(get_app()) as client:
        registered = _register(client)
        assert registered.status_code == 200, registered.text
        recovery_code = registered.json()["recovery_code"]

        def broken(principal: str, secret: str) -> bool:
            raise RuntimeError("模拟改密落盘失败")

        monkeypatch.setattr(accounts_module, "rotate_credential", broken)
        failed = client.post(
            "/v1/accounts/recovery/reset",
            json={"recovery_code": recovery_code, "password": NEW_PASSWORD},
        )
        assert failed.status_code == 500, failed.text
        assert failed.json()["code"] == "ACCOUNT_CREDENTIAL_WRITE_FAILED"

        # 恢复码没有被消费：哈希仍在库里，旧密码仍然有效。
        manager = get_identity_manager()
        assert manager.account_root().recovery_hash
        assert _login(client, "alice", PASSWORD).status_code == 200

        # 恢复写入后，同一个恢复码仍然可以完成改密。
        monkeypatch.setattr(accounts_module, "rotate_credential", real_rotate)
        recovered = client.post(
            "/v1/accounts/recovery/reset",
            json={"recovery_code": recovery_code, "password": NEW_PASSWORD},
        )
        assert recovered.status_code == 200, recovered.text
        assert _login(client, "alice", NEW_PASSWORD).status_code == 200
        assert _login(client, "alice", PASSWORD).status_code == 401

        # 成功之后才作废：同一恢复码第二次使用必须被拒绝。
        assert manager.account_root().recovery_hash == ""
        reused = client.post(
            "/v1/accounts/recovery/reset",
            json={"recovery_code": recovery_code, "password": "thirdpass789"},
        )
        # 文档 §4.3：恢复码无效是 400 + ACCOUNT_RECOVERY_CODE_INVALID（不再是
        # 旧实现的 401 + RECOVERY_CODE_INVALID）。
        assert reused.status_code == 400, reused.text
        assert reused.json()["code"] == "ACCOUNT_RECOVERY_CODE_INVALID"
