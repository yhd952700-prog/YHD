"""Provider Adapter for Model Gateway

Wraps various AI model providers with standardized interface.

Spec items 181-182: Provider Adapter supports:
- Provider type enumeration
- Capability metadata
- Standardized request/response
- Streaming support
- Structured output
- Tool calling
"""

from __future__ import annotations

import abc
import json
import time
from typing import (
    Any,
    Optional,
    Dict,
    List,
    Tuple,
    Union,
    Callable,
    Awaitable,
)


class ProviderType:
    """Enumeration of supported provider types."""

    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GOOGLE = "google"
    OLLAMA = "ollama"
    MOONSHOT = "moonshot"
    DEEPSEEK = "deepseek"
    MOCK = "mock"


class ProviderCapabilities:
    """Standardized capability metadata for providers."""

    def __init__(
        self,
        *,
        supports_streaming: bool = False,
        supports_structured_output: bool = False,
        supports_function_calling: bool = False,
        supports_vision: bool = False,
        supports_embeddings: bool = False,
        max_context_tokens: int = 4096,
        max_output_tokens: int = 1024,
        rate_limit_rpm: int = 60,
        rate_limit_tpm: int = 10000,
        **kwargs: Any,
    ):
        self.supports_streaming = supports_streaming
        self.supports_structured_output = supports_structured_output
        self.supports_function_calling = supports_function_calling
        self.supports_vision = supports_vision
        self.supports_embeddings = supports_embeddings
        self.max_context_tokens = max_context_tokens
        self.max_output_tokens = max_output_tokens
        self.rate_limit_rpm = rate_limit_rpm
        self.rate_limit_tpm = rate_limit_tpm


class ProviderAdapter(abc.ABC):
    """Abstract base class for all model provider adapters.

    Every concrete provider adapter must implement:
    - completion(): Generate a completion
    - streaming_completion(): Generate a streaming completion
    - capabilities(): Return provider capabilities
    - validate_request(): Validate a request before sending
    """

    def __init__(
        self,
        provider_type: ProviderType,
        model_name: str,
        capabilities: Optional[ProviderCapabilities] = None,
        **kwargs: Any,
    ):
        self.provider_type = provider_type
        self.model_name = model_name
        self.capabilities = capabilities or ProviderCapabilities()
        self._health: bool = True
        self._last_request_time: Optional[float] = None
        self._daily_request_count: int = 0
        self._monthly_request_count: int = 0

    @abc.abstractmethod
    async def completion(
        self,
        messages: List[Dict[str, Any]],
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Generate a completion.

        Returns dict with at least:
        - content: str
        - model: str
        - usage: Dict[str, int] (prompt_tokens, completion_tokens, total_tokens)
        - finished_reason: Optional[str]
        """
        ...

    @abc.abstractmethod
    async def streaming_completion(
        self,
        messages: List[Dict[str, Any]],
        **kwargs: Any,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Generate a streaming completion.

        Yields dict with at least:
        - content: str (accumulated or per-chunk)
        - model: str
        - done: bool
        """
        ...

    @abc.abstractmethod
    def capabilities(self) -> ProviderCapabilities:
        """Return provider capabilities."""
        ...

    @abc.abstractmethod
    def validate_request(self, request: Dict[str, Any]) -> Tuple[bool, str]:
        """Validate a request before sending.

        Returns (is_valid, error_message).
        """
        ...

    def health(self) -> bool:
        """Return provider health status."""
        return self._health

    def set_health(self, healthy: bool) -> None:
        """Set provider health status."""
        self._health = healthy

    def record_request(self) -> None:
        """Record a request for rate limiting."""
        self._last_request_time = time.time()
        self._daily_request_count += 1
        self._monthly_request_count += 1


class OpenAIAdapter(ProviderAdapter):
    """Adapter for OpenAI providers (gpt-4, gpt-3.5, etc.)."""

    def __init__(
        self,
        model_name: str,
        api_key: str,
        **kwargs: Any,
    ):
        super().__init__(ProviderType.OPENAI, model_name, **kwargs)
        self.api_key = api_key

    async def completion(
        self,
        messages: List[Dict[str, Any]],
        **kwargs: Any,
    ) -> Dict[str, Any]:
        import openai

        client = openai.AzureOpenAI(
            api_key=self.api_key,
            model=self.model_name,
        ) if self.model_name.startswith("gpt-4-a") else openai.OpenAI(
            api_key=self.api_key,
        )

        response = await client.chat.completions.create(
            model=self.model_name,
            messages=messages,
            **{k: v for k, v in kwargs.items() if k in [
                "temperature", "max_tokens", "top_p", "frequency_penalty",
                "presence_penalty", "stream"
            ]}
        )

        return {
            "content": response.choices[0].message.content,
            "model": response.model,
            "usage": {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            },
            "finished_reason": response.choices[0].finish_reason,
        }

    async def streaming_completion(
        self,
        messages: List[Dict[str, Any]],
        **kwargs: Any,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        import openai

        client = openai.OpenAI(
            api_key=self.api_key,
        )

        stream = await client.chat.completions.create(
            model=self.model_name,
            messages=messages,
            stream=True,
        )

        async for chunk in stream:
            delta = chunk.choices[0].delta
            content = delta.content or ""

            yield {
                "content": content,
                "model": self.model_name,
                "done": False,
            }

        yield {
            "content": "",
            "model": self.model_name,
            "done": True,
        }

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            supports_streaming=True,
            supports_structured_output=True,
            supports_function_calling=True,
            max_context_tokens=self.capabilities.max_context_tokens,
            max_output_tokens=self.capabilities.max_output_tokens,
        )

    def validate_request(self, request: Dict[str, Any]) -> Tuple[bool, str]:
        if "messages" not in request or not request["messages"]:
            return False, "messages is required"
        if "model" not in request:
            return False, "model is required"
        return True, ""


class AnthropicAdapter(ProviderAdapter):
    """Adapter for Anthropic providers."""

    def __init__(
        self,
        model_name: str,
        api_key: str,
        **kwargs: Any,
    ):
        super().__init__(ProviderType.ANTHROPIC, model_name, **kwargs)
        self.api_key = api_key

    async def completion(
        self,
        messages: List[Dict[str, Any]],
        **kwargs: Any,
    ) -> Dict[str, Any]:
        import anthropic

        client = anthropic.AsyncAnthropic(
            api_key=self.api_key,
        )

        response = await client.completions.create(
            model=self.model_name,
            max_tokens_to_sample=kwargs.get("max_tokens", 1024),
            temperature=kwargs.get("temperature", 0.5),
            prompt=self._format_anthropic_prompt(messages),
        )

        return {
            "content": response.completion,
            "model": self.model_name,
            "usage": {
                "prompt_tokens": response.usage.input_tokens,
                "completion_tokens": response.usage.output_tokens,
                "total_tokens": response.usage.input_tokens + response.usage.output_tokens,
            },
            "finished_reason": "stop",
        }

    def _format_anthropic_prompt(self, messages: List[Dict[str, Any]]) -> str:
        # Simplified Anthropic prompt formatting
        parts = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            parts.append(f"{role}: {content}")
        return "\n\n".join(parts)

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            supports_streaming=True,
            supports_structured_output=True,
            supports_function_calling=True,
        )

    def validate_request(self, request: Dict[str, Any]) -> Tuple[bool, str]:
        if "messages" not in request or not request["messages"]:
            return False, "messages is required"
        return True, ""


class MockAdapter(ProviderAdapter):
    """Mock provider adapter for development and testing."""

    def __init__(
        self,
        model_name: str = "mock-model",
        **kwargs: Any,
    ):
        super().__init__(ProviderType.MOCK, model_name, **kwargs)

    async def completion(
        self,
        messages: List[Dict[str, Any]],
        **kwargs: Any,
    ) -> Dict[str, Any]:
        # Return a deterministic mock response
        last_user_msg = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                last_user_msg = msg.get("content", "")
                break

        mock_content = f"Mock response to: {last_user_msg[:50]}{'...' if len(last_user_msg) > 50 else ''}"

        return {
            "content": mock_content,
            "model": self.model_name,
            "usage": {
                "prompt_tokens": sum(len(m.get("content", "").split()) for m in messages),
                "completion_tokens": len(mock_content.split()),
                "total_tokens": sum(len(m.get("content", "").split()) for m in messages) + len(mock_content.split()),
            },
            "finished_reason": "stop",
        }

    async def streaming_completion(
        self,
        messages: List[Dict[str, Any]],
        **kwargs: Any,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        yield {
            "content": "Mock response start",
            "model": self.model_name,
            "done": False,
        }
        yield {
            "content": "Mock response content",
            "model": self.model_name,
            "done": False,
        }
        yield {
            "content": "",
            "model": self.model_name,
            "done": True,
        }

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            supports_streaming=True,
            supports_structured_output=False,
            supports_function_calling=False,
        )

    def validate_request(self, request: Dict[str, Any]) -> Tuple[bool, str]:
        if "messages" not in request or not request["messages"]:
            return False, "messages is required"
        return True, ""


# Async generator typing for Python < 3.9
if hasattr(__builtins__, "AsyncGenerator"):
    AsyncGenerator = __builtins__["AsyncGenerator"]
else:
    from typing import AsyncGenerator  # type: ignore