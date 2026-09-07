"""SelfHostProvider - Self-hosted provider adapter contract."""

from __future__ import annotations

from src.providers.llm_base import LLMProvider


class SelfHostProvider(LLMProvider):
    """Self-hosted provider adapter contract."""

    @property
    def name(self) -> str:
        return "self_host"

    @property
    def type(self) -> str:
        return "self_host"

    def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float = 1.0,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Self-hosted chat completion stub."""
        content = messages[-1].get("content", "") if messages else ""
        return {
            "choices": [{"message": {"role": "assistant", "content": content}}],
            "model": "self-hosted-model",
            "usage": {"prompt_tokens": 0, "completion_tokens": len(content.split()), "total_tokens": len(content.split())},
        }

    def generate(self, prompt: str, **kwargs: Any) -> dict[str, Any]:
        """Self-hosted generation stub."""
        return {
            "choices": [{"message": {"role": "assistant", "content": prompt}}],
            "model": "self-hosted-model",
            "usage": {"prompt_tokens": len(prompt.split()), "completion_tokens": 0, "total_tokens": len(prompt.split())},
        }

    def embeddings(self, texts: list[str], **kwargs: Any) -> dict[str, Any]:
        """Self-hosted embeddings stub."""
        import numpy as np
        dim = kwargs.get("dimension", 1536)
        return {
            "data": [{"embedding": [0.0] * dim, "index": 0, "object": "embedding"}],
            "model": "self-hosted-embedding",
            "usage": {"prompt_tokens": 0, "total_tokens": 0},
        }

    def supports(self, capability: str) -> bool:
        """Check capability support."""
        capabilities = {"chat", "generate", "embeddings", "streaming"}
        return capability in capabilities

    def health_check(self) -> dict[str, Any]:
        return {"status": "healthy", "provider": "self_host"}