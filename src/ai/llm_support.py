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

import contextvars
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
        # 模型调用在独立线程里执行，必须显式复制 ContextVar，否则 provider
        # 上报的真实 usage 到不了调用方的记账作用域（派活/Goal 会因此漏记）。
        ctx = contextvars.copy_context()
        try:
            box["value"] = ctx.run(generator, prompt)
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
            stripped = stripped[first_newline + 1:]
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
        # file_write 的输出是结构化回执：直接读路径，别把整段 JSON 当"结果"
        # 塞进汇总（那会变成用户看不懂的流水账）。
        path = value.get("rel_path") or value.get("path") or value.get("saved_path")
        if isinstance(path, str) and path.strip():
            size = value.get("bytes")
            suffix = f"（{size} 字节）" if isinstance(size, int) else ""
            return f"已写入文件 {path.strip()}{suffix}"
        if value.get("success") is False:
            for key in ("error", "message", "reason"):
                candidate = value.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    return candidate.strip()
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


def _artifact_paths(value: Any) -> List[str]:
    """Real file paths a tool output says it produced (never invented)."""
    if not isinstance(value, dict):
        return []
    paths: List[str] = []
    relative = value.get("rel_path")
    if isinstance(relative, str) and relative.strip():
        paths.append(relative.strip())
    else:
        for key in ("path", "saved_path", "file_path"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                paths.append(candidate.strip())
    for key in ("artifacts", "files", "outputs"):
        items = value.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, str) and item.strip():
                paths.append(item.strip())
            elif isinstance(item, dict):
                for sub in ("path", "file_path"):
                    candidate = item.get(sub)
                    if isinstance(candidate, str) and candidate.strip():
                        paths.append(candidate.strip())
    ordered: List[str] = []
    for path in paths:
        if path not in ordered:
            ordered.append(path)
    return ordered


#: 示例/占位域名：任何输出里都不允许出现（用户会当真）。
_PLACEHOLDER_URL_HINTS = (
    "example", "your-domain", "yourdomain", "domain.com", "test.com",
)


def _is_placeholder_url(url: str) -> bool:
    host = str(url or "").split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0].lower()
    if not host:
        return False
    if host in ("localhost", "127.0.0.1"):
        return True
    return any(hint in host for hint in _PLACEHOLDER_URL_HINTS)


def _strip_unverified_urls(text: str, allowed: Optional[set]) -> str:
    """Replace links that were not really retrieved (or are placeholders).

    ``allowed=None`` means "only drop placeholder domains" (used where the
    whitelist is unknown); passing a set also drops any link outside it.
    """
    if not text:
        return ""

    def _replace(match: "re.Match[str]") -> str:
        url = match.group(0).rstrip(_URL_TRAILING)
        if allowed is not None:
            # A link that was really retrieved this run stays, even when the
            # fixture/domain happens to look like a placeholder.
            return match.group(0) if url in allowed else "（未验证链接已省略）"
        if _is_placeholder_url(url):
            return "（未验证链接已省略）"
        return match.group(0)

    return _URL_PATTERN.sub(_replace, text)


#: 结果里自认"什么都没写"的说法：即便任务状态是 completed，也不能算产出。
_NO_OUTPUT_MARKERS = (
    "没有向工作区写入任何文件", "没向工作区写入任何文件", "未向工作区写入",
    "没有写入任何文件", "未写入任何文件", "没有产生任何产物",
    "没有产出任何内容", "未生成任何文件",
)


def task_output_facts(result: Any, *, require_artifact: bool = False) -> tuple:
    """What a task **really** produced: ``(produced, artifact_paths)``.

    ``produced`` is False for an empty result and for an explicit failure
    receipt -- a task that admits "没有写入任何文件" must not be reported as a
    success just because its status says completed.

    ``require_artifact`` raises the bar for goals that explicitly asked for a
    file: non-empty prose is a message, not a deliverable, so only a real path
    counts. Without this, "写了一份纪要" without ever touching the disk read as
    produced while ``artifacts`` stayed empty -- the same false success in a new
    shape.
    """
    if isinstance(result, dict) and result.get("success") is False:
        return False, []
    paths = _artifact_paths(result)
    if paths:
        return True, paths
    if require_artifact:
        # 目标要文件：没有真实路径就不算产出，文本不能替代交付物。
        return False, []
    text = _output_text(result).strip()
    if not text:
        return False, []
    if any(marker in text for marker in _NO_OUTPUT_MARKERS):
        return False, []
    return True, []


#: 目标要求"落到文件"的句式：动作词 + 近距离的产物名词。
#: 只匹配「产出/生成/整理成/写出/导出/保存/写入 + 文件/文档」，
#: 刻意不匹配裸「文件」——「读取这个文件」不是要产物。
_ARTIFACT_REQUEST_PATTERN = re.compile(
    r"(?:产出|生成|整理成|写出|导出|保存|写入|输出)"
    r"[^。；;，,\n]{0,20}"
    r"(?:文件|文档)"
)


def goal_requires_artifact(goal_text: str) -> bool:
    text = str(goal_text or "")
    return bool(_ARTIFACT_REQUEST_PATTERN.search(text))


#: 兼容别名：有调用方/并行改动仍在用私有名。公开名与私有名指向同一个
#: 函数，任何一侧改名都不会让另一侧的 `from ... import` 在启动时炸成 503。
_goal_requires_artifact = goal_requires_artifact


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
    # 任务名也是模型写的：它同样可能把活儿记到没参与的人头上。汇总里的
    # 每一处人名（任务名、确定性兜底、模型结论）都走同一份白名单。
    records = _sanitize_record_names(records)
    completed = [
        r for r in records
        if str(r.get("status") or "") in _SUMMARY_STATUS_OK
    ]
    failed = [r for r in records if str(r.get("status") or "") == "failed"]
    skipped = [r for r in records if str(r.get("status") or "") == "skipped"]
    requires_artifact = goal_requires_artifact(goal_text)
    produced = [
        r for r in completed
        if task_output_facts(r.get("result"), require_artifact=requires_artifact)[0]
    ]
    empty_completed = [
        r for r in completed
        if not task_output_facts(r.get("result"), require_artifact=requires_artifact)[0]
    ]

    deterministic = _deterministic_summary(goal_text, completed, failed, skipped)
    trusted = _trusted_compute_values(completed)
    real_sources, _fetched_at = _collect_source_dicts(completed)
    real_urls = {str(item["url"]) for item in real_sources}
    if generator is None or not produced:
        # 没有任何真实产出时不给模型"编一段"的机会：直接走证据兜底。
        return deterministic

    lines: List[str] = []
    for record in records:
        status = str(record.get("status") or "")
        name = str(record.get("name") or "未命名任务")
        capability = str(record.get("capability") or "")
        who = str(record.get("assigned_employee") or "").strip()
        reason = _shorten_text(record.get("assignment_reason") or "", limit=120)
        meta = [capability]
        if who:
            meta.append(f"执行人：{who}")
        if reason:
            meta.append(f"指派理由：{reason}")
        label = f"- {name}（{'；'.join(part for part in meta if part)}）"
        if status in _SUMMARY_STATUS_OK:
            text = _strip_unverified_urls(
                _strip_conflicting_claims(_output_text(record.get("result")), trusted),
                real_urls,
            )
            did_produce, paths = task_output_facts(
                record.get("result"), require_artifact=requires_artifact
            )
            if not did_produce:
                lines.append(f"{label}执行结束但没有产出：没有产物、没有可交付内容")
                continue
            line = f"{label}已完成并产出：{text[:600]}"
            if paths:
                line += f"；产物：{'、'.join(paths)}"
            lines.append(line)
        elif status == "failed":
            lines.append(
                f"{label}失败：{str(record.get('error') or '未知错误')[:300]}"
            )
        elif status == "skipped":
            lines.append(f"{label}未执行（跳过）")

    prompt = (
        "你是「鎏灏管家」的结果汇总员。请只根据下面的真实执行记录，"
        "用中文给用户写一段人能直接读懂的最终结论（不超过 400 字），"
        "不要编造记录之外的事实。\n"
        "写法要求：\n"
        "1. 第一句先给结论：目标完成到什么程度（全部完成 / 部分完成 / 未完成）。\n"
        "2. 再说分工：哪件事由谁做；记录里有「指派理由」时，用一句话说明为什么派给他。\n"
        "3. 有产物就把产物路径原样写出来（照抄，不要改写或推测路径）。\n"
        "4. 有失败任务必须单独写清楚：哪件失败、失败原因是什么、是否建议重试。"
        "记录里没有依据就不要猜原因，也不要把失败说成成功。\n"
        "5. 不要把子任务的原始输出整段粘贴过来；用自己的话概括结论和关键结果。\n"
        "6. 记录中已有确定性的计算结果（python_compute）时，结论必须与之一致，"
        "不得给出与之矛盾的数值，也不要用检索任务的推测覆盖它。\n"
        "7. 终态只有三种说法：①「已完成并产出」（必须带产物路径或可读结果）；"
        "②「已完成但无产出」（明确说没有可交付内容）；③「未完成」（附原因）。"
        "记录里写着「执行结束但没有产出」的任务，禁止说成已完成、禁止计入完成数。\n"
        "8. 目标要求写入文件、但记录里没有任何产物路径时，第一句必须说未完成，"
        "不得说全部完成，也不得用 100% 完成的口吻。\n"
        "来源规则：只有当结论依赖外部信息、且记录里确实给出了来源链接时，"
        "才附上 1-3 条真实来源；记录里没有链接就完全不写链接，"
        "并明确写「未检索到来源」。绝不新增、改写或猜测任何链接与日期，"
        "禁止使用 example.com / example1.com 这类示例或占位域名。\n\n"
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
    # 任何链接都必须来自本次真实检索；没有真实来源时一个链接都不许出现。
    allowed = real_urls | _urls_in(goal_text)
    cited = _urls_in(summary)
    # 只有当链接"本次没有真实抓到"时，示例域名才算编造；真实抓到的链接
    # （哪怕域名像 example.com 的测试源）必须留住。
    placeholders = {
        url for url in cited if url not in real_urls and _is_placeholder_url(url)
    }
    foreign = cited - allowed
    if placeholders or foreign:
        logger.warning(
            "model summary cited unverified link(s) (%s); using deterministic summary",
            sorted(placeholders | foreign)[:3],
        )
        return deterministic
    if real_sources:
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
    else:
        # 没有真实来源：模型必须明说"未检索到来源"，不能只字不提。
        # 检索任务失败（外网不可用）同样算"没有来源"。
        if any(
            str(r.get("capability") or "") == "network_bus"
            for r in list(completed) + list(failed)
        ):
            if "未检索到来源" not in summary and "没有来源" not in summary:
                summary = summary.rstrip() + "\n\n未检索到来源。"
    if empty_completed:
        summary = _ensure_empty_output_visibility(summary, empty_completed)
    unknown_staff = _unverified_staff_mentions(summary, records)
    if unknown_staff:
        # 把活儿记到不存在/没参与的人头上，和编造引用一样是伪造。
        logger.warning(
            "model summary credited unknown employee(s) %s; using deterministic summary",
            unknown_staff[:3],
        )
        return deterministic
    if goal_requires_artifact(goal_text) and not any(
        task_output_facts(r.get("result"))[1] for r in completed
    ):
        # 目标要文件、却没有任何产物：不许以完成的口吻交付。
        summary = (
            "未完成：本次没有产生任何产物文件（没有任何任务向工作区写入文件，"
            "也没有可交付的文件路径）。\n" + summary
        )
    if failed:
        # 模型可能漏写失败项或把整体说成"全部完成"；失败可见是硬要求，
        # 缺什么就用记录补什么，绝不把没成的说成成了。
        summary = _ensure_failure_visibility(summary, failed)
    conflicts = _conflicting_claims(summary, trusted)
    if conflicts:
        # Never ship a conclusion that contradicts a real compute result.
        logger.warning(
            "model summary contradicts compute output (%s); using deterministic summary",
            conflicts[:3],
        )
        return deterministic
    return summary[:2500]


def _ensure_failure_visibility(
    summary: str, failed: Sequence[Dict[str, Any]]
) -> str:
    """Append an evidence-only failure note when the model's summary hides it."""
    names = [str(record.get("name") or "未命名任务") for record in failed]
    missing = [name for name in names if name not in summary]
    claims_all_done = "全部完成" in summary and not any(
        word in summary for word in ("部分完成", "未完成", "失败")
    )
    if not missing and not claims_all_done:
        return summary
    details = []
    for record in failed[:5]:
        name = str(record.get("name") or "未命名任务")
        error = _shorten_text(record.get("error") or "未知错误", limit=120)
        details.append(f"{name}（{error}）")
    footer = (
        f"需要说明的是，有 {len(failed)} 项任务没有完成："
        + "；".join(details)
        + "。"
        + _retry_advice(failed)
    )
    return f"{summary}\n\n{footer}"


def _ensure_empty_output_visibility(
    summary: str, empty_completed: Sequence[Dict[str, Any]]
) -> str:
    """Name every task that "completed" without producing anything.

    A task whose receipt admits "没有写入任何文件" must never be counted as a
    success. When the model's summary silently treats it as done, append an
    evidence-only note so the user sees "已完成但无产出" instead of a 100%.
    """
    if not empty_completed:
        return summary
    names = [str(record.get("name") or "未命名任务") for record in empty_completed]
    missing = [name for name in names if name not in summary]
    if not missing:
        return summary
    details = "；".join(
        f"{name}（没有产物、没有可交付内容）" for name in missing[:5]
    )
    footer = f"另有 {len(missing)} 项任务已完成但无产出：{details}，不计入完成。"
    return f"{summary}\n\n{footer}"


#: 汇总里"谁做了什么"的说法：用这些模式抓出人名，核对是否真的在场。
_STAFF_MENTION_PATTERNS = (
    r"由([\u4e00-\u9fa5A-Za-z0-9_]{2,8}?)(?:完成|撰写|负责|产出|执行|提供|给出)",
    r"因为?用户点名指派给([\u4e00-\u9fa5A-Za-z0-9_]{2,8})",
    r"指派给([\u4e00-\u9fa5A-Za-z0-9_]{2,8})",
)

#: 任务名不像汇总那样成句，模型常写成「小文撰写宣传文案」。这里只抓
#: "人名 + 动作"的紧邻写法，避免把普通名词（如「文案撰写」）误当人名。
_TASK_NAME_STAFF_PATTERN = re.compile(
    r"([\u4e00-\u9fa5]{2,4})(?=撰写|负责|完成|执行|整理|调研|设计|开发|处理|编写)"
)

#: 这些词不是员工名，出现也不代表编造。
_STAFF_MENTION_STOPWORDS = {
    "用户", "系统", "管家", "模型", "本人", "团队", "员工", "我们", "负责人",
    "任务", "目标", "项目", "文案", "报告", "文件", "数据", "内容", "素材",
    "专业", "自动", "智能", "人工", "统一", "批量", "本次", "助手", "专员",
}


def _known_staff_names(records: Sequence[Dict[str, Any]]) -> set:
    known = {
        str(record.get("assigned_employee") or "").strip() for record in records
    }
    known.discard("")
    return known


def _looks_like_known_or_generic(name: str, known: set) -> bool:
    """人名白名单之外，普通名词（系统/自动/团队…）也不算编造。"""
    return name in known or any(word in name for word in _STAFF_MENTION_STOPWORDS)


def scrub_unverified_staff_mentions(
    text: Any, known_names: Sequence[str]
) -> str:
    """Replace staff names that this run cannot prove took part.

    The same whitelist is used for summaries, task names and any other
    user-facing prose: a name the record does not contain is not shown as
    "who did the work". The unknown name is replaced with 「某位员工」 rather
    than deleting the sentence, so the remaining claim stays readable.
    """
    raw = str(text or "")
    known = {str(name).strip() for name in known_names if str(name).strip()}
    if not raw or not known:
        return raw

    def _replace(match: "re.Match[str]") -> str:
        name = match.group(1).strip()
        if _looks_like_known_or_generic(name, known):
            return match.group(0)
        # 只替换人名本身，保留「由/指派给…」的句式与动作。
        start, end = match.span(1)
        return match.group(0)[: start - match.start()] + "某位员工" + match.group(0)[end - match.start():]

    out = raw
    for pattern in _STAFF_MENTION_PATTERNS:
        out = re.sub(pattern, _replace, out)

    def _replace_task_name(match: "re.Match[str]") -> str:
        name = match.group(1)
        if _looks_like_known_or_generic(name, known):
            return match.group(0)
        return "某位员工" + match.group(0)[len(name):]

    return _TASK_NAME_STAFF_PATTERN.sub(_replace_task_name, out)


def _sanitize_record_names(
    records: Sequence[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Copy records with any unverified staff name removed from ``name``."""
    known = _known_staff_names(records)
    sanitized: List[Dict[str, Any]] = []
    for record in records:
        item = dict(record)
        item["name"] = scrub_unverified_staff_mentions(
            record.get("name"), known
        )
        sanitized.append(item)
    return sanitized




def _unverified_staff_mentions(
    summary: str, records: Sequence[Dict[str, Any]]
) -> List[str]:
    """Names the summary credits with work that are not in this run's records.

    The model must not invent who did the work: a summary saying "由小文撰写"
    when the task was assigned to 小研 is indistinguishable from a fabricated
    citation for the user. Unknown names => reject the model summary.
    """
    known = {
        str(record.get("assigned_employee") or "").strip() for record in records
    }
    known.discard("")
    if not known:
        return []
    unknown: List[str] = []
    for pattern in _STAFF_MENTION_PATTERNS:
        for raw in re.findall(pattern, summary):
            name = str(raw).strip()
            if not name or name in known or name in unknown:
                continue
            if name in _STAFF_MENTION_STOPWORDS:
                continue
            unknown.append(name)
    return unknown


def _shorten_text(text: Any, limit: int = 220) -> str:
    """Cut an excerpt at a **sentence/clause boundary**, never mid-word.

    A raw ``text[:220]`` used to leave half a word behind (``…功能与授``) and
    the next part was then glued on with a space, producing
    ``…功能与授 整理三条要点：``. Prefer the last sentence terminator, then the
    last clause punctuation, then a whitespace boundary; only when the text has
    no boundary at all do we hard-cut (character slicing never splits a CJK
    character).
    """
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(cleaned) <= limit:
        return cleaned
    head = cleaned[:limit]
    cut = max(head.rfind(ch) for ch in "。！？；!?;\n")
    if cut > 0:
        return head[:cut + 1].strip()
    cut = max(head.rfind(ch) for ch in "，,、：: ")
    if cut > 0:
        return head[:cut].rstrip("，,、：: ") + "…"
    return head.rstrip()


def _join_summary_parts(parts: Sequence[str]) -> str:
    """Join fallback parts without double spaces or an orphaned separator.

    Once a part ends with Chinese sentence punctuation the next sentence
    follows directly (no extra space); otherwise a single space is used.
    """
    out = ""
    for raw in parts:
        part = re.sub(r"\s+", " ", str(raw or "")).strip()
        # 模型/工具的错误文案本身可能以「。」结尾，拼接时又补了一句，
        # 界面会出现「。。」；这里只折叠连续中文句号，不改变内容。
        part = re.sub(r"。{2,}", "。", part)
        if not part:
            continue
        if not out:
            out = part
        elif out[-1] in "。！？；.!?;":
            out += part
        else:
            out += " " + part
    return out.strip()


#: 这些字样说明失败来自临时性故障（模型超时/限流/空回复），重试有意义。
_TRANSIENT_ERROR_MARKERS = (
    "timeout", "timed out", "超时", "未返回", "限流", "rate limit", "429",
    "空内容", "unavailable", "temporarily", "connection reset",
)


def _retry_advice(failed: Sequence[Dict[str, Any]]) -> str:
    """Whether retrying the failed tasks is likely to help (evidence-based)."""
    text = " ".join(
        str(record.get("error") or "") for record in failed
    ).lower()
    if any(marker in text for marker in _TRANSIENT_ERROR_MARKERS):
        return "这类错误通常是临时故障，建议直接重试失败的任务。"
    return "这类错误需要先修正配置或目标描述，直接重试大概率仍会失败。"


#: 模型层故障的分型标记。命中即按分型给出一句可核对的原因，不猜。
_MODEL_FAILURE_CLASSES = (
    (
        "额度不足",
        ("insufficient", "quota", "balance", "402", "额度", "余额", "配额", "欠费"),
        "请充值或换用有额度的模型后重试。",
    ),
    (
        "密钥无效",
        ("401", "unauthorized", "invalid api key", "authentication", "鉴权", "密钥无效", "api key"),
        "请到「设置 → 模型管理」更新密钥后重试。",
    ),
    (
        "调用超时",
        ("timeout", "timed out", "超时", "未返回", "connection reset", "read timed out"),
        "这通常是临时故障，请稍后重试。",
    ),
    (
        "请求被限流",
        ("429", "rate limit", "限流", "too many requests"),
        "请稍后重试；持续限流请降低并发或更换模型。",
    ),
    (
        "模型未配置",
        ("mockprovider", "未配置", "not configured", "no model"),
        "请先到「设置 → 模型管理」配置并启用一个真实模型。",
    ),
)


def describe_model_failure(error: Any) -> tuple[str, str]:
    """把模型故障归类成 ``(人话原因, 下一步建议)``。

    只按异常文本里的可核对标记分型；认不出时如实说"原因未知"，不硬套。
    """
    text = str(error or "").lower()
    for label, markers, advice in _MODEL_FAILURE_CLASSES:
        if any(marker in text for marker in markers):
            return label, advice
    return "原因未知", "请检查模型配置与网络后重试，或补充更明确的需求。"


def failure_summary(error: Any) -> str:
    """A goal that died before/without results gets an evidence-only summary.

    This path exists because a model outage (timeout / quota / invalid key)
    used to leave ``evaluation`` empty: the page then showed "未完成" without
    saying why, and nothing stopped a later layer from filling the gap with
    invented prose. The text here never says 已完成/已生成 and never contains
    a file path, because no result was produced.
    """
    reason, advice = describe_model_failure(error)
    return (
        f"未完成：任务执行中断（{reason}）。本次没有生成任何文件，"
        f"也没有任何可交付的产物路径。{advice}"
    )



#: Arithmetic a summary may state: "128 + 64 = 192" / "128 + 64 的结果是 204".
#: A real ``python_compute`` result is authoritative; a research task's prose
#: is not allowed to contradict it (observed: 204 in the summary next to 192).
_ARITH_CLAIM_RE = re.compile(
    r"(-?\d+(?:\.\d+)?)\s*([+\-*/×÷])\s*(-?\d+(?:\.\d+)?)\s*"
    r"(?:[=＝]|的结果(?:是|为)|结果是)\s*(-?\d+(?:\.\d+)?)"
)


def _number_key(value: float) -> str:
    return f"{value:.10g}"


def _apply_operator(left: float, operator: str, right: float) -> Optional[float]:
    if operator == "+":
        return left + right
    if operator == "-":
        return left - right
    if operator in ("*", "×"):
        return left * right
    if operator in ("/", "÷"):
        return None if right == 0 else left / right
    return None


def _trusted_compute_values(completed: Sequence[Dict[str, Any]]) -> set:
    """Numbers the dedicated compute task really produced (never prose)."""
    values = set()
    for record in completed:
        if str(record.get("capability") or "") != "python_compute":
            continue
        for match in re.finditer(r"-?\d+(?:\.\d+)?", _output_text(record.get("result"))):
            try:
                values.add(_number_key(float(match.group(0))))
            except ValueError:
                continue
    return values


def _conflicting_claims(text: str, trusted: set) -> List[str]:
    claims: List[str] = []
    for match in _ARITH_CLAIM_RE.finditer(text or ""):
        left, operator, right, stated = match.groups()
        try:
            stated_value = float(stated)
            if trusted:
                # Trusted compute output wins: a different result is a conflict
                # even if the model's own arithmetic happens to be consistent.
                if _number_key(stated_value) not in trusted:
                    claims.append(match.group(0))
                continue
            actual = _apply_operator(float(left), operator, float(right))
        except ValueError:
            continue
        if actual is not None and abs(actual - stated_value) > 1e-6:
            claims.append(match.group(0))
    return claims


def _strip_conflicting_claims(text: str, trusted: set) -> str:
    """Drop only the sentences that state a contradictory computation."""
    if not text or not _ARITH_CLAIM_RE.search(text):
        return text
    kept: List[str] = []
    for sentence in re.split(r"(?<=[。！？!?\n])", text):
        if sentence and _conflicting_claims(sentence, trusted):
            continue
        kept.append(sentence)
    return "".join(kept).strip()


def _deterministic_summary(
    goal_text: str,
    completed: Sequence[Dict[str, Any]],
    failed: Sequence[Dict[str, Any]],
    skipped: Sequence[Dict[str, Any]],
) -> str:
    """Evidence-only fallback: names, statuses and real output excerpts.

    Terminal states are never blurred: a task that "completed" without any
    output is reported as 「已完成但无产出」 and explicitly excluded from the
    completed count; a goal that asked for a file but produced none is led by
    「未完成」. Links come only from sources the executor really returned.
    """
    parts: List[str] = []
    trusted = _trusted_compute_values(completed)
    requires_artifact = goal_requires_artifact(goal_text)
    produced = [
        r for r in completed
        if task_output_facts(r.get("result"), require_artifact=requires_artifact)[0]
    ]
    empty_completed = [
        r for r in completed
        if not task_output_facts(
            r.get("result"), require_artifact=requires_artifact
        )[0]
    ]
    real_sources, _fetched_at = _collect_source_dicts(completed)
    real_urls = {str(item["url"]) for item in real_sources}
    artifacts: List[str] = []
    for record in completed:
        # 只有真实产出的任务才能贡献产物路径；写失败回执里的 path 不算。
        for path in task_output_facts(record.get("result"))[1]:
            if path not in artifacts:
                artifacts.append(path)

    if goal_requires_artifact(goal_text) and not artifacts:
        parts.append(
            "未完成：本次没有产生任何产物文件（没有任何任务向工作区写入文件，"
            "也没有可交付的文件路径）。"
        )
    if produced:
        names = "、".join(str(r.get("name") or "未命名任务") for r in produced[:5])
        parts.append(f"已完成 {len(produced)} 项任务（均已产出）：{names}。")
    if empty_completed:
        names = "、".join(
            str(r.get("name") or "未命名任务") for r in empty_completed[:5]
        )
        parts.append(
            f"已完成 {len(empty_completed)} 项任务但无产出：{names}"
            "（没有产物、没有可交付内容），不计入完成。"
        )
    assignments = []
    for record in completed:
        who = str(record.get("assigned_employee") or "").strip()
        if not who:
            continue
        reason = _shorten_text(record.get("assignment_reason") or "", limit=80)
        line = f"{str(record.get('name') or '任务')}由{who}负责"
        if reason:
            line += f"（{reason}）"
        assignments.append(line)
    if assignments:
        parts.append("分工：" + "；".join(assignments[:5]) + "。")
    shown = 0
    for record in produced:
        text = _strip_unverified_urls(
            _strip_conflicting_claims(_output_text(record.get("result")), trusted),
            real_urls,
        )
        if not text:
            continue
        parts.append(f"{str(record.get('name') or '任务')}：{_shorten_text(text)}")
        shown += 1
        if shown >= 3:
            break
    if artifacts:
        parts.append("产物：" + "、".join(artifacts[:5]) + "。")
    if failed:
        names = "、".join(str(r.get("name") or "未命名任务") for r in failed[:5])
        first_error = _shorten_text(
            failed[0].get("error") or "未知错误", limit=200
        ).rstrip("。.;；")
        parts.append(
            f"未完成：仍有 {len(failed)} 项任务失败：{names}。首个错误：{first_error}。"
            + _retry_advice(failed)
        )
    if skipped:
        names = "、".join(str(r.get("name") or "未命名任务") for r in skipped[:5])
        parts.append(f"另有 {len(skipped)} 项任务未执行：{names}。")
    citations, fetched_at = _collect_citations(completed)
    if citations:
        stamp = f"（抓取时间 {fetched_at}）" if fetched_at else ""
        parts.append(f"来源{stamp}：" + "；".join(citations))
    elif any(
        str(r.get("capability") or "") == "network_bus"
        for r in list(completed) + list(failed)
    ):
        # 检索类任务跑过（或失败）却没有任何可引用来源：必须明说，不能留白。
        parts.append("未检索到来源。")
    if not parts:
        return "本次没有产生可汇总的执行结果；请补充更明确的目标后重试。"
    return _join_summary_parts(parts)


#: 研究类输出里表示"什么都没查到"的说法。带这些说法的任务，其来源链接
#: 与结论无关，不能拿来给汇总当引用（否则就是给用户塞无关名单）。
_NO_FINDING_MARKERS = (
    "未查到", "未找到", "没有找到", "未检索到", "无相关结果", "没有相关",
    "均未涉及", "未提供相关", "无法确认", "没有提供任何",
)


def _reports_no_finding(record: Dict[str, Any]) -> bool:
    text = _output_text(record.get("result"))
    if not text:
        return False
    head = text[:200]
    return any(marker in head for marker in _NO_FINDING_MARKERS)


def _collect_source_dicts(
    completed: Sequence[Dict[str, Any]],
    limit: int = 8,
) -> tuple[List[Dict[str, Any]], str]:
    """Collect only sources the executor really returned (never invented).

    A research task that explicitly reports "未查到 / 没有找到" must not lend its
    (irrelevant) links to the conclusion: doing so was how a homepage rewrite
    summary ended up citing five unrelated GitHub repositories. No finding means
    no citable source.
    """
    sources: List[Dict[str, Any]] = []
    fetched_at = ""
    seen = set()
    for record in completed:
        if _reports_no_finding(record):
            continue
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


#: Characters that end a link. Both half-width **and** full-width brackets/
#: punctuation must terminate the match: a Chinese prose summary commonly writes
#: ``https://example.com（抓取时间 2026-11-07T…）``, and if ``（`` were allowed
#: inside the URL the match would become ``https://example.com（抓取时间`` — a
#: string that is not in the whitelist, so the whole verified summary was
#: discarded ("unverified link" / "dropped all real citations").
_URL_TERMINATORS = (
    "\"'<>"
    "()（）[]［］【】{}｛｝"
    "，。；：、！？"
    "“”‘’「」『』《》〈〉"
    "…—"
)
_URL_PATTERN = re.compile(r"https?://[^\s" + re.escape(_URL_TERMINATORS) + r"]+")

#: Trailing punctuation stripped from a match. Full-width variants are included
#: so ``https://example.com，`` compares equal to the whitelisted link while a
#: different host/path is still rejected.
_URL_TRAILING = ".,;:!?…" + "，。；：、！？" + "）)】]」』”’\"'"


def _urls_in(text: str) -> set[str]:
    """Links that literally appear in a text (trailing punctuation stripped)."""
    return {match.rstrip(_URL_TRAILING) for match in _URL_PATTERN.findall(text or "")}


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "DEFAULT_TOOL_TIMEOUT_SECONDS",
    "ModelUnavailable",
    "compose_execution_summary",
    "default_generate",
    "describe_model_failure",
    "failure_summary",
    "generate_with_timeout",
    "goal_requires_artifact",
    "model_timeout_seconds",
    "parse_json_payload",
    "scrub_unverified_staff_mentions",
    "task_output_facts",
    "tool_timeout_seconds",
]
