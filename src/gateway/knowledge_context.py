"""对话链路的知识库注入（网关侧，只读调用 ``src.knowledge``）。

职责边界：

* 判断是否有可用知识库（**有文档才检索**，空库直接降级）；
* 检索相关片段，并按固定 token 预算裁剪（绝不把整库塞进提示词）；
* 把片段拼成带编号来源的 system 提示词块；
* 生成响应里的引用元信息；检索失败/为空时**静默降级**为普通对话，
  但元信息必须说明"本次未使用知识库"及原因。

**为什么不能"有命中就注入"**：检索侧在无答案时也可能返回凑数的 top_k 片段。
把这些片段喂给模型，会让它基于无关上下文编出 *带引用的错误答案* —— 比不带
引用更可信、也更危险。因此本模块有两道闸：

1. **检索侧信号**：检索结果显式声明"无相关内容"（``no_relevant`` /
   ``has_relevant=false`` / ``reason=no_relevant`` 等，具体键以知识库返回为准）
   时，一个片段都不注入；
2. **网关侧保险**：即使检索侧给了候选，最高分低于门槛，或与亚军分差过小
   （评分是"一锅平铺"，说明没有真正的相关项）时，同样不注入。

两种情况在 ``knowledge`` 元信息里分别说明：前者是"未找到相关内容"，
后者是"知识库为空"或"相关度不足" —— 对用户的含义完全不同，不能混用。
决定理由会写入日志与元信息 ``diagnostics``，便于排查"为什么这次没引用"。

本模块不修改 ``src.knowledge``；检索依赖真实 embedding provider，任何
不可用/异常都只降级、不伪造结果。
"""

from __future__ import annotations

import logging
import os
import threading
import weakref
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

logger = logging.getLogger("liuhao.gateway.knowledge_context")

#: 注入提示词的上下文上限（token，按 4 字符≈1 token 估算）。
KNOWLEDGE_MAX_CONTEXT_TOKENS = 600
_CHARS_PER_TOKEN = 4
KNOWLEDGE_MAX_CONTEXT_CHARS = KNOWLEDGE_MAX_CONTEXT_TOKENS * _CHARS_PER_TOKEN

#: 单条片段注入上限，避免一份超长 chunk 吃光全部预算。
_MAX_SNIPPET_CHARS = 800

#: 返回给客户端的引用片段预览上限。
_CITATION_PREVIEW_CHARS = 200

_DEFAULT_TOP_K = 4

#: 相关度阈值（低于它的候选一律丢弃）。
_MIN_SCORE_ENV = "LIUHAO_CHAT_KNOWLEDGE_MIN_SCORE"
_DEFAULT_MIN_SCORE = 0.25

#: 最高分与亚军分的最小差距。低于它说明评分“平铺”，没有真正的相关项。
_MIN_MARGIN_ENV = "LIUHAO_CHAT_KNOWLEDGE_MIN_MARGIN"
_DEFAULT_MIN_MARGIN = 0.02


def _env_float(name: str, default: float) -> float:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning(
            "knowledge_context: invalid %s=%r, falling back to %s", name, raw, default
        )
        return default


def _min_score() -> float:
    return _env_float(_MIN_SCORE_ENV, _DEFAULT_MIN_SCORE)


def _min_margin() -> float:
    return _env_float(_MIN_MARGIN_ENV, _DEFAULT_MIN_MARGIN)


def _truncate(text: str, limit: int) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


def _source_of(hit: Dict[str, Any]) -> Dict[str, Any]:
    """把一条检索命中投影成稳定的引用结构（不发整段原文）。"""
    metadata = hit.get("metadata") if isinstance(hit.get("metadata"), dict) else {}
    chunk_id = str(hit.get("doc_id") or "")
    document_id = str(metadata.get("document_id") or chunk_id.split("#", 1)[0])
    return {
        "document_id": document_id,
        "chunk_id": chunk_id,
        "chunk_index": metadata.get("chunk_index"),
        "score": hit.get("score"),
        "snippet": _truncate(str(hit.get("text") or ""), _CITATION_PREVIEW_CHARS),
    }


def _not_used(
    reason: str, human_reason: str, diagnostics: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    return {
        "used": False,
        "reason": reason,
        "decision": reason,
        "message": f"本次未使用知识库：{human_reason}",
        "sources": [],
        "context": "",
        "context_tokens": 0,
        "hits": 0,
        "diagnostics": dict(diagnostics or {}),
    }


#: 检索侧可能使用的“无相关内容”表达。知识库最终以哪个键为准以实际返回为准，
#: 这里做并集匹配：只认显式否定，不把缺失字段解释成“无相关内容”。
_NEGATIVE_FLAGS = ("no_relevant", "no_relevant_content", "no_answer", "not_relevant")
_NEGATIVE_STATUS = frozenset(
    {"no_relevant", "no_relevant_content", "no_match", "not_found", "not_relevant"}
)


def _says_no_relevant(result: Dict[str, Any]) -> bool:
    """检索结果是否**显式**声明“无相关内容”。

    只接受显式信号：布尔标记、``has_relevant/has_relevance=False``、
    ``status`` 命中否定集合、或 ``reason`` 文本包含否定语义。缺失字段不算否定，
    否则任何实现变更都会被误读成“没找到”。
    """
    containers = [result]
    retrieval = result.get("retrieval")
    if isinstance(retrieval, dict):
        containers.append(retrieval)
    for container in containers:
        if not isinstance(container, dict):
            continue
        if any(bool(container.get(key)) for key in _NEGATIVE_FLAGS):
            return True
        for key in ("has_relevant", "has_relevance", "relevant"):
            if container.get(key) is False:
                return True
        status = str(container.get("status") or "").strip().lower()
        if status in _NEGATIVE_STATUS:
            return True
        reason = str(container.get("reason") or "").strip().lower()
        if any(
            token in reason
            for token in ("no_relevant", "no_related", "not_relevant", "no_match")
        ):
            return True
    return False


def _score_gate(hits: List[Dict[str, Any]]) -> tuple[bool, str, Dict[str, Any]]:
    """网关侧的相关度保险。返回 ``(可注入, 决定, 诊断信息)``。"""
    min_score = _min_score()
    min_margin = _min_margin()
    scored = []
    for hit in hits:
        try:
            scored.append(float(hit.get("score") or 0.0))
        except (TypeError, ValueError):
            scored.append(0.0)
    scored.sort(reverse=True)
    top = scored[0] if scored else 0.0
    runner_up = scored[1] if len(scored) > 1 else None
    diagnostics = {
        "candidates": len(scored),
        "top_score": round(top, 6),
        "runner_up_score": round(runner_up, 6) if runner_up is not None else None,
        "min_score": min_score,
        "min_margin": min_margin,
    }
    if not scored:
        return False, "low_score", diagnostics
    if top < min_score:
        return False, "low_score", diagnostics
    # 只有存在亚军时才谈“分差”；单一候选由 min_score 把关。
    if runner_up is not None and (top - runner_up) < min_margin:
        return False, "ambiguous", diagnostics
    return True, "inject", diagnostics


def build_knowledge_context(
    query: str, enabled: Optional[bool] = None
) -> Dict[str, Any]:
    """检索与 ``query`` 相关的知识片段，返回注入用的上下文与引用。

    ``enabled=False`` 时完全跳过（用户显式关闭）。``enabled=None`` 时自动：
    知识库可用且**有文档**才检索。任何失败都返回 ``used=False`` 并说明原因，
    不抛异常、不影响普通对话。
    """
    if enabled is False:
        return _not_used("disabled_by_user", "用户已关闭本次检索")

    try:
        from src.knowledge.documents import get_knowledge_service

        service = get_knowledge_service()
        status = service.status()
    except Exception as exc:  # 知识库模块不可用：静默降级
        logger.warning("knowledge_context: status unavailable: %s", exc, exc_info=True)
        return _not_used("unavailable", f"知识库不可用（{type(exc).__name__}）")

    if not status.get("enabled"):
        reason = str(status.get("reason") or "未配置 embedding provider")
        return _not_used("unavailable", f"知识库未启用（{reason}）")
    if int(status.get("vectors") or 0) <= 0:
        # “库里没有文档”与“库里有文档但没有答案”是两件事，文案不能混用。
        return _not_used("empty", "知识库为空（暂无可检索文档）")

    try:
        result = service.search(
            query, top_k=_DEFAULT_TOP_K, threshold=_min_score()
        )
    except Exception as exc:  # 检索失败：静默降级，但记录真实原因
        logger.warning("knowledge_context: retrieval failed: %s", exc, exc_info=True)
        return _not_used("error", f"检索失败（{type(exc).__name__}）")

    hits: List[Dict[str, Any]] = [
        hit for hit in (result.get("hits") or []) if isinstance(hit, dict)
    ]
    candidate_diagnostics = {"candidates": len(hits)}

    # 闸 1：检索侧显式说“无相关内容”——候选一个都不看，直接不注入。
    if _says_no_relevant(result):
        logger.info(
            "knowledge injection skipped: decision=no_relevant candidates=%d",
            len(hits),
        )
        return _not_used("no_relevant", "未找到相关内容", candidate_diagnostics)

    if not hits:
        # 非空库里检索不到任何片段，同样是“未找到相关内容”，不是“库为空”。
        logger.info("knowledge injection skipped: decision=no_relevant candidates=0")
        return _not_used("no_relevant", "未找到相关内容", candidate_diagnostics)

    # 闸 2：网关自己的相关度保险。评分过低或“平铺无区分度”一律不注入。
    injectable, decision, score_diagnostics = _score_gate(hits)
    if not injectable:
        min_score = score_diagnostics["min_score"]
        top = score_diagnostics["top_score"]
        runner_up = score_diagnostics["runner_up_score"]
        if decision == "ambiguous":
            human_reason = (
                f"未找到足够相关的内容（最高分 {top} 与次高 {runner_up} 分差 "
                f"小于 {score_diagnostics['min_margin']}，候选无区分度）"
            )
        else:
            human_reason = f"未找到足够相关的内容（最高分 {top} 低于门槛 {min_score}）"
        logger.info(
            "knowledge injection skipped: decision=%s top_score=%s "
            "runner_up=%s min_score=%s min_margin=%s candidates=%d",
            decision,
            top,
            runner_up,
            min_score,
            score_diagnostics["min_margin"],
            len(hits),
        )
        return _not_used(decision, human_reason, score_diagnostics)

    # 通过保险后，再丢弃低于门槛的候选，绝不拿低分片段凑数。
    min_score = score_diagnostics["min_score"]
    qualified: List[Dict[str, Any]] = []
    for hit in hits:
        try:
            score = float(hit.get("score") or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        if score >= min_score:
            qualified.append(hit)
    if not qualified:
        return _not_used(
            "low_score",
            f"未找到足够相关的内容（最高分 {score_diagnostics['top_score']} 低于门槛 {min_score}）",
            score_diagnostics,
        )

    # 按 token 预算拼装：每条先截断，累计超过预算即停止，保证不超上限。
    blocks: List[str] = []
    sources: List[Dict[str, Any]] = []
    used_chars = 0
    for index, hit in enumerate(qualified, start=1):
        text = _truncate(str(hit.get("text") or ""), _MAX_SNIPPET_CHARS)
        if not text:
            continue
        source = _source_of(hit)
        header = (
            f"[{index}] 文档={source['document_id']} "
            f"片段={source['chunk_index']} 相关度={source['score']}"
        )
        block = f"{header}\n{text}"
        if used_chars + len(block) > KNOWLEDGE_MAX_CONTEXT_CHARS:
            remaining = KNOWLEDGE_MAX_CONTEXT_CHARS - used_chars
            # 预算不足以容纳标题+正文时停止；绝不为了凑数而破坏边界。
            if remaining < len(header) + 40:
                break
            block = _truncate(block, remaining)
        blocks.append(block)
        sources.append(source)
        used_chars += len(block)
        if used_chars >= KNOWLEDGE_MAX_CONTEXT_CHARS:
            break

    if not blocks:
        return _not_used("no_relevant", "未找到相关内容", score_diagnostics)

    context = (
        "\n\n[知识库上下文]\n"
        "以下是与用户问题相关的资料片段。回答时若使用了某条资料，"
        "请用来源编号标注（例如 [1]）。不要编造未出现在片段中的事实。\n\n"
        + "\n\n".join(blocks)
        + "\n[知识库上下文结束]"
    )

    score_diagnostics["injected"] = len(sources)
    logger.info(
        "knowledge injection applied: decision=inject hits=%d top_score=%s "
        "runner_up=%s context_tokens=%d",
        len(sources),
        score_diagnostics["top_score"],
        score_diagnostics["runner_up_score"],
        max(1, len(context) // _CHARS_PER_TOKEN),
    )
    return {
        "used": True,
        "reason": "",
        "decision": "inject",
        "message": f"本次使用知识库：{len(sources)} 个片段",
        "sources": sources,
        "context": context,
        "context_tokens": max(1, len(context) // _CHARS_PER_TOKEN),
        "hits": len(sources),
        "diagnostics": score_diagnostics,
    }


#: 每个 assistant 实例一把提示词锁（不同会话互不阻塞）。弱引用键保证
#: 会话实例被移除后锁也随之回收，不会无限增长。
_PROMPT_LOCKS: "weakref.WeakKeyDictionary[Any, Any]" = weakref.WeakKeyDictionary()
_PROMPT_LOCKS_GUARD = threading.Lock()


def _prompt_lock(assistant: Any):
    with _PROMPT_LOCKS_GUARD:
        lock = _PROMPT_LOCKS.get(assistant)
        if lock is None:
            lock = threading.Lock()
            _PROMPT_LOCKS[assistant] = lock
        return lock


@contextmanager
def knowledge_prompt(assistant: Any, knowledge: Dict[str, Any]):
    """在调用期间把知识上下文临时追加到 assistant 的 system prompt。

    ``LiuHaoAssistant`` 的 system prompt 是实例属性；本模块只能在网关侧拼接，
    因此调用前后恢复原值，避免污染后续轮次。测试替身没有该属性时直接透传。
    """
    if not knowledge.get("used"):
        yield
        return
    original = getattr(assistant, "system_prompt", None)
    if not isinstance(original, str):
        yield
        return
    lock = _prompt_lock(assistant)
    with lock:
        assistant.system_prompt = original + "\n\n" + str(knowledge.get("context") or "")
        try:
            yield
        finally:
            assistant.system_prompt = original


def public_metadata(knowledge: Dict[str, Any]) -> Dict[str, Any]:
    """响应中可见的知识库元信息（始终存在，说明本次是否使用）。"""
    return {
        "used": bool(knowledge.get("used")),
        "reason": str(knowledge.get("reason") or ""),
        "decision": str(knowledge.get("decision") or ""),
        "message": str(knowledge.get("message") or ""),
        "context_tokens": int(knowledge.get("context_tokens") or 0),
        "hits": int(knowledge.get("hits") or 0),
        "sources": list(knowledge.get("sources") or []),
        # 排查“为什么这次没引用”的依据：候选数、最高分/次高/门槛/分差。
        "diagnostics": dict(knowledge.get("diagnostics") or {}),
    }
