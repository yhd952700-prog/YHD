"""OpenAIProvider - OpenAI-compatible provider adapter contract."""

from __future__ import annotations

from typing import Any

from src.providers.llm_base import LLMProvider


class OpenAIProvider(LLMProvider):
    """OpenAI-compatible provider adapter contract."""

    @property
    def name(self) -> str:
        return "openai"

    @property
    def type(self) -> str:
        return "openai"

    def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float = 1.0,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """OpenAI-compatible chat completion stub."""
        content = messages[-1].get("content", "") if messages else ""
        return {
            "choices": [{"message": {"role": "assistant", "content": content}}],
            "model": "gpt-4o-mini",
            "usage": {"prompt_tokens": 0, "completion_tokens": len(content.split()), "total_tokens": len(content.split())},
        }

    def generate(self, prompt: str, **kwargs: Any) -> dict[str, Any]:
        """OpenAI-compatible generation stub."""
        return {
            "choices": [{"message": {"role": "assistant", "content": prompt}}],
            "model": "gpt-4o-mini",
            "usage": {"prompt_tokens": len(prompt.split()), "completion_tokens": 0, "total_tokens": len(prompt.split())},
        }

    def embeddings(self, texts: list[str], **kwargs: Any) -> dict[str, Any]:
        """OpenAI-compatible embeddings stub."""
        dim = kwargs.get("dimension", 1536)
        return {
            "data": [{"embedding": [0.0] * dim, "index": 0, "object": "embedding"}],
            "model": "text-embedding-3-small",
            "usage": {"prompt_tokens": 0, "total_tokens": 0},
        }

    def supports(self, capability: str) -> bool:
        """Check capability support."""
        capabilities = {"chat", "generate", "embeddings", "streaming"}
        return capability in capabilities

    def health_check(self) -> dict[str, Any]:
        return {"status": "healthy", "provider": "openai"}
