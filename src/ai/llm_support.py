"""Shared, layering-safe helpers for calling the *configured* default model.

The execution kernel must not import this module (strict layering:
``spec -> kernels -> ai -> gateway/console``). Both the decomposition layer
(:mod:`src.ai.llm_decomposer`) and the synthesis/summary layer
(:mod:`src.ai.agent_runtime`) go through here so that:

* the model is resolved from the model-config centre on **every** call
  (``resolve_provider()`` re-reads the store, so a newly saved model works
  without a process restart);
* a ``MockProvider`` is never mistaken for a real model -- that would turn a
  missing configuration into a fabricated answer;
* every call is bounded by a wall-clock timeout, and a timeout / empty
  completion / malformed output is surfaced as an exception so callers can
  fall back deterministically instead of hanging or silently succeeding.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from typing import Any, Callable, Dict, List, Optional, Sequence

logger = logging.getLogger("liuhao.ai.llm_support")

#: Default wall-clock budget for one *control* model call (planning / JSON).
DEFAULT_TIMEOUT_SECONDS = 60.0

#: Default budget for calls that must generate prose for the user (research /
#: synthesis / summary). A local 3B model on CPU routinely needs more than a
#: minute for a few hundred Chinese characters, so the control-plane timeout
#: would report a healthy model as "timed out".
DEFAULT_TOOL_TIMEOUT_SECONDS = 150.0

#: Marker key used by deterministic fallbacks so a UI can tell model text and
#: rule-based text apart without guessing.
_SUMMARY_STATUS_OK = {"completed", "completed_unattributed"}


class ModelUnavailable(RuntimeError):
    """The configured default model cannot be used right now."""


def model_timeout_seconds() -> float:
    raw = os.environ.get("LIUHAO_LLM_TIMEOUT_SECONDS", "")
    try:
        value = float(raw) if raw else DEFAULT_TIMEOUT_SECONDS
    except (TypeError, ValueError):
        value = DEFAULT_TIMEOUT_SECONDS
    # Never allow a zero/negative budget: that would turn every call into a
    # guaranteed timeout and make the fallback look like a model outage.
    return max(5.0, value)


def tool_timeout_seconds() -> float:
    """Wall-clock budget for prose-generating calls (research/synthesis/summary).

    ``LIUHAO_LLM_TOOL_TIMEOUT_SECONDS`` wins; otherwise a user-set
    ``LIUHAO_LLM_TIMEOUT_SECONDS`` is honoured globally, and the last resort is
    the prose-specific default.
    """
    raw = os.environ.get("LIUHAO_LLM_TOOL_TIMEOUT_SECONDS", "")
    if not raw:
        raw = os.environ.get("LIUHAO_LLM_TIMEOUT_SECONDS", "")
    try:
        value = float(raw) if raw else DEFAULT_TOOL_TIMEOUT_SECONDS
    except (TypeError, ValueError):
        value = DEFAULT_TOOL_TIMEOUT_SECONDS
    return max(10.0, value)


def resolve_execution_provider(requester: Optional[str] = None):
    """执行侧模型凭据的**唯一解析入口**（拆解 / 检索 / 整理 / 汇总都走这里）。

    ``requester`` 是发起本次执行的主体标识（主账号 / 子账号 / 员工）。
    账号体系尚未接入，因此当前忽略该参数，行为与 ``resolve_provider()``
    完全一致；将来要支持"主账号用子账号 Key"时，只需要在这里把
    ``requester`` 映射成 ``resolve_provider(visible_to=[...],
    allow_unowned=...)``，所有执行侧调用方都不用改。

    注意：执行侧禁止直接调用 ``providers.resolve_provider()``，否则账号
    隔离逻辑会散落到各处。
    """
    # Imported lazily: providers imports model_config which touches storage.
    from .providers import resolve_provider

    return resolve_provider()


def default_generate(prompt: str, *, requester: Optional[str] = None) -> str:
    """Call the configured default model, refusing the Mock provider.

    Raises:
        ModelUnavailable: no real model is configured, or the model returned
            nothing.
        Exception: any provider-level failure is propagated unchanged so the
            caller can record the real root cause.
    """
    from .providers import MockProvider

    provider = resolve_execution_provider(requester)
    if isinstance(provider, MockProvider):
        raise ModelUnavailable(
            "默认模型未配置：当前解析到 MockProvider。请到「设置 → 模型管理」"
            "添加并启用一个默认模型后重试。"
        )
    text = provider.generate_with_retry(prompt)
    if not isinstance(text, str) or not text.strip():
        raise ModelUnavailable(
            "默认模型返回了空内容，本次不视为成功；请检查模型服务后重试。"
        )
    return text


def generate_with_timeout(
    generator: Callable[[str], str],
    prompt: str,
    timeout: Optional[float] = None,
) -> str:
    """Run ``generator(prompt)`` in a daemon thread with a hard deadline.

    A stuck provider must never freeze a goal forever. The provider's own HTTP
    timeout still bounds the orphaned worker; this layer only guarantees the
    *caller* regains control and can fall back.
    """
    budget = timeout if timeout is not None else model_timeout_seconds()
    box: Dict[str, Any] = {}

    def _worker() -> None:
        try:
            box["value"] = generator(prompt)
        except BaseException as exc:  # noqa: BLE001 - transported to caller
            box["error"] = exc

    thread = threading.Thread(
        target=_worker, name="liuhao-llm-call", daemon=True
    )
    thread.start()
    thread.join(budget)
    if thread.is_alive():
        raise TimeoutError(f"模型调用超过 {budget:g} 秒未返回")
    if "error" in box:
        raise box["error"]
    text = box.get("value", "")
    if not isinstance(text, str) or not text.strip():
        raise ModelUnavailable("模型返回了空内容，本次不视为成功。")
    return text


def parse_json_payload(text: str) -> Any:
    """Extract the first JSON array/object from a model reply.

    Small local models often wrap JSON in prose or code fences, so we deliberately
    accept a surrounding envelope instead of demanding ``json.loads(text)``.
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError("模型没有返回内容")
    stripped = text.strip()
    if stripped.startswith("```"):
        first_newline = stripped.find("\n")
        if first_newline != -1:
            stripped = stripped[first_newline + 1 :]
        if stripped.rstrip().endswith("```"):
            stripped = stripped.rstrip()[:-3]
    decoder = json.JSONDecoder()
    for opener in ("[", "{"):
        index = stripped.find(opener)
        while index != -1:
            try:
                value, _end = decoder.raw_decode(stripped, index)
            except json.JSONDecodeError:
                index = stripped.find(opener, index + 1)
                continue
            return value
    raise ValueError("模型输出不是合法 JSON")


def _output_text(value: Any) -> str:
    """Flatten a task output into user-readable text."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("result", "text", "summary", "content", "answer", "output"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
            if isinstance(candidate, (dict, list)):
                nested = _output_text(candidate)
                if nested:
                    return nested
        try:
            return json.dumps(value, ensure_ascii=False)[:2000]
        except (TypeError, ValueError):
            return str(value)[:2000]
    if isinstance(value, (list, tuple)):
        return "；".join(part for part in (_output_text(v) for v in value) if part)[:2000]
    return str(value).strip()


def _result_sources(value: Any) -> List[Dict[str, Any]]:
    """Extract real, citable sources from a tool output (empty when none)."""
    if not isinstance(value, dict):
        return []
    sources = value.get("sources")
    if not isinstance(sources, list):
        return []
    return [
        item for item in sources
        if isinstance(item, dict) and str(item.get("url") or "").strip()
    ]


def compose_execution_summary(
    goal_text: str,
    records: Sequence[Dict[str, Any]],
    generator: Optional[Callable[[str], str]] = None,
    timeout: Optional[float] = None,
) -> str:
    """Synthesize the user-facing conclusion of a goal.

    ``records`` items use ``name`` / ``capability`` / ``status`` / ``result`` /
    ``error``. The model is tried first (when a generator is available and at
    least one task really produced output); any failure falls back to a
    deterministic, evidence-only sentence. No path ever invents a result.
    """
    completed = [
        r for r in records
        if str(r.get("status") or "") in _SUMMARY_STATUS_OK
    ]
    failed = [r for r in records if str(r.get("status") or "") == "failed"]
    skipped = [r for r in records if str(r.get("status") or "") == "skipped"]

    deterministic = _deterministic_summary(goal_text, completed, failed, skipped)
    real_sources, _fetched_at = _collect_source_dicts(completed)
    if generator is None or not completed:
        return deterministic

    lines: List[str] = []
    for record in records:
        status = str(record.get("status") or "")
        name = str(record.get("name") or "未命名任务")
        capability = str(record.get("capability") or "")
        if status in _SUMMARY_STATUS_OK:
            text = _output_text(record.get("result"))
            lines.append(f"- {name}（{capability}）已完成：{text[:600]}")
        elif status == "failed":
            lines.append(
                f"- {name}（{capability}）失败：{str(record.get('error') or '未知错误')[:300]}"
            )
        elif status == "skipped":
            lines.append(f"- {name}（{capability}）未执行（跳过）")

    prompt = (
        "你是「鎏灏管家」的结果汇总员。请只根据下面的真实执行记录，"
        "用中文给用户写一段最终结论（3-6 句，不超过 300 字，直接回答目标，"
        "不要复述任务清单，不要编造记录之外的事实）。如果记录不足以回答目标，"
        "明确说明还缺什么以及下一步怎么做。"
        "如果记录里有来源（站点/链接/抓取时间），必须保留至少 3 条来源，"
        "不得新增、改写或猜测任何链接与日期。\n\n"
        f"用户目标：{goal_text}\n\n"
        "真实执行记录：\n"
        + "\n".join(lines)
    )
    try:
        summary = generate_with_timeout(
            generator,
            prompt,
            timeout if timeout is not None else tool_timeout_seconds(),
        ).strip()
    except Exception as exc:  # noqa: BLE001 - fallback is the contract
        logger.warning("model summary unavailable (%s); using deterministic summary", exc)
        return deterministic
    if not summary:
        return deterministic
    if real_sources:
        # Never let the summary be the *only* carrier of citations, and never
        # let it cite a link the executors did not actually return.
        real_urls = {str(item["url"]) for item in real_sources}
        allowed = real_urls | _urls_in(goal_text)
        cited = _urls_in(summary)
        foreign = cited - allowed
        if foreign:
            logger.warning(
                "model summary cited %d unverified link(s); using deterministic summary",
                len(foreign),
            )
            return deterministic
        if not (cited & real_urls):
            logger.warning(
                "model summary dropped all real citations; using deterministic summary"
            )
            return deterministic
        if _fetched_at and _fetched_at not in summary:
            # Traceability requires the fetch time too, not just a link.
            citations, stamp = _collect_citations(completed)
            if citations:
                summary = (
                    f"{summary}\n\n来源（抓取时间 {stamp}）：" + "；".join(citations)
                )
    return summary[:2500]


def _deterministic_summary(
    goal_text: str,
    completed: Sequence[Dict[str, Any]],
    failed: Sequence[Dict[str, Any]],
    skipped: Sequence[Dict[str, Any]],
) -> str:
    """Evidence-only fallback: names, statuses and real output excerpts."""
    parts: List[str] = []
    if completed:
        names = "、".join(str(r.get("name") or "未命名任务") for r in completed[:5])
        parts.append(f"已完成 {len(completed)} 项任务：{names}。")
        shown = 0
        for record in completed:
            text = _output_text(record.get("result"))
            if not text:
                continue
            parts.append(f"{str(record.get('name') or '任务')}：{text[:220]}")
            shown += 1
            if shown >= 3:
                break
    if failed:
        names = "、".join(str(r.get("name") or "未命名任务") for r in failed[:5])
        first_error = str(failed[0].get("error") or "未知错误")[:200]
        parts.append(
            f"仍有 {len(failed)} 项任务失败：{names}。首个错误：{first_error}。"
            "下一步可以修复失败原因后对失败任务单独重试。"
        )
    if skipped:
        names = "、".join(str(r.get("name") or "未命名任务") for r in skipped[:5])
        parts.append(f"另有 {len(skipped)} 项任务未执行：{names}。")
    citations, fetched_at = _collect_citations(completed)
    if citations:
        stamp = f"（抓取时间 {fetched_at}）" if fetched_at else ""
        parts.append(f"来源{stamp}：" + "；".join(citations))
    if not parts:
        return "本次没有产生可汇总的执行结果；请补充更明确的目标后重试。"
    return " ".join(parts)


def _collect_source_dicts(
    completed: Sequence[Dict[str, Any]],
    limit: int = 8,
) -> tuple[List[Dict[str, Any]], str]:
    """Collect only sources the executor really returned (never invented)."""
    sources: List[Dict[str, Any]] = []
    fetched_at = ""
    seen = set()
    for record in completed:
        result = record.get("result")
        if not fetched_at and isinstance(result, dict):
            fetched_at = str(result.get("fetched_at") or "")
        for item in _result_sources(result):
            url = str(item.get("url") or "").strip()
            if url in seen:
                continue
            seen.add(url)
            sources.append({
                "title": str(item.get("title") or "").strip(),
                "url": url,
                "site": str(item.get("site") or "").strip(),
                "published_at": str(item.get("published_at") or "").strip(),
            })
            if len(sources) >= limit:
                return sources, fetched_at
    return sources, fetched_at


def _collect_citations(
    completed: Sequence[Dict[str, Any]],
    limit: int = 5,
) -> tuple[List[str], str]:
    """Human-readable citation lines built from the same verified sources."""
    sources, fetched_at = _collect_source_dicts(completed, limit=limit)
    lines: List[str] = []
    for item in sources:
        line = f"{item['title'] or item['url']} — {item['site'] or '来源'} — {item['url']}"
        if item["published_at"]:
            line += f"（{item['published_at']}）"
        lines.append(line)
    return lines, fetched_at


_URL_PATTERN = re.compile(r"https?://[^\s，。；：、）)】\]\"'<>]+")


def _urls_in(text: str) -> set[str]:
    """Links that literally appear in a text (trailing punctuation stripped)."""
    return {match.rstrip(".,;:!?") for match in _URL_PATTERN.findall(text or "")}


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "DEFAULT_TOOL_TIMEOUT_SECONDS",
    "ModelUnavailable",
    "compose_execution_summary",
    "default_generate",
    "generate_with_timeout",
    "model_timeout_seconds",
    "parse_json_payload",
    "tool_timeout_seconds",
]
