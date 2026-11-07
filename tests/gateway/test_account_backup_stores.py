"""账号备份必须真的"带走全部"：记忆库与会话库都要进包、能落回新实例。

闭环证据（同一进程内模拟两台实例）：
成员写一条记忆 + 发两条对话 → 根全量导出 → 全新实例导入 →
记忆与会话条数一致、可读、可搜，且清单写明两个库与各自的条数。
"""

from __future__ import annotations

import sqlite3

import pytest

PASSPHRASE = "backup-passphrase-123"


@pytest.fixture()
def _env(monkeypatch, tmp_path):
    """每个"实例"一套独立路径；切换目录即切换实例。"""
    import src.ai.conversation_store as conversation_module
    import src.kernels.identity as identity_module

    def apply(prefix: str) -> dict:
        base = tmp_path / prefix
        base.mkdir(parents=True, exist_ok=True)
        values = {
            "LIUHAO_HUMAN_IDENTITIES_BACKEND": "file",
            "LIUHAO_HUMAN_IDENTITIES_FILE": str(base / "identities.json"),
            "LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY": f"{prefix}-integrity-key",
            "LIUHAO_AUTH_SECRETS_FILE": str(base / "auth_secrets.json"),
            "LIUHAO_ACCOUNT_SETTINGS_FILE": str(base / "account_settings.json"),
            "LIUHAO_ACCOUNT_BACKUP_DB": str(base / "account_backup.sqlite3"),
            "LIUHAO_ACCOUNT_MEMORY_DB": str(base / "account_memory.sqlite3"),
            "LIUHAO_ACCOUNT_USAGE_DB": str(base / "account_usage.sqlite3"),
            "CONVERSATION_DB_PATH": str(base / "conversation_store.db"),
            "LIUHAO_WORKSPACE_ROOT": str(base / "workspace"),
            "LIUHAO_MODEL_CONFIG_DB": str(base / "model_config.sqlite3"),
            "LIUHAO_MODEL_CONFIG_KEY_FILE": str(base / "model_config_key.bin"),
        }
        for key, value in values.items():
            monkeypatch.setenv(key, value)
        # 单例按"当时"的环境构造；切换实例必须同时复位。会话库默认路径在
        # 模块导入时求值，测试里改为显式构造，等价于真实进程启动时的环境。
        monkeypatch.setattr(identity_module, "_global_manager", None)
        monkeypatch.setattr(
            conversation_module,
            "_conversation_store",
            conversation_module.ConversationStore(values["CONVERSATION_DB_PATH"]),
        )
        return values

    return apply


def test_backup_carries_memory_and_conversations_across_instances(_env):
    from src.gateway.account_backup import export_bundle, import_bundle

    # ── 实例 A：建立账号树，成员写记忆、发对话 ──────────────────────────
    _env("source")
    from src.kernels.identity import get_identity_manager

    manager = get_identity_manager()
    manager.register_root_account("root1", permissions={"*"})
    manager.register_self_service_account("member1")

    from src.gateway import account_memory

    with account_memory._connect() as connection:  # noqa: SLF001 - 测试直写库
        connection.execute(
            "INSERT INTO account_memory_entries "
            "(id, owner, text, source, tags, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("mem-1", "member1", "成员偏好：先给结论", "user", "[]", "2026-11-07T10:00:00+00:00"),
        )
        connection.commit()

    from src.ai.conversation_store import get_conversation_store

    store = get_conversation_store()
    # 网关真实会话主体是 liuhao-{账号}-{会话名}，不是裸账号名。
    store.append("liuhao-member1-member-session", 1, "user", "帮我看看这段代码")
    store.append("liuhao-member1-member-session", 1, "assistant", "先给结论：问题在边界判断")
    store.append("liuhao-root1-root-session", 1, "user", "根借成员 Key")

    exported = export_bundle(
        actor="root1",
        scope="all",
        passphrase=PASSPHRASE,
        subtree=manager.subtree_principals("root1"),
        is_root=True,
    )
    manifest_stores = exported["manifest"]["stores"]
    assert exported["manifest"]["member_count"] == 2
    assert manifest_stores["account_memory"]["entries"] == 1
    assert manifest_stores["account_memory"]["owners"] == ["member1"]
    assert manifest_stores["conversation"]["turns"] == 3
    assert manifest_stores["conversation"]["principals"] == ["member1", "root1"]

    # 成员 self 导出只带自己的行。
    member_export = export_bundle(
        actor="member1",
        scope="self",
        passphrase=PASSPHRASE,
        subtree=("member1",),
        is_root=False,
    )
    assert member_export["manifest"]["stores"]["account_memory"]["entries"] == 1
    assert member_export["manifest"]["stores"]["conversation"]["turns"] == 2

    # ── 实例 B：全新空库导入 ────────────────────────────────────────────
    _env("target")
    imported = import_bundle(bundle=exported["bundle"], passphrase=PASSPHRASE)
    assert imported["accounts"] == 2
    assert imported["memory"]["imported"] == 1
    assert imported["conversations"]["imported"] == 3

    # 条数一致、可读、可搜。
    assert account_memory.db_path().endswith("account_memory.sqlite3")
    with sqlite3.connect(account_memory.db_path()) as connection:
        rows = connection.execute(
            "SELECT owner, text FROM account_memory_entries"
        ).fetchall()
    assert rows == [("member1", "成员偏好：先给结论")]
    entries = account_memory._query_entries(("member1",), "结论")  # noqa: SLF001
    assert len(entries) == 1 and entries[0]["account_id"] == "member1"

    from src.ai.conversation_store import get_conversation_store as target_store

    history = target_store().load("liuhao-member1-member-session")
    assert history == [
        {"role": "user", "content": "帮我看看这段代码"},
        {"role": "assistant", "content": "先给结论：问题在边界判断"},
    ]


def test_backup_import_merges_instead_of_overwriting(_env):
    """目标实例已有数据时按主键合并：不覆盖、不重复、如实报数。"""
    from src.gateway.account_backup import _restore_conversations, _restore_memory

    _env("merge")
    from src.gateway import account_memory

    with account_memory._connect() as connection:  # noqa: SLF001
        connection.execute(
            "INSERT INTO account_memory_entries VALUES (?, ?, ?, ?, ?, ?)",
            ("mem-1", "member1", "现场版本", "user", "[]", "2026-11-07T09:00:00+00:00"),
        )
        connection.commit()

    report = _restore_memory(
        {
            "entries": [
                {
                    "id": "mem-1",
                    "owner": "member1",
                    "text": "备份版本",
                    "source": "user",
                    "tags": "[]",
                    "created_at": "2026-11-07T10:00:00+00:00",
                },
                {
                    "id": "mem-2",
                    "owner": "member1",
                    "text": "新增条目",
                    "source": "user",
                    "tags": "[]",
                    "created_at": "2026-11-07T11:00:00+00:00",
                },
            ]
        }
    )
    assert report == {
        "imported": 1,
        "skipped": 1,
        "custody_imported": 0,
        "custody_skipped": 0,
    }
    with sqlite3.connect(account_memory.db_path()) as connection:
        texts = dict(
            connection.execute("SELECT id, text FROM account_memory_entries").fetchall()
        )
    assert texts == {"mem-1": "现场版本", "mem-2": "新增条目"}

    from src.ai.conversation_store import get_conversation_store

    store = get_conversation_store()
    store.append("liuhao-member1-s1", 1, "user", "同一条")
    conversation_report = _restore_conversations(
        {
            "turns": [
                {
                    "principal_id": "liuhao-member1-s1",
                    "turn": 1,
                    "role": "user",
                    "content": "同一条",
                },
                {
                    "principal_id": "liuhao-member1-s1",
                    "turn": 1,
                    "role": "assistant",
                    "content": "新回复",
                },
            ]
        }
    )
    assert conversation_report == {"imported": 1, "skipped": 1}
    assert [item["content"] for item in store.load("liuhao-member1-s1")] == [
        "同一条",
        "新回复",
    ]
