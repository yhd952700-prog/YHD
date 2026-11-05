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


__all__ = ["KnowledgeSearchUnavailable", "knowledge_search"]
