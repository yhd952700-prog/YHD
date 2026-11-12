"""跨机备份：三把机器绑定密钥的导出解包 / 恢复重包。

为什么单独测这一层
------------------
上一轮加固把 ``model_config.key``、``integrity.key`` 和 ``app.env`` 里的
``LIUHAO_JWT_SECRET`` 变成了 DPAPI 本机密文。直接把数据目录拷到别的机器
确实解不开；但如果口令加密的迁移包原样封进这些密文，换机恢复后同样解不开
——「能换机」这条产品承诺就被加固悄悄破坏了。

这里的用例走完整链路：真实数据目录 → 加密归档 → 解出的归档 JSON →
模拟另一台机器（全新目标目录）→ 恢复落盘 → 用目标机 DPAPI 解封校验。
"""
from __future__ import annotations

import base64
import gzip
import json
import os
import sqlite3
from pathlib import Path

import pytest

from src.backup import backup_manager as backup_mod
from src.cli.backup_commands import BackupManager, _open_archive
from src.security import encryption

MODEL_KEY = bytes(range(32))
INTEGRITY_KEY = "a" * 64
JWT_SECRET = "jwt-测试-secret-001"
SECRET_KEY_VALUE = "secret-key-测试-002"
PASSWORD = "换机口令"


def _wrap(data: bytes, purpose: bytes) -> bytes:
    return encryption.wrap_machine_bound(data, purpose=purpose)


def _seed_source(data_dir: Path) -> None:
    """造一份和真实数据目录同形的源数据（含三把密钥与运行痕迹）。"""
    config = data_dir / "config"
    config.mkdir(parents=True)
    nested = data_dir / "data"
    nested.mkdir()

    (nested / "model_config.key").write_bytes(
        _wrap(MODEL_KEY, backup_mod._dpapi_purpose_for("model_config.key"))
    )
    (config / "integrity.key").write_bytes(
        _wrap(INTEGRITY_KEY.encode("utf-8"), backup_mod._dpapi_purpose_for("integrity.key"))
    )
    wrapped_jwt = _wrap(JWT_SECRET.encode("utf-8"), backup_mod._jwt_dpapi_purpose())
    wrapped_secret_key = _wrap(
        SECRET_KEY_VALUE.encode("utf-8"), backup_mod._env_secret_purpose("SECRET_KEY")
    )
    (config / "app.env").write_text(
        "APP_ENV=production\n"
        f"LIUHAO_JWT_SECRET={wrapped_jwt.decode('ascii')}\n"
        f"SECRET_KEY={wrapped_secret_key.decode('ascii')}\n",
        encoding="utf-8",
    )
    (config / "auth_secrets.json").write_text('{"root1": "hash"}', encoding="utf-8")
    (config / "human_identities.json").write_text(
        '{"humans": [{"id": "root1"}]}', encoding="utf-8"
    )
    (nested / "model_registry.sqlite3").write_bytes(b"api-key-ciphertext")

    workspace = data_dir / "workspace"
    workspace.mkdir()
    (workspace / "liuhao_employees.json").write_text(
        '{"employees": [{"id": "emp-1"}]}', encoding="utf-8"
    )

    # 真实 SQLite + 一个 WAL sidecar：sidecar 绝不能单独进包。
    with sqlite3.connect(data_dir / "memory_store.db") as connection:
        connection.execute("CREATE TABLE t (v INTEGER)")
        connection.execute("INSERT INTO t VALUES (1)")
    (data_dir / "memory_store.db-wal").write_bytes(b"stale-wal-bytes")

    (data_dir / "logs").mkdir()
    (data_dir / "logs" / "liuhao.log").write_text("old path", encoding="utf-8")
    (data_dir / "diagnostics").mkdir()
    (data_dir / "diagnostics" / "diag.zip").write_bytes(b"PK")
    (data_dir / "notes.txt").write_text("keep me", encoding="utf-8")


def _decrypted_payload(created: dict) -> dict:
    raw = _open_archive(Path(created["file_path"]), password=PASSWORD)
    return json.loads(gzip.decompress(raw).decode("utf-8"))


def _entry(payload: dict, rel: str) -> dict:
    files = payload["data"]["user_data"]["files"]
    assert rel in files, f"{rel} missing from migration archive"
    return files[rel]


def _content(entry: dict) -> bytes:
    return base64.b64decode(entry["content_b64"])


@pytest.fixture
def source_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "old-machine" / "data"
    _seed_source(directory)
    return directory


@pytest.fixture
def manager(tmp_path: Path, source_dir: Path) -> BackupManager:
    return BackupManager(
        backup_dir=str(tmp_path / "backups"), data_dir=str(source_dir)
    )


def test_export_contains_raw_key_material_not_dpapi_ciphertext(manager):
    created = manager.create_backup(encrypted=True, password=PASSWORD)
    payload = _decrypted_payload(created)
    text = json.dumps(payload, ensure_ascii=False)

    # 包里绝不出现两种机器绑定密文形态。
    assert encryption.DPAPI_PREFIX not in text
    assert encryption.RAW_PREFIX not in text

    model_entry = _entry(payload, "data/model_config.key")
    assert _content(model_entry) == MODEL_KEY
    assert model_entry["machine_bound"] == {"file": "model_config.key"}

    integrity_entry = _entry(payload, "config/integrity.key")
    assert _content(integrity_entry) == INTEGRITY_KEY.encode("utf-8")
    assert integrity_entry["machine_bound"] == {"file": "integrity.key"}

    app_env = _content(_entry(payload, "config/app.env")).decode("utf-8")
    assert f"LIUHAO_JWT_SECRET={JWT_SECRET}" in app_env
    assert f"SECRET_KEY={SECRET_KEY_VALUE}" in app_env
    assert _entry(payload, "config/app.env")["machine_bound"] == {
        "env": ["LIUHAO_JWT_SECRET", "SECRET_KEY"]
    }

    # 包里绝不出现的运行痕迹与 SQLite sidecar。
    assert "logs/liuhao.log" not in text
    assert "diagnostics/diag.zip" not in text
    assert "memory_store.db-wal" not in text
    assert created["excluded_runtime_files"] >= 2


def test_cross_machine_restore_rewraps_with_target_dpapi(manager, tmp_path, monkeypatch):
    """换数据目录 = 模拟另一台机器：恢复后必须重新绑定目标机。"""
    created = manager.create_backup(encrypted=True, password=PASSWORD)
    target = tmp_path / "new-machine" / "data"

    # 记录恢复期间目标机侧的包裹调用：证明三把密钥都经过目标机 DPAPI。
    purposes = []
    real_wrap = encryption.wrap_machine_bound

    def _spy(data, *, purpose=b"generic"):
        purposes.append(purpose)
        return real_wrap(data, purpose=purpose)

    monkeypatch.setattr(encryption, "wrap_machine_bound", _spy)

    result = manager.restore_backup(
        created["backup_id"], target=str(target), password=PASSWORD
    )
    assert result["status"] == "restored"
    assert set(purposes) >= {
        backup_mod._dpapi_purpose_for("model_config.key"),
        backup_mod._dpapi_purpose_for("integrity.key"),
        backup_mod._jwt_dpapi_purpose(),
        backup_mod._env_secret_purpose("SECRET_KEY"),
    }

    model_file = target / "data" / "model_config.key"
    raw = model_file.read_bytes()
    assert encryption.is_machine_bound_payload(raw)
    plain, _mode = encryption.unwrap_machine_bound(
        raw, purpose=backup_mod._dpapi_purpose_for("model_config.key")
    )
    assert plain == MODEL_KEY
    if os.name == "nt":
        assert raw.startswith(encryption.DPAPI_PREFIX.encode("ascii"))

    integrity_file = target / "config" / "integrity.key"
    integrity_raw = integrity_file.read_bytes()
    assert encryption.is_machine_bound_payload(integrity_raw)
    integrity_plain, _mode = encryption.unwrap_machine_bound(
        integrity_raw, purpose=backup_mod._dpapi_purpose_for("integrity.key")
    )
    assert integrity_plain.decode("utf-8") == INTEGRITY_KEY

    app_env = (target / "config" / "app.env").read_text(encoding="utf-8")
    line = next(
        item for item in app_env.splitlines() if item.startswith("LIUHAO_JWT_SECRET=")
    )
    value = line.split("=", 1)[1]
    assert encryption.is_machine_bound_payload(value)
    jwt_plain, _mode = encryption.unwrap_machine_bound(
        value.encode("utf-8"), purpose=backup_mod._jwt_dpapi_purpose()
    )
    assert jwt_plain.decode("utf-8") == JWT_SECRET
    # SECRET_KEY 与 JWT 一样：包内是原始材料，恢复时用目标机 DPAPI 重包。
    secret_line = next(
        item for item in app_env.splitlines() if item.startswith("SECRET_KEY=")
    )
    secret_value = secret_line.split("=", 1)[1]
    assert encryption.is_machine_bound_payload(secret_value)
    secret_plain, _mode = encryption.unwrap_machine_bound(
        secret_value.encode("utf-8"),
        purpose=backup_mod._env_secret_purpose("SECRET_KEY"),
    )
    assert secret_plain.decode("utf-8") == SECRET_KEY_VALUE

    # 登录/身份/员工数据随包恢复，换机后能接着用。
    assert (target / "config" / "auth_secrets.json").is_file()
    assert (target / "config" / "human_identities.json").is_file()
    assert (target / "workspace" / "liuhao_employees.json").is_file()


def test_restore_failure_leaves_existing_files_untouched(
    manager, source_dir, monkeypatch
):
    """目标机 DPAPI 不可用：中文报错，且不覆盖任何已有文件。"""
    created = manager.create_backup(encrypted=True, password=PASSWORD)
    notes = source_dir / "notes.txt"
    notes.write_text("newer-local-value", encoding="utf-8")
    key_before = (source_dir / "data" / "model_config.key").read_bytes()

    def _unavailable(*_args, **_kwargs):
        raise encryption.MachineBoundProtectionUnavailable()

    monkeypatch.setattr(encryption, "wrap_machine_bound", _unavailable)

    with pytest.raises(ValueError, match="目标机无法使用 Windows DPAPI"):
        manager.restore_backup(created["backup_id"], password=PASSWORD)

    assert notes.read_text(encoding="utf-8") == "newer-local-value"
    assert (source_dir / "data" / "model_config.key").read_bytes() == key_before


def test_export_failure_when_key_undecryptable(manager, monkeypatch):
    """原机密文解不开时拒绝导出，不写出一份换机也用不了的包。"""
    def _cross_machine(*_args, **_kwargs):
        raise encryption.MachineBoundKeyError("cross-machine")

    monkeypatch.setattr(encryption, "unwrap_machine_bound", _cross_machine)

    with pytest.raises(ValueError, match="无法生成可跨机器恢复的备份包"):
        manager.create_backup(encrypted=True, password=PASSWORD)

    assert not list(Path(manager._backup_dir).glob("backup_*"))


def test_plaintext_archive_still_excludes_all_credentials(manager):
    """未加密归档不用于换机：凭据/密钥文件一个都不能出现。"""
    created = manager.create_backup()
    assert created["encrypted"] is False
    payload = json.loads(
        gzip.decompress(Path(created["file_path"]).read_bytes()).decode("utf-8")
    )
    text = json.dumps(payload, ensure_ascii=False)
    for name in (
        "model_config.key",
        "integrity.key",
        "app.env",
        "auth_secrets.json",
        "human_identities.json",
        "model_registry.sqlite3",
    ):
        assert name not in text, f"{name} leaked into plaintext archive"
    assert encryption.DPAPI_PREFIX not in text
