"""Regressions for the two summary-path defects found on the real chain.

D1: a verified link written as ``https://…（抓取时间 2026-11-07T…）`` used to
swallow the full-width ``（`` into the URL, so the link no longer matched the
executor's whitelist and the whole model summary was thrown away twice over
("unverified link" and then "dropped all real citations").

D2: the deterministic fallback cut excerpts with a raw ``text[:220]`` slice,
leaving half a word (``…功能与授``) which was then glued to the next part with
a space (``…功能与授 整理三条要点：``).
"""
from __future__ import annotations

from src.ai.llm_support import (
    _join_summary_parts,
    _shorten_text,
    _urls_in,
    compose_execution_summary,
)

REAL_URL = "https://example.com/ai-projects-2026"
OTHER_REAL = "https://example.com/ai-projects-2026/second"
FETCHED_AT = "2026-11-07T08:30:00Z"


def _records(result_text: str = "项目 A、B、C 值得关注。") -> list[dict]:
    return [
        {
            "name": "调研今日 AI 开源项目",
            "capability": "network_bus",
            "status": "completed",
            "result": {
                "result": result_text,
                "fetched_at": FETCHED_AT,
                "sources": [
                    {
                        "title": "AI 项目合集",
                        "url": REAL_URL,
                        "site": "example.com",
                        "published_at": "2026-11-06",
                    }
                ],
            },
        }
    ]


# ── D1：链接边界 ────────────────────────────────────────────


def test_full_width_parenthesis_with_fetch_time_stays_a_real_link():
    text = f"结论：项目 A 值得关注。详见 {REAL_URL}（抓取时间 {FETCHED_AT}）。"
    assert _urls_in(text) == {REAL_URL}


def test_full_width_punctuation_right_after_link_is_not_part_of_url():
    assert _urls_in(f"{REAL_URL}，") == {REAL_URL}
    assert _urls_in(f"{REAL_URL}。") == {REAL_URL}
    assert _urls_in(f"{REAL_URL}、") == {REAL_URL}
    assert _urls_in(f"{REAL_URL}；") == {REAL_URL}
    assert _urls_in(f"{OTHER_REAL}。") == {OTHER_REAL}


def test_model_summary_with_chinese_bracket_and_timestamp_is_accepted():
    summary = f"结论：项目 A 值得关注。详见 {REAL_URL}（抓取时间 {FETCHED_AT}）。"
    result = compose_execution_summary(
        "整理三条要点",
        _records(),
        generator=lambda prompt: summary,
    )
    assert result == summary


def test_model_summary_with_full_width_comma_after_link_is_accepted():
    summary = f"结论：项目 A 值得关注，详见 {REAL_URL}，抓取时间 {FETCHED_AT}。"
    result = compose_execution_summary(
        "整理三条要点",
        _records(),
        generator=lambda prompt: summary,
    )
    assert result == summary


def test_invented_link_is_still_rejected():
    summary = (
        "结论：项目 A 值得关注。详见 https://fake.example/not-real"
        f"（抓取时间 {FETCHED_AT}）。"
    )
    result = compose_execution_summary(
        "整理三条要点",
        _records(),
        generator=lambda prompt: summary,
    )

    assert "fake.example" not in result
    assert "已完成 1 项任务" in result, "must fall back to the evidence-only summary"


def test_summary_without_any_real_link_is_still_rejected():
    summary = f"结论：项目 A 值得关注。详见 https://fake.example/other（抓取时间 {FETCHED_AT}）。"
    result = compose_execution_summary(
        "整理三条要点",
        _records(),
        generator=lambda prompt: summary,
    )
    assert "fake.example" not in result
    assert "已完成 1 项任务" in result
    assert "结论：项目 A 值得关注。详见" not in result


# ── D2：兜底摘要断字 ────────────────────────────────────────


def test_shorten_text_cuts_at_sentence_boundary_not_mid_word():
    text = "该工具来源未说明其功能与授权。" * 20
    shortened = _shorten_text(text)

    assert len(shortened) <= 220
    assert shortened.endswith("。")
    assert not shortened.endswith("授")
    assert not shortened.endswith("权")


def test_shorten_text_uses_clause_boundary_when_no_sentence_end():
    text = "功能说明甲，" + "乙" * 240
    shortened = _shorten_text(text)

    assert len(shortened) <= 220
    assert shortened.endswith("…")
    assert not shortened.endswith("乙")


def test_join_summary_parts_has_no_double_space_or_orphaned_separator():
    joined = _join_summary_parts(
        [
            "已完成 2 项任务：甲、乙。",
            "甲：正文…",
            "另有 1 项任务未执行：丙。",
        ]
    )

    assert "  " not in joined
    assert "。甲" in joined
    assert joined.endswith("。")


def test_deterministic_summary_does_not_produce_half_word_gluing():
    records = [
        {
            "name": "调研今日 AI 开源项目",
            "capability": "network_bus",
            "status": "completed",
            "result": {"result": "该工具来源未说明其功能与授权。" * 20},
        },
        {
            "name": "整理三条要点",
            "capability": "execution_pipeline",
            "status": "completed",
            "result": {"result": "三条要点：一是 A；二是 B；三是 C。"},
        },
    ]
    summary = compose_execution_summary("调研并整理三条要点", records)

    assert "  " not in summary
    assert "授 整理" not in summary
    assert not summary.rstrip().endswith("授")
    assert summary.strip()
