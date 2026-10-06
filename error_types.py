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
        super().__init__(
            f"Provider {provider} returned an empty response "
            f"({detail}; attempts={attempts}). No model output was produced, so "
            "this is reported as a FAILURE rather than an empty success.",
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
    """将任意异常分类为ProviderError"""
    import re
    msg = str(exc).lower()
    
    if "rate limit" in msg or "429" in msg:
        return ProviderRateLimitError(provider)
    elif "api error" in msg or "invalid key" in msg or "401" in msg or "403" in msg:
        return ProviderAPIError(provider, str(exc))
    elif "not found" in msg or "404" in msg:
        return ProviderAPIError(provider, str(exc), 404)
    elif "connection" in msg or "timeout" in msg:
        return ProviderAPIError(provider, str(exc), "timeout")
    else:
        return ProviderError(str(exc), provider)
