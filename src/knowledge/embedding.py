"""Embedding pipeline provider adapter service.

Uses the Phase 2.1 provider registry to select an embedding provider
and runs an EmbeddingPipeline over chunked text to produce vector embeddings.
"""

from __future__ import annotations

from typing import List, Optional

from error_types import ProviderError

from .chunker import chunk_text


class EmbeddingPipeline:
    """Runs embedding over chunked text using a provider from the Phase 2.1 registry."""

    def __init__(
        self,
        provider_key: str = "mock",
        chunk_size: int = 512,
        chunk_overlap: int = 50,
        separator: str = "\n",
    ) -> None:
        self.provider_key = provider_key
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.separator = separator
        self._provider = None  # lazy init

    @property
    def provider(self):
        """Lazily get the embedding provider from the registry."""
        from src.providers import get_provider
        if self._provider is None:
            self._provider = get_provider(self.provider_key)
        return self._provider

    def chunk(self, text: str) -> List[str]:
        """Chunk text using the configured parameters."""
        return chunk_text(
            text,
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
            separator=self.separator,
        )

    def embed(self, text: str) -> List[float]:
        """Generate embeddings for a single text, or fail loudly.

        Delegates to the configured provider and returns its first vector.

        Failure is deliberately loud. A provider with no real backend (the
        ``openai`` / ``self_host`` stubs) raises
        :class:`~src.providers.llm_base.ProviderNotConfiguredError` when the
        simulation opt-in (``LIUHAO_ALLOW_SIMULATED_EXECUTION``) is unset, and
        that error MUST reach the caller. The previous implementation wrapped
        the call in ``except Exception: pass`` and fell back to
        ``[0.0] * 1536``: an all-zero vector is indistinguishable from a real
        embedding, so it silently poisoned downstream retrieval and similarity
        scoring. Fabricating data is worse than failing, so no fallback vector
        is ever returned here. (The ``mock`` provider's own declared zero
        vectors still pass through unchanged -- that is an honest contract, not
        a swallowed error.)
        """
        # The provider call is intentionally NOT wrapped in try/except: a
        # provider error (not configured, transport, auth) is a real fault the
        # caller must see, not something to hide behind a fake vector.
        emb = self.provider.embeddings([text])

        # A usable payload carries at least one ``embedding`` entry. A payload
        # that does not is a provider fault too -- surface it rather than
        # inventing a vector. The except is narrowed to the shape errors this
        # parse can actually raise; notably it does NOT catch
        # ProviderNotConfiguredError, which must propagate untouched.
        try:
            return emb["data"][0]["embedding"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(
                f"provider {self.provider_key!r} returned a payload without a "
                "usable embedding; refusing to fabricate a zero vector",
                provider=self.provider_key,
                error_code="EMPTY_EMBEDDING",
                severity="high",
            ) from exc

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for a list of texts."""
        return [self.embed(t) for t in texts]


def embed_text(
    text: str,
    provider_key: str = "mock",
    chunk_size: int = 512,
    chunk_overlap: int = 50,
) -> List[float]:
    """Convenience function to embed a single text.

    Args:
        text: The text to embed.
        provider_key: Provider alias ('mock', 'openai', 'self_host').
        chunk_size: Chunk size for preprocessing.
        chunk_overlap: Chunk overlap for preprocessing.

    Returns:
        Embedding vector (1536-dim by default).
    """
    pipeline = EmbeddingPipeline(
        provider_key=provider_key,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )
    return pipeline.embed(text)


class EmbeddingService:
    """Service wrapper around :class:`EmbeddingPipeline`, selected by provider name.

    The S2 RAG refactor constructs ``EmbeddingService(provider_name)`` and keeps
    it on the pipeline; this class lazily builds the underlying pipeline so the
    constructor stays cheap and provider-agnostic.
    """

    def __init__(self, provider_name: str = "mock") -> None:
        self.provider_name = provider_name
        self._pipeline: Optional[EmbeddingPipeline] = None

    @property
    def pipeline(self) -> EmbeddingPipeline:
        if self._pipeline is None:
            self._pipeline = EmbeddingPipeline(provider_key=self.provider_name)
        return self._pipeline

    def embed(self, text: str) -> List[float]:
        """Embed `text` via the underlying pipeline."""
        return self.pipeline.embed(text)
