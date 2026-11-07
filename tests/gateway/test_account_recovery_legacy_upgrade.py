"""老数据升级后的恢复码缺口回归（0.1.0 → 当前版本）。

0.1.0 时代创建的根账号升级后 ``recovery_hash`` 是**空串**：忘记密码时
``/recovery/reset`` 恒 401、``/register`` 409、``/recovery/rotate`` 又需要先
登录 —— 等于没有任何 HTTP 自助出口。另外 ``/v1/auth/config`` 的
``recovery.available`` 只看"根是否存在"，与"实际没有恢复码"矛盾；恢复码响应
用 ``updated_at`` 冒充 ``expires_at``，而它根本没有 TTL。

这里用真实 HTTP（TestClient）复现"老根 = 已建根 + 恢复码已被清空"这一现场，
并覆盖补发入口、本机自救入口与文档错误码。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

PASSWORD = "initpass123"
NEW_PASSWORD = "resetpass456"
MEMBER_PASSWORD = "memberpass789"
WRONG_CODE = "RC-AAAA-AAAA-AAAA-AAAA-AAAA-AAAA"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    base = tmp_path
    values = {
        "LIUHAO_HUMAN_IDENTITIES_BACKEND": "file",
        "LIUHAO_HUMAN_IDENTITIES_FILE": str(base / "identities.json"),
        "LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY": "legacy-recovery-test-key",
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
    yield values


@pytest.fixture(autouse=True)
def _reset_recovery_limits():
    """恢复码限次器是模块级状态，测前测后都清掉，避免互相污染。"""
    from src.kernels.identity import _accounts

    for key in ("recover:root", "recovery:local"):
        _accounts.limiter.reset(key)
    yield
    for key in ("recover:root", "recovery:local"):
        _accounts.limiter.reset(key)


def _register(client: TestClient, principal: str = "alice", password: str = PASSWORD):
    return client.post(
        "/v1/accounts/register",
        json={"principal": principal, "password": password},
    )


def _login(client: TestClient, principal: str, password: str):
    return client.post(
        "/v1/auth/login", json={"principal": principal, "secret": password}
    )


def _token(client: TestClient, principal: str, password: str) -> str:
    response = _login(client, principal, password)
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _make_legacy_root():
    """复现 0.1.0 现场：根已建、密码可用、但没有恢复码（hash 为空串）。"""
    from src.gateway.auth import rotate_credential
    from src.kernels.identity import get_identity_manager

    manager = get_identity_manager()
    _node, code = manager.register_root_account("alice")
    manager.consume_recovery_code(code)  # 老版本：恢复码未被保存下来
    assert manager.account_root().recovery_hash == ""
    assert rotate_credential("alice", PASSWORD) is True
    return manager


def _local_recovery_file() -> Path:
    from src.kernels.identity import _persistence

    return _persistence.default_data_dir() / "config" / "recovery_code.txt"


def test_legacy_root_can_reissue_recovery_code_after_login(env):
    """老根登录后补发 → 新码立刻可用 → 二次失效，且响应不展示有效期。"""
    manager = _make_legacy_root()
    from src.gateway.main import get_app

    with TestClient(get_app()) as client:
        config = client.get("/v1/auth/config").json()
        # 事实一致：根存在，但确实没有恢复码 → available=false，可本机自救。
        assert config["recovery"] == {"available": False, "self_service": True}, config

        token = _token(client, "alice", PASSWORD)
        rotated = client.post("/v1/accounts/recovery/rotate", headers=_bearer(token))
        assert rotated.status_code == 200, rotated.text
        body = rotated.json()
        code = body["recovery_code"]
        assert code.startswith("RC-")
        assert "expires_at" not in body, "恢复码没有 TTL，不得展示“有效期至”"
        assert manager.account_root().recovery_hash

        # 补发后 config 必须翻面。
        assert client.get("/v1/auth/config").json()["recovery"] == {
            "available": True,
            "self_service": False,
        }

        # 生成后立刻可用（不需要重新登录、不需要等）。
        reset = client.post(
            "/v1/accounts/recovery/reset",
            json={"recovery_code": code, "password": NEW_PASSWORD},
        )
        assert reset.status_code == 200, reset.text
        assert _login(client, "alice", NEW_PASSWORD).status_code == 200

        # 同码二次失效：文档错误码 + 状态码。
        reused = client.post(
            "/v1/accounts/recovery/reset",
            json={"recovery_code": code, "password": "thirdpass789"},
        )
        assert reused.status_code == 400, reused.text
        assert reused.json()["code"] == "ACCOUNT_RECOVERY_CODE_INVALID"


def test_member_cannot_issue_root_recovery_code(env):
    """补发入口只对根开放；成员拿不到根恢复码。"""
    manager = _make_legacy_root()
    from src.gateway.main import get_app

    with TestClient(get_app()) as client:
        root_token = _token(client, "alice", PASSWORD)
        created = client.post(
            "/v1/accounts", json={"principal": "bob"}, headers=_bearer(root_token)
        )
        assert created.status_code == 200, created.text
        activation = created.json()["activation_code"]

        activated = client.post(
            "/v1/accounts/activate",
            json={
                "principal": "bob",
                "code": activation,
                "password": MEMBER_PASSWORD,
            },
        )
        assert activated.status_code == 200, activated.text
        bob_token = activated.json()["access_token"]

        denied = client.post(
            "/v1/accounts/recovery/rotate", headers=_bearer(bob_token)
        )
        assert denied.status_code == 403, denied.text
        assert manager.account_root().recovery_hash == ""


def test_local_recovery_handoff_for_forgotten_password(env):
    """忘记密码且没有恢复码：本机文件交接 → 重置成功 → 文件自动删除。"""
    manager = _make_legacy_root()
    from src.gateway.main import get_app

    with TestClient(get_app()) as client:
        response = client.post("/v1/accounts/recovery/local")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["written"] is True
        assert "RC-" not in response.text, "恢复码绝不能出现在 HTTP 响应里"

        path = Path(body["file"])
        assert path == _local_recovery_file()
        code = path.read_text(encoding="utf-8").splitlines()[0].strip()
        assert code.startswith("RC-")
        assert manager.account_root().recovery_hash

        reset = client.post(
            "/v1/accounts/recovery/reset",
            json={"recovery_code": code, "password": NEW_PASSWORD},
        )
        assert reset.status_code == 200, reset.text
        assert not path.exists(), "码被消费后本机文件必须自动删除"
        assert _login(client, "alice", NEW_PASSWORD).status_code == 200


def test_local_handoff_refuses_when_a_recovery_code_exists(env):
    """已有恢复码时禁止本机补发：否则任何人都能远程作废用户的码（DoS）。"""
    from src.gateway.main import get_app
    from src.kernels.identity import get_identity_manager

    with TestClient(get_app()) as client:
        registered = _register(client)
        assert registered.status_code == 200, registered.text
        code = registered.json()["recovery_code"]
        manager = get_identity_manager()
        before = manager.account_root().recovery_hash

        refused = client.post("/v1/accounts/recovery/local")
        assert refused.status_code == 403, refused.text
        assert refused.json()["code"] == "ACCOUNT_RECOVERY_UNAVAILABLE"
        assert manager.account_root().recovery_hash == before

        # 旧码没有被作废，仍然可用。
        reset = client.post(
            "/v1/accounts/recovery/reset",
            json={"recovery_code": code, "password": NEW_PASSWORD},
        )
        assert reset.status_code == 200, reset.text


def test_local_handoff_write_failure_leaves_account_recoverable(env, monkeypatch):
    """写盘失败必须撤掉刚生成的码，否则账号停在“有码但用户没有码”的死角。"""
    manager = _make_legacy_root()
    from src.gateway import accounts as accounts_module
    from src.gateway.main import get_app

    real_write = accounts_module.atomic_write_text

    def broken(*_args, **_kwargs):
        raise OSError("模拟磁盘不可写")

    monkeypatch.setattr(accounts_module, "atomic_write_text", broken)

    with TestClient(get_app()) as client:
        failed = client.post("/v1/accounts/recovery/local")
        assert failed.status_code == 500, failed.text
        assert failed.json()["code"] == "ACCOUNT_RECOVERY_WRITE_FAILED"
        assert manager.account_root().recovery_hash == ""

        monkeypatch.setattr(accounts_module, "atomic_write_text", real_write)
        retried = client.post("/v1/accounts/recovery/local")
        assert retried.status_code == 200, retried.text
        assert Path(retried.json()["file"]).is_file()


def test_recovery_failure_codes_match_docs(env):
    """连续失败 5 次是 400 ACCOUNT_RECOVERY_CODE_INVALID，第 6 次锁定 429。"""
    from src.gateway.main import get_app

    with TestClient(get_app()) as client:
        registered = _register(client)
        assert registered.status_code == 200, registered.text

        for _ in range(5):
            failed = client.post(
                "/v1/accounts/recovery/reset",
                json={"recovery_code": WRONG_CODE, "password": NEW_PASSWORD},
            )
            assert failed.status_code == 400, failed.text
            assert failed.json()["code"] == "ACCOUNT_RECOVERY_CODE_INVALID"

        locked = client.post(
            "/v1/accounts/recovery/reset",
            json={"recovery_code": WRONG_CODE, "password": NEW_PASSWORD},
        )
        assert locked.status_code == 429, locked.text
        assert locked.json()["code"] == "ACCOUNT_RECOVERY_LOCKED"
