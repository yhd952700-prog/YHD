"""登录页自助注册账号的恢复码闭环（0.1.2.2 缺陷回归）。

现场：从登录页「创建账户」建号的账号（``source == "self_service"``）既没有
管理员可代发激活码，``/v1/accounts/self-register`` 又曾只返回 token —— 换机
或忘记密码后永久进不去，且界面上没有任何"去设置恢复码"的提示。

这里用真实 HTTP（TestClient）覆盖三件事：

1. 建号响应当场给一次恢复码，库里只留 sha256，明文不出现在任何落盘文件；
2. 用户关掉横幅后仍可在「我的 → 恢复码」补发一次，并且只能重置**本账号**
   的密码，根账号的恢复码与密码不受影响；
3. 明文恢复码不出现在日志里。
"""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

ROOT_PASSWORD = "initpass123"
MEMBER_PASSWORD = "selfpass1234"
NEW_PASSWORD = "selfreset456"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    base = tmp_path
    values = {
        "LIUHAO_HUMAN_IDENTITIES_BACKEND": "file",
        "LIUHAO_HUMAN_IDENTITIES_FILE": str(base / "identities.json"),
        "LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY": "self-register-recovery-test-key",
        "LIUHAO_AUTH_SECRETS_FILE": str(base / "auth_secrets.json"),
        "LIUHAO_ACCOUNT_SETTINGS_FILE": str(base / "account_settings.json"),
        "LIUHAO_ALERTS_STORE_PATH": str(base / "alerts.json"),
        "AUDIT_DB_PATH": str(base / "audit_store.db"),
        "AUDIT_DB_BACKUP_PATH": str(base / "audit_store.db.backup"),
        "LIUHAO_WORKSPACE_ROOT": str(base / "workspace"),
        "LIUHAO_MODEL_CONFIG_DB": str(base / "model_config.sqlite3"),
        "LIUHAO_MODEL_CONFIG_KEY_FILE": str(base / "model_config_key.bin"),
        "LIUHAO_JWT_REVOCATION_DB": str(base / "jwt_revocation.db"),
        "LIUHAO_DATA_DIR": str(base / "data"),
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


@pytest.fixture(autouse=True)
def _reset_recovery_limits():
    """恢复码限次器是模块级状态，测前测后都清掉，避免互相污染。"""
    from src.kernels.identity import _accounts

    for key in ("recover:root", "recovery:local"):
        _accounts.limiter.reset(key)
    yield
    for key in ("recover:root", "recovery:local"):
        _accounts.limiter.reset(key)


def _register_root(client: TestClient, principal: str = "alice") -> dict:
    response = client.post(
        "/v1/accounts/register",
        json={"principal": principal, "password": ROOT_PASSWORD},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _root_token(client: TestClient, principal: str = "alice") -> str:
    login = _login(client, principal, ROOT_PASSWORD)
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


def _self_register(client: TestClient, principal: str = "carol"):
    return client.post(
        "/v1/accounts/self-register",
        json={"principal": principal, "password": MEMBER_PASSWORD},
    )


def _login(client: TestClient, principal: str, password: str):
    return client.post(
        "/v1/auth/login", json={"principal": principal, "secret": password}
    )


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _reset_headers() -> dict:
    """桌面端一次性证明的测试替身：直接向令牌库申请（等价于管道发放）。"""
    from src.gateway import local_proof

    return {
        "X-LiuHao-Reset-Proof": local_proof.issue_token(scope=local_proof.SCOPE_RESET)
    }


def _reset_password(client: TestClient, code: str, password: str):
    return client.post(
        "/v1/accounts/recovery/reset",
        headers=_reset_headers(),
        json={"recovery_code": code, "password": password},
    )


def test_self_register_returns_one_time_recovery_code_stored_only_as_hash(
    env, tmp_path, caplog
):
    """建号即给一次恢复码；库里只留 hash，明文不落盘、不进日志。"""
    from src.gateway.main import get_app
    from src.kernels.identity import _accounts, get_identity_manager

    with TestClient(get_app()) as client:
        _register_root(client)
        manager = get_identity_manager()
        root_hash_before = manager.account_root().recovery_hash
        assert root_hash_before

        with caplog.at_level(logging.DEBUG):
            response = _self_register(client)

        assert response.status_code == 200, response.text
        body = response.json()
        code = body["recovery_code"]
        assert code.startswith("RC-")
        assert "只显示这一次" in body["notice"]
        assert "换机" in body["notice"]

        node = manager.get_account("carol")
        assert node is not None
        # 只落 sha256：库里存的是哈希，不是明文。
        assert node.recovery_hash == _accounts.hash_code(code)
        assert node.recovery_hash != code
        # 自助注册不改变根的恢复码。
        assert manager.account_root().recovery_hash == root_hash_before

        # 明文不落盘：整个测试数据目录（身份、审计、密钥……）都搜不到。
        leaked = [
            str(path)
            for path in tmp_path.rglob("*")
            if path.is_file() and code.encode() in path.read_bytes()
        ]
        assert leaked == [], f"恢复码明文出现在落盘文件中: {leaked}"

        # 明文不进日志。
        assert code not in caplog.text


def test_self_registered_account_can_reissue_and_reset_own_password(env, caplog):
    """关掉横幅后仍可补发一次；新码只重置本账号，根账号不受影响。"""
    from src.gateway.main import get_app
    from src.kernels.identity import get_identity_manager

    with TestClient(get_app()) as client:
        _register_root(client)
        manager = get_identity_manager()
        root_hash_before = manager.account_root().recovery_hash

        registered = _self_register(client)
        assert registered.status_code == 200, registered.text
        first_code = registered.json()["recovery_code"]
        token = registered.json()["token"]

        # 界面能力位：自助注册账号能自己补发，但不是根。
        me = client.get("/v1/auth/me", headers=_bearer(token))
        assert me.status_code == 200, me.text
        assert me.json()["is_root"] is False
        assert me.json()["can_rotate_recovery"] is True

        # 用户关掉了横幅：重新进「我的 → 恢复码」补发一次。
        with caplog.at_level(logging.DEBUG):
            rotated = client.post("/v1/accounts/recovery/rotate", headers=_bearer(token))
        assert rotated.status_code == 200, rotated.text
        second_code = rotated.json()["recovery_code"]
        assert second_code.startswith("RC-")
        assert second_code != first_code
        assert second_code not in caplog.text

        # 旧码立即失效。
        stale = _reset_password(client, first_code, NEW_PASSWORD)
        assert stale.status_code == 400, stale.text
        assert stale.json()["code"] == "ACCOUNT_RECOVERY_CODE_INVALID"

        # 新码只能重置**本账号**的密码。
        reset = _reset_password(client, second_code, NEW_PASSWORD)
        assert reset.status_code == 200, reset.text
        issued = reset.json()["access_token"]
        whoami = client.get("/v1/auth/me", headers=_bearer(issued)).json()
        assert whoami["principal"] == "carol"
        assert whoami["is_root"] is False

        assert _login(client, "carol", NEW_PASSWORD).status_code == 200
        # 根账号的密码与恢复码都没有被这枚码动过。
        assert _login(client, "alice", ROOT_PASSWORD).status_code == 200
        assert manager.account_root().recovery_hash == root_hash_before

        # 恢复码一次性：同码再用即失效。
        reused = _reset_password(client, second_code, "thirdpass789")
        assert reused.status_code == 400, reused.text
        assert reused.json()["code"] == "ACCOUNT_RECOVERY_CODE_INVALID"


def test_admin_created_member_still_has_no_recovery_entry(env):
    """边界：管理员创建的成员维持文档约定 —— 不签发恢复码（由管理员重置）。"""
    from src.gateway.main import get_app

    with TestClient(get_app()) as client:
        _register_root(client)
        root = _root_token(client)
        created = client.post(
            "/v1/accounts", json={"principal": "dave"}, headers=_bearer(root)
        )
        assert created.status_code == 200, created.text
        activation = created.json()["activation_code"]

        activated = client.post(
            "/v1/accounts/activate",
            json={
                "principal": "dave",
                "code": activation,
                "password": "davepass1234",
            },
        )
        assert activated.status_code == 200, activated.text
        member_token = activated.json()["access_token"]

        me = client.get("/v1/auth/me", headers=_bearer(member_token))
        assert me.status_code == 200, me.text
        assert me.json()["is_root"] is False
        assert me.json()["can_rotate_recovery"] is False

        rotated = client.post(
            "/v1/accounts/recovery/rotate", headers=_bearer(member_token)
        )
        assert rotated.status_code == 403, rotated.text
        assert rotated.json().get("code") != "ACCOUNT_RECOVERY_CODE_INVALID"
