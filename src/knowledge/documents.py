"""Live knowledge base (ingest + retrieval over the persisted vector store).

The service never fabricates an answer: if no embedding provider is configured,
``build_embedding_provider`` raises and every caller reports "未启用". The vector
store is the process-level SQLite singleton, so a restart keeps the corpus.
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
import uuid
from typing import Any, Callable, Dict, List, Optional

from .chunker import chunk_document_structured
from .embedding import EmbeddingUnavailableError, build_embedding_provider
from .vector_store import get_vector_store

logger = logging.getLogger("liuhao.knowledge.documents")

_ID_SAFE = re.compile(r"[^A-Za-z0-9_.:-]+")


class IngestCancelled(Exception):
    """Raised at a chunk boundary when a cancellable ingest is aborted.

    Nothing has been written to the store at that point: vectors are computed
    before the swap, so cancellation is **all-or-nothing** (全回滚).
    """

    def __init__(self, processed: int, total: int) -> None:
        super().__init__(
            f"入库已取消（已处理 {processed}/{total} 个分块，未写入任何分块）"
        )
        self.processed = processed
        self.total = total


def _safe_document_id(raw: Optional[str]) -> str:
    if not raw or not raw.strip():
        return uuid.uuid4().hex
    return _ID_SAFE.sub("-", raw.strip())[:128]


def content_hash(text: str) -> str:
    """Whitespace-insensitive content hash (same text, different line endings)."""
    normalized = re.sub(r"\s+", " ", text or "").strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _hit_text(metadata: Dict[str, Any]) -> str:
    """Body plus heading breadcrumb; legacy rows (no header) still work."""
    body = str(metadata.get("text", ""))
    header = str(metadata.get("context_header") or "").strip()
    if not header:
        return body
    return f"[{header}]\n{body}" if body else f"[{header}]"


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
        *,
        on_progress: Optional[Callable[[int, int], None]] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> Dict[str, Any]:
        embedder = self._embedding()
        store = self._store()
        document_id = _safe_document_id(doc_id)
        chunks = chunk_document_structured(text)

        static_metadata = dict(metadata or {})
        # The heading breadcrumb is embedded together with the body so a chunk
        # that starts with "如下所示" still carries its section topic.
        if on_progress is None and cancel_event is None:
            vectors = embedder.embed([chunk.embed_text for chunk in chunks])
        else:
            # Cancellable path: one embedding per chunk so a cancel request is
            # honoured at the next chunk boundary, and progress is observable.
            vectors = []
            for index, chunk in enumerate(chunks):
                if cancel_event is not None and cancel_event.is_set():
                    raise IngestCancelled(index, len(chunks))
                vectors.append(embedder.embed([chunk.embed_text])[0])
                if on_progress is not None:
                    on_progress(index + 1, len(chunks))

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
                    "text": chunk.text,
                    "heading_path": list(chunk.heading_path),
                    "context_header": chunk.context_header,
                },
            )
            for index, (chunk, vector) in enumerate(zip(chunks, vectors))
        ]
        outcome = store.upsert_document(
            f"{document_id}#", items, content_hash(text), document_id
        )
        if outcome.get("deduplicated"):
            matched = outcome.get("matched_document_id")
            logger.info(
                "knowledge ingest: document=%s skipped (duplicate of %s)",
                document_id,
                matched,
            )
            return {
                "document_id": document_id,
                "chunks": 0,
                "vectors": store.count(),
                "deduplicated": True,
                "matched_document_id": matched,
            }

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
            "deduplicated": False,
            "matched_document_id": None,
        }

    # -- read ---------------------------------------------------------------
    def search(
        self, query: str, top_k: int = 4, threshold: Optional[float] = None
    ) -> Dict[str, Any]:
        embedder = self._embedding()
        store = self._store()
        query_vector = embedder.embed([query])[0]
        if hasattr(store, "hybrid_search"):
            results, retrieval = store.hybrid_search(
                query, query_vector, top_k=top_k, threshold=threshold
            )
        else:  # pragma: no cover - third-party stores without the hybrid API
            results = store.search(query_vector, top_k=top_k, threshold=threshold)
            retrieval = {"mode": "vector", "fts_available": False}
        hits: List[Dict[str, Any]] = [
            {
                "doc_id": doc_id,
                "score": round(score, 6),
                "text": _hit_text(meta or {}),
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
        return {
            "query": query,
            "top_k": top_k,
            "hits": hits,
            "count": store.count(),
            "retrieval": retrieval,
        }

    # -- document administration (no embedder needed) -----------------------
    def list_documents(self, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
        store = self._store()
        documents, total = store.list_documents(limit=limit, offset=offset)
        return {
            "documents": documents,
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    def document_chunks(self, document_id: str) -> Dict[str, Any]:
        chunks = self._store().get_document_chunks(document_id)
        return {
            "document_id": document_id,
            "chunks": chunks,
            "count": len(chunks),
        }

    def delete_document(self, document_id: str) -> Dict[str, Any]:
        outcome = self._store().delete_document(document_id)
        logger.info(
            "knowledge delete: document=%s deleted=%s chunks_removed=%s",
            document_id,
            outcome.get("deleted"),
            outcome.get("chunks_removed"),
        )
        return {
            "document_id": document_id,
            "deleted": bool(outcome.get("deleted")),
            "chunks_removed": int(outcome.get("chunks_removed") or 0),
        }


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
