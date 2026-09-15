"""Phase 2.3 RAG pipeline prototype.

Implements a minimal retrieval augmented generation orchestration that:
query -> embeddings -> vector store -> context -> provider -> structured output.

Phase 2.4 adds a light security gate layer around the existing flow while
keeping the return payload compatible with the earlier contract:
{
  "query": "",
  "sources": [],
  "context": "",
  "answer": "",
  "metadata": {}
}
"""

from __future__ import annotations

import inspect
from typing import Any, Dict, Optional, Sequence

from src.knowledge.embedding import EmbeddingService
from src.knowledge.pii import detect_pii
from src.knowledge.retriever import Retriever
from src.knowledge.security import KnowledgeSecurityPolicy
from src.providers.registry import get_provider


class RAGPipeline:
    """Minimal RAG pipeline for the Phase 2.3 demonstration path.

    Returned structure is exactly:
    {
      "query": "",
      "sources": [],
      "context": "",
      "answer": "",
      "metadata": {}
    }
    """

    def __init__(self, vector_store: Any, provider_name: str = "mock"):
        self.vector_store = vector_store
        self.provider_name = provider_name
        self.policy = KnowledgeSecurityPolicy()
        self.embedding_service = EmbeddingService(provider_name)
        self.retriever = Retriever(vector_store, provider_name=provider_name)

    async def query(self, query: str, limit: int = 5, user: Optional[Any] = None, documents: Optional[Sequence[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """Execute the complete RAG flow for a plain-text user question.

        Security hook:
        - validate retrieval before vector search
        - filter content after LLM answer generation
        - audit security metadata before return
        """

        # permission validation of retrieval path before vector search
        docs_for_access = list(documents or [])
        access = self.policy.validate_retrieval(user, docs_for_access)
        if not access.get("allowed", True):
            return {
                "query": query,
                "sources": [],
                "context": "",
                "answer": "Access denied by knowledge security policy.",
                "metadata": {
                    "provider": self.provider_name,
                    "retrieval": "vector_similarity",
                    "security_status": "denied",
                    "pii_detected": False,
                    "filtered": False,
                    "policy_version": self.policy.policy_version,
                    "reason": access.get("reason"),
                },
            }

        hits = await self.retriever.search(query, limit=limit)
        context = self.retriever.assemble_context(hits)

        # Context security check to keep small but consistent.
        pii_context = detect_pii(context)
        security_status = "passed"
        context = self.policy.filter_content(context)

        provider = get_provider(self.provider_name)
        prompt = (
            "Use the supplied context to answer the query.\n\n"
            f"Query: {query}\n\nContext:\n{context}"
        )
        if hasattr(provider, "chat"):
            chat_result = provider.chat([{"role": "user", "content": prompt}])
            # The provider contract allows ``chat`` to be a coroutine; resolve
            # it when necessary so both sync and async providers work.
            if inspect.isawaitable(chat_result):
                chat_result = await chat_result
            # ``chat`` returns a dict with a "choices" list per the LLMProvider
            # interface; extract the assistant message content, falling back to
            # str() for any non-standard payload.
            if isinstance(chat_result, dict) and "choices" in chat_result:
                choice = chat_result["choices"][0]
                answer = (
                    choice.get("message", {}).get("content", "")
                    if isinstance(choice, dict)
                    else str(choice)
                )
            else:
                answer = str(chat_result)
        else:
            answer = context[:200]

        filtered_answer = self.policy.filter_content(answer)
        filtered = answer != filtered_answer
        pii_answer = detect_pii(answer)
        pii_detected = pii_answer["detected"] or pii_context["detected"]

        sources = []
        for hit in hits:
            sources.append({
                "chunk_id": hit.get("chunk_id"),
                "document_id": hit.get("document_id"),
                "score": hit.get("score"),
                "content": hit.get("content"),
                "metadata": hit.get("metadata", {}),
            })

        metadata = {
            "provider": self.provider_name,
            "retrieval": "vector_similarity",
            "security": {
                "permission_check": "available",
                "source_tracking": "document_id",
                "retrieval_audit": "enabled",
            },
            "hits": len(hits),
            "security_status": security_status,
            "pii_detected": bool(pii_detected),
            "filtered": bool(filtered),
            "policy_version": self.policy.policy_version,
            "security_check": "pii_scan",
            "pii_types": list(dict.fromkeys(pii_context["types"] + pii_answer["types"])),
        }

        # record stable security event
        self.policy.audit_security_event({
            "event_type": "retrieval",
            "query": query,
            "documents": [s.get("document_id") for s in sources],
            "provider": self.provider_name,
            "timestamp": "now",
            "status": security_status,
            "security_status": security_status,
            "pii_detected": bool(pii_detected),
            "filtered": bool(filtered),
            "policy_version": self.policy.policy_version,
        })

        return {
            "query": query,
            "sources": sources,
            "context": context,
            "answer": filtered_answer,
            "metadata": metadata,
        }
