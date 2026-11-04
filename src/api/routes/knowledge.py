"""Knowledge base HTTP surface (live).

Canonical prefix is ``/v1/knowledge`` to match the rest of the gateway;
``/knowledge`` is kept as a deprecated alias so existing clients (and the
single-port reserved-path list) keep working. Both are behind the human-principal
gate, so ingest (POST) additionally requires an admin role / L0 scope.

Retrieval and ingest go through :class:`KnowledgeDocumentService`, which uses
the process-level SQLite vector store and a **real** embedding provider. When no
provider is configured the endpoints answer 503 with "未启用" -- they never
return fabricated vectors or a mock-written answer.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.knowledge.documents import get_knowledge_service
from src.knowledge.embedding import EmbeddingUnavailableError

core = APIRouter(tags=["knowledge"])


class IngestRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=200_000)
    doc_id: Optional[str] = Field(default=None, max_length=128)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=8000)
    top_k: int = Field(default=4, ge=1, le=20)
    threshold: Optional[float] = Field(default=None, ge=-1.0, le=1.0)


def _unavailable(exc: Exception) -> HTTPException:
    return HTTPException(status_code=503, detail=f"知识库未启用：{exc}")


@core.get("/status")
def knowledge_status() -> Dict[str, Any]:
    """Always 200: lets the console show "未启用" instead of hiding the feature."""
    return get_knowledge_service().status()


@core.post("/documents")
def knowledge_ingest(payload: IngestRequest) -> Dict[str, Any]:
    try:
        return get_knowledge_service().ingest(
            payload.text, doc_id=payload.doc_id, metadata=payload.metadata
        )
    except EmbeddingUnavailableError as exc:
        raise _unavailable(exc)
    except (OSError, sqlite3.Error) as exc:
        raise HTTPException(status_code=503, detail=f"向量库不可用：{exc}")


@core.post("/search")
def knowledge_search(payload: SearchRequest) -> Dict[str, Any]:
    try:
        return get_knowledge_service().search(
            payload.query, top_k=payload.top_k, threshold=payload.threshold
        )
    except EmbeddingUnavailableError as exc:
        raise _unavailable(exc)
    except (OSError, sqlite3.Error) as exc:
        raise HTTPException(status_code=503, detail=f"向量库不可用：{exc}")


@core.post("/query", deprecated=True)
def knowledge_query(payload: SearchRequest) -> Dict[str, Any]:
    """Retrieval-only RAG contract. Generation stays on ``/v1/chat``.

    The old handler answered with a mock-written ``answer``; returning that from
    a production endpoint is exactly the "pretend it works" failure this pass
    removes. ``answer`` is therefore empty and the hits are real.
    """
    try:
        result = get_knowledge_service().search(
            payload.query, top_k=payload.top_k, threshold=payload.threshold
        )
    except EmbeddingUnavailableError as exc:
        raise _unavailable(exc)
    except (OSError, sqlite3.Error) as exc:
        raise HTTPException(status_code=503, detail=f"向量库不可用：{exc}")

    hits = result["hits"]
    return {
        "query": payload.query,
        "sources": [
            {
                "doc_id": hit["doc_id"],
                "score": hit["score"],
                "content": hit["text"],
                "metadata": hit["metadata"],
            }
            for hit in hits
        ],
        "context": "\n\n".join(hit["text"] for hit in hits if hit["text"]),
        "answer": "",
        "metadata": {
            "provider": None,
            "retrieval": "vector_similarity",
            "hits": len(hits),
            "note": "仅检索；生成回答请调用 /v1/chat",
        },
    }


router = APIRouter(prefix="/v1/knowledge", tags=["knowledge"])
router.include_router(core)

#: Deprecated compatibility alias. Kept because the console's reserved-prefix
#: list and existing clients still reference ``/knowledge``.
legacy_router = APIRouter(prefix="/knowledge", tags=["knowledge"])
legacy_router.include_router(core, deprecated=True)
