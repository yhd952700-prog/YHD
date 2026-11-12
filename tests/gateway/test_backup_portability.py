"""回归：打包带走后"还能继续干活"，不只是数据在。

覆盖两条跨机硬要求：

* 模型 Key：源机主密钥与目标机不同时，导出的 Key 仍能在新机重新加密并可用；
* 员工：整实体随包带走，新机（已有种子员工）合并导入后可继续读写。
"""

from __future__ import annotations

import sqlite3

SOURCE_KEY = "sk-original-abcdef123456"


def _raw_model_rows(db_path) -> list:
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute("SELECT * FROM ai_model_configs")]


def test_model_key_portable_across_different_master_keys(monkeypatch, tmp_path):
    import src.ai.model_config as model_config

    source_db = tmp_path / "source.sqlite3"
    monkeypatch.setenv("LIUHAO_MODEL_CONFIG_KEY", "source-master-key")
    monkeypatch.setenv("LIUHAO_MODEL_CONFIG_DB", str(source_db))
    monkeypatch.delenv("LIUHAO_MODEL_CONFIG_KEY_FILE", raising=False)

    source = model_config.ModelConfigStore()
    created = source.create(
        {"name": "root-key", "provider": "mock", "model": "mock-1", "api_key": SOURCE_KEY},
        "root1",
        owner_principal="root1",
    )
    rows = _raw_model_rows(source_db)
    assert rows and rows[0]["api_key_enc"]

    secrets = source.export_plaintext_secrets(rows)
    assert secrets == {created["id"]: SOURCE_KEY}

    target_db = tmp_path / "target.sqlite3"
    monkeypatch.setenv("LIUHAO_MODEL_CONFIG_KEY", "target-master-key")
    monkeypatch.setenv("LIUHAO_MODEL_CONFIG_DB", str(target_db))
    target = model_config.ModelConfigStore()
    target.import_encrypted_rows(rows)
    # 主密钥不同：原密文在新机解不开，导入端会丢弃这段无效密文
    # （missing）或保留并标 needs_reentry；关键是下面的重加密必须救回来。
    assert target.get_public(created["id"])["api_key_status"] in ("missing", "needs_reentry")

    assert target.import_plaintext_secrets(secrets) == 1
    public = target.get_public(created["id"])
    assert public["api_key_status"] == "ok"
    assert public["api_key_masked"].endswith(SOURCE_KEY[-4:])


def test_model_key_vault_round_trip_without_plaintext_in_payload(monkeypatch, tmp_path):
    """包内只有口令包裹的密文；同一口令在目标机仍能解出原 Key。"""
    import json

    import src.ai.model_config as model_config
    from src.gateway.account_backup import _open_model_secrets, _seal_model_secrets

    monkeypatch.setenv("LIUHAO_MODEL_CONFIG_KEY", "vault-source-key")
    monkeypatch.setenv("LIUHAO_MODEL_CONFIG_DB", str(tmp_path / "vault.sqlite3"))
    monkeypatch.delenv("LIUHAO_MODEL_CONFIG_KEY_FILE", raising=False)
    source = model_config.ModelConfigStore()
    created = source.create(
        {"name": "root-key", "provider": "mock", "model": "mock-1", "api_key": SOURCE_KEY},
        "root1",
        owner_principal="root1",
    )

    vault, count = _seal_model_secrets(source, _raw_model_rows(tmp_path / "vault.sqlite3"),
                                       "bundle-passphrase-123")
    assert count == 1
    # 明文 Key 不出现在包的任何字段里。
    assert SOURCE_KEY not in json.dumps(vault, ensure_ascii=False)
    assert _open_model_secrets(vault, "bundle-passphrase-123") == {created["id"]: SOURCE_KEY}
    # 口令不对时按"未携带"处理，不抛错、不泄露。
    assert _open_model_secrets(vault, "wrong-passphrase") == {}


def test_employees_survive_cross_machine(monkeypatch, tmp_path):
    from src.ai.employee_store import EmployeeStore

    monkeypatch.setenv("LIUHAO_MODEL_CONFIG_DB", str(tmp_path / "models.sqlite3"))
    monkeypatch.setenv("LIUHAO_MODEL_CONFIG_KEY", "unit-test-master-key")

    monkeypatch.setenv("LIUHAO_WORKSPACE_ROOT", str(tmp_path / "source" / "workspace"))
    source = EmployeeStore()
    source.create_employee(
        "member-writer", owner_account_id="member1", role_description="成员负责整理日报"
    )
    source.create_employee("other-writer", owner_account_id="member2")
    exported = source.export_employees({"member1"})
    exported_names = [item["name"] for item in exported]
    # 新机会自动播种内置角色，内置角色也归属 member1，因此随包导出是正常的；
    # 关键约束是「只导出该账号的员工」。
    assert "member-writer" in exported_names
    assert "other-writer" not in exported_names
    assert {item.get("owner_account_id") for item in exported} == {"member1"}

    monkeypatch.setenv("LIUHAO_WORKSPACE_ROOT", str(tmp_path / "target" / "workspace"))
    target = EmployeeStore()
    target.create_employee("liuhao-default")  # 新机启动会先种一个默认员工
    assert target.import_employees(exported) == {"restored": len(exported), "skipped": 0}
    restored = target.load("member-writer")
    assert restored is not None
    assert restored.owner_account_id == "member1"
    assert restored.role_description == "成员负责整理日报"
    # 同名员工已存在时不覆盖现场数据。
    assert target.import_employees(exported) == {"restored": 0, "skipped": len(exported)}
