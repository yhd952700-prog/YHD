"""画像 HTTP 端点 + 跨会话用户级画像共享 的测试。

批次2 之后，画像主体**只来自 bearer 令牌的 sub**：请求体里的 ``user_id`` /
``principal``、查询参数里的 ``principal`` 一律不采信。下面同时钉住：

* 正常读写/摘要/清空走令牌主体（``user-{sub}``）；
* 客户端声明别的用户也读不到、删不掉别人的画像；
* 非管理员令牌可以读，但写操作 403（最小权限）。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.ai import personal_context as pc_module
from src.gateway.main import get_app


@pytest.fixture()
def client(tmp_path, monkeypatch, auth_headers):
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
    # 画像端点在控制台鉴权闸门之后，因此带一个真实签发的令牌。
    yield TestClient(get_app(), headers=auth_headers)


def _principal() -> str:
    """``auth_headers`` 的令牌主体是 test-human，画像主体因此固定为它。"""
    return "user-test-human"


def _reader_headers() -> dict:
    """已登录但没有管理员角色的令牌（只允许读）。"""
    from src.security import get_jwt_handler

    token, _payload = get_jwt_handler().create_token(subject="reader-human")
    return {"Authorization": f"Bearer {token}"}


def test_get_empty_profile(client):
    r = client.get("/v1/profile")
    assert r.status_code == 200
    d = r.json()
    assert d["principal_id"] == _principal()
    assert d["has_profile"] is False
    assert d["summary"] == ""  # 空画像不给兜底串，避免注入噪声


def test_put_display_name_and_fact_then_get(client):
    r = client.put(
        "/v1/profile",
        json={
            "display_name": "刘总",
            "fact": {"key": "公司", "value": "鎏灏科技"},
        },
    )
    assert r.status_code == 200
    assert r.json()["written"] == ["display_name", "fact:公司"]

    g = client.get("/v1/profile")
    d = g.json()
    assert d["display_name"] == "刘总"
    assert d["facts"]["公司"] == "鎏灏科技"
    assert d["has_profile"] is True
    assert "刘总" in d["summary"] and "鎏灏科技" in d["summary"]


def test_put_preference_and_lists(client):
    client.put(
        "/v1/profile",
        json={
            "preference": {"key": "语言", "value": "简体中文", "confidence": 0.9},
            "interest": "AI 工程",
            "expertise": "Python",
            "relationship": {"who": "小王", "relation": "同事"},
            "behavior_pattern": "偏好简短回答",
        },
    )
    d = client.get("/v1/profile").json()
    assert d["preferences"]["语言"]["value"] == "简体中文"
    assert d["interests"] == ["AI 工程"]
    assert d["expertise"] == ["Python"]
    assert d["relationships"] == {"小王": "同事"}
    assert d["behavior_patterns"] == ["偏好简短回答"]


def test_body_user_id_is_ignored(client):
    """请求体里的 user_id 不再被采信：主体永远是令牌主体。"""
    r = client.put("/v1/profile", json={"user_id": "alice", "display_name": "Alice"})
    assert r.status_code == 200
    assert r.json()["principal"] == _principal()
    d = client.get("/v1/profile").json()
    assert d["principal_id"] == _principal()
    assert d["display_name"] == "Alice"


def test_query_principal_cannot_read_somebody_else(client):
    """查询参数里的 principal 不参与解析：拿不到别人的画像。"""
    import src.gateway.profile as profile_mod

    pcm = profile_mod.get_personal_context()
    pcm.set_display_name("user-victim", "被保护的人")

    d = client.get("/v1/profile", params={"principal": "victim"}).json()
    assert d["principal_id"] == _principal()
    assert d["has_profile"] is False
    assert "被保护的人" not in d["summary"]


def test_summary_endpoint(client):
    client.put("/v1/profile", json={"display_name": "老刘"})
    r = client.get("/v1/profile/summary")
    assert r.status_code == 200
    d = r.json()
    assert d["principal"] == _principal()
    assert d["has_profile"] is True
    assert "老刘" in d["summary"]

    empty = client.get("/v1/profile/summary").json()
    assert empty["has_profile"] is True  # 已写入，不再是空画像


def test_delete_only_affects_own_profile(client):
    """DELETE 只清自己的画像；带别人的 principal 查询参数也删不到别人。"""
    import src.gateway.profile as profile_mod

    pcm = profile_mod.get_personal_context()
    pcm.set_display_name("user-victim", "不能删")

    client.put("/v1/profile", json={"display_name": "临时"})
    assert client.get("/v1/profile").json()["has_profile"]

    r = client.delete("/v1/profile", params={"principal": "victim", "user_id": "victim"})
    assert r.status_code == 200
    assert r.json()["principal"] == _principal()
    assert client.get("/v1/profile").json()["has_profile"] is False
    # 别人的画像仍在。
    assert pcm.has_profile("user-victim")


def test_write_requires_admin_claim_but_read_does_not(client):
    """最小权限：读不拦，写必须带 admin 角色或 L0 人类主权。"""
    reader = _reader_headers()
    assert client.get("/v1/profile", headers=reader).status_code == 200
    resp = client.put(
        "/v1/profile", json={"display_name": "x"}, headers=reader
    )
    assert resp.status_code == 403
    assert "管理员" in resp.json()["detail"]
    assert client.delete("/v1/profile", headers=reader).status_code == 403


def test_profile_principal_shared_across_sessions(client):
    """同一令牌主体的两个会话共享画像，但历史各自隔离。"""
    r1 = client.get("/v1/chat/history", params={"session_id": "s1"})
    r2 = client.get("/v1/chat/history", params={"session_id": "s2"})
    assert r1.json()["profile_principal"] == _principal()
    assert r2.json()["profile_principal"] == _principal()
    assert r1.json()["session_id"] == "s1"
    assert r2.json()["session_id"] == "s2"


def test_chat_binds_profile_principal_from_token(client, monkeypatch):
    """chat 的画像主体来自令牌，而不是请求体里的 user_id。"""
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
    # user_id=carol 被忽略：画像主体仍是令牌主体。
    assert seen["profile_principal"] == _principal()
    assert seen["principal"] == "liuhao-test-human-s9"  # 历史键含主体，跨用户隔离


def test_chat_without_user_id_backward_compatible(client, monkeypatch):
    """不传 user_id 时行为一致：画像主体同样来自令牌。"""
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
    assert seen["profile_principal"] == _principal()


def test_sessions_exposes_token_owner(client, monkeypatch):
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
    assert d["users"]["sa"] == "test-human"
