"""Live knowledge base (ingest + retrieval over the persisted vector store).

The service never fabricates an answer: if no embedding provider is configured,
``build_embedding_provider`` raises and every caller reports "未启用". The vector
store is the process-level SQLite singleton, so a restart keeps the corpus.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any, Dict, List, Optional

from .chunker import chunk_text
from .embedding import EmbeddingUnavailableError, build_embedding_provider
from .vector_store import get_vector_store

logger = logging.getLogger("liuhao.knowledge.documents")

_ID_SAFE = re.compile(r"[^A-Za-z0-9_.:-]+")


def _safe_document_id(raw: Optional[str]) -> str:
    if not raw or not raw.strip():
        return uuid.uuid4().hex
    return _ID_SAFE.sub("-", raw.strip())[:128]


class KnowledgeDocumentService:
    """Ingest text into and search over the persisted knowledge index."""

    def __init__(self, vector_store=None, embedder=None) -> None:
        self._vector_store = vector_store
        self._embedder = embedder
        self._unavailable_reason: Optional[str] = None

    # -- component wiring -------------------------------------------------
    def _embedding(self):
        if self._embedder is not None:
            return self._embedder
        try:
            self._embedder = build_embedding_provider()
        except EmbeddingUnavailableError as exc:
            self._unavailable_reason = str(exc)
            raise
        self._unavailable_reason = None
        return self._embedder

    def _store(self):
        if self._vector_store is not None:
            return self._vector_store
        self._vector_store = get_vector_store()
        return self._vector_store

    # -- read-only status --------------------------------------------------
    def status(self) -> Dict[str, Any]:
        """Human-readable availability; never raises."""
        provider = None
        model = None
        try:
            embedder = self._embedding()
            provider = getattr(embedder, "name", type(embedder).__name__)
            model = getattr(embedder, "model", None)
        except EmbeddingUnavailableError as exc:
            reason = str(exc)
        else:
            reason = None

        vectors = 0
        try:
            vectors = self._store().count()
        except Exception as exc:  # pragma: no cover - 存储首次打开失败
            logger.error("knowledge status: vector store unavailable (%s)", exc)
            reason = reason or f"向量库不可用：{type(exc).__name__}"

        return {
            "enabled": reason is None,
            "provider": provider,
            "model": model,
            "vectors": vectors,
            "reason": reason,
            "message": "知识库已启用" if reason is None else f"知识库未启用：{reason}",
        }

    # -- write -------------------------------------------------------------
    def ingest(
        self,
        text: str,
        doc_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        embedder = self._embedding()
        store = self._store()
        document_id = _safe_document_id(doc_id)
        chunks = chunk_text(text)

        static_metadata = dict(metadata or {})
        vectors = embedder.embed(chunks)

        # Replace the *whole* document revision atomically. Embeddings are
        # computed before touching the store, so an embedding failure leaves the
        # previous revision intact. Swapping (delete old ids + insert new ones)
        # in one call also drops tail chunks from a longer prior version -- the
        # old implementation only overwrote ``doc#0..n`` and stale tail chunks
        # kept leaking into retrieval.
        items = [
            (
                f"{document_id}#{index}",
                vector,
                {
                    **static_metadata,
                    "document_id": document_id,
                    "chunk_index": index,
                    "text": chunk,
                },
            )
            for index, (chunk, vector) in enumerate(zip(chunks, vectors))
        ]
        store.replace_prefix(f"{document_id}#", items)

        logger.info(
            "knowledge ingest: document=%s chunks=%d provider=%s",
            document_id,
            len(chunks),
            getattr(embedder, "name", "unknown"),
        )
        return {
            "document_id": document_id,
            "chunks": len(chunks),
            "vectors": store.count(),
        }

    # -- read ---------------------------------------------------------------
    def search(
        self, query: str, top_k: int = 4, threshold: Optional[float] = None
    ) -> Dict[str, Any]:
        embedder = self._embedding()
        store = self._store()
        query_vector = embedder.embed([query])[0]
        results = store.search(query_vector, top_k=top_k, threshold=threshold)
        hits: List[Dict[str, Any]] = [
            {
                "doc_id": doc_id,
                "score": round(score, 6),
                "text": str((meta or {}).get("text", "")),
                "metadata": meta or {},
            }
            for doc_id, score, meta in results
        ]
        logger.info(
            "knowledge search: top_k=%d hits=%d provider=%s",
            top_k,
            len(hits),
            getattr(embedder, "name", "unknown"),
        )
        return {"query": query, "top_k": top_k, "hits": hits, "count": store.count()}


_SERVICE: Optional[KnowledgeDocumentService] = None


def get_knowledge_service() -> KnowledgeDocumentService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = KnowledgeDocumentService()
    return _SERVICE


def reset_knowledge_service() -> None:
    """Test helper: forget the cached embedder/store wiring."""
    global _SERVICE
    _SERVICE = None
