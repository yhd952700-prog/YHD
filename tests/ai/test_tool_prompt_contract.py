"""R1：工具提示只列参数名与必填 ``*``，不把完整 JSON Schema 塞进 prompt。

真实缺陷：``build_tool_prompt`` 从不告诉模型该传哪些字段，模型只能猜
``time/instruction``；工具只认 ``text``，于是稳定失败。修复后仍然只给"名字 +
必填标记"，不塞类型/枚举/说明，避免小模型照抄 schema。
"""

from __future__ import annotations

from src.ai.tool_registry import Tool
from src.ai.tools import build_tool_prompt
from src.tasks.reminder_tool import build_reminder_tool


def _tool(properties: dict, required: list[str]) -> Tool:
    return Tool(
        tool_id="t.demo",
        name="demo_tool",
        version="1.0",
        description="演示工具",
        capability="cap.demo",
        schema={
            "type": "object",
            "properties": properties,
            "required": required,
        },
        fn=lambda **kwargs: {"status": "ok"},
    )


def test_prompt_lists_only_param_names_and_required_marker():
    prompt = build_tool_prompt(
        [
            _tool(
                {
                    "action": {"type": "string", "enum": ["a", "b"]},
                    "text": {"type": "string", "description": "正文"},
                    "reminder_id": {"type": "integer"},
                },
                ["action"],
            )
        ]
    )

    assert "参数：action* / text / reminder_id" in prompt
    # 只列名字：类型、枚举、说明都不能进提示词。
    assert "enum" not in prompt
    assert "integer" not in prompt
    assert 'type": "string' not in prompt


def test_reminder_prompt_says_create_must_use_text():
    prompt = build_tool_prompt([build_reminder_tool("u-test")])

    assert "参数：" in prompt
    assert "action*" in prompt
    assert "create 必须用 text" in prompt
    # text 不是全局必填，所以不能标成 text*。
    assert "text*" not in prompt
