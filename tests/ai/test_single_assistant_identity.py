"""单助手结合版回归：后端只留一个助手配置，没有可切换的 persona。

2026-11-12 用户纠正（最高优先级，覆盖之前"可切换 Muse / 小龙虾"的
要求）：不要两个可切换的助手，只要 Muse 与小龙虾的结合版。

本文件钉住三件事：

1. 唯一的助手定义只有一份（``DEFAULT_SYSTEM_PROMPT``）：能力像 Muse、
   说话风格融合小龙虾的活泼亲切，没有 persona 切换分支；
2. ``LiuHaoAssistant`` 的构造签名里没有 ``persona`` 参数 —— 单助手
   配置不是"默认 persona + 可选切换"，而是根本没有切换入口；
3. 未配置模型时 ``MockProvider`` 只返回一条未配置提示，不编两种风格
   （Muse 风 / 小龙虾风）的回复；演示模式与非演示模式的区别只在
   是否标注 "[演示数据]"，不在回复风格。
"""

from __future__ import annotations

import inspect

from src.ai.liuhao import DEFAULT_SYSTEM_PROMPT, LiuHaoAssistant
from src.ai.providers import (
    DEMO_MODEL_NOT_CONFIGURED_MESSAGE,
    MODEL_NOT_CONFIGURED_MESSAGE,
    MockProvider,
)


def test_single_system_prompt_is_the_combined_assistant():
    """唯一 system prompt：Muse 能力 + 小龙虾风格，无切换分支。"""
    assert "Muse" in DEFAULT_SYSTEM_PROMPT
    assert "小龙虾" in DEFAULT_SYSTEM_PROMPT
    assert "唯一" in DEFAULT_SYSTEM_PROMPT
    # 没有"切到另一个人格/风格"的说法；"切换"二字只应出现在"没有可切换"的否定里。
    assert "没有可切换" in DEFAULT_SYSTEM_PROMPT
    assert "切换人格" not in DEFAULT_SYSTEM_PROMPT
    assert "双助手" not in DEFAULT_SYSTEM_PROMPT
    # 能力与风格两条都写明了，才算结合版，不是只挂一个名字。
    assert "活泼亲切" in DEFAULT_SYSTEM_PROMPT
    assert "不伪造" in DEFAULT_SYSTEM_PROMPT


def test_assistant_constructor_takes_no_persona():
    """构造签名里没有 persona 参数：切换入口不存在，不只是默认关掉。"""
    params = inspect.signature(LiuHaoAssistant.__init__).parameters
    assert "persona" not in params
    assert "assistant_type" not in params
    assert "style" not in params


def test_default_assistant_uses_the_single_prompt():
    """默认构造即用唯一 prompt，不按 persona 分流。

    构造时会在 prompt 后追加一份工具调用说明（``liuhao.py`` 的工具提示），
    所以断言"以唯一 prompt 开头"，而不是逐字相等。
    """
    assistant = LiuHaoAssistant(
        name="liuhao-single-identity",
        provider=MockProvider(name="mock", model="mock-model"),
    )
    assert assistant.system_prompt.startswith(DEFAULT_SYSTEM_PROMPT)
    assert assistant.system_prompt.count(DEFAULT_SYSTEM_PROMPT) == 1


def test_unconfigured_mock_has_one_reply_not_two_styles(monkeypatch):
    """未配 key 时只有一条未配置提示，不编两种风格的回复。"""
    provider = MockProvider(name="mock", model="mock-model")

    monkeypatch.delenv("LIUHAO_DEMO_SEED", raising=False)
    text = provider.generate("你好")
    assert text == MODEL_NOT_CONFIGURED_MESSAGE
    assert "请先配置模型密钥" in text
    # 不是回答，更不是分风格的回答：两种人格名都不应出现在回复正文里。
    assert "Muse" not in text
    assert "小龙虾" not in text
    assert "演示数据" not in text

    monkeypatch.setenv("LIUHAO_DEMO_SEED", "1")
    demo_text = provider.generate("你好")
    assert demo_text == DEMO_MODEL_NOT_CONFIGURED_MESSAGE
    assert "演示数据" in demo_text
    assert "Muse" not in demo_text
    assert "小龙虾" not in demo_text
    # 演示与非演示只差 "[演示数据]" 标注，不差风格：两者都是同一条未配置提示。
    assert "还没有配置模型" in demo_text
    assert "系统设置 → 模型管理" in demo_text
