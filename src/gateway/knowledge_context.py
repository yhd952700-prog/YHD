"""对话链路的知识库注入（网关侧，只读调用 ``src.knowledge``）。

职责边界：

* 判断是否有可用知识库（未启用/不可用时直接降级）；
* 检索相关片段，并按固定 token 预算裁剪（绝不把整库塞进提示词）；
* 把片段拼成带编号来源的 system 提示词块；
* 生成响应里的引用元信息；检索失败/弃答时**静默降级**为普通对话，
  但元信息必须说明"本次未使用知识库"及原因。

**为什么不能"有命中就注入"**：top-k 检索总会返回点什么。把凑数片段喂给
模型，会让它基于无关上下文编出 *带引用的错误答案* —— 比不带引用更可信、
也更危险。

**权威判定在知识库侧**（``src.knowledge.abstention``，字段定稿）：

* ``retrieval.status`` 只会是 ``"ok"`` / ``"no_relevant"``；
* ``retrieval.reason`` 三选一：``empty_corpus`` / ``below_threshold`` /
  ``ambiguous``（``status="ok"`` 时为 ``None``）；
* ``no_relevant`` 时 ``retrieval.hits`` 一定是空数组，另有
  ``top_score`` / ``runner_up_score`` / ``thresholds``。

因此本模块的闸门顺序是：

1. ``status == "no_relevant"`` → 一个片段都不注入，``reason`` 原样透传；
2. ``status == "ok"`` → **以知识库判定为准**（它的阈值有评测数据支撑），网关
   只在分数缺失/异常/与状态矛盾时兜底否决；
3. 没有权威字段的旧实现 → 走网关自己的分数门槛（``_score_gate``），仅作兼容。

三种弃答原因对用户的含义完全不同，文案不能混用：
``empty_corpus`` → "知识库为空"，``below_threshold`` → "未找到相关内容"，
``ambiguous`` → "找到的内容不够确定"。决定理由同时写入日志与元信息
``diagnostics``，便于排查"为什么这次没引用"。

本模块不修改 ``src.knowledge``；检索依赖真实 embedding provider，任何
不可用/异常都只降级、不伪造结果。
"""

from __future__ import annotations

import logging
import math
import os
import threading
import weakref
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

from ..ai.context_budget import estimate_tokens

logger = logging.getLogger("liuhao.gateway.knowledge_context")

#: 注入提示词的上下文上限（token，与全项目统一的 ``estimate_tokens`` 同口径）。
KNOWLEDGE_MAX_CONTEXT_TOKENS = 600

#: 单条片段注入上限（字符，先做粗截断，再按 token 预算精截）。
_MAX_SNIPPET_CHARS = 800

#: 截断后正文至少保留的字符数：预算不足以容纳标题加这点正文时直接停止。
_MIN_BLOCK_BODY_CHARS = 40

#: 返回给客户端的引用片段预览上限。
_CITATION_PREVIEW_CHARS = 200

_DEFAULT_TOP_K = 4

#: 网关侧兜底门槛：**只在检索侧没有权威判定时**使用（兼容旧实现）。
#: 知识库侧 ``retrieval.status`` 存在时以它为准，见模块 docstring。
_MIN_SCORE_ENV = "LIUHAO_CHAT_KNOWLEDGE_MIN_SCORE"
_DEFAULT_MIN_SCORE = 0.25

#: 最高分与亚军分的最小差距（同上，仅兼容旧实现）。
_MIN_MARGIN_ENV = "LIUHAO_CHAT_KNOWLEDGE_MIN_MARGIN"
_DEFAULT_MIN_MARGIN = 0.02

#: 知识库弃答契约（``src.knowledge.abstention`` 定稿字段）。
_STATUS_OK = "ok"
_STATUS_NO_RELEVANT = "no_relevant"
_ABSTAIN_REASONS = frozenset({"empty_corpus", "below_threshold", "ambiguous"})

#: 三种弃答原因对用户的含义完全不同，文案不可混用。
_ABSTAIN_MESSAGES = {
    "empty_corpus": "知识库为空（暂无可检索文档）",
    "below_threshold": "未找到相关内容",
    "ambiguous": "找到的内容不够确定",
}


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


def _truncate_to_tokens(text: str, limit: int) -> str:
    """把文本截到 token 预算内（``estimate_tokens`` 单调，二分安全）。"""
    text = text or ""
    if limit <= 0:
        return ""
    if estimate_tokens(text) <= limit:
        return text
    low, high = 0, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        if estimate_tokens(text[:mid]) <= limit:
            low = mid
        else:
            high = mid - 1
    return text[:low].rstrip()


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


#: 弃答时也要给模型的指令：没有资料就明说，不许用自身记忆补数字/结论。
#: 这不是“注入片段”（used 仍为 False），而是把“知识库里没有”变成可执行的回答约束。
_NO_KNOWLEDGE_INSTRUCTION = (
    "\n\n[知识库检索结果]\n"
    "本次没有检索到与该问题相关的资料。\n"
    "如果用户的问题需要依据知识库/文档内容回答，请直接说明“知识库中没有找到相关内容”，"
    "不要猜测，也不要用模型自身记忆补全数字、步骤或结论；"
    "如果只是普通闲聊，可以正常回答，但不要声称内容来自知识库。\n"
    "[知识库检索结果结束]"
)


def _abstained(
    reason: str, human_reason: str, diagnostics: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """``used=False`` 的弃答结果，但带上“没有资料”的模型指令。"""
    result = _not_used(reason, human_reason, diagnostics)
    result["context"] = _NO_KNOWLEDGE_INSTRUCTION
    result["context_tokens"] = max(1, estimate_tokens(_NO_KNOWLEDGE_INSTRUCTION))
    return result


#: 旧实现的“无相关内容”表达（兼容用）。权威字段存在时不会走到这里。
_NEGATIVE_FLAGS = ("no_relevant", "no_relevant_content", "no_answer", "not_relevant")
_NEGATIVE_STATUS = frozenset(
    {"no_relevant", "no_relevant_content", "no_match", "not_found", "not_relevant"}
)


def _retrieval_info(result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    retrieval = result.get("retrieval")
    return retrieval if isinstance(retrieval, dict) else None


def _authoritative_verdict(result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """读取知识库定稿契约；返回 ``None`` 表示没有权威字段（走旧逻辑）。

    权威字段一旦存在就压过旧的并集匹配：``status="ok"`` 永远不算弃答，
    即使某个遗留标记为真。``no_relevant`` 时 ``reason`` 只认三选一，
    缺失/未知一律按 ``below_threshold`` 处理（不夸大成“知识库为空”）。
    """
    retrieval = _retrieval_info(result)
    if retrieval is None:
        return None
    status = str(retrieval.get("status") or "").strip().lower()
    if status not in (_STATUS_OK, _STATUS_NO_RELEVANT):
        return None
    raw_reason = retrieval.get("reason")
    reason = str(raw_reason).strip().lower() if raw_reason is not None else None
    if status == _STATUS_OK:
        reason = None
    elif reason not in _ABSTAIN_REASONS:
        reason = "below_threshold"
    thresholds = retrieval.get("thresholds")
    return {
        "status": status,
        "reason": reason,
        "top_score": retrieval.get("top_score"),
        "runner_up_score": retrieval.get("runner_up_score"),
        "thresholds": dict(thresholds) if isinstance(thresholds, dict) else {},
    }


def _retrieval_diagnostics(
    verdict: Dict[str, Any], hits: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """元信息里的“为什么这样决定”：原样带上知识库的分数与阈值。"""
    return {
        "gate": "knowledge",
        "retrieval_status": verdict["status"],
        "retrieval_reason": verdict["reason"],
        "candidates": len(hits),
        "top_score": verdict["top_score"],
        "runner_up_score": verdict["runner_up_score"],
        "thresholds": verdict["thresholds"],
    }


def _score_anomaly(hits: List[Dict[str, Any]]) -> bool:
    """分数缺失/非数值/非有限/负数时返回 True（网关此时才保留否决权）。

    知识库判 ``ok`` 时分数只用于展示与引用；只要数据可用，就不该用网关
    自己的门槛二次否决——两套阈值尺度不同（RRF 融合分 vs 余弦分）。
    """
    if not hits:
        return True
    for hit in hits:
        # 注意：这里的 True 是「检测到异常」，不是「成功」。仍然不把 return
        # 放进 except 分支——门禁 liuhao-swallow-exception-return-success 无法
        # 区分两者，而放宽规则等于给真正的「谎报成功」开口子。
        value = None
        try:
            value = float(hit.get("score"))
        except (TypeError, ValueError):
            value = None
        if value is None or not math.isfinite(value) or value < 0:
            return True
    return False


def _legacy_says_no_relevant(result: Dict[str, Any]) -> bool:
    """旧实现的“无相关内容”并集匹配；仅在缺少权威 ``status`` 时使用。

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


def _scope_for_principal(principal: Optional[str]):
    """把调用者账号映射成知识库检索范围；没有账号内核时退化为"只有自己"。

    知识库是账号自有资产：主账号与成员一样只检索自己的文档（外加单机老
    部署的无归属历史），不聚合子账号资料。这样成员资料不会被混进主账号的
    回答，也让"检索不串"与删除边界同口径。
    """
    if not principal:
        return None
    from src.knowledge.documents import KnowledgeScope

    try:
        from src.kernels.identity import get_identity_manager

        node = get_identity_manager().get_account(principal)
    except Exception:
        node = None
    if node is None:
        return KnowledgeScope(
            principal=principal,
            owners=(principal,),
            include_unowned=True,
            owns_legacy=True,
            unowned_writes=True,
        )
    if node.is_root:
        return KnowledgeScope(
            principal=principal,
            owners=(principal,),
            include_unowned=True,
            owns_legacy=True,
        )
    return KnowledgeScope(
        principal=principal,
        owners=(principal,),
        include_unowned=False,
        owns_legacy=False,
    )


def build_knowledge_context(
    query: str,
    enabled: Optional[bool] = None,
    principal: Optional[str] = None,
) -> Dict[str, Any]:
    """检索与 ``query`` 相关的知识片段，返回注入用的上下文与引用。

    ``enabled=False`` 时完全跳过（用户显式关闭）。``enabled=None`` 时自动：
    知识库可用才检索；空库/弃答由知识库侧的 ``retrieval`` 判定并原样透传。
    ``principal`` 是经核验的令牌主体：每个账号只检索自己的文档（主账号也
    不聚合子账号资料）；缺省（内部/评测调用）保持不限制，绝不据此绕过 HTTP
    侧判定。任何失败都返回 ``used=False`` 并说明原因，不抛异常、不影响普通对话。
    """
    if enabled is False:
        return _not_used("disabled_by_user", "用户已关闭本次检索")

    try:
        from src.knowledge.documents import get_knowledge_service

        service = get_knowledge_service()
        status = service.status()
    except Exception as exc:  # 知识库模块不可用：静默降级
        logger.warning("knowledge_context: status unavailable: %s", exc, exc_info=True)
        return _not_used("unavailable", "知识库暂时不可用，本次没有参考它。")

    if not status.get("enabled"):
        reason = str(status.get("reason") or "知识库不可用")
        logger.info("knowledge_context: not enabled: %s", reason)
        # MEM-020：不能只说"不可用"。把服务端真实原因（未配置 / 维度或模型
        # 错配 / 向量库故障）原样带给用户，否则"没查"会被误当成"查了没找到"。
        human = (
            reason
            if reason.startswith("知识库未启用")
            else f"知识库未启用，原因：{reason}"
        )
        return _not_used("unavailable", human)

    try:
        # 相关度阈值归知识库侧（有评测数据支撑），网关不再混入自己的门槛。
        # 唯一例外：显式声明接受 0 分（zero-vector 测试 provider）时传 0.0，
        # 这是知识库文档里约定的测试逃生门。
        threshold = 0.0 if _min_score() <= 0.0 else None
        result = service.search(
            query,
            top_k=_DEFAULT_TOP_K,
            threshold=threshold,
            scope=_scope_for_principal(principal),
        )
    except Exception as exc:  # 检索失败：静默降级，但记录真实原因
        logger.warning("knowledge_context: retrieval failed: %s", exc, exc_info=True)
        return _not_used("error", "检索暂时失败，本次没有参考知识库。")

    retrieval = _retrieval_info(result)
    raw_hits = result.get("hits")
    if raw_hits is None and retrieval is not None:
        # 契约里的 ``retrieval.hits`` 与顶层 ``hits`` 是同一份列表。
        raw_hits = retrieval.get("hits")
    hits: List[Dict[str, Any]] = [
        hit for hit in (raw_hits or []) if isinstance(hit, dict)
    ]
    verdict = _authoritative_verdict(result)

    if verdict is not None:
        # ---- 权威判定：以知识库侧为准 ----
        diagnostics = _retrieval_diagnostics(verdict, hits)
        if verdict["status"] == _STATUS_NO_RELEVANT:
            reason = verdict["reason"] or "below_threshold"
            logger.info(
                "knowledge injection skipped: gate=knowledge reason=%s "
                "top_score=%s runner_up=%s candidates=%d",
                reason,
                verdict["top_score"],
                verdict["runner_up_score"],
                len(hits),
            )
            return _abstained(reason, _ABSTAIN_MESSAGES[reason], diagnostics)

        # status=ok：不再用网关的 0.25 门槛二次否决（两套分数尺度不同）。
        # 网关只在数据不可用时兜底。
        if not hits:
            logger.warning(
                "knowledge_context: retrieval status=ok but hits empty; skipping"
            )
            return _not_used(
                "inconsistent", "检索结果标记为相关但没有任何片段", diagnostics
            )
        if _score_anomaly(hits):
            logger.warning(
                "knowledge_context: retrieval status=ok but scores unusable; skipping"
            )
            return _not_used(
                "score_anomaly", "检索结果的相关度分数缺失或异常", diagnostics
            )
        qualified = hits
        score_diagnostics = diagnostics
    else:
        # ---- 兼容旧实现：没有权威 status，才启用网关自己的门槛 ----
        candidate_diagnostics = {"candidates": len(hits), "gate": "gateway"}
        if _legacy_says_no_relevant(result):
            logger.info(
                "knowledge injection skipped: gate=gateway legacy=no_relevant "
                "candidates=%d",
                len(hits),
            )
            return _abstained("no_relevant", "未找到相关内容", candidate_diagnostics)

        if not hits:
            # 非空库里检索不到任何片段，同样是“未找到相关内容”，不是“库为空”。
            logger.info(
                "knowledge injection skipped: gate=gateway legacy=no_relevant "
                "candidates=0"
            )
            return _abstained("no_relevant", "未找到相关内容", candidate_diagnostics)

        injectable, decision, score_diagnostics = _score_gate(hits)
        score_diagnostics["gate"] = "gateway"
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
                human_reason = (
                    f"未找到足够相关的内容（最高分 {top} 低于门槛 {min_score}）"
                )
            logger.info(
                "knowledge injection skipped: gate=gateway decision=%s "
                "top_score=%s runner_up=%s min_score=%s min_margin=%s candidates=%d",
                decision,
                top,
                runner_up,
                min_score,
                score_diagnostics["min_margin"],
                len(hits),
            )
            return _abstained(decision, human_reason, score_diagnostics)

        # 通过保险后，再丢弃低于门槛的候选，绝不拿低分片段凑数。
        min_score = score_diagnostics["min_score"]
        qualified = []
        for hit in hits:
            try:
                score = float(hit.get("score") or 0.0)
            except (TypeError, ValueError):
                score = 0.0
            if score >= min_score:
                qualified.append(hit)
        if not qualified:
            return _abstained(
                "low_score",
                f"未找到足够相关的内容（最高分 "
                f"{score_diagnostics['top_score']} 低于门槛 {min_score}）",
                score_diagnostics,
            )

    # 按 token 预算拼装：与裁剪/配额/账本共用 ``estimate_tokens``。固定外壳
    # 与片段分隔符也占预算，先预留出来，保证最终 context_tokens 不超上限。
    _context_prefix = (
        "\n\n[知识库上下文]\n"
        "以下是与用户问题相关的资料片段。\n"
        "规则：\n"
        "1. 只能依据这些片段回答，不要使用片段之外的记忆、常识或推测补全；\n"
        "2. 使用了某条资料时，用来源编号标注（例如 [1]）；\n"
        "3. 如果片段中没有能回答问题的信息，直接回答“知识库中没有找到相关内容”，"
        "不要猜测，也不要编造数字、步骤或结论。\n\n"
    )
    _context_suffix = "\n[知识库上下文结束]"
    overhead_tokens = (
        estimate_tokens(_context_prefix)
        + estimate_tokens(_context_suffix)
        # 片段间 "\n\n" 分隔符：最多 top_k 段，每条 ≤1 token。
        + _DEFAULT_TOP_K
    )
    block_budget = max(0, KNOWLEDGE_MAX_CONTEXT_TOKENS - overhead_tokens)

    blocks: List[str] = []
    sources: List[Dict[str, Any]] = []
    used_tokens = 0
    for hit in qualified:
        text = _truncate(str(hit.get("text") or ""), _MAX_SNIPPET_CHARS)
        if not text:
            continue
        # 引用标签必须与 sources 的下标一一对应（len(blocks) + 1）：跳过空
        # 片段时编号不得移位，否则客户端把回复中的 [N] 映射回 sources[N-1]
        # 时会展示错误的命中块。
        index = len(blocks) + 1
        source = _source_of(hit)
        header = (
            f"[{index}] 文档={source['document_id']} "
            f"片段={source['chunk_index']} 相关度={source['score']}"
        )
        block = f"{header}\n{text}"
        block_tokens = estimate_tokens(block)
        if used_tokens + block_tokens > block_budget:
            remaining = block_budget - used_tokens
            # 预算不足以容纳标题+正文时停止；绝不为了凑数而破坏边界。
            if remaining < estimate_tokens(header) + _MIN_BLOCK_BODY_CHARS:
                break
            block = _truncate_to_tokens(block, remaining)
        blocks.append(block)
        sources.append(source)
        used_tokens += estimate_tokens(block)
        if used_tokens >= block_budget:
            break

    if not blocks:
        return _abstained("no_relevant", "未找到相关内容", score_diagnostics)

    context = _context_prefix + "\n\n".join(blocks) + _context_suffix
    context_tokens = max(1, estimate_tokens(context))

    score_diagnostics["injected"] = len(sources)
    logger.info(
        "knowledge injection applied: decision=inject hits=%d top_score=%s "
        "runner_up=%s context_tokens=%d",
        len(sources),
        score_diagnostics["top_score"],
        score_diagnostics["runner_up_score"],
        context_tokens,
    )
    return {
        "used": True,
        "reason": "",
        "decision": "inject",
        "message": f"本次使用知识库：{len(sources)} 个片段",
        "sources": sources,
        "context": context,
        "context_tokens": context_tokens,
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
    # ``used=False`` 但有 context 的情况是“弃答指令”：知识库没找到时要让模型
    # 明确说没找到，而不是用自己的记忆编一个答案。
    if not knowledge.get("used") and not knowledge.get("context"):
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
