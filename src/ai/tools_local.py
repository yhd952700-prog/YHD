"""Real, locally-executable tools — the capabilities that need no external service.

These close the gap left by the Execution Kernel's simulated ``ActionExecutor``:
a goal that maps to one of these capabilities now executes *for real* (locally,
sandboxed) instead of returning ``status: "simulated"``.

The flagship local tool is ``python_compute`` — it runs pure-compute Python through
the RestrictedPython backend (capability isolation: no ``import``/``open``/``eval``,
CPU timeout via a child process; **no resource isolation** — see
``src/plugins/sandbox/backends/restricted_python_backend.py``). It is genuinely
real: it executes the user's code and returns the computed value.

Why this matters for launch
---------------------------
"Autonomous agent" is dishonest if the execution layer only ever simulates. With
these tools registered, the autonomous loop can *actually do* local work, and
capabilities that require an external service (network, LLM provider) still fail
honestly with ``no active tool`` / backend-unavailable instead of pretending.

Two local tools:

* ``python_compute`` — pure compute via the RestrictedPython backend.
* ``file_write``     — a real file write, **contained** to the workspace root
  (``src/ai/workspace.py``). Autonomous writes are only defensible with a hard
  boundary, so containment is part of the tool, not the caller's job.
"""
from __future__ import annotations

import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

from .tool_registry import Tool, ToolRegistry
from .workspace import workspace_root, WorkspaceViolation
from .world_interface import FilesystemAdapter, WorldInterface, WorldRequest
from . import web_research
from .llm_support import (
    ModelUnavailable,
    default_generate,
    generate_with_timeout,
    tool_timeout_seconds,
)
from ..plugins.sandbox.backends.restricted_python_backend import (
    RestrictedPythonBackend,
    RESULT_VAR,
)

#: A goal may carry an executable payload behind an explicit directive, e.g.
#: ``"python: result = sum(range(5))"`` or ``"code: result = 6*7"``. We only
#: treat the goal as code when such a directive is present — never by guessing
#: that arbitrary natural language is executable.
_CODE_DIRECTIVE = re.compile(
    r"^\s*(?:python|py|code|计算|执行代码)\s*[:：]\s*(?P<code>.+)$",
    re.IGNORECASE | re.DOTALL,
)


def _resolve_code(inputs: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """Resolve the Python source to execute from the action inputs.

    Returns ``(code, reason)`` — exactly one is not ``None``. The planner (an
    LLM, or a human) is responsible for producing executable code; this helper
    only recognises three *explicit* shapes and never invents code from prose:

    1. ``code``       — a full program that assigns its answer to ``result``.
    2. ``expression`` — a pure expression, wrapped as ``result = <expr>``.
    3. ``goal``       — only when it carries a ``python:``/``code:`` directive.
    """
    raw = inputs.get("code")
    if isinstance(raw, str) and raw.strip():
        return raw, None

    expr = inputs.get("expression")
    if isinstance(expr, str) and expr.strip():
        return f"{RESULT_VAR} = {expr.strip()}", None

    goal = inputs.get("goal")
    if isinstance(goal, str):
        m = _CODE_DIRECTIVE.match(goal.strip())
        if m:
            return m.group("code").strip(), None

    return None, (
        "python_compute needs executable code. Provide `code` (a program that "
        "assigns its answer to `result`), an `expression`, or a goal carrying a "
        "`python:`/`code:` directive. The planner must produce the code — this "
        "capability will not guess Python from prose."
    )


def python_compute(**inputs: Any) -> Dict[str, Any]:
    """Run pure-compute Python locally via the RestrictedPython backend.

    Public entry point (used by both the Execution-Kernel tool and the cockpit
    assistant tool). Contract: the resolved code must assign its result to the
    variable ``result``. Unsafe code (``import``/``open``/``eval``) is rejected at
    compile time; runaway loops are killed by the child-process timeout. If
    RestrictedPython is not installed, the backend reports the reason instead of
    failing silently.
    """
    code, reason = _resolve_code(inputs)
    if code is None:
        return {
            "success": False,
            "status": "rejected",
            "error": reason,
        }

    try:
        timeout = int(inputs.get("timeout", 10))
    except (TypeError, ValueError):
        timeout = 10

    backend = RestrictedPythonBackend()
    res = backend.execute(
        "local-python-compute",
        [sys.executable, "-c", code],
        timeout=timeout,
    )

    if not res.success:
        # Distinguish a security rejection from a plain runtime failure so the
        # caller (and any human reading the audit log) knows *why* it didn't run.
        rejected = "REJECTED" in str(res.status)
        return {
            "success": False,
            "status": "rejected" if rejected else "failed",
            "error": res.error,
            "stage": str(res.status),
        }

    # 跑完但什么都没产出 = 没有答案。不能把「空字符串」当成功上报，否则
    # 管家会把一个未完成的计算任务显示成已完成（验收第 3/4 项的反例）。
    answer = (res.output or "").strip() or (res.stdout or "").strip()
    if not answer:
        return {
            "success": False,
            "status": "failed",
            "error": (
                "代码执行完成但没有产出答案：请把结果赋给 result 变量，"
                "或使用 print(...) 输出。空输出不会被当成成功。"
            ),
            "stage": "empty_result",
        }

    return {
        "success": True,
        "status": "executed",
        "result": answer,
        "stdout": res.stdout,
    }


def _model_call(prompt: str) -> str:
    """One bounded call to the configured default model (prose budget)."""
    return generate_with_timeout(
        default_generate, prompt, timeout=tool_timeout_seconds()
    )


def _model_failure(exc: BaseException) -> Dict[str, Any]:
    """Turn a model failure into a readable, actionable tool failure."""
    if isinstance(exc, ModelUnavailable):
        error = str(exc)
    elif isinstance(exc, TimeoutError):
        error = (
            f"默认模型调用超时：{exc}。请检查模型服务，或调大 "
            "LIUHAO_LLM_TOOL_TIMEOUT_SECONDS 后重试。"
        )
    else:
        error = (
            f"默认模型调用失败（{type(exc).__name__}）：{exc}。"
            "请检查「设置 → 模型管理」里的模型服务与网络后重试。"
        )
    return {"success": False, "status": "failed", "error": error}


def _live_research_answer(query: str, report: "web_research.ResearchReport") -> Dict[str, Any]:
    """Answer from REAL retrieved sources, with citations the user can check.

    The model is allowed to *summarise* the retrieved material only. If it
    fails, the deterministic source list is returned instead — the retrieval
    succeeded, so the task must not be marked failed just because the summary
    model was unavailable.
    """
    source_block = web_research.format_sources(report.sources, report.fetched_at)
    prompt = (
        "你是「鎏灏管家」里的调研执行体。下面是一次真实联网检索的结果。"
        "请只用这些来源回答问题：先给结论，再给 3-5 条依据（总长不超过 300 字）。"
        "严格要求：不得编造任何链接、日期、项目名或版本号；"
        "提到具体项目时必须保留其来源编号；如果来源不足以回答，直接说“未查到”。\n\n"
        f"问题：{query.strip()}\n\n"
        f"真实检索结果：\n{source_block}\n"
    )
    sources = [item.as_dict() for item in report.sources]
    meta = {
        "basis": "live_web",
        "sources": sources,
        "fetched_at": report.fetched_at,
        "providers": list(report.providers),
        "search_phrase": report.search_phrase,
    }
    try:
        text = _model_call(prompt).strip()
    except BaseException as exc:  # noqa: BLE001 - retrieval succeeded, summary did not
        return {
            "success": True,
            "status": "executed",
            "result": (
                f"已联网检索到以下真实来源（抓取时间 {report.fetched_at}）。"
                "默认模型本次未能生成摘要，以下直接列出原始来源，未做任何推断：\n"
                + source_block
            ),
            **meta,
            "model_error": type(exc).__name__,
            "summary": f"已检索到 {len(report.sources)} 条真实来源，抓取于 {report.fetched_at}。",
        }
    return {
        "success": True,
        "status": "executed",
        "result": f"{text}\n\n{source_block}",
        **meta,
        "summary": text[:300],
    }


#: A search task must not re-settle arithmetic that belongs to a dedicated
#: ``python_compute`` task. Observed live: the whole goal ("查项目，同时计算
#: 128 + 64") was passed as the query, and the research model volunteered
#: "128 + 64 的结果是204" on one run and "...192" on the next. The retrieval
#: sources contained no arithmetic at all, so the number was pure free-form.
_ARITH_CLAUSE_RE = re.compile(r"\d\s*[+\-*/×÷]\s*\d")
_COMPUTE_CLAUSE_RE = re.compile(
    r"(?:^|[\s，。；;,])(?:请|帮我|请帮我|同时|并|顺便|另外)?"
    r"(?:计算|算一下|算算|算出)(?!机)"
)
_NUM_TASKS_CLAUSE_RE = re.compile(
    r"^(?:这|两|三|几|多|\d+)\s*件事.*(?:完成|做完)"
    r"|^(?:都|全部|一并|同时)?(?:要|需要|得)?(?:完成|做完|处理)(?:这[两三四几]件事)?$"
)


def _search_intent(text: str) -> str:
    """Keep only the clauses that describe *what to research*.

    Compute directives ("同时计算 128 + 64 的结果") and meta instructions
    ("两件事都要完成") are dropped so the research model is never asked to
    answer an arithmetic question it cannot ground in retrieved sources.
    """
    raw = str(text or "").strip()
    if not raw:
        return ""
    kept: List[str] = []
    for clause in re.split(r"[，。；;,.!?！？\n]+", raw):
        part = clause.strip()
        if not part:
            continue
        if _ARITH_CLAUSE_RE.search(part):
            continue
        if _COMPUTE_CLAUSE_RE.search(part):
            continue
        if _NUM_TASKS_CLAUSE_RE.search(part):
            continue
        kept.append(part)
    return "，".join(kept).strip() or raw


def llm_research(**inputs: Any) -> Dict[str, Any]:
    """Research a question, preferring LIVE retrieval over model memory.

    Live retrieval is read-only and uses key-free public endpoints. It is
    **off by default**: the user must turn it on in Settings, where the exact
    destination sites are disclosed. When it is disabled, blocked, empty or
    times out, this falls back to the original model-knowledge path and keeps
    its honest "未接入实时联网检索" label — a retrieval problem is never a
    task failure.
    """
    raw_query = (
        inputs.get("query")
        or inputs.get("goal")
        or inputs.get("instruction")
        or ""
    )
    if not isinstance(raw_query, str) or not raw_query.strip():
        return {
            "success": False,
            "status": "rejected",
            "error": "network_bus needs a `query` (the concrete question to research).",
        }
    # The kernel forwards the whole goal sentence as `query`; strip the
    # compute/meta clauses so this task only researches (see _search_intent).
    query = _search_intent(raw_query)

    retrieval_error = ""
    if web_research.enabled():
        # 先抹掉邮箱/电话/密钥/URL 再切句：反过来会把 alice@example.com 拆成
        # "alice@example" + "com"，残片逃过邮箱正则后仍会被当成关键词发出去。
        search_query = _search_intent(web_research.scrub_personal_data(raw_query))
        report = web_research.search(search_query)
        if report.ok:
            return _live_research_answer(query, report)
        retrieval_error = report.error or "检索没有返回任何来源"

    label = "（说明：本次由默认模型基于已有知识生成，未接入实时联网检索。）"
    if retrieval_error:
        # 用户看到的是"检索没成功，但问题照常回答"，不是一串 HTTP 错误。
        # 具体哪个站点、什么错误仍保留在 ``retrieval_error`` 字段与审计里。
        label = (
            "（说明：这次联网检索没成功（目标站点暂时连不上或没有返回结果），"
            "但你的问题照常回答——下面是基于已有知识的回答，不是今天的实时结果。）"
        )
    prompt = (
        "你是「鎏灏管家」里的调研执行体。请围绕下面的问题，给出尽可能具体、"
        "可核实的回答：先给结论，再给 3-5 条依据或候选（总长不超过 300 字）；"
        "如果涉及“今天/最新”而你无法确认实时信息，必须明确说明这一点并给出核实建议。\n\n"
        f"问题：{query.strip()}"
    )
    try:
        text = _model_call(prompt)
    except BaseException as exc:  # noqa: BLE001 - surfaced as failure
        return _model_failure(exc)
    return {
        "success": True,
        "status": "executed",
        "result": label + "\n" + text.strip(),
        "basis": "model_knowledge",
        "retrieval_error": retrieval_error,
        "summary": text.strip()[:300],
    }


def _upstream_citations(upstream: Any) -> Tuple[List[Dict[str, Any]], str]:
    """Collect only citations the upstream executors really returned.

    The synthesis model is allowed to summarise; it is *not* allowed to be the
    only carrier of the links. Anything here comes from a real tool output, so
    appending it back to the final text is honest even when the model drops it.
    """
    sources: List[Dict[str, Any]] = []
    fetched_at = ""
    seen: set = set()
    if not isinstance(upstream, list):
        return sources, fetched_at
    for item in upstream:
        if not isinstance(item, dict):
            continue
        output = item.get("output")
        if not isinstance(output, dict):
            continue
        if not fetched_at:
            fetched_at = str(output.get("fetched_at") or "")
        for raw in output.get("sources") or []:
            if not isinstance(raw, dict):
                continue
            url = str(raw.get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            sources.append({
                "title": str(raw.get("title") or ""),
                "url": url,
                "site": str(raw.get("site") or ""),
                "published_at": str(raw.get("published_at") or ""),
            })
            if len(sources) >= 12:
                return sources, fetched_at
    return sources, fetched_at


def llm_synthesis(**inputs: Any) -> Dict[str, Any]:
    """Turn upstream results / a goal into the user-facing deliverable.

    This backs the abstract ``execution_pipeline`` capability. Unlike a
    hard-coded formatter it uses the real upstream outputs (injected by the
    Execution Kernel as ``upstream``) so the conclusion is grounded in what the
    earlier tasks actually produced.
    """
    goal = inputs.get("goal") or ""
    instruction = (
        inputs.get("instruction")
        or inputs.get("description")
        or inputs.get("query")
        or ""
    )
    if not isinstance(goal, str):
        goal = str(goal)
    if not isinstance(instruction, str):
        instruction = str(instruction)
    if not (goal.strip() or instruction.strip()):
        return {
            "success": False,
            "status": "rejected",
            "error": "execution_pipeline needs a `goal` or `instruction` to synthesize.",
        }

    upstream_blocks = []
    upstream = inputs.get("upstream")
    if isinstance(upstream, list):
        for item in upstream:
            if not isinstance(item, dict):
                continue
            name = item.get("task") or item.get("name") or "上游任务"
            output = item.get("output")
            if isinstance(output, dict):
                output = output.get("result") or output.get("summary") or output
            text = str(output).strip()
            if text:
                upstream_blocks.append(f"【{name}】\n{text[:1500]}")
    citations, fetched_at = _upstream_citations(upstream)

    def _with_citations(text: str) -> str:
        """Always carry the real links, even when the model drops them."""
        if not citations:
            return text
        needed = min(3, len(citations))
        present = sum(1 for item in citations if str(item.get("url")) in text)
        has_time = (fetched_at in text) if fetched_at else True
        if present >= needed and has_time:
            return text
        block = web_research.format_source_dicts(citations, fetched_at)
        return f"{text}\n\n{block}" if text.strip() else block

    prompt = (
        "你是「鎏灏管家」的结果整理执行体。请完成下面这个任务，直接产出"
        "用户可用的最终内容（中文，结构清晰，有明确结论，总长不超过 500 字，"
        "不要只复述过程）。\n\n"
        f"用户目标：{goal.strip() or '（未提供）'}\n"
        f"本任务：{instruction.strip() or '整理上游结果并给出最终结论'}\n"
    )
    if upstream_blocks:
        prompt += "\n上游任务的真实结果：\n" + "\n\n".join(upstream_blocks) + "\n"
    prompt += (
        "\n只有当任务依赖外部事实、而现有信息确实不足以判断时，才说明缺什么，不要编造。"
        "写作、文案、方案、结构设计这类任务可以直接基于用户目标完成，"
        "不要求来源，也不得把「缺少来源」当成拒绝产出的理由。"
        "如果上游确实给出了来源（站点/链接/抓取时间），且结论引用了这些事实，"
        "就必须原样保留至少 3 条来源，不得新增或改写任何链接、日期、项目名；"
        "没有来源时不要编造，也不要为了凑来源去引用无关链接。"
    )

    try:
        text = _model_call(prompt)
    except BaseException as exc:  # noqa: BLE001 - surfaced as failure
        if upstream_blocks:
            # The upstream work really happened and is citable; losing the
            # summary model must not turn that into a failed task.
            fallback = (
                "（说明：整理模型本次不可用，以下直接保留上游真实结果，未做推断。）\n\n"
                + "\n\n".join(upstream_blocks)
            )
            if citations:
                block = web_research.format_source_dicts(citations, fetched_at)
                if block:
                    fallback += "\n\n" + block
            return {
                "success": True,
                "status": "executed",
                "result": fallback,
                "model_error": type(exc).__name__,
                "summary": "整理模型不可用，已直接保留上游真实结果。",
                **({"sources": citations, "fetched_at": fetched_at} if citations else {}),
            }
        return _model_failure(exc)
    final_text = _with_citations(text.strip())
    return {
        "success": True,
        "status": "executed",
        "result": final_text,
        "summary": final_text[:300],
        **(
            {"basis": "live_web", "sources": citations, "fetched_at": fetched_at}
            if citations else {}
        ),
    }


def _make_file_write(root: str):
    """Build the ``file_write`` callable bound to a **fixed** workspace root.

    The root is resolved **once, at registration time** (not per call): a root
    that can change mid-run produces "this round is allowed, the next is not"
    behaviour that nobody can reproduce. The same rationale as the read tools in
    ``src/ai/tools.py``.

    Containment: every path goes through :func:`resolve_in_workspace`, i.e. it is
    realpath-resolved (``..`` and symlinks collapses) and must land inside
    ``root``. Anything else raises :class:`WorkspaceViolation` and is reported as
    a failed action — fail-closed, never a silent escape upward.
    """
    fs_root = os.path.abspath(root)
    fs_world = WorldInterface(
        adapters=[FilesystemAdapter(root=fs_root)],
        # Autonomous face: default-DENY, and the only thing this interface is
        # allowed to do is write inside the workspace. No shell, no read-elsewhere.
        authorize=lambda request: (
            request.adapter == "filesystem" and request.action == "write"
        ),
        actor="autonomous",
    )

    def file_write(**inputs: Any) -> Dict[str, Any]:
        """Write ``content`` to ``path`` inside the workspace. Real side effect."""
        path = inputs.get("path") or inputs.get("file") or inputs.get("filename")
        if not isinstance(path, str) or not path.strip():
            return {
                "success": False,
                "status": "rejected",
                "error": (
                    "file_write needs a `path` (relative to the workspace root). "
                    "The planner must supply it — this tool will not invent one."
                ),
            }

        if "content" not in inputs:
            return {
                "success": False,
                "status": "rejected",
                "error": "file_write needs `content` (the exact bytes to write).",
            }
        content = inputs["content"]
        if isinstance(content, (int, float)):
            content = str(content)
        if not isinstance(content, str):
            return {
                "success": False,
                "status": "rejected",
                "error": (
                    f"file_write `content` must be a string (got "
                    f"{type(content).__name__}); refusing to serialize it."
                ),
            }

        # 账号分区（§3.5）：执行侧若带 owner 字段，产物落到 ``accounts/<owner>/``。
        # 调度逻辑不在这里改——这里只负责"按 owner 落盘"。
        owner_text = ""
        raw_owner = inputs.get("owner_account_id") or inputs.get("owner")
        if raw_owner is not None:
            owner_text = str(raw_owner).strip()
            if owner_text and (
                "/" in owner_text
                or "\\" in owner_text
                or owner_text in (".", "..")
            ):
                return {
                    "success": False,
                    "status": "rejected",
                    "error": "owner_account_id 含有非法路径字符，拒绝写入。",
                }
        rel_path = path.strip().lstrip("/")
        if owner_text and not rel_path.startswith("accounts/"):
            rel_path = f"accounts/{owner_text}/{rel_path}"

        result = fs_world.execute(
            WorldRequest(
                adapter="filesystem",
                action="write",
                params={"path": rel_path, "content": content},
            )
        )
        if not result.success:
            return {
                "success": False,
                "status": "failed",
                "error": result.error,
                "workspace_root": fs_root,
            }

        written = None
        if isinstance(result.output, dict):
            written = result.output.get("written")
        if written is None:
            # Fall back to resolving the same way the adapter did, purely for
            # reporting — the write itself already happened via the adapter.
            try:
                from .workspace import resolve_in_workspace

                written = resolve_in_workspace(rel_path, fs_root)
            except WorkspaceViolation:
                written = None
        return {
            "success": True,
            "status": "executed",
            "path": written,
            "rel_path": rel_path,
            "owner_account_id": owner_text or None,
            "bytes": len(content.encode("utf-8")),
            "workspace_root": fs_root,
        }

    return file_write


def register_default_local_tools(
    registry: ToolRegistry,
    root: Optional[str] = None,
) -> None:
    """Register the real, locally-executable tools into ``registry``.

    Walks each tool through the §40 lifecycle (REGISTER -> VALIDATE -> APPROVE ->
    ACTIVE) so it is immediately executable. Idempotent: re-registering is a no-op
    beyond re-asserting ACTIVE.

    Args:
        registry: target registry.
        root: workspace root for the file-write tool. Defaults to
            :func:`workspace_root` (env ``LIUHAO_WORKSPACE_ROOT``).
    """
    # Already registered — keep it simple and idempotent.
    if registry.get("local_python_compute") is None:
        registry.register(Tool(
            tool_id="local_python_compute",
            name="Local Python Compute",
            version="1.0.0",
            description=(
                "Execute pure-compute Python locally via the RestrictedPython backend. "
                "Supply a `code` program (assigning its answer to `result`), an "
                "`expression`, or a goal carrying a `python:`/`code:` directive. "
                "import/open/eval are blocked at compile time and there is a CPU "
                "timeout. Provides capability isolation only — not resource isolation."
            ),
            capability="python_compute",
            schema={"code": "str", "expression": "str", "goal": "str", "timeout": "int"},
            fn=python_compute,
            risk="LOW",
            sandbox_policy="restricted_python",
            audit_policy="p9.tool.execute",
        ))
        registry.validate("local_python_compute")
        registry.approve("local_python_compute")
        registry.activate("local_python_compute")

    fs_root = os.path.abspath(root or workspace_root())
    if registry.get("local_file_write") is None:
        registry.register(Tool(
            tool_id="local_file_write",
            name="Local Workspace File Write",
            version="1.0.0",
            description=(
                "Write a file inside the LIUHAO workspace. Requires `path` "
                "(relative to the workspace root) and `content`. The path is "
                "realpath-resolved and must stay inside the workspace root — "
                "traversal (`..`, absolute paths, symlinks pointing outward) is "
                "refused. This is a REAL side effect on disk."
            ),
            capability="file_write",
            schema={"path": "str", "content": "str"},
            fn=_make_file_write(fs_root),
            risk="MEDIUM",
            sandbox_policy="workspace_containment",
            audit_policy="p9.tool.execute",
        ))
        registry.validate("local_file_write")
        registry.approve("local_file_write")
        registry.activate("local_file_write")

    # Abstract capabilities that the planner has always been able to emit but
    # nothing could execute. They are backed by the *configured default model*
    # (resolved per call), so "research" and "organize" are real executions
    # rather than phantom tasks that doom the whole plan at validation time.
    if registry.get("local_llm_research") is None:
        registry.register(Tool(
            tool_id="local_llm_research",
            name="LLM Research (network_bus)",
            version="1.0.0",
            description=(
                "Research a concrete question with the configured default model. "
                "Requires `query` (or `goal`). Returns a model-knowledge answer "
                "explicitly labelled as such — this is not a live web crawler, so "
                "it must not be presented as today's internet result."
            ),
            capability="network_bus",
            schema={"query": "str", "goal": "str", "instruction": "str"},
            fn=llm_research,
            risk="MEDIUM",
            sandbox_policy="model_provider",
            audit_policy="p9.tool.execute",
        ))
        registry.validate("local_llm_research")
        registry.approve("local_llm_research")
        registry.activate("local_llm_research")

    if registry.get("local_llm_synthesis") is None:
        registry.register(Tool(
            tool_id="local_llm_synthesis",
            name="LLM Synthesis (execution_pipeline)",
            version="1.0.0",
            description=(
                "Produce the user-facing deliverable from the goal, the task "
                "instruction, and the REAL upstream task outputs injected by the "
                "Execution Kernel. Requires `instruction` or `goal`."
            ),
            capability="execution_pipeline",
            schema={"instruction": "str", "goal": "str", "description": "str"},
            fn=llm_synthesis,
            risk="MEDIUM",
            sandbox_policy="model_provider",
            audit_policy="p9.tool.execute",
        ))
        registry.validate("local_llm_synthesis")
        registry.approve("local_llm_synthesis")
        registry.activate("local_llm_synthesis")
