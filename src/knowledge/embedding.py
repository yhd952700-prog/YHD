"""Embedding pipeline provider adapter service.

Two layers live here:

* :class:`EmbeddingPipeline` — the historical adapter over ``src.providers``
  (mock/contract stubs); kept for existing callers.
* The **real** HTTP embedders below (:class:`OpenAICompatibleEmbedder`,
  :class:`OllamaEmbedder`) plus :func:`build_embedding_provider`, which is what
  the live knowledge-base path uses. Mock embeddings are only built when the
  operator is *explicitly* in development mode **and** opted in; otherwise the
  builder raises :class:`EmbeddingUnavailableError` (fail-closed) so the
  knowledge base reports "未启用" instead of pretending to answer.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import List, Optional, Sequence

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


# ==================== 真实 embedding provider（知识库活链路） ====================

EMBEDDING_PROVIDER_ENV = "LIUHAO_EMBEDDING_PROVIDER"
EMBEDDING_MODEL_ENV = "LIUHAO_EMBEDDING_MODEL"
KNOWLEDGE_ALLOW_MOCK_ENV = "LIUHAO_KNOWLEDGE_ALLOW_MOCK"
EMBEDDING_TIMEOUT_ENV = "LIUHAO_EMBEDDING_TIMEOUT"

_DEV_ENVIRONMENTS = frozenset({"development", "dev", "test", "local"})
_TRUTHY = frozenset({"1", "true", "yes", "on"})


class EmbeddingUnavailableError(RuntimeError):
    """Embedding is not configured. Fail-closed; never fabricate a vector."""


def _environment() -> str:
    return (
        (os.environ.get("LIUHAO_ENV") or os.environ.get("APP_ENV") or "")
        .strip()
        .lower()
    )


def mock_embedding_allowed() -> bool:
    """Mock embeddings require *explicit* development mode plus an opt-in."""
    allow = (os.environ.get(KNOWLEDGE_ALLOW_MOCK_ENV) or "").strip().lower()
    return _environment() in _DEV_ENVIRONMENTS and allow in _TRUTHY


def _timeout() -> float:
    try:
        return float(os.environ.get(EMBEDDING_TIMEOUT_ENV) or 30)
    except ValueError:
        return 30.0


def _post_json(
    url: str, payload: dict, headers: Optional[dict] = None, timeout: Optional[float] = None
) -> dict:
    """Minimal stdlib JSON POST (no new dependency)."""
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout or _timeout()) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise EmbeddingUnavailableError(
            f"embedding endpoint {url} returned HTTP {exc.code}"
        ) from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise EmbeddingUnavailableError(
            f"embedding endpoint {url} is unreachable ({type(exc).__name__})"
        ) from exc


class OpenAICompatibleEmbedder:
    """``POST {base_url}/embeddings`` — OpenAI and every compatible vendor."""

    def __init__(
        self, *, model: str, base_url: str, api_key: str, timeout: Optional[float] = None
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout or _timeout()

    @property
    def name(self) -> str:
        return "openai"

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        payload = {"model": self.model, "input": list(texts)}
        data = _post_json(
            f"{self.base_url}/embeddings",
            payload,
            {"Authorization": f"Bearer {self.api_key}"},
            timeout=self.timeout,
        )
        try:
            items = sorted(data["data"], key=lambda item: item.get("index", 0))
            vectors = [item["embedding"] for item in items]
        except (KeyError, TypeError, AttributeError) as exc:
            raise EmbeddingUnavailableError(
                f"provider {self.name!r} returned a payload without embeddings"
            ) from exc
        if len(vectors) != len(texts):
            raise EmbeddingUnavailableError(
                f"provider {self.name!r} returned {len(vectors)} vectors for "
                f"{len(texts)} inputs"
            )
        return vectors


class OllamaEmbedder:
    """Ollama ``/api/embed`` with a per-text ``/api/embeddings`` fallback."""

    def __init__(
        self, *, model: str, base_url: str = "http://127.0.0.1:11434"
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")

    @property
    def name(self) -> str:
        return "ollama"

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        data = _post_json(
            f"{self.base_url}/api/embed",
            {"model": self.model, "input": list(texts)},
        )
        embeddings = data.get("embeddings")
        if isinstance(embeddings, list) and len(embeddings) == len(texts):
            return embeddings
        # Older Ollama builds only expose the single-prompt endpoint.
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> List[float]:
        data = _post_json(
            f"{self.base_url}/api/embeddings",
            {"model": self.model, "prompt": text},
        )
        vector = data.get("embedding")
        if not isinstance(vector, list) or not vector:
            raise EmbeddingUnavailableError(
                "provider 'ollama' returned a payload without an embedding"
            )
        return vector


class MockEmbedder:
    """Explicit-dev-only zero vectors. Never constructed in production."""

    def __init__(self, *, dimension: int = 1536) -> None:
        self.dimension = dimension

    @property
    def name(self) -> str:
        return "mock"

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        return [[0.0] * self.dimension for _ in texts]


def build_embedding_provider() -> object:
    """Build the real embedder from env, or raise ``EmbeddingUnavailableError``.

    ``LIUHAO_EMBEDDING_PROVIDER`` selects explicitly (``openai`` / ``ollama`` /
    ``mock``); ``auto`` (the default) picks OpenAI-compatible when a key is
    present, else Ollama when its base URL is configured, else stays disabled.
    """
    choice = (os.environ.get(EMBEDDING_PROVIDER_ENV) or "auto").strip().lower()
    model = (os.environ.get(EMBEDDING_MODEL_ENV) or "").strip()

    if choice == "mock":
        if not mock_embedding_allowed():
            raise EmbeddingUnavailableError(
                "mock embeddings require explicit development mode: set "
                "LIUHAO_ENV=development and LIUHAO_KNOWLEDGE_ALLOW_MOCK=1"
            )
        return MockEmbedder()

    api_key = (
        os.environ.get("LIUHAO_EMBEDDING_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or ""
    ).strip()
    openai_base = (
        os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1"
    ).strip()
    ollama_base = (
        os.environ.get("OLLAMA_BASE_URL") or os.environ.get("LIUHAO_OLLAMA_BASE_URL") or ""
    ).strip()

    if choice in {"openai", "openai_compatible", "auto"} and api_key:
        return OpenAICompatibleEmbedder(
            model=model or "text-embedding-3-small", base_url=openai_base, api_key=api_key
        )
    if choice in {"openai", "openai_compatible"}:
        raise EmbeddingUnavailableError(
            "LIUHAO_EMBEDDING_PROVIDER=openai but neither "
            "LIUHAO_EMBEDDING_API_KEY nor OPENAI_API_KEY is set"
        )

    if choice in {"ollama", "auto"} and ollama_base:
        return OllamaEmbedder(model=model or "nomic-embed-text", base_url=ollama_base)
    if choice == "ollama":
        raise EmbeddingUnavailableError(
            "LIUHAO_EMBEDDING_PROVIDER=ollama but OLLAMA_BASE_URL is not set"
        )

    raise EmbeddingUnavailableError(
        "知识库未启用：没有可用的 embedding provider（设置 "
        "LIUHAO_EMBEDDING_PROVIDER 与对应凭据/地址，或在开发模式显式允许 mock）"
    )


def chunk_document(text: str, chunk_size: int = 512, chunk_overlap: int = 50) -> List[str]:
    """Chunk a document for storage (shared by the ingest path)."""
    return chunk_text(text, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
