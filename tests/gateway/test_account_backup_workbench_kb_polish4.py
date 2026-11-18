"""打磨第四轮：凭据包跨机迁移补齐工作台分组/Bot 与企业知识文档。

闭环证据（同一进程内模拟两台实例）：
根全量导出 → 全新实例认领导入 → 分组/Bot/群聊/例程/头像、企业文档/
版本/分块/原件/长期记忆逐项对回；成员合并导入只进自己的数据；
同 id / 同 doc_key 冲突时现场数据优先、包里数据跳过且不留孤儿行；
旧包（无这两段）导入不受影响。
"""

from __future__ import annotations

import sqlite3

import pytest

PASSPHRASE = "backup-passphrase-123"
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"polish4-avatar-payload"


@pytest.fixture()
def _env(monkeypatch, tmp_path):
    """每个"实例"一套独立路径；切换目录即切换实例（沿用 stores 测试的搭法）。"""
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
            "MEMORY_DB_PATH": str(base / "kernel_memory.db"),
            "PERSONAL_CONTEXT_DB_PATH": str(base / "personal_context.db"),
            "LIUHAO_KNOWLEDGE_DB": str(base / "knowledge_vectors.sqlite3"),
            "LIUHAO_WORKBENCH_DB": str(base / "workbench.sqlite3"),
            "LIUHAO_KNOWLEDGE_ENTERPRISE_DB": str(
                base / "knowledge_enterprise.sqlite3"
            ),
            "LIUHAO_KNOWLEDGE_MEMORY_DB": str(base / "knowledge_memory.sqlite3"),
            "LIUHAO_KNOWLEDGE_FILES_DIR": str(base / "knowledge_files"),
        }
        for key, value in values.items():
            monkeypatch.setenv(key, value)
        monkeypatch.setattr(identity_module, "_global_manager", None)
        monkeypatch.setattr(
            conversation_module,
            "_conversation_store",
            conversation_module.ConversationStore(values["CONVERSATION_DB_PATH"]),
        )
        return values

    return apply


def _seed_accounts():
    from src.kernels.identity import get_identity_manager

    manager = get_identity_manager()
    manager.register_root_account("root1", permissions={"*"})
    manager.register_self_service_account("member1")
    return manager


def _seed_workbench():
    """根：分组+Bot（带头像）+群聊+例程；成员：一个 Bot。返回关键 id。"""
    from src.workbench.store import WorkbenchStore

    store = WorkbenchStore()
    group = store.create_group("root1", "根的分组")
    bot = store.create_bot(
        "root1", {"name": "根的Bot", "group_id": group["id"], "job": "管账"}
    )
    # 头像：先落文件再由服务端写 avatar_url（与产品路径一致）。
    avatar_dir = store.avatar_dir()
    avatar_dir.mkdir(parents=True, exist_ok=True)
    from src.workbench.store import _safe_avatar_stem

    (avatar_dir / f"{_safe_avatar_stem(bot['id'])}.png").write_bytes(PNG_BYTES)
    store.set_bot_avatar(["root1"], bot["id"])
    chat = store.create_group_chat("root1", "根的群聊", [bot["id"]])
    store.set_jarvis_routines("root1", ["每天九点汇总"])
    member_bot = store.create_bot("member1", {"name": "成员的Bot"})
    return {
        "group_id": group["id"],
        "bot_id": bot["id"],
        "chat_id": chat["id"],
        "member_bot_id": member_bot["id"],
    }


def _seed_enterprise(tmp_path):
    """根：自建分类+文档（带原件）+分块+长期记忆；成员：一篇文档+一条记忆。"""
    from pathlib import Path

    from src.ai.knowledge import store as kb_store

    connection = kb_store.open_knowledge()
    stamp = "2026-11-18T00:00:00+00:00"
    connection.execute(
        "INSERT INTO kb_categories (id, name, parent_id, path, description,"
        " visibility, created_by, system, sort_order, created_at, updated_at)"
        " VALUES ('cat_custom_root', '根的分类', NULL, '/cat_custom_root', '',"
        " 'team', 'root1', 0, 90, ?, ?)",
        (stamp, stamp),
    )
    files_dir = kb_store.knowledge_files_dir() / "root1" / "doc_root_1"
    files_dir.mkdir(parents=True, exist_ok=True)
    original = files_dir / "v1.txt"
    original.write_text("原件正文内容", encoding="utf-8")
    connection.execute(
        "INSERT INTO kb_documents (id, owner, project_id, category_id, doc_key,"
        " title, current_version, source_type, source_ref, origin_filename,"
        " mime, visibility, tags, description, expires_at,"
        " expiry_notice_days, status, created_at, updated_at, deleted_at)"
        " VALUES ('doc_root_1', 'root1', '', 'cat_custom_root', 'key-root-1',"
        " '根的文档', 1, 'upload', '', 'v1.txt', 'text/plain', 'team', '[]',"
        " '', NULL, 30, 'active', ?, ?, NULL)",
        (stamp, stamp),
    )
    connection.execute(
        "INSERT INTO kb_document_versions (id, document_id, version,"
        " content_hash, extracted_text, chunk_count, file_path,"
        " origin_filename, mime, size_bytes, text_source, note, created_by,"
        " created_at) VALUES ('ver_root_1', 'doc_root_1', 1, 'hash-root-1',"
        " '根的文档正文用于检索', 1, ?, 'v1.txt', 'text/plain', 18,"
        " 'extracted', '', 'root1', ?)",
        (str(original), stamp),
    )
    connection.execute(
        "INSERT INTO kb_chunks (id, document_id, version, chunk_index, text,"
        " heading_path, page) VALUES ('chk_root_1', 'doc_root_1', 1, 0,"
        " '根的文档正文用于检索', '', NULL)"
    )
    connection.execute(
        "INSERT INTO kb_documents (id, owner, project_id, category_id, doc_key,"
        " title, current_version, source_type, source_ref, origin_filename,"
        " mime, visibility, tags, description, expires_at,"
        " expiry_notice_days, status, created_at, updated_at, deleted_at)"
        " VALUES ('doc_member_1', 'member1', '', NULL, 'key-member-1',"
        " '成员的文档', 0, 'text', '', '', '', 'private', '[]', '', NULL, 30,"
        " 'active', ?, ?, NULL)",
        (stamp, stamp),
    )
    connection.commit()
    connection.close()

    memory = kb_store.open_memory()
    memory.execute(
        "INSERT INTO lt_memories (id, owner, project_id, employee_id, scope,"
        " title, content, source, source_ref, tags, confidence, expires_at,"
        " created_at, updated_at, deleted_at) VALUES ('mem_root_1', 'root1',"
        " '', '', 'personal', '根的记忆', '根的长期记忆内容', '', '', '[]',"
        " NULL, NULL, ?, ?, NULL)",
        (stamp, stamp),
    )
    memory.execute(
        "INSERT INTO lt_memory_revisions (id, memory_id, revision, title,"
        " content, source, tags, edited_by, created_at) VALUES"
        " ('rev_root_1', 'mem_root_1', 1, '根的记忆', '根的长期记忆内容', '',"
        " '[]', 'root1', ?)",
        (stamp,),
    )
    memory.execute(
        "INSERT INTO lt_memories (id, owner, project_id, employee_id, scope,"
        " title, content, source, source_ref, tags, confidence, expires_at,"
        " created_at, updated_at, deleted_at) VALUES ('mem_member_1',"
        " 'member1', '', '', 'personal', '成员的记忆', '成员的长期记忆内容',"
        " '', '', '[]', NULL, NULL, ?, ?, NULL)",
        (stamp, stamp),
    )
    memory.commit()
    memory.close()
    return {"original_text": "原件正文内容"}


def test_bundle_carries_workbench_and_enterprise_across_instances(_env, tmp_path):
    from src.gateway.account_backup import export_bundle, import_bundle

    _env("source")
    _seed_accounts()
    ids = _seed_workbench()
    seeded = _seed_enterprise(tmp_path)

    exported = export_bundle(
        actor="root1", scope="all", passphrase=PASSPHRASE,
        subtree=["root1", "member1"], is_root=True,
    )
    manifest = exported["manifest"]
    assert manifest["workbench"]["groups"] == 1
    assert manifest["workbench"]["bots"] == 2
    assert manifest["workbench"]["group_chats"] == 1
    assert manifest["workbench"]["avatars"] == 1
    assert manifest["knowledge_enterprise"]["documents"] == 2
    assert manifest["knowledge_enterprise"]["chunks"] == 1
    assert manifest["knowledge_enterprise"]["memories"] == 2
    assert manifest["knowledge_enterprise"]["files"] == 1

    # ── 全新实例认领导入 ────────────────────────────────────────────────
    _env("target")
    result = import_bundle(bundle=exported["bundle"], passphrase=PASSPHRASE)
    assert result["workbench"]["groups"] == 1
    assert result["workbench"]["bots"] == 2
    assert result["workbench"]["group_chats"] == 1
    assert result["workbench"]["avatars"] == 1
    assert result["knowledge_enterprise"]["documents"] == 2
    assert result["knowledge_enterprise"]["versions"] == 1
    assert result["knowledge_enterprise"]["chunks"] == 1
    assert result["knowledge_enterprise"]["files"] == 1
    assert result["knowledge_enterprise"]["memories"] == 2
    assert result["knowledge_enterprise"]["memory_revisions"] == 1

    # ── 逐项对回：工作台 ────────────────────────────────────────────────
    from src.workbench.store import WorkbenchStore

    store = WorkbenchStore()
    groups = store.list_groups(["root1", "member1"])
    assert [g["name"] for g in groups] == ["根的分组"]
    bots = store.list_bots(["root1"])
    assert [b["name"] for b in bots] == ["根的Bot"]
    assert bots[0]["group_id"] == ids["group_id"]
    chats = store.list_group_chats(["root1"])
    assert len(chats) == 1
    assert chats[0]["member_bot_ids"] == [ids["bot_id"]]
    assert store.get_jarvis_routines("root1") == ["每天九点汇总"]
    avatar = store.find_bot_avatar_file(ids["bot_id"])
    assert avatar is not None and avatar.read_bytes() == PNG_BYTES
    member_bots = store.list_bots(["member1"])
    assert [b["name"] for b in member_bots] == ["成员的Bot"]

    # ── 逐项对回：企业知识（行、原件路径改写、FTS 可检索） ──────────────
    from src.ai.knowledge import store as kb_store

    connection = kb_store.open_knowledge()
    titles = {
        row[0]
        for row in connection.execute("SELECT title FROM kb_documents")
    }
    assert titles == {"根的文档", "成员的文档"}
    category_id = connection.execute(
        "SELECT category_id FROM kb_documents WHERE id = 'doc_root_1'"
    ).fetchone()[0]
    assert category_id == "cat_custom_root"
    file_path, = connection.execute(
        "SELECT file_path FROM kb_document_versions WHERE id = 'ver_root_1'"
    ).fetchone()
    target_files = kb_store.knowledge_files_dir()
    assert file_path.startswith(str(target_files))
    from pathlib import Path

    assert Path(file_path).read_text(encoding="utf-8") == seeded["original_text"]
    hits = connection.execute(
        "SELECT chunk_id FROM kb_chunk_fts WHERE kb_chunk_fts MATCH '文档'"
    ).fetchall()
    assert [row[0] for row in hits] == ["chk_root_1"]
    connection.close()

    memory = kb_store.open_memory()
    memory_titles = {
        row[0] for row in memory.execute("SELECT title FROM lt_memories")
    }
    assert memory_titles == {"根的记忆", "成员的记忆"}
    memory.close()


def test_merge_import_only_takes_own_workbench_and_enterprise(_env, tmp_path):
    from src.gateway.account_backup import (
        export_bundle,
        import_bundle_for_account,
    )

    _env("source")
    _seed_accounts()
    _seed_workbench()
    _seed_enterprise(tmp_path)
    exported = export_bundle(
        actor="member1", scope="self", passphrase=PASSPHRASE,
        subtree=["member1"], is_root=False,
    )
    assert exported["manifest"]["workbench"]["bots"] == 1
    assert exported["manifest"]["knowledge_enterprise"]["documents"] == 1

    _env("target")
    _seed_accounts()
    result = import_bundle_for_account(
        bundle=exported["bundle"], passphrase=PASSPHRASE, actor="member1"
    )
    assert result["workbench"]["bots"] == 1
    assert result["workbench"]["groups"] == 0
    assert result["knowledge_enterprise"]["documents"] == 1
    assert result["knowledge_enterprise"]["memories"] == 1

    from src.workbench.store import WorkbenchStore

    store = WorkbenchStore()
    assert [b["name"] for b in store.list_bots(["member1"])] == ["成员的Bot"]
    assert store.list_bots(["root1"]) == []

    from src.ai.knowledge import store as kb_store

    connection = kb_store.open_knowledge()
    titles = {
        row[0]
        for row in connection.execute("SELECT title FROM kb_documents")
    }
    assert titles == {"成员的文档"}
    connection.close()


def test_merge_filters_drop_other_owners_rows():
    from src.gateway.account_backup import (
        _merge_filter_knowledge_enterprise,
        _merge_filter_workbench,
    )

    workbench = {
        "groups": [{"id": "g1", "owner": "root1"}, {"id": "g2", "owner": "member1"}],
        "bots": [{"id": "b1", "owner": "root1"}, {"id": "b2", "owner": "member1"}],
        "group_chats": [{"id": "c1", "owner": "root1"}],
        "group_chat_members": [{"chat_id": "c1", "bot_id": "b1"}],
        "jarvis_routines": [{"owner": "root1"}, {"owner": "member1"}],
        "avatars": [{"bot_id": "b1"}, {"bot_id": "b2"}],
    }
    filtered = _merge_filter_workbench(workbench, ["member1"])
    assert [row["id"] for row in filtered["groups"]] == ["g2"]
    assert [row["id"] for row in filtered["bots"]] == ["b2"]
    assert filtered["group_chats"] == []
    assert filtered["group_chat_members"] == []
    assert [row["owner"] for row in filtered["jarvis_routines"]] == ["member1"]
    assert [row["bot_id"] for row in filtered["avatars"]] == ["b2"]
    # 主账号（allowed=None）不过滤，原样返回。
    assert _merge_filter_workbench(workbench, None) is workbench

    enterprise = {
        "categories": [
            {"id": "cat_sys", "system": 1, "created_by": "__system__"},
            {"id": "cat_root", "system": 0, "created_by": "root1"},
            {"id": "cat_ref", "system": 0, "created_by": "root1"},
        ],
        "documents": [
            {"id": "d1", "owner": "root1", "category_id": "cat_root"},
            {"id": "d2", "owner": "member1", "category_id": "cat_ref"},
        ],
        "versions": [
            {"id": "v1", "document_id": "d1"},
            {"id": "v2", "document_id": "d2"},
        ],
        "chunks": [
            {"id": "k1", "document_id": "d1"},
            {"id": "k2", "document_id": "d2"},
        ],
        "memories": [{"id": "m1", "owner": "root1"}, {"id": "m2", "owner": "member1"}],
        "memory_revisions": [
            {"id": "r1", "memory_id": "m1"},
            {"id": "r2", "memory_id": "m2"},
        ],
        "files": [{"version_id": "v1"}, {"version_id": "v2"}],
        "file_rels": {"v1": "a/v1.txt", "v2": "b/v2.txt"},
    }
    filtered = _merge_filter_knowledge_enterprise(enterprise, ["member1"])
    # 系统分类保留（新机本来就有）、被成员文档引用的分类保留、无关分类丢掉。
    assert [row["id"] for row in filtered["categories"]] == ["cat_sys", "cat_ref"]
    assert [row["id"] for row in filtered["documents"]] == ["d2"]
    assert [row["id"] for row in filtered["versions"]] == ["v2"]
    assert [row["id"] for row in filtered["chunks"]] == ["k2"]
    assert [row["id"] for row in filtered["memories"]] == ["m2"]
    assert [row["id"] for row in filtered["memory_revisions"]] == ["r2"]
    assert [row["version_id"] for row in filtered["files"]] == ["v2"]
    assert filtered["file_rels"] == {"v2": "b/v2.txt"}
    assert _merge_filter_knowledge_enterprise(enterprise, None) is enterprise


def test_import_conflicts_keep_local_rows_and_leave_no_orphans(_env, tmp_path):
    from src.gateway.account_backup import export_bundle, import_bundle

    _env("source")
    _seed_accounts()
    ids = _seed_workbench()
    _seed_enterprise(tmp_path)
    exported = export_bundle(
        actor="root1", scope="all", passphrase=PASSPHRASE,
        subtree=["root1", "member1"], is_root=True,
    )

    # 目标机先有同 id 的 Bot（不同名字）与同 (owner, doc_key) 的文档。
    _env("target")
    from src.workbench.store import WorkbenchStore

    store = WorkbenchStore()
    store.create_bot("root1", {"id": ids["bot_id"], "name": "本机自己的Bot"})

    from src.ai.knowledge import store as kb_store

    connection = kb_store.open_knowledge()
    stamp = "2026-11-18T01:00:00+00:00"
    connection.execute(
        "INSERT INTO kb_documents (id, owner, project_id, category_id, doc_key,"
        " title, current_version, source_type, source_ref, origin_filename,"
        " mime, visibility, tags, description, expires_at,"
        " expiry_notice_days, status, created_at, updated_at, deleted_at)"
        " VALUES ('doc_local_1', 'root1', '', NULL, 'key-root-1',"
        " '本机自己的文档', 0, 'text', '', '', '', 'team', '[]', '', NULL, 30,"
        " 'active', ?, ?, NULL)",
        (stamp, stamp),
    )
    connection.commit()
    connection.close()

    result = import_bundle(bundle=exported["bundle"], passphrase=PASSPHRASE)
    # 同 id Bot 被跳过（2 个里只进 1 个），现场名字不变。
    assert result["workbench"]["bots"] == 1
    assert result["workbench"]["bots_skipped"] == 1
    bot = store.get_bot(["root1"], ids["bot_id"])
    assert bot is not None and bot["name"] == "本机自己的Bot"
    # 同 doc_key 文档被跳过，其版本/分块不落孤儿行。
    assert result["knowledge_enterprise"]["documents"] == 1
    assert result["knowledge_enterprise"]["documents_skipped"] == 1
    assert result["knowledge_enterprise"]["versions"] == 0
    assert result["knowledge_enterprise"]["chunks"] == 0
    connection = kb_store.open_knowledge()
    orphans = connection.execute(
        "SELECT COUNT(*) FROM kb_chunks WHERE document_id = 'doc_root_1'"
    ).fetchone()[0]
    assert orphans == 0
    titles = {
        row[0]
        for row in connection.execute("SELECT title FROM kb_documents")
    }
    assert titles == {"本机自己的文档", "成员的文档"}
    connection.close()


def test_restore_sections_tolerate_old_bundles_without_new_keys():
    from src.gateway.account_backup import (
        _restore_knowledge_enterprise,
        _restore_workbench,
    )

    for payload in (None, {}, {"groups": []}):
        workbench = _restore_workbench(payload)
        assert workbench["groups"] == 0 and workbench["bots"] == 0
        enterprise = _restore_knowledge_enterprise(payload)
        assert enterprise["documents"] == 0 and enterprise["memories"] == 0


def test_workbench_snapshot_survives_legacy_db_without_avatar_column(
    _env, tmp_path
):
    """老库没有 avatar_url 列时，收集不能整表丢 Bot（补空值继续）。"""
    _env("source")
    values_db = tmp_path / "source" / "workbench.sqlite3"
    connection = sqlite3.connect(str(values_db))
    connection.execute(
        "CREATE TABLE bots (id TEXT PRIMARY KEY, owner TEXT NOT NULL,"
        " name TEXT NOT NULL, job TEXT NOT NULL DEFAULT '',"
        " personality TEXT NOT NULL DEFAULT '', idea TEXT NOT NULL DEFAULT '',"
        " direction TEXT NOT NULL DEFAULT '', mood TEXT NOT NULL DEFAULT '',"
        " gender TEXT NOT NULL DEFAULT '', color TEXT NOT NULL DEFAULT '',"
        " accessory TEXT NOT NULL DEFAULT '', model_id TEXT NOT NULL DEFAULT '',"
        " pinned INTEGER NOT NULL DEFAULT 0, hidden INTEGER NOT NULL DEFAULT 0,"
        " group_id TEXT NOT NULL DEFAULT '', routines TEXT NOT NULL DEFAULT '[]',"
        " order_index INTEGER NOT NULL DEFAULT 0,"
        " created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    connection.execute(
        "INSERT INTO bots VALUES ('bot-legacy', 'root1', '老库Bot', '', '', '',"
        " '', '', '', '', '', '', 0, 0, '', '[]', 0, '2026-01-01', '2026-01-01')"
    )
    connection.commit()
    connection.close()

    from src.gateway.account_backup import _collect_workbench

    collected = _collect_workbench(["root1"], include_public=False)
    assert collected["manifest"]["bots"] == 1
    bot = collected["payload"]["bots"][0]
    assert bot["name"] == "老库Bot"
    assert bot["avatar_url"] == ""
