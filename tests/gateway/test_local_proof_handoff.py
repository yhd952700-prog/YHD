"""“本机”不再等于“本人”：进程级证明的回归测试。

覆盖：
* 回环来源即产品声明的本机信任边界：``/recovery/local`` 与 ``/recovery/reset``
  按现行契约允许无桌面端令牌的自助取码/重置（依据
  ``docs/account-hierarchy.md`` §S3b），但非回环来源必须 403；
* 伪造、过期、绑定到已退出进程、绑定到其他用户 SID 的令牌全部被拒；
* 交接令牌取用即焚，重放失败；同一令牌并发只成功一次；
* 重置令牌“占用后成功才消费”：失败可重试，并发只有一个能继续；
* 命名管道 DACL 读回断言：只有当前用户、无继承 ACE、无 Everyone/Users；
* 匿名 ``/auth/config`` 在半初始化状态不回显任何 principal；
* 模型主密钥文件读回 DACL 断言，安全删除不留内容。
"""

from __future__ import annotations

import json
import logging
import os
import socket
import sqlite3
import subprocess
import sys
import threading
import time
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


def test_loopback_without_proof_uses_documented_self_service(env):
    """回环即本机信任边界：按现行契约，无桌面端证明也能自助取码/重置。

    依据 ``docs/account-hierarchy.md``（现行契约，非旧版用例假设）：

    * §S3b 表格：``POST /v1/accounts/recovery/local`` **无需令牌，仅回环来源**；
    * §S3b 正文：``/recovery/reset`` 默认同样只接受回环来源；
    * §S3b 残余风险：回环可达对本机其他账户的进程同样成立，DPAPI 只保护
      磁盘副本，不承诺阻止同机进程调用该接口 —— 能访问回环的主体在信任
      边界之内。

    因此旧用例“回环但无证明 → 403”与现行契约相反；这里改断言真实契约，
    同时钉住回环放宽不能带出的两条硬边界：非回环来源必须中性 403、恢复码
    使用后必须失效。
    """
    manager = _make_legacy_root()
    from src.gateway.main import get_app

    with TestClient(get_app()) as client:
        # 回环 + 无证明：允许生成本机恢复码（200），而不是 403。
        local = client.post("/v1/accounts/recovery/local")
        assert local.status_code == 200, local.text
        payload = local.json()
        assert payload["written"] is True, payload
        code = payload["recovery_code"]
        assert manager.account_root().recovery_hash

        # 同机浏览器直接重置：按契约不要求桌面端令牌。
        reset = client.post(
            "/v1/accounts/recovery/reset",
            json={"recovery_code": code, "password": NEW_PASSWORD},
        )
        assert reset.status_code == 200, reset.text
        logged_in = client.post(
            "/v1/auth/login",
            json={"principal": "alice", "secret": NEW_PASSWORD},
        )
        assert logged_in.status_code == 200, logged_in.text

        # 恢复码一次性：成功使用后同一枚码不能再解锁。
        reused = client.post(
            "/v1/accounts/recovery/reset",
            json={"recovery_code": code, "password": NEW_PASSWORD},
        )
        assert reused.status_code >= 400, reused.text

        # 回环判定不得被非回环转发链带出本机：远程来源必须中性 403。
        remote = client.post(
            "/v1/accounts/recovery/local",
            headers={"X-Forwarded-For": "203.0.113.9"},
        )
        assert remote.status_code == 403, remote.text
        assert remote.json()["code"] == "ACCOUNT_RECOVERY_LOCAL_ONLY"


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


def test_trusted_launch_first_registration_requires_proof(env, monkeypatch, caplog):
    """可信启动：首次注册必须带桌面端证明；开发模式只告警、不阻断。"""
    from src.gateway import local_proof
    from src.gateway.main import get_app

    # TestClient 没有真实 TCP 对端，无法反查 PID；这里模拟“同用户”这一层通过，
    # 单独验证证明门禁。真实对端身份由下面的 mismatch 测试覆盖。
    monkeypatch.setattr(local_proof, "peer_is_same_user", lambda request: True)
    monkeypatch.setenv(local_proof.TRUSTED_LAUNCH_ENV, "1")

    with TestClient(get_app()) as client:
        refused = client.post(
            "/v1/accounts/register",
            json={"principal": "alice", "password": PASSWORD},
        )
        assert refused.status_code == 403, refused.text
        assert refused.json()["code"] == "ACCOUNT_RECOVERY_PROOF_REQUIRED"

        created = client.post(
            "/v1/accounts/register",
            json={"principal": "alice", "password": PASSWORD},
            headers=_handoff(),
        )
        assert created.status_code == 200, created.text
        assert created.json()["account"]["principal"] == "alice"


def test_trusted_launch_rejects_other_windows_user_even_with_token(env, monkeypatch):
    """同机另一个 Windows 账户：即使拿到令牌明文，HTTP 也因对端 SID 不符被拒。"""
    from src.gateway import local_proof
    from src.gateway.main import get_app

    monkeypatch.setattr(local_proof, "peer_is_same_user", lambda request: False)
    monkeypatch.setenv(local_proof.TRUSTED_LAUNCH_ENV, "1")

    with TestClient(get_app()) as client:
        refused = client.post(
            "/v1/accounts/register",
            json={"principal": "alice", "password": PASSWORD},
            headers=_handoff(),
        )
        assert refused.status_code == 403, refused.text
        assert refused.json()["code"] == "ACCOUNT_LOCAL_USER_MISMATCH"

        local = client.post(
            "/v1/accounts/recovery/local", headers=_handoff()
        )
        assert local.status_code == 403, local.text
        assert local.json()["code"] == "ACCOUNT_LOCAL_USER_MISMATCH"


def test_dev_mode_first_registration_warns_but_allows(env, monkeypatch, caplog):
    """``python -m src.gateway`` 开发用法：无证明也放行，但必须留下告警。"""
    import logging

    from src.gateway.main import get_app

    monkeypatch.delenv("LIUHAO_TRUSTED_LAUNCH", raising=False)
    with caplog.at_level(logging.WARNING, logger="src.gateway.accounts"):
        with TestClient(get_app()) as client:
            created = client.post(
                "/v1/accounts/register",
                json={"principal": "alice", "password": PASSWORD},
            )
            assert created.status_code == 200, created.text
    assert any("首次注册未校验桌面端证明" in record.message for record in caplog.records)


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
def test_peer_pid_resolves_client_process_not_the_server(tmp_path):
    """TCP 表必须按客户端侧匹配，否则只会查到网关自己（SID 恒等）。

    回归的是真实 bug：``remote_addr:remote_port`` 在服务端那一行正好是客户端
    地址，但持有者是网关进程；按它匹配会让“另一 Windows 用户”的请求永远通过。
    """
    from src.gateway import local_proof

    pid_file = tmp_path / "client.pid"
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    child = None
    try:
        code = (
            "import os,socket,time;"
            f"s=socket.create_connection(('127.0.0.1',{port}));"
            f"open(r'{pid_file}','w').write(str(os.getpid()));"
            "time.sleep(10)"
        )
        child = subprocess.Popen([sys.executable, "-c", code])
        conn, addr = server.accept()
        try:
            deadline = time.monotonic() + 10.0
            while not pid_file.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            client_pid = int(pid_file.read_text(encoding="utf-8").strip())
            resolved = local_proof._windows_peer_pid(addr[0], addr[1])
            assert resolved == client_pid, (
                f"解析到 {resolved}，客户端实际是 {client_pid}"
                "（按服务端 remote 匹配会返回网关自己）"
            )
            assert resolved != os.getpid()
        finally:
            conn.close()
    finally:
        server.close()
        if child is not None and child.poll() is None:
            child.kill()
            child.wait(timeout=10)


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


def _recovery_audit_rows(env, action: str) -> list[dict]:
    """读取真实审计库，只返回 details.action 命中指定动作的行。"""
    db = sqlite3.connect(env["AUDIT_DB_PATH"])
    db.row_factory = sqlite3.Row
    try:
        raw = db.execute(
            "SELECT event_type, principal_id, outcome, details "
            "FROM audit_events WHERE details LIKE ? ORDER BY seq",
            (f"%{action}%",),
        ).fetchall()
    finally:
        db.close()
    return [
        {
            "event_type": row["event_type"],
            "principal_id": row["principal_id"],
            "outcome": row["outcome"],
            "details": json.loads(row["details"]),
        }
        for row in raw
    ]


def test_recovery_local_is_rate_limited_per_source_and_audited(env):
    """来源+端点限流：前 10 次进入业务，第 11 次 429 且带 Retry-After；成败都审计。"""
    _make_legacy_root()
    from src.gateway.main import get_app

    with TestClient(get_app()) as client:
        responses = [client.post("/v1/accounts/recovery/local") for _ in range(11)]

    assert responses[0].status_code == 200, responses[0].text
    assert [r.status_code for r in responses[1:10]] == [403] * 9
    blocked = responses[10]
    assert blocked.status_code == 429, blocked.text
    assert blocked.json()["code"] == "ACCOUNT_RECOVERY_LOCKED"
    assert int(blocked.headers["Retry-After"]) >= 60

    rows = _recovery_audit_rows(env, "account.recovery.local")
    assert len(rows) == 11, rows
    assert sum(1 for r in rows if r["outcome"] == "allowed") == 1
    assert sum(1 for r in rows if r["outcome"] == "denied") == 10
    assert {r["details"]["source"] for r in rows} == {"testclient"}
    assert {r["details"]["endpoint"] for r in rows} == {"/v1/accounts/recovery/local"}
    # 成功那条能回答“谁在什么时候取了码”；失败条目标注真实错误码。
    allowed = next(r for r in rows if r["outcome"] == "allowed")
    assert allowed["details"]["actor"] == "alice"
    assert allowed["details"]["target"] == "alice"
    assert allowed["details"]["mode"] == "generated"
    denied_codes = {
        r["details"].get("error_code") for r in rows if r["outcome"] == "denied"
    }
    assert {"account_recovery_unavailable", "account_recovery_locked"} <= denied_codes


def test_forged_forwarding_headers_cannot_open_new_recovery_buckets(env):
    """伪造 X-Forwarded-For 不能换桶：所有本机请求都归到同一个桶。

    真实 uvicorn 会按回环对端的 ``X-Forwarded-For`` 重写 ``request.client``；
    若桶 key 直接使用 client.host，攻击者每换一个伪造地址就得到一个新桶，
    10 次上限被绕开（修复前真实 30 次全部进入业务）。这里直接构造“已被
    重写的 client + 伪造 XFF”的 Request，确保判定按可信来源收敛。
    """
    from fastapi import HTTPException
    from starlette.requests import Request

    from src.gateway import accounts as accounts_module
    from src.kernels.identity import _accounts

    bucket_key = (
        "recovery:http:local:local:" + accounts_module._recovery_http_scope()
    )
    _accounts.limiter.reset(bucket_key)
    try:
        statuses = []
        for index in range(11):
            forged = f"127.0.0.{index + 1}"
            scope = {
                "type": "http",
                "method": "POST",
                "path": "/v1/accounts/recovery/local",
                "headers": [(b"x-forwarded-for", forged.encode())],
                # 模拟 uvicorn 依据 XFF 重写后的 client（每个伪造地址都不同）。
                "client": (forged, 5000),
                "server": ("127.0.0.1", 8080),
                "scheme": "http",
                "query_string": b"",
            }
            try:
                accounts_module._recovery_http_rate_limit(Request(scope), "local")
                statuses.append(200)
            except HTTPException as exc:
                statuses.append(exc.status_code)
        assert statuses[:10] == [200] * 10, statuses
        assert statuses[10] == 429, statuses
    finally:
        _accounts.limiter.reset(bucket_key)


def test_recovery_reset_audits_success_and_failure_with_source(env):
    """重置成功与失败都写审计：成功记 actor=根账号，失败记匿名 + 目标根账号。"""
    _make_legacy_root()
    from src.gateway.main import get_app

    with TestClient(get_app()) as client:
        local = client.post("/v1/accounts/recovery/local")
        assert local.status_code == 200, local.text
        code = local.json()["recovery_code"]

        ok = client.post(
            "/v1/accounts/recovery/reset",
            json={"recovery_code": code, "password": NEW_PASSWORD},
        )
        assert ok.status_code == 200, ok.text

        bad = client.post(
            "/v1/accounts/recovery/reset",
            json={"recovery_code": "RC-NOT-A-REAL-CODE", "password": NEW_PASSWORD},
        )
        assert bad.status_code == 400, bad.text
        assert bad.json()["code"] == "ACCOUNT_RECOVERY_CODE_INVALID"

    rows = _recovery_audit_rows(env, "account.recovery.reset")
    assert len(rows) == 2, rows
    allowed = next(r for r in rows if r["outcome"] == "allowed")
    denied = next(r for r in rows if r["outcome"] == "denied")
    assert allowed["principal_id"] == "alice"
    assert allowed["details"]["actor"] == "alice"
    assert allowed["details"]["target"] == "alice"
    assert allowed["details"]["mode"] == "password_reset"
    assert denied["details"]["actor"] == "anonymous"
    assert denied["details"]["target"] == "alice"
    assert denied["details"]["error_code"] == "account_recovery_code_invalid"
    for row in (allowed, denied):
        assert row["details"]["source"] == "testclient"
        assert row["details"]["endpoint"] == "/v1/accounts/recovery/reset"


def test_audit_write_failure_is_reported_without_blocking_recovery(
    env, monkeypatch, caplog
):
    """审计库写不进时：取码/重置仍可完成，但响应与日志明确告知“审计未写入”。

    故障注入点选在 ``src.kernels.audit.log_event``（真实写库入口），因此走的是
    与磁盘满/只读完全相同的异常路径：``_account_audit`` 捕获后返回失败。
    """
    _make_legacy_root()

    import src.kernels.audit as audit_mod

    def broken_log_event(*args, **kwargs):
        raise RuntimeError("模拟审计库只读/磁盘满")

    monkeypatch.setattr(audit_mod, "log_event", broken_log_event)

    from src.gateway.main import get_app

    with TestClient(get_app()) as client:
        with caplog.at_level(logging.WARNING, logger="src.gateway.accounts"):
            local = client.post("/v1/accounts/recovery/local")
            assert local.status_code == 200, local.text
            payload = local.json()
            assert payload.get("audit_written") is False, payload
            assert "审计未写入" in payload.get("audit_warning", ""), payload
            code = payload["recovery_code"]

            reset = client.post(
                "/v1/accounts/recovery/reset",
                json={"recovery_code": code, "password": NEW_PASSWORD},
            )
            assert reset.status_code == 200, reset.text
            body = reset.json()
            assert body.get("audit_written") is False, body
            assert "审计未写入" in body.get("audit_warning", ""), body

        # 审计失败不得堵死自救：用新密码能正常登录。
        logged_in = client.post(
            "/v1/auth/login",
            json={"principal": "alice", "secret": NEW_PASSWORD},
        )
        assert logged_in.status_code == 200, logged_in.text

    messages = [record.getMessage() for record in caplog.records]
    assert any("审计未写入" in message for message in messages), messages
