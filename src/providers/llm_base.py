"""`LLMProvider` base interface for `chat()`, `generate()`, and `embeddings()`.

All concrete provider implementations must adhere to this interface
to ensure compatibility with the provider registry and switching logic.
"""


from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional, Sequence


class ProviderConfig:
    """Minimal config wrapper used by the provider registry."""

    def __init__(self, name: str, api_key: str = "", **kwargs: Any) -> None:
        self.name = name
        self.api_key = api_key
        self.kwargs = kwargs


class LLMProvider(ABC):
    """Base interface for all LLM providers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the provider name identifier."""

    @property
    @abstractmethod
    def type(self) -> str:
        """Return the provider type string."""

    @abstractmethod
    def chat(
        self,
        messages: Sequence[dict[str, str]],
        temperature: float = 1.0,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Generate a chat completion."""

    @abstractmethod
    def generate(self, prompt: str, **kwargs: Any) -> dict[str, Any]:
        """Generate a completion for a single prompt."""

    @abstractmethod
    def embeddings(
        self,
        texts: Sequence[str],
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Generate embeddings for a sequence of texts."""

    @abstractmethod
    def supports(self, capability: str) -> bool:
        """Check if the provider supports a given capability."""

    @abstractmethod
    def health_check(self) -> dict[str, Any]:
        """Return a health check status dictionary."""
