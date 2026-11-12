"""本机绑定密钥回归：模型主密钥 / 身份签名密钥 / JWT 签名密钥。

目标行为（验收口径）
--------------------
1. Windows 上落盘必须带 ``LIUHAO-DPAPI-1:`` 版本头，内容是 DPAPI（CurrentUser）
   包裹后的密文，**不是**裸字节；
2. 旧的明文 / 裸 32 字节 / 33 字节口令形态在读取时自动迁移；
3. 迁移失败不阻塞启动，只留下中文告警；
4. 换机器后解不开时必须给中文可执行步骤，且**不得**静默生成新密钥覆盖旧文件；
5. 备份与离线导出不得包含这些文件（包括迁移临时文件/改名副本）。

本文件刻意只通过公开行为断言，避免把实现细节（函数名）写进断言；模拟
DPAPI 失败时用 ``monkeypatch`` 打在 ``src.security.encryption`` 上。
"""
from __future__ import annotations

import hashlib
import json
import os

import pytest

import src.security.encryption as encryption
from src.ai import model_config as model_config_module
from src.ai.model_config import ModelConfigError, ModelConfigStore
from src.common import integrity_key as ik
from src.upgrade.backups import is_protected_state_file

WINDOWS_ONLY = pytest.mark.skipif(
    os.name != "nt", reason="DPAPI 只在 Windows 上可验证"
)


# --------------------------------------------------------------------------- #
# 辅助
# --------------------------------------------------------------------------- #

def _store(tmp_path) -> ModelConfigStore:
    return ModelConfigStore(
        db_path=str(tmp_path / "registry.sqlite3"),
        key_path=str(tmp_path / "model_config.key"),
    )


def _create(
    store: ModelConfigStore, *, api_key: str = "sk-abcdefghijklmnop", name: str = "machine-bound"
):
    return store.create(
        {
            "name": name,
            "provider": "mock",
            "model": "mock-model",
            "api_key": api_key,
        },
        "admin",
    )


def _simulate_dpapi_unprotect_failure(monkeypatch) -> None:
    """换机器/换用户：DPAPI 解不开，返回 None（真实实现也是 None）。"""
    monkeypatch.setattr(
        encryption, "dpapi_unprotect", lambda *args, **kwargs: None, raising=False
    )


def _key_file_is_plain(raw: bytes, needle: bytes) -> bool:
    return needle in raw


# --------------------------------------------------------------------------- #
# 模型主密钥
# --------------------------------------------------------------------------- #

@WINDOWS_ONLY
def test_new_model_key_file_is_dpapi_wrapped_not_raw(tmp_path, monkeypatch):
    monkeypatch.delenv(model_config_module.MODEL_CONFIG_KEY_ENV, raising=False)
    store = _store(tmp_path)
    created = _create(store)
    assert created["api_key_status"] == "ok"

    raw = (tmp_path / "model_config.key").read_bytes()
    assert raw.startswith(b"LIUHAO-DPAPI-1:"), "落盘必须带明确版本头"
    assert len(raw) != 32, "不允许再写裸 32 字节"

    # 正常读写往返：重开仍能解密（同一台机器）。
    reopened = _store(tmp_path)
    assert reopened.get_public(created["id"])["api_key_masked"] == "sk-a…mnop"


@WINDOWS_ONLY
def test_legacy_raw_32_byte_key_is_migrated_without_plaintext_left(
    tmp_path, monkeypatch
):
    monkeypatch.delenv(model_config_module.MODEL_CONFIG_KEY_ENV, raising=False)
    key = b"K" * 32
    key_path = tmp_path / "model_config.key"
    key_path.write_bytes(key)

    store = _store(tmp_path)
    created = _create(store)
    assert created["api_key_status"] == "ok"

    migrated = key_path.read_bytes()
    assert migrated.startswith(b"LIUHAO-DPAPI-1:")
    assert key not in migrated
    assert migrated != key
    # 迁移后旧密文仍可解（密钥语义未变）。
    assert _store(tmp_path).get_public(created["id"])["api_key_status"] == "ok"


@WINDOWS_ONLY
def test_legacy_32_byte_with_trailing_newline_is_migrated(tmp_path, monkeypatch):
    monkeypatch.delenv(model_config_module.MODEL_CONFIG_KEY_ENV, raising=False)
    key = b"B" * 32
    key_path = tmp_path / "model_config.key"
    key_path.write_bytes(key + b"\n")

    created = _create(_store(tmp_path))
    assert created["api_key_status"] == "ok"
    assert key_path.read_bytes().startswith(b"LIUHAO-DPAPI-1:")


@WINDOWS_ONLY
def test_legacy_33_byte_file_keeps_old_passphrase_semantics(tmp_path, monkeypatch):
    """33 字节在旧实现里走"当口令"分支；迁移必须保持这条确定性语义。"""
    import base64

    monkeypatch.delenv(model_config_module.MODEL_CONFIG_KEY_ENV, raising=False)
    legacy_text = b"L" * 33
    legacy_key = hashlib.sha256(legacy_text).digest()
    key_path = tmp_path / "model_config.key"
    key_path.write_bytes(legacy_text)

    # 用旧语义（口令派生）写一条密文，模拟升级前的真实数据。
    store = _store(tmp_path)
    store._connect()
    cipher = encryption.AesGcmCipher(legacy_key)
    sealed = cipher.encrypt(
        b"sk-legacy33secret", associated_data=b"ai-model:legacy33"
    )
    stored = (
        "enc:v1:"
        + base64.b64encode(sealed.nonce).decode()
        + ":"
        + base64.b64encode(sealed.ciphertext).decode()
    )
    conn = store._connect()
    conn.execute(
        "INSERT INTO ai_model_configs (id, name, provider, model, base_url, "
        "api_key_enc, owner_principal, timeout, is_default, enabled, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "legacy33",
            "legacy33",
            "mock",
            "mock-model",
            None,
            stored,
            "",
            120.0,
            1,
            1,
            "2026-01-01T00:00:00+00:00",
            "2026-01-01T00:00:00+00:00",
        ),
    )
    conn.commit()

    migrated = _store(tmp_path)
    public = migrated.get_public("legacy33")
    assert public["api_key_status"] == "ok", "33 字节旧文件必须还能解开旧密文"
    assert public["api_key_masked"] == "sk-l…cret"
    raw = key_path.read_bytes()
    assert raw.startswith(b"LIUHAO-DPAPI-1:")
    assert legacy_text not in raw


@WINDOWS_ONLY
def test_migration_failure_still_boots_and_warns(tmp_path, monkeypatch, caplog):
    monkeypatch.delenv(model_config_module.MODEL_CONFIG_KEY_ENV, raising=False)
    key = b"K" * 32
    key_path = tmp_path / "model_config.key"
    key_path.write_bytes(key)

    def _boom(*args, **kwargs):
        raise RuntimeError("dpapi unavailable")

    monkeypatch.setattr(
        encryption, "dpapi_protect", _boom, raising=False
    )

    with caplog.at_level("WARNING"):
        created = _create(_store(tmp_path))
    assert created["api_key_status"] == "ok", "迁移失败不得阻断本次使用"
    assert key_path.read_bytes() == key, "迁移失败时不得覆盖旧文件"
    assert any("迁移" in rec.message for rec in caplog.records)


@WINDOWS_ONLY
def test_cross_machine_model_key_reports_chinese_and_does_not_regenerate(
    tmp_path, monkeypatch
):
    monkeypatch.delenv(model_config_module.MODEL_CONFIG_KEY_ENV, raising=False)
    store = _store(tmp_path)
    created = _create(store)
    key_path = tmp_path / "model_config.key"
    before = key_path.read_bytes()

    _simulate_dpapi_unprotect_failure(monkeypatch)

    reopened = _store(tmp_path)
    listed = reopened.list_models()
    assert listed[0]["api_key_status"] == "needs_reentry"
    with pytest.raises(ModelConfigError) as excinfo:
        reopened.resolve(created["id"])
    message = excinfo.value.detail
    assert "无法在此机器解密" in message
    assert "账号备份包" in message
    for internal in ("Traceback", "InvalidTag", "CryptUnprotectData"):
        assert internal not in message

    # 解不开时不能写一把新密钥把旧密文彻底废掉。
    assert key_path.read_bytes() == before


@WINDOWS_ONLY
def test_missing_key_file_regenerates_with_loud_warning_and_keeps_old_rows(
    tmp_path, monkeypatch, caplog
):
    """用户按提示删除密钥文件后必须能恢复；旧 Key 行保持 needs_reentry。"""
    monkeypatch.delenv(model_config_module.MODEL_CONFIG_KEY_ENV, raising=False)
    store = _store(tmp_path)
    created = _create(store)
    key_path = tmp_path / "model_config.key"
    key_path.unlink()

    with caplog.at_level("WARNING"):
        fresh = _create(
            _store(tmp_path), api_key="sk-newsecretvalue", name="fresh-model"
        )
    assert fresh["api_key_status"] == "ok"
    assert any("主密钥文件缺失" in rec.message for rec in caplog.records)
    assert key_path.exists(), "删除旧文件后应能生成新密钥继续使用"
    assert key_path.read_bytes().startswith(b"LIUHAO-DPAPI-1:")

    reopened = _store(tmp_path)
    old = reopened.get_public(created["id"])
    assert old["api_key_status"] == "needs_reentry"


# --------------------------------------------------------------------------- #
# 身份签名密钥
# --------------------------------------------------------------------------- #

@WINDOWS_ONLY
def test_integrity_key_is_wrapped_and_legacy_is_migrated(tmp_path, monkeypatch):
    monkeypatch.delenv(ik.INTEGRITY_KEY_ENV, raising=False)
    key_path = tmp_path / "config" / "integrity.key"

    first = ik.ensure_integrity_key(data_dir=tmp_path)
    raw = key_path.read_bytes()
    assert raw.startswith(b"LIUHAO-DPAPI-1:")
    assert first.encode() not in raw
    assert ik.read_key_file(key_path) == first

    # 旧明文文件（升级前磁盘上的形态）自动迁移，值不变。
    key_path.write_bytes((first + "\n").encode())
    second = ik.ensure_integrity_key(data_dir=tmp_path)
    assert second == first
    assert key_path.read_bytes().startswith(b"LIUHAO-DPAPI-1:")


@WINDOWS_ONLY
def test_cross_machine_integrity_key_warns_and_preserves_old_secret(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.delenv(ik.INTEGRITY_KEY_ENV, raising=False)
    key_path = tmp_path / "config" / "integrity.key"
    old_key = ik.ensure_integrity_key(data_dir=tmp_path)
    before = key_path.read_bytes()

    _simulate_dpapi_unprotect_failure(monkeypatch)
    new_key = ik.ensure_integrity_key(data_dir=tmp_path)
    err = capsys.readouterr().err

    assert "无法在此机器解密" in err
    assert "账号备份包" in err
    assert new_key != old_key
    # 旧密文必须留在磁盘上（改名保存），不能被新密钥直接覆盖丢失。
    preserved = list((tmp_path / "config").glob("integrity.key.unreadable-*"))
    assert preserved, "旧密文必须以可找回的方式保留"
    assert any(item.read_bytes() == before for item in preserved)


@WINDOWS_ONLY
def test_integrity_migration_failure_still_returns_old_key(tmp_path, monkeypatch, caplog):
    monkeypatch.delenv(ik.INTEGRITY_KEY_ENV, raising=False)
    key_path = tmp_path / "config" / "integrity.key"
    key_path.parent.mkdir(parents=True)
    key_path.write_text("legacy-integrity-key\n", encoding="utf-8")

    def _boom(*args, **kwargs):
        raise RuntimeError("dpapi unavailable")

    monkeypatch.setattr(ik, "wrap_machine_bound", _boom)

    with caplog.at_level("WARNING"):
        key = ik.ensure_integrity_key(data_dir=tmp_path)
    assert key == "legacy-integrity-key"
    assert key_path.read_text(encoding="utf-8").strip() == "legacy-integrity-key"
    assert any("迁移失败" in rec.message for rec in caplog.records)


@WINDOWS_ONLY
def test_jwt_migration_failure_keeps_plaintext_and_boots(tmp_path, monkeypatch, caplog):
    from src.security import jwt_handler as jh

    monkeypatch.setenv("LIUHAO_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("LIUHAO_APP_ENV_FILE", raising=False)
    secret = "legacy-plain-jwt-secret"
    path = _write_app_env(tmp_path, secret)
    monkeypatch.setenv(jh.JWT_SECRET_ENV, secret)

    def _boom(*args, **kwargs):
        raise RuntimeError("dpapi unavailable")

    monkeypatch.setattr(jh, "wrap_machine_bound", _boom)
    with caplog.at_level("WARNING"):
        handler = jh._handler_from_env()
    assert handler is not None, "迁移失败不得影响本次启动/登录"
    assert secret in path.read_text(encoding="utf-8"), "迁移失败时不得破坏原文件"
    assert any("迁移失败" in rec.message for rec in caplog.records)


@WINDOWS_ONLY
def test_cross_machine_does_not_resign_legacy_identity_rows(
    tmp_path, monkeypatch, caplog
):
    monkeypatch.delenv(ik.INTEGRITY_KEY_ENV, raising=False)
    monkeypatch.delenv(ik.IDENTITY_FILE_ENV, raising=False)
    old_key = ik.ensure_integrity_key(data_dir=tmp_path)
    registry = tmp_path / "config" / "human_identities.json"
    registry.write_text(
        json.dumps({"humans": [{"principal": "legacy", "role": "admin"}]}),
        encoding="utf-8",
    )

    _simulate_dpapi_unprotect_failure(monkeypatch)
    with caplog.at_level("WARNING"):
        new_key = ik.ensure_integrity_key(data_dir=tmp_path)
    assert new_key != old_key
    rows = json.loads(registry.read_text(encoding="utf-8"))["humans"]
    assert "integrity" not in rows[0], "拷来的未签名历史行不得被新密钥洗白补签"
    assert any("跳过" in rec.message for rec in caplog.records)


# --------------------------------------------------------------------------- #
# JWT 签名密钥（app.env）
# --------------------------------------------------------------------------- #

def _write_app_env(root, secret: str):
    config = root / "config"
    config.mkdir(parents=True, exist_ok=True)
    path = config / "app.env"
    path.write_text(
        "APP_ENV=production\nLOG_LEVEL=info\nLIUHAO_JWT_SECRET=" + secret + "\n",
        encoding="utf-8",
    )
    return path


@WINDOWS_ONLY
def test_plaintext_jwt_secret_is_wrapped_on_first_start(tmp_path, monkeypatch):
    from src.security import jwt_handler as jh

    monkeypatch.setenv("LIUHAO_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("LIUHAO_APP_ENV_FILE", raising=False)
    secret = "legacy-plain-jwt-secret-0123456789abcdef"
    path = _write_app_env(tmp_path, secret)
    monkeypatch.setenv(jh.JWT_SECRET_ENV, secret)

    handler = jh._handler_from_env()
    assert handler is not None
    text = path.read_text(encoding="utf-8")
    assert "LIUHAO-DPAPI-1:" in text, "落盘必须换成带版本头的包裹格式"
    assert secret not in text, "迁移后不得残留明文 JWT 密钥"

    # 包裹后的值在本机仍能正常解出并签发/校验令牌。
    line = next(
        item for item in text.splitlines() if item.startswith("LIUHAO_JWT_SECRET=")
    )
    wrapped = line.split("=", 1)[1]
    monkeypatch.setenv(jh.JWT_SECRET_ENV, wrapped)
    reloaded = jh._handler_from_env()
    token, _payload = reloaded.create_token("alice")
    assert reloaded.validate_token(token).sub == "alice"


@WINDOWS_ONLY
def test_cross_machine_jwt_secret_warns_chinese_and_keeps_file(
    tmp_path, monkeypatch, caplog
):
    from src.security import jwt_handler as jh

    monkeypatch.setenv("LIUHAO_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("LIUHAO_APP_ENV_FILE", raising=False)
    secret = "legacy-plain-jwt-secret-0123456789abcdef"
    path = _write_app_env(tmp_path, secret)
    monkeypatch.setenv(jh.JWT_SECRET_ENV, secret)
    jh._handler_from_env()  # 先迁移成包裹格式

    text = path.read_text(encoding="utf-8")
    wrapped = next(
        item for item in text.splitlines() if item.startswith("LIUHAO_JWT_SECRET=")
    ).split("=", 1)[1]
    monkeypatch.setenv(jh.JWT_SECRET_ENV, wrapped)
    before = text
    _simulate_dpapi_unprotect_failure(monkeypatch)

    jh._default_handler = None
    with caplog.at_level("WARNING"):
        handler = jh.get_jwt_handler()
    assert handler is not None, "解不开时仍应能启动（用本次进程临时密钥）"
    assert path.read_text(encoding="utf-8") == before, "不得覆盖旧密文"
    messages = "\n".join(rec.message for rec in caplog.records)
    assert "无法在此机器解密" in messages
    assert "账号备份包" in messages


# --------------------------------------------------------------------------- #
# 备份 / 导出排除
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "relpath",
    [
        "config/model_config.key",
        "config/model_config.key.1234.tmp",
        "config/.model_config.key.migrate-1234-abcd.tmp",
        "config/integrity.key",
        "config/integrity.key.unreadable-20261108",
        "config/.integrity.key.migrate-1234-abcd.tmp",
        "config/app.env",
        "config/app.env.bak",
        "config/.app.env.migrate-1234-abcd.tmp",
        "config/auth_secrets.json",
        "config/human_identities.json",
        "data/audit_chain.key",
    ],
)
def test_backup_protection_covers_new_and_temp_forms(relpath):
    assert is_protected_state_file(relpath)


def test_upgrade_data_backup_contains_no_machine_bound_key(tmp_path):
    from src.upgrade.backups import backup_data

    data_dir = tmp_path / "data"
    config = data_dir / "config"
    config.mkdir(parents=True)
    (config / "model_config.key").write_bytes(
        b"LIUHAO-DPAPI-1:" + b"ZmFrZS1kcGFwaS1ibG9i"
    )
    (config / "integrity.key").write_bytes(
        b"LIUHAO-DPAPI-1:" + b"ZmFrZS1kcGFwaS1ibG9i"
    )
    (config / "app.env").write_text(
        "LIUHAO_JWT_SECRET=LIUHAO-JWT-SECRET-DPAPI-1:abc\n", encoding="utf-8"
    )
    (data_dir / "notes.txt").write_text("hello", encoding="utf-8")

    dest = tmp_path / "backup"
    result = backup_data(tmp_path, data_dir, [], dest)
    manifest = json.loads((dest / "data-backup.json").read_text(encoding="utf-8"))
    rels = {entry["relpath"] for entry in manifest["entries"]}
    assert rels == {"notes.txt"}
    assert result.file_count == 1
    assert not (dest / "data" / "config").exists()


def test_account_backup_workspace_scan_skips_protected_files(tmp_path, monkeypatch):
    from src.gateway import account_backup

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "model_config.key").write_bytes(b"LIUHAO-DPAPI-1:abc")
    (workspace / "app.env").write_text("LIUHAO_JWT_SECRET=x\n", encoding="utf-8")
    (workspace / "normal.txt").write_text("ok", encoding="utf-8")

    monkeypatch.setenv("LIUHAO_WORKSPACE_ROOT", str(workspace))
    monkeypatch.setattr(
        account_backup, "_artifact_owner_index", lambda: {}
    )
    entries, _total, _skipped = account_backup._collect_files(
        principals=["root"], include_public=True
    )
    paths = {entry["path"] for entry in entries}
    assert paths == {"normal.txt"}
