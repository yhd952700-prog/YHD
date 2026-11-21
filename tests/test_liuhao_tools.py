"""工具调用（tool registry + 提示词约束 JSON 解析）测试。

覆盖：工具集构建、调用协议解析、工具执行、工具调用 loop 闭环。
"""

from __future__ import annotations

import json

import pytest

from src.ai import liuhao as lh_module
from src.ai.conversation_store import ConversationStore
from src.ai.providers import BaseProvider, MockProvider
from src.ai.liuhao import LiuHaoAssistant, coerce_tool_output
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
def test_make_tools_builtin_set_is_exact():
    """内置工具集合是**精确**断言，不是"至少包含"。

    刻意不放松成子集判断：新增工具必须显式更新这里，从而保证每一次扩大"模型可
    调用的动作面"都是一次有意识的决定 —— 而不是某天悄悄多出来一只手。
    """
    tools = make_tools("alice", status_fn=lambda: {"turn": 0})
    # 0.4.6 技能页透明化 §9.3 注册闸：默认集 = A 档（3 内置 + 7 插件安全子集
    # + system_status）。B 档（python_compute / personal_* / read_file /
    # list_files / manage_reminders 等）默认关，须在技能页显式启用；
    # 全集精确断言见 test_make_tools_full_set_is_exact_with_disabled_included。
    expected = {
        "search_memory",
        "query_audit",
        "system_status",
        "calculate",
        "base64_text",
        "hash_text",
        "convert_units",
        "timezone_time",
        "generate_uuid",
        "roman_numeral",
        "make_slug",
        # 大升级 U1/U2/U3/U4 新增 A 档（只读/汇总，默认开）：
        "usage_report",
        "check_update",
        "voice_status",
        "morning_briefing",
        # 第二季 V7 新增 A 档（技能包只读查询，默认开）：
        "list_skill_packages",
        "inspect_skill_package",
        # 大升级第二季 V3 新增 A 档（只读状态，默认开）：
        "android_app_status",
    }
    assert {t.name for t in tools} == expected


def test_make_tools_full_set_is_exact_with_disabled_included():
    """include_disabled=True 时的全集精确断言（注册闸的另一面）。"""
    tools = make_tools("alice", status_fn=lambda: {"turn": 0}, include_disabled=True)
    expected = {
        "search_memory", "query_audit", "system_status",
        "calculate", "base64_text", "hash_text", "convert_units",
        "timezone_time", "generate_uuid", "roman_numeral", "make_slug",
        "python_compute", "personal_set_preference", "personal_add_fact",
        "personal_set_display_name", "read_file", "list_files",
        "manage_reminders", "knowledge_search", "web_search",
        "delegate_to_team", "run_skill",
        # 完善第二轮 D-R1 新增（精而简审计补聊天通路，均为 B 档默认关）：
        "manage_bots", "backup_now",
        # 大升级 U1–U5 新增：A 档默认开 4 个（见默认集断言）+ B 档默认关 3 个
        # （apply_update 高风险正式确认 / routine_templates / device_sync）：
        "usage_report", "check_update", "voice_status", "morning_briefing",
        "apply_update", "routine_templates", "device_sync",
        # 第二季 V2 新增（B 档默认关）：简报语音播报 / Web Push 管理。
        "briefing_broadcast", "push_notifications",
        # 第二季 V7 新增：读 2 个 A 档默认开（见默认集断言）、写 3 个 B 档默认关。
        "list_skill_packages", "inspect_skill_package",
        "install_skill_package", "upgrade_skill_package", "uninstall_skill_package",
        # 第二季 V10 新增（B 档默认关）：加密云备份。
        "cloud_backup",
        # 大升级第二季 V3 新增 A 档（默认开，全集里同样在册）：
        "android_app_status",
        # 第二季 V6 新增（B 档默认关，写本地知识库）：聊天一句话存文档。
        "knowledge_ingest",
        # 第二季 V8 新增（B 档默认关）：团队编排（多 Bot 派活，工具内确认门）。
        "team_orchestrate",
        # 第二季 V9 新增（B 档默认关）：成本智能路由开关。
        "cost_routing",
    }
    assert {t.name for t in tools} == expected


def test_tools_registered_active():
    a = make_assistant()
    # 16 = 0.4.6 A 档默认集（3 内置 + 7 插件安全子集 + system_status）
    # + 大升级新增 A 档 4 个（usage_report / check_update / voice_status /
    # morning_briefing），见 test_make_tools_builtin_set_is_exact。
    # + 第二季 V7 新增 A 档 2 个（list_skill_packages / inspect_skill_package）
    # + 第二季 V3 新增 A 档 1 个（android_app_status）。
    # B 档默认关，不进注册表。
    assert len(a._tool_list) == 18
    for t in a._tool_list:
        assert a.tools.status(t.tool_id).value == "active"


def test_tool_registration_is_shared_once_per_profile(monkeypatch):
    """同一画像主体只注册一次工具：第二个冷会话直接复用已激活的注册表。

    注册/审批/激活每一步都写审计；若每个冷会话都重放，冷启动成本会随会话数
    线性增长（实测 10 个工具 × 4 步 ≈ 40 条同步写入）。
    """
    lh_module._TOOL_CACHE.clear()
    builds = []
    real_make_tools = lh_module.make_tools

    def counting_make_tools(principal, status_fn=None):
        builds.append(principal)
        return real_make_tools(principal, status_fn=status_fn)

    monkeypatch.setattr(lh_module, "make_tools", counting_make_tools)
    try:
        first = LiuHaoAssistant(
            name="session-a",
            profile_principal="user-shared",
            provider=MockProvider(name="test", model="mock-model"),
        )
        second = LiuHaoAssistant(
            name="session-b",
            profile_principal="user-shared",
            provider=MockProvider(name="test", model="mock-model"),
        )

        assert builds == ["user-shared"], "同一主体不应重复构建工具集"
        assert first.tools is second.tools, "注册表应被复用"
        assert first._tool_list is second._tool_list
        assert all(
            first.tools.status(tool.tool_id).value == "active"
            for tool in first._tool_list
        )
    finally:
        lh_module._TOOL_CACHE.clear()


def test_tool_cache_is_bounded_and_evicts_lru(monkeypatch):
    """工具缓存有上限；超限按 LRU 淘汰，淘汰后下一次冷构造重建一次。"""
    lh_module._TOOL_CACHE.clear()
    monkeypatch.setenv("LIUHAO_TOOL_CACHE_MAX", "2")
    builds = []
    real_make_tools = lh_module.make_tools

    def counting_make_tools(principal, status_fn=None):
        builds.append(principal)
        return real_make_tools(principal, status_fn=status_fn)

    monkeypatch.setattr(lh_module, "make_tools", counting_make_tools)

    def build(principal: str) -> LiuHaoAssistant:
        return LiuHaoAssistant(
            name=f"session-{principal}",
            profile_principal=principal,
            provider=MockProvider(name="test", model="mock-model"),
        )

    try:
        build("user-a")
        build("user-b")
        # 触碰 user-a：user-b 成为最久未使用，应被淘汰。
        lh_module._shared_tools("user-a")
        build("user-c")

        assert len(lh_module._TOOL_CACHE) == 2, "工具缓存不得超过配置上限"
        assert "user-b" not in lh_module._TOOL_CACHE
        assert builds.count("user-b") == 1

        # 被淘汰的主体再次使用时会重建一次（只影响冷构造，不影响已有会话）。
        build("user-b")
        assert builds.count("user-b") == 2
        assert len(lh_module._TOOL_CACHE) == 2
    finally:
        lh_module._TOOL_CACHE.clear()


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
    assert isinstance(out, (dict, list)), "工具结果必须是结构化 JSON，不是 repr 字符串"
    assert "hello" in json.dumps(out, ensure_ascii=False)


def test_execute_unknown_tool():
    a = make_assistant()
    out = a._execute_tool("no_such_tool", {})
    assert isinstance(out, dict)
    assert "未知工具" in out["error"]


def test_execute_system_status():
    a = make_assistant(name="tool-status")
    out = a._execute_tool("system_status", {})
    assert isinstance(out, dict)
    assert "MockProvider" in json.dumps(out, ensure_ascii=False)  # stats 含 provider 类名


def test_execute_python_compute_real(tmp_path, monkeypatch):
    """驾驶舱助手的计算工具必须真执行（返回真实计算结果，非模拟）。

    python_compute 是 B 档（默认关）：先按技能页的真实路径显式启用
    （开关文件落在临时工作区，不污染仓库），再走助手执行。
    """
    monkeypatch.setenv("LIUHAO_WORKSPACE_ROOT", str(tmp_path))
    from src.gateway.chat_tool_settings import set_chat_tool_enabled

    set_chat_tool_enabled("liuhao.python.compute", True)
    lh_module._TOOL_CACHE.clear()
    a = make_assistant(name="tool-compute")
    out = a._execute_tool("python_compute", {"code": "result = sum(i * i for i in range(1, 11))"})
    rendered = json.dumps(out, ensure_ascii=False)
    assert "385" in rendered
    assert "executed" in rendered


def test_execute_python_compute_rejects_unsafe():
    """计算工具必须诚实拒绝不安全代码，而不是静默成功。"""
    a = make_assistant(name="tool-compute-bad")
    out = a._execute_tool("python_compute", {"code": "import os"})
    rendered = json.dumps(out, ensure_ascii=False).lower()
    assert '"success": false' in rendered or "rejected" in rendered


def test_coerce_tool_output_reads_legacy_repr_records():
    """D5 向后兼容：旧记录里的 Python repr 字符串要能还原成结构。"""
    assert coerce_tool_output("{'a': 1, 'b': None}") == {"a": 1, "b": None}
    assert coerce_tool_output("[1, 2, None]") == [1, 2, None]
    assert coerce_tool_output('{"a": 1}') == {"a": 1}
    # 普通文本原样保留，不被误解析。
    assert coerce_tool_output("hello") == "hello"
    assert coerce_tool_output("{not valid}") == "{not valid}"
    # 已经是结构化数据时不做任何改变。
    assert coerce_tool_output({"a": 1}) == {"a": 1}


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
    output = tool_events[0]["output"]
    assert isinstance(output, dict), "tool 事件的 output 必须是结构化 JSON"
    json.dumps(output)  # 必须可 JSON 序列化，前端无需解析 repr
    # 最终回复 token 存在（工具 JSON 原文也会诚实流出，前端用 raw 剥离）
    assert "系统状态已查询" in "".join(text_tokens)
    # 收尾落盘：turn 递增、历史写入两条（user + assistant）
    assert a.turn == 1
    assert len(a.history) == 2
    assert p.calls == 2  # 工具调用一轮 + 最终回复一轮

