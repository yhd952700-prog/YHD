"""D1 回归：备份带走后导入，会话列表/历史/续聊/删除都必须可用。

复现口径与现场一致：成员在实例 A 聊过天 → 根全量导出 → 全新数据目录的
实例 B 导入 → 成员登录后 ``GET /v1/chat/sessions`` 必须能看到历史会话，
点开能读全文、能继续对话、能删除；并且重启（新进程/新 store 实例）后仍在。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

PASSPHRASE = "backup-passphrase-123"
SESSION_KEY = "liuhao-member1-ui-history"


@pytest.fixture()
def _env(monkeypatch, tmp_path):
    """每个"实例"一套独立路径；切换目录即切换实例。"""
    import src.ai.conversation_store as conversation_module
    import src.gateway.chat as chat_module
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
        monkeypatch.setattr(identity_module, "_global_manager", None)
        monkeypatch.setattr(
            conversation_module,
            "_conversation_store",
            conversation_module.ConversationStore(values["CONVERSATION_DB_PATH"]),
        )
        chat_module._sessions.clear()
        chat_module._session_users.clear()
        chat_module._session_last_used.clear()
        return values

    return apply


def _make_source_with_history(_env) -> dict:
    """实例 A：账号树 + 两轮真实会话，返回可导入的备份包。"""
    _env("source")
    from src.kernels.identity import get_identity_manager

    manager = get_identity_manager()
    manager.register_root_account("root1", permissions={"*"})
    manager.register_self_service_account("member1")

    from src.ai.conversation_store import get_conversation_store

    store = get_conversation_store()
    store.append(SESSION_KEY, 1, "user", "Q7紫罗兰-历史-8842：先给结论")
    store.append(SESSION_KEY, 1, "assistant", "结论：边界判断有误，建议加空值校验")
    store.append(SESSION_KEY, 2, "user", "那具体改哪一行？")
    store.append(SESSION_KEY, 2, "assistant", "第 42 行的分支条件改成双等号判断")

    from src.gateway.account_backup import export_bundle

    exported = export_bundle(
        actor="root1",
        scope="all",
        passphrase=PASSPHRASE,
        subtree=manager.subtree_principals("root1"),
        is_root=True,
    )
    assert exported["manifest"]["stores"]["conversation"]["turns"] == 4
    return exported


def _import_into_fresh(_env, exported) -> None:
    _env("target")
    from src.gateway.account_backup import import_bundle

    imported = import_bundle(bundle=exported["bundle"], passphrase=PASSPHRASE)
    assert imported["accounts"] == 2
    assert imported["conversations"]["imported"] == 4


def test_imported_sessions_are_listed_readable_continuable_and_deletable(_env):
    """导入后列表非空、点开可读全文、能续聊、能删除。"""
    exported = _make_source_with_history(_env)
    _import_into_fresh(_env, exported)

    import src.gateway.chat as chat_module
    from src.ai.providers import MockProvider

    # 1) 列表非空：成员自己的会话必须出现，且不能混入别人的。
    assert chat_module.list_sessions(
        "member1", known_principals=["member1", "root1"], allow_legacy=False
    ) == ["ui-history"]

    # 2) 点开能读全文。
    history = chat_module.read_history(session_id="ui-history", principal="member1")
    assert history["count"] == 4
    assert [m["content"] for m in history["history"]][0].startswith("Q7紫罗兰")

    # 3) 能继续对话：历史被载入，下一轮编号从 3 开始。
    monkeypatch_provider = MockProvider(name="mock", model="mock-model")
    a = chat_module.LiuHaoAssistant(
        name=chat_module._resolve_history_principal("member1", "ui-history"),
        profile_principal="user-member1",
        provider=monkeypatch_provider,
    )
    assert a.turn == 2
    result = a.chat("继续")
    assert result["status"] == "completed"
    assert a.turn == 3

    # 4) 能删除：持久库与列表都清掉。
    assert chat_module.delete_session("ui-history", "member1") is True
    assert chat_module.list_sessions(
        "member1", known_principals=["member1", "root1"], allow_legacy=False
    ) == []
    after = chat_module.read_history(session_id="ui-history", principal="member1")
    assert after["count"] == 0


def test_sessions_survive_restart(_env):
    """模拟后端重启：清空进程内会话表与 store 单例后，列表仍非空。"""
    exported = _make_source_with_history(_env)
    _import_into_fresh(_env, exported)

    import src.ai.conversation_store as conversation_module
    import src.gateway.chat as chat_module

    chat_module._sessions.clear()
    chat_module._session_users.clear()
    chat_module._session_last_used.clear()
    # 新进程 = 新的 Store 实例，重新打开同一个库。
    db_path = conversation_module.get_conversation_store().db_path
    conversation_module._conversation_store = conversation_module.ConversationStore(
        db_path
    )

    assert chat_module.list_sessions(
        "member1", known_principals=["member1", "root1"], allow_legacy=False
    ) == ["ui-history"]
    assert chat_module.read_history(
        session_id="ui-history", principal="member1"
    )["count"] == 4


def test_http_sessions_endpoint_sees_imported_history(_env, monkeypatch):
    """接口级证据：成员登录后 /v1/chat/sessions 与 /v1/chat/history 都有数据。"""
    exported = _make_source_with_history(_env)
    _import_into_fresh(_env, exported)

    import src.gateway.chat as chat_module
    from src.ai.providers import MockProvider

    # 续聊必须走真实网关路径，只把模型这一层换成离线桩（与现场"已配模型"等价）。
    monkeypatch.setattr(
        chat_module,
        "provider_for_task",
        lambda *_a, **_k: MockProvider(name="mock", model="mock-model"),
    )

    from src.gateway.main import get_app
    from src.security import get_jwt_handler

    token, _ = get_jwt_handler().create_token(
        subject="member1", metadata={"kind": "human"}
    )
    headers = {"Authorization": f"Bearer {token}"}
    client = TestClient(get_app())

    listed = client.get("/v1/chat/sessions", headers=headers)
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["sessions"] == ["ui-history"]
    assert body["count"] == 1

    detail = client.get(
        "/v1/chat/history", params={"session_id": "ui-history"}, headers=headers
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["count"] == 4

    # 3) 点开之后能继续对话：历史被载入，下一轮编号接上（不是从 1 重开）。
    continued = client.post(
        "/v1/chat",
        json={"message": "继续", "session_id": "ui-history"},
        headers=headers,
    )
    assert continued.status_code == 200, continued.text
    assert continued.json()["status"] == "completed"
    assert continued.json()["turn"] == 3
    assert client.get(
        "/v1/chat/history", params={"session_id": "ui-history"}, headers=headers
    ).json()["count"] == 6

    deleted = client.delete("/v1/chat/sessions/ui-history", headers=headers)
    assert deleted.status_code == 200, deleted.text
    assert client.get("/v1/chat/sessions", headers=headers).json()["sessions"] == []
