"""推理回复纯逻辑专属测试（完善第二轮 A4）。

覆盖可离线单测的部分：难度分类规则、回复结构化（含“没依据不许编”
的诚实口径）、事实冲突检测、反驳决策与话术。不碰真实模型调用。
"""

from __future__ import annotations

from src.ai.reasoned_reply import (
    check_fact_conflict,
    classify_difficulty,
    evaluate_rebuttal,
    format_rebuttal,
    structure_reply,
)


def test_classify_daily_for_simple_question():
    out = classify_difficulty("今天天气怎么样")
    assert out["level"] == "daily"
    assert out["matched_keywords"] == []


def test_classify_hard_on_explicit_deep_request():
    out = classify_difficulty("请深入分析这份财报的现金流问题")
    assert out["level"] == "hard"
    assert out["matched_explicit"]


def test_classify_hard_on_two_reasoning_keywords():
    out = classify_difficulty("比较这两种方案的优劣，并推导成本变化的原因")
    assert out["level"] == "hard"
    assert len(out["matched_keywords"]) >= 2


def test_classify_hard_on_long_with_one_keyword():
    text = "背景说明" * 120 + "请比较一下"  # >400 字 + 1 个推理词
    out = classify_difficulty(text)
    assert out["length"] >= 400
    assert out["level"] == "hard"


def test_classify_empty_text_is_daily():
    assert classify_difficulty("")["level"] == "daily"
    assert classify_difficulty(None)["level"] == "daily"


def test_structure_reply_extracts_conclusion():
    reply = structure_reply("结论：可以做。\n理由：成本可控。")
    assert "可以做" in reply.conclusion


def test_structure_reply_no_evidence_is_honest():
    reply = structure_reply("结论：先这样办。")
    assert reply.to_dict()["evidence"] == ["没找到依据"]


def test_structure_reply_keeps_given_sources():
    reply = structure_reply("结论：可以做。", sources=["https://example.com/a"])
    assert reply.to_dict()["evidence"] == ["https://example.com/a"]


_FACTS = [{"subject": "预算", "value": "5000 元", "source": "财务表"}]


def test_fact_conflict_number_mismatch():
    hit = check_fact_conflict("预算是 8000 元对吧", _FACTS)
    assert hit is not None
    assert hit["fact_value"] == "5000 元"
    assert hit["user_value"] == "8000"


def test_fact_conflict_no_hit_when_numbers_agree():
    assert check_fact_conflict("预算是 5000 元", _FACTS) is None
    assert check_fact_conflict("今天吃了吗", _FACTS) is None


def test_rebuttal_triggered_on_fact_conflict():
    decision = evaluate_rebuttal("预算是 8000 元", facts=_FACTS)
    assert decision.triggered is True
    assert decision.condition == "fact_conflict"
    rendered = format_rebuttal(decision)
    assert rendered  # 触发了就必须有话术


def test_rebuttal_silent_when_nothing_wrong():
    decision = evaluate_rebuttal("预算是 5000 元，按计划推进", facts=_FACTS)
    assert decision.triggered is False
    assert format_rebuttal(decision) == ""
