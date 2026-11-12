"""回滚数据形态判据的回归测试。

这些用例锁住三件事：
1. **可逆的密钥形态**必须被判为 ``downgrade`` 且能真的降级回旧版可读；
2. **不可逆的数据版本**（schema 高于目标且备份也不会恢复）必须 ``refuse``；
3. **响亮降级**（审计链算法、向量维度）判 ``direct`` 但必须带出告警，
   不能当成“完全没事”，因为旧版会用不同语义继续运行。
"""

from __future__ import annotations

import sqlite3

import pytest

from src.ai.model_config import KEY_DPAPI_PURPOSE
from src.common.integrity_key import INTEGRITY_DPAPI_PURPOSE
from src.security.encryption import wrap_machine_bound
from src.security.jwt_handler import JWT_SECRET_DPAPI_PURPOSE
from src.upgrade.compat import (
    CompatLevel,
    apply_rollback_downgrades,
    assess_rollback,
    check_startup_compat,
)
from src.upgrade.keyformat import KEY_FORMAT_PLAINTEXT, detect_key_format


def _wrapped(data: bytes, purpose) -> str:
    return wrap_machine_bound(data, purpose=purpose).decode("utf-8")


def _by_component(report, name: str):
    return next(f for f in report.findings if f.component == name)


def _write_schema(data_dir, version: int) -> None:
    import json

    payload = {
        "schema": "liuhao.data-schema/1",
        "version": version,
        "written_by": "0.1.2",
        "updated_at": "2026-11-09T00:00:00Z",
    }
    (data_dir / "data-schema.json").write_text(json.dumps(payload), encoding="utf-8")


def test_missing_schema_marker_is_direct(tmp_path):
    report = assess_rollback(tmp_path, target_version="0.1.1", target_schema=1)
    finding = _by_component(report, "data_schema")
    assert finding.level is CompatLevel.DIRECT
    assert "结构性缺口" in finding.detail


def test_newer_schema_without_restore_refuses(tmp_path):
    _write_schema(tmp_path, 2)
    report = assess_rollback(tmp_path, target_version="0.1.1", target_schema=1)
    assert report.level is CompatLevel.REFUSE
    assert "只支持 v1" in _by_component(report, "data_schema").detail


def test_newer_schema_with_backup_restore_is_direct(tmp_path):
    _write_schema(tmp_path, 2)
    backup = tmp_path / "backup"
    (backup / "data").mkdir(parents=True)
    (backup / "data" / "data-schema.json").write_text("{}", encoding="utf-8")
    report = assess_rollback(
        tmp_path, target_version="0.1.1", target_schema=1, backup_dir=backup
    )
    assert report.level is CompatLevel.DIRECT


def test_newer_schema_with_legacy_backup_clears_stale_marker(tmp_path):
    _write_schema(tmp_path, 2)
    backup = tmp_path / "backup"
    (backup / "data").mkdir(parents=True)
    report = assess_rollback(
        tmp_path, target_version="0.1.1", target_schema=1, backup_dir=backup
    )
    finding = _by_component(report, "data_schema")
    assert finding.level is CompatLevel.DOWNGRADE
    assert finding.action == "data_schema_marker"

    apply_rollback_downgrades(report, tmp_path)
    assert not (tmp_path / "data-schema.json").exists()


def test_wrapped_integrity_key_downgrades_to_plaintext(tmp_path):
    key_dir = tmp_path / "config"
    key_dir.mkdir()
    original = "ab" * 32
    (key_dir / "integrity.key").write_text(
        _wrapped(original.encode(), INTEGRITY_DPAPI_PURPOSE), encoding="utf-8"
    )

    report = assess_rollback(tmp_path, target_version="0.1.1", target_schema=1)
    finding = _by_component(report, "integrity_key")
    assert finding.level is CompatLevel.DOWNGRADE
    assert finding.action == "integrity_key"

    results = apply_rollback_downgrades(report, tmp_path)
    assert results[0]["changed"] is True
    assert detect_key_format(tmp_path) == KEY_FORMAT_PLAINTEXT
    assert (key_dir / "integrity.key").read_text(encoding="utf-8").strip() == original


def test_wrapped_model_key_restores_raw_32_bytes(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    key_bytes = bytes(range(32))
    (data / "model_config.key").write_bytes(
        wrap_machine_bound(key_bytes, purpose=KEY_DPAPI_PURPOSE)
    )

    report = assess_rollback(tmp_path, target_version="0.1.1", target_schema=1)
    finding = _by_component(report, "model_config_key")
    assert finding.level is CompatLevel.DOWNGRADE

    apply_rollback_downgrades(report, tmp_path)
    assert (data / "model_config.key").read_bytes() == key_bytes


def test_wrapped_app_env_secrets_become_plaintext(tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    jwt_secret = "j" * 64
    app_secret = "s" * 32
    (config / "app.env").write_text(
        "# comment\n"
        f"LIUHAO_JWT_SECRET={_wrapped(jwt_secret.encode(), JWT_SECRET_DPAPI_PURPOSE)}\n"
        f"SECRET_KEY={_wrapped(app_secret.encode(), b'secret-key-v1')}\n"
        "LIUHAO_OTHER=keep-me\n",
        encoding="utf-8",
    )

    report = assess_rollback(tmp_path, target_version="0.1.1", target_schema=1)
    finding = _by_component(report, "app_env_secrets")
    assert finding.level is CompatLevel.DOWNGRADE
    assert set(finding.data["wrapped_keys"]) == {"LIUHAO_JWT_SECRET", "SECRET_KEY"}

    apply_rollback_downgrades(report, tmp_path)
    lines = (config / "app.env").read_text(encoding="utf-8").splitlines()
    assert f"LIUHAO_JWT_SECRET={jwt_secret}" in lines
    assert f"SECRET_KEY={app_secret}" in lines
    assert "LIUHAO_OTHER=keep-me" in lines


def test_hmac_audit_chain_is_direct_but_degrades_loudly(tmp_path):
    conn = sqlite3.connect(tmp_path / "audit_store.db")
    conn.execute(
        "CREATE TABLE audit_events (seq INTEGER PRIMARY KEY, hash_alg TEXT)"
    )
    conn.execute("INSERT INTO audit_events (hash_alg) VALUES ('sha256')")
    conn.execute("INSERT INTO audit_events (hash_alg) VALUES ('hmac-sha256')")
    conn.commit()
    conn.close()

    report = assess_rollback(tmp_path, target_version="0.1.1", target_schema=1)
    finding = _by_component(report, "audit_chain")
    assert finding.level is CompatLevel.DIRECT
    assert finding.data["keyed"] == 1
    assert "响亮降级" in finding.degraded


def test_vector_without_dimension_marker_is_direct_with_warning(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    conn = sqlite3.connect(data / "knowledge_vectors.sqlite3")
    conn.execute("CREATE TABLE knowledge_vectors (doc_id TEXT, embedding TEXT)")
    conn.execute("INSERT INTO knowledge_vectors VALUES ('d', '[1,0,0]')")
    conn.commit()
    conn.close()

    report = assess_rollback(tmp_path, target_version="0.1.1", target_schema=1)
    finding = _by_component(report, "vector_index")
    assert finding.level is CompatLevel.DIRECT
    assert "静默" in finding.degraded


def test_startup_compat_blocks_older_binary_reading_wrapped_key(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "model_config.key").write_bytes(
        wrap_machine_bound(b"k" * 32, purpose=KEY_DPAPI_PURPOSE)
    )

    assert check_startup_compat(tmp_path, "0.1.1") is not None
    assert check_startup_compat(tmp_path, "0.1.2") is None


def test_rollback_refuses_before_touching_code(tmp_path, monkeypatch):
    """判 REFUSE 时：绝不恢复旧版代码，且判定结果必须写进 journal。"""
    from src.upgrade import manager as manager_mod
    from src.upgrade.compat import CompatFinding, CompatReport, DataCompatError

    refusal = CompatReport(
        target_version="0.1.1",
        target_schema=1,
        findings=[
            CompatFinding(
                component="data_schema",
                title="数据 schema 超前且备份无法恢复",
                level=CompatLevel.REFUSE,
                detail="测试构造的阻断项",
                files=[],
                action="",
                degraded="",
                data={},
            )
        ],
    )
    monkeypatch.setattr(manager_mod, "assess_rollback", lambda *a, **k: refusal)

    app_root = tmp_path / "app"
    data_dir = tmp_path / "data"
    app_root.mkdir()
    data_dir.mkdir()
    (app_root / "VERSION").write_text("0.1.2\n", encoding="utf-8")

    mgr = manager_mod.UpgradeManager(
        manager_mod.UpgradeConfig(
            app_root=app_root,
            data_dir=data_dir,
            backup_root=tmp_path / "backups",
            state_dir=tmp_path / "state",
        )
    )
    monkeypatch.setattr(mgr, "_resolve_backup_id", lambda *a, **k: "b1")
    monkeypatch.setattr(
        mgr,
        "_read_backup",
        lambda *a, **k: {"from_version": "0.1.1", "to_version": "0.1.2"},
    )
    monkeypatch.setattr(mgr, "current_version", lambda: ("0.1.2", None))
    monkeypatch.setattr(mgr, "_set_status", lambda *a, **k: None)
    restored = {"called": False}
    monkeypatch.setattr(
        mgr,
        "_restore_backup",
        lambda *a, **k: restored.__setitem__("called", True),
    )

    with pytest.raises(DataCompatError):
        mgr.rollback()

    # 阻断发生在恢复备份之前：代码与数据都没有被碰过。
    assert restored["called"] is False
    assert (app_root / "VERSION").read_text(encoding="utf-8").strip() == "0.1.2"

    records = mgr.ledger.list_records(kind="rollback", limit=5)
    assert records, "拒绝也必须留痕，否则用户查不到这次尝试"
    assert records[0]["status"] == "failed"
    assert records[0]["detail"]["data_compat"]["level"] == "refuse"
    assert records[0]["detail"]["blocked"] is True
