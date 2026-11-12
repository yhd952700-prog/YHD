"""任务结果上屏必须是给人读的文本，而不是 Python repr。

验收第 5 项：结果里带真实链接/数据时必须可点击、带时间戳；不许出现
``{'key': 'value'}`` 这种原始结构。``task_result_text`` 是 gateway 详情与
内核进度快照共用的唯一渲染口。
"""
from __future__ import annotations

import json

from src.kernels.execution import task_result_text


def test_network_result_keeps_readable_text_sources_and_stamp():
    payload = {
        "success": True,
        "result": "三条要点：Ollama、vLLM、LangGraph。",
        "sources": [
            {"title": "Ollama", "url": "https://github.com/ollama/ollama"},
            {"title": "vLLM", "url": "https://github.com/vllm-project/vllm"},
        ],
        "fetched_at": "2026-11-08T10:00:00+08:00",
    }
    text = task_result_text(payload)
    assert text is not None
    assert text.startswith("三条要点：")
    assert "- Ollama: https://github.com/ollama/ollama" in text
    assert "- vLLM: https://github.com/vllm-project/vllm" in text
    assert "抓取时间：2026-11-08T10:00:00+08:00" in text
    assert "'success'" not in text


def test_dict_without_business_field_uses_json_not_python_repr():
    payload = {"key": "value", "nested": {"n": 1}}
    text = task_result_text(payload)
    assert text is not None
    assert "{'key'" not in text
    assert json.loads(text) == payload


def test_plain_string_and_none_pass_through():
    assert task_result_text("ok") == "ok"
    assert task_result_text(None) is None


def test_list_result_is_json():
    text = task_result_text([{"a": 1}])
    assert text is not None
    assert json.loads(text) == [{"a": 1}]
    assert "'a'" not in text
