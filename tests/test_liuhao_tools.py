"""工具调用（tool registry + 提示词约束 JSON 解析）测试。

覆盖：工具集构建、调用协议解析、工具执行、工具调用 loop 闭环。
"""

from __future__ import annotations

import pytest

from src.ai import liuhao as lh_module
from src.ai.conversation_store import ConversationStore
from src.ai.providers import BaseProvider, MockProvider
from src.ai.liuhao import LiuHaoAssistant
from src.ai.tools import build_tool_prompt, make_tools, parse_tool_call


@pytest.fixture(autouse=True)
def isolate_conversation_store(tmp_path, monkeypatch):
    store = ConversationStore(db_path=str(tmp_path / "conv.db"))
    monkeypatch.setattr(lh_module, "get_conversation_store", lambda: store)
    return store


def make_assistant(name: str = "tool-test", provider=None) -> LiuHaoAssistant:
    return LiuHaoAssistant(
        name=name,
        provider=provider or MockProvider(name="test", model="mock-model"),
    )


# ---------------------------------------------------------------------- #
# 工具集构建
# ---------------------------------------------------------------------- #
def test_make_tools_has_three_builtins():
    tools = make_tools("alice", status_fn=lambda: {"turn": 0})
    assert {t.name for t in tools} == {"search_memory", "query_audit", "system_status"}


def test_tools_registered_active():
    a = make_assistant()
    assert len(a._tool_list) == 3
    for t in a._tool_list:
        assert a.tools.status(t.tool_id).value == "active"


def test_build_tool_prompt_lists_tools():
    tools = make_tools("alice", status_fn=lambda: {"turn": 0})
    prompt = build_tool_prompt(tools)
    assert "search_memory" in prompt
    assert "query_audit" in prompt
    assert "system_status" in prompt


# ---------------------------------------------------------------------- #
# 调用协议解析
# ---------------------------------------------------------------------- #
def test_parse_tool_call_plain_json():
    assert parse_tool_call('{"tool": "search_memory", "args": {"query": "x"}}') == (
        "search_memory",
        {"query": "x"},
    )


def test_parse_tool_call_json_code_block():
    text = '```json\n{"tool": "system_status", "args": {}}\n```'
    assert parse_tool_call(text) == ("system_status", {})


def test_parse_tool_call_non_tool():
    assert parse_tool_call("你好，我是鎏灏。") is None
    assert parse_tool_call('{"foo": "bar"}') is None  # 无 tool 字段
    assert parse_tool_call("不是 JSON") is None


# ---------------------------------------------------------------------- #
# 工具执行
# ---------------------------------------------------------------------- #
def test_execute_search_memory_finds_stored():
    a = make_assistant(name="tool-mem")
    a.chat("hello world")
    out = a._execute_tool("search_memory", {"query": "hello"})
    assert "hello" in out


def test_execute_unknown_tool():
    a = make_assistant()
    assert "未知工具" in a._execute_tool("no_such_tool", {})


def test_execute_system_status():
    a = make_assistant(name="tool-status")
    out = a._execute_tool("system_status", {})
    assert "MockProvider" in out  # stats 含 provider 类名


# ---------------------------------------------------------------------- #
# 工具调用 loop 闭环
# ---------------------------------------------------------------------- #
class ToolThenTextProvider(BaseProvider):
    """第一次 chat 返回工具调用，第二次返回最终回复。"""

    def __init__(self):
        super().__init__(name="test", model="mock-model")
        self.calls = 0

    def chat(self, messages, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return '{"tool": "system_status", "args": {}}'
        return "系统状态已查询。"


def test_chat_executes_tool_then_answers():
    p = ToolThenTextProvider()
    a = make_assistant(name="tool-loop", provider=p)
    result = a.chat("查一下系统状态")
    assert result["status"] == "completed"
    assert "系统状态已查询" in result["reply"]
    assert p.calls == 2  # 工具调用一次 + 最终回复一次


def test_chat_tool_loop_terminates_at_max_rounds():
    """LLM 持续请求工具时，loop 应在 MAX_TOOL_ROUNDS 后终止（防死循环）。"""

    class AlwaysToolProvider(BaseProvider):
        def __init__(self):
            super().__init__(name="test", model="mock-model")
            self.calls = 0

        def chat(self, messages, **kwargs):
            self.calls += 1
            return '{"tool": "system_status", "args": {}}'

    p = AlwaysToolProvider()
    a = make_assistant(name="tool-maxround", provider=p)
    result = a.chat("查状态")
    # 最终回复是最后一次工具调用 JSON（诚实返回，不伪造成功文本）
    assert result["status"] == "completed"
    assert p.calls == lh_module.MAX_TOOL_ROUNDS


# ---------------------------------------------------------------------- #
# 流式工具事件（chat_stream 应 yield 结构化 tool 事件）
# ---------------------------------------------------------------------- #
def test_chat_stream_yields_tool_event():
    """流式对话命中工具时，应 yield 结构化 tool 事件（含 raw 原文）。"""

    class StreamingToolProvider(BaseProvider):
        def __init__(self):
            super().__init__(name="test", model="mock-model")
            self.calls = 0

        def chat_stream(self, messages, **kwargs):
            self.calls += 1
            if self.calls == 1:
                yield '{"tool": "system_status", "args": {}}'
            else:
                yield "系统状态已查询。"

    p = StreamingToolProvider()
    a = make_assistant(name="tool-stream", provider=p)
    items = list(a.chat_stream("查状态"))

    tool_events = [i for i in items if isinstance(i, dict)]
    text_tokens = [i for i in items if isinstance(i, str)]

    assert len(tool_events) == 1
    assert tool_events[0]["type"] == "tool"
    assert tool_events[0]["name"] == "system_status"
    assert tool_events[0]["raw"] == '{"tool": "system_status", "args": {}}'
    assert isinstance(tool_events[0]["output"], str) and tool_events[0]["output"]
    # 最终回复 token 存在（工具 JSON 原文也会诚实流出，前端用 raw 剥离）
    assert "系统状态已查询" in "".join(text_tokens)
    # 收尾落盘：turn 递增、历史写入两条（user + assistant）
    assert a.turn == 1
    assert len(a.history) == 2
    assert p.calls == 2  # 工具调用一轮 + 最终回复一轮

