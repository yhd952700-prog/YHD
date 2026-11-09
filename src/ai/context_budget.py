"""按模型上下文窗口规划历史裁剪（纯函数，无 I/O、无模型调用）。

背景：``LiuHaoAssistant`` 过去只按固定条数（20 条）截断历史，与模型真实
窗口无关——长对话在 128k 模型上被过早丢掉，短窗口模型又会把必然 400 的
超长 prompt 直接发给上游。这里把"留多少历史"改成按 token 预算计算：

* system 提示与**当前这一句**永不裁剪；
* 历史按"轮"（user + 后续 assistant）裁剪，不会留下孤立 assistant；
* 保留最近 ``keep_recent_turns`` 轮，最低 ``min_recent_turns`` 轮；
* 被裁掉的早期历史生成**确定性摘要**（每条消息首句），不调用模型；
  未来要接模型摘要时，把 ``summarize`` 回调传进来即可，本模块不感知 provider。

窗口预算 = ``window - output_reserve - system - current``。输出预留默认
``max(1024, 10% 窗口)``，避免把窗口全部塞给输入、导致模型没有空间回答。

窗口来源优先级：环境变量 ``LIUHAO_CONTEXT_WINDOW`` > 模型配置显式值 >
内置模型家族表 > 保守 8192。环境变量只接受 2048–1,000,000 的整数，非法值
回落并打 warn（不影响启动）。
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence

logger = logging.getLogger("liuhao.ai.context_budget")

#: 未知模型时的保守窗口：宁可早一点摘要，也不要发一个必然 400 的 prompt。
DEFAULT_WINDOW_TOKENS = 8192
#: 环境变量覆盖（启动前设置即可吃到更大的上下文，不必等配置字段落地）。
CONTEXT_WINDOW_ENV = "LIUHAO_CONTEXT_WINDOW"
#: 环境变量允许范围：低于 2048 连输出预留都放不下，高于 1M 基本是手误。
MIN_CONTEXT_WINDOW_TOKENS = 2048
MAX_CONTEXT_WINDOW_TOKENS = 1_000_000
#: 最近轮次上限 / 下限（一轮 = user + 其后的 assistant 消息）。
DEFAULT_KEEP_RECENT_TURNS = 6
MIN_RECENT_TURNS = 2
#: 输出预留：至少 1024 token，或窗口的 10%（取大者）。
MIN_OUTPUT_RESERVE_TOKENS = 1024
OUTPUT_RESERVE_RATIO = 0.10
#: 确定性摘要的字符上限（中文按 1 字≈1 token，800 字以内）。
SUMMARY_MAX_CHARS = 800
#: 单条消息首句的长度上限，避免一条消息把摘要占满。
SUMMARY_SENTENCE_MAX_CHARS = 120

#: 关键事实提示：早期约束/数字/否定条件不能只靠"首句"规则保留。
#: MEM-005 实测：``我要做一个个人记账工具。预算只有三百元。`` 的首句规则会把
#: 预算整句吃掉；这里对每个消息逐句扫描，命中提示的句子进「关键事实」层。
_KEY_FACT_HINTS = re.compile(
    r"预算|金额|价格|费用|上限|下限|总共|一共|元|块钱|万元|"
    r"不要|不能|禁止|别|必须|务必|只能|只允许|不允许|"
    r"截止|期限|deadline|目标|要求|偏好|喜欢|讨厌|"
    r"地址|电话|日期|时间|账号|密码|版本|规则|约束|记住|"
    r"必须用|改用|替换|删除|保留",
    re.IGNORECASE,
)
_SENTENCE_BREAK = re.compile(r"(?<=[。！？!?\n])|(?<=\.)\s+")


def _split_sentences(text: Any, *, max_chars: int = SUMMARY_SENTENCE_MAX_CHARS) -> List[str]:
    """按中英文句末标点/换行切句，压成单行并逐句截断。"""
    if not isinstance(text, str) or not text.strip():
        return []
    normalized = " ".join(text.split())
    pieces = [part.strip() for part in _SENTENCE_BREAK.split(normalized) if part.strip()]
    return [piece[:max_chars] for piece in pieces]

#: 已知模型的上下文窗口（按 "provider model" 小写串做前缀匹配）。
#: 只收录确定值；不确定的一律落到 DEFAULT_WINDOW_TOKENS，不猜大窗口。
_KNOWN_WINDOW_PREFIXES = (
    ("gpt-4.1", 128000),
    ("gpt-4o", 128000),
    ("gpt-4-turbo", 128000),
    ("claude", 200000),
    ("deepseek", 65536),
    ("gemini", 32768),
    ("qwen", 32768),
    ("moonshot", 32768),
    ("kimi", 32768),
    ("llama", 8192),
    ("mistral", 32768),
    ("ollama", 8192),
    ("local", 8192),
)


class ContextBudgetExceeded(RuntimeError):
    """窗口连「system + 当前这一句」（外加最低轮次）都装不下。

    ``upstream_kind`` 与上游的上下文超限同码：``user_facing_failure`` /
    流式 ``done`` 帧据此给出「对话太长，请新开会话或分段发送」，而不是
    「请检查密钥与网络」。
    """

    upstream_kind = "context_length_exceeded"

    def __init__(self, *, window_tokens: int, needed_tokens: int):
        self.window_tokens = window_tokens
        self.needed_tokens = needed_tokens
        super().__init__(
            f"对话太长：仅系统提示与当前输入就约 {needed_tokens} token，"
            f"超过模型窗口 {window_tokens}。"
        )


@dataclass(frozen=True)
class TrimResult:
    """一次裁剪的完整结论（既给 provider 用，也给日志/测试看）。"""

    messages: List[Dict[str, str]]
    kept_turns: int
    dropped_turns: int
    dropped_messages: int
    estimated_tokens: int
    prompt_budget_tokens: int
    window_tokens: int
    window_source: str
    summary: str
    summary_tokens: int


def estimate_tokens(text: Any) -> int:
    """粗估文本 token 数。

    启发式：CJK 等非 ASCII 字符按 1 字 ≈ 1 token，ASCII 按 4 字符 ≈ 1 token。
    这是**上界倾向**的估算（不同 tokenizer 有差异），用于预算而不是计费；
    非字符串（损坏的历史条目）按 0 计。

    **全项目唯一的 token 估算实现**：裁剪预算、配额拦截、用量账本与知识库
    注入都必须调用这里，不得再写 ``len(text) // 4`` 之类的第二套口径。
    """
    if not isinstance(text, str) or not text:
        return 0
    wide_chars = 0
    ascii_chars = 0
    for ch in text:
        if ord(ch) < 128:
            ascii_chars += 1
        else:
            wide_chars += 1
    return wide_chars + (ascii_chars + 3) // 4


def _env_context_window() -> Optional[int]:
    """读取环境变量里的窗口覆盖；未设置返回 ``None``。

    非法值（非整数 / 过小 / 过大）一律回落并打一条 warn，绝不让启动失败：
    用户手里可能有 128k 的模型，填错不该把整个对话功能变成异常。
    """
    raw = (os.environ.get(CONTEXT_WINDOW_ENV) or "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        logger.warning(
            "evt=context.window.invalid env=%s value=%r reason=not_an_integer "
            "action=fallback",
            CONTEXT_WINDOW_ENV,
            raw[:40],
        )
        return None
    if value < MIN_CONTEXT_WINDOW_TOKENS or value > MAX_CONTEXT_WINDOW_TOKENS:
        logger.warning(
            "evt=context.window.invalid env=%s value=%r reason=out_of_range "
            "min=%s max=%s action=fallback",
            CONTEXT_WINDOW_ENV,
            raw[:40],
            MIN_CONTEXT_WINDOW_TOKENS,
            MAX_CONTEXT_WINDOW_TOKENS,
        )
        return None
    return value


def resolve_context_window(
    *,
    configured: Optional[int] = None,
    provider: str = "",
    model: str = "",
) -> tuple[int, str]:
    """解析模型窗口，返回 ``(窗口, 来源)``。

    来源顺序：``env``（``LIUHAO_CONTEXT_WINDOW``，唯一能"不改代码就调大"的入口）
    → ``config``（用户/模型配置显式值，且 > 0）→ ``known``（内置家族表）
    → ``default``（保守 8192）。环境变量是覆盖语义，优先级高于配置；
    ModelConfig 目前没有 ``context_window`` 字段，调用方可先传 ``None``。
    """
    env_window = _env_context_window()
    if env_window is not None:
        return env_window, "env"
    if isinstance(configured, int) and configured > 0:
        return configured, "config"
    haystack = f"{provider or ''} {model or ''}".lower()
    for prefix, window in _KNOWN_WINDOW_PREFIXES:
        if prefix in haystack:
            return window, "known"
    return DEFAULT_WINDOW_TOKENS, "default"


def _group_into_turns(
    history: Sequence[Any],
) -> List[List[tuple[int, Dict[str, str]]]]:
    """把消息列表按轮分组：遇到 ``user`` 开新轮，其余消息归属当前轮。

    返回 ``(原始下标, 消息)``：裁剪后要按"哪些下标被保留"算摘要与丢弃数，
    用下标而不是 ``id(dict)``，历史里出现同一条消息两次时也不会误判。
    """
    turns: List[List[tuple[int, Dict[str, str]]]] = []
    for index, item in enumerate(history):
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "")
        if not turns or role == "user":
            turns.append([(index, item)])
        else:
            turns[-1].append((index, item))
    return turns


def _fit_turns_to_budget(
    turns: Sequence[Sequence[tuple[int, Dict[str, str]]]],
    room: int,
) -> Optional[tuple[List[List[tuple[int, Dict[str, str]]]], int]]:
    """把"必须保留的最近 N 轮"塞进 ``room``，单条超大消息允许被丢掉。

    单条 12000 字的助手回复会单独撑爆一整轮——只按轮裁时，这种"轮数没超但
    token 爆了"的历史要么整轮被丢弃后再也保不住最近轮次，要么直接把对话判成
    超限。这里改为：整轮放不下就逐条收，放不下的那条进摘要；每轮至少保留
    第一条消息（user 提问），连它都放不下才返回 ``None``（真正超限）。
    """
    kept_turns: List[List[tuple[int, Dict[str, str]]]] = []
    kept_tokens = 0
    for turn in turns:
        kept_turn: List[tuple[int, Dict[str, str]]] = []
        for index, message in turn:
            cost = estimate_tokens(message.get("content"))
            if kept_tokens + cost <= room:
                kept_turn.append((index, message))
                kept_tokens += cost
        if not kept_turn:
            return None
        kept_turns.append(kept_turn)
    return kept_turns, kept_tokens


def _first_sentence(text: Any, *, max_chars: int = SUMMARY_SENTENCE_MAX_CHARS) -> str:
    """取第一条句子（中英文句末标点 + 换行），并压成单行。"""
    if not isinstance(text, str) or not text.strip():
        return ""
    normalized = " ".join(text.split())
    cut = len(normalized)
    for index, ch in enumerate(normalized):
        if ch in "。！？!?\n":
            cut = index + 1
            break
        if ch == "." and (
            index + 1 >= len(normalized) or normalized[index + 1] in " \t"
        ):
            cut = index + 1
            break
    return normalized[:cut][:max_chars]


def deterministic_summary(
    messages: Sequence[Dict[str, str]],
    *,
    max_chars: int = SUMMARY_MAX_CHARS,
) -> str:
    """被裁消息 → 确定性分层摘要（不调用模型）。

    MEM-005：旧实现只取每条消息**首句**，像「我要做记账工具。预算只有三百元。」
    这种第二句才是关键约束的内容会被整句吃掉。这里分两层：

    * 【关键事实】逐句扫描约束/数字/否定/期限等提示词，优先保留；
    * 【对话要点】再补每条消息首句，维持原有可读性；
    * 每条摘要带 ``[#seq]`` 前缀（若调用方提供 seq），可回溯到原始消息。
    """
    key_parts: List[str] = []
    plain_parts: List[str] = []
    seen: set = set()
    used = 0

    def _tag(message: Dict[str, str]) -> str:
        seq = (message or {}).get("seq")
        return f"[#{seq}] " if seq is not None else ""

    def _append(target: List[str], text: str) -> bool:
        nonlocal used
        if text in seen:
            return True
        remaining = max_chars - used - (1 if (key_parts or plain_parts) else 0)
        if remaining <= 0:
            return False
        part = text[:remaining]
        if not part:
            return False
        target.append(part)
        seen.add(text)
        used += len(part) + 1
        return used < max_chars

    # 第一层：关键事实。先全量收集，避免前面的普通首句把预算挤掉。
    for message in messages:
        tag = _tag(message or {})
        for sentence in _split_sentences((message or {}).get("content", "")):
            if _KEY_FACT_HINTS.search(sentence):
                if not _append(key_parts, tag + sentence):
                    break
    # 第二层：每条消息首句（跳过已在关键事实层出现的句子）。
    for message in messages:
        tag = _tag(message or {})
        sentence = _first_sentence((message or {}).get("content", ""))
        if sentence and (tag + sentence) not in seen:
            if not _append(plain_parts, tag + sentence):
                break

    sections: List[str] = []
    if key_parts:
        sections.append("【关键事实】" + "；".join(key_parts))
    if plain_parts:
        sections.append("【对话要点】" + "；".join(plain_parts))
    return "".join(sections)


def _truncate_summary(summary: str, allowed_tokens: int) -> str:
    """把摘要截到 token 预算内。

    中文按 1 字≈1 token；英文按 4 字符≈1 token。用字符二分找最长前缀，
    估算单调，二分安全。
    """
    if allowed_tokens <= 0 or not summary:
        return ""
    if estimate_tokens(summary) <= allowed_tokens:
        return summary
    low, high = 0, len(summary)
    while low < high:
        mid = (low + high + 1) // 2
        if estimate_tokens(summary[:mid]) <= allowed_tokens:
            low = mid
        else:
            high = mid - 1
    return summary[:low].rstrip("； ")


def trim_history(
    history: Sequence[Dict[str, str]],
    *,
    window_tokens: int,
    system_text: str,
    current_message: str,
    keep_recent_turns: int = DEFAULT_KEEP_RECENT_TURNS,
    min_recent_turns: int = MIN_RECENT_TURNS,
    early_summary: str = "",
    summarize: Optional[Callable[[List[Dict[str, str]]], str]] = None,
    window_source: str = "",
) -> TrimResult:
    """按 token 预算裁剪历史，返回可直接发给 provider 的 messages。

    永不裁剪：``system_text``、``current_message``。
    裁剪顺序：从最新轮往前装，最多 ``keep_recent_turns`` 轮；若预算内不足
    ``min_recent_turns`` 轮，则强制保留最近的 ``min_recent_turns`` 轮。单条超大
    消息（例如 12000 字的助手回复）会把自己的整轮撑爆，此时整轮逐条收：放不下
    的单条进摘要，每轮至少保住 user 提问；连提问都放不下才抛
    ``ContextBudgetExceeded``（提前给出"对话太长"文案，而不是让上游 400）。

    ``early_summary``：外部算好的摘要（例如未来的模型滚动摘要）优先；
    否则用 ``summarize`` 回调（可选，用于接模型摘要）；再否则用确定性摘要。
    """
    window = max(1, int(window_tokens))
    reserve = max(MIN_OUTPUT_RESERVE_TOKENS, int(window * OUTPUT_RESERVE_RATIO))
    budget = window - reserve
    base_tokens = estimate_tokens(system_text) + estimate_tokens(current_message)
    if base_tokens > budget:
        raise ContextBudgetExceeded(
            window_tokens=window, needed_tokens=base_tokens + reserve
        )

    turns = _group_into_turns(history)
    room = budget - base_tokens

    selected: List[List[tuple[int, Dict[str, str]]]] = []
    used_tokens = 0
    for turn in reversed(turns):
        if len(selected) >= max(0, keep_recent_turns):
            break
        turn_tokens = sum(estimate_tokens(m.get("content")) for _, m in turn)
        if used_tokens + turn_tokens > room:
            break
        selected.insert(0, turn)
        used_tokens += turn_tokens

    # 最低轮数保障：预算内不足 2 轮时，强制保留最近 2 轮。整轮装不下就把超长
    # 的单条消息丢掉（它会被折进摘要）；连每轮第一条都放不下才真正超限。
    required = min(max(0, min_recent_turns), len(turns))
    if len(selected) < required:
        forced = turns[-required:] if required else []
        fitted = _fit_turns_to_budget(forced, room)
        if fitted is None:
            minimal = sum(
                estimate_tokens(turn[0][1].get("content")) for turn in forced if turn
            )
            raise ContextBudgetExceeded(
                window_tokens=window,
                needed_tokens=base_tokens + minimal + reserve,
            )
        selected, used_tokens = fitted

    kept_indices = {index for turn in selected for index, _ in turn}
    kept_count = len(selected)
    dropped_turns = sum(
        1 for turn in turns if any(index not in kept_indices for index, _ in turn)
    )
    dropped_messages = [
        message
        for index, message in enumerate(history)
        if index not in kept_indices and isinstance(message, dict)
    ]

    if dropped_messages or early_summary:
        # 分层合并：持久化的"已丢弃历史摘要"（early_summary）在前，本次窗口
        # 内新裁掉的消息摘要在后；只取其一都会丢一半记忆。
        # 兼容契约：显式传入 ``summarize`` 回调时，early_summary 仍独占摘要位
        #（回调方自己负责滚动合并，不能被本模块偷偷再调一次）。
        pieces: List[str] = []
        if early_summary:
            pieces.append(early_summary)
        if dropped_messages:
            if summarize is not None:
                if not early_summary:
                    pieces.append(summarize(dropped_messages))
            else:
                pieces.append(deterministic_summary(dropped_messages))
        summary = "；".join(piece for piece in pieces if piece)
        header = f"\n\n## 早期对话摘要（已省略 {dropped_turns} 轮）\n"
        available_for_summary = budget - base_tokens - used_tokens - estimate_tokens(header)
        summary = _truncate_summary(summary, available_for_summary)
    else:
        summary = ""

    summary_block = ""
    if summary:
        summary_block = (
            f"\n\n## 早期对话摘要（已省略 {dropped_turns} 轮）\n{summary}"
        )
    system_content = system_text + summary_block

    messages: List[Dict[str, str]] = [{"role": "system", "content": system_content}]
    for turn in selected:
        messages.extend(message for _, message in turn)
    messages.append({"role": "user", "content": current_message})

    elapsed_tokens = base_tokens + used_tokens + estimate_tokens(summary_block)
    return TrimResult(
        messages=messages,
        kept_turns=kept_count,
        dropped_turns=dropped_turns,
        dropped_messages=len(dropped_messages),
        estimated_tokens=elapsed_tokens,
        prompt_budget_tokens=budget,
        window_tokens=window,
        window_source=window_source,
        summary=summary,
        summary_tokens=estimate_tokens(summary_block),
    )
