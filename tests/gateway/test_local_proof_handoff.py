"""“本机”不再等于“本人”：进程级证明的回归测试。

覆盖：
* 只有回环、没有桌面端一次性证明 → 一律 403（取码 / 重置 / 半初始化补全）；
* 伪造、过期、绑定到已退出进程、绑定到其他用户 SID 的令牌全部被拒；
* 交接令牌取用即焚，重放失败；同一令牌并发只成功一次；
* 重置令牌“占用后成功才消费”：失败可重试，并发只有一个能继续；
* 命名管道 DACL 读回断言：只有当前用户、无继承 ACE、无 Everyone/Users；
* 匿名 ``/auth/config`` 在半初始化状态不回显任何 principal；
* 模型主密钥文件读回 DACL 断言，安全删除不留内容。
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

PASSWORD = "initpass123"
NEW_PASSWORD = "resetpass456"

WINDOWS_ONLY = pytest.mark.skipif(os.name != "nt", reason="DACL 只在 Windows 上可验证")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    base = tmp_path
    values = {
        "LIUHAO_HUMAN_IDENTITIES_BACKEND": "file",
        "LIUHAO_HUMAN_IDENTITIES_FILE": str(base / "identities.json"),
        "LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY": "local-proof-test-key",
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
        "LIUHAO_ALLOW_REMOTE_RECOVERY": "0",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    yield values
    from src.gateway import local_proof

    local_proof.stop_all_brokers()
    local_proof.reset_token_store()


@pytest.fixture(autouse=True)
def _reset_recovery_limits():
    from src.kernels.identity import _accounts

    for key in ("recover:root", "recovery:local"):
        _accounts.limiter.reset(key)
    yield
    for key in ("recover:root", "recovery:local"):
        _accounts.limiter.reset(key)


def _handoff() -> dict[str, str]:
    from src.gateway import local_proof

    return {
        "X-LiuHao-Local-Proof": local_proof.issue_token(
            scope=local_proof.SCOPE_HANDOFF
        )
    }


def _reset_proof() -> dict[str, str]:
    from src.gateway import local_proof

    return {
        "X-LiuHao-Reset-Proof": local_proof.issue_token(scope=local_proof.SCOPE_RESET)
    }


def _make_legacy_root():
    """根已建、密码可用、没有恢复码：本机交接的唯一合法入口。"""
    from src.gateway.auth import rotate_credential
    from src.kernels.identity import get_identity_manager

    manager = get_identity_manager()
    _node, code = manager.register_root_account("alice")
    manager.consume_recovery_code(code)
    assert rotate_credential("alice", PASSWORD) is True
    return manager


def test_loopback_without_proof_is_rejected(env):
    """回环只说明同一台机器：没有桌面端证明，取码/重置/补全全部拒绝。"""
    manager = _make_legacy_root()
    from src.gateway.main import get_app

    with TestClient(get_app()) as client:
        denied = client.post("/v1/accounts/recovery/local")
        assert denied.status_code == 403, denied.text
        assert denied.json()["code"] == "ACCOUNT_RECOVERY_PROOF_REQUIRED"
        assert manager.account_root().recovery_hash == ""

        code = manager.rotate_recovery_code("alice")
        denied_reset = client.post(
            "/v1/accounts/recovery/reset",
            json={"recovery_code": code, "password": NEW_PASSWORD},
        )
        assert denied_reset.status_code == 403, denied_reset.text
        assert denied_reset.json()["code"] == "ACCOUNT_RECOVERY_PROOF_REQUIRED"
        # 被拒的请求不能消费有效恢复码。
        assert manager.account_root().recovery_hash


def test_forged_expired_and_dead_pid_tokens_are_rejected(env, monkeypatch):
    """伪造 / 过期 / 绑定已退出进程的令牌都不能换到恢复码。"""
    _make_legacy_root()
    from src.gateway import local_proof
    from src.gateway.main import get_app

    with TestClient(get_app()) as client:
        forged = client.post(
            "/v1/accounts/recovery/local",
            headers={"X-LiuHao-Local-Proof": "forged-token"},
        )
        assert forged.status_code == 403, forged.text

        monkeypatch.setenv(local_proof.PROOF_TTL_ENV, "0")
        expired = client.post("/v1/accounts/recovery/local", headers=_handoff())
        assert expired.status_code == 403, expired.text
        monkeypatch.delenv(local_proof.PROOF_TTL_ENV, raising=False)

        dead = local_proof.issue_token(
            scope=local_proof.SCOPE_HANDOFF, bound_pid=2**31 - 1
        )
        refused = client.post(
            "/v1/accounts/recovery/local",
            headers={"X-LiuHao-Local-Proof": dead},
        )
        assert refused.status_code == 403, refused.text


def test_token_bound_to_another_windows_user_is_rejected(env, monkeypatch):
    """同机另一个 Windows 账户（不同 SID）即使拿到令牌明文也不能用。"""
    _make_legacy_root()
    from src.gateway import local_proof
    from src.gateway.main import get_app

    token = local_proof.issue_token(scope=local_proof.SCOPE_HANDOFF)
    monkeypatch.setattr(
        local_proof, "current_user_sid_string", lambda: "S-1-5-21-0-0-0-9999"
    )
    with TestClient(get_app()) as client:
        refused = client.post(
            "/v1/accounts/recovery/local",
            headers={"X-LiuHao-Local-Proof": token},
        )
        assert refused.status_code == 403, refused.text
        assert refused.json()["code"] == "ACCOUNT_RECOVERY_PROOF_REQUIRED"


def test_handoff_token_is_single_use_and_concurrent_only_one_wins(env):
    """交接令牌取用即焚；同一令牌并发只允许一次成功。"""
    _make_legacy_root()
    from src.gateway.main import get_app

    app = get_app()
    headers = _handoff()
    barrier = threading.Barrier(6)
    statuses: list[int] = []

    with TestClient(app) as client:
        def worker() -> None:
            barrier.wait(timeout=10)
            response = client.post(
                "/v1/accounts/recovery/local", headers=headers
            )
            statuses.append(response.status_code)

        threads = [threading.Thread(target=worker) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        assert len(statuses) == 6, statuses
        assert statuses.count(200) == 1, statuses
        assert statuses.count(403) == 5, statuses

        replay = client.post("/v1/accounts/recovery/local", headers=headers)
        assert replay.status_code == 403, replay.text
        assert replay.json()["code"] == "ACCOUNT_RECOVERY_PROOF_REQUIRED"


def test_reset_token_claim_allows_retry_but_only_one_concurrent_winner(env, monkeypatch):
    """重置令牌占用后成功才消费：失败可重试，并发只有一个能继续。"""
    from src.gateway import accounts as accounts_module
    from src.gateway import local_proof
    from src.gateway.main import get_app

    real_rotate = accounts_module.rotate_credential

    with TestClient(get_app()) as client:
        registered = client.post(
            "/v1/accounts/register",
            json={"principal": "alice", "password": PASSWORD},
        )
        assert registered.status_code == 200, registered.text
        code = registered.json()["recovery_code"]
        proof = _reset_proof()

        # 第一次改密失败：令牌必须释放，用户可以原样重试。
        monkeypatch.setattr(
            accounts_module,
            "rotate_credential",
            lambda principal, secret: (_ for _ in ()).throw(
                RuntimeError("模拟改密失败")
            ),
        )
        failed = client.post(
            "/v1/accounts/recovery/reset",
            headers=proof,
            json={"recovery_code": code, "password": NEW_PASSWORD},
        )
        assert failed.status_code == 500, failed.text

        monkeypatch.setattr(accounts_module, "rotate_credential", real_rotate)
        retried = client.post(
            "/v1/accounts/recovery/reset",
            headers=proof,
            json={"recovery_code": code, "password": NEW_PASSWORD},
        )
        assert retried.status_code == 200, retried.text

        # 成功之后令牌已消费：再拿同一枚令牌必须 403。
        used = client.post(
            "/v1/accounts/recovery/reset",
            headers=proof,
            json={"recovery_code": code, "password": "thirdpass789"},
        )
        assert used.status_code == 403, used.text
        assert used.json()["code"] == "ACCOUNT_RECOVERY_PROOF_REQUIRED"

    # 令牌库原语的并发占用：8 个线程同一令牌只有一个 True。
    token = local_proof.issue_token(scope=local_proof.SCOPE_RESET)
    results: list[bool] = []
    barrier = threading.Barrier(8)

    def claim_worker() -> None:
        barrier.wait(timeout=10)
        results.append(local_proof.claim_token(token, scope=local_proof.SCOPE_RESET))

    threads = [threading.Thread(target=claim_worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert results.count(True) == 1, results
    assert results.count(False) == 7, results


def test_half_initialized_completion_needs_proof_and_never_echoes_name(env):
    """半初始化补全：知道名字也不能接管，必须持有桌面端证明。"""
    from src.gateway.main import get_app
    from src.kernels.identity import get_identity_manager

    get_identity_manager().register_root_account("alice", display_name="Alice")

    with TestClient(get_app()) as client:
        config = client.get("/v1/auth/config")
        assert config.status_code == 200
        body = config.json()
        assert body["initialization"] == {
            "state": "incomplete",
            "recoverable": True,
            "principal": None,
        }
        assert "alice" not in config.text

        # 知道名字 + 只有回环：仍然拒绝。
        anonymous = client.post(
            "/v1/accounts/register",
            json={"principal": "alice", "password": NEW_PASSWORD},
        )
        assert anonymous.status_code == 403, anonymous.text
        assert anonymous.json()["code"] == "ACCOUNT_RECOVERY_PROOF_REQUIRED"
        assert "alice" not in anonymous.text

        # 有证明但名字不对：中性 409，不泄露根名。
        wrong = client.post(
            "/v1/accounts/register",
            json={"principal": "bobby", "password": NEW_PASSWORD},
            headers=_handoff(),
        )
        assert wrong.status_code == 409, wrong.text
        assert wrong.json()["code"] == "ACCOUNT_INITIALIZATION_INCOMPLETE"
        assert "alice" not in wrong.text

        resumed = client.post(
            "/v1/accounts/register",
            json={"principal": "alice", "password": NEW_PASSWORD},
            headers=_handoff(),
        )
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["resumed"] is True


@WINDOWS_ONLY
def test_named_pipe_acl_is_current_user_only(env):
    """读回命名管道真实 DACL：只有当前用户，没有继承/Everyone/Users。"""
    from src.gateway import local_proof
    from src.gateway.main import get_app

    with TestClient(get_app()):
        data_dir = Path(env["LIUHAO_DATA_DIR"])
        assert local_proof.wait_for_broker(data_dir), "证明通道未能启动"
        assert local_proof.pipe_acl_is_current_user_only(data_dir) is True

        entries = local_proof.pipe_acl_entries(data_dir)
        assert entries, "必须能读到真实 ACE，而不是只声称设置成功"
        from src.common.windows_acl import current_user_sid_string

        me = current_user_sid_string()
        assert {entry["sid"] for entry in entries} == {me}, entries
        for entry in entries:
            assert entry["type"] == 0, entry  # ACCESS_ALLOWED
            assert entry["flags"] & 0x10 == 0, entry  # 不得有继承 ACE
            assert entry["sid"] not in {"S-1-1-0", "S-1-5-32-545"}, entry


@WINDOWS_ONLY
def test_model_key_file_acl_and_secure_delete(env, monkeypatch):
    """模型主密钥文件创建后 DACL 仅当前用户；安全删除后内容不残留。"""
    from src.ai import model_config
    from src.ai.model_config import ModelConfigStore
    from src.common.windows_acl import (
        acl_is_current_user_only,
        secure_delete_file,
    )

    # 全局测试夹具默认给了口令型密钥；这里要走“文件型密钥”分支。
    monkeypatch.delenv(model_config.MODEL_CONFIG_KEY_ENV, raising=False)
    store = ModelConfigStore()
    key = store._encryption_key()
    assert len(key) == 32
    path = Path(env["LIUHAO_MODEL_CONFIG_KEY_FILE"])
    assert path.exists()
    assert acl_is_current_user_only(path), "模型密钥文件必须只有当前用户可读"

    path.write_bytes(b"PLAINTEXT-KEY-MATERIAL-SHOULD-VANISH")
    assert secure_delete_file(path) is True
    assert not path.exists()
