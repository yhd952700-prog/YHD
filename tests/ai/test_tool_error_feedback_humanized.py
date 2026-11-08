"""R5a：工具结果回喂模型时必须"人话化"，禁止把 JSON 原文再喂回去。

真实缺陷：工具失败返回 ``{"status":"error","error":"请提供 text…"}``，网关
原样回喂模型，模型于是复述这段 JSON，用户看到机器字段。修复后失败/需澄清
都翻成一句可执行中文，并明确要求不复述 JSON；成功结果保持原结构。
"""

from __future__ import annotations

import json

from src.ai.liuhao import _tool_feedback_text


def test_error_result_becomes_human_instruction():
    text = _tool_feedback_text(
        "manage_reminders",
        json.dumps(
            {"status": "error", "error": "请提供 text，例如「每天十点提醒我喝水」。"},
            ensure_ascii=False,
        ),
    )

    assert "[工具 manage_reminders 执行失败]" in text
    assert "请提供 text" in text
    assert "不要复述 JSON" in text
    assert '{"status"' not in text


def test_needs_clarification_keeps_question_and_forbids_guessing():
    text = _tool_feedback_text(
        "manage_reminders",
        {"status": "needs_clarification", "question": "你想什么时候提醒你？"},
    )

    assert "[工具 manage_reminders 需要确认]" in text
    assert "你想什么时候提醒你？" in text
    assert "不要自己猜" in text


def test_success_result_is_passed_through_unchanged():
    success = {"status": "ok", "message": "已设置提醒，1 分钟后提醒你喝水。"}
    text = _tool_feedback_text("manage_reminders", success)

    assert json.loads(text) == success
