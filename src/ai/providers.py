"""Provider configuration for LiuHao AI OS.

This module provides configuration and instantiation of AI providers.
Supports both MockProvider for development and real providers
(OpenAI, Anthropic, Google, Ollama, Moonshot, DeepSeek) for production use.
Also integrates with external frameworks: AutoGen, AG2, LangGraph.

Usage:
   from ai.providers import get_provider
   provider = get_provider()  # Auto-detects from env config
"""

import os
import re
import json
import logging
import threading
import warnings
from typing import Optional, Dict, Any, Sequence

from .net_guard import assert_safe_runtime_target
from .observability import observe

logger = logging.getLogger("liuhao.ai.providers")


#: 机器信号：判定"额度耗尽"要看上游错误码/关键词，而给用户看的文案必须是中性
#: 中文。两者必须分开算——先按原文分类，再丢弃原文，只把标签带出去。
_UPSTREAM_QUOTA_TOKENS = (
    "insufficient_quota",
    "insufficient balance",
    "insufficient_balance",
    "quota exceeded",
    "exceeded your current quota",
    "billing_hard_limit",
    "out of credits",
    "credit balance",
    "余额不足",
    "配额",
)

_UPSTREAM_REVOKED_TOKENS = (
    "revoked",
    "deactivated",
    "suspended",
    "api key disabled",
    "key disabled",
    "key expired",
    "已吊销",
    "已停用",
    "已被禁用",
)

#: 200 + ``{"error": ...}`` 的厂商（Ollama / Moonshot / DeepSeek 等）不会给
#: HTTP 状态码，只能从错误体里认出"限流 / Key 无效 / 模型不存在"。
_UPSTREAM_RATE_TOKENS = (
    "rate limit",
    "rate_limit",
    "rate_limit_exceeded",
    "too many requests",
    "requests per minute",
    "tokens per minute",
    "并发",
    "限流",
)

_UPSTREAM_INVALID_KEY_TOKENS = (
    "invalid api key",
    "incorrect api key",
    "invalid_api_key",
    "authentication_error",
    "unauthorized",
    "权限不足",
)

_UPSTREAM_MODEL_MISSING_TOKENS = (
    "model not found",
    "model_not_found",
    "does not exist",
    "unknown model",
    "no such model",
    "not_found_error",
)


# 用户可见的中性失败文案：原始异常（URL、堆栈、英文错误）只进日志与诊断，
# 绝不直接展示给用户，也不写进会话历史。
GENERATION_FAILURE_MESSAGE = (
    "这次没有拿到回复，请稍后再试。若持续失败，请到「系统设置 → 模型管理」检查密钥与网络连接。"
)

#: 空回复的专属文案。与通用失败分开：用户需要知道"模型这次什么都没说"，
#: 而不是"网络可能有问题"。
EMPTY_RESPONSE_MESSAGE = "模型没有返回内容，请重试或换一个模型。"

#: 流没有正常结束时保留已收到内容，并明确标注不完整。
INCOMPLETE_STREAM_MESSAGE = "回复可能不完整（连接提前结束），请重试。"

#: 超时与"密钥无效"必须是两句不同的话：把超时写成"请检查 API Key"会让用户
#: 在完全正确的配置上来回找错。本地模型首次加载（冷启动）超过 30 秒很正常。
#:
#: 超时本身还分三种：首字迟迟不来（再等等就好）、已经出字后卡住（重试）、
#: 连不上（去检查服务是否启动）。三句话都要带模型名与可执行动作，用户才
#: 知道下一步做什么。
TIMEOUT_MESSAGE = "模型首次加载较慢，还没有返回内容，请稍候或重试。"
STALLED_RESPONSE_MESSAGE = (
    "模型在生成途中卡住了（超过 {timeout:g} 秒没有新内容），请重试。"
)
CONNECTION_FAILURE_MESSAGE = "连不上模型服务，请确认本地服务已启动或网络可用。"
INVALID_KEY_MESSAGE = (
    "API Key 无效或已失效，请到「系统设置 → 模型管理」更新密钥。"
)

# 「额度/余额不足」「模型不存在」「被限流」各自是一句能行动的话：
# 混进通用失败文案会让用户去改一个完全正确的 Key 或地址。
QUOTA_EXHAUSTED_MESSAGE = "模型账户的额度或余额不足，请到提供方控制台充值后重试。"
MODEL_NOT_FOUND_MESSAGE = "模型不存在或已下线，请核对模型名（可点「测试连接」查看服务端返回）。"
RATE_LIMITED_MESSAGE = "请求被上游限流（短时间内调用过多），请稍后重试。"

#: 判断"连不上"用的异常类名片段（SDK 常把连接失败包成自己的类型）。
_CONNECT_ERROR_NAMES = ("connection", "connect", "network", "unreachable")

#: 生成读超时的类默认值（旧版本是 30 秒，冷启动会被误判超时）。
DEFAULT_GENERATION_TIMEOUT = 120.0
#: 首字等待的默认值：只在用户**没有配置**超时时使用。
DEFAULT_FIRST_TOKEN_TIMEOUT = 180.0

#: 自动重试时给用户看的可见反馈（由流式链路作为 notice 事件下发）。
RETRY_NOTICE_MESSAGE = "生成较慢，正在重试（模型：{model}）…"


def _with_model(message: str, model: Any) -> str:
    """在失败文案里带出实际模型名：用户才知道该换哪个模型。

    只追加一次：消息里已经有 ``（模型：…）`` 时原样返回，避免同一张卡片上
    出现两个模型名（长模型名重复两次会把一句话挤成三行）。
    """
    name = str(model or "").strip()
    if not name or "模型：" in message:
        return message
    return f"{message}（模型：{name}）"


class ProviderStreamIncompleteError(RuntimeError):
    """流式响应没有正常结束（缺 ``[DONE]``/``done``、半截帧、连接中断）。

    已产出的 token 是真实内容，调用方应保留它们，但**不能**把这一轮当成
    ``completed``：否则用户看到的是一段没有结尾的回答，却没有任何提示。
    """

    def __init__(self, label: str, detail: str = "连接提前结束"):
        self.label = label
        self.detail = detail
        self.upstream_kind = "incomplete_stream"
        super().__init__(f"{INCOMPLETE_STREAM_MESSAGE}（{label}：{detail}）")


class TurnCancelled(Exception):
    """本轮生成被主动取消（目前唯一来源：客户端已断开）。

    与真实失败严格区分：它不记 ``llm.call.failed``、不触发重试、不产出错误
    文案，由调用方按「用户中断」收口。
    """

    def __init__(self, reason: str = "client_disconnected"):
        self.reason = reason
        super().__init__("本轮生成已被取消")


class TurnCancelToken:
    """跨线程取消令牌：网关线程置位，生成线程在检查点退出。

    ``cancel()`` 会顺带执行已登记的关闭回调（provider 的流式 ``response``），
    让阻塞在 ``iter_lines()`` 上的生成线程**立即**抛错退出，而不是干等一次
    读取超时。回调失败一律吞掉：取消本身不能被关闭异常掩盖。
    """

    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._closers: list = []
        self.mode = ""

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self, mode: str = "disconnect") -> None:
        with self._lock:
            if self._event.is_set():
                return
            self.mode = mode
            self._event.set()
            closers = list(self._closers)
            self._closers.clear()
        for closer in closers:
            try:
                closer()
            except Exception:  # noqa: BLE001 - 关闭失败不能掩盖取消
                logger.debug("cancelling provider stream failed", exc_info=True)

    def bind_closer(self, closer) -> None:
        """登记"取消时关闭上游流"的回调；已取消则立即执行。"""
        run_now = False
        with self._lock:
            if self._event.is_set():
                run_now = True
            else:
                self._closers.append(closer)
        if run_now:
            try:
                closer()
            except Exception:  # noqa: BLE001
                logger.debug("closing already-cancelled stream failed", exc_info=True)

    def raise_if_cancelled(self) -> None:
        if self._event.is_set():
            raise TurnCancelled(self.mode or "client_disconnected")


class ProviderTimeoutError(TimeoutError):
    """在超时窗口内没有收到任何**有效内容**（心跳/keepalive 不算）。

    ``requests``/``httpx`` 的读超时是"每次 read"的：上游持续发
    ``: keepalive`` 时每次 read 都会成功，超时永远不会触发。这里改用
    "最后一次有效内容距今多久"判定。

    ``first_token_timeout`` 与 ``idle_timeout`` 是两个不同的阈值：
    首字之前允许更长等待（本地模型冷启动可能要 1 分钟），首字之后按
    正常节奏判定"卡住"。
    """

    def __init__(
        self,
        label: str,
        timeout: float,
        *,
        before_first_token: bool = False,
        model: Optional[str] = None,
    ):
        self.label = label
        self.timeout = timeout
        self.before_first_token = before_first_token
        self.model = model
        phase = "还没有返回第一个字" if before_first_token else "没有继续返回内容"
        suffix = f"（模型：{model}）" if model else ""
        super().__init__(
            f"{label}在 {timeout:g} 秒内{phase}，可能是首次加载较慢或网络中断{suffix}。"
        )


def _server_detail_text(exc: BaseException) -> str:
    """给用户看的"服务端返回了什么"（已脱敏、已截断）。"""
    detail = getattr(exc, "upstream_detail", None)
    if not isinstance(detail, str):
        return ""
    detail = detail.strip()
    if not detail:
        return ""
    return detail


def technical_detail_for(exc: BaseException) -> str:
    """失败轮次的「技术详情」：只取上游 ``message``（已脱敏、≤120 字）。

    主文案必须是可行动的中文；上游原文只允许出现在界面上**可展开**的
    「技术详情」里。只保留 ``error.message``——``type``、完整响应体、
    请求头一律不进，避免把英文错误码与内部信息当正文展示。
    """
    return sanitize_upstream_detail(_server_detail_text(exc))


def user_facing_failure(exc: BaseException, model: Optional[str] = None) -> str:
    """把 provider 异常翻成用户能行动的中文结论。

    通用文案对空回复/断流/超时都是误导：空回复不是网络问题，断流不是彻底
    失败，超时更不该让用户去查 API Key（配置完全正确也会超时）。
    """
    from error_types import ProviderEmptyResponseError

    # 多模型回落失败：消息里已经逐条列了模型名与真实原因，直接展示，
    # 不能再套一层"这次没有拿到回复"把清单盖掉。
    if isinstance(exc, ProviderChainError):
        return str(exc)
    if isinstance(exc, ProviderEmptyResponseError):
        return _with_model(EMPTY_RESPONSE_MESSAGE, model)
    if isinstance(exc, ProviderStreamIncompleteError):
        return _with_model(INCOMPLETE_STREAM_MESSAGE, model)
    if isinstance(exc, ProviderTimeoutError):
        # 首字慢与"出字后卡住"两种超时给不同建议：前者再等等，后者重试。
        actual = model or exc.model
        if exc.before_first_token:
            return _with_model(TIMEOUT_MESSAGE, actual)
        return _with_model(
            STALLED_RESPONSE_MESSAGE.format(timeout=exc.timeout), actual
        )
    if isinstance(exc, TimeoutError):
        return _with_model(TIMEOUT_MESSAGE, model)
    name = type(exc).__name__.lower()
    is_connect = (
        isinstance(exc, (ConnectionError, OSError))
        or any(token in name for token in _CONNECT_ERROR_NAMES)
    )
    if is_connect:
        return _with_model(CONNECTION_FAILURE_MESSAGE, model)
    # SDK 会把"请求阶段超时"包成 APITimeoutError（既不是内建 TimeoutError，
    # 也不是 ProviderTimeoutError）。漏掉它就会掉进通用文案，重新引导用户
    # 去查 Key —— 正是 P0 要消除的误导。
    if "timeout" in name:
        return _with_model(TIMEOUT_MESSAGE, model)
    kind = getattr(exc, "upstream_kind", None)
    status = getattr(exc, "status_code", None)
    if kind == "quota_exhausted" or status == 402:
        return _with_model(QUOTA_EXHAUSTED_MESSAGE, model)
    if kind in ("invalid_key", "key_revoked") or status in (401, 403):
        return _with_model(INVALID_KEY_MESSAGE, model)
    if kind == "model_not_found" or status == 404:
        # 名字写错时上游原话有用，但只能进「技术详情」（见 technical_detail_for）。
        return _with_model(MODEL_NOT_FOUND_MESSAGE, model)
    if kind == "rate_limited" or status == 429:
        return _with_model(RATE_LIMITED_MESSAGE, model)
    if kind == "unreachable":
        return _with_model(CONNECTION_FAILURE_MESSAGE, model)
    if kind == "upstream_error":
        return _with_model(GENERATION_FAILURE_MESSAGE, model)
    return _with_model(GENERATION_FAILURE_MESSAGE, model)


def _is_timeout_or_connection(exc: BaseException) -> bool:
    """首次调用的自动重试只针对"再来一次可能就好"的两类失败。

    超时（模型还在加载）与连接中断（keep-alive 被回收）重试一次有意义；
    401/400 重试只会让用户多等几秒再看到同一句错话。
    """
    if isinstance(exc, ProviderTimeoutError):
        return True
    if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
        return True
    name = type(exc).__name__.lower()
    return "timeout" in name or "connection" in name


class ProviderHTTPError(RuntimeError):
    """上游 HTTP 失败：消息中性、可展示；机器信号单独保留给分类器。

    上游响应体可能包含内部地址、账号信息或英文堆栈，只写服务端日志，
    绝不拼进异常消息（异常消息会进入诊断与部分用户可见路径）。

    ``upstream_kind`` 是**脱敏之前**按状态码 + 上游错误码/关键词算好的分类
    （``quota_exhausted`` / ``key_revoked`` / ``invalid_key`` / ``rate_limited``
    / ``model_not_found`` / ``upstream_error``）。消费方必须优先读它：
    中性文案里已经没有 ``insufficient_quota``、``revoked`` 这些词，
    只看文案会把"去充值"错报成"请稍后重试"。
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        upstream_kind: Optional[str] = None,
        upstream_detail: Optional[str] = None,
        upstream_code: Optional[str] = None,
        upstream_type: Optional[str] = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        #: 服务端错误体里的 ``message``（已脱敏、已截断，≤120 字符）。
        #: 只用于界面「技术详情」；主文案不得拼接它。
        self.upstream_detail = upstream_detail or ""
        self.upstream_kind = upstream_kind
        #: 上游错误体里的 ``code`` / ``type``，**原样**保留（只能是标识符，绝不含正文）。
        #: 日志据此认出 ``insufficient_quota`` / ``model_not_found`` 这类未来新增错误码。
        self.upstream_code = upstream_code or ""
        self.upstream_type = upstream_type or ""


def _error_code_type(raw: Any) -> tuple[str, str]:
    """从上游错误体取 ``(code, type)``：只接受短标识符，原样保留、不做翻译。

    错误体可能是已解析的 dict（OpenAI SDK 的 ``exc.body``）或原始 JSON 文本。
    只取 ``error.code`` / ``error.type``（没有 ``error`` 时取顶层同名字段），
    并压缩成单个 token——错误码是给机器认的，不需要也不允许带上正文。
    """
    data: Any = raw
    if isinstance(data, (str, bytes)):
        try:
            data = json.loads(data)
        except (ValueError, TypeError):
            return "", ""
    if not isinstance(data, dict):
        return "", ""
    source = data.get("error")
    if not isinstance(source, dict):
        source = data
    code = source.get("code")
    error_type = source.get("type")
    code_text = str(code).split()[0][:64] if isinstance(code, (str, int)) and str(code).strip() else ""
    type_text = str(error_type).split()[0][:64] if isinstance(error_type, str) and error_type.strip() else ""
    return code_text, type_text


def _neutral_http_message(label: str, status: Optional[int]) -> str:
    if status is not None and 300 <= status < 400:
        return f"{label}返回重定向，已按安全策略拒绝跟随。"
    if status is not None:
        return f"{label}返回 HTTP {status}，详情已记录到服务端日志。"
    return f"{label}请求失败，详情已记录到服务端日志。"


def _upstream_error_text(exc: BaseException) -> str:
    """取上游错误原文，**只用于分类**，绝不进入异常消息或日志正文。"""
    parts: list[str] = []
    body = getattr(exc, "body", None)
    if isinstance(body, (dict, list, str)):
        parts.append(str(body))
    response = getattr(exc, "response", None)
    text = getattr(response, "text", None)
    if isinstance(text, str):
        parts.append(text)
    message = getattr(exc, "message", None)
    if isinstance(message, str):
        parts.append(message)
    parts.append(str(exc))
    return " ".join(parts).lower()


def classify_upstream_error(
    exc: Optional[BaseException] = None,
    *,
    status: Optional[int] = None,
    text: Optional[str] = None,
) -> str:
    """在脱敏前给上游 HTTP 失败打机器可读标签。

    ``quota_exhausted`` 与 ``rate_limited`` 在多数厂商都返回 429，
    ``key_revoked`` 与 ``invalid_key`` 都返回 401/403 —— 只能读原文区分。
    """
    code = status
    if not isinstance(code, int) and exc is not None:
        raw = getattr(exc, "status_code", None)
        if not isinstance(raw, int):
            raw = getattr(exc, "status", None)
        if not isinstance(raw, int):
            # google-genai 的 APIError 只暴露 ``code``（HTTP 状态码）。
            raw = getattr(exc, "code", None)
        code = raw if isinstance(raw, int) else None
    # 已经被脱敏过的异常自带机器标签：绝不能拿中性文案再判一次
    # （文案里的 "HTTP 429" 会把 quota_exhausted 退化成 rate_limited）。
    existing = getattr(exc, "upstream_kind", None) if exc is not None else None
    if isinstance(existing, str) and existing:
        return existing
    body = (text or "")
    if exc is not None:
        body = f"{body} {_upstream_error_text(exc)}"
    body = body.lower()
    if any(token in body for token in _UPSTREAM_QUOTA_TOKENS):
        return "quota_exhausted"
    if code == 402:
        return "quota_exhausted"
    if code in (401, 403):
        if any(token in body for token in _UPSTREAM_REVOKED_TOKENS):
            return "key_revoked"
        return "invalid_key"
    if code == 404:
        return "model_not_found"
    if code == 429:
        return "rate_limited"
    # 没有 HTTP 状态码的"假 200"：Ollama / Moonshot / DeepSeek 会把错误写进
    # 200 响应体。只能按错误体关键词判类，否则额度耗尽会退化成通用失败。
    if code in (None, 200):
        if any(token in body for token in _UPSTREAM_RATE_TOKENS):
            return "rate_limited"
        if any(token in body for token in _UPSTREAM_REVOKED_TOKENS):
            return "key_revoked"
        if any(token in body for token in _UPSTREAM_INVALID_KEY_TOKENS):
            return "invalid_key"
        if any(token in body for token in _UPSTREAM_MODEL_MISSING_TOKENS):
            return "model_not_found"
    return "upstream_error"


_DETAIL_LIMIT = 120
_DETAIL_KEY_PATTERN = re.compile(r"(sk-|Bearer\s+)[A-Za-z0-9_\-\.]{6,}", re.IGNORECASE)


def sanitize_upstream_detail(raw: Any) -> str:
    """把上游错误体压成一句可展示的"服务端原话"。

    用户排查"模型名写错"时必须看到上游到底说了什么；但完整响应体可能含
    密钥、内部地址或整段堆栈，因此这里：优先取 JSON 的 ``error.message`` /
    ``message`` 字段 → 压空白 → 抹掉疑似密钥 → 截断 120 字符。
    """
    if not isinstance(raw, str):
        raw = str(raw or "")
    text = raw.strip()
    if not text:
        return ""
    parsed: Any = None
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        parsed = None
    if isinstance(parsed, dict):
        error = parsed.get("error")
        candidate = None
        if isinstance(error, dict):
            candidate = error.get("message") or error.get("detail")
        elif isinstance(error, str):
            candidate = error
        candidate = candidate or parsed.get("message") or parsed.get("detail")
        if isinstance(candidate, str) and candidate.strip():
            text = candidate
    text = " ".join(text.split())
    text = _DETAIL_KEY_PATTERN.sub(lambda m: f"{m.group(1)}***", text)
    if len(text) > _DETAIL_LIMIT:
        text = text[: _DETAIL_LIMIT - 1].rstrip() + "…"
    return text


def _raise_upstream_http(label: str, response: Any) -> None:
    """上游非 200 统一成中性异常，并保留脱敏前的机器分类。

    直接把 ``response.text`` 拼进异常会把上游英文原文（含内部地址/账号信息）
    带到用户侧；分类必须在丢弃原文之前完成。
    """
    status = getattr(response, "status_code", 0)
    raw_text = getattr(response, "text", "")
    kind = classify_upstream_error(status=status, text=raw_text)
    code, error_type = _error_code_type(raw_text)
    logger.info("%s upstream HTTP failure status=%s kind=%s", label, status, kind)
    raise ProviderHTTPError(
        _neutral_http_message(label, status),
        status_code=status,
        upstream_kind=kind,
        upstream_detail=sanitize_upstream_detail(raw_text),
        upstream_code=code,
        upstream_type=error_type,
    )


def _neutralize_http_error(exc: BaseException, label: str = "模型服务") -> None:
    """把 SDK 的 HTTP 错误消息换成中性文案；非 HTTP 错误原样返回给调用方。

    分类在脱敏之前完成：上游原文可能含密钥/内网地址，所以只保留
    ``upstream_kind`` 与状态码，其余丢弃（不进异常消息、不进日志正文）。
    """
    status = getattr(exc, "status_code", None)
    if not isinstance(status, int):
        status = getattr(exc, "status", None)
    if not isinstance(status, int):
        # google-genai 的 APIError 只暴露 ``code``（HTTP 状态码）。
        status = getattr(exc, "code", None)
    if not isinstance(status, int):
        return
    kind = classify_upstream_error(exc, status=status)
    detail = ""
    body = getattr(exc, "body", None)
    if isinstance(body, (dict, list)):
        detail = sanitize_upstream_detail(json.dumps(body, ensure_ascii=False))
    elif isinstance(body, str):
        detail = sanitize_upstream_detail(body)
    if not detail:
        response = getattr(exc, "response", None)
        text = getattr(response, "text", None)
        if isinstance(text, str):
            detail = sanitize_upstream_detail(text)
    if not detail:
        detail = sanitize_upstream_detail(getattr(exc, "message", "") or str(exc))
    code, error_type = _error_code_type(body)
    if not code and not error_type:
        response = getattr(exc, "response", None)
        code, error_type = _error_code_type(getattr(response, "text", None))
    logger.info("%s upstream HTTP failure status=%s kind=%s", label, status, kind)
    raise ProviderHTTPError(
        _neutral_http_message(label, status),
        status_code=status,
        upstream_kind=kind,
        upstream_detail=detail,
        upstream_code=code,
        upstream_type=error_type,
    ) from exc


def _is_retryable_failure(exc: BaseException) -> bool:
    """这个失败重试一次有没有意义？

    旧实现把 401/400/402 也重试 4 次：既放大计费，也把"密钥错了"拖成
    "等了几秒还是不行"。额度耗尽的 429 同样不该重试——再打几次不会让
    余额变多，只会让上游多记几次失败。
    """
    from error_types import ProviderEmptyResponseError

    # 多模型回落失败：每个候选都已经试过一次，整体再重试只会让用户多等。
    if isinstance(exc, ProviderChainError):
        return False
    if isinstance(exc, ProviderEmptyResponseError):
        return True
    status = getattr(exc, "status_code", None)
    kind = getattr(exc, "upstream_kind", None)
    if isinstance(status, int):
        if status == 429:
            return kind != "quota_exhausted"
        if 300 <= status < 500:
            return False
        if status >= 500:
            return True
        # 2xx 但响应体里是错误对象：按 kind 判断。
    if isinstance(kind, str) and kind:
        return kind == "rate_limited"
    if isinstance(exc, (AttributeError, TypeError, IndexError, KeyError)):
        # 解析代码的编程错误：重试 4 次不会让字段长出来，只会掩盖根因。
        return False
    if isinstance(exc, ProviderStreamIncompleteError):
        return True
    return True


# ─── 响应解析共用工具 ───────────────────────────────────────
#
# OpenAI 的 ``message.content`` 在官方 SDK 类型里是 ``str | None``，但
# OpenAI 兼容服务（以及部分新版 API）会返回 **content parts 列表**：
# ``[{"type": "text", "text": "..."}]``。此前 ``return content or ""`` 直接
# 把 list 当字符串返回，``chat`` 后续处理抛 ``AttributeError``，而
# ``generate_with_retry`` 只判"非空"就当成功——同一份响应两条链路结论相反。
# 这里把"取文本"统一成一处，所有 provider/流式/非流式共用。

def _coerce_content_text(content: Any) -> str:
    """把 ``str | None | list[part]`` 统一成纯文本，绝不返回非字符串。

    * ``str`` 原样返回；
    * ``None`` → ``""``；
    * list → 只拼接 text part（``{"type": "text", "text": ...}`` /
      ``{"text": ...}`` / SDK 对象 ``.text``），跳过 image/tool/thinking；
    * 其他类型保守转成字符串（避免把整个 payload 当回复上屏的旧行为）。
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, (list, tuple)):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
                continue
            if isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str):
                    parts.append(text)
                continue
            text = getattr(part, "text", None)
            if isinstance(text, str):
                parts.append(text)
        return "".join(parts)
    return str(content)


def _extract_openai_message_text(message: Any) -> str:
    """取 OpenAI ``message`` 的文本（``content`` 可能是 list）。"""
    if message is None:
        return ""
    if isinstance(message, dict):
        return _coerce_content_text(message.get("content"))
    return _coerce_content_text(getattr(message, "content", None))


def _extract_openai_payload_text(payload: Any) -> str:
    """取 OpenAI 非流式 payload 的文本；空 ``choices``/缺 ``message`` → ``""``。"""
    if not isinstance(payload, dict):
        return ""
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    return _extract_openai_message_text(first.get("message"))


def _embedded_error(payload: Any) -> Any:
    """取出 200 响应体里夹带的 ``error`` 对象（没有则返回 ``None``）。"""
    if isinstance(payload, dict):
        return payload.get("error")
    if payload is None:
        return None
    extra = getattr(payload, "model_extra", None)
    if isinstance(extra, dict):
        return extra.get("error")
    return None


def _raise_embedded_upstream_error(
    label: str, error_payload: Any, *, status: Optional[int] = None
) -> None:
    """200 + ``{"error": ...}``：按错误体分类，**不把原始 JSON 上屏**。

    Ollama / Moonshot / DeepSeek 都会用 200 包错误；不识别就会把
    ``{"error": "..."}`` 直接当回复展示，或者（OpenAI SDK）在
    ``choices[0]`` 上抛 ``AttributeError`` 后重试 4 次。
    """
    try:
        error_text = json.dumps(error_payload, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        error_text = str(error_payload)
    kind = classify_upstream_error(status=status, text=error_text)
    logger.info(
        "%s upstream body-level error http_status=%s kind=%s",
        label,
        status if status is not None else "-",
        kind,
    )
    raise ProviderHTTPError(
        f"{label}返回错误，详情已记录到服务端日志。",
        status_code=status if isinstance(status, int) else None,
        upstream_kind=kind,
        upstream_detail=sanitize_upstream_detail(error_text),
    )


def _raise_invalid_response(label: str, exc: Optional[BaseException] = None) -> None:
    """上游返回了无法解析的响应（HTML / 空 body / 非 JSON）。"""
    logger.info(
        "%s returned an unparseable response: %s",
        label,
        type(exc).__name__ if exc is not None else "-",
    )
    raise ProviderHTTPError(
        f"{label}返回了无法解析的响应，请稍后重试。",
        upstream_kind="upstream_error",
    ) from exc


def _require_nonempty_text(label: str, text: str, detail: str = "模型返回为空") -> str:
    """``generate_with_retry`` 之外的直连路径统一判空。

    ``chat`` / ``chat_stream`` 不经过 ``generate_with_retry``，此前空回复直接
    返回 ``""`` 并被上层记成 ``completed``。空内容不是成功，这里显式失败。
    """
    from error_types import ProviderEmptyResponseError

    if text is None or not str(text).strip():
        raise ProviderEmptyResponseError(label, detail=detail)
    return text


def _usage_counts(usage: Any) -> tuple[int, int]:
    """把 SDK/dict 两种 usage 统一成 ``(prompt_tokens, completion_tokens)``。"""
    if usage is None:
        return 0, 0
    if isinstance(usage, dict):
        return (
            int(usage.get("prompt_tokens") or 0),
            int(usage.get("completion_tokens") or 0),
        )
    return (
        int(getattr(usage, "prompt_tokens", 0) or 0),
        int(getattr(usage, "completion_tokens", 0) or 0),
    )


def _iter_raw_lines(response):
    """按行读取流式响应；尽量 1 字节粒度，避免心跳被整块缓冲。

    ``requests.iter_lines(chunk_size=512)`` 的 ``read(512)`` 会等满 512 字节
    或连接关闭才返回——持续发 ``\\n`` 心跳时，6 秒内一个行都到不了，
    "空闲检测"形同不存在。``chunk_size=1`` 让每个字节立刻可见。

    测试里的 fake response 只提供无参 ``iter_lines``，退回默认值即可。
    """
    try:
        return response.iter_lines(chunk_size=1)
    except TypeError:
        return response.iter_lines()


def _iter_with_cancel(iterable, cancel_token: Optional[TurnCancelToken]):
    """在两个 chunk 之间检查取消令牌；阻塞中的读取由关闭回调打断。"""
    for item in iterable:
        if cancel_token is not None:
            cancel_token.raise_if_cancelled()
        yield item


def _iter_sse_text(
    lines,
    *,
    label: str,
    idle_timeout: float,
    first_token_timeout: Optional[float] = None,
    on_usage=None,
):
    """把 OpenAI 兼容 SSE 原始行解析成文本 token。

    与旧实现的区别（每一条都对应一个真实故障）：

    * 原始行而不是 SDK 解析后的 chunk：只有这样才能看到 ``[DONE]``、
      ``: keepalive`` 和半截帧；
    * 缺 ``[DONE]``（且没有 ``finish_reason``）→
      :class:`ProviderStreamIncompleteError`，不再把半截内容当完整回复；
    * 中间坏帧 → 同上，不再 ``continue`` 静默跳过；
    * ``: keepalive`` / ``event: ping`` 不产生 data，既不算内容也不重置
      "最后一次有效内容"时间；
    * 持续 keepalive 超过 ``idle_timeout`` → :class:`ProviderTimeoutError`；
    * ``delta.content`` 为 list（content parts）→ 统一取 text part。
    """
    import time

    idle = max(0.5, float(idle_timeout or 0.0))
    first_wait = max(idle, float(first_token_timeout or idle))
    last_valid = time.monotonic()
    usage = None
    saw_done = False
    saw_finish = False
    content_chars = 0
    non_sse: list[str] = []

    def _idle_check() -> None:
        # 首字之前用 first_wait（本地模型冷启动可达 1 分钟），首字之后用 idle：
        # 两个阈值分开，才不会把"加载慢"和"生成卡住"混成同一个超时。
        before_first = content_chars == 0 and not saw_finish
        limit = first_wait if before_first else idle
        if time.monotonic() - last_valid > limit:
            raise ProviderTimeoutError(
                label, limit, before_first_token=before_first
            )

    try:
        for raw in lines:
            _idle_check()
            text = raw.decode("utf-8", "ignore") if isinstance(raw, bytes) else str(raw)
            text = text.strip()
            if not text:
                continue
            # SSE 注释 / 事件名：都不是内容，不能重置空闲计时。
            if text.startswith(":") or text.startswith("event:"):
                continue
            if not text.startswith("data:"):
                non_sse.append(text)
                continue
            payload = text[5:].strip()
            if payload == "[DONE]":
                saw_done = True
                break
            if not payload:
                continue
            try:
                chunk = json.loads(payload)
            except json.JSONDecodeError as exc:
                raise ProviderStreamIncompleteError(
                    label, "流中出现无法解析的数据帧"
                ) from exc
            if not isinstance(chunk, dict):
                continue
            error_payload = chunk.get("error")
            if error_payload:
                _raise_embedded_upstream_error(label, error_payload, status=200)
            chunk_usage = chunk.get("usage")
            if chunk_usage:
                usage = chunk_usage
            for choice in chunk.get("choices") or []:
                if not isinstance(choice, dict):
                    continue
                if choice.get("finish_reason"):
                    saw_finish = True
                delta = choice.get("delta") or {}
                if not isinstance(delta, dict):
                    continue
                token = _coerce_content_text(delta.get("content"))
                if token:
                    content_chars += len(token)
                    last_valid = time.monotonic()
                    yield token
    except (ProviderHTTPError, ProviderStreamIncompleteError, ProviderTimeoutError):
        raise
    except Exception as exc:
        name = type(exc).__name__.lower()
        if isinstance(exc, TimeoutError) or "timeout" in name:
            raise ProviderTimeoutError(
                label, idle, before_first_token=content_chars == 0
            ) from exc
        # 连接被上游截断 / 解码失败：已产出的内容保留，但这一轮不能算完成。
        raise ProviderStreamIncompleteError(
            label, f"{type(exc).__name__}"
        ) from exc
    finally:
        if usage is not None and on_usage is not None:
            on_usage(usage)

    # 非 SSE 响应体（HTML / 纯 JSON / 网关忽略 stream 参数）。
    if non_sse:
        blob = "\n".join(non_sse).strip()
        start = blob.find("{")
        parsed = None
        if start >= 0:
            try:
                parsed = json.loads(blob[start:])
            except json.JSONDecodeError:
                parsed = None
        if isinstance(parsed, dict):
            error_payload = parsed.get("error")
            if error_payload:
                _raise_embedded_upstream_error(label, error_payload, status=200)
            token = _extract_openai_payload_text(parsed)
            if token:
                content_chars += len(token)
                yield token
        else:
            lowered = blob.lower()
            if "<html" in lowered or "bad gateway" in lowered:
                logger.info("%s returned HTML instead of SSE", label)
            _raise_invalid_response(label)

    if content_chars == 0:
        from error_types import ProviderEmptyResponseError

        raise ProviderEmptyResponseError(label, detail="流式响应没有产生任何文本")
    if not saw_done and not saw_finish:
        raise ProviderStreamIncompleteError(label, "流在没有结束标记的情况下关闭")


def _iter_ndjson_text(
    lines,
    *,
    label: str,
    idle_timeout: float,
    first_token_timeout: Optional[float] = None,
    on_usage=None,
):
    """Ollama ``/api/chat`` 的 NDJSON 流解析。

    与 SSE 版本同一条原则：``done: true`` 才代表正常结束；解析失败的帧
    直接判"不完整"，不再 ``continue`` 后把半截内容当完整回复。
    """
    import time

    idle = max(0.5, float(idle_timeout or 0.0))
    first_wait = max(idle, float(first_token_timeout or idle))
    last_valid = time.monotonic()
    saw_done = False
    content_chars = 0
    usage: Optional[dict] = None
    non_json: list[str] = []

    def _idle_check() -> None:
        before_first = content_chars == 0
        limit = first_wait if before_first else idle
        if time.monotonic() - last_valid > limit:
            raise ProviderTimeoutError(
                label, limit, before_first_token=before_first
            )

    try:
        for raw in lines:
            _idle_check()
            text = raw.decode("utf-8", "ignore") if isinstance(raw, bytes) else str(raw)
            text = text.strip()
            if not text:
                continue
            if text.startswith(":"):
                continue
            try:
                chunk = json.loads(text)
            except json.JSONDecodeError as exc:
                if not text.startswith("{"):
                    non_json.append(text)
                    continue
                raise ProviderStreamIncompleteError(
                    label, "流中出现无法解析的数据帧"
                ) from exc
            if not isinstance(chunk, dict):
                continue
            error_payload = chunk.get("error")
            if error_payload:
                _raise_embedded_upstream_error(label, error_payload, status=200)
            message = chunk.get("message")
            token = _coerce_content_text(
                message.get("content") if isinstance(message, dict) else None
            )
            if token:
                content_chars += len(token)
                last_valid = time.monotonic()
                yield token
            if chunk.get("done"):
                saw_done = True
                usage = {
                    "prompt_tokens": chunk.get("prompt_eval_count") or 0,
                    "completion_tokens": chunk.get("eval_count") or 0,
                }
                break
    except (ProviderHTTPError, ProviderStreamIncompleteError, ProviderTimeoutError):
        raise
    except Exception as exc:
        name = type(exc).__name__.lower()
        if isinstance(exc, TimeoutError) or "timeout" in name:
            raise ProviderTimeoutError(
                label, idle, before_first_token=content_chars == 0
            ) from exc
        raise ProviderStreamIncompleteError(
            label, f"{type(exc).__name__}"
        ) from exc
    finally:
        if usage is not None and on_usage is not None:
            on_usage(usage)

    if non_json:
        blob = "\n".join(non_json).strip()
        if "<html" in blob.lower() or "bad gateway" in blob.lower():
            logger.info("%s returned HTML instead of NDJSON", label)
        _raise_invalid_response(label)
    if content_chars == 0:
        from error_types import ProviderEmptyResponseError

        raise ProviderEmptyResponseError(label, detail="流式响应没有产生任何文本")
    if not saw_done:
        raise ProviderStreamIncompleteError(label, "流在没有 done 标记的情况下关闭")


# 未配置模型时的演示数据提示：明确告诉用户发生了什么、下一步怎么做，
# 不再输出 provider 内部状态转储。
MODEL_NOT_CONFIGURED_MESSAGE = (
    "[演示数据] 还没有配置模型，因此无法生成真实回答。"
    "请到「系统设置 → 模型管理」填写密钥并选择模型服务，然后重新发送这条消息。"
)

# Provider type enumeration


class ProviderType:
    MOCK = "mock"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GOOGLE = "google"
    OLLAMA = "ollama"
    MOONSHOT = "moonshot"
    DEEPSEEK = "deepseek"

# External framework integration flags


class Framework:
    AUTOgen = "autogen"
    AG2 = "ag2"
    LANGGRAPH = "langgraph"

# Provider capability metadata


class ProviderCapabilities:
    """Standardized capability metadata for providers"""

    def __init__(self, **kwargs):
        self.supports_streaming = kwargs.get("supports_streaming", False)
        self.supports_structured_output = kwargs.get("supports_structured_output", False)
        self.supports_function_calling = kwargs.get("supports_function_calling", False)
        self.supports_vision = kwargs.get("supports_vision", False)
        self.supports_embeddings = kwargs.get("supports_embeddings", False)
        self.max_context_tokens = kwargs.get("max_context_tokens", 4096)
        self.max_output_tokens = kwargs.get("max_output_tokens", 1024)
        self.rate_limit_rpm = kwargs.get("rate_limit_rpm", 60)
        self.rate_limit_tpm = kwargs.get("rate_limit_tpm", 10000)

# Base provider class


class BaseProvider:
    """Base class for all provider implementations."""

    def __init__(
        self,
        name: str,
        model: str,
        api_key: Optional[str] = None,
        timeout: Optional[float] = None,
        max_retries: int = 3,
        retry_base_delay: float = 1.0,
        base_url: Optional[str] = None,
        first_token_timeout: Optional[float] = None,
    ):
        self.name = name
        self.model = model
        # 拼进 ``Authorization`` 之前必须去首尾空白：带空格的 Key 会让
        # requests 抛 ``Illegal header value b'Bearer   sk-...'``，请求根本
        # 没发出去，用户却以为 Key 本身无效。
        self.api_key = str(
            api_key or os.environ.get("AI_PROVIDER_KEY", "[REDACTED]")
        ).strip()
        # 模型配置中心保存的 base_url 需要随实例走：各 provider 的 ``generate``
        # 历史上只读 ``os.environ``/kwargs，导致"界面上填的 base_url"不生效。
        self.base_url = base_url or None
        # ``None`` = 调用方没配，用类默认值；显式值（哪怕比默认值短）是硬约束，
        # 任何默认下限都不许盖过它。验收脚本用 2 秒超时验证"超时轮不入账"，
        # 静默抬高会让该轮变成"慢成功"。
        self.timeout_explicit = timeout is not None
        self.timeout = (
            float(timeout) if timeout is not None else DEFAULT_GENERATION_TIMEOUT
        )
        # 首字等待与整体读超时是两个阈值：首字之前允许更长的冷启动时间，
        # 首字之后按正常节奏判定"卡住"。用户显式配置的超时是硬约束，
        # 首字等待一律跟随它；180 秒只在完全没配超时时生效。
        # 旧库里的 30 秒已由一次性迁移改成 120 秒，现在还能出现的 30 秒
        # 只可能来自用户自己写下的配置——那就是明确意图，不做任何特判。
        if first_token_timeout is not None:
            self.first_token_timeout = float(first_token_timeout)
        elif not self.timeout_explicit:
            self.first_token_timeout = DEFAULT_FIRST_TOKEN_TIMEOUT
        else:
            self.first_token_timeout = self.timeout
        # 这个超时值是谁定的：``registry``（模型配置中心）/ ``env``
        # （AI_PROVIDER_TIMEOUT）/ ``default``（类默认值）。排障时"120 秒是哪来的"
        # 和"120 秒"本身一样重要。
        self.timeout_source = "default"
        self.max_retries = max_retries
        self.retry_base_delay = retry_base_delay
        self._capabilities = ProviderCapabilities()

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    def _note_usage(self, model: str, tokens_in: int, tokens_out: int) -> None:
        """把 provider 返回的**真实** usage 上报给当前记账作用域。

        拿不到 usage 的 provider 不调用这里：账本会回退到估算并保持
        ``estimated=1``，绝不把估算伪装成真实 usage。
        """
        try:
            from .usage_meter import report_usage

            report_usage(str(model or self.model), tokens_in, tokens_out)
        except Exception:  # noqa: BLE001 - 计量不能影响生成
            logger.debug("usage report skipped", exc_info=True)

    def generate(self, prompt: str, **kwargs) -> str:
        """Generate response to prompt. To be implemented by subclasses."""
        raise NotImplementedError

    def generate_with_retry(self, prompt: str, **kwargs) -> str:
        """Generate with automatic retry and error classification.

        An EMPTY response is treated as a FAILURE, not as a successful
        generation.

        Why
        ---
        Several providers normalise a missing completion to ``""`` --
        e.g. ``response.choices[0].message.content or ""`` -- because the SDK
        type allows ``None``. That ``""`` used to flow straight back to the
        caller as a legitimate result. Downstream (``employee.execute``) then
        reported ``status="completed"``, incremented ``completed_tasks`` and
        emitted a ``completed`` metric, while carrying no model output at all.
        That is a silent fake success: every observable signal says the task was
        done, and nothing records that the model actually said nothing. A caller
        that trusts the status string stores an empty answer as if it were the
        model's reply.

        So emptiness is checked HERE, at the provider boundary, rather than at
        each call site. This is the single choke point every consumer already
        routes through, so the fix covers present call sites
        (``employee.execute``, ``goal_task_graph``) and any future one. An
        empty completion is also retried: a transient upstream hiccup that
        returns no content should not be reported as a completed task on the
        first try.

        Raises:
            ProviderError: if every attempt produced an empty response. The
                message names the provider so the failure is actionable instead
                of surfacing as a bare ``""``.
        """
        import time
        from error_types import ProviderEmptyResponseError

        last_error: Optional[BaseException] = None
        last_empty_detail = ""
        for attempt in range(self.max_retries + 1):
            try:
                response = self.generate(prompt, **kwargs)
            except Exception as e:
                last_error = e
                if not _is_retryable_failure(e):
                    raise
                # 超时/连接类只重试一次：第一次多半是模型还在加载，第二次
                # 还超时就应尽快把"较慢"的结论交给用户，而不是再等 4 轮。
                allowed = (
                    min(self.max_retries, 1)
                    if _is_timeout_or_connection(e)
                    else self.max_retries
                )
                if attempt < allowed:
                    delay = self._retry_delay(e, attempt)
                    time.sleep(delay)
                    continue
                raise

            # The transport succeeded. Emptiness is a SEPARATE failure mode and is
            # checked OUTSIDE the try above, so it is never re-wrapped by
            # classify_error() into a generic ProviderError (which would triple
            # the message and bury the actionable cause).
            if response is None or not str(response).strip():
                last_empty_detail = (
                    f"type={type(response).__name__}, "
                    f"length={len(response) if isinstance(response, str) else 'n/a'}"
                )
                if attempt < self.max_retries:
                    delay = self.retry_base_delay * (2 ** attempt)
                    time.sleep(delay)
                    continue
                raise ProviderEmptyResponseError(
                    self.name,
                    detail=f"{last_empty_detail} after {attempt + 1} attempt(s)",
                    attempts=attempt + 1,
                )
            return response

        # Only reachable if max_retries < 0, which no real provider config uses.
        raise ProviderEmptyResponseError(
            self.name,
            detail=last_empty_detail or "no response produced",
        )

    def _retry_delay(self, exc: BaseException, attempt: int) -> float:
        """指数退避；如果上游给了 ``Retry-After``，尊重但不小于基础退避。"""
        delay = self.retry_base_delay * (2 ** attempt)
        retry_after = getattr(exc, "retry_after", None)
        if isinstance(retry_after, (int, float)) and retry_after > 0:
            delay = max(delay, min(float(retry_after), 60.0))
        return delay

    def chat(self, messages, **kwargs) -> str:
        """Chat-style generation over a message list.

        Default implementation collapses ``messages`` (list of
        ``{"role": ..., "content": ...}`` dicts) into a single text prompt and
        delegates to ``generate``. Chat-native providers (e.g. Ollama) override
        this to use their ``/api/chat`` endpoint for higher multi-turn quality.

        空回复在这里显式失败：``chat`` 不经过 ``generate_with_retry``，旧实现
        会把 ``""`` 当成功返回，上层就记成 ``completed``。
        """
        prompt = "\n".join(
            f'{m.get("role", "user")}: {m.get("content", "")}'
            for m in messages
            if isinstance(m, dict)
        )
        return _require_nonempty_text(self.name, self.generate(prompt, **kwargs))

    def close(self) -> None:
        """Release provider-owned resources (HTTP clients, sessions, ...).

        Default is a no-op: stateless providers own nothing. Providers that
        cache a client override this so a retired instance does not leak
        file descriptors in a long-running gateway.
        """

    def chat_stream(self, messages, **kwargs):
        """Stream chat tokens one at a time.

        Default implementation degrades to non-streaming: it yields the full
        reply as a single token. Providers with native streaming (e.g. Ollama)
        override this to yield incremental tokens.

        空回复不会产出一个空 token：``chat`` 会抛
        :class:`ProviderEmptyResponseError`，调用方据此把这一轮判成失败。
        """
        # 取消令牌只对流式实现有意义；非流式降级直接丢掉，避免透传给下层。
        kwargs.pop("cancel_token", None)
        text = self.chat(messages, **kwargs)
        _require_nonempty_text(self.name, text)
        yield text

    def chat_with_retry(self, messages, **kwargs) -> str:
        """非流式对话：超时/连接类失败自动重试一次。

        只重试"再来一次可能就好"的两类：首次冷启动超时、连接被回收。
        401/400 不重试——重试只让用户多等几秒再看到同一句错话。
        """
        import time

        for attempt in range(2):
            try:
                return self.chat(messages, **kwargs)
            except Exception as exc:
                if attempt >= 1 or not _is_timeout_or_connection(exc):
                    raise
                time.sleep(self._retry_delay(exc, attempt))
        raise RuntimeError("chat_with_retry: unreachable")

    def stream_with_retry(self, messages, **kwargs):
        """流式对话：首个 token 之前超时/断连时自动重试一次。

        重试前先 yield 一个 ``{"type": "notice"}`` 事件：界面据此显示
        "正在重试"，而不是让用户对着一块空白等第二次冷启动。已经产出过
        token 的轮次绝不重试——重放会让用户看到两段重复回答。

        ``cancel_token`` 置位后不再重试：客户端已经走了，第二次生成既没有
        收件人，又会继续占着会话锁。
        """
        import time

        cancel_token = kwargs.pop("cancel_token", None)
        for attempt in range(2):
            produced = False
            try:
                for token in self.chat_stream(
                    messages, cancel_token=cancel_token, **kwargs
                ):
                    if cancel_token is not None:
                        cancel_token.raise_if_cancelled()
                    produced = True
                    yield token
                return
            except Exception as exc:
                if cancel_token is not None and cancel_token.cancelled:
                    raise TurnCancelled(cancel_token.mode or "client_disconnected")
                if produced or attempt >= 1 or not _is_timeout_or_connection(exc):
                    raise
                yield {
                    "type": "notice",
                    "code": "retrying",
                    "message": RETRY_NOTICE_MESSAGE.format(model=self.model),
                }
                time.sleep(self._retry_delay(exc, attempt))

    def get_capabilities(self) -> Dict[str, Any]:
        """Return provider capabilities metadata."""
        return {
            "name": self.name,
            "model": self.model,
            "type": self.__class__.__name__,
            "supports_streaming": self._capabilities.supports_streaming,
            "supports_structured_output": self._capabilities.supports_structured_output,
            "supports_function_calling": self._capabilities.supports_function_calling,
            "supports_vision": self._capabilities.supports_vision,
            "supports_embeddings": self._capabilities.supports_embeddings,
            "max_context_tokens": self._capabilities.max_context_tokens,
            "max_output_tokens": self._capabilities.max_output_tokens,
        }


# MockProvider for development and testing
class MockProvider(BaseProvider):
    """Mock provider for development, testing, and local operations."""

    def __init__(self, name: str, model: str, api_key: Optional[str] = None, **kwargs):
        super().__init__(name, model, api_key, **kwargs)
        self._capabilities = ProviderCapabilities(
            supports_streaming=False,
            supports_structured_output=False,
            supports_function_calling=False,
            supports_vision=False,
            supports_embeddings=False,
            max_context_tokens=4096,
            max_output_tokens=1024,
        )

    def generate(self, prompt: str, **kwargs) -> str:
        """Return an explicit demo notice, never an internal state dump.

        A ``MockProvider`` means no real model is configured. Echoing prompt
        content or dumping capabilities looks like a broken answer and leaks
        internal state, so the user-visible text states the situation and the
        next action, and is explicitly labelled as demo data.
        """
        return MODEL_NOT_CONFIGURED_MESSAGE


#: 逐模型失败原因的**用户可读**说法。上游原文（英文报错/内网地址）只进日志，
#: 这里只按脱敏前算好的 ``upstream_kind`` 翻成一句能指导下一步的中文。
_CHAIN_REASON_TEXT = {
    "quota_exhausted": "额度不足或余额不足，请充值后重试",
    "key_revoked": "API Key 已被停用或吊销，请更换密钥",
    "invalid_key": "API Key 无效或已失效，请更新密钥",
    "rate_limited": "被上游限流，请稍后重试",
    "model_not_found": "模型名不存在或未开通，请核对模型名称",
    "unreachable": "接口地址不可达，请检查地址、端口与网络",
    "upstream_error": "上游返回错误",
}


class ProviderChainError(RuntimeError):
    """所有候选模型都失败：消息本身可直接展示给用户。

    单一 provider 的失败文案只说"这次没拿到回复"，用户不知道系统试过哪些模型、
    每个模型到底败在哪一步。多模型回落必须把**实际尝试顺序**与**每次的真实
    原因**一起交出来，否则"为什么不回落到可用的那个"永远无法自查。
    """

    def __init__(self, attempts: Sequence[Dict[str, str]]):
        self.attempts = [dict(item) for item in attempts]
        kinds = [item.get("kind", "") for item in self.attempts if item.get("kind")]
        # done 帧 / 审计只带一个机器标签：取第一次尝试的分类（"为什么第一个
        # 模型没成"通常最接近用户配置的问题）。
        self.upstream_kind = kinds[0] if kinds else "upstream_error"
        lines = [
            f"{index}. {item.get('model', '未知模型')}：{item.get('reason', '失败')}"
            for index, item in enumerate(self.attempts, 1)
        ]
        super().__init__(
            "所有已配置的模型都没有成功返回，按以下顺序尝试过：\n"
            + "\n".join(lines)
            + "\n请到「系统设置 → 模型管理」逐个点「测试连接」排查，或换一个可用模型。"
        )


def _attempt_failure_reason(exc: BaseException) -> Dict[str, str]:
    """把一次失败的尝试翻成 ``{reason, kind}``；不含任何上游原文。"""
    from error_types import ProviderEmptyResponseError

    if isinstance(exc, ProviderEmptyResponseError):
        return {"reason": "模型没有返回内容，请重试或换一个模型", "kind": "empty_response"}
    if isinstance(exc, ProviderStreamIncompleteError):
        return {"reason": "回复不完整（连接提前结束）", "kind": "incomplete_stream"}
    if isinstance(exc, ProviderTimeoutError) or isinstance(exc, TimeoutError):
        return {"reason": "请求超时（首次加载较慢或网络中断）", "kind": "timeout"}
    if isinstance(exc, ProviderHTTPError):
        base = _CHAIN_REASON_TEXT.get(exc.upstream_kind or "", "上游返回错误")
        # ``exc`` 的消息已中性化（只有状态码与"详情见服务端日志"），可安全展示。
        reason = f"{base}；{exc}"
        detail = _server_detail_text(exc)
        if detail:
            reason += f"；服务端返回：{detail}"
        return {"reason": reason, "kind": exc.upstream_kind or "upstream_error"}
    name = type(exc).__name__.lower()
    if isinstance(exc, (ConnectionError, OSError)) or any(
        token in name for token in _CONNECT_ERROR_NAMES
    ):
        return {"reason": "接口地址不可达，请检查地址与网络", "kind": "unreachable"}
    kind = getattr(exc, "upstream_kind", None)
    if isinstance(kind, str) and kind in _CHAIN_REASON_TEXT:
        return {"reason": _CHAIN_REASON_TEXT[kind], "kind": kind}
    return {"reason": "请求失败（详情已记录到服务端日志）", "kind": "upstream_error"}


class FailoverProvider(BaseProvider):
    """按给定顺序依次尝试多个已配置模型，全部失败时给出完整清单。

    * 顺序由调用方决定（聊天链里「设为默认」的模型排第一），本类不做重排；
    * 流式在**已经吐过字**之后失败时不再换模型：重放会让用户看到两段回答；
    * 单个模型的重试仍由该 provider 自身负责，本类只负责"换下一个"。
    """

    def __init__(
        self,
        providers: Sequence[BaseProvider],
        *,
        labels: Optional[Sequence[str]] = None,
        name: str = "model-chain",
    ):
        if not providers:
            raise ValueError("FailoverProvider 至少需要一个 provider")
        primary = providers[0]
        super().__init__(
            name,
            getattr(primary, "model", "") or "default",
            None,
            timeout=getattr(primary, "timeout", None),
            first_token_timeout=getattr(primary, "first_token_timeout", None),
        )
        self._providers = list(providers)
        self._labels = list(labels) if labels else [
            str(getattr(p, "model", "") or "模型") for p in self._providers
        ]
        if len(self._labels) != len(self._providers):
            self._labels = [
                str(getattr(p, "model", "") or "模型") for p in self._providers
            ]
        self._capabilities = primary.capabilities
        # 运行期归属标记与主模型一致：审计里"这一轮用了谁的 Key"仍按主模型算。
        self.model_config_id = getattr(primary, "model_config_id", "")
        self.owner_principal = getattr(primary, "owner_principal", "")
        self.timeout_source = getattr(primary, "timeout_source", "default")

    @property
    def models(self) -> list[str]:
        """实际会被尝试的模型顺序（供日志/诊断/测试断言）。"""
        return list(self._labels)

    def _record(self, index: int, exc: BaseException) -> Dict[str, str]:
        item = _attempt_failure_reason(exc)
        logger.warning(
            "model chain: attempt %s/%s model=%s failed (%s: %s)",
            index + 1,
            len(self._providers),
            self._labels[index],
            type(exc).__name__,
            getattr(exc, "upstream_kind", None) or "-",
        )
        return {"model": self._labels[index], **item}

    def _run(self, call) -> str:
        attempts: list[dict[str, str]] = []
        for index, provider in enumerate(self._providers):
            try:
                return call(provider)
            except Exception as exc:  # noqa: BLE001 - 逐条记录后换下一个
                attempts.append(self._record(index, exc))
        raise ProviderChainError(attempts)

    def generate(self, prompt: str, **kwargs) -> str:
        return self._run(lambda provider: provider.generate(prompt, **kwargs))

    def chat(self, messages, **kwargs) -> str:
        return self._run(lambda provider: provider.chat(messages, **kwargs))

    def chat_stream(self, messages, **kwargs):
        attempts: list[dict[str, str]] = []
        produced = False
        cancel_token = kwargs.get("cancel_token")
        for index, provider in enumerate(self._providers):
            try:
                for token in provider.chat_stream(messages, **kwargs):
                    if cancel_token is not None:
                        cancel_token.raise_if_cancelled()
                    produced = True
                    yield token
                return
            except Exception as exc:  # noqa: BLE001
                if cancel_token is not None and cancel_token.cancelled:
                    raise TurnCancelled(cancel_token.mode or "client_disconnected")
                if produced:
                    # 已经吐过字：这一段是真实内容，抛给上层按"断流/不完整"处理。
                    raise
                attempts.append(self._record(index, exc))
        raise ProviderChainError(attempts)

    def close(self) -> None:
        for provider in self._providers:
            try:
                provider.close()
            except Exception:  # noqa: BLE001 - 关闭失败不影响已完成的请求
                logger.debug("model chain: closing %r failed", provider, exc_info=True)


# OpenAI provider (1.x SDK compatible, using official openai library)
class OpenAIProvider(BaseProvider):
    """OpenAI API provider integration using official openai Python SDK (1.x)."""

    def __init__(self, name: str, model: str, api_key: Optional[str] = None, **kwargs):
        super().__init__(name, model, api_key, **kwargs)
        # Store proxy config. Resolve through ``_provider_env`` so a value written
        # in ``.env`` actually takes effect: ConfigManager loads .env into its own
        # store WITHOUT exporting to ``os.environ``, so reading only os.environ
        # here silently ignored OPENAI_BASE_URL / OPENAI_PROXY from ``.env``.
        self.proxy = kwargs.get("proxy") or _provider_env("OPENAI_PROXY") or None
        self.base_url = kwargs.get("base_url") or _provider_env("OPENAI_BASE_URL") or None
        # 复用同一个 SDK client：此前每次 ``_get_client()`` 都新建
        # openai.OpenAI + httpx.Client 且从不关闭，长跑会话会不断累积句柄。
        self._client = None
        self._client_lock = threading.Lock()
        # Track if using newer SDK version
        self.sdk_version = "1.x"
        self._capabilities = ProviderCapabilities(
            supports_streaming=True,
            supports_structured_output=True,
            supports_function_calling=True,
            supports_vision=True,
            supports_embeddings=True,
            max_context_tokens=128000 if "gpt-4" in model.lower() else 16384,
            max_output_tokens=4096,
            rate_limit_rpm=500,
            rate_limit_tpm=30000,
        )

    def _get_client(self):
        """Get (and reuse) the OpenAI client with proper configuration for 1.x SDK."""
        client = self._client
        if client is not None:
            return client
        with self._client_lock:
            if self._client is None:
                import openai
                import httpx

                # 关闭自动重定向：openai SDK 默认 follow_redirects=True，会把
                # "保存时是公网、运行时 302 到 169.254.169.254" 变成真实出站请求。
                # 这里让 3xx 以状态码形式返回，再由 _guard_upstream_error 转为
                # 中性拒绝文案。
                http_client = httpx.Client(
                    follow_redirects=False,
                    timeout=self.first_token_timeout,
                    proxy=self.proxy or None,
                )

                self._client = openai.OpenAI(
                    api_key=self.api_key,
                    base_url=self.base_url,
                    # Every other provider hands ``self.timeout`` to its
                    # transport; this one used to leave the SDK default (10 min)
                    # in place, so the declared 30s timeout was silently ignored.
                    timeout=self.first_token_timeout,
                    http_client=http_client,
                    # 重试策略由 ``generate_with_retry`` 统一负责：SDK 自己再
                    # 重试 2 次会让"到底打了几次上游"对不上账，也会放大计费。
                    max_retries=0,
                )
            return self._client

    def _guard_target(self) -> None:
        """每次真实请求前重新校验目标（防 DNS rebinding/私网端点）。"""
        assert_safe_runtime_target(self.base_url, context="模型服务")

    def _guard_upstream_error(self, exc: BaseException) -> None:
        """把 SDK 的 HTTP 错误转成中性消息；非 HTTP 错误交给调用方原样抛出。"""
        _neutralize_http_error(exc, "模型服务")

    def close(self) -> None:
        """Close the cached OpenAI/httpx client (idempotent)."""
        with self._client_lock:
            client, self._client = self._client, None
        if client is not None:
            try:
                client.close()
            except Exception as exc:  # noqa: BLE001 - closing must not raise
                logging.getLogger(__name__).warning(
                    "OpenAIProvider close failed: %s", exc
                )

    def _response_text(self, response: Any, label: str = "模型服务") -> str:
        """统一解析非流式响应，返回纯文本；空回复返回 ``""`` 由调用方判失败。

        覆盖：
        * ``content`` 为 ``str`` / ``None`` / **content parts list**；
        * 200 但响应体是 ``{"error": ...}``；
        * SDK 在某些 content-type 下直接返回原始 ``str``（HTML/纯文本/JSON）；
        * 空 ``choices`` / 缺 ``message``。
        """
        if isinstance(response, str):
            try:
                payload = json.loads(response)
            except (json.JSONDecodeError, ValueError) as exc:
                lowered = response.lower()
                if "<html" in lowered or "bad gateway" in lowered:
                    logger.info("%s returned HTML instead of JSON", label)
                _raise_invalid_response(label, exc)
            return self._response_text(payload, label)
        if isinstance(response, (dict, list)):
            error_payload = _embedded_error(response)
            if error_payload:
                _raise_embedded_upstream_error(label, error_payload, status=200)
            return _extract_openai_payload_text(response)
        error_payload = _embedded_error(response)
        if error_payload:
            _raise_embedded_upstream_error(label, error_payload, status=200)
        choices = getattr(response, "choices", None)
        if not isinstance(choices, list) or not choices:
            return ""
        return _extract_openai_message_text(getattr(choices[0], "message", None))

    def generate(self, prompt: str, **kwargs) -> str:
        """Generate response via OpenAI API (1.x SDK compatible)."""
        model = kwargs.get("model", self.model)
        temperature = kwargs.get("temperature", 0.7)
        max_tokens = kwargs.get("max_tokens", 1000)
        # Support new OpenAI SDK response_format for structured output
        response_format = kwargs.get("response_format")

        self._guard_target()
        try:
            client = self._get_client()
            create_params = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            # Add response_format if specified (structured output support)
            if response_format:
                create_params["response_format"] = response_format

            response = client.chat.completions.create(**create_params)
            text = self._response_text(response)
            # 空回复不记用量：没有内容就没有可计费的产出，也不能让账本把
            # 一次失败记成一次成功调用。
            if text:
                usage = getattr(response, "usage", None)
                if usage is not None:
                    self._note_usage(model, *_usage_counts(usage))
            return text
        except ProviderHTTPError:
            raise
        except Exception as exc:
            if isinstance(exc, (json.JSONDecodeError, ValueError)):
                _raise_invalid_response("模型服务", exc)
            self._guard_upstream_error(exc)
            # Re-raise to let generate_with_retry handle it
            raise

    def chat(self, messages, **kwargs) -> str:
        """原生 chat completions：保留多轮角色，不再折叠成单段 prompt。

        ``provider=openai`` 同时承担"任意 OpenAI 兼容服务"（DeepSeek/Moonshot/
        vLLM/LM Studio/Ollama ``/v1``/自建网关）。走原生 messages 后，多轮上下文、
        系统提示与工具消息才不会被降级成一段纯文本。
        """
        model = kwargs.get("model", self.model)
        temperature = kwargs.get("temperature", 0.7)
        max_tokens = kwargs.get("max_tokens", 1000)
        self._guard_target()
        try:
            response = self._get_client().chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            text = self._response_text(response)
            _require_nonempty_text(self.name, text)
            usage = getattr(response, "usage", None)
            if usage is not None:
                self._note_usage(model, *_usage_counts(usage))
            return text
        except ProviderHTTPError:
            raise
        except Exception as exc:
            if isinstance(exc, (json.JSONDecodeError, ValueError)):
                _raise_invalid_response("模型服务", exc)
            self._guard_upstream_error(exc)
            raise

    def _open_stream(self, *, params: dict):
        """建立并进入原始 SSE 流；``stream_options`` 不被支持时（400）退回。

        注意：``with_streaming_response.create`` 是惰性的，请求在
        ``__enter__`` 时才发出，所以 400 只能在这一步捕获。
        """
        client = self._get_client()
        create = client.chat.completions.with_streaming_response.create

        def _enter(include_usage: bool):
            stream_params = dict(params)
            stream_params["stream"] = True
            if include_usage:
                stream_params["stream_options"] = {"include_usage": True}
            cm = create(**stream_params)
            return cm, cm.__enter__()

        try:
            return _enter(True)
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            if status == 400:
                # 只有"不支持 stream_options"的 400 才值得退回；429/401 也
                # 吞掉重试会把额度耗尽/Key 吊销的真实原因再掩盖一次。
                logger.info("模型服务不支持 stream_options，退回普通流式")
                try:
                    return _enter(False)
                except Exception as retry_exc:
                    self._guard_upstream_error(retry_exc)
                    raise
            self._guard_upstream_error(exc)
            raise

    def chat_stream(self, messages, **kwargs):
        """按原始 SSE 行流式返回 OpenAI 兼容响应。

        为什么不用 ``for chunk in stream``：SDK 已经吞掉 ``[DONE]`` 与
        ``: keepalive``，导致"流被干净截断"看起来像正常结束，且持续心跳会
        让读超时永不触发。这里拿原始行自行解析：

        * 缺 ``[DONE]``（且无 ``finish_reason``）→ 判不完整；
        * ``: keepalive`` / ``event: ping`` 不重置空闲计时；
        * ``delta.content`` 为 content parts list → 统一取文本。
        """
        model = kwargs.get("model", self.model)
        temperature = kwargs.get("temperature", 0.7)
        max_tokens = kwargs.get("max_tokens", 1000)
        cancel_token = kwargs.get("cancel_token")
        self._guard_target()
        params = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        response_cm = None
        try:
            try:
                response_cm, response = self._open_stream(params=params)
            except Exception as exc:
                if isinstance(exc, ProviderHTTPError):
                    raise
                if isinstance(exc, (json.JSONDecodeError, ValueError)):
                    _raise_invalid_response("模型服务", exc)
                self._guard_upstream_error(exc)
                raise
            if cancel_token is not None and hasattr(response, "close"):
                cancel_token.bind_closer(response.close)
            yield from _iter_with_cancel(
                _iter_sse_text(
                    response.iter_lines(),
                    label="模型服务",
                    idle_timeout=self.timeout,
                    first_token_timeout=self.first_token_timeout,
                    on_usage=lambda u: self._note_usage(model, *_usage_counts(u)),
                ),
                cancel_token,
            )
        finally:
            if response_cm is not None:
                try:
                    response_cm.__exit__(None, None, None)
                except Exception:  # noqa: BLE001 - 关闭不能掩盖真实结论
                    logger.debug("closing openai stream failed", exc_info=True)


# Anthropic provider (using official anthropic SDK)
class AnthropicProvider(BaseProvider):
    """Anthropic API provider integration using official anthropic SDK."""

    def __init__(self, name: str, model: str, api_key: Optional[str] = None, **kwargs):
        super().__init__(name, model, api_key, **kwargs)
        self._capabilities = ProviderCapabilities(
            supports_streaming=True,
            supports_structured_output=True,
            supports_function_calling=True,
            supports_vision=True,
            supports_embeddings=False,
            max_context_tokens=200000,
            max_output_tokens=8192,
            rate_limit_rpm=50,
            rate_limit_tpm=40000,
        )
        # Reuse one SDK client per provider instance; constructing a fresh
        # anthropic.Anthropic per request leaked its urllib3 pools' sockets in a
        # long-running gateway (same class of defect as OpenAIProvider).
        self._client = None
        self._client_lock = threading.Lock()

    def _get_client(self):
        client = self._client
        if client is not None:
            return client
        import anthropic

        # anthropic 1.x（本仓库 requirements 钉的 1.3.0）内部依赖 ``httpx2``，
        # ``__init__`` 会对 ``http_client`` 做类型校验；继续传 ``httpx.Client``
        # 会在建连前就 TypeError，表现为"0 个上游请求 + 3.5 秒后通用失败"。
        # 不能硬编码任一模块：0.x 生态仍用 httpx，1.x 用 httpx2。跟随 SDK 自己
        # 导入的那个模块最稳。
        try:
            from anthropic._base_client import httpx2 as _anthropic_http
        except ImportError:  # pragma: no cover - 兼容 anthropic 0.x
            import httpx as _anthropic_http

        with self._client_lock:
            if self._client is None:
                options = {
                    "api_key": self.api_key,
                    # 与 OpenAI 兼容路径同一条策略：不跟随重定向。
                    "http_client": _anthropic_http.Client(
                        follow_redirects=False, timeout=self.first_token_timeout
                    ),
                    # 重试由 generate_with_retry 统一管理，避免 SDK 偷偷放大。
                    "max_retries": 0,
                }
                if self.base_url:
                    options["base_url"] = self.base_url
                self._client = anthropic.Anthropic(**options)
            return self._client

    def close(self) -> None:
        """Close the cached Anthropic SDK client (idempotent)."""
        with self._client_lock:
            client, self._client = self._client, None
        if client is not None:
            try:
                client.close()
            except Exception as exc:  # noqa: BLE001 - closing must not raise
                logging.getLogger(__name__).warning(
                    "AnthropicProvider close failed: %s", exc
                )

    def _response_text(self, response: Any) -> str:
        """从 Anthropic ``content`` 中提取文本，跳过 thinking / tool_use。

        ``content[0]`` 不一定是文本：Anthropic 可能先返回 ``thinking`` 或
        ``tool_use`` block，旧实现 ``response.content[0].text`` 会抛
        ``AttributeError``（被重试 4 次）；空 ``content`` 会 ``IndexError``；
        ``text=None`` 会返回 ``None``。这里逐块筛选：
        * 只取 ``type == "text"``（或没有 type 但有字符串 text）的块；
        * ``thinking`` / ``tool_use`` / ``tool_result`` / ``image`` 跳过；
        * ``text`` 不是字符串（``None``）时跳过。
        """
        error_payload = _embedded_error(response)
        if error_payload:
            _raise_embedded_upstream_error("Anthropic 服务", error_payload, status=200)
        content = response.get("content") if isinstance(response, dict) else getattr(
            response, "content", None
        )
        if not isinstance(content, (list, tuple)):
            return ""
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                block_type = block.get("type")
                text = block.get("text")
            else:
                block_type = getattr(block, "type", None)
                text = getattr(block, "text", None)
            if block_type in ("thinking", "tool_use", "tool_result", "image"):
                continue
            if isinstance(text, str) and text:
                parts.append(text)
        return "".join(parts)

    def generate(self, prompt: str, **kwargs) -> str:
        """Generate response via Anthropic API."""
        model = kwargs.get("model", self.model)
        assert_safe_runtime_target(self.base_url, context="Anthropic 服务")
        try:
            client = self._get_client()
            response = client.messages.create(
                model=model,
                max_tokens=kwargs.get("max_tokens", 1000),
                # temperature removed for compatibility - use system prompt instead
                messages=[{"role": "user", "content": prompt}],
            )
            text = self._response_text(response)
            if text:
                usage = getattr(response, "usage", None)
                if usage is not None:
                    self._note_usage(
                        model,
                        getattr(usage, "input_tokens", 0),
                        getattr(usage, "output_tokens", 0),
                    )
            return text
        except ProviderHTTPError:
            raise
        except Exception as exc:
            _neutralize_http_error(exc, "Anthropic 服务")
            # Re-raise to let generate_with_retry handle it
            raise


# Google provider (Gemini)
class GoogleProvider(BaseProvider):
    """Google Gemini API provider integration."""

    def __init__(self, name: str, model: str, api_key: Optional[str] = None, **kwargs):
        super().__init__(name, model, api_key, **kwargs)
        self._capabilities = ProviderCapabilities(
            supports_streaming=True,
            supports_structured_output=True,
            supports_function_calling=True,
            supports_vision=True,
            supports_embeddings=True,
            max_context_tokens=1000000,
            max_output_tokens=8192,
            rate_limit_rpm=60,
            rate_limit_tpm=32000,
        )

    def generate(self, prompt: str, **kwargs) -> str:
        """Generate response via Google Gemini API (``google-genai`` SDK)."""
        try:
            from google import genai
            from google.genai import types
        except ModuleNotFoundError as exc:  # pragma: no cover - environment guard
            raise RuntimeError(
                "GoogleProvider requires the 'google-genai' package "
                "(pip install 'google-genai'); the legacy "
                "'google-generativeai' SDK is not supported because it pins "
                "protobuf<6, which conflicts with the shipped protobuf 6.x."
            ) from exc

        model = kwargs.get("model", self.model)
        base_url = (kwargs.get("base_url") or self.base_url or "").strip()
        if base_url:
            # 自定义 base_url（含本地兼容桩/代理）必须过同一条运行时目标校验。
            assert_safe_runtime_target(base_url, context="Gemini 服务")
        try:
            if base_url:
                client = genai.Client(
                    api_key=self.api_key,
                    http_options=types.HttpOptions(base_url=base_url),
                )
            else:
                client = genai.Client(api_key=self.api_key)
            response = client.models.generate_content(model=model, contents=prompt)
            usage = getattr(response, "usage_metadata", None)
            if usage is not None:
                self._note_usage(
                    model,
                    getattr(usage, "prompt_token_count", 0),
                    getattr(usage, "candidates_token_count", 0),
                )
            return response.text
        except ProviderHTTPError:
            raise
        except Exception as exc:
            # Gemini SDK 的错误（ClientError/ServerError）此前完全不分类：
            # 界面上只会得到"这次没有拿到回复"，用户不知道是 Key、额度还是
            # 模型名的问题。这里统一转成带状态码与中性文案的 ProviderHTTPError。
            _neutralize_http_error(exc, "Gemini 服务")
            raise


# Ollama provider (self-hosted)
class OllamaProvider(BaseProvider):
    """Ollama self-hosted model provider integration."""

    def __init__(self, name: str, model: str, api_key: Optional[str] = None, **kwargs):
        super().__init__(name, model, api_key, **kwargs)
        self._capabilities = ProviderCapabilities(
            supports_streaming=True,
            supports_structured_output=False,
            supports_function_calling=False,
            supports_vision=False,
            supports_embeddings=True,
            max_context_tokens=32768,
            max_output_tokens=4096,
            rate_limit_rpm=1000,
            rate_limit_tpm=100000,
        )

    def generate(self, prompt: str, **kwargs) -> str:
        """Generate response via Ollama local API."""
        import requests

        model = kwargs.get("model", self.model)
        base_url = kwargs.get("base_url") or self.base_url or os.environ.get(
            "OLLAMA_BASE_URL", "http://localhost:11434"
        )
        assert_safe_runtime_target(
            base_url, context="Ollama 服务", allow_private_service=True
        )

        try:
            # Bypass any HTTP(S) proxy for the (usually localhost) Ollama
            # endpoint. requests inherits HTTP_PROXY/HTTPS_PROXY from the
            # environment, which would route localhost traffic through an
            # unintended proxy (e.g. Ollama's own app proxy) and fail with
            # a 502 "upstream connect failed". Passing proxies=None forces a
            # direct connection.
            response = requests.post(
                f"{base_url}/api/generate",
                json={"model": model, "prompt": prompt, "stream": False},
                timeout=self.first_token_timeout,
                proxies={"http": None, "https": None},
                allow_redirects=False,
            )
            if response.status_code != 200:
                _raise_upstream_http("Ollama 服务", response)
            try:
                result = response.json()
            except ValueError as exc:
                _raise_invalid_response("Ollama 服务", exc)
            if not isinstance(result, dict):
                _raise_invalid_response("Ollama 服务")
            # Ollama 也会用 200 包 ``{"error": ...}``；旧实现把整个 dict
            # ``str(result)`` 当回复上屏。
            if result.get("error"):
                _raise_embedded_upstream_error(
                    "Ollama 服务", result.get("error"), status=200
                )
            text = _coerce_content_text(result.get("response"))
            if text:
                self._note_usage(
                    model,
                    int(result.get("prompt_eval_count") or 0),
                    int(result.get("eval_count") or 0),
                )
            return text
        except ProviderHTTPError:
            raise
        except Exception:
            # Re-raise to let generate_with_retry handle it
            raise

    def chat(self, messages, **kwargs) -> str:
        """Generate a reply via Ollama's native ``/api/chat`` endpoint.

        Uses the official chat interface (``messages`` array) which yields much
        higher multi-turn quality for chat models like qwen2.5 than the raw
        ``/api/generate`` completion path. Bypasses any HTTP(S) proxy for the
        (usually localhost) endpoint, same as ``generate``.
        """
        import requests

        model = kwargs.get("model", self.model)
        base_url = kwargs.get("base_url") or self.base_url or os.environ.get(
            "OLLAMA_BASE_URL", "http://localhost:11434"
        )
        assert_safe_runtime_target(
            base_url, context="Ollama 服务", allow_private_service=True
        )

        try:
            response = requests.post(
                f"{base_url}/api/chat",
                json={"model": model, "messages": messages, "stream": False},
                timeout=self.first_token_timeout,
                proxies={"http": None, "https": None},
                allow_redirects=False,
            )
            if response.status_code != 200:
                _raise_upstream_http("Ollama 服务", response)
            try:
                result = response.json()
            except ValueError as exc:
                _raise_invalid_response("Ollama 服务", exc)
            if not isinstance(result, dict):
                _raise_invalid_response("Ollama 服务")
            if result.get("error"):
                _raise_embedded_upstream_error(
                    "Ollama 服务", result.get("error"), status=200
                )
            message = result.get("message")
            text = _coerce_content_text(
                message.get("content") if isinstance(message, dict) else None
            )
            # 空回复必须显式失败：以前会返回 ""，上层记成 completed，
            # 界面只剩一个「…」。
            _require_nonempty_text(self.name, text)
            self._note_usage(
                model,
                int(result.get("prompt_eval_count") or 0),
                int(result.get("eval_count") or 0),
            )
            return text
        except ProviderHTTPError:
            raise
        except Exception:
            # Re-raise to let generate_with_retry handle it
            raise

    def chat_stream(self, messages, **kwargs):
        """Stream tokens via Ollama's native ``/api/chat`` (NDJSON, stream=True).

        Each response line is a JSON object whose ``message.content`` is the
        incremental token; the final frame has ``done: true``. ``done`` is the
        only proof of a normal end: a stream that stops early raises
        :class:`ProviderStreamIncompleteError` instead of looking complete.
        """
        import requests

        model = kwargs.get("model", self.model)
        base_url = kwargs.get("base_url") or self.base_url or os.environ.get(
            "OLLAMA_BASE_URL", "http://localhost:11434"
        )
        assert_safe_runtime_target(
            base_url, context="Ollama 服务", allow_private_service=True
        )

        cancel_token = kwargs.get("cancel_token")
        response = requests.post(
            f"{base_url}/api/chat",
            json={"model": model, "messages": messages, "stream": True},
            timeout=self.first_token_timeout,
            proxies={"http": None, "https": None},
            stream=True,
            allow_redirects=False,
        )
        try:
            if response.status_code != 200:
                _raise_upstream_http("Ollama 服务", response)
            if cancel_token is not None:
                cancel_token.bind_closer(response.close)
            yield from _iter_with_cancel(
                _iter_ndjson_text(
                    _iter_raw_lines(response),
                    label="Ollama 服务",
                    idle_timeout=self.timeout,
                    first_token_timeout=self.first_token_timeout,
                    on_usage=lambda u: self._note_usage(model, *_usage_counts(u)),
                ),
                cancel_token,
            )
        finally:
            # ``stream=True`` keeps the urllib3 connection checked out until the
            # response is closed. Breaking out early on the ``done`` frame (or a
            # consumer abandoning the generator) used to leave that socket open
            # until GC -- the +200 handles/hour long-run defect. Closing here
            # returns the connection deterministically on every path.
            try:
                response.close()
            except Exception:  # noqa: BLE001 - closing must never mask a stream error
                pass


# Moonshot provider
class MoonshotProvider(BaseProvider):
    """Moonshot AI provider integration."""

    def __init__(self, name: str, model: str, api_key: Optional[str] = None, **kwargs):
        super().__init__(name, model, api_key, **kwargs)
        self._capabilities = ProviderCapabilities(
            supports_streaming=True,
            supports_structured_output=False,
            supports_function_calling=True,
            supports_vision=False,
            supports_embeddings=False,
            max_context_tokens=32768,
            max_output_tokens=4096,
            rate_limit_rpm=60,
            rate_limit_tpm=40000,
        )



    # ─── 统一解析（Moonshot 只走这一条路径） ─────────
    def _post(self, messages, *, stream: bool, **kwargs):
        """一次真实请求：``(model, response)``；非 200 已转成中性异常。"""
        import requests

        model = kwargs.get("model", self.model)
        base_url = kwargs.get("base_url") or self.base_url or os.environ.get(
            "MOONSHOT_BASE_URL", "https://api.moonshot.ai/v1"
        )
        api_key = self.api_key or os.environ.get("MOONSHOT_API_KEY", "[REDACTED]")
        headers = {"Authorization": f"Bearer {api_key}"} if api_key != "[REDACTED]" else {}
        assert_safe_runtime_target(base_url, context="Moonshot 服务")
        try:
            response = requests.post(
                f"{base_url}/chat/completions",
                json={"model": model, "messages": messages, "stream": stream},
                headers=headers,
                timeout=self.first_token_timeout,
                stream=stream,
                proxies={"http": None, "https": None},
                allow_redirects=False,
            )
        except requests.exceptions.RequestException as exc:
            logger.warning("Moonshot request failed: %s", type(exc).__name__)
            raise ProviderHTTPError(
                "Moonshot 服务不可达，请检查端点与网络。",
                upstream_kind="unreachable",
            ) from exc
        if response.status_code != 200:
            try:
                _raise_upstream_http("Moonshot", response)
            finally:
                response.close()
        return model, response

    def _payload_text(self, result: Any) -> str:
        if not isinstance(result, dict):
            _raise_invalid_response("Moonshot")
        if result.get("error"):
            _raise_embedded_upstream_error(
                "Moonshot", result.get("error"), status=200
            )
        return _extract_openai_payload_text(result)

    def generate(self, prompt: str, **kwargs) -> str:
        """Generate response via Moonshot API."""
        return self.chat([{"role": "user", "content": prompt}], **kwargs)

    def chat(self, messages, **kwargs) -> str:
        """Moonshot 的 messages 接口（非流式）。"""
        model, response = self._post(messages, stream=False, **kwargs)
        try:
            result = response.json()
        except ValueError as exc:
            _raise_invalid_response("Moonshot", exc)
        finally:
            response.close()
        text = self._payload_text(result)
        _require_nonempty_text(self.name, text)
        usage = result.get("usage") or {}
        self._note_usage(model, *_usage_counts(usage))
        return text

    def chat_stream(self, messages, **kwargs):
        """真实 SSE 流式；缺 ``[DONE]``/坏帧/连接中断按"不完整"处理。"""
        cancel_token = kwargs.get("cancel_token")
        model, response = self._post(messages, stream=True, **kwargs)
        try:
            if cancel_token is not None:
                cancel_token.bind_closer(response.close)
            yield from _iter_with_cancel(
                _iter_sse_text(
                    _iter_raw_lines(response),
                    label="Moonshot 服务",
                    idle_timeout=self.timeout,
                    first_token_timeout=self.first_token_timeout,
                    on_usage=lambda u: self._note_usage(model, *_usage_counts(u)),
                ),
                cancel_token,
            )
        finally:
            response.close()


# DeepSeek provider
class DeepSeekProvider(BaseProvider):
    """DeepSeek API provider integration."""

    def __init__(self, name: str, model: str, api_key: Optional[str] = None, **kwargs):
        super().__init__(name, model, api_key, **kwargs)
        self._capabilities = ProviderCapabilities(
            supports_streaming=True,
            supports_structured_output=False,
            supports_function_calling=True,
            supports_vision=False,
            supports_embeddings=False,
            max_context_tokens=65536,
            max_output_tokens=8192,
            rate_limit_rpm=60,
            rate_limit_tpm=40000,
        )

    def _post(self, messages, *, stream: bool, **kwargs):
        """一次真实请求：``(model, response)``；非 200 已转成中性异常。"""
        import requests

        model = kwargs.get("model", self.model)
        base_url = kwargs.get("base_url") or self.base_url or os.environ.get(
            "DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"
        )
        api_key = self.api_key or os.environ.get("DEEPSEEK_API_KEY", "[REDACTED]")
        headers = {"Authorization": f"Bearer {api_key}"} if api_key != "[REDACTED]" else {}

        # 地址由用户填写：每次请求前复核一次，堵住保存后改 DNS 的绕过路径。
        assert_safe_runtime_target(base_url, context="DeepSeek 服务")
        try:
            response = requests.post(
                f"{base_url}/chat/completions",
                json={"model": model, "messages": messages, "stream": stream},
                headers=headers,
                timeout=self.first_token_timeout,
                stream=stream,
                proxies={"http": None, "https": None},
                allow_redirects=False,
            )
        except requests.exceptions.RequestException as exc:
            logger.warning("DeepSeek request failed: %s", type(exc).__name__)
            raise ProviderHTTPError(
                "DeepSeek 服务不可达，请检查端点与网络。",
                upstream_kind="unreachable",
            ) from exc
        if response.status_code != 200:
            try:
                _raise_upstream_http("DeepSeek", response)
            finally:
                response.close()
        return model, response

    def generate(self, prompt: str, **kwargs) -> str:
        """Generate response via DeepSeek API."""
        return self.chat([{"role": "user", "content": prompt}], **kwargs)

    def chat(self, messages, **kwargs) -> str:
        """DeepSeek 的 messages 接口（非流式）。"""
        model, response = self._post(messages, stream=False, **kwargs)
        try:
            result = response.json()
        except ValueError as exc:
            _raise_invalid_response("DeepSeek", exc)
        finally:
            response.close()
        if not isinstance(result, dict):
            _raise_invalid_response("DeepSeek")
        if result.get("error"):
            _raise_embedded_upstream_error("DeepSeek", result.get("error"), status=200)
        text = _extract_openai_payload_text(result)
        _require_nonempty_text(self.name, text)
        usage = result.get("usage") or {}
        self._note_usage(model, *_usage_counts(usage))
        return text

    def chat_stream(self, messages, **kwargs):
        """真实 SSE 流式；缺 ``[DONE]``/坏帧/连接中断按"不完整"处理。"""
        cancel_token = kwargs.get("cancel_token")
        model, response = self._post(messages, stream=True, **kwargs)
        try:
            if cancel_token is not None:
                cancel_token.bind_closer(response.close)
            yield from _iter_with_cancel(
                _iter_sse_text(
                    _iter_raw_lines(response),
                    label="DeepSeek 服务",
                    idle_timeout=self.timeout,
                    first_token_timeout=self.first_token_timeout,
                    on_usage=lambda u: self._note_usage(model, *_usage_counts(u)),
                ),
                cancel_token,
            )
        finally:
            response.close()


# AutoGen integrator
class AutoGenIntegrator:
    """Integration layer for AutoGen framework."""

    def __init__(self, provider: BaseProvider):
        self.provider = provider

    def create_agent(self, name: str, system_prompt: str) -> Dict[str, Any]:
        """Create an AutoGen-compatible agent configuration."""
        return {
            "name": name,
            "system_prompt": system_prompt,
            "model": self.provider.model,
            "api_key": self.provider.api_key,
            "provider": self.provider.name,
        }

    def generate_response(self, messages: list, **kwargs) -> str:
        """Generate response using provider through AutoGen pipeline."""
        # Last message in conversation
        last_msg = messages[-1]["content"] if messages else ""
        return self.provider.generate(last_msg, **kwargs)


# AG2 integrator
class AG2Integrator:
    """Integration layer for AG2 (Autogen 2.0) framework."""

    def __init__(self, provider: BaseProvider):
        self.provider = provider

    def create_agent_profile(self, name: str, instructions: str) -> Dict[str, Any]:
        """Create an AG2 agent profile."""
        return {
            "name": name,
            "instructions": instructions,
            "model": self.provider.model,
            "provider": self.provider.name,
            "capabilities": self.provider.get_capabilities(),
        }

    def dispatch_task(self, agent_name: str, task: str, **kwargs) -> str:
        """Dispatch a task to an AG2 agent."""
        return self.provider.generate(
            f"Agent {agent_name} executing task: {task}",
            **kwargs
        )


# LangGraph integrator
class LangGraphIntegrator:
    """Integration layer for LangGraph framework."""

    def __init__(self, provider: BaseProvider):
        self.provider = provider

    def add_node(self, name: str, func) -> None:
        """Add a LangGraph node using provider generation."""
        def wrapped(**state):
            return {"output": self.provider.generate(str(func(state)))}
        # In real usage, this would be added to a StateGraph
        return wrapped

    def create_edge(self, source: str, target: str, condition: str = None) -> Dict[str, Any]:
        """Create a LangGraph edge configuration."""
        return {
            "source": source,
            "target": target,
            "condition": condition or "always",
        }


#: 会被视为"非真实"的 provider 类型取值。
#:
#: 这不是一张"低质量 provider"名单，而是"接上去也不会产生真实模型调用"的名单。
#: 抽成模块级常量，是因为这个判断此前在两处各写了一遍（这里，以及
#: ``roster_payload`` 的逐项标记），而两处独立实现的同一 judgement 必然各自演化
#: —— 那时名册说mock、``/v1/health`` 说 real，界面该信谁就没有答案了。
NON_REAL_PROVIDER_TYPES: frozenset = frozenset({"mock", "", "none"})


def provider_mode_for(provider_type: Optional[str]) -> str:
    """某个 provider 类型的诚实性模式：``"mock"``（编造结果）或 ``"real"``。

    这是全仓**唯一**裁决"这个 provider 类型是不是假的"的地方。它是纯函数 ——
    只看传入的类型串，不读环境、不构造实例 —— 所以任何调用方（名册逐项标记、
    ``ai_provider_status``、将来的校验脚本）都能得到同一个答案，且不需要临时改动
    任何全局状态。

    Args:
        provider_type: provider 类型串；``None`` / 空串按"未配置"处理，而未配置的
            默认就是 ``mock``（安全默认：宁可说这是假的，也不要让人以为接了真模型）。
    """
    return "real" if str(provider_type or "").strip().lower() not in NON_REAL_PROVIDER_TYPES else "mock"


def ai_provider_status() -> Dict[str, Any]:
    """Honest, real-time view of the configured AI provider mode.

    The default is ``mock`` (a safe development default -- no network, no fake
    key). Anything other than ``mock`` is a REAL provider. We also surface
    whether the execution kernel's *simulated* capability path is opted in
    (``LIUHAO_ALLOW_SIMULATED_EXECUTION=1``), so a reader can tell at a glance
    whether outcomes are real executions or explicitly-requested simulations.
    Never silently pretends a simulation is real.
    """
    ptype = _provider_env("AI_PROVIDER_TYPE", "mock").lower()
    source = "env"
    # 模型配置中心优先：界面上的默认模型才是**实际**会被调用的 provider。
    cfg = _registry_config()
    if cfg is not None:
        ptype = str(cfg.get("provider") or "").lower()
        source = "model_config"
    simulated = (os.environ.get("LIUHAO_ALLOW_SIMULATED_EXECUTION") or "").strip().lower() in {
        "1", "true", "yes", "on",
    }
    # 非法类型如实标记为 invalid：旧实现会在此处因 provider_mode_for() 只判断
    # "是否 mock" 而把写错的类型显示成 real，而真正调用时又悄悄降级 —— 两端
    # 都不诚实。现在调用处直接抛错，状态面同步反映 invalid。
    if ptype not in ProviderFactory._providers:
        return {
            "type": ptype,
            "mode": "invalid",
            "simulated": simulated,
            "source": source,
            "error": (
                f"Invalid provider type {ptype!r}; supported: "
                f"{', '.join(sorted(ProviderFactory._providers))}"
            ),
        }
    mode = provider_mode_for(ptype)
    return {"type": ptype, "mode": mode, "simulated": simulated, "source": source}


# Provider factory and registry
class ProviderFactory:
    """Factory class for creating provider instances."""

    _providers: Dict[str, type] = {
        ProviderType.MOCK: MockProvider,
        ProviderType.OPENAI: OpenAIProvider,
        ProviderType.ANTHROPIC: AnthropicProvider,
        ProviderType.GOOGLE: GoogleProvider,
        ProviderType.OLLAMA: OllamaProvider,
        ProviderType.MOONSHOT: MoonshotProvider,
        ProviderType.DEEPSEEK: DeepSeekProvider,
    }

    _integrators: Dict[str, type] = {
        Framework.AUTOgen: AutoGenIntegrator,
        Framework.AG2: AG2Integrator,
        Framework.LANGGRAPH: LangGraphIntegrator,
    }

    @classmethod
    def register_provider(cls, name: str, provider_class: type) -> None:
        """Register (or replace) a provider implementation.

        The mapping below is the **built-in defaults**, not a closed set: a new
        vendor or an internal OpenAI-compatible subclass can be wired in without
        editing this module, and the existing 7 keep working unchanged.

        Replacement is deliberate -- a deployment may shadow a built-in -- but
        the replacement must still be a ``BaseProvider`` subclass, otherwise the
        mistake only surfaces at the first ``generate()`` call on a live request.
        """
        if not isinstance(name, str) or not name.strip():
            raise ValueError("provider name must be a non-empty string")
        if not isinstance(provider_class, type) or not issubclass(provider_class, BaseProvider):
            raise TypeError(
                f"provider class for {name!r} must subclass BaseProvider, "
                f"got {provider_class!r}"
            )
        cls._providers[name.strip().lower()] = provider_class

    @classmethod
    def create_provider(cls, provider_type: str = None, **kwargs) -> BaseProvider:
        """Create a provider instance by type."""
        if provider_type is None:
            provider_type = os.environ.get("AI_PROVIDER_TYPE", ProviderType.MOCK)

        provider_class = cls._providers.get(str(provider_type).strip().lower())
        if not provider_class:
            raise ValueError(
                f"Unknown provider type: {provider_type}; supported: "
                f"{', '.join(sorted(cls._providers))}"
            )

        return provider_class(**kwargs)

    @classmethod
    def create_integrator(cls, framework: str, provider: BaseProvider) -> Any:
        """Create an integrator for the specified framework."""
        integrator_class = cls._integrators.get(framework)
        if not integrator_class:
            raise ValueError(f"Unknown framework: {framework}")
        return integrator_class(provider)

    @classmethod
    def get_supported_providers(cls) -> list:
        """Return list of supported provider types."""
        return list(cls._providers.keys())

    @classmethod
    def get_supported_frameworks(cls) -> list:
        """Return list of supported external frameworks."""
        return list(cls._integrators.keys())


# Global provider instance
_provider_instance: BaseProvider = None

#: 每个 provider 认可的密钥环境变量别名（与名册 ``PROVIDER_KEY_ENVS`` 同源）。
#: ``AI_PROVIDER_KEY`` 是通用兜底，优先级最高；别名只在通用键缺失时才读，
#: 因此既有部署（只写 AI_PROVIDER_KEY）行为不变。
PROVIDER_KEY_ALIASES: Dict[str, tuple] = {
    "openai": ("OPENAI_API_KEY",),
    "anthropic": ("ANTHROPIC_API_KEY",),
    "google": ("GOOGLE_API_KEY",),
    "moonshot": ("MOONSHOT_API_KEY",),
    "deepseek": ("DEEPSEEK_API_KEY",),
    "ollama": (),
    "mock": (),
}


class AIProviderConfigError(RuntimeError):
    """provider 配置错误（如 ``AI_PROVIDER_TYPE`` 写错）。

    为什么不在运行期静默降级 mock：静默降级会让界面/健康检查显示"已接入真实
    模型"，而实际每次对话都由 mock 编造结果 —— 这正是本项目反复要消除的
    "假的绿"。配置错误必须显式暴露（启动 warning + 调用处异常 + 健康检查
    标记 invalid），让人立即修正环境变量。
    """


def _resolve_provider_api_key(provider_type: str) -> str:
    """按 ``AI_PROVIDER_KEY`` > provider 专用别名 > 默认值的顺序读密钥。"""
    generic = _provider_env("AI_PROVIDER_KEY")
    if generic:
        return generic
    for alias in PROVIDER_KEY_ALIASES.get(provider_type, ()):
        value = _provider_env(alias)
        if value:
            return value
    return "[REDACTED]"


def _provider_env(key: str, default: str = "") -> str:
    """Resolve a provider env var: ``os.environ`` first, then ``.env`` file.

    ``ConfigManager`` loads ``.env`` into its own config store (without exporting
    to ``os.environ``), so reading only ``os.environ`` here silently ignored any
    ``AI_PROVIDER_*`` values written in ``.env``. Priority:
    ``os.environ`` > ``.env``（经 ConfigManager，键保留原样大小写）> default。
    """
    val = os.environ.get(key)
    if val:
        return val
    try:
        from ..config_manager import get as _cfg_get

        val = _cfg_get(key)
        if val:
            return str(val)
    except Exception:
        # ConfigManager 不可用时静默回退默认值（保持 provider 可用性优先）。
        pass
    return default


@observe("provider.get_provider")
def get_provider() -> BaseProvider:
    """Get the global provider instance, auto-detecting from environment.

    The provider type is determined by the ``AI_PROVIDER_TYPE`` environment
    variable or the ``.env`` file (``os.environ`` wins). Set this to a valid
    ProviderType (openai, anthropic, google, ollama, moonshot, deepseek) to
    use a real provider, or MOCK for development.

    Model-config centre first
    -------------------------
    界面上新增/切换的模型配置（``src.ai.model_config``）**优先**于 ``.env``：
    存在已启用的默认模型时，每次调用都按当前配置即时构造实例，因此保存后
    无需重启即可生效。注册表为空时行为与旧实现逐字节一致（.env + 进程单例）。

    Example:
        export AI_PROVIDER_TYPE=openai
        export AI_PROVIDER_KEY=[REDACTED]
    """
    global _provider_instance

    # 显式注入的实例（测试与嵌入场景）永远优先，不会被注册表覆盖。
    if _provider_instance is not None:
        return _provider_instance

    cfg = _registry_config()
    if cfg is not None:
        return provider_from_config(cfg)

    # Get provider type from env var / .env, default to mock (safe, no
    # fake-key network call; consistent with ProviderFactory.create_provider
    # and detect_provider_type_from_env, which also default to MOCK).
    provider_type = _provider_env("AI_PROVIDER_TYPE", ProviderType.MOCK).lower()

    # 非法类型：显式告警 + 抛错，绝不静默降级成 mock（详见 AIProviderConfigError）。
    if provider_type not in ProviderFactory._providers:
        message = (
            f"Invalid AI_PROVIDER_TYPE={provider_type!r}; "
            f"supported: {', '.join(sorted(ProviderFactory._providers))}. "
            "Refusing to silently downgrade to mock -- fix the environment variable "
            "(or configure a model in the console model manager)."
        )
        warnings.warn(message, RuntimeWarning, stacklevel=2)
        logger.error(message)
        raise AIProviderConfigError(message)

    # Required args for all providers: name and model
    # Use sensible defaults; override with env vars if needed
    name = _provider_env("AI_PROVIDER_NAME", "liuhao-assistant")
    model = _provider_env("AI_PROVIDER_MODEL", "mock-model")
    api_key = _resolve_provider_api_key(provider_type)

    create_kwargs: Dict[str, Any] = {
        "name": name,
        "model": model,
        "api_key": api_key,
    }

    # Opt-in timeout override. Slow upstreams (large hosted models behind a
    # proxy, a busy local runner) need far more than the default; when
    # the var is unset we keep the class default rather than guessing.
    timeout_raw = _provider_env("AI_PROVIDER_TIMEOUT", "")
    if timeout_raw:
        try:
            create_kwargs["timeout"] = float(timeout_raw)
        except ValueError:
            logger.warning(
                "invalid AI_PROVIDER_TIMEOUT=%r; using default", timeout_raw
            )

    first_token_raw = _provider_env("AI_PROVIDER_FIRST_TOKEN_TIMEOUT", "")
    if first_token_raw:
        try:
            create_kwargs["first_token_timeout"] = float(first_token_raw)
        except ValueError:
            logger.warning(
                "invalid AI_PROVIDER_FIRST_TOKEN_TIMEOUT=%r; using default",
                first_token_raw,
            )

    _provider_instance = ProviderFactory.create_provider(provider_type, **create_kwargs)
    if "timeout" in create_kwargs:
        _provider_instance.timeout_source = "env"

    # Provider info (no key material — not even a prefix: stdout/stderr commonly
    # end up in CI logs and support bundles).
    logger.info(
        "Provider initialized: %s (name=%s, model=%s, key=%s)",
        provider_type,
        name,
        model,
        "configured" if api_key else "none",
    )

    return _provider_instance


def provider_from_config(cfg: Dict[str, Any]) -> BaseProvider:
    """按模型配置中心的一条记录即时构造 provider（不缓存、不依赖单例）。

    ``cfg`` 来自 ``ModelConfigStore.resolve()``，其中 ``api_key`` 已由存储层解密。
    该函数**不打印**密钥；异常信息也不包含密钥。
    """
    provider_type = str(cfg.get("provider") or "").strip().lower()
    if provider_type not in ProviderFactory._providers:
        raise ValueError(f"Unknown provider type: {provider_type}")
    kwargs: Dict[str, Any] = {
        "name": cfg.get("name") or "liuhao-assistant",
        "model": cfg.get("model") or "default",
        "api_key": cfg.get("api_key") or "[REDACTED]",
    }
    if cfg.get("base_url"):
        kwargs["base_url"] = cfg["base_url"]
    timeout = cfg.get("timeout")
    if timeout:
        configured = float(timeout)
        # 显式配置必须原样生效：验收脚本用 2 秒超时验证"超时轮不入账"，
        # 静默抬高会让该轮变成"慢成功"。旧 30 秒默认值由存储层一次性迁移。
        kwargs["timeout"] = configured
    provider = ProviderFactory.create_provider(provider_type, **kwargs)
    provider.timeout_source = "registry" if "timeout" in kwargs else "default"
    # 只作为**运行期归属标记**：供网关写"谁用了谁的 Key"的审计，不参与请求。
    provider.model_config_id = cfg.get("id") or ""
    provider.owner_principal = cfg.get("owner_principal") or ""
    return provider


def resolve_provider(
    model_id: Optional[str] = None,
    *,
    visible_to: Optional[Sequence[str]] = None,
    allow_unowned: bool = False,
    allow_owners: Optional[Sequence[str]] = None,
    allow_env_fallback: bool = True,
) -> BaseProvider:
    """按模型配置中心解析 provider；未配置或找不到时回退到 ``.env``。

    每次调用都重新读取配置并构造实例 —— 这就是"保存后立即生效"的实现方式，
    也刻意**不**复用进程级单例（单例会保留旧密钥/旧模型名）。

    ``visible_to`` 是 Key 归属视角：给定后，指向他人 Key 的显式请求会抛出
    ``ModelConfigError(403, KEY_OWNER_DENIED)``，绝不静默回退到 ``.env``。

    ``allow_owners`` 在可见之上再加知情授权：显式 ``model_id`` 指向未开启
    ``allow_superior_key_use`` 的成员 Key 时抛出 ``ACCOUNT_KEY_CONSENT_REQUIRED``。
    自动路由与显式选择因此共用同一套授权口径。

    ``allow_env_fallback=False``（成员账号）时，明明有其他模型却都不可见的情况下
    返回 409，而不是拿机器级 ``.env`` Key 顶上 —— 成员只能用自己的 Key。
    单账号老部署（``allow_env_fallback=True``）行为不变。
    """
    cfg = _registry_config(
        model_id,
        visible_to=visible_to,
        allow_unowned=allow_unowned,
        allow_owners=allow_owners,
    )
    if cfg is not None:
        return provider_from_config(cfg)
    if visible_to is not None and (not allow_env_fallback or _has_enabled_models()):
        raise _no_visible_model_error()
    return get_provider()


def _has_enabled_models() -> bool:
    """配置中心里是否存在任何启用的模型（区分"空库"与"全不可见"）。"""
    try:
        from .model_config import get_model_config_store

        return bool(get_model_config_store().list_models())
    except Exception:  # noqa: BLE001
        return False


def _no_visible_model_error():
    from .model_config import ModelConfigError

    return ModelConfigError(
        409,
        "当前工作区暂未配置可用模型，请先添加自己的模型",
        code="ACCOUNT_KEY_NOT_CONFIGURED",
    )


def _registry_config(
    model_id: Optional[str] = None,
    *,
    visible_to: Optional[Sequence[str]] = None,
    allow_unowned: bool = False,
    allow_owners: Optional[Sequence[str]] = None,
) -> Optional[Dict[str, Any]]:
    """读取模型配置中心；任何存储异常都回退到 ``.env``（并记 warning）。

    回退而不是抛出的理由：模型配置中心是**新增**能力，它的故障不应让原本可用的
    ``.env`` 部署整体瘫痪。异常本身仍然可见（日志），不会被静默吞掉。

    唯一例外是权限类 ``ModelConfigError``：它必须原样上抛，不能被"回退"吞掉。
    """
    try:
        from .model_config import ModelConfigError, get_model_config_store

        return get_model_config_store().resolve(
            model_id,
            visible_to=visible_to,
            allow_unowned=allow_unowned,
            allow_owners=allow_owners,
        )
    except ModelConfigError:
        raise
    except Exception as exc:  # pragma: no cover - 存储故障
        logger.warning(
            "model config store unavailable (%s); falling back to .env",
            type(exc).__name__,
        )
        return None


def set_provider(provider: BaseProvider) -> None:
    """Set the global provider instance explicitly."""
    global _provider_instance
    _provider_instance = provider


def reset_provider() -> None:
    """Reset the global provider instance."""
    global _provider_instance
    _provider_instance = None

# Provider type detection helper


def detect_provider_type_from_env() -> str:
    """Detect and return provider type from environment variables."""
    provider_type = os.environ.get("AI_PROVIDER_TYPE", ProviderType.MOCK)
    return provider_type

# Structured output utility functions


def apply_structured_output(provider_name: str, schema: dict, response_text: str) -> dict:
    """Apply structured output validation to LLM response text.

    Maps Pydantic/JSON schemas to provider-specific structured output formats.
    Supports OpenAI response_format with json_schema, Anthropic structured outputs,
    and falls back to json_object with prompt-level JSON parsing.

    Args:
        provider_name: Name of the provider (openai, anthropic, google, etc.)
        schema: Pydantic model or JSON Schema dict to validate against
        response_text: Raw text response from LLM

    Returns:
        Parsed and validated dictionary, or raises ValueError if validation fails

    Example:
        >>> from pydantic import BaseModel
        >>> class SearchResult(BaseModel):
        >>>     keywords: list[str]
        >>>     summary: str
        >>>
        >>> result = apply_structured_output(
        ...     "openai",
        ...     SearchModel.model_json_schema(),
        ...     llm_response_text
        ... )
    """
    # Try OpenAI response_format approach first
    if provider_name == "openai":
        try:
            parsed = json.loads(response_text)
            # Basic validation - check required keys exist
            if isinstance(schema, dict) and "properties" in schema:
                properties = schema.get("properties", {})
                for prop_name, prop_schema in properties.items():
                    if prop_name not in parsed:
                        raise ValueError(f"Missing required field: {prop_name}")
                    # Type checking basics
                    expected_type = prop_schema.get("type", "string")
                    if expected_type == "integer" and not isinstance(parsed[prop_name], int):
                        raise ValueError(f"Field {prop_name} should be integer, got {type(parsed[prop_name])}")
                    elif expected_type == "number" and not isinstance(parsed[prop_name], (int, float)):
                        raise ValueError(f"Field {prop_name} should be number, got {type(parsed[prop_name])}")
                    elif expected_type == "string" and not isinstance(parsed[prop_name], str):
                        raise ValueError(f"Field {prop_name} should be string, got {type(parsed[prop_name])}")
            return parsed
        except (json.JSONDecodeError, ValueError):
            pass

    # Fallback: parse JSON from text (handle markdown fences, etc.)
    try:
        # Remove markdown code fences if present
        cleaned = response_text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            # Find first non-fence line
            start_idx = 0
            if lines[0].strip() == "```":
                start_idx = 1
            if lines[-1].strip() == "```":
                end_idx = len(lines) - 1
            cleaned = "\n".join(lines[start_idx:end_idx]).strip()

        # Remove leading/trailing text before first {
        brace_idx = cleaned.find("{")
        if brace_idx >= 0:
            cleaned = cleaned[brace_idx:]

        # Try to find closing }
        last_brace = cleaned.rfind("}")
        if last_brace >= 0:
            cleaned = cleaned[:last_brace + 1]

        parsed = json.loads(cleaned)

        # Basic schema validation
        if isinstance(schema, dict) and "properties" in schema:
            properties = schema.get("properties", {})
            for prop_name in properties:
                if prop_name not in parsed:
                    raise ValueError(f"Missing required field: {prop_name}")
        return parsed
    except (json.JSONDecodeError, ValueError) as e:
        # Last resort: return raw text wrapped in dict
        return {"raw_response": response_text, "error": str(e)}

# Convenience functions for external framework usage


def with_autogen(func):
    """Decorator to wrap functions for AutoGen integration."""
    def wrapper(*args, **kwargs):
        provider = get_provider()
        integrator = ProviderFactory.create_integrator(Framework.AUTOgen, provider)
        return integrator.generate_response(kwargs.get("messages", []), **kwargs)
    return wrapper


def with_ag2(func):
    """Decorator to wrap functions for AG2 integration."""
    def wrapper(*args, **kwargs):
        provider = get_provider()
        integrator = ProviderFactory.create_integrator(Framework.AG2, provider)
        return integrator.dispatch_task(kwargs.get("agent", "default"), str(kwargs.get("task", "")), **kwargs)
    return wrapper


def with_langgraph(func):
    """Decorator to wrap functions for LangGraph integration."""
    def wrapper(*args, **kwargs):
        provider = get_provider()
        integrator = ProviderFactory.create_integrator(Framework.LANGGRAPH, provider)
        return integrator.add_node(kwargs.get("node_name", "default"), func)
    return wrapper
