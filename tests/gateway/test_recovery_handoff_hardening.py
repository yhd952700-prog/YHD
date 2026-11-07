"""恢复码落盘收紧回归：DPAPI / ACL / 备份排除 / 回环限制 / 旧明文迁移。

安全口径（对应本次收紧的五条）：

1. 恢复码不以明文长期落盘：Windows 优先 DPAPI（CurrentUser），失败降级为
   ACL + 短 TTL；取用一次即焚，文件与解密暂存都不残留；
2. 备份与离线导出排除恢复码、凭据与 DPAPI 密文副本；
3. ``/recovery/local`` 与 ``/recovery/reset`` 默认只认回环来源，响应不回显
   绝对路径；远程重置必须显式开关；
4. 旧明文文件就地转加密、转换成功后删除，并提示用户换新；
5. 上述每一条都有可执行断言。
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

PASSWORD = "initpass123"
NEW_PASSWORD = "resetpass456"

WINDOWS_ONLY = pytest.mark.skipif(
    os.name != "nt", reason="DPAPI / icacls 只在 Windows 上可验证"
)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    base = tmp_path
    values = {
        "LIUHAO_HUMAN_IDENTITIES_BACKEND": "file",
        "LIUHAO_HUMAN_IDENTITIES_FILE": str(base / "identities.json"),
        "LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY": "handoff-hardening-test-key",
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
        "LIUHAO_RECOVERY_HANDOFF_TTL_SECONDS": "600",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    yield values


@pytest.fixture(autouse=True)
def _reset_recovery_limits():
    from src.kernels.identity import _accounts

    for key in ("recover:root", "recovery:local"):
        _accounts.limiter.reset(key)
    yield
    for key in ("recover:root", "recovery:local"):
        _accounts.limiter.reset(key)


def _make_legacy_root():
    """根已建、密码可用、但没有恢复码（与 0.1.0 现场一致）。"""
    from src.gateway.auth import rotate_credential
    from src.kernels.identity import get_identity_manager

    manager = get_identity_manager()
    _node, code = manager.register_root_account("alice")
    manager.consume_recovery_code(code)
    assert manager.account_root().recovery_hash == ""
    assert rotate_credential("alice", PASSWORD) is True
    return manager


def _icacls(path: Path) -> str:
    result = subprocess.run(
        ["icacls", str(path)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def _assert_acl_only_current_user(path: Path) -> None:
    from src.gateway import recovery_handoff

    assert recovery_handoff.acl_is_current_user_only(path), _icacls(path)
    output = _icacls(path)
    # 继承项会带 (I)：必须显式去掉继承，而不是“看起来只有一个人”。
    assert "(I)" not in output, output
    assert "BUILTIN\\Users" not in output, output
    assert "Everyone" not in output, output
    user = os.environ.get("USERNAME", "")
    assert user and user.lower() in output.lower(), output


@WINDOWS_ONLY
def test_dpapi_handoff_is_encrypted_acl_tight_and_burns_on_take(env):
    """密文落盘（非明文）→ ACL 只给当前用户 → 取用一次后文件与暂存都清空。"""
    import win32crypt

    from src.gateway import recovery_handoff as rh

    manager = _make_legacy_root()
    code = manager.rotate_recovery_code("alice")
    result = rh.write_handoff("alice", code)
    assert result["protection"] == "dpapi-current-user"

    path = rh.handoff_path()
    raw = path.read_bytes()
    wrapper = json.loads(raw.decode("utf-8"))
    assert wrapper["schema"] == rh.SCHEMA
    assert wrapper["protection"] == "dpapi-current-user"
    # 磁盘字节里既没有码本身，也没有 RC- 前缀。
    assert code.encode("utf-8") not in raw
    assert b"RC-" not in raw

    # 同一 Windows 用户可解密（进程重启后也成立），拿到的就是原码。
    _description, plain = win32crypt.CryptUnprotectData(
        base64.b64decode(wrapper["blob"]), None, None, None, 0
    )
    assert json.loads(plain.decode("utf-8"))["code"] == code

    _assert_acl_only_current_user(path)

    taken = rh.take_handoff()
    assert taken == code
    assert not path.exists(), "取用后交接文件必须立即删除"
    assert rh.scratch_buffer() is None, "解密暂存必须清零并释放"
    assert rh.last_wiped_bytes() > 0
    # 模块状态里不残留码本身。
    leftovers = [
        value
        for value in vars(rh).values()
        if isinstance(value, (str, bytes, bytearray)) and code in str(value)
    ]
    assert leftovers == []


@WINDOWS_ONLY
def test_loose_acl_is_detected_by_selfcheck(env):
    """自检读实际 DACL：给 Everyone 读权限后必须报问题。"""
    from src.gateway import recovery_handoff as rh

    manager = _make_legacy_root()
    rh.write_handoff("alice", manager.rotate_recovery_code("alice"))
    path = rh.handoff_path()
    assert rh.handoff_selfcheck() == []

    grant = subprocess.run(
        ["icacls", str(path), "/grant", "*S-1-1-0:(R)"],
        capture_output=True, text=True, check=False,
    )
    assert grant.returncode == 0, grant.stderr
    assert not rh.acl_is_current_user_only(path)
    assert any("ACL" in item for item in rh.handoff_selfcheck())


def test_dpapi_unavailable_degrades_to_acl_plus_ttl(env, monkeypatch):
    """无 DPAPI：降级为 ACL + 短 TTL，记录原因；过期即焚。"""
    from src.gateway import recovery_handoff as rh

    monkeypatch.setattr(rh, "dpapi_available", lambda: False)
    manager = _make_legacy_root()
    code = manager.rotate_recovery_code("alice")
    rh.write_handoff("alice", code)

    path = rh.handoff_path()
    wrapper = json.loads(path.read_bytes().decode("utf-8"))
    assert wrapper["protection"] == "acl-fallback"
    assert wrapper["expires_at"], "降级文件必须有 TTL"
    assert "DPAPI" in rh.last_degradation() or "DPAPI" in rh.last_degradation().upper()
    if os.name == "nt":
        _assert_acl_only_current_user(path)

    # 一次性取用即焚（降级路径同样不留文件）。
    assert rh.take_handoff() == code
    assert not path.exists()

    # TTL 过期 → 拒绝取用并删除，不允许明文长期驻留。
    monkeypatch.setenv(rh.FALLBACK_TTL_ENV, "-60")
    rh.write_handoff("alice", manager.rotate_recovery_code("alice"))
    assert rh.take_handoff() is None
    assert not path.exists()


def test_concurrent_take_handoff_yields_exactly_one_code(env, monkeypatch):
    """并发取用只允许一次成功：锁包住"读→解密→删"，其余拿到 ``None``。

    用 ``_expired`` 作为"已读到文件、尚未删除"的注入点：没有互斥时 6 个线程
    都会在删除前读到同一份文件、各返回一次明文码；加锁后第二个调用只能看到
    文件已被前一个删除。
    """
    import threading
    import time

    from src.gateway import recovery_handoff as rh

    monkeypatch.setattr(rh, "dpapi_available", lambda: False)
    manager = _make_legacy_root()
    code = manager.rotate_recovery_code("alice")
    rh.write_handoff("alice", code)

    real_expired = rh._expired

    def slow_expired(value):
        time.sleep(0.02)
        return real_expired(value)

    monkeypatch.setattr(rh, "_expired", slow_expired)

    results = []
    barrier = threading.Barrier(6)

    def worker():
        barrier.wait(timeout=5)
        results.append(rh.take_handoff())

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert len(results) == 6, results
    successes = [item for item in results if item == code]
    assert len(successes) == 1, results
    assert [item for item in results if item is None] == [None] * 5, results
    assert not rh.handoff_path().exists()


def test_backup_and_export_exclude_recovery_and_credentials(env, tmp_path):
    """新备份与离线导出都不含恢复码、凭据与密钥文件。"""
    from src.upgrade.backups import backup_data, export_data_snapshot

    data_dir = tmp_path / "data"
    config = data_dir / "config"
    config.mkdir(parents=True)
    (config / "recovery_code.txt").write_text("RC-LEAK-LEAK-LEAK", encoding="utf-8")
    (config / "recovery_handoff_legacy.json").write_text("{}", encoding="utf-8")
    (config / "auth_secrets.json").write_text('{"alice": "hash"}', encoding="utf-8")
    (config / "model_config_key.bin").write_bytes(b"secret-key-material")
    (config / "notes.txt").write_text("normal user data", encoding="utf-8")

    backup = backup_data(tmp_path, data_dir, [], tmp_path / "backup")
    manifest = json.loads(backup.manifest_path.read_text(encoding="utf-8"))
    relpaths = [entry["relpath"] for entry in manifest["entries"]]
    assert "config/notes.txt" in relpaths
    for rel in relpaths:
        assert "recovery_code" not in rel
        assert "recovery_handoff" not in rel
        assert Path(rel).name not in {"auth_secrets.json", "model_config_key.bin"}
    assert manifest["excluded_protected"] >= 3
    for leaked in ("recovery_code.txt", "recovery_handoff_legacy.json",
                   "auth_secrets.json", "model_config_key.bin"):
        assert not list((tmp_path / "backup").rglob(leaked))

    # 旧备份清单里还列着恢复码：导出层必须过滤，而不是原样复制。
    legacy_backup = tmp_path / "legacy-backup"
    (legacy_backup / "data" / "config").mkdir(parents=True)
    source_code = legacy_backup / "data" / "config" / "recovery_code.txt"
    source_code.write_text("RC-LEAK-LEAK-LEAK", encoding="utf-8")
    source_notes = legacy_backup / "data" / "config" / "notes.txt"
    source_notes.write_text("ok", encoding="utf-8")
    entries = [
        {
            "kind": "data",
            "relpath": "config/recovery_code.txt",
            "sqlite": False,
            "size": source_code.stat().st_size,
            "sha256": hashlib.sha256(source_code.read_bytes()).hexdigest(),
        },
        {
            "kind": "data",
            "relpath": "config/notes.txt",
            "sqlite": False,
            "size": source_notes.stat().st_size,
            "sha256": hashlib.sha256(source_notes.read_bytes()).hexdigest(),
        },
    ]
    (legacy_backup / "data-backup.json").write_text(
        json.dumps(
            {
                "schema": "liuhao.data-backup/1",
                "entries": entries,
                "file_count": len(entries),
                "sqlite_count": 0,
                "total_bytes": 0,
            }
        ),
        encoding="utf-8",
    )

    exported = export_data_snapshot(legacy_backup, tmp_path / "export")
    exported_files = [
        item.relative_to(exported.destination).as_posix()
        for item in exported.destination.rglob("*")
        if item.is_file()
    ]
    assert "data/config/notes.txt" in exported_files
    assert not [item for item in exported_files if "recovery_code" in item]
    snapshot = json.loads(exported.manifest_path.read_text(encoding="utf-8"))
    assert snapshot["excluded_protected"] == 1


def test_non_loopback_is_rejected_and_remote_switch_defaults_off(env, monkeypatch):
    """回环之外：本机交接恒拒；远程重置默认拒，显式开关后才放行。"""
    manager = _make_legacy_root()
    from src.gateway.main import get_app

    remote = ("203.0.113.9", 5555)
    with TestClient(get_app(), client=remote) as client:
        denied_local = client.post("/v1/accounts/recovery/local")
        assert denied_local.status_code == 403, denied_local.text
        assert denied_local.json()["code"] == "ACCOUNT_RECOVERY_LOCAL_ONLY"

        code = manager.rotate_recovery_code("alice")
        denied_reset = client.post(
            "/v1/accounts/recovery/reset",
            json={"recovery_code": code, "password": NEW_PASSWORD},
        )
        assert denied_reset.status_code == 403, denied_reset.text
        assert denied_reset.json()["code"] == "ACCOUNT_RECOVERY_LOCAL_ONLY"

        monkeypatch.setenv("LIUHAO_ALLOW_REMOTE_RECOVERY", "1")
        allowed = client.post(
            "/v1/accounts/recovery/reset",
            json={"recovery_code": code, "password": NEW_PASSWORD},
        )
        assert allowed.status_code == 200, allowed.text

        # 开关只放宽 reset；本机交接仍必须回环。
        still_denied = client.post("/v1/accounts/recovery/local")
        assert still_denied.status_code == 403

    # 对端回环但转发链里有非回环地址：整条链必须都回环。
    with TestClient(get_app(), client=("testclient", 50000)) as client:
        forwarded = client.post(
            "/v1/accounts/recovery/local",
            headers={"X-Forwarded-For": "203.0.113.9"},
        )
        assert forwarded.status_code == 403, forwarded.text


def test_legacy_plaintext_is_migrated_then_deleted(env):
    """旧明文 → 原地转加密（不保留双份）→ 取用即焚 → 提示换新。"""
    from src.kernels.identity import get_identity_manager

    manager = get_identity_manager()
    _node, code = manager.register_root_account("alice")
    from src.kernels.identity import _persistence

    path = _persistence.default_data_dir() / "config" / "recovery_code.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"{code}\n# LiuHao 本机恢复码 · 账号 alice\n# 旧版明文格式\n",
        encoding="utf-8",
    )

    from src.gateway.main import get_app

    with TestClient(get_app()) as client:
        assert path.exists()
        raw = path.read_bytes()
        wrapper = json.loads(raw.decode("utf-8"))
        assert wrapper["schema"].startswith("liuhao.recovery-handoff/")
        if wrapper["protection"] == "dpapi-current-user":
            assert code.encode() not in raw and b"RC-" not in raw
        else:
            # 非 DPAPI 平台：只能靠 ACL + TTL，但绝不允许无限期明文。
            assert wrapper.get("expires_at")
        assert "旧版明文" not in raw.decode("utf-8"), "旧明文注释必须随转换消失"

        config = client.get("/v1/auth/config").json()
        assert config["recovery"]["rotate_recommended"] is True

        # 本机交接还需要桌面端一次性证明：回环只说明“同一台机器”。
        from src.gateway import local_proof

        bundle = local_proof.issue_bundle()
        handoff = client.post(
            "/v1/accounts/recovery/local",
            headers={"X-LiuHao-Local-Proof": bundle["handoff_token"]},
        )
        assert handoff.status_code == 200, handoff.text
        assert handoff.json()["recovered"] is True
        assert handoff.json()["recovery_code"] == code
        assert not path.exists()

        # 同一枚交接令牌已消费，重放必须被拒。
        replay = client.post(
            "/v1/accounts/recovery/local",
            headers={"X-LiuHao-Local-Proof": bundle["handoff_token"]},
        )
        assert replay.status_code == 403, replay.text
        assert replay.json()["code"] == "ACCOUNT_RECOVERY_PROOF_REQUIRED"

        reset = client.post(
            "/v1/accounts/recovery/reset",
            headers={"X-LiuHao-Reset-Proof": bundle["reset_token"]},
            json={"recovery_code": code, "password": NEW_PASSWORD},
        )
        assert reset.status_code == 200, reset.text
        # 用过码即视为已知晓：换新提示随之清除。
        assert client.get("/v1/auth/config").json()["recovery"][
            "rotate_recommended"
        ] is False
