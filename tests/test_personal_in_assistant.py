"""KAREN 个人画像接入对话链路测试。

覆盖：
- ``personal_context.summarize()`` 被注入到 system prompt（Personalization）
- 三个个性化工具（set_preference / add_fact / set_display_name）真实写入画像
- 不同 principal 之间画像严格隔离
- 画像为空时 system prompt 不出现「用户画像」分隔段（无副作用）
"""

from __future__ import annotations

import json

import pytest

from src.ai import liuhao as lh_module
from src.ai import personal_context as pc_module
from src.ai.conversation_store import ConversationStore
from src.ai.providers import MockProvider
from src.ai.tools import make_tools


@pytest.fixture(autouse=True)
def isolate_state(tmp_path, monkeypatch):
    """每个用例独立：fresh conversation store + fresh personal context DB。"""
    store = ConversationStore(db_path=str(tmp_path / "conv.db"))
    monkeypatch.setattr(lh_module, "get_conversation_store", lambda: store)

    # personal_context 的 singleton 用 env 驱动 DB 路径；本测试用临时文件覆盖。
    # 注意：tools.py 用 `from .personal_context import get_personal_context`，
    # 已绑成本地引用，所以必须同时 patch tools 模块下的同名符号。
    db_path = str(tmp_path / "personal.db")
    fresh_mgr = pc_module.PersonalContextManager(db_path=db_path)
    import src.ai.tools as tools_module
    monkeypatch.setattr(tools_module, "get_personal_context", lambda: fresh_mgr)
    monkeypatch.setattr(pc_module, "get_personal_context", lambda: fresh_mgr)

    return {"conv": store, "personal": fresh_mgr}


def make_assistant(name: str = "karen-test", provider=None):
    from src.ai.liuhao import LiuHaoAssistant

    return LiuHaoAssistant(
        name=name,
        provider=provider or MockProvider(name="test", model="mock-model"),
    )


# ==================== 工具写真实画像 ====================


def test_set_preference_tool_writes_real_profile(isolate_state):
    tools = make_tools("alice", status_fn=lambda: {"turn": 0})
    fn = next(t.fn for t in tools if t.name == "personal_set_preference")
    out = fn(key="语言", value="简体中文", confidence=0.9)
    payload = json.loads(out)
    assert payload["status"] == "ok" and payload["key"] == "语言"
    # 读回画像：真的写进去了
    profile = isolate_state["personal"].get_profile("alice")
    assert profile.preferences["语言"].value == "简体中文"


def test_add_fact_tool_writes_real_profile(isolate_state):
    tools = make_tools("alice", status_fn=lambda: {"turn": 0})
    fn = next(t.fn for t in tools if t.name == "personal_add_fact")
    fn(key="公司", value="鎏灏科技")
    profile = isolate_state["personal"].get_profile("alice")
    assert profile.facts["公司"] == "鎏灏科技"


def test_set_display_name_tool_writes_real_profile(isolate_state):
    tools = make_tools("alice", status_fn=lambda: {"turn": 0})
    fn = next(t.fn for t in tools if t.name == "personal_set_display_name")
    fn(display_name="辛宏达")
    profile = isolate_state["personal"].get_profile("alice")
    assert profile.display_name == "辛宏达"


# ==================== system prompt 注入画像 ====================


def test_build_messages_injects_profile_summary(isolate_state):
    """预设偏好后，_build_messages 的 system 段必须包含画像摘要。"""
    isolate_state["personal"].set_preference("alice", "语言", "简体中文", confidence=0.95)
    isolate_state["personal"].record_fact("alice", "公司", "鎏灏科技")
    a = make_assistant("alice")
    messages = a._build_messages("你好")
    sys_msg = messages[0]
    assert sys_msg["role"] == "system"
    assert "用户画像" in sys_msg["content"]
    assert "简体中文" in sys_msg["content"]
    assert "鎏灏科技" in sys_msg["content"]


def test_build_messages_omits_profile_section_when_empty(isolate_state):
    """画像为空时，system prompt 不出现「用户画像」段（无副作用、不污染上下文）。"""
    a = make_assistant("brand-new-user")
    messages = a._build_messages("你好")
    assert "用户画像" not in messages[0]["content"]


def test_build_messages_isolated_by_principal(isolate_state):
    """不同 principal 的画像互不可见（per-principal 隔离）。"""
    isolate_state["personal"].set_preference("alice", "语言", "简体中文", confidence=0.9)
    a = make_assistant("bob")
    messages = a._build_messages("你好")
    assert "简体中文" not in messages[0]["content"]
