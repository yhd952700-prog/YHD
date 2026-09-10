"""画像 HTTP 端点 + 跨会话用户级画像共享 的测试。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.ai import personal_context as pc_module
from src.gateway.main import get_app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """隔离画像库到临时文件，避免污染真实 personal_context.db。"""
    db_path = str(tmp_path / "profile_api.db")
    fresh = pc_module.PersonalContextManager(db_path=db_path)
    monkeypatch.setattr(pc_module, "_personal_context", fresh)
    monkeypatch.setattr(pc_module, "get_personal_context", lambda: fresh)

    import src.gateway.chat as chat_mod

    chat_mod._sessions.clear()
    chat_mod._session_users.clear()

    # 注意：不要用 `with TestClient(app)` —— 它会把 lifespan 放到独立线程执行，
    # 使全局 audit/memory 的 SQLite 连接跨线程创建，进而污染后续测试
    # （"SQLite objects created in a thread can only be used in that same thread"）。
    # 直接实例化即可在主线程内完成请求（本测试不依赖 lifespan 初始化）。
    yield TestClient(get_app())


def test_get_empty_profile(client):
    r = client.get("/v1/profile", params={"principal": "u-empty"})
    assert r.status_code == 200
    d = r.json()
    assert d["has_profile"] is False
    assert d["summary"] == ""  # 空画像不给兜底串，避免注入噪声


def test_put_display_name_and_fact_then_get(client):
    r = client.put(
        "/v1/profile",
        json={
            "principal": "u1",
            "display_name": "刘总",
            "fact": {"key": "公司", "value": "鎏灏科技"},
        },
    )
    assert r.status_code == 200
    assert r.json()["written"] == ["display_name", "fact:公司"]

    g = client.get("/v1/profile", params={"principal": "u1"})
    d = g.json()
    assert d["display_name"] == "刘总"
    assert d["facts"]["公司"] == "鎏灏科技"
    assert d["has_profile"] is True
    assert "刘总" in d["summary"] and "鎏灏科技" in d["summary"]


def test_put_preference_and_lists(client):
    client.put(
        "/v1/profile",
        json={
            "principal": "u2",
            "preference": {"key": "语言", "value": "简体中文", "confidence": 0.9},
            "interest": "AI 工程",
            "expertise": "Python",
            "relationship": {"who": "小王", "relation": "同事"},
            "behavior_pattern": "偏好简短回答",
        },
    )
    d = client.get("/v1/profile", params={"principal": "u2"}).json()
    assert d["preferences"]["语言"]["value"] == "简体中文"
    assert d["interests"] == ["AI 工程"]
    assert d["expertise"] == ["Python"]
    assert d["relationships"] == {"小王": "同事"}
    assert d["behavior_patterns"] == ["偏好简短回答"]


def test_user_id_maps_to_user_principal(client):
    """user_id 应解析为 user-{id} 主体。"""
    client.put(
        "/v1/profile",
        json={"user_id": "alice", "display_name": "Alice"},
    )
    d = client.get("/v1/profile", params={"user_id": "alice"}).json()
    assert d["principal_id"] == "user-alice"
    assert d["display_name"] == "Alice"


def test_summary_endpoint(client):
    client.put("/v1/profile", json={"principal": "u3", "display_name": "老刘"})
    r = client.get("/v1/profile/summary", params={"principal": "u3"})
    assert r.status_code == 200
    d = r.json()
    assert d["has_profile"] is True
    assert "老刘" in d["summary"]

    empty = client.get("/v1/profile/summary", params={"principal": "nobody"}).json()
    assert empty["has_profile"] is False and empty["summary"] == ""


def test_delete_profile(client):
    client.put("/v1/profile", json={"principal": "u4", "display_name": "临时"})
    assert client.get("/v1/profile", params={"principal": "u4"}).json()["has_profile"]
    r = client.delete("/v1/profile", params={"principal": "u4"})
    assert r.status_code == 200 and r.json()["cleared"] is True
    assert client.get("/v1/profile", params={"principal": "u4"}).json()["has_profile"] is False


def test_profile_principal_shared_across_sessions(client):
    """同一 user_id 的两个会话共享画像，但历史各自隔离。"""
    r1 = client.get("/v1/chat/history", params={"session_id": "s1", "user_id": "bob"})
    r2 = client.get("/v1/chat/history", params={"session_id": "s2", "user_id": "bob"})
    assert r1.json()["profile_principal"] == "user-bob"
    assert r2.json()["profile_principal"] == "user-bob"
    # 历史隔离键仍是会话级
    assert r1.json()["session_id"] == "s1"
    assert r2.json()["session_id"] == "s2"


def test_chat_binds_profile_principal(client, monkeypatch):
    """chat 带 user_id 时，画像主体绑定为 user-{id}（共享），历史仍按 session。"""
    import src.gateway.chat as chat_mod

    seen = {}

    class FakeAssistant:
        def __init__(self, name="x", profile_principal=None, **kw):
            self.principal = name
            self.profile_principal = profile_principal or name
            self.turn = 1

        def chat(self, message):
            seen["profile_principal"] = self.profile_principal
            seen["principal"] = self.principal
            return {
                "reply": "ok",
                "status": "completed",
                "turn": 1,
                "correlation_id": "c1",
                "agent": self.principal,
            }

    monkeypatch.setattr(chat_mod, "LiuHaoAssistant", FakeAssistant)
    r = client.post(
        "/v1/chat",
        json={"message": "hi", "session_id": "s9", "user_id": "carol"},
    )
    assert r.status_code == 200
    assert seen["profile_principal"] == "user-carol"
    assert seen["principal"] == "liuhao-s9"  # 历史隔离键仍是会话级


def test_chat_without_user_id_backward_compatible(client, monkeypatch):
    """不传 user_id 时行为不变：画像主体 = 会话主体。"""
    import src.gateway.chat as chat_mod

    seen = {}

    class FakeAssistant:
        def __init__(self, name="x", profile_principal=None, **kw):
            self.principal = name
            self.profile_principal = profile_principal or name

        def chat(self, message):
            seen["profile_principal"] = self.profile_principal
            return {
                "reply": "ok", "status": "completed", "turn": 1,
                "correlation_id": "c1", "agent": self.principal,
            }

    monkeypatch.setattr(chat_mod, "LiuHaoAssistant", FakeAssistant)
    client.post("/v1/chat", json={"message": "hi", "session_id": "s8"})
    assert seen["profile_principal"] == "liuhao-s8"


def test_sessions_exposes_user_mapping(client, monkeypatch):
    import src.gateway.chat as chat_mod

    class FakeAssistant:
        def __init__(self, name="x", profile_principal=None, **kw):
            self.principal = name
            self.profile_principal = profile_principal or name

        def chat(self, message):
            return {
                "reply": "ok", "status": "completed", "turn": 1,
                "correlation_id": "c", "agent": self.principal,
            }

    monkeypatch.setattr(chat_mod, "LiuHaoAssistant", FakeAssistant)
    client.post("/v1/chat", json={"message": "hi", "session_id": "sa", "user_id": "dave"})
    d = client.get("/v1/chat/sessions").json()
    assert "sa" in d["sessions"]
    assert d["users"]["sa"] == "dave"
