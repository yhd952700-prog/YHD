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
    ):
        super().__init__(message)
        self.status_code = status_code
        self.upstream_kind = upstream_kind


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
        code = raw if isinstance(raw, int) else None
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
    return "upstream_error"


def _raise_upstream_http(label: str, response: Any) -> None:
    """上游非 200 统一成中性异常，并保留脱敏前的机器分类。

    直接把 ``response.text`` 拼进异常会把上游英文原文（含内部地址/账号信息）
    带到用户侧；分类必须在丢弃原文之前完成。
    """
    status = getattr(response, "status_code", 0)
    kind = classify_upstream_error(status=status, text=response.text)
    logger.info("%s upstream HTTP failure status=%s kind=%s", label, status, kind)
    raise ProviderHTTPError(
        _neutral_http_message(label, status),
        status_code=status,
        upstream_kind=kind,
    )


def _neutralize_http_error(exc: BaseException, label: str = "模型服务") -> None:
    """把 SDK 的 HTTP 错误消息换成中性文案；非 HTTP 错误原样返回给调用方。

    分类在脱敏之前完成：上游原文可能含密钥/内网地址，所以只保留
    ``upstream_kind`` 与状态码，其余丢弃（不进异常消息、不进日志正文）。
    """
    status = getattr(exc, "status_code", None)
    if not isinstance(status, int):
        return
    kind = classify_upstream_error(exc, status=status)
    logger.info("%s upstream HTTP failure status=%s kind=%s", label, status, kind)
    raise ProviderHTTPError(
        _neutral_http_message(label, status),
        status_code=status,
        upstream_kind=kind,
    ) from exc

# 用户可见的中性失败文案：原始异常（URL、堆栈、英文错误）只进日志与诊断，
# 绝不直接展示给用户，也不写进会话历史。
GENERATION_FAILURE_MESSAGE = (
    "这次没有拿到回复，请稍后再试。若持续失败，请到「设置 → 模型」检查 API Key 与网络连接。"
)

# 未配置模型时的演示数据提示：明确告诉用户发生了什么、下一步怎么做，
# 不再输出 provider 内部状态转储。
MODEL_NOT_CONFIGURED_MESSAGE = (
    "[演示数据] 还没有配置模型，因此无法生成真实回答。"
    "请到「设置 → 模型」填写 API Key（或配置 AI_PROVIDER_TYPE），然后重新发送这条消息。"
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
        timeout: float = 30.0,
        max_retries: int = 3,
        retry_base_delay: float = 1.0,
        base_url: Optional[str] = None,
    ):
        self.name = name
        self.model = model
        self.api_key = api_key or os.environ.get("AI_PROVIDER_KEY", "[REDACTED]")
        # 模型配置中心保存的 base_url 需要随实例走：各 provider 的 ``generate``
        # 历史上只读 ``os.environ``/kwargs，导致"界面上填的 base_url"不生效。
        self.base_url = base_url or None
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_base_delay = retry_base_delay
        self._capabilities = ProviderCapabilities()

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

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
        from error_types import (
            ProviderEmptyResponseError,
            ProviderRateLimitError,
            classify_error,
        )

        last_error = None
        last_empty_detail = ""
        for attempt in range(self.max_retries + 1):
            try:
                response = self.generate(prompt, **kwargs)
            except Exception as e:
                last_error = classify_error(e, self.name)

                # Don't retry on certain errors
                if isinstance(last_error, ProviderRateLimitError):
                    if attempt < self.max_retries and last_error.retry_after:
                        time.sleep(min(last_error.retry_after, 60))
                        continue
                    # If no retry_after or max retries reached, raise
                    raise
                elif last_error.error_code == "API_ERROR":
                    # Retry on 5xx errors, not on 4xx
                    if hasattr(last_error, 'status_code') and isinstance(last_error.status_code, int):
                        if 400 <= last_error.status_code < 500:
                            raise

                if attempt < self.max_retries:
                    delay = self.retry_base_delay * (2 ** attempt)
                    time.sleep(delay)
                    continue
                else:
                    raise
                continue

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

    def chat(self, messages, **kwargs) -> str:
        """Chat-style generation over a message list.

        Default implementation collapses ``messages`` (list of
        ``{"role": ..., "content": ...}`` dicts) into a single text prompt and
        delegates to ``generate``. Chat-native providers (e.g. Ollama) override
        this to use their ``/api/chat`` endpoint for higher multi-turn quality.
        """
        prompt = "\n".join(
            f'{m.get("role", "user")}: {m.get("content", "")}'
            for m in messages
            if isinstance(m, dict)
        )
        return self.generate(prompt, **kwargs)

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
        """
        yield self.chat(messages, **kwargs)

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
                    timeout=self.timeout,
                    proxy=self.proxy or None,
                )

                self._client = openai.OpenAI(
                    api_key=self.api_key,
                    base_url=self.base_url,
                    # Every other provider hands ``self.timeout`` to its
                    # transport; this one used to leave the SDK default (10 min)
                    # in place, so the declared 30s timeout was silently ignored.
                    timeout=self.timeout,
                    http_client=http_client,
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
            return response.choices[0].message.content or ""
        except Exception as exc:
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
            return response.choices[0].message.content or ""
        except Exception as exc:
            self._guard_upstream_error(exc)
            raise

    def chat_stream(self, messages, **kwargs):
        """按块流式返回 OpenAI 兼容响应（``stream=True``）。

        没有流式能力的自建网关可能忽略该参数并一次返回全文，此时 SDK 仍会
        给出一个完整的 chunk，循环逻辑不受影响。
        """
        model = kwargs.get("model", self.model)
        temperature = kwargs.get("temperature", 0.7)
        max_tokens = kwargs.get("max_tokens", 1000)
        self._guard_target()
        client = self._get_client()
        try:
            stream = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
            )
        except Exception as exc:
            # 429/401 在建立流阶段就失败：中性化并保留分类，绝不能吞掉重试，
            # 否则"额度耗尽 / Key 吊销"会被兜底成笼统失败。
            self._guard_upstream_error(exc)
            raise
        try:
            for chunk in stream:
                choices = getattr(chunk, "choices", None) or []
                if not choices:
                    continue
                delta = getattr(choices[0], "delta", None)
                content = getattr(delta, "content", None) if delta is not None else None
                if content:
                    yield content
        except Exception as exc:
            # 建流后中途报错（如流内返回 429）同样要中性化并带上分类。
            self._guard_upstream_error(exc)
            raise


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
        import httpx

        with self._client_lock:
            if self._client is None:
                options = {
                    "api_key": self.api_key,
                    # 与 OpenAI 兼容路径同一条策略：不跟随重定向。
                    "http_client": httpx.Client(
                        follow_redirects=False, timeout=self.timeout
                    ),
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

    def generate(self, prompt: str, **kwargs) -> str:
        """Generate response via Anthropic API."""
        model = kwargs.get("model", self.model)
        assert_safe_runtime_target(self.base_url, context="Anthropic 服务")
        try:
            client = self._get_client()
            # v1.x SDK approach - simplified
            response = client.messages.create(
                model=model,
                max_tokens=kwargs.get("max_tokens", 1000),
                # temperature removed for compatibility - use system prompt instead
                messages=[{"role": "user", "content": prompt}],
            )
            return response.content[0].text
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
        except ModuleNotFoundError as exc:  # pragma: no cover - environment guard
            raise RuntimeError(
                "GoogleProvider requires the 'google-genai' package "
                "(pip install 'google-genai'); the legacy "
                "'google-generativeai' SDK is not supported because it pins "
                "protobuf<6, which conflicts with the shipped protobuf 6.x."
            ) from exc

        model = kwargs.get("model", self.model)
        try:
            client = genai.Client(api_key=self.api_key)
            response = client.models.generate_content(model=model, contents=prompt)
            return response.text
        except Exception:
            # Re-raise to let generate_with_retry handle it
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
                timeout=self.timeout,
                proxies={"http": None, "https": None},
                allow_redirects=False,
            )
            if response.status_code == 200:
                result = response.json()
                return result.get("response", str(result))
            logger.info(
                "ollama upstream HTTP failure status=%s model=%s body=%s",
                response.status_code,
                model,
                str(response.text)[:200],
            )
            raise ProviderHTTPError(
                _neutral_http_message("Ollama 服务", response.status_code),
                status_code=response.status_code,
                upstream_kind=classify_upstream_error(
                    status=response.status_code, text=response.text
                ),
            )
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
                timeout=self.timeout,
                proxies={"http": None, "https": None},
                allow_redirects=False,
            )
            if response.status_code == 200:
                result = response.json()
                return result.get("message", {}).get("content", str(result))
            logger.info(
                "ollama upstream HTTP failure status=%s model=%s body=%s",
                response.status_code,
                model,
                str(response.text)[:200],
            )
            raise ProviderHTTPError(
                _neutral_http_message("Ollama 服务", response.status_code),
                status_code=response.status_code,
                upstream_kind=classify_upstream_error(
                    status=response.status_code, text=response.text
                ),
            )
        except ProviderHTTPError:
            raise
        except Exception:
            # Re-raise to let generate_with_retry handle it
            raise

    def chat_stream(self, messages, **kwargs):
        """Stream tokens via Ollama's native ``/api/chat`` (NDJSON, stream=True).

        Each response line is a JSON object whose ``message.content`` is the
        incremental token; the final frame has ``done: true``. Lines are parsed
        defensively — malformed frames are skipped rather than aborting the
        stream (an honest best-effort for a local NDJSON source).
        """
        import json as _json

        import requests

        model = kwargs.get("model", self.model)
        base_url = kwargs.get("base_url") or self.base_url or os.environ.get(
            "OLLAMA_BASE_URL", "http://localhost:11434"
        )
        assert_safe_runtime_target(
            base_url, context="Ollama 服务", allow_private_service=True
        )

        response = requests.post(
            f"{base_url}/api/chat",
            json={"model": model, "messages": messages, "stream": True},
            timeout=self.timeout,
            proxies={"http": None, "https": None},
            stream=True,
            allow_redirects=False,
        )
        try:
            if response.status_code != 200:
                logger.info(
                    "ollama upstream HTTP failure status=%s model=%s body=%s",
                    response.status_code,
                    model,
                    str(response.text)[:200],
                )
                raise ProviderHTTPError(
                    _neutral_http_message("Ollama 服务", response.status_code),
                    status_code=response.status_code,
                    upstream_kind=classify_upstream_error(
                        status=response.status_code, text=response.text
                    ),
                )

            for line in response.iter_lines():
                if not line:
                    continue
                try:
                    chunk = _json.loads(line)
                except _json.JSONDecodeError:
                    continue
                if chunk.get("done"):
                    break
                content = chunk.get("message", {}).get("content", "")
                if content:
                    yield content
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

    def generate(self, prompt: str, **kwargs) -> str:
        """Generate response via Moonshot API."""
        import requests

        model = kwargs.get("model", self.model)
        base_url = kwargs.get("base_url") or self.base_url or os.environ.get(
            "MOONSHOT_BASE_URL", "https://api.moonshot.ai/v1"
        )
        api_key = self.api_key or os.environ.get("MOONSHOT_API_KEY", "[REDACTED]")

        try:
            headers = {"Authorization": f"Bearer {api_key}"} if api_key != "[REDACTED]" else {}
            response = requests.post(
                f"{base_url}/chat/completions",
                json={"model": model, "messages": [{"role": "user", "content": prompt}]},
                headers=headers,
                timeout=self.timeout,
            )
            if response.status_code == 200:
                result = response.json()
                return result.get("choices", [{}])[0].get("message", {}).get("content", str(result))
            else:
                raise Exception(f"Moonshot error {response.status_code}: {response.text}")
        except Exception:
            # Re-raise to let generate_with_retry handle it
            raise


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
                timeout=self.timeout,
                stream=stream,
                proxies={"http": None, "https": None},
                allow_redirects=False,
            )
        except requests.exceptions.RequestException as exc:
            logger.warning("DeepSeek request failed: %s", type(exc).__name__)
            raise ProviderHTTPError("DeepSeek 服务不可达，请检查端点与网络。") from exc
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
        _model, response = self._post(messages, stream=False, **kwargs)
        try:
            result = response.json()
        finally:
            response.close()
        return result.get("choices", [{}])[0].get("message", {}).get("content", str(result))

    def chat_stream(self, messages, **kwargs):
        """真实 SSE 流式（``stream=true``），逐 token 产出。"""
        import json as _json

        _model, response = self._post(messages, stream=True, **kwargs)
        try:
            for line in response.iter_lines():
                if not line:
                    continue
                text = line.decode("utf-8", "ignore") if isinstance(line, bytes) else line
                if not text.startswith("data:"):
                    continue
                payload = text[5:].strip()
                if payload == "[DONE]":
                    break
                if not payload:
                    continue
                try:
                    chunk = _json.loads(payload)
                except _json.JSONDecodeError:
                    continue
                for choice in chunk.get("choices") or []:
                    content = (choice.get("delta") or {}).get("content")
                    if content:
                        yield content
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
    # proxy, a busy local runner) need far more than the 30s default; when
    # the var is unset we keep the class default rather than guessing.
    timeout_raw = _provider_env("AI_PROVIDER_TIMEOUT", "")
    if timeout_raw:
        try:
            create_kwargs["timeout"] = float(timeout_raw)
        except ValueError:
            logger.warning(
                "invalid AI_PROVIDER_TIMEOUT=%r; using default", timeout_raw
            )

    _provider_instance = ProviderFactory.create_provider(provider_type, **create_kwargs)

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
        kwargs["timeout"] = float(timeout)
    provider = ProviderFactory.create_provider(provider_type, **kwargs)
    # 只作为**运行期归属标记**：供网关写"谁用了谁的 Key"的审计，不参与请求。
    provider.model_config_id = cfg.get("id") or ""
    provider.owner_principal = cfg.get("owner_principal") or ""
    return provider


def resolve_provider(
    model_id: Optional[str] = None,
    *,
    visible_to: Optional[Sequence[str]] = None,
    allow_unowned: bool = False,
    allow_env_fallback: bool = True,
) -> BaseProvider:
    """按模型配置中心解析 provider；未配置或找不到时回退到 ``.env``。

    每次调用都重新读取配置并构造实例 —— 这就是"保存后立即生效"的实现方式，
    也刻意**不**复用进程级单例（单例会保留旧密钥/旧模型名）。

    ``visible_to`` 是 Key 归属视角：给定后，指向他人 Key 的显式请求会抛出
    ``ModelConfigError(403, KEY_OWNER_DENIED)``，绝不静默回退到 ``.env``。

    ``allow_env_fallback=False``（成员账号）时，明明有其他模型却都不可见的情况下
    返回 409，而不是拿机器级 ``.env`` Key 顶上 —— 成员只能用自己的 Key。
    单账号老部署（``allow_env_fallback=True``）行为不变。
    """
    cfg = _registry_config(
        model_id, visible_to=visible_to, allow_unowned=allow_unowned
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
) -> Optional[Dict[str, Any]]:
    """读取模型配置中心；任何存储异常都回退到 ``.env``（并记 warning）。

    回退而不是抛出的理由：模型配置中心是**新增**能力，它的故障不应让原本可用的
    ``.env`` 部署整体瘫痪。异常本身仍然可见（日志），不会被静默吞掉。

    唯一例外是权限类 ``ModelConfigError``：它必须原样上抛，不能被"回退"吞掉。
    """
    try:
        from .model_config import ModelConfigError, get_model_config_store

        return get_model_config_store().resolve(
            model_id, visible_to=visible_to, allow_unowned=allow_unowned
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
