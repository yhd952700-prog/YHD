"""可靠性：磁盘满 / 只读盘 / 无权限时的"中文可行动提示 + 不半写"契约。

对应演练项：断电（半写）、磁盘写满、数据目录只读/被占用。
只使用 pytest 的临时目录，不触碰真实用户数据。
"""
from __future__ import annotations

import errno
import sqlite3
from pathlib import Path

import pytest

from src.ai.conversation_store import ConversationStore, ConversationStoreError
from src.common import atomic_write
from src.common.atomic_write import (
    StorageWriteError,
    atomic_write_bytes,
    describe_storage_failure,
    is_storage_failure,
)
from src.gateway import account_settings


def _assert_chinese_actionable(message: str) -> None:
    assert "无法写入" in message
    # 必须是"给人看"的中文，而不是英文堆栈。
    assert any(hint in message for hint in ("磁盘已满", "只读", "权限", "占用", "写入失败"))


def test_describe_disk_full_is_chinese_and_actionable():
    exc = OSError(errno.ENOSPC, "No space left on device")
    message = describe_storage_failure(exc, "/tmp/data/x.json")
    _assert_chinese_actionable(message)
    assert "磁盘已满" in message
    assert "/tmp/data/x.json" in message
    # 保留原始错误便于排障，但整体仍是中文说明。
    assert "No space left" in message


def test_describe_permission_denied_is_chinese():
    message = describe_storage_failure(
        PermissionError(errno.EACCES, "Permission denied"), "D:/data/x.json"
    )
    _assert_chinese_actionable(message)
    assert "不可写" in message


def test_is_storage_failure_classifies_sqlite_and_oserror():
    assert is_storage_failure(sqlite3.OperationalError("database or disk is full"))
    assert is_storage_failure(sqlite3.OperationalError("attempt to write a readonly database"))
    assert is_storage_failure(OSError(errno.ENOSPC, "no space"))
    assert not is_storage_failure(RuntimeError("provider timeout"))


def test_atomic_write_fails_fast_on_read_only_volume(tmp_path, monkeypatch):
    target = tmp_path / "store.json"
    target.write_text('{"old":true}', encoding="utf-8")
    monkeypatch.setattr(atomic_write, "volume_is_read_only", lambda _p: True)

    with pytest.raises(StorageWriteError) as excinfo:
        atomic_write_bytes(target, b'{"new":true}')

    _assert_chinese_actionable(str(excinfo.value))
    assert "只读" in str(excinfo.value)
    # 主文件保持上一版，不出现半写内容。
    assert target.read_text(encoding="utf-8") == '{"old":true}'
    assert not list(tmp_path.glob("*.tmp"))


def test_atomic_write_disk_full_does_not_half_write(tmp_path, monkeypatch):
    target = tmp_path / "store.json"
    target.write_text("old", encoding="utf-8")

    def _mkstemp(*args, **kwargs):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(atomic_write.tempfile, "mkstemp", _mkstemp)
    with pytest.raises(StorageWriteError) as excinfo:
        atomic_write_bytes(target, b"new")

    _assert_chinese_actionable(str(excinfo.value))
    assert "磁盘已满" in str(excinfo.value)
    assert target.read_text(encoding="utf-8") == "old"


def test_conversation_store_unopenable_path_raises_chinese(tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    with pytest.raises(ConversationStoreError) as excinfo:
        ConversationStore(str(blocker / "conversation.db"))
    message = str(excinfo.value)
    assert "对话历史库" in message
    assert any(hint in message for hint in ("磁盘已满", "只读", "占用", "写入失败"))


def test_conversation_store_append_failure_is_chinese(tmp_path):
    store = ConversationStore(str(tmp_path / "conversation.db"))
    store.append("u", 1, "user", "ok")
    store._conn.execute("PRAGMA query_only=ON")  # 模拟只读库 / 磁盘拒绝写入
    with pytest.raises(ConversationStoreError) as excinfo:
        store.append("u", 2, "user", "应失败")
    assert "本轮对话未能保存" in str(excinfo.value)
    assert any(hint in str(excinfo.value) for hint in ("磁盘已满", "只读", "占用", "写入失败"))


def test_account_settings_write_failure_is_chinese(tmp_path, monkeypatch):
    monkeypatch.setenv(account_settings.SETTINGS_FILE_ENV, str(tmp_path / "settings.json"))

    def _boom(*args, **kwargs):
        raise StorageWriteError("无法写入「settings.json」：磁盘已满。请清理磁盘。")

    monkeypatch.setattr(account_settings, "atomic_write_text", _boom)
    with pytest.raises(StorageWriteError) as excinfo:
        account_settings.set_allow_self_registration(False)
    assert "磁盘已满" in str(excinfo.value)


def _configure_offline_gateway(monkeypatch, tmp_path):
    """最小离线环境：真实网关 + 假 provider，不碰仓库数据。"""
    import src.ai.conversation_store as conversation_module
    import src.gateway.chat as chat_module
    import src.kernels.identity as identity_module
    from src.ai.providers import MockProvider

    base = tmp_path / "instance"
    base.mkdir(parents=True, exist_ok=True)
    values = {
        "LIUHAO_HUMAN_IDENTITIES_BACKEND": "file",
        "LIUHAO_HUMAN_IDENTITIES_FILE": str(base / "identities.json"),
        "LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY": "reliability-integrity-key",
        "LIUHAO_AUTH_SECRETS_FILE": str(base / "auth_secrets.json"),
        "LIUHAO_ACCOUNT_SETTINGS_FILE": str(base / "account_settings.json"),
        "LIUHAO_ACCOUNT_USAGE_DB": str(base / "account_usage.sqlite3"),
        "CONVERSATION_DB_PATH": str(base / "conversation_store.db"),
        "LIUHAO_WORKSPACE_ROOT": str(base / "workspace"),
        "LIUHAO_MODEL_CONFIG_DB": str(base / "model_config.sqlite3"),
        "LIUHAO_MODEL_CONFIG_KEY_FILE": str(base / "model_config_key.bin"),
        "LIUHAO_CHAT_DEDUP_WINDOW_SECONDS": "8",
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
    chat_module._dedup_entries.clear()

    manager = identity_module.get_identity_manager()
    manager.register_root_account("root1", permissions={"*"})
    manager.register_self_service_account("member1")

    class _Provider(MockProvider):
        def __init__(self) -> None:
            super().__init__(name="storage-failure-mock", model="storage-failure-model")

        def chat(self, messages, **kwargs):
            return "回复已生成"

        def chat_stream(self, messages, **kwargs):
            yield "回复已生成"

        def close(self):
            pass

    monkeypatch.setattr(chat_module, "provider_for_task", lambda *_a, **_k: _Provider())
    return conversation_module


def _auth_headers() -> dict:
    from src.security import get_jwt_handler

    token, _ = get_jwt_handler().create_token(
        subject="member1", metadata={"kind": "human"}
    )
    return {"Authorization": f"Bearer {token}"}


def test_chat_history_save_failure_returns_chinese_503(monkeypatch, tmp_path):
    """对话正文可生成，但历史落盘失败时：503 + 中文可行动提示（不是英文 500）。"""
    from fastapi.testclient import TestClient

    conversation_module = _configure_offline_gateway(monkeypatch, tmp_path)

    def _boom(*_a, **_k):
        raise ConversationStoreError(
            "本轮对话未能保存「conversation.db」：磁盘已满。请清理磁盘可用空间后重试。"
        )

    monkeypatch.setattr(
        conversation_module._conversation_store, "append_turn", _boom
    )

    from src.gateway.main import get_app

    response = TestClient(get_app()).post(
        "/v1/chat",
        json={"message": "落盘失败测试", "session_id": "storage-fail"},
        headers=_auth_headers(),
    )
    assert response.status_code == 503, response.text
    body = response.json()
    assert body["code"] == "storage_unavailable"
    assert "保存" in body["message"]
    assert "磁盘已满" in body["message"]


def test_chat_stream_emits_error_frame_when_history_save_fails(monkeypatch, tmp_path):
    """流式对话：最后一个 token 之后落盘失败，必须发 SSE 错误帧，不能静默截断。"""
    from fastapi.testclient import TestClient

    conversation_module = _configure_offline_gateway(monkeypatch, tmp_path)

    def _boom(*_a, **_k):
        raise ConversationStoreError("本轮对话未能保存：磁盘已满，请清理磁盘后重试。")

    monkeypatch.setattr(
        conversation_module._conversation_store, "append_turn", _boom
    )

    from src.gateway.main import get_app

    response = TestClient(get_app()).post(
        "/v1/chat/stream",
        json={"message": "流式落盘失败测试", "session_id": "storage-stream"},
        headers=_auth_headers(),
    )
    assert response.status_code == 200
    assert "storage_unavailable" in response.text
    assert "磁盘已满" in response.text


def test_ensure_writable_directory_message_mentions_read_only(tmp_path):
    directory = tmp_path / "data"
    directory.mkdir()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(atomic_write, "volume_is_read_only", lambda _p: True)
        with pytest.raises(StorageWriteError) as excinfo:
            atomic_write.ensure_writable_directory(directory, purpose="数据目录")
    assert "只读" in str(excinfo.value)
    assert str(directory) in str(excinfo.value)


def _raise_disk_io_error(*_args, **_kwargs):
    """模拟磁盘满时 SQLite 的裸错误（不是 ConversationStoreError）。"""
    raise sqlite3.OperationalError("disk I/O error")


def test_chat_maps_bare_sqlite_disk_error_to_chinese_503(monkeypatch, tmp_path):
    """知识库 / 个人上下文等底层 SQLite 抛裸 ``OperationalError`` 时也要 503 中文。

    实测磁盘写满后 ``_assistant_for`` 抛的是裸 ``sqlite3.OperationalError``，
    旧行为会变成英文 ``500 Internal Server Error``；用户既看不懂也不知道该清理
    磁盘。这里锁定：必须映射成 ``storage_unavailable`` + 中文可行动提示。
    """
    from fastapi.testclient import TestClient

    _configure_offline_gateway(monkeypatch, tmp_path)
    import src.gateway.chat as chat_module

    monkeypatch.setattr(chat_module, "_assistant_for", _raise_disk_io_error)

    from src.gateway.main import get_app

    response = TestClient(get_app()).post(
        "/v1/chat",
        json={"message": "磁盘满裸错误", "session_id": "storage-bare"},
        headers=_auth_headers(),
    )
    assert response.status_code == 503, response.text
    body = response.json()
    assert body["code"] == "storage_unavailable"
    assert "无法写入" in body["message"]
    assert "Internal Server Error" not in response.text


def test_chat_stream_maps_bare_sqlite_disk_error_to_chinese_503(monkeypatch, tmp_path):
    """流式对话在 preflight 阶段遇到裸存储错误也应 503 中文，而不是英文 500。"""
    from fastapi.testclient import TestClient

    _configure_offline_gateway(monkeypatch, tmp_path)
    import src.gateway.chat as chat_module

    monkeypatch.setattr(chat_module, "_assistant_for", _raise_disk_io_error)

    from src.gateway.main import get_app

    response = TestClient(get_app()).post(
        "/v1/chat/stream",
        json={"message": "流式磁盘满裸错误", "session_id": "storage-bare-stream"},
        headers=_auth_headers(),
    )
    assert response.status_code == 503, response.text
    assert "storage_unavailable" in response.text
    assert "Internal Server Error" not in response.text
