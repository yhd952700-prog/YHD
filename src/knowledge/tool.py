"""Real knowledge-base search for the tool surface.

The pre-existing agent "knowledge search" was a hardcoded dictionary: it always
returned canned paragraphs, even when the knowledge base was disabled. This
module performs an actual vector retrieval through
:class:`~src.knowledge.documents.KnowledgeDocumentService` and, when no
embedding provider is configured, reports *unavailable* instead of fabricating
results.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .documents import get_knowledge_service
from .embedding import EmbeddingUnavailableError


class KnowledgeSearchUnavailable(RuntimeError):
    """The knowledge base cannot answer because no embedding provider exists."""


def knowledge_ingest_text(
    text: str,
    title: Optional[str] = None,
) -> Dict[str, Any]:
    """Ingest a block of text into the persisted knowledge index.

    The chat-side twin of the HTTP upload path for plain text: the caller
    hands over already-extracted text, the service chunks / embeds / stores
    it under the caller's document id (derived from ``title`` when given).
    When embeddings are not configured the result is ``available=False``
    with the honest reason -- nothing is silently dropped or faked.
    """
    clean_text = (text or "").strip()
    if not clean_text:
        raise ValueError("knowledge_ingest_text requires non-empty text")

    service = get_knowledge_service()
    try:
        outcome = service.ingest(
            clean_text,
            doc_id=(title or None),
            metadata={"source": "chat", "title": title or ""},
        )
    except EmbeddingUnavailableError as exc:
        return {
            "available": False,
            "reason": str(exc),
            "document_id": None,
            "chunks": 0,
        }
    return {
        "available": True,
        "reason": None,
        "document_id": outcome.get("document_id"),
        "chunks": outcome.get("chunks"),
        "vectors": outcome.get("vectors"),
        "deduplicated": bool(outcome.get("deduplicated")),
    }


def knowledge_search(
    query: str,
    top_k: int = 4,
    threshold: Optional[float] = None,
) -> Dict[str, Any]:
    """Search the persisted knowledge index and return honest chunk hits.

    Returns a dict with ``available`` (bool), ``reason`` (str | None), ``query``
    and ``results``. When embeddings are not configured, ``available`` is
    ``False`` and ``results`` is empty -- never simulated content.
    """
    clean_query = (query or "").strip()
    if not clean_query:
        raise ValueError("knowledge_search requires a non-empty query")

    service = get_knowledge_service()
    try:
        found = service.search(clean_query, top_k=top_k, threshold=threshold)
    except EmbeddingUnavailableError as exc:
        return {
            "available": False,
            "reason": str(exc),
            "query": clean_query,
            "results": [],
            "count": 0,
        }

    return {
        "available": True,
        "reason": None,
        "query": clean_query,
        "results": [
            {
                "chunk_id": hit.get("doc_id"),
                "document_id": (hit.get("metadata") or {}).get(
                    "document_id", hit.get("doc_id")
                ),
                "score": hit.get("score"),
                "text": hit.get("text"),
                "metadata": hit.get("metadata", {}),
            }
            for hit in found.get("hits", [])
        ],
        "count": found.get("count", 0),
    }


__all__ = [
    "KnowledgeSearchUnavailable",
    "knowledge_ingest_text",
    "knowledge_search",
]
