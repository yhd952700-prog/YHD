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

**Switching provider or model is not a free change.** The relevance gate
(``abstention``) is tuned against the cosine-score distribution of a specific
embedding model; a lexical floor such as ``0.08`` is a near no-op under a real
semantic model, so recall and abstention can degrade silently while every
request still returns "ok". After changing ``LIUHAO_EMBEDDING_PROVIDER`` /
``LIUHAO_EMBEDDING_MODEL`` (or the backend behind an OpenAI-compatible
endpoint), re-run::

    python -m src.knowledge.evaluation --provider <provider>

and set ``LIUHAO_KB_ABSTAIN_MIN_SIMILARITY`` / ``LIUHAO_KB_ABSTAIN_MIN_GAP`` /
``LIUHAO_KB_ABSTAIN_STRONG_MATCH`` from that report's ``recommended.env``.
Do not change the model without changing the thresholds.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from error_types import ProviderError

from .chunker import chunk_text

#: Local install defaults. A user who only installed the product (no env vars)
#: should still get a working knowledge base when the bundled Ollama runtime is
#: up, instead of an empty index that says "未启用".
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_OLLAMA_EMBEDDING_MODEL = "nomic-embed-text"
OLLAMA_PROBE_TIMEOUT_ENV = "LIUHAO_EMBEDDING_PROBE_TIMEOUT_SECONDS"
DEFAULT_PROBE_TIMEOUT = 2.0

_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"


def _read_yaml(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        import yaml

        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        return document if isinstance(document, dict) else {}
    except Exception:  # noqa: BLE001 - config must never break embedding selection
        return {}


def _merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def local_ollama_settings() -> Tuple[str, str]:
    """``(base_url, embedding_model)`` declared by ``config/*.yaml``.

    The gateway already routes chat to the Ollama declared there; reading the
    same declaration for embeddings is what makes a plain installation usable
    without the user exporting variables. Environment variables still win.
    """
    environment = (
        os.environ.get("APP_ENV") or os.environ.get("LIUHAO_ENV") or "development"
    ).strip().lower()
    document = _merge(
        _read_yaml(_CONFIG_DIR / "default.yaml"),
        _read_yaml(_CONFIG_DIR / f"{environment}.yaml"),
    )
    providers = document.get("providers") if isinstance(document.get("providers"), dict) else {}
    ollama = providers.get("ollama") if isinstance(providers.get("ollama"), dict) else {}
    base_url = str(ollama.get("base_url") or DEFAULT_OLLAMA_BASE_URL).strip()
    memory = document.get("memory") if isinstance(document.get("memory"), dict) else {}
    long_term = memory.get("long_term") if isinstance(memory.get("long_term"), dict) else {}
    model = str(long_term.get("embedding_model") or DEFAULT_OLLAMA_EMBEDDING_MODEL).strip()
    return base_url, model


def _probe_timeout() -> float:
    raw = (os.environ.get(OLLAMA_PROBE_TIMEOUT_ENV) or "").strip()
    try:
        value = float(raw) if raw else DEFAULT_PROBE_TIMEOUT
    except ValueError:
        value = DEFAULT_PROBE_TIMEOUT
    return max(0.2, min(value, 10.0))


def _installed_ollama_models(base_url: str) -> Optional[List[str]]:
    """Installed model names, or ``None`` when the daemon is unreachable."""
    request = urllib.request.Request(f"{base_url.rstrip('/')}/api/tags", method="GET")
    try:
        with urllib.request.urlopen(request, timeout=_probe_timeout()) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 - "not running" is the normal first-run state
        return None
    models = payload.get("models") if isinstance(payload, dict) else None
    return [
        str(item.get("name") or "").strip()
        for item in models or []
        if isinstance(item, dict) and item.get("name")
    ]


def _model_available(installed: Sequence[str], requested: str) -> bool:
    """Match ``nomic-embed-text`` against ``nomic-embed-text:latest`` (and back)."""
    want = requested.strip().lower()
    want_base = want.split(":", 1)[0]
    for name in installed:
        candidate = name.strip().lower()
        if candidate == want:
            return True
        if candidate.split(":", 1)[0] == want_base and candidate.endswith(":latest"):
            return True
    return False


def _production() -> bool:
    """Whether this deployment is production (single canonical resolver)."""
    try:
        from ..security.posture import is_production

        return is_production()
    except Exception:  # noqa: BLE001 - never let posture break provider selection
        raw = (
            os.environ.get("LIUHAO_ENV")
            or os.environ.get("ENVIRONMENT")
            or os.environ.get("APP_ENV")
            or ""
        ).strip().lower()
        return raw in {"production", "prod"}


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


class EmbeddingEndpointError(EmbeddingUnavailableError):
    """The endpoint answered, but with an HTTP error.

    Carries the status code so callers can tell "this Ollama build does not
    expose the endpoint" (404/400, fall back to the legacy API) from a genuine
    transport/auth failure, without having to parse the message text.
    """

    def __init__(self, message: str, *, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


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
        raise EmbeddingEndpointError(
            f"embedding endpoint {url} returned HTTP {exc.code}",
            status_code=exc.code,
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
        """Embed ``texts``, falling back to the legacy single-prompt endpoint.

        Older Ollama builds do not expose ``/api/embed`` at all and answer
        HTTP 404/400 (rather than 200 with an unexpected shape). Those two
        statuses mean "this build lacks the endpoint", so we retry one text at
        a time against ``/api/embeddings``. Any other HTTP status (auth,
        upstream 5xx) is a real fault and propagates unchanged.
        """
        try:
            data = _post_json(
                f"{self.base_url}/api/embed",
                {"model": self.model, "input": list(texts)},
            )
        except EmbeddingEndpointError as exc:
            if exc.status_code not in (400, 404):
                raise
            return self._embed_legacy(texts)

        embeddings = data.get("embeddings")
        if isinstance(embeddings, list) and len(embeddings) == len(texts):
            return embeddings
        # 200 but the payload is not what ``/api/embed`` promises: same fix.
        return self._embed_legacy(texts)

    def _embed_legacy(self, texts: Sequence[str]) -> List[List[float]]:
        """One-by-one ``/api/embeddings`` for Ollama builds without ``/api/embed``."""
        vectors: List[List[float]] = []
        for text in texts:
            try:
                vectors.append(self._embed_one(text))
            except EmbeddingEndpointError as exc:
                if exc.status_code == 404:
                    raise EmbeddingUnavailableError(
                        "provider 'ollama' 版本不支持 embedding 接口："
                        f"本机 Ollama 既没有 /api/embed 也没有 /api/embeddings"
                        f"（{exc}）；这属于版本不兼容，不是未配置"
                    ) from exc
                raise
        return vectors

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
    ``mock``). ``auto`` (the default) **never** goes to the cloud: it only uses
    a local Ollama, so merely having ``OPENAI_API_KEY`` in the environment can
    no longer ship knowledge-base text to ``api.openai.com``. Cloud embedding
    requires an explicit ``openai`` / ``openai_compatible`` choice.
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

    openai_base = (
        os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1"
    ).strip()
    ollama_base = (
        os.environ.get("OLLAMA_BASE_URL") or os.environ.get("LIUHAO_OLLAMA_BASE_URL") or ""
    ).strip()

    # 云端只在**显式选择**时启用。``auto`` 不再因为环境里恰好有
    # ``OPENAI_API_KEY`` 就把知识库分片原文发到 api.openai.com —— 这是全产品
    # 最隐蔽的一条外发路径，默认必须留在本机。
    if choice in {"openai", "openai_compatible"}:
        api_key = (
            os.environ.get("LIUHAO_EMBEDDING_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
            or ""
        ).strip()
        if not api_key:
            raise EmbeddingUnavailableError(
                "LIUHAO_EMBEDDING_PROVIDER=openai but neither "
                "LIUHAO_EMBEDDING_API_KEY nor OPENAI_API_KEY is set"
            )
        return OpenAICompatibleEmbedder(
            model=model or "text-embedding-3-small", base_url=openai_base, api_key=api_key
        )

    if choice in {"ollama", "auto"} and ollama_base:
        return OllamaEmbedder(model=model or "nomic-embed-text", base_url=ollama_base)

    # 纯安装、没有导出任何变量的场景：用 config/*.yaml 里已经声明的本机
    # Ollama（聊天也用它），先探测再启用。生产环境保持 fail-closed：部署方
    # 必须显式声明 provider，而不是继承开发机的默认值。
    if choice in {"ollama", "auto"} and not _production():
        config_base, config_model = local_ollama_settings()
        model_name = model or config_model
        installed = _installed_ollama_models(config_base)
        if installed is None:
            raise EmbeddingUnavailableError(
                "知识库未启用：没有可用的 embedding provider。本机 Ollama "
                f"（{config_base}）未运行，无法自动启用；请启动 Ollama，或设置 "
                "LIUHAO_EMBEDDING_PROVIDER 与对应凭据/地址"
            )
        if not _model_available(installed, model_name):
            available = "、".join(installed) if installed else "无"
            raise EmbeddingUnavailableError(
                f"知识库未启用：本机 Ollama 缺少 embedding 模型 '{model_name}'"
                f"（已安装：{available}）。请执行 `ollama pull {model_name}`，"
                "或设置 LIUHAO_EMBEDDING_MODEL"
            )
        return OllamaEmbedder(model=model_name, base_url=config_base)

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
