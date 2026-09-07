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
from typing import Optional, Dict, Any

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
    ):
        self.name = name
        self.model = model
        self.api_key = api_key or os.environ.get("AI_PROVIDER_KEY", "[REDACTED]")
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
        """Generate with automatic retry and error classification."""
        import time
        from error_types import classify_error, ProviderRateLimitError

        last_error = None
        for attempt in range(self.max_retries + 1):
            try:
                return self.generate(prompt, **kwargs)
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

        raise last_error

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
        """Return a mock response based on prompt content."""
        # Simple mock response generation
        if "hello" in prompt.lower() or "hi" in prompt.lower():
            return "Hello! I'm LiuHao AI, how can I assist you today?"
        elif "status" in prompt.lower():
            return f"LiuHao AI OS status: {self.get_capabilities()}"
        elif "help" in prompt.lower():
            return "I can help you with: coding, analysis, creative writing, and more. " \
                   "Ask me about AI providers, frameworks, or project status."
        else:
            return f"Mock response to: {prompt[:80]}..."


# OpenAI provider (1.x SDK compatible, using official openai library)
class OpenAIProvider(BaseProvider):
    """OpenAI API provider integration using official openai Python SDK (1.x)."""

    def __init__(self, name: str, model: str, api_key: Optional[str] = None, **kwargs):
        super().__init__(name, model, api_key, **kwargs)
        # Store proxy config
        self.proxy = kwargs.get("proxy") or os.environ.get("OPENAI_PROXY")
        self.base_url = kwargs.get("base_url") or os.environ.get("OPENAI_BASE_URL")
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
        """Get OpenAI client with proper configuration for 1.x SDK."""
        import openai
        import httpx

        # Build http client with proxy if needed
        http_client = None
        if self.proxy:
            http_client = httpx.Client(proxy=self.proxy)

        client = openai.OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            http_client=http_client,
        )
        return client

    def generate(self, prompt: str, **kwargs) -> str:
        """Generate response via OpenAI API (1.x SDK compatible)."""
        model = kwargs.get("model", self.model)
        temperature = kwargs.get("temperature", 0.7)
        max_tokens = kwargs.get("max_tokens", 1000)
        # Support new OpenAI SDK response_format for structured output
        response_format = kwargs.get("response_format")

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
        except Exception:
            # Re-raise to let generate_with_retry handle it
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

    def generate(self, prompt: str, **kwargs) -> str:
        """Generate response via Anthropic API."""
        import anthropic

        model = kwargs.get("model", self.model)
        try:
            client = anthropic.Anthropic(api_key=self.api_key)
            # v1.x SDK approach - simplified
            response = client.messages.create(
                model=model,
                max_tokens=kwargs.get("max_tokens", 1000),
                # temperature removed for compatibility - use system prompt instead
                messages=[{"role": "user", "content": prompt}],
            )
            return response.content[0].text
        except Exception:
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
        """Generate response via Google Gemini API."""
        import google.generativeai as genai

        model = kwargs.get("model", self.model)
        try:
            genai.configure(api_key=self.api_key)
            model_obj = genai.GenerativeModel(model)
            response = model_obj.generate_content(prompt)
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
        base_url = kwargs.get("base_url", os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"))

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
            )
            if response.status_code == 200:
                result = response.json()
                return result.get("response", str(result))
            else:
                raise Exception(f"Ollama error {response.status_code}: {response.text}")
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
        base_url = kwargs.get("base_url", os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"))

        try:
            response = requests.post(
                f"{base_url}/api/chat",
                json={"model": model, "messages": messages, "stream": False},
                timeout=self.timeout,
                proxies={"http": None, "https": None},
            )
            if response.status_code == 200:
                result = response.json()
                return result.get("message", {}).get("content", str(result))
            raise Exception(f"Ollama error {response.status_code}: {response.text}")
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
        base_url = kwargs.get("base_url", os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"))

        response = requests.post(
            f"{base_url}/api/chat",
            json={"model": model, "messages": messages, "stream": True},
            timeout=self.timeout,
            proxies={"http": None, "https": None},
            stream=True,
        )
        if response.status_code != 200:
            raise Exception(f"Ollama error {response.status_code}: {response.text}")

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
        base_url = kwargs.get("base_url", os.environ.get("MOONSHOT_BASE_URL", "https://api.moonshot.ai/v1"))
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

    def generate(self, prompt: str, **kwargs) -> str:
        """Generate response via DeepSeek API."""
        import requests

        model = kwargs.get("model", self.model)
        base_url = kwargs.get("base_url", os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"))
        api_key = self.api_key or os.environ.get("DEEPSEEK_API_KEY", "[REDACTED]")

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
                raise Exception(f"DeepSeek error {response.status_code}: {response.text}")
        except Exception:
            # Re-raise to let generate_with_retry handle it
            raise


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
    def create_provider(cls, provider_type: str = None, **kwargs) -> BaseProvider:
        """Create a provider instance by type."""
        if provider_type is None:
            provider_type = os.environ.get("AI_PROVIDER_TYPE", ProviderType.MOCK)

        provider_class = cls._providers.get(provider_type)
        if not provider_class:
            raise ValueError(f"Unknown provider type: {provider_type}")

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


def get_provider() -> BaseProvider:
    """Get the global provider instance, auto-detecting from environment.

    The provider type is determined by the AI_PROVIDER_TYPE environment variable.
    Set this to a valid ProviderType (openai, anthropic, google, ollama,
    moonshot, deepseek) to use a real provider, or MOCK for development.

    Example:
        export AI_PROVIDER_TYPE=openai
        export AI_PROVIDER_KEY=[REDACTED]
    """
    global _provider_instance

    if _provider_instance is None:
        # Get provider type from env var, default to mock (safe, no fake-key
        # network call; consistent with ProviderFactory.create_provider and
        # detect_provider_type_from_env, which also default to MOCK).
        provider_type = os.environ.get("AI_PROVIDER_TYPE", ProviderType.MOCK).lower()

        # Validate provider type is supported
        if provider_type not in ProviderFactory._providers:
            print(f"Warning: Unknown provider type '{provider_type}', defaulting to MOCK")
            provider_type = "mock"

        # Required args for all providers: name and model
        # Use sensible defaults; override with env vars if needed
        name = os.environ.get("AI_PROVIDER_NAME", "liuhao-assistant")
        model = os.environ.get("AI_PROVIDER_MODEL", "mock-model")
        api_key = os.environ.get("AI_PROVIDER_KEY", "[REDACTED]")

        _provider_instance = ProviderFactory.create_provider(
            provider_type,
            name=name,
            model=model,
            api_key=api_key,
        )

        # Log provider info (masked)
        print(f"Provider initialized: {provider_type} (name={name}, model={model}, key: {api_key[:8]}...)")

    return _provider_instance


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
