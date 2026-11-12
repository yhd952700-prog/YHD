"""历史裁剪（context_budget）回归：预算、轮边界、摘要与失败文案。

背景（真实缺陷）：``LiuHaoAssistant`` 过去只按固定条数（20 条）截断历史，
与模型真实窗口无关——最近 20 条本身就超过窗口时，prompt 会原样发出去，
上游必然返回 400；而长对话在 128k 模型上又被过早丢掉。

这里钉死 ``trim_history`` 的行为：
1. 裁剪后 prompt 的 token 估算不超预算（reproduce case：60 轮长历史）；
2. 第一轮与最后一轮都不会凭空消失（第一轮进确定性摘要，最后一轮原样保留）；
3. system 段与当前这一句永不裁；按轮裁、不留孤立 assistant；≥2 轮、≤6 轮；
4. 装不下时抛 ``ContextBudgetExceeded``，文案是「对话太长」，不是「检查密钥」。
"""

from __future__ import annotations

import logging

import pytest

import src.ai.personal_context as personal_context_module
from src.ai import liuhao as lh_module
from src.ai.context_budget import (
    CONTEXT_WINDOW_ENV,
    DEFAULT_KEEP_RECENT_TURNS,
    DEFAULT_WINDOW_TOKENS,
    ContextBudgetExceeded,
    estimate_tokens,
    resolve_context_window,
    trim_history,
)
from src.ai.conversation_store import ConversationStore
from src.ai.liuhao import LiuHaoAssistant
from src.ai.providers import MockProvider, user_facing_failure


@pytest.fixture(autouse=True)
def _clear_context_window_env(monkeypatch):
    """每个用例都从"未设置覆盖"开始，避免开发机上的环境变量污染断言。"""
    monkeypatch.delenv(CONTEXT_WINDOW_ENV, raising=False)


# ==================== 纯函数：token 估算 / 窗口解析 ====================
class TestEstimateTokens:
    def test_cjk_one_char_one_token(self):
        assert estimate_tokens("中文字") == 3

    def test_ascii_four_chars_one_token(self):
        assert estimate_tokens("abcd") == 1
        assert estimate_tokens("abcde") == 2

    def test_mixed_text(self):
        # 1 个 CJK = 1；1 个 ASCII 向上取整 = 1。
        assert estimate_tokens("a中") == 2

    @pytest.mark.parametrize("value", [None, "", 0, [], {}])
    def test_non_text_counts_zero(self, value):
        assert estimate_tokens(value) == 0


class TestSingleTokenHeuristicAcrossModules:
    """同一段文字在裁剪、配额、账本、知识库四处必须得出同一个数。

    回归背景：40 个中文字符在 ``context_budget`` 里算 40 token，账本侧却按
    ``len // 4`` 只记 10 token——两套口径差 4 倍。收敛后只允许一处实现，
    这里用具体数字把一致性钉死。
    """

    @pytest.mark.parametrize("size", [40, 200, 1200])
    def test_trim_quota_and_ledger_agree(self, size):
        from src.gateway import chat as chat_module

        text = "中" * size

        # 裁剪判定使用的数字。
        plan = trim_history(
            [], window_tokens=DEFAULT_WINDOW_TOKENS, system_text="", current_message=text
        )
        # 配额拦截使用的数字。
        quota_tokens = lh_module._history_token_count(
            [{"role": "user", "content": text}]
        )
        # 用量账本回退估算使用的数字（非流式与流式回复共用同一函数）。
        ledger_tokens = chat_module.estimate_tokens(text)

        assert plan.estimated_tokens == size
        assert quota_tokens == size
        assert ledger_tokens == size

    def test_all_modules_call_the_one_implementation(self):
        from src.ai import agent_factory
        from src.gateway import chat as chat_module
        from src.gateway import knowledge_context

        assert chat_module.estimate_tokens is estimate_tokens
        assert knowledge_context.estimate_tokens is estimate_tokens
        assert lh_module.estimate_tokens is estimate_tokens
        # 第二套口径不得复活。
        assert not hasattr(chat_module, "_estimate_tokens")
        assert not hasattr(knowledge_context, "_CHARS_PER_TOKEN")
        assert not hasattr(agent_factory, "CHARS_PER_TOKEN")

    def test_knowledge_context_reports_the_same_estimate(self, monkeypatch):
        from src.gateway import knowledge_context as kc
        from src.knowledge import documents

        long_text = "知识" * 2000

        class _Service:
            def status(self):
                return {"enabled": True}

            def search(self, *args, **kwargs):
                return {
                    "retrieval": {
                        "status": "ok",
                        "reason": None,
                        "top_score": 0.9,
                        "runner_up_score": 0.4,
                        "thresholds": {},
                        "hits": [
                            {
                                "doc_id": "doc#1",
                                "text": long_text,
                                "score": 0.9,
                                "metadata": {"chunk_index": 0},
                            }
                        ],
                    }
                }

        monkeypatch.setattr(documents, "get_knowledge_service", lambda: _Service())

        result = kc.build_knowledge_context("长文档")
        assert result["used"] is True
        assert result["context_tokens"] == kc.estimate_tokens(result["context"])
        assert result["context_tokens"] <= kc.KNOWLEDGE_MAX_CONTEXT_TOKENS


class TestResolveContextWindow:
    def test_configured_wins(self):
        assert resolve_context_window(
            configured=4096, provider="openai", model="gpt-4o"
        ) == (4096, "config")

    def test_non_positive_configured_is_ignored(self):
        assert resolve_context_window(configured=0, model="gpt-4o") == (
            128000,
            "known",
        )

    def test_env_override_beats_known_family(self, monkeypatch):
        monkeypatch.setenv(CONTEXT_WINDOW_ENV, "131072")
        assert resolve_context_window(provider="openai", model="gpt-4o") == (
            131072,
            "env",
        )

    def test_env_override_beats_configured_value(self, monkeypatch):
        monkeypatch.setenv(CONTEXT_WINDOW_ENV, "131072")
        assert resolve_context_window(configured=4096, model="gpt-4o") == (
            131072,
            "env",
        )

    def test_env_bounds_are_inclusive(self, monkeypatch):
        monkeypatch.setenv(CONTEXT_WINDOW_ENV, "2048")
        assert resolve_context_window(model="no-such-model") == (2048, "env")
        monkeypatch.setenv(CONTEXT_WINDOW_ENV, "1000000")
        assert resolve_context_window(model="no-such-model") == (1000000, "env")

    @pytest.mark.parametrize("raw", ["abc", "128k", "8192.0", "8,192"])
    def test_non_numeric_env_falls_back_with_warning(self, raw, monkeypatch, caplog):
        monkeypatch.setenv(CONTEXT_WINDOW_ENV, raw)
        with caplog.at_level(logging.WARNING, logger="liuhao.ai.context_budget"):
            window, source = resolve_context_window(provider="openai", model="gpt-4o")
        assert (window, source) == (128000, "known")
        warned = [r.getMessage() for r in caplog.records]
        assert any(
            "evt=context.window.invalid" in line and "reason=not_an_integer" in line
            for line in warned
        )

    @pytest.mark.parametrize("raw", ["1024", "2047", "-100", "1000001", "999999999"])
    def test_out_of_range_env_falls_back_with_warning(self, raw, monkeypatch, caplog):
        monkeypatch.setenv(CONTEXT_WINDOW_ENV, raw)
        with caplog.at_level(logging.WARNING, logger="liuhao.ai.context_budget"):
            window, source = resolve_context_window(model="no-such-model")
        assert (window, source) == (DEFAULT_WINDOW_TOKENS, "default")
        warned = [r.getMessage() for r in caplog.records]
        assert any(
            "evt=context.window.invalid" in line and "reason=out_of_range" in line
            for line in warned
        )

    @pytest.mark.parametrize("raw", ["", "   "])
    def test_blank_env_is_treated_as_unset(self, raw, monkeypatch, caplog):
        monkeypatch.setenv(CONTEXT_WINDOW_ENV, raw)
        with caplog.at_level(logging.WARNING, logger="liuhao.ai.context_budget"):
            window, source = resolve_context_window(provider="openai", model="gpt-4o")
        assert (window, source) == (128000, "known")
        assert not caplog.records, "空值不算非法，不应制造 warn 噪音"

    @pytest.mark.parametrize(
        ("provider", "model", "expected"),
        [
            ("openai", "gpt-4o-mini", 128000),
            ("anthropic", "claude-3-5-sonnet", 200000),
            ("deepseek", "deepseek-chat", 65536),
            ("ollama", "qwen2.5", 32768),
        ],
    )
    def test_known_families(self, provider, model, expected):
        assert resolve_context_window(provider=provider, model=model) == (
            expected,
            "known",
        )

    def test_unknown_model_falls_back_to_conservative_default(self):
        assert resolve_context_window(provider="custom", model="no-such-model") == (
            DEFAULT_WINDOW_TOKENS,
            "default",
        )


# ==================== trim_history ====================
SYSTEM_TEXT = "你是测试助手，必须遵守以下规则：" + "规则内容保持简洁但完整。" * 20
CURRENT = "最后一句：请把今天所有待办按优先级排一下。"


def _long_history(turns: int) -> list:
    """构造 ``turns`` 轮长历史：每轮约 900 token，总计远超 8192 窗口。"""
    history: list = []
    for i in range(1, turns + 1):
        history.append(
            {
                "role": "user",
                "content": (
                    f"第{i}轮：请帮我整理今天的会议记录，逐条列出结论、待办、"
                    "负责人和截止时间。" + "明细" * 100
                ),
            }
        )
        history.append(
            {
                "role": "assistant",
                "content": f"第{i}轮回复："
                + "这是整理后的会议纪要，包含结论、待办和负责人。" * 28,
            }
        )
    return history


def _assert_prompt_within_budget(plan):
    actual = sum(estimate_tokens(m.get("content")) for m in plan.messages)
    assert actual <= plan.prompt_budget_tokens, (
        f"prompt 估算 {actual} 超过预算 {plan.prompt_budget_tokens}"
    )
    assert plan.estimated_tokens <= plan.prompt_budget_tokens


class TestTrimHistoryReproduceCase:
    """复现用例：历史条数与长度都超过窗口。"""

    def test_prompt_stays_in_budget_and_keeps_first_and_last_turn(self):
        history = _long_history(60)
        total_history = sum(estimate_tokens(m["content"]) for m in history)
        window = DEFAULT_WINDOW_TOKENS  # 8192
        assert total_history > window, "前提：历史总长必须超过窗口"

        plan = trim_history(
            history,
            window_tokens=window,
            system_text=SYSTEM_TEXT,
            current_message=CURRENT,
            window_source="default",
        )

        # 1）发出去的 prompt token 估算不超预算。
        _assert_prompt_within_budget(plan)
        assert plan.window_tokens == window
        assert plan.prompt_budget_tokens == window - max(1024, int(window * 0.10))

        # 2）最后一轮原样保留（user + assistant 都在）。
        contents = [m["content"] for m in plan.messages]
        assert any(c.startswith("第60轮：") for c in contents)
        assert any(c.startswith("第60轮回复：") for c in contents)

        # 3）第一轮没有凭空消失：被裁掉的早期历史折进了确定性摘要。
        assert plan.dropped_turns >= 1
        assert "第1轮：" in plan.summary

        # 4）保留轮数在 [2, 6] 内（默认最多 6 轮）。
        assert 2 <= plan.kept_turns <= DEFAULT_KEEP_RECENT_TURNS
        assert plan.kept_turns == DEFAULT_KEEP_RECENT_TURNS

        # 5）system 段永不裁；当前这一句永远最后一条。
        assert plan.messages[0]["role"] == "system"
        assert plan.messages[0]["content"].startswith(SYSTEM_TEXT)
        assert plan.messages[-1] == {"role": "user", "content": CURRENT}

        # 6）按轮裁：system 之后第一条必须是 user，不能留孤立 assistant。
        assert plan.messages[1]["role"] == "user"

    def test_old_fixed_20_messages_would_exceed_the_window(self):
        """证明旧实现（固定取最近 20 条）确实会超预算——这就是要修的问题。"""
        history = _long_history(60)
        window = DEFAULT_WINDOW_TOKENS
        budget = window - max(1024, int(window * 0.10))

        legacy = [{"role": "system", "content": SYSTEM_TEXT}]
        legacy.extend(history[-20:])
        legacy.append({"role": "user", "content": CURRENT})
        legacy_tokens = sum(estimate_tokens(m["content"]) for m in legacy)

        assert legacy_tokens > budget
        plan = trim_history(
            history,
            window_tokens=window,
            system_text=SYSTEM_TEXT,
            current_message=CURRENT,
        )
        _assert_prompt_within_budget(plan)


class TestTrimHistoryBoundaries:
    def test_short_history_is_kept_whole_without_summary(self):
        history = [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好，有什么可以帮你？"},
            {"role": "user", "content": "今天几号"},
            {"role": "assistant", "content": "今天是 11 月 8 日。"},
        ]
        plan = trim_history(
            history,
            window_tokens=8192,
            system_text="system",
            current_message="谢谢",
        )
        assert plan.kept_turns == 2
        assert plan.dropped_turns == 0
        assert plan.dropped_messages == 0
        assert plan.summary == ""
        assert plan.messages[1:-1] == history

    def test_min_two_turns_is_enforced_even_if_cap_is_lower(self):
        """``keep_recent_turns=1`` 时仍要保住最近 2 轮（不把上下文切到只剩一句）。"""
        history = _long_history(8)
        plan = trim_history(
            history,
            window_tokens=8192,
            system_text="system",
            current_message="继续",
            keep_recent_turns=1,
            min_recent_turns=2,
        )
        assert plan.kept_turns == 2
        assert plan.messages[1]["role"] == "user"
        assert plan.messages[2]["role"] == "assistant"

    def test_leading_assistant_fragment_never_becomes_orphan(self):
        """持久化历史首条可能是 assistant（上一轮被截断）：只保留后续完整轮。"""
        history = [
            {"role": "assistant", "content": "上一轮回复（其 user 已丢失）"},
            {"role": "user", "content": "问题一"},
            {"role": "assistant", "content": "回答一"},
            {"role": "user", "content": "问题二"},
            {"role": "assistant", "content": "回答二"},
        ]
        plan = trim_history(
            history,
            window_tokens=8192,
            system_text="system",
            current_message="问题三",
            keep_recent_turns=1,
            min_recent_turns=1,
        )
        assert plan.messages[1]["role"] == "user"
        assert "上一轮回复" in plan.summary

    def test_model_summary_switch_uses_callback(self):
        """接模型摘要只是可选开关：传 ``summarize`` 就用回调的结论。"""
        history = _long_history(10)
        plan = trim_history(
            history,
            window_tokens=8192,
            system_text="system",
            current_message="继续",
            summarize=lambda dropped: "模型生成的摘要",
        )
        assert plan.summary == "模型生成的摘要"

    def test_early_summary_takes_priority(self):
        history = _long_history(10)
        plan = trim_history(
            history,
            window_tokens=8192,
            system_text="system",
            current_message="继续",
            early_summary="外部算好的滚动摘要",
            summarize=lambda dropped: "不应被调用",
        )
        assert plan.summary == "外部算好的滚动摘要"


class TestSingleOversizedMessage:
    """单条超长消息也必须计入估算：轮数没超，token 也不能爆。"""

    def test_huge_reply_in_older_turn_is_dropped_and_summarized(self):
        history = _long_history(20)
        huge = "第3轮回复：" + "超长内容" * 3000  # 12000+ 字
        history[5] = {"role": "assistant", "content": huge}

        plan = trim_history(
            history,
            window_tokens=DEFAULT_WINDOW_TOKENS,
            system_text=SYSTEM_TEXT,
            current_message=CURRENT,
        )

        # 这条大户单条就超过整个输入预算——裁剪必须把它算进估算并排除掉。
        assert estimate_tokens(huge) > plan.prompt_budget_tokens
        _assert_prompt_within_budget(plan)
        assert all(m["content"] != huge for m in plan.messages)
        assert plan.kept_turns == DEFAULT_KEEP_RECENT_TURNS
        assert "第3轮回复" in plan.summary

    def test_huge_reply_in_newest_turn_keeps_question_and_drops_reply(self):
        history = _long_history(8)
        huge = "第8轮回复：" + "超长内容" * 3000
        history[-1] = {"role": "assistant", "content": huge}

        plan = trim_history(
            history,
            window_tokens=DEFAULT_WINDOW_TOKENS,
            system_text=SYSTEM_TEXT,
            current_message=CURRENT,
        )

        _assert_prompt_within_budget(plan)
        # 整轮装不下 → 只丢这条超大回复，user 提问仍保留（不判超限、不丢整轮）。
        assert plan.kept_turns == 2
        contents = [m["content"] for m in plan.messages]
        assert not any(c == huge for c in contents)
        assert any(c.startswith("第8轮：") for c in contents)
        assert plan.messages[1]["role"] == "user"
        assert plan.dropped_messages >= 1


class TestContextBudgetExceeded:
    def test_system_and_current_alone_overflow(self):
        with pytest.raises(ContextBudgetExceeded) as raised:
            trim_history(
                [],
                window_tokens=2048,  # 预算 2048 - 1024 = 1024
                system_text="字" * 1200,
                current_message="你好",
            )
        exc = raised.value
        assert exc.upstream_kind == "context_length_exceeded"
        assert exc.window_tokens == 2048

    def test_two_turns_do_not_fit(self):
        with pytest.raises(ContextBudgetExceeded):
            trim_history(
                [
                    {"role": "user", "content": "大问题一" * 300},
                    {"role": "assistant", "content": "大回答一" * 300},
                    {"role": "user", "content": "大问题二" * 300},
                    {"role": "assistant", "content": "大回答二" * 300},
                ],
                window_tokens=4096,  # 预算 4096 - 1024 = 3072
                system_text="system",
                current_message="继续",
                min_recent_turns=2,
            )

    def test_user_facing_copy_is_about_length_not_keys(self):
        exc = ContextBudgetExceeded(window_tokens=8192, needed_tokens=9000)
        text = user_facing_failure(exc)
        assert "对话太长" in text
        assert "密钥" not in text
        assert "网络" not in text


# ==================== 接入 LiuHaoAssistant._build_messages ====================
@pytest.fixture
def isolate_store(tmp_path, monkeypatch):
    store = ConversationStore(db_path=str(tmp_path / "conv.db"))
    monkeypatch.setattr(lh_module, "get_conversation_store", lambda: store)
    return store


@pytest.fixture
def no_profile(monkeypatch):
    """不让真实画像库影响 system 段长度（本文件的断言只关心裁剪本身）。"""

    class _Empty:
        def has_profile(self, principal_id: str) -> bool:
            return False

    monkeypatch.setattr(
        personal_context_module, "get_personal_context", lambda: _Empty()
    )


def _assistant(name: str) -> LiuHaoAssistant:
    return LiuHaoAssistant(
        name=name, provider=MockProvider(name="test", model="mock-model")
    )


def test_build_messages_uses_budget_trimming(isolate_store, no_profile):
    a = _assistant("ctx-budget-build")
    a.history = _long_history(60)

    messages = a._build_messages("最后一问")

    assert messages[0]["role"] == "system"
    assert messages[-1] == {"role": "user", "content": "最后一问"}
    assert messages[1]["role"] == "user"

    window, source = resolve_context_window(model="mock-model")
    budget = window - max(1024, int(window * 0.10))
    actual = sum(estimate_tokens(m["content"]) for m in messages)
    assert actual <= budget, f"{actual} > {budget}（window_source={source}）"

    contents = [m["content"] for m in messages]
    assert any(c.startswith("第60轮：") for c in contents)
    assert "第1轮：" in messages[0]["content"]


def test_chat_reports_context_overflow_instead_of_500(isolate_store, no_profile):
    """裁剪装不下时 ``chat`` 返回「对话太长」，而不是冒出 500 / 让上游 400。"""
    a = _assistant("ctx-budget-chat")
    a.system_prompt = "字" * 8000  # 预算 7168，仅 system 就超了

    result = a.chat("你好")

    assert result["status"] == "error"
    assert "对话太长" in result["reply"]
    assert "密钥" not in result["reply"]
    assert a.last_turn_reason == "context_length_exceeded"


def test_trim_emits_structured_log_without_message_content(
    caplog, isolate_store, no_profile
):
    """裁剪发生时要留结构化日志：能回查"是不是历史被裁掉了"。"""
    a = _assistant("ctx-budget-log")
    a.history = _long_history(20)

    with caplog.at_level(logging.INFO, logger="liuhao.liuhao"):
        a._build_messages("最后一问")

    lines = [
        r.getMessage()
        for r in caplog.records
        if r.getMessage().startswith("evt=context.trim")
    ]
    assert lines, "裁剪时必须留下 evt=context.trim 日志"

    line = lines[-1]
    assert "principal=ctx-budget-log" in line
    for field in (
        "kept_turns=",
        "dropped_turns=",
        "estimated_tokens=",
        "window_tokens=",
        "source=",
    ):
        assert field in line
    assert "第1轮" not in line, "日志只记计数与预算，不能打印消息内容"
