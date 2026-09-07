"""鎏灏主控（LiuHaoAssistant）闭环测试。

验证「输入 → 授权 → 生成 → 记忆 → 审计」完整链路，使用 MockProvider
（不依赖真实 LLM，可离线全量回归）。
"""

from __future__ import annotations

import pytest

from src.ai.providers import MockProvider
from src.ai.liuhao import LiuHaoAssistant
from src.kernels.audit import audit_query


def make_assistant(name: str = "test-liuhao") -> LiuHaoAssistant:
    return LiuHaoAssistant(
        name=name,
        provider=MockProvider(name="test", model="mock-model"),
    )


def test_chat_completed_with_mock():
    a = make_assistant()
    result = a.chat("hello")
    assert result["status"] == "completed"
    assert result["reply"]
    assert result["turn"] == 1
    assert result["agent"] == "test-liuhao"


def test_chat_persists_history_and_memory():
    a = make_assistant(name="mem-isolated")
    before = a.memory.stats()["total_entries"]
    a.chat("hello")
    a.chat("what is the status")
    # 2 轮 × (user + assistant) = 4 条历史
    assert len(a.history) == 4
    assert a.turn == 2
    # 记忆 kernel 真实持久化（本实例本轮新增 2 条）
    after = a.memory.stats()["total_entries"]
    assert after - before == 2


def test_chat_audits():
    a = make_assistant()
    result = a.chat("hello")
    events = audit_query(correlation_id=result["correlation_id"])
    assert len(events) == 1
    assert events[0]["event_type"] == "access_allowed"
    assert events[0]["outcome"] == "allow"
    assert events[0]["principal_id"] == "test-liuhao"


def test_reset_clears_history():
    a = make_assistant()
    a.chat("hello")
    a.reset()
    assert a.history == []
    assert a.turn == 0


def test_stats_reports_provider_and_model():
    a = make_assistant()
    st = a.stats()
    assert st["provider"] == "MockProvider"
    assert st["model"] == "mock-model"
    assert st["turn"] == 0


def test_policy_only_allows_chat_action():
    """授权基线只放行 agent 主体的 chat 动作，其它动作不放行。"""
    a = make_assistant()
    assert a.policy.is_allowed("chat") is True
    # 其它动作无 allow 规则 → NOT_APPLICABLE → is_allowed False
    assert a.policy.is_allowed("delete_data") is False
    assert a.policy.is_allowed("execute_code") is False


def test_chat_history_feeds_context():
    """多轮历史应注入后续 prompt（mock 不校验，但 history 顺序须正确）。"""
    a = make_assistant()
    a.chat("first")
    msgs = a._build_messages("second")
    # system + (user:first, assistant:...) + user:second
    assert msgs[0]["role"] == "system"
    assert msgs[-1] == {"role": "user", "content": "second"}
    roles = [m["role"] for m in msgs]
    assert roles.count("user") == 2
    assert roles.count("assistant") == 1
