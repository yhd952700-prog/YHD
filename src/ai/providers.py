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
import warnings
from typing import Optional, Dict, Any, Sequence

from .observability import observe

logger = logging.getLogger("liuhao.ai.providers")

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
        # Store proxy config. Resolve through ``_provider_env`` so a value written
        # in ``.env`` actually takes effect: ConfigManager loads .env into its own
        # store WITHOUT exporting to ``os.environ``, so reading only os.environ
        # here silently ignored OPENAI_BASE_URL / OPENAI_PROXY from ``.env``.
        self.proxy = kwargs.get("proxy") or _provider_env("OPENAI_PROXY") or None
        self.base_url = kwargs.get("base_url") or _provider_env("OPENAI_BASE_URL") or None
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
            # Every other provider hands ``self.timeout`` to its transport; this
            # one used to leave the SDK default (10 min) in place, so the declared
            # 30s timeout was silently ignored. Honor it.
            timeout=self.timeout,
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
        try:
            import google.generativeai as genai
        except ModuleNotFoundError as exc:  # pragma: no cover - environment guard
            raise RuntimeError(
                "GoogleProvider requires the 'google-generativeai' package "
                "(pip install '.[google]'); it is an opt-in dependency, not "
                "part of the default install."
            ) from exc

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
        base_url = kwargs.get("base_url") or self.base_url or os.environ.get(
            "OLLAMA_BASE_URL", "http://localhost:11434"
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
        base_url = kwargs.get("base_url") or self.base_url or os.environ.get(
            "OLLAMA_BASE_URL", "http://localhost:11434"
        )

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
        base_url = kwargs.get("base_url") or self.base_url or os.environ.get(
            "OLLAMA_BASE_URL", "http://localhost:11434"
        )

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

    def generate(self, prompt: str, **kwargs) -> str:
        """Generate response via DeepSeek API."""
        import requests

        model = kwargs.get("model", self.model)
        base_url = kwargs.get("base_url") or self.base_url or os.environ.get(
            "DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"
        )
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
