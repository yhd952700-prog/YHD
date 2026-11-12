"""MEM-016：旧库迁移的归属判定与未归属区。

闭环要求：
* 能认出账号的行落到真实账号，**不再默认归根**；
* 认不出账号的行落到 ``__unassigned__``，普通列表看不到，只有根账号能查；
* 迁移报告给出两侧真实条数并能核对守恒（源行 = 导入 + 跳过）。
"""

from __future__ import annotations

import sqlite3
from typing import Dict

import pytest
from fastapi.testclient import TestClient

PASSWORD = "migrationpass123"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    base = tmp_path
    values = {
        "LIUHAO_HUMAN_IDENTITIES_BACKEND": "file",
        "LIUHAO_HUMAN_IDENTITIES_FILE": str(base / "identities.json"),
        "LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY": "account-memory-migration-key",
        "LIUHAO_AUTH_SECRETS_FILE": str(base / "auth_secrets.json"),
        "LIUHAO_ACCOUNT_SETTINGS_FILE": str(base / "account_settings.json"),
        "LIUHAO_ACCOUNT_MEMORY_DB": str(base / "account_memory.sqlite3"),
        "LIUHAO_ACCOUNT_USAGE_DB": str(base / "account_usage.sqlite3"),
        "LIUHAO_ALERTS_STORE_PATH": str(base / "alerts.json"),
        "AUDIT_DB_PATH": str(base / "audit_store.db"),
        "AUDIT_DB_BACKUP_PATH": str(base / "audit_store.db.backup"),
        "LIUHAO_WORKSPACE_ROOT": str(base / "workspace"),
        "LIUHAO_MODEL_CONFIG_DB": str(base / "model_config.sqlite3"),
        "LIUHAO_MODEL_CONFIG_KEY_FILE": str(base / "model_config_key.bin"),
        "LIUHAO_JWT_REVOCATION_DB": str(base / "jwt_revocation.db"),
        "LIUHAO_DATA_DIR": str(base / "data"),
        "LIUHAO_KNOWLEDGE_DB": str(base / "knowledge_vectors.sqlite3"),
        "MEMORY_DB_PATH": str(base / "kernel_memory.db"),
        "PERSONAL_CONTEXT_DB_PATH": str(base / "personal_context.db"),
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


@pytest.fixture()
def client(env):
    from src.gateway.main import get_app

    with TestClient(get_app()) as test_client:
        yield test_client


def _bearer(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _register_root(client: TestClient, principal: str = "root1") -> Dict[str, str]:
    response = client.post(
        "/v1/accounts/register",
        json={"principal": principal, "password": PASSWORD},
    )
    assert response.status_code == 200, response.text
    return _login(client, principal)


def _login(client: TestClient, principal: str) -> Dict[str, str]:
    response = client.post(
        "/v1/auth/login", json={"principal": principal, "secret": PASSWORD}
    )
    assert response.status_code == 200, response.text
    return _bearer(response.json()["access_token"])


def _create_child(client: TestClient, headers: Dict[str, str], principal: str) -> Dict[str, str]:
    created = client.post("/v1/accounts", json={"principal": principal}, headers=headers)
    assert created.status_code == 200, created.text
    activated = client.post(
        "/v1/accounts/activate",
        json={
            "principal": principal,
            "code": created.json()["activation_code"],
            "password": PASSWORD,
        },
    )
    assert activated.status_code == 200, activated.text
    token = activated.json().get("access_token") or activated.json().get("token")
    assert token
    return _bearer(token)


def _seed_kernel_memory(path: str) -> None:
    """5 条源行：2 条可判归属、1 条无法判定、1 条运行时遥测、1 条异常格式。"""
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE memory_entries (entry_id TEXT, key_hash TEXT, key TEXT, "
        "value TEXT, tier TEXT, scope TEXT, created_at TEXT, expires_at TEXT, "
        "tags TEXT)"
    )
    rows = [
        (
            "m-1",
            "turn:liuhao-member1-s1",
            '"成员自己的偏好"',
            "semantic",
            "user",
            "2026-11-07T10:00:00+00:00",
            None,
            '["agent:liuhao-member1-s1", "preference"]',
        ),
        (
            "m-2",
            "turn:liuhao-root1-s1",
            '"根自己的记录"',
            "semantic",
            "user",
            "2026-11-07T10:00:01+00:00",
            None,
            '["agent:liuhao-root1-s1"]',
        ),
        (
            "m-3",
            "preference:answer_style",
            '"先给结论"',
            "semantic",
            "user",
            "2026-11-07T10:00:02+00:00",
            None,
            '["preference"]',
        ),
        (
            "t-1",
            "episodic:run:goal-1",
            '{"goal_id": "goal-1"}',
            "episodic",
            "global",
            "2026-11-07T10:00:03+00:00",
            None,
            '["episodic", "agent_run"]',
        ),
        (
            "bad-1",
            "broken:row",
            "",
            "semantic",
            "user",
            "2026-11-07T10:00:04+00:00",
            None,
            '["preference"]',
        ),
    ]
    connection.executemany(
        "INSERT INTO memory_entries "
        "(entry_id, key, value, tier, scope, created_at, expires_at, tags) "
        "VALUES (?,?,?,?,?,?,?,?)",
        rows,
    )
    connection.commit()
    connection.close()


def test_migration_counts_and_unassigned_visibility(client, env):
    root = _register_root(client)
    member = _create_child(client, root, "member1")

    _seed_kernel_memory(env["MEMORY_DB_PATH"])

    from src.gateway import account_memory

    report = account_memory.ensure_legacy_memory_migrated()

    # ── 逐行去向账：源 5 = 新库 3 + 隔离区 2 + 丢失 0 ───────────────────
    assert report["source_rows"] == 5
    assert report["imported_rows"] == 3
    assert report["merged_rows"] == 0
    assert report["quarantined_rows"] == 2
    assert report["lost_rows"] == 0
    assert report["rows_accounted"] is True
    assert report["target_legacy_rows"] == 3
    assert report["quarantine_rows"] == 2
    assert report["preserved_rows"] == 5
    assert report["unassigned_rows"] == 1
    assert report["skip_reasons"] == {
        "non_memory_telemetry": 1,
        "empty_text": 1,
    }
    # ``merged_into`` 明细为空（首次迁移没有重复 id）；第二遍重跑才会产生。
    assert report["merged_into"] == []

    # ── 归属：能认出的落真实账号，认不出的落未归属 ─────────────────────────
    connection = sqlite3.connect(env["LIUHAO_ACCOUNT_MEMORY_DB"])
    owners = dict(
        connection.execute(
            "SELECT id, owner FROM account_memory_entries ORDER BY id"
        ).fetchall()
    )
    connection.close()
    assert owners["m-1"] == "member1"
    assert owners["m-2"] == "root1"
    assert owners["m-3"] == account_memory._UNASSIGNED_OWNER  # noqa: SLF001

    # ── 未归属：根可查，成员不可见且与"不存在"同形 ──────────────────────────
    root_unassigned = client.get("/v1/memory/unassigned", headers=root)
    assert root_unassigned.status_code == 200, root_unassigned.text
    body = root_unassigned.json()
    assert body["total"] == 1
    assert [entry["text"] for entry in body["entries"]] == ["先给结论"]
    # 隔离区：两条被跳过的源行原文与原因都能在根的未归属清单里看到。
    assert body["quarantined_total"] == 2
    quarantined = {item["source_ref"]: item for item in body["quarantined"]}
    assert set(quarantined) == {"t-1", "bad-1"}
    assert quarantined["t-1"]["reason"] == "non_memory_telemetry"
    assert quarantined["t-1"]["payload"]["key"] == "episodic:run:goal-1"
    assert quarantined["bad-1"]["reason"] == "empty_text"
    assert quarantined["bad-1"]["payload"]["value"] == ""

    member_unassigned = client.get("/v1/memory/unassigned", headers=member)
    assert member_unassigned.status_code == 404

    # ── 普通列表不泄露：根看不到成员与未归属，成员只看到自己的 ─────────────
    root_entries = client.get("/v1/memory/entries", headers=root).json()["entries"]
    root_texts = {entry["text"] for entry in root_entries}
    assert "成员自己的偏好" not in root_texts
    assert "先给结论" not in root_texts
    assert "根自己的记录" in root_texts

    member_entries = client.get("/v1/memory/entries", headers=member).json()["entries"]
    member_texts = {entry["text"] for entry in member_entries}
    assert "成员自己的偏好" in member_texts
    assert "根自己的记录" not in member_texts
    assert "先给结论" not in member_texts


def test_repeated_migration_merges_into_existing_instead_of_dropping(client, env):
    """第二遍迁移：已存在同 id 的条目必须记为 merged 并给出最终正文，不能丢。"""
    root = _register_root(client)
    _create_child(client, root, "member1")
    _seed_kernel_memory(env["MEMORY_DB_PATH"])

    from src.gateway import account_memory

    first = account_memory.ensure_legacy_memory_migrated()
    assert first["imported_rows"] == 3

    second = account_memory.ensure_legacy_memory_migrated(force=True)
    assert second["source_rows"] == 5
    assert second["imported_rows"] == 0
    assert second["merged_rows"] == 3
    assert second["quarantined_rows"] == 2
    assert second["lost_rows"] == 0
    assert second["rows_accounted"] is True
    assert second["quarantine_rows"] == 2
    assert second["preserved_rows"] == 5

    merged = {
        item["entry_id"]: item["final_text"] for item in second["merged_into"]
    }
    assert merged == {
        "m-1": "成员自己的偏好",
        "m-2": "根自己的记录",
        "m-3": "先给结论",
    }
    assert second["merged_into_truncated"] is False


def test_root_can_delete_unassigned_and_quarantine_but_member_cannot(client, env):
    """未归属区与隔离区必须"看得见也处置得掉"：根能删，成员固定 404。

    普通 ``DELETE /entries/{id}`` 对未归属条目仍应 404（它不属于任何账号），
    删除只能走根专属入口；隔离区同理。
    """
    root = _register_root(client)
    member = _create_child(client, root, "member1")
    _seed_kernel_memory(env["MEMORY_DB_PATH"])

    from src.gateway import account_memory

    account_memory.ensure_legacy_memory_migrated()

    body = client.get("/v1/memory/unassigned", headers=root).json()
    entry_id = body["entries"][0]["id"]
    quarantine_id = next(
        item["id"] for item in body["quarantined"] if item["source_ref"] == "t-1"
    )

    # 成员：未归属与隔离区都不可见、不可处置（与不存在同形）。
    assert (
        client.delete(f"/v1/memory/unassigned/{entry_id}", headers=member).status_code
        == 404
    )
    assert (
        client.delete(f"/v1/memory/quarantine/{quarantine_id}", headers=member).status_code
        == 404
    )
    assert client.get("/v1/memory/unassigned", headers=member).status_code == 404
    # 成员看到的 404 必须与"这条记录不存在"逐字一致，否则 404 本身就是
    # 一个"这里确实有东西"的信号。
    member_view = client.get("/v1/memory/unassigned", headers=member)
    missing_entry = client.get("/v1/memory/entries/does-not-exist", headers=member)
    assert member_view.status_code == missing_entry.status_code == 404
    assert member_view.content == missing_entry.content

    # 普通删除入口对未归属条目仍是 404 —— 专用入口才是唯一路径。
    assert client.delete(f"/v1/memory/entries/{entry_id}", headers=root).status_code == 404

    deleted = client.delete(f"/v1/memory/unassigned/{entry_id}", headers=root)
    assert deleted.status_code == 200, deleted.text
    assert deleted.json() == {"id": entry_id, "deleted": True, "removed": 1}

    # 已删除再删：中性 404，不会重复计数。
    assert (
        client.delete(f"/v1/memory/unassigned/{entry_id}", headers=root).status_code == 404
    )

    after = client.get("/v1/memory/unassigned", headers=root).json()
    assert after["total"] == 0
    assert after["quarantined_total"] == 2

    # 隔离区逐条删除：只影响隔离区，不影响已导入的三条记忆。
    removed = client.delete(f"/v1/memory/quarantine/{quarantine_id}", headers=root)
    assert removed.status_code == 200, removed.text
    assert removed.json() == {"id": quarantine_id, "deleted": True, "removed": 1}

    remaining = client.get("/v1/memory/unassigned", headers=root).json()
    assert remaining["quarantined_total"] == 1
    assert {item["source_ref"] for item in remaining["quarantined"]} == {"bad-1"}

    root_entries = client.get("/v1/memory/entries", headers=root).json()["entries"]
    assert {entry["text"] for entry in root_entries} == {"根自己的记录"}
    member_entries = client.get("/v1/memory/entries", headers=member).json()["entries"]
    assert {entry["text"] for entry in member_entries} == {"成员自己的偏好"}

