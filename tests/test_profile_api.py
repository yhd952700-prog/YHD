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

    token, _payload = get_jwt_handler().create_token(
        subject="reader-human", metadata={"kind": "human"}
    )
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


def test_delete_single_fact_keeps_sibling_key(client):
    """DELETE /v1/profile/facts/{key} 必须精确删除：city 不能带走 city_code。

    这是真实对外入口 + 真实内核的端到端回归：两条 key 共享前缀，旧实现按
    前缀删会让用户点一下「删除城市」就静默丢掉 city_code，且不可逆。
    """
    client.put("/v1/profile", json={"fact": {"key": "city", "value": "Shanghai"}})
    client.put("/v1/profile", json={"fact": {"key": "city_code", "value": "021"}})

    r = client.delete("/v1/profile/facts/city")
    assert r.status_code == 200
    assert r.json()["deleted"] is True

    assert client.get("/v1/profile").json()["facts"] == {"city_code": "021"}

    # 内核里的 PERSISTENT 副本同样只能少一条。
    from src.kernels.memory import get_memory_kernel

    kernel = get_memory_kernel()
    assert kernel.recall(f"profile:{_principal()}:fact:city") is None
    assert kernel.recall(f"profile:{_principal()}:fact:city_code").value == "021"


def test_delete_single_preference_keeps_sibling_key(client):
    client.put("/v1/profile", json={"preference": {"key": "tone", "value": "formal"}})
    client.put(
        "/v1/profile", json={"preference": {"key": "tone_style", "value": "short"}}
    )
    r = client.delete("/v1/profile/preferences/tone")
    assert r.status_code == 200
    assert r.json()["deleted"] is True
    assert set(client.get("/v1/profile").json()["preferences"]) == {"tone_style"}


def test_profile_operations_follow_business_semantics_not_http_verbs(client):
    """画像读写属于**业务读语义**：任何已登录人类都能读写自己的画像。

    授权按业务动作判定，不能因为用了 PUT/DELETE 就当成管理员写操作；
    真正的管理动作（审批凭据）仍必须显式 admin/L0。
    """
    reader = _reader_headers()
    assert client.get("/v1/profile", headers=reader).status_code == 200
    assert (
        client.put("/v1/profile", json={"display_name": "x"}, headers=reader).status_code
        == 200
    )
    assert client.delete("/v1/profile", headers=reader).status_code == 200

    resp = client.post(
        "/v1/policy/approvals",
        headers=reader,
        json={
            "actions": ["identity.grant_permission"],
            "target": "another-resource",
            "reason": "must not work",
        },
    )
    assert resp.status_code == 403
    assert "管理员" in resp.json()["detail"]


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
        # 产品现在会读 ``assistant.provider``（用量归属/模型名）；替身给个空值，
        # 但保留"画像主体来自令牌"的断言重点。
        provider = None

        def serialized_turn(self, **_kw):
            # 产品现在每轮都用会话级互斥锁串行化（见 src/gateway/chat.py）；
            # 替身没有并发写风险，返回空上下文即可。
            from contextlib import nullcontext

            return nullcontext()

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
        # 产品现在会读 ``assistant.provider``（用量归属/模型名）；替身给个空值，
        # 但保留"画像主体来自令牌"的断言重点。
        provider = None

        def serialized_turn(self, **_kw):
            # 产品现在每轮都用会话级互斥锁串行化（见 src/gateway/chat.py）；
            # 替身没有并发写风险，返回空上下文即可。
            from contextlib import nullcontext

            return nullcontext()

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
        # 产品现在会读 ``assistant.provider``（用量归属/模型名）；替身给个空值，
        # 但保留"画像主体来自令牌"的断言重点。
        provider = None

        def serialized_turn(self, **_kw):
            # 产品现在每轮都用会话级互斥锁串行化（见 src/gateway/chat.py）；
            # 替身没有并发写风险，返回空上下文即可。
            from contextlib import nullcontext

            return nullcontext()

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
