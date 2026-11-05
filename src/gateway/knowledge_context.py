"""对话链路的知识库注入（网关侧，只读调用 ``src.knowledge``）。

职责边界：

* 判断是否有可用知识库（**有文档才检索**，空库直接降级）；
* 检索相关片段，并按固定 token 预算裁剪（绝不把整库塞进提示词）；
* 把片段拼成带编号来源的 system 提示词块；
* 生成响应里的引用元信息；检索失败/为空时**静默降级**为普通对话，
  但元信息必须说明"本次未使用知识库"及原因。

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

#: 相关度阈值默认值（0.0 表示仍按 top_k 取，仅由 env 调整）。
_MIN_SCORE_ENV = "LIUHAO_CHAT_KNOWLEDGE_MIN_SCORE"


def _min_score() -> Optional[float]:
    raw = (os.environ.get(_MIN_SCORE_ENV) or "").strip()
    if not raw:
        return 0.25
    try:
        return float(raw)
    except ValueError:
        logger.warning(
            "knowledge_context: invalid %s=%r, falling back to 0.25",
            _MIN_SCORE_ENV,
            raw,
        )
        return 0.25


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


def _not_used(reason: str, human_reason: str) -> Dict[str, Any]:
    return {
        "used": False,
        "reason": reason,
        "message": f"本次未使用知识库：{human_reason}",
        "sources": [],
        "context": "",
        "context_tokens": 0,
        "hits": 0,
    }


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
        return _not_used("empty", "知识库暂无文档")

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
    if not hits:
        return _not_used("no_hits", "未检索到相关片段")

    # 按 token 预算拼装：每条先截断，累计超过预算即停止，保证不超上限。
    blocks: List[str] = []
    sources: List[Dict[str, Any]] = []
    used_chars = 0
    for index, hit in enumerate(hits, start=1):
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
        return _not_used("no_hits", "检索结果为空或超出上下文预算")

    context = (
        "\n\n[知识库上下文]\n"
        "以下是与用户问题相关的资料片段。回答时若使用了某条资料，"
        "请用来源编号标注（例如 [1]）。不要编造未出现在片段中的事实。\n\n"
        + "\n\n".join(blocks)
        + "\n[知识库上下文结束]"
    )

    return {
        "used": True,
        "reason": "",
        "message": f"本次使用知识库：{len(sources)} 个片段",
        "sources": sources,
        "context": context,
        "context_tokens": max(1, len(context) // _CHARS_PER_TOKEN),
        "hits": len(sources),
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
        "message": str(knowledge.get("message") or ""),
        "context_tokens": int(knowledge.get("context_tokens") or 0),
        "hits": int(knowledge.get("hits") or 0),
        "sources": list(knowledge.get("sources") or []),
    }
