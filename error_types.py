"""统一错误类体系 for LiuHao AI OS."""

class LiuHaoError(Exception):
    """LiuHao AI OS 基础异常"""
    def __init__(self, message: str, error_code: str = "UNKNOWN", severity: str = "medium"):
        self.message = message
        self.error_code = error_code
        self.severity = severity  # low, medium, high, critical
        super().__init__(self.message)

class ProviderError(LiuHaoError):
    """Provider基础异常"""
    def __init__(self, message: str, provider: str = "unknown", error_code: str = "PROVIDER_ERROR", severity: str = "medium"):
        self.provider = provider
        super().__init__(f"[{provider}] {message}", error_code, severity)

class ProviderAPIError(ProviderError):
    """Provider API 请求错误"""
    def __init__(self, provider: str, api_error: str, status_code: int = None):
        self.api_error = api_error
        self.status_code = status_code
        super().__init__(
            f"Provider {provider} API error: {api_error}",
            provider,
            "API_ERROR"
        )

class ProviderRateLimitError(ProviderError):
    """速率限制错误"""
    def __init__(self, provider: str, retry_after: int = None):
        self.retry_after = retry_after
        super().__init__(
            f"Provider {provider} rate limit exceeded",
            provider,
            "RATE_LIMIT_ERROR"
        )

class ProviderEmptyResponseError(ProviderError):
    """Provider returned a syntactically valid but EMPTY completion.

    Why this exists
    ---------------
    Several providers normalise a missing completion to ``""`` (e.g.
    ``response.choices[0].message.content or ""``) because the SDK type permits
    ``None``. Returning that ``""`` as a normal result is a silent fake success:
    the caller reports the task as completed and stores an empty string as if it
    were the model's answer. Nothing downstream can tell "the model said
    nothing" from "the model said something".

    It is deliberately distinct from :class:`ProviderAPIError` -- the request
    SUCCEEDED (HTTP 200, valid envelope). The failure is that no usable content
    came back, which is a different operator action (check model filters,
    content policy, max_tokens truncation, or upstream health) than a transport
    failure. Collapsing the two would make the diagnosis misleading.
    """

    def __init__(self, provider: str, detail: str = "empty response", attempts: int = 1):
        self.detail = detail
        self.attempts = attempts
        # 用户可见的结论必须是中文、可行动；技术细节放在括号里供排障使用。
        self.user_message = "模型没有返回内容，请重试或换一个模型。"
        super().__init__(
            f"{self.user_message}（提供方：{provider}；{detail}；尝试 {attempts} 次）",
            provider,
            "EMPTY_RESPONSE",
            severity="high",
        )


class ConfigurationError(LiuHaoError):
    """配置错误"""
    def __init__(self, message: str, config_key: str = None):
        self.config_key = config_key
        super().__init__(f"Configuration error: {message}", "CONFIG_ERROR")

class MemoryError(LiuHaoError):
    """Memory系统错误"""
    def __init__(self, message: str, backend: str = "unknown"):
        self.backend = backend
        super().__init__(f"Memory error [{backend}]: {message}", "MEMORY_ERROR")

# 工厂函数，根据环境自动分类错误
def classify_error(exc: Exception, provider: str = "unknown") -> ProviderError:
    """将任意异常分类为 ProviderError。

    **只信机器信号**：优先读 provider 在**脱敏前**算好的
    ``upstream_kind``，其次读 ``status_code``；不再对已经中性化的消息做
    字符串二次分类。

    旧实现会对异常消息匹配 ``"429"``/``"401"`` 等数字：可中性化文案本身就
    含 "HTTP 429"，于是"额度耗尽"被一律退化成 ``rate_limited``，401 也会
    被错误归入 ``API_ERROR`` 后重试 4 次。
    """
    kind = getattr(exc, "upstream_kind", None)
    status = getattr(exc, "status_code", None)

    if isinstance(kind, str) and kind:
        if kind == "rate_limited":
            return ProviderRateLimitError(
                provider, retry_after=getattr(exc, "retry_after", None)
            )
        if kind == "quota_exhausted":
            return ProviderAPIError(provider, str(exc), 402)
        if kind in ("invalid_key", "key_revoked"):
            return ProviderAPIError(provider, str(exc), status if isinstance(status, int) else 401)
        if kind == "model_not_found":
            return ProviderAPIError(provider, str(exc), 404)
        if kind == "incomplete_stream":
            return ProviderError(str(exc), provider, "STREAM_INCOMPLETE", "medium")
        return ProviderAPIError(provider, str(exc), status if isinstance(status, int) else None)

    if isinstance(status, int):
        if status == 429:
            return ProviderRateLimitError(provider)
        if status in (401, 403):
            return ProviderAPIError(provider, str(exc), status)
        if status == 404:
            return ProviderAPIError(provider, str(exc), 404)
        return ProviderAPIError(provider, str(exc), status)

    # 没有机器信号的非 HTTP 失败：只按异常类型/短语判断，**不匹配数字**。
    msg = str(exc).lower()
    if isinstance(exc, TimeoutError) or "timeout" in type(exc).__name__.lower():
        return ProviderAPIError(provider, "timeout", None)
    if isinstance(exc, (ConnectionError, OSError)) or "connection" in msg:
        return ProviderAPIError(provider, "connection error", None)
    if "rate limit" in msg or "too many requests" in msg:
        return ProviderRateLimitError(provider)
    if "invalid api key" in msg or "invalid key" in msg or "unauthorized" in msg:
        return ProviderAPIError(provider, str(exc), 401)
    if "model not found" in msg or "does not exist" in msg:
        return ProviderAPIError(provider, str(exc), 404)
    return ProviderError(str(exc), provider)
