"""鎏灏智能中枢 主控 — 可对话的 AI OS 入口。

把「用户输入 → 权限检查 → 记忆召回 → 上下文构建 → 真实 LLM 生成 → 记忆持久化 →
审计留痕」串成一条可对话的闭环。这是"用鎏灏"的最小、真实的入口。

复用（不重写，全部建立在既有内核/能力层之上）：
- ``Agent`` / ``AgentMemory`` / ``AgentPolicy`` — AgentFactory 的构件
- ``providers`` — 真实 LLM（ollama qwen2.5）或 mock
- memory kernel  — ``AgentMemory`` 多轮记忆（tier/scope/tags 归属）
- policy kernel  — ``AgentPolicy`` 每轮授权（default-deny）
- audit kernel   — 每轮对话审计留痕（hash-chain，SQLite 落盘）

诚实原则：真实 LLM 生成失败时返回错误、审计记为失败，绝不伪造回复。
"""

from __future__ import annotations

import ast
import contextvars
import json
import logging
import os
import re
import threading
import time
import urllib.parse
import uuid
from collections import OrderedDict
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from .employee import Agent
from .agent_factory import (
    DEFAULT_MAX_OUTPUT_TOKENS,
    MAX_OUTPUT_TOKENS_CEILING,
    AgentMemory,
    AgentPolicy,
    economy_inputs,
)
from .providers import (
    BaseProvider,
    GENERATION_FAILURE_MESSAGE,
    ProviderStreamIncompleteError,
    ProviderTruncatedError,
    TRUNCATED_RESPONSE_MESSAGE,
    TurnCancelToken,
    TurnCancelled,
    certificate_failure_kind,
    is_address_rejected_error,
    is_certificate_error,
    resolve_provider,
    technical_detail_for,
    user_facing_failure,
)
from .conversation_store import get_conversation_store
from .context_budget import (
    DEFAULT_KEEP_RECENT_TURNS,
    estimate_tokens,
    resolve_context_window,
    trim_history,
)
from .tool_registry import ToolRegistry
from .tools import make_tools, build_tool_prompt, parse_tool_call
from ..kernels.identity import get_identity_manager
from ..kernels.audit import log_event, AuditEventType, AuditScope
from ..kernels.policy import (
    get_policy_engine,
    PolicyRule,
    PolicyCondition,
    PolicyAction,
    PolicyScope,
    PolicyOperator,
)
from .observability import TraceContext, observe, get_logger

DEFAULT_SYSTEM_PROMPT = (
    "你是「鎏灏智能中枢」（LiuHao AI OS），一个由十源 DNA（ULTRON / VISION / ADA / EDITH / "
    "FRIDAY / JARVIS / JOCaSTA / KAREN / ENOCH / ZOON）统一形成的 AI 操作系统人格。\n"
    "你运行在 LiuHao-AI-OS 之上，具备 14 个内核能力与 21-Phase 能力层。\n"
    "你诚实、可审计：不确定时明说，不伪造能力与数据；做不到的事直接说做不到。\n"
    "请用简洁、准确、有帮助的中文回答。"
)

# 会话构造时读取的历史条数上限（user+assistant 各算一条）。
# 240 条只是"裁剪源"：真正发给模型多少由 ``context_budget.trim_history``
# 按模型窗口决定，留出足够早期历史供摘要使用。
CONTEXT_SOURCE_MESSAGES = 240

# 单轮对话中最多允许的工具调用轮数（防 LLM 死循环请求工具）。
MAX_TOOL_ROUNDS = 3

_log = logging.getLogger(__name__)

#: 工具集按**画像主体**缓存：``make_tools`` 与每个工具的 §40 生命周期
#: （register/validate/approve/activate）每步都写一条审计，10 个工具 ≈ 40 条
#: 同步 SQLite 写入，实测占一次冷会话构造的绝大部分时间。工具表一旦构造完成
#: 只读执行（``ToolRegistry.execute`` 不改状态），因此同一主体共享一份是安全的。
_TOOL_CACHE_LOCK = threading.Lock()
_TOOL_BUILD_LOCKS: Dict[str, threading.Lock] = {}
#: 容量上限：按画像主体缓存，条数必须与账号数同阶而不能随请求数增长。
#: 超限按 LRU 淘汰（被淘汰的条目只影响下一次冷构造，不影响已持有它的会话）。
_TOOL_CACHE_MAX_DEFAULT = 64
_TOOL_CACHE: "OrderedDict[str, tuple[List[Any], str, ToolRegistry]]" = OrderedDict()


def _tool_cache_max() -> int:
    raw = (os.environ.get("LIUHAO_TOOL_CACHE_MAX") or "").strip()
    try:
        value = int(raw) if raw else _TOOL_CACHE_MAX_DEFAULT
    except ValueError:
        value = _TOOL_CACHE_MAX_DEFAULT
    return max(1, value)

#: 当前正在执行工具调用的 assistant。工具表共享后，``system_status`` 不能再把
#: 某一个 assistant 的 ``stats`` 绑死在闭包里；执行时按当前上下文解析会话。
#: 用 ``ContextVar`` 而不是 ``threading.local``：工具在独立 worker 线程执行时，
#: 注册表会 ``copy_context()`` 把该上下文带过去，``system_status`` 才不会退化成
#: "provider=unknown"。
_TURN_CONTEXT: "contextvars.ContextVar[Optional[Any]]" = contextvars.ContextVar(
    "liuhao_turn_assistant", default=None
)


def current_turn_stats() -> Dict[str, Any]:
    """返回**当前上下文正在执行工具的那个 assistant** 的 stats。

    非对话路径（例如直接调用工具）没有上下文时返回占位值，绝不抛异常：
    状态查询失败不能反过来打断工具本身。
    """
    assistant = _TURN_CONTEXT.get()
    stats = getattr(assistant, "stats", None)
    if callable(stats):
        try:
            return stats()
        except Exception:  # noqa: BLE001 - 见 docstring
            pass
    return {
        "provider": "unknown",
        "model": "unknown",
        "turn": 0,
        "history_messages": 0,
    }


def _shared_tools(profile_principal: str) -> "tuple[List[Any], str, ToolRegistry]":
    """构建/复用某个画像主体的工具集（每个主体只注册一次）。

    全局锁只护缓存字典；真正的构建在按主体的 build lock 下完成，因此不同主体
    可以并发构建，同一主体只有第一个线程重放注册审计，其余线程等待后直接命中。
    """
    with _TOOL_CACHE_LOCK:
        entry = _TOOL_CACHE.get(profile_principal)
        if entry is not None:
            _TOOL_CACHE.move_to_end(profile_principal)
            return entry
        build_lock = _TOOL_BUILD_LOCKS.setdefault(profile_principal, threading.Lock())
    with build_lock:
        try:
            with _TOOL_CACHE_LOCK:
                entry = _TOOL_CACHE.get(profile_principal)
                if entry is not None:
                    _TOOL_CACHE.move_to_end(profile_principal)
            if entry is None:
                tools = make_tools(profile_principal, status_fn=current_turn_stats)
                registry = ToolRegistry()
                for tool in tools:
                    registry.register(tool)
                    registry.validate(tool.tool_id)
                    registry.approve(tool.tool_id)
                    registry.activate(tool.tool_id)
                entry = (tools, build_tool_prompt(tools), registry)
                with _TOOL_CACHE_LOCK:
                    # 已持有旧条目的会话继续用它自己的引用，不受淘汰影响；
                    # 淘汰只让"下一次冷构造"重新注册一次。
                    while len(_TOOL_CACHE) >= _tool_cache_max():
                        _TOOL_CACHE.popitem(last=False)
                    _TOOL_CACHE[profile_principal] = entry
            return entry
        finally:
            # 只在仍是当前这把锁时移除：等待者已持有引用，醒来后会命中缓存，
            # 因此不会出现同一主体并发构建两份。
            with _TOOL_CACHE_LOCK:
                if _TOOL_BUILD_LOCKS.get(profile_principal) is build_lock:
                    _TOOL_BUILD_LOCKS.pop(profile_principal, None)


def _provider_endpoint(provider: Any) -> tuple[str, Optional[int]]:
    """从 provider 配置里取上游主机/端口（不发起任何请求）。"""
    raw = str(getattr(provider, "base_url", None) or "").strip()
    if not raw:
        name = type(provider).__name__.lower()
        if "ollama" in name:
            raw = os.environ.get("OLLAMA_BASE_URL") or "http://127.0.0.1:11434"
        elif "openai" in name:
            raw = os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com"
        elif "anthropic" in name:
            raw = "https://api.anthropic.com"
        elif "google" in name or "gemini" in name:
            raw = "https://generativelanguage.googleapis.com"
    if not raw:
        return "-", None
    parsed = urllib.parse.urlsplit(raw if "://" in raw else "//" + raw)
    host = parsed.hostname or "-"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return host, port


def _turn_failure_reason(exc: BaseException) -> Optional[str]:
    """取出 provider 在**脱敏前**算好的机器标签（``quota_exhausted`` 等）。

    异常消息已被中性化，用户看到的文案里没有 ``insufficient_quota``、
    ``revoked`` 这些词；只有这个标签能区分"去充值"和"稍后重试"。
    网络不可达返回 ``unreachable``，超时返回 ``timeout``，其余未知错误返回 ``None``。
    """
    kind = getattr(exc, "upstream_kind", None)
    if isinstance(kind, str) and kind:
        return kind
    # net_guard 的运行时拒绝原样抛出（没有 upstream_kind）：界面要能区分
    # "域名解析不了"与"地址指向内网"，否则只能落回通用文案。
    if is_address_rejected_error(exc):
        text = str(exc)
        return "dns_failed" if "解析" in text else "address_rejected"
    if is_certificate_error(exc):
        # 三类证书失败的动作不同（换网络/核对地址/核对时间），界面按
        # 细分类渲染，不能都落回一句"证书校验失败"。
        return certificate_failure_kind(exc) or "certificate"
    # 空回复没有 upstream_kind（不是 HTTP 失败），但界面需要知道"模型什么都没说"，
    # 而不是笼统的"网络异常"。
    if getattr(exc, "error_code", None) == "EMPTY_RESPONSE":
        return "empty_response"
    # provider 用 ``raise ... from`` 保留了底层连接异常；Ollama 直连路径则原样抛出
    # requests 异常。不归类的话 reason 会是 null，前端只能显示通用文案，无法区分
    # "端点填错"和"模型不存在"。
    for err in (exc, exc.__cause__):
        if err is None:
            continue
        if isinstance(err, TimeoutError) or type(err).__name__ in {
            "Timeout", "ReadTimeout", "ConnectTimeout", "ConnectTimeoutError",
        }:
            return "timeout"
        if isinstance(err, OSError):
            return "unreachable"
    return None


#: 预算（额度）拒绝与权限拒绝必须分开说、分开编码：前者是可自我修复的
#: 「钱不够」，后者是「不允许」，把两者都写成「权限不足」会让用户去改错地方。
BUDGET_DENIAL_REASON = "budget_exceeded"
PERMISSION_DENIAL_REASON = "permission_denied"
BUDGET_DENIAL_MESSAGE = (
    "本次调用会超出可用预算（按保守 token 估算），请调整额度或换更便宜的模型。"
)
PERMISSION_DENIAL_MESSAGE = "权限不足：当前账号未被授权执行对话操作。"


def _denial_reason(decision: Any) -> str:
    """把 policy 的拒绝归因到预算或权限（按实际命中的规则 id 判断）。

    只有 ``quota_enforcement`` 规则命中才算预算拒绝；其余（默认拒绝、
    账号越权等）都按权限拒绝处理。绝不靠文案猜测。
    """
    rules = getattr(decision, "denied_rules", None) or []
    rule_ids = {str(getattr(rule, "id", "") or "") for rule in rules}
    return BUDGET_DENIAL_REASON if "quota_enforcement" in rule_ids else PERMISSION_DENIAL_REASON


_UPSTREAM_CODE_RE = re.compile(r"""['"]code['"]\s*:\s*['"]([^'"]{1,80})['"]""")
_UPSTREAM_TYPE_RE = re.compile(r"""['"]type['"]\s*:\s*['"]([^'"]{1,80})['"]""")


def _upstream_token(value: Any) -> str:
    """上游 ``code`` / ``type`` 是单个标识符：去掉空白、引号与超长尾巴。"""
    text = str(value or "").strip()
    if not text:
        return "-"
    return text.split()[0][:64].replace('"', "").replace("'", "")


def _upstream_error_fields(exc: BaseException) -> tuple[str, str]:
    """上游返回的 ``code`` / ``type``，原样记进事件，让日志自己判断错误类型。

    额度耗尽（``insufficient_quota``）和模型不存在（``model_not_found``）以前只能
    靠 ``status=402/404`` 猜；把上游 code/type 原样落盘后，以后新出现的错误码
    也能在日志里被认出来，不需要改代码。

    取值的优先级：``ProviderHTTPError`` 在脱敏前保存的 ``upstream_code`` /
    ``upstream_type`` → OpenAI SDK 已解析的 ``body`` → 异常原文里的
    ``'code': '...'``。provider 会把错误体换成中性文案，所以第一条是主路径。
    """
    code = getattr(exc, "upstream_code", None)
    error_type = getattr(exc, "upstream_type", None)
    body = getattr(exc, "body", None)
    if isinstance(body, (dict, str, bytes)) and not (code and error_type):
        try:
            if isinstance(body, (str, bytes)):
                body = json.loads(body)
            if isinstance(body, dict):
                inner = body.get("error")
                source = inner if isinstance(inner, dict) else body
                code = code or source.get("code")
                error_type = error_type or source.get("type")
        except (ValueError, TypeError):
            pass
    text = f"{exc}"
    if not code:
        match = _UPSTREAM_CODE_RE.search(text)
        code = match.group(1) if match else None
    if not error_type:
        match = _UPSTREAM_TYPE_RE.search(text)
        error_type = match.group(1) if match else None
    return _upstream_token(code), _upstream_token(error_type)


def _log_llm_call_failure(
    logger: logging.Logger,
    exc: BaseException,
    provider: Any,
    *,
    correlation_id: str,
    duration_ms: float,
) -> None:
    """``evt=llm.call.failed``：一条 ERROR 同时回答"谁、连哪、为什么、多久"。

    以前只有一条 WARNING + traceback：``error.log`` 是空的，且 401 与网络不通
    在用户侧文案完全相同。这里把上游状态、错误类别、errno、DNS 主机、Key 指纹、
    超时值与来源、耗时、request_id 全部结构化，写进 ``error.log``。
    """
    try:
        try:
            from ..observability.boot_report import key_fingerprint
        except Exception:  # noqa: BLE001 - 指纹失败不影响主日志
            key_fingerprint = lambda _key: "none"  # noqa: E731

        host, port = _provider_endpoint(provider)
        text = f"{type(exc).__name__}: {exc}"
        lowered = text.lower()
        status = getattr(exc, "status_code", None)
        error_class = type(exc).__name__
        errno_value = getattr(exc, "errno", None)
        upstream_code, upstream_type = _upstream_error_fields(exc)
        dns_host = "-"
        if (
            "getaddrinfo failed" in lowered
            or "nameresolutionerror" in lowered
            or "failed to resolve" in lowered
            or "name resolution" in lowered
        ):
            error_class = "DNSFailure"
            errno_value = errno_value or 11001
            dns_host = host
        elif (
            "winerror 10061" in lowered
            or "connection refused" in lowered
            or "积极拒绝" in text
        ):
            error_class = "ConnectionRefused"
            errno_value = errno_value or 10061
        elif (
            isinstance(exc, TimeoutError)
            or "timed out" in lowered
            or "timeout" in type(exc).__name__.lower()
        ):
            error_class = "Timeout"
            errno_value = errno_value or 10060
        elif status is not None:
            error_class = (
                "AuthenticationError" if int(status) in (401, 403) else f"HTTP{status}"
            )

        provider_name = type(provider).__name__.replace("Provider", "").lower() or "unknown"
        logger.error(
            "evt=llm.call.failed provider=%s model=%s host=%s port=%s status=%s "
            "error_class=%s errno=%s dns_host=%s upstream_code=%s upstream_type=%s "
            "upstream_kind=%s key_fp=%s timeout_s=%s "
            "timeout_source=%s duration_ms=%.0f attempt=%s request_id=%s",
            provider_name,
            getattr(provider, "model", "-") or "-",
            host,
            port if port is not None else "-",
            status if status is not None else "-",
            error_class,
            errno_value if errno_value is not None else "-",
            dns_host,
            upstream_code,
            upstream_type,
            getattr(exc, "upstream_kind", None) or "-",
            key_fingerprint(getattr(provider, "api_key", None)),
            getattr(provider, "timeout", "-"),
            getattr(provider, "timeout_source", "default"),
            float(duration_ms),
            getattr(exc, "attempt", 1),
            correlation_id,
            exc_info=True,
        )
    except Exception:  # noqa: BLE001 - 诊断日志失败不能改变"生成失败"这一结论
        logger.warning(
            "chat generation failed correlation=%s provider=%s: %s",
            correlation_id,
            type(provider).__name__,
            exc,
            exc_info=True,
        )



def _json_safe_output(value: Any) -> Any:
    """Best-effort conversion to JSON-native data (dict/list/scalar)."""
    if value is None or isinstance(value, (bool, int, float, str, list, dict)):
        return value
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return str(value)


def coerce_tool_output(value: Any) -> Any:
    """Normalise tool output to structured JSON, not a Python ``repr`` string.

    New executions return ``dict``/``list``/scalar directly. For backward
    compatibility with old records (and any legacy caller) a string that is a
    Python repr such as ``"{'a': 1, 'b': None}"`` is parsed back into structure
    when it is safe; ordinary text is returned unchanged.
    """
    value = _json_safe_output(value)
    if isinstance(value, str) and value.lstrip()[:1] in "[{":
        try:
            parsed = json.loads(value)
        except ValueError:
            try:
                parsed = ast.literal_eval(value)
            except (ValueError, SyntaxError, MemoryError, RecursionError):
                return value
        if isinstance(parsed, (dict, list)):
            return _json_safe_output(parsed)
    return value


def _tool_output_text(value: Any) -> str:
    """Serialise a structured tool result for the model context (never repr)."""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


#: 回喂模型的单条工具结果上限（字符）。可用 ``LIUHAO_TOOL_OUTPUT_LIMIT`` 覆盖。
DEFAULT_TOOL_OUTPUT_LIMIT = 32000


def _tool_output_limit() -> int:
    raw = (os.environ.get("LIUHAO_TOOL_OUTPUT_LIMIT") or "").strip()
    try:
        value = int(raw) if raw else DEFAULT_TOOL_OUTPUT_LIMIT
    except ValueError:
        value = DEFAULT_TOOL_OUTPUT_LIMIT
    return max(1000, min(1_000_000, value))


def _persist_tool_output(text: str, tool_name: str) -> Optional[str]:
    """把超限结果完整落盘，返回路径；落盘失败返回 None（不阻断本轮）。"""
    try:
        from .workspace import workspace_root

        base = Path(workspace_root()) / "artifacts" / "tool_outputs"
        base.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r"[^0-9A-Za-z_.-]+", "_", tool_name)[:40] or "tool"
        filename = (
            f"{time.strftime('%Y%m%d-%H%M%S')}-{safe_name}-"
            f"{uuid.uuid4().hex[:8]}.txt"
        )
        path = base / filename
        path.write_text(text, encoding="utf-8")
        return str(path)
    except Exception as exc:  # noqa: BLE001 - 落盘失败只降级为纯截断
        _log.warning("工具结果落盘失败（tool=%s）：%s", tool_name, exc)
        return None


def _limit_tool_output(text: str, tool_name: str) -> str:
    """统一结果大小上限：超限截断并落盘，给模型一个可读引用。"""
    limit = _tool_output_limit()
    if len(text) <= limit:
        return text
    reference = _persist_tool_output(text, tool_name)
    head = text[:limit]
    if reference:
        return (
            f"{head}\n\n…[结果已截断] 原始 {len(text)} 字符，仅前 {limit} 字符进入上下文；"
            f"完整结果已保存到：{reference}"
            "（需要细节时可用 read_file 读取该路径）"
        )
    return (
        f"{head}\n\n…[结果已截断] 原始 {len(text)} 字符，仅前 {limit} 字符进入上下文；"
        "完整结果落盘失败，请缩小查询范围后重试。"
    )


def _empty_tool_output_hint(tool_name: str, value: Any) -> Optional[str]:
    """工具返回空值时给模型一条明确提示，避免它把空响应当成"没有答案"。"""
    if value is None:
        return (
            f"[工具 {tool_name} 返回空结果] 工具没有返回值（null）。"
            "请检查参数是否命中数据，或用一句话告诉用户没有找到内容，不要编造。"
        )
    if isinstance(value, str) and not value.strip():
        return (
            f"[工具 {tool_name} 返回空结果] 输出为空字符串（0 字符）。"
            "这通常表示目标存在但没有内容或查询无匹配；请如实告知用户，不要编造。"
        )
    if isinstance(value, (list, tuple)) and len(value) == 0:
        return (
            f"[工具 {tool_name} 返回空结果] 输出为空列表（0 条）。"
            "请如实告知用户没有匹配项，不要编造。"
        )
    if isinstance(value, dict) and len(value) == 0:
        return (
            f"[工具 {tool_name} 返回空结果] 输出为空对象。"
            "请如实告知用户没有内容，不要编造。"
        )
    return None


def _tool_feedback_text(tool_name: str, value: Any) -> str:
    """把工具结果回喂模型时，失败/需澄清必须是"人话指令"，不是 JSON 原文。

    直接把 ``{"status":"error","error":"请提供 text…"}`` 回喂给模型，模型会
    原样复述这段 JSON（用户看到的是一堆机器字段）。这里把失败原因与下一步
    动作翻成一句可执行的中文，并明确禁止复述 JSON；成功结果仍按原结构回喂。
    """
    payload = value
    if isinstance(value, str) and value.lstrip()[:1] in "{[":
        try:
            payload = json.loads(value)
        except ValueError:
            payload = None
    if isinstance(payload, dict):
        status = str(payload.get("status") or "")
        question = str(payload.get("question") or "").strip()
        if status == "needs_clarification" or question:
            return (
                f"[工具 {tool_name} 需要确认]\n{question or '缺少必要信息。'}\n"
                "请把上面的问题原样问用户，不要自己猜，也不要复述 JSON。"
            )
        error = payload.get("error") or payload.get("detail") or payload.get("message")
        # 只有明确失败才把 message 当错误原因：成功结果里 message 是给用户看的
        # 结论（例如「已设置提醒…」），不能误判成失败。
        if status in {"error", "failed", "failure"} or payload.get("success") is False:
            reason = str(error or "工具没有返回可用结果").strip()
            return (
                f"[工具 {tool_name} 执行失败]\n原因：{reason}\n"
                "请用一句中文告诉用户失败原因和下一步怎么做，不要复述 JSON 原文。"
            )
    empty_target = payload if isinstance(payload, (dict, list)) else value
    empty_hint = _empty_tool_output_hint(tool_name, empty_target)
    if empty_hint is not None:
        return empty_hint
    return _limit_tool_output(_tool_output_text(value), tool_name)


def _history_token_count(history: Any) -> int:
    """Total prompt tokens in a (possibly malformed) history list.

    This runs on the authorization path over externally persisted data, so it
    must never raise. :func:`estimate_tokens` is the project's single token
    heuristic and already degrades non-strings to 0; a raw non-dict entry that
    happens to be text is still prompt material, so it is counted rather than
    dropped.
    """
    try:
        entries = list(history)
    except Exception:  # pragma: no cover - defensive, non-iterable history
        return 0
    total = 0
    for entry in entries:
        if isinstance(entry, dict):
            total += estimate_tokens(entry.get("content"))
        else:
            total += estimate_tokens(entry)
    return total


def _declared_max_output_tokens(provider: Any) -> int:
    """The provider's output ceiling, clamped to a credible range.

    Never raises: a malformed declaration degrades to the default. A value
    above :data:`MAX_OUTPUT_TOKENS_CEILING` is clamped **and warned about**,
    so a misconfigured provider is visible instead of silently denying every
    request it serves (the estimate is worst-case, see the caller).
    """
    capabilities = getattr(provider, "capabilities", None)
    raw = getattr(capabilities, "max_output_tokens", None)
    try:
        declared = int(raw) if raw is not None else DEFAULT_MAX_OUTPUT_TOKENS
    except (TypeError, ValueError):
        _log.warning(
            "liuhao: provider declared a non-numeric max_output_tokens=%r; "
            "falling back to %d for the budget estimate",
            raw,
            DEFAULT_MAX_OUTPUT_TOKENS,
        )
        return DEFAULT_MAX_OUTPUT_TOKENS
    if declared <= 0:
        return DEFAULT_MAX_OUTPUT_TOKENS
    if declared > MAX_OUTPUT_TOKENS_CEILING:
        _log.warning(
            "liuhao: provider declared max_output_tokens=%d, above the "
            "credible ceiling %d; clamping the budget estimate to the "
            "ceiling (the estimate is worst-case, so this prevents a "
            "self-inflicted denial of legitimate traffic)",
            declared,
            MAX_OUTPUT_TOKENS_CEILING,
        )
        return MAX_OUTPUT_TOKENS_CEILING
    return declared


class LiuHaoAssistant:
    """鎏灏主控：一条真实的「输入→授权→生成→记忆→审计」对话闭环。

    每次 ``chat`` 都走完整链路，不跳过任何一步。同一个实例内维护多轮历史，
    跨轮保持上下文连续。
    """

    def __init__(
        self,
        name: str = "liuhao",
        agent_type: str = "assistant",
        system_prompt: Optional[str] = None,
        provider: Optional[BaseProvider] = None,
        capabilities: Optional[List[str]] = None,
        profile_principal: Optional[str] = None,
        audit_principal: Optional[str] = None,
    ) -> None:
        self.name = name
        self.principal = name
        # 审计归属主体：会话主体（name）可能是 gateway 拼出的会话级 id
        # （如 `liuhao-alice-s1`），而审计页按账号主体读；显式传入账号主体，
        # 使账本 actor（账号）与审计 principal_id（账号）能够一一对应。
        self.audit_principal = audit_principal or name
        # 画像主体：默认与会话主体一致；传入时同一用户可跨会话共享画像
        # （会话历史仍按 self.principal 隔离，二者刻意解耦）。
        self.profile_principal = profile_principal or name
        self.system_prompt = system_prompt or DEFAULT_SYSTEM_PROMPT
        # 模型配置中心优先：界面上保存的默认模型即时生效；未配置时回退 .env。
        self.provider = provider or resolve_provider()
        self.capabilities = list(capabilities or ["chat"])
        # 能力层可观测性：结构化日志 + trace/correlation 透传（第 12 轮审计增强）。
        self._log = get_logger("liuhao")
        # 本轮被成本护栏评估出来的预估花费（无定价时为 None）——记账时复用同
        # 一个数，避免"放行时按 A 判、记账时按 B 收"的错位。
        self._turn_estimated_cost: Optional[float] = None
        # 同一次评估里策略看到的可用额度（无 COST 配额时为 None）。
        self._turn_quota_available: Optional[float] = None
        # provider 生命周期：新请求会替换 provider；旧实例必须在安全时机
        # 关闭（httpx/openai client 不关闭会随请求数累积句柄）。
        self._provider_lock = threading.Lock()
        self._active_turns = 0
        self._retired_providers: List[BaseProvider] = []
        # 同一会话串行：两个标签页同时发消息时共享 turn / history，若并发进入
        # chat/chat_stream 会导致轮次号重复、记忆键（turn:N）互相覆盖。
        self._turn_lock = threading.Lock()
        # 本轮真实结果：gateway 依据它决定 done 帧状态与是否记用量。
        self.last_turn_status: Optional[str] = None
        self.last_turn_error: Optional[str] = None
        #: 机器可读的失败原因（quota_exhausted / key_revoked / ...），供网关
        #: 写进 done 帧，让界面给出"充值/换 Key"这类可行动文案。
        self.last_turn_reason: Optional[str] = None
        #: 失败轮的「技术详情」：只含上游 message（已脱敏、≤120 字），
        #: 供界面折叠展示；主文案必须是可行动的中文。
        self.last_turn_technical_detail: Optional[str] = None
        self.last_turn_correlation_id: str = ""
        #: 非流式路径的"正在重试"提示（由 provider 的重试回调写入）。
        self.last_turn_retry_notice: Optional[str] = None

        # 1. Identity — 复用 identity kernel（复用已有主体，或新建带信任分）。
        manager = get_identity_manager()
        identity = manager.get_identity_by_principal(self.principal)
        if identity is None:
            identity = manager.create_identity(principal=self.principal, trust_score=0.9)
        self.identity = identity

        # 2. Agent — 复用既有 Agent 模型（不重写）。
        self.agent = Agent(
            id=self.principal,
            agent_type=agent_type,
            name=self.principal,
            provider=self.provider,
            system_prompt=self.system_prompt,
        )

        # 3. Memory & Policy — 复用 kernel 之上的真实绑定。
        self.memory = AgentMemory(principal=self.principal)
        self.policy = AgentPolicy(principal=self.principal, capabilities=self.capabilities)

        # 4. 授权基线 — policy engine 是 default-deny，需显式注册一条
        #    "agent 主体可执行低风险动作"的授权规则，鎏灏才能被授权对话。
        self._ensure_chat_policy()

        # 5. 会话历史持久化 — 跨进程重启恢复多轮上下文（memory kernel 是
        #    内存态不落盘，对话历史走独立 ConversationStore，风格对齐 audit）。
        self._conv_store = get_conversation_store()
        self.history: List[Dict[str, str]] = self._conv_store.load_recent(
            self.principal, CONTEXT_SOURCE_MESSAGES
        )
        self.turn = self._conv_store.get_last_turn(self.principal)

        # 6. 工具注册 — 复用 ToolRegistry（§40 生命周期），暴露真实内核能力。
        #    工具集按画像主体全局缓存：注册一次、所有冷会话复用，避免每个新会话
        #    重放 40+ 条注册审计（这是冷启动的主要成本）。
        self._tool_list, tool_prompt, self.tools = _shared_tools(self.profile_principal)
        # system prompt 追加工具描述（提示词约束 + JSON 解析的诚实协议）。
        self.system_prompt = self.system_prompt + tool_prompt

    @contextmanager
    def serialized_turn(
        self, cancel_token: Optional["TurnCancelToken"] = None
    ) -> Iterator[None]:
        """把同一会话的整轮读写串行化（principal+session 维度）。

        gateway 在 ``chat`` / ``chat_stream`` 的完整轮次（含落盘、记账、结束帧）
        外层持有它：同一账号的两个标签页并发写同一会话时严格一轮一写，
        不同账号/会话各自是独立 assistant 实例，互不阻塞。

        传入 ``cancel_token`` 时改为**可取消的获取**：客户端已经离开的请求
        不应该在锁上继续排队（那等于让已断开的连接占着一个等待位）。
        """
        if cancel_token is None:
            with self._turn_lock:
                yield
            return
        while not self._turn_lock.acquire(timeout=0.25):
            if cancel_token.cancelled:
                raise TurnCancelled(cancel_token.mode or "client_disconnected")
        try:
            yield
        finally:
            self._turn_lock.release()

    def begin_turn(self) -> None:
        """标记一轮生成正在进行；期间被替换的 provider 延后关闭。"""
        with self._provider_lock:
            self._active_turns += 1

    def end_turn(self) -> None:
        """一轮生成结束；若无其它轮次在跑，关闭已退休的 provider。"""
        to_close: List[BaseProvider] = []
        with self._provider_lock:
            if self._active_turns > 0:
                self._active_turns -= 1
            if self._active_turns == 0 and self._retired_providers:
                to_close = list(self._retired_providers)
                self._retired_providers.clear()
        for provider in to_close:
            self._close_provider_quietly(provider)

    def set_provider(self, provider: Optional[BaseProvider]) -> None:
        """替换 provider（热加载），按是否存在进行中的轮次决定何时关闭旧实例。"""
        if provider is None or provider is self.provider:
            return
        to_close: Optional[BaseProvider] = None
        with self._provider_lock:
            old = self.provider
            self.provider = provider
            if hasattr(self.agent, "provider"):
                self.agent.provider = provider
            if old is not None:
                if self._active_turns > 0:
                    self._retired_providers.append(old)
                else:
                    to_close = old
        if to_close is not None:
            self._close_provider_quietly(to_close)

    def close(self) -> None:
        """关闭当前及已退休的 provider（会话被删除/淘汰时调用）。"""
        with self._provider_lock:
            providers: List[BaseProvider] = []
            seen: set = set()
            for provider in [self.provider] + list(self._retired_providers):
                if provider is not None and id(provider) not in seen:
                    seen.add(id(provider))
                    providers.append(provider)
            self._retired_providers.clear()
        for provider in providers:
            self._close_provider_quietly(provider)

    @staticmethod
    def _close_provider_quietly(provider: BaseProvider) -> None:
        closer = getattr(provider, "close", None)
        if callable(closer):
            try:
                closer()
            except Exception as exc:  # noqa: BLE001 - closing must not raise
                _log.warning("liuhao: provider close failed: %s", exc)

    @staticmethod
    def _ensure_chat_policy() -> None:
        """注册鎏灏 agent 低风险动作授权规则（幂等）。

        policy engine 内置规则全是 DENY（唯一 allow 是 human sovereignty，
        要求 actor.type == "human"）。agent 主体在 scope=L1 下没有任何 allow
        路径（default_deny 是 L7，被 scope filter 排除后落到 NOT_APPLICABLE）。
        这条规则补齐：agent 主体执行 LOW 风险动作（对话等）→ allow。
        """
        engine = get_policy_engine()
        engine.register_rule(
            PolicyRule(
                id="liuhao_agent_low_risk_allow",
                name="LiuHao Agent Low-Risk Allow",
                description="鎏灏 agent 主体可执行低风险动作（如对话）",
                conditions=[
                    PolicyCondition("actor.type", PolicyOperator.EQ, "agent"),
                    PolicyCondition("action.name", PolicyOperator.IN, ["chat"]),
                ],
                action=PolicyAction.ALLOW,
                scope=PolicyScope.L1,
                precedence=50,
            )
        )

    def _quota_authorize_kwargs(self, message: str) -> Dict[str, Any]:
        """Real cost/quota inputs for the policy quota rule (``chat`` gate).

        Estimates the worst-case spend of the turn about to be made -- prompt
        size plus the model's declared maximum output -- and hands it to
        ``economy_inputs``, which prices it against the real per-model price
        and the real ``COST`` quota. Anything genuinely unknown comes back
        *omitted*, never as a fabricated zero (see ``economy_inputs``); the
        policy decision then records the rule as an unapplied control.

        This is on the authorization path, so it must not raise: prompt
        estimates go through :func:`estimate_tokens` (the single project-wide
        implementation) over externally-sourced data, never raw ``len()``.
        """
        tokens_in = max(
            1,
            estimate_tokens(self.system_prompt)
            + _history_token_count(self.history)
            + estimate_tokens(message),
        )
        tokens_out = _declared_max_output_tokens(self.provider)
        inputs = economy_inputs(
            principal=self.audit_principal,
            model=getattr(self.provider, "model", None),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
        )
        # Remember exactly what the guard evaluated so the turn can be charged
        # the same number once it completes (see ``_commit_turn``). When the
        # model is unpriced there is no ``estimated_cost`` key, and we then
        # record nothing rather than inventing a 0.
        self._turn_estimated_cost = inputs.get("estimated_cost")
        self._turn_quota_available = (inputs.get("resource") or {}).get("available")
        return inputs

    # ------------------------------------------------------------------ #
    # 主入口
    # ------------------------------------------------------------------ #
    def _new_turn(self) -> str:
        """开一轮：轮次 +1、生成 correlation id、绑定 trace。

        网关的建流前闸门（``preflight_chat``）与 ``chat`` / ``chat_stream``
        必须共用它：轮次号与 correlation id 只能由一处产生，否则同一轮会出现
        两个编号、审计与响应也对不上。
        """
        self.turn += 1
        correlation_id = uuid.uuid4().hex[:16]
        self.last_turn_correlation_id = correlation_id
        self.last_turn_retry_notice = None
        # 把本轮回话 id 透传到能力层日志（供追踪"哪一轮触发了哪些 kernel action"）。
        TraceContext.set(correlation_id=correlation_id)
        return correlation_id

    def _on_retry_notice(self, notice: str) -> None:
        """provider 重试回调（非流式路径）：记录本轮正在重试，供网关外发。"""
        self.last_turn_retry_notice = str(notice or "") or None
        self._log.info(
            "chat retry notice turn=%s notice=%s", self.turn, notice
        )

    def _authorize_chat(self, message: str, *, phase: str) -> Optional[Dict[str, Any]]:
        """跑一次对话授权；拒绝时写审计并返回可直接外发的拒绝结果。

        ``phase`` 是拒绝发生的**位置**，用于把两类拒绝分清楚：

        * ``pre_stream``   —— 建流之前被拒（网关物理返回 402/403，没有 SSE）；
        * ``during_stream``—— SSE 已建立后的兜底复查被拒（只能走 ``done`` 帧）；
        * ``stream_direct``—— 直接消费 ``chat_stream`` 生成器（CLI/测试）时被拒；
        * ``non_stream``   —— ``POST /v1/chat`` 的拒绝。

        放行返回 ``None``；拒绝返回与 ``chat()`` 同构的结果（含 ``reply`` /
        ``reason`` / ``correlation_id``），由调用方决定走 HTTP 还是事件流。
        """
        decision = self.policy.authorize(
            "chat", risk_level="LOW", **self._quota_authorize_kwargs(message)
        )
        if decision.is_allowed:
            return None
        denial_reason = _denial_reason(decision)
        denial_message = (
            BUDGET_DENIAL_MESSAGE
            if denial_reason == BUDGET_DENIAL_REASON
            else PERMISSION_DENIAL_MESSAGE
        )
        correlation_id = self.last_turn_correlation_id
        self.last_turn_status = "denied"
        self.last_turn_error = denial_message
        self.last_turn_reason = denial_reason
        self.last_turn_technical_detail = None
        log_event(
            AuditEventType.ACCESS_DENIED,
            principal_id=self.audit_principal,
            scope=AuditScope.L1,
            outcome="deny",
            details={
                "turn": self.turn,
                "action": "chat",
                "reason": denial_reason,
                # 拒绝发生在建流前还是流中：审计与日志用同一个标签，便于
                # 排查"用户说没建流，日志却是 done 帧"这类口径错位。
                "phase": phase,
                "estimated_cost": self._turn_estimated_cost,
                "available": self._turn_quota_available,
            },
            correlation_id=correlation_id,
        )
        self._log.info(
            "chat turn denied phase=%s reason=%s correlation=%s",
            phase, denial_reason, correlation_id,
        )
        return {
            "reply": denial_message,
            "status": "denied",
            "reason": denial_reason,
            "turn": self.turn,
            "correlation_id": correlation_id,
            "agent": self.principal,
        }

    def preflight_chat(self, message: str) -> Optional[Dict[str, Any]]:
        """建流前的授权闸门（网关在返回 ``StreamingResponse`` 之前调用）。

        返回 ``None`` 表示已放行，且本轮 turn/correlation 已经就绪 —— 后续
        必须用 ``chat_stream(..., turn_prepared=True)``，不能再开一轮。
        返回 dict 表示已拒绝（已写 ACCESS_DENIED 审计），网关据此物理返回
        402/403，**不建立事件流**。
        """
        self._new_turn()
        return self._authorize_chat(message, phase="pre_stream")

    @observe("LiuHaoAssistant.chat")
    def chat(self, message: str) -> Dict[str, Any]:
        """处理一条用户消息，返回结构化结果（回复 + 状态 + 审计）。

        链路：授权 → 生成 → 记忆 → 审计。任一步的失败都诚实登记。
        """
        correlation_id = self._new_turn()

        # 1. 授权（policy kernel，default-deny）。
        denial = self._authorize_chat(message, phase="non_stream")
        if denial is not None:
            return denial

        # 2-3. 构建 messages（system + 历史 + 当前）+ 生成（真实 LLM / mock）。
        #    构建也放进 try：历史裁剪超出模型窗口时抛 ``ContextBudgetExceeded``
        #    （带 ``upstream_kind=context_length_exceeded``），要按统一的失败文案
        #    与 ``reason`` 收口，而不是冒成 500。
        _gen_started = time.perf_counter()
        reason: Optional[str] = None
        technical_detail: Optional[str] = None
        try:
            messages = self._build_messages(message)
            reply = self._generate_with_tools(messages)
            status = "completed"
            error_text: Optional[str] = None
        except ProviderTruncatedError as exc:
            # 非流式：内容真实但没说完。保留已产出的正文，状态必须是
            # truncated（不是 completed），并附上中文提示。
            _log_llm_call_failure(
                self._log,
                exc,
                self.provider,
                correlation_id=correlation_id,
                duration_ms=(time.perf_counter() - _gen_started) * 1000.0,
            )
            reply = exc.partial_text or TRUNCATED_RESPONSE_MESSAGE
            status = "truncated"
            error_text = TRUNCATED_RESPONSE_MESSAGE
            reason = "truncated"
            technical_detail = technical_detail_for(exc)
        except Exception as exc:  # 真实失败，诚实返回，不伪造
            # 原始异常只进日志/诊断；用户侧与状态字段只保留中性中文说明。
            _log_llm_call_failure(
                self._log,
                exc,
                self.provider,
                correlation_id=correlation_id,
                duration_ms=(time.perf_counter() - _gen_started) * 1000.0,
            )
            # 错误文案按失败类型分开：空回复不是网络问题，断流不是彻底失败，
            # 超时更不该让用户去查 API Key。
            failure_text = user_facing_failure(
                exc, model=getattr(self.provider, "model", None)
            )
            reply = failure_text
            status = "error"
            error_text = failure_text
            reason = _turn_failure_reason(exc)
            technical_detail = technical_detail_for(exc)

        self.last_turn_status = status
        self.last_turn_error = error_text
        self.last_turn_reason = reason
        self.last_turn_technical_detail = technical_detail

        # 4-5. 记忆 + 会话落盘 + 审计（chat / chat_stream 共用）。
        self._commit_turn(message, reply, correlation_id, status)

        return {
            "reply": reply,
            "status": status,
            "turn": self.turn,
            "correlation_id": correlation_id,
            "agent": self.principal,
        }

    def chat_stream(
        self,
        message: str,
        *,
        turn_prepared: bool = False,
        cancel_token: Optional[TurnCancelToken] = None,
    ):
        """流式处理一条消息：逐 token yield，收尾时落盘记忆 + 审计。

        与 ``chat`` 走同一条授权链路（policy → 生成 → 记忆 → 审计），生成阶段
        改为逐 token 产出。生成器既 yield ``str``（逐 token），也 yield ``dict``
        （工具调用事件，``{"type": "tool", "name": ..., "args": ..., "output": ...}``），
        供 SSE / 前端渲染工具卡片。生成器是惰性的：SSE / CLI 消费时才真正执行。

        ``turn_prepared=True``：网关已经在建流前跑过 ``preflight_chat``（轮次与
        correlation id 已就绪）。这里仍会**再授权一次**，但只作为兜底 —— 等会话
        锁期间额度可能被别的会话花光。兜底被拒时只能走 ``done`` 帧（SSE 已建立）。

        ``cancel_token``：客户端断开时由网关置位。生成器在每个 token / 每个工具轮
        之间检查它，命中后按「用户中断」收口（不记失败、不写记忆、不记成功），
        让``serialized_turn`` 的锁尽快归还给下一条请求。
        """

        def _cancelled() -> bool:
            return cancel_token is not None and cancel_token.cancelled

        if not turn_prepared:
            # 没有前置闸门（CLI / 测试直接消费生成器）：沿用原有的流中判定。
            self._new_turn()
        self._log.debug("ENTER LiuHaoAssistant.chat_stream turn=%s", self.turn)

        # 1. 授权（policy kernel，default-deny）。兜底路径与前置闸门同源。
        denial = self._authorize_chat(
            message, phase="during_stream" if turn_prepared else "stream_direct"
        )
        if denial is not None:
            yield denial["reply"]
            return

        # 本轮 turn / correlation 可能由 preflight_chat 提前开好。
        correlation_id = self.last_turn_correlation_id

        # 2. 构建 messages（system + 历史 + 当前）。
        messages = self._build_messages(message)

        # 3. 流式生成 + 工具调用 loop。每轮逐 token yield；命中工具时 yield
        #    结构化工具事件（前端据此渲染工具卡片）；最后一轮强制为纯文本
        #    回复（不再检测工具，防 LLM 死循环）。
        final_reply = ""
        status = "completed"
        error_text: Optional[str] = None
        reason: Optional[str] = None
        technical_detail: Optional[str] = None
        round_chunks: List[str] = []
        _gen_started = time.perf_counter()
        try:
            for round_idx in range(MAX_TOOL_ROUNDS + 1):
                if _cancelled():
                    status = "interrupted"
                    reason = "client_disconnected"
                    break
                round_chunks = []
                stream_call = getattr(self.provider, "stream_with_retry", None)
                if callable(stream_call):
                    stream = (
                        stream_call(messages, cancel_token=cancel_token)
                        if cancel_token is not None
                        else stream_call(messages)
                    )
                else:
                    stream = self.provider.chat_stream(messages)
                for item in stream:
                    if _cancelled():
                        # 客户端已离开：不再消费 token，也不把半截内容当完整回答。
                        status = "interrupted"
                        reason = "client_disconnected"
                        final_reply = "".join(round_chunks)
                        break
                    if isinstance(item, dict):
                        # provider 的可见反馈（例如"正在重试"）：直接透传给
                        # 前端，不计入回复正文。
                        yield item
                        continue
                    round_chunks.append(item)
                    yield item
                if _cancelled():
                    # provider 可能因取消而"正常返回"（关闭上游流后迭代结束）。
                    # 这同样属于客户端断开，不能把已收到的半截内容判成 completed。
                    status = "interrupted"
                    reason = "client_disconnected"
                    final_reply = "".join(round_chunks)
                    break
                if status == "interrupted":
                    break
                round_reply = "".join(round_chunks)
                final_reply = round_reply

                if round_idx == MAX_TOOL_ROUNDS:
                    break  # 已达工具轮上限，强制收口为纯文本轮。

                tool_call = parse_tool_call(round_reply)
                if tool_call is None:
                    break  # 纯文本回复，结束。
                if _cancelled():
                    # 不再发起有副作用的新工具调用（已跑完的保持原样）。
                    status = "interrupted"
                    reason = "client_disconnected"
                    break
                tool_name, args = tool_call
                tool_output = self._execute_tool(tool_name, args)
                yield {
                    "type": "tool",
                    "name": tool_name,
                    "args": args,
                    "output": tool_output,
                    # 工具 JSON 原文：前端据此从流式内容里精确剥离工具调用片段。
                    "raw": round_reply,
                }
                messages.append({"role": "assistant", "content": round_reply})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"[工具 {tool_name} 结果]\n"
                            f"{_tool_feedback_text(tool_name, tool_output)}"
                        ),
                    }
                )
        except ProviderTruncatedError as exc:
            # 流式：已 yield 的 token 是真内容，必须保留；但不能算 completed，
            # 也不能把提示文案塞进正文（由 done 帧的 truncated/error 表达）。
            status = "truncated"
            _log_llm_call_failure(
                self._log,
                exc,
                self.provider,
                correlation_id=correlation_id,
                duration_ms=(time.perf_counter() - _gen_started) * 1000.0,
            )
            error_text = TRUNCATED_RESPONSE_MESSAGE
            final_reply = "".join(round_chunks) if round_chunks else exc.partial_text
            reason = "truncated"
            technical_detail = technical_detail_for(exc)
        except ProviderStreamIncompleteError as exc:
            if _cancelled():
                # 取消导致的"断流"不是上游故障：不写失败日志、不给错误文案。
                status = "interrupted"
                reason = "client_disconnected"
                final_reply = "".join(round_chunks)
                error_text = None
                technical_detail = None
            else:
                # 已收到的 token 是真内容，必须保留；但这一轮不能算 completed：
                # 上游没有给出结束标记，用户看到的是一段没有结尾的回答。
                status = "incomplete"
                _log_llm_call_failure(
                    self._log,
                    exc,
                    self.provider,
                    correlation_id=correlation_id,
                    duration_ms=(time.perf_counter() - _gen_started) * 1000.0,
                )
                error_text = user_facing_failure(
                    exc, model=getattr(self.provider, "model", None)
                )
                final_reply = "".join(round_chunks) if round_chunks else error_text
                reason = "stream_incomplete"
        except Exception as exc:  # 真实失败，诚实返回，不伪造
            if _cancelled():
                # 关闭上游流通常会让阻塞的读取抛异常；这属于主动取消，不是失败。
                status = "interrupted"
                reason = "client_disconnected"
                final_reply = "".join(round_chunks)
                error_text = None
                technical_detail = None
            else:
                status = "error"
                # 原始异常只写日志；用户侧与事件流都只看到中性中文说明。
                _log_llm_call_failure(
                    self._log,
                    exc,
                    self.provider,
                    correlation_id=correlation_id,
                    duration_ms=(time.perf_counter() - _gen_started) * 1000.0,
                )
                failure_text = user_facing_failure(
                    exc, model=getattr(self.provider, "model", None)
                )
                error_text = failure_text
                final_reply = failure_text
                reason = _turn_failure_reason(exc)
                technical_detail = technical_detail_for(exc)
                yield final_reply

        self.last_turn_status = status
        self.last_turn_error = error_text
        self.last_turn_reason = reason
        self.last_turn_technical_detail = technical_detail
        # 4-5. 记忆 + 会话落盘 + 审计（共用 _commit_turn）。
        self._commit_turn(message, final_reply, correlation_id, status)
        self._log.info("EXIT chat_stream status=%s", status)

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #
    def _generate_with_tools(self, messages: List[Dict[str, str]]) -> str:
        """生成回复，并在 LLM 请求工具时执行真实工具、把结果喂回。

        协议：LLM 需要查询系统信息时输出 ``{"tool": ..., "args": {...}}``，
        主控解析后执行工具、把结果作为下一条 user 消息喂回，直到 LLM 输出
        纯文本回复（或达到 ``MAX_TOOL_ROUNDS`` 上限防死循环）。
        """
        for _ in range(MAX_TOOL_ROUNDS):
            chat_call = getattr(self.provider, "chat_with_retry", None)
            if callable(chat_call):
                # 非流式路径没有 SSE 通道：把"正在重试"经回调写到本轮状态上，
                # 由网关放进响应体的 notice 字段（流式路径由 provider yield）。
                reply = chat_call(messages, on_retry=self._on_retry_notice)
            else:
                reply = self.provider.chat(messages)
            tool_call = parse_tool_call(reply)
            if tool_call is None:
                return reply
            tool_name, args = tool_call
            tool_output = self._execute_tool(tool_name, args)
            # 把工具调用与结果加入上下文，继续生成最终回复。
            messages.append({"role": "assistant", "content": reply})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"[工具 {tool_name} 结果]\n"
                        f"{_tool_feedback_text(tool_name, tool_output)}"
                    ),
                }
            )
        return reply

    def _execute_tool(self, tool_name: str, args: Dict[str, Any]) -> Any:
        """按工具名查找并执行，返回结构化结果（含失败信息，不抛异常）。

        ``output`` 直接返回 dict/list/scalar，让 SSE 与下游可以按 JSON 解析；
        绝不把 ``str(result.output)`` 的 Python repr 抛给调用方。
        """
        for tool in self._tool_list:
            if tool.name == tool_name:
                # 工具表是跨会话共享的：``system_status`` 需要看到"当前这一轮"
                # 的 turn/history，因此把 assistant 放进上下文中，执行完即还原
                # （保留外层上下文，避免嵌套调用时被清空）。
                token = _TURN_CONTEXT.set(self)
                try:
                    result = self.tools.execute(
                        tool.tool_id, args,
                        principal_id=self.audit_principal,
                        correlation_id=self.last_turn_correlation_id or None,
                    )
                finally:
                    _TURN_CONTEXT.reset(token)
                if result.success:
                    return coerce_tool_output(result.output)
                return {
                    "success": False,
                    "status": "failed",
                    "error": str(result.error),
                }
        return {
            "success": False,
            "status": "unknown_tool",
            "error": f"未知工具: {tool_name}",
        }

    def _commit_turn(
        self, message: str, reply: str, correlation_id: str, status: str
    ) -> None:
        """落盘一轮对话：记忆 kernel + 会话存储 + 审计 hash-chain。

        顺带把本轮花费记账进 ``COST`` 配额 —— 否则配额的 ``used`` 永远是 0，
        成本护栏只在配额被人为设紧时才有意义（见
        ``docs/QUOTA-ACCOUNTING-DESIGN.md``）。
        """
        # ``interrupted``（客户端断开）既不是成功也不是失败：不记账、不写历史、
        # 不写记忆，只留一条审计说明"这一轮是用户中断的"。
        # ``truncated`` 是"内容真实但没说完"：照常落盘/记账（token 确实被上游
        # 消耗了，正文也确实产出了），但审计结果单独记为 truncated，不记成功。
        if status in ("completed", "truncated"):
            self._account_turn_spend()
            # 失败轮次不进入历史、不写会话存储、不写记忆：否则用户会在下一轮
            # 上下文里反复看到失败文案，而"这次没拿到回复"并不属于对话内容。
            self.history.append({"role": "user", "content": message})
            self.history.append({"role": "assistant", "content": reply})
            # user/assistant 两行一次事务写入 + 一条审计：此前两次 append 各自
            # commit 并各写一条审计，把一轮里的固定落盘成本翻倍且没有额外语义。
            self._conv_store.append_turn(
                self.principal,
                self.turn,
                message,
                reply,
                owner_principal=self.audit_principal,
            )
            # MEM-005：把滑出 240 条窗口的消息增量折进持久化摘要，避免早期
            # 约束（预算/期限/禁用词）在长会话里彻底消失。
            try:
                self._conv_store.summarize_overflow(
                    self.principal, CONTEXT_SOURCE_MESSAGES
                )
            except Exception as exc:  # noqa: BLE001 - 摘要失败不否决已保存的对话
                self._log.warning(
                    "evt=context.summary_update_failed principal=%s error=%s",
                    self.principal,
                    exc,
                )
            try:
                self.memory.store(
                    f"turn:{self.principal}:{self.turn}",
                    {"user": message, "assistant": reply},
                    tags={"conversation", "assistant:liuhao"},
                )
            except Exception as exc:  # noqa: BLE001
                # 会话正文已经提交成功：memory 只是附属索引，失败不能把整轮
                # 报成失败，否则用户重试会写出重复轮次。这里只留警告，正文仍
                # 可从 conversation_store 读回。
                _log.warning(
                    "memory store failed after conversation committed "
                    "(principal=%s turn=%s): %s",
                    self.principal,
                    self.turn,
                    exc,
                )
        # 事件类型按结果如实选择：
        # - completed → ACCESS_ALLOWED（授权通过且本轮成功）
        # - interrupted → STATE_CHANGE + outcome="interrupted"（用户中断，不是失败）
        # - 否则（生成失败等）→ STATE_CHANGE + outcome="error"
        #
        # 修复（2026-09-11）：此前非 completed 分支错误地记为 POLICY_EVAL，
        # 导致一次「生成失败」被读成「策略评估失败」，并在驾驶舱 breakdown 里
        # 形成虚假的 `policy_eval:error` 计数。生成失败与策略无关，如实记为
        # 状态变更即可。
        if status == "completed":
            event_type = AuditEventType.ACCESS_ALLOWED
            outcome = "allow"
        elif status == "truncated":
            # 不是成功：如实记成"被长度上限截断"，避免驾驶舱把它算成完成。
            event_type = AuditEventType.STATE_CHANGE
            outcome = "truncated"
        elif status == "interrupted":
            event_type = AuditEventType.STATE_CHANGE
            outcome = "interrupted"
        else:
            event_type = AuditEventType.STATE_CHANGE
            outcome = "error"
        # 跨模型 failover 后，真正消耗额度的是"实际应答"的那家：优先取
        # provider 记录的 last_served_owner，而不是链路里的主模型归属。
        key_owner = str(
            getattr(self.provider, "last_served_owner", "")
            or getattr(self.provider, "owner_principal", "")
            or self.audit_principal
        )
        details = {
            "turn": self.turn,
            "action": "chat",
            "chars_in": len(message),
            "chars_out": len(reply),
            # 与账本对齐：账本按 key_owner 记费、按 actor 记用量；
            # 审计在同一 correlation_id 下注明 Key 归属与实际调用账号，
            # 使"根借用子账号 Key"在审计页也能对上账本。
            "key_owner_account_id": key_owner,
            "effective_account_id": self.audit_principal,
        }
        if status == "interrupted":
            details["reason"] = "client_disconnected"
        log_event(
            event_type,
            principal_id=self.audit_principal,
            scope=AuditScope.L1,
            outcome=outcome,
            details=details,
            correlation_id=correlation_id,
        )

    def _account_turn_spend(self) -> None:
        """Charge the completed turn's spend to the ``COST`` quota.

        Best effort by design: accounting must never break or delay a turn that
        already succeeded. A failure is warned about (never swallowed silently)
        and the turn still completes -- the alternative would turn a
        bookkeeping hiccup into a user-visible outage.

        Nothing is recorded when the model is unpriced: there is no honest
        number to charge, and recording 0 would be a lie that leaves the guard
        just as blind as before.
        """
        cost = getattr(self, "_turn_estimated_cost", None)
        if not cost:
            return
        try:
            from ..kernels.resource import ResourceType, get_resource_manager

            manager = get_resource_manager()
            owner = self.audit_principal
            own_quotas = [
                q
                for q in manager.get_all_quotas(owner)
                if q.resource_type == ResourceType.COST
            ]
            if not own_quotas:
                owner = "system"
            if not manager.account_spend(ResourceType.COST, float(cost), owner):
                _log.warning(
                    "quota accounting: no COST quota to charge for principal=%r "
                    "(cost=%.6f); the spend was not recorded",
                    self.audit_principal,
                    cost,
                )
        except Exception:  # pragma: no cover - defensive
            _log.warning(
                "quota accounting failed for principal=%r (cost=%.6f); "
                "the turn still completed",
                getattr(self, "principal", "?"),
                cost,
                exc_info=True,
            )

    def _build_messages(self, message: str) -> List[Dict[str, str]]:
        """拼出给 provider 的 messages：system + 裁剪后的历史 + 当前输入。

        system 段由三部分组成（按优先级叠加）：
        1. ``DEFAULT_SYSTEM_PROMPT``（人格 + 能力边界）
        2. 工具描述段（``build_tool_prompt``）
        3. KAREN 用户画像摘要（如果存在）—— 让 LLM 按真实偏好/背景个性化回复

        历史不再按固定条数截断（固定 20 条与模型真实窗口无关）：改由
        ``context_budget.trim_history`` 按模型上下文窗口算 token 预算，保留
        最近 6 轮、最低 2 轮；system 段与当前这一句永不裁，被裁掉的早期历史
        折成确定性摘要挂回 system 段。装不下时抛 ``ContextBudgetExceeded``
        （``upstream_kind=context_length_exceeded``），调用方按「对话太长」收口。
        """
        from .personal_context import get_personal_context

        system_content = self.system_prompt
        pcm = get_personal_context()
        # 仅在真的有画像时注入：summarize() 对空画像也返回兜底说明串，
        # 直接拼接会给 system prompt 塞入无意义噪声。
        if pcm.has_profile(self.profile_principal):
            summary = pcm.summarize(self.profile_principal)
            system_content += (
                "\n\n## 用户画像（来自 KAREN Personal Intelligence）\n" + summary
            )

        window_tokens, window_source = resolve_context_window(
            # ModelConfig 目前没有 context_window 字段（由模型配置线负责），
            # 先把 None 传进去：解析顺序退化为「内置家族表 → 保守 8192」。
            configured=None,
            provider=str(getattr(self.provider, "name", "") or ""),
            model=str(getattr(self.provider, "model", "") or ""),
        )
        # MEM-005：超出 240 条窗口的早期历史由持久化分层摘要兜底；摘要读取
        # 失败只降级（不阻断本轮对话），但会记 warn 方便定位。
        early_summary = ""
        if self._conv_store is not None:
            try:
                early_summary = self._conv_store.get_summary(self.principal)
            except Exception as exc:  # noqa: BLE001
                self._log.warning(
                    "evt=context.summary_load_failed principal=%s error=%s",
                    self.principal,
                    exc,
                )
        plan = trim_history(
            self.history,
            window_tokens=window_tokens,
            system_text=system_content,
            current_message=message,
            keep_recent_turns=DEFAULT_KEEP_RECENT_TURNS,
            window_source=window_source,
            early_summary=early_summary,
        )
        # 可观测性：裁剪发生时留一条结构化日志，便于用户反馈"它怎么忘了我前面
        # 说的"时回查是不是被裁掉了。只记计数与预算，不打印任何消息内容。
        if plan.dropped_turns:
            self._log.info(
                "evt=context.trim principal=%s kept_turns=%s dropped_turns=%s "
                "dropped_messages=%s estimated_tokens=%s window_tokens=%s source=%s",
                self.principal,
                plan.kept_turns,
                plan.dropped_turns,
                plan.dropped_messages,
                plan.estimated_tokens,
                plan.window_tokens,
                plan.window_source,
            )
        else:
            self._log.debug(
                "evt=context.keep principal=%s kept_turns=%s estimated_tokens=%s "
                "window_tokens=%s source=%s",
                self.principal,
                plan.kept_turns,
                plan.estimated_tokens,
                plan.window_tokens,
                plan.window_source,
            )
        return plan.messages

    def reset(self) -> None:
        """清空会话历史（记忆与身份保留）。"""
        self.history = []
        self.turn = 0
        self._conv_store.clear(self.principal)

    def stats(self) -> Dict[str, Any]:
        """返回当前会话统计（含 provider / 记忆 / 审计）。"""
        return {
            "principal": self.principal,
            "provider": self.provider.__class__.__name__,
            "model": getattr(self.provider, "model", "unknown"),
            "turn": self.turn,
            "history_messages": len(self.history),
        }
