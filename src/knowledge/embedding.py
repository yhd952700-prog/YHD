"""Embedding pipeline provider adapter service.

Uses the Phase 2.1 provider registry to select an embedding provider
and runs an EmbeddingPipeline over chunked text to produce vector embeddings.
"""

from __future__ import annotations

from typing import List

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
        """Generate embeddings for a single text.

        In the mock/stub mode, returns a zero vector.
        In a real implementation, this would call the provider's embeddings() method.
        """
        # Use the provider's embeddings if available
        try:
            emb = self.provider.embeddings([text])
            # Return the first embedding vector
            if emb and "data" in emb and len(emb["data"]) > 0:
                return emb["data"][0]["embedding"]
        except Exception:
            pass
        # Fallback: return zero vector
        return [0.0] * 1536

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
