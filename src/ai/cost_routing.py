"""第二季 V9：成本智能路由的分级判据（纯函数，可单测穷举）。

设计文档 upgrade2-evidence/v9-design.md §2：分级只用调用前已有的信号，
按序短路；兜底永远是 hard——分级器只放行「明确轻」的活，误判方向是
「该省没省」，绝不把硬活降级。G3（规模）必须先于 G4（轻活形态），
这个顺序由测试锁死。
"""

from __future__ import annotations

from src.ai.context_budget import estimate_tokens

#: G3：估算输入规模超过此值直接按硬任务（长上下文降级最容易翻车）。
HARD_CONTEXT_TOKENS = 4000
#: G4：轻活形态的规模上限（消息 + 历史的估算总量）。
LIGHT_CONTEXT_TOKENS = 800

#: G4 轻活形态关键词（中英）：翻译 / 改写润色 / 摘要总结 / 简单问答寒暄。
#: 只收「形态明确」的词；含糊的请求一律落 G5 硬任务。
LIGHT_INTENT_KEYWORDS = (
    "翻译", "译成", "译为", "改写", "润色", "缩写", "扩写",
    "摘要", "总结一下", "概括", "提炼要点",
    "translate", "rewrite", "polish", "summarize", "summarise",
    "tl;dr", "tldr",
)

GREETINGS = (
    "你好", "您好", "早上好", "下午好", "晚上好", "谢谢", "多谢",
    "hello", "hi", "thanks", "thank you", "good morning",
)


def looks_light(message: str) -> bool:
    """消息是否命中轻活形态：短问候，或含轻活关键词。"""
    text = str(message or "").strip().lower()
    if not text:
        return False
    if len(text) <= 12 and any(text.startswith(g) or g in text for g in GREETINGS):
        return True
    return any(keyword in text for keyword in LIGHT_INTENT_KEYWORDS)


def grade_turn(
    *,
    explicit_model: bool = False,
    member_task: bool = False,
    prev_turn_used_tools: bool = False,
    message: str = "",
    context_tokens: int | None = None,
) -> str:
    """返回 "pinned" | "hard" | "light"（判据顺序见设计 §2，勿调换）。

    - G0 显式指定模型（用户/Bot 钉了）→ pinned，路由跳过。
    - G1 硬任务入口（V8 编排成员任务等内部正经活）→ hard。
    - G2 上一轮用过工具（工具链上下文延续）→ hard。
    - G3 估算规模 > 4000 token → hard（先于 G4，顺序锁死）。
    - G4 轻活形态且规模 ≤ 800 token → light。
    - G5 其余一切 → hard。
    """
    if explicit_model:
        return "pinned"
    if member_task:
        return "hard"
    if prev_turn_used_tools:
        return "hard"
    if context_tokens is None:
        context_tokens = estimate_tokens(message)
    if context_tokens > HARD_CONTEXT_TOKENS:
        return "hard"
    if context_tokens <= LIGHT_CONTEXT_TOKENS and looks_light(message):
        return "light"
    return "hard"
