"""RAG (Retrieval-Augmented Generation) pipeline.

Phase 2.3 RAG pipeline that connects:
Query -> Embedding -> Vector Store Search -> Context Generation -> LLM Provider -> Structured Output

The data flow is:
User Query -> Embedding Service -> Vector Store Search -> Context Generation -> LLM Provider -> Output
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .retriever import Retriever
from .vector_store import VectorStore


class RAGPipeline:
    """Phase 2.3 RAG pipeline that connects retrieval to generation."""

    def __init__(
        self,
        retriever: Retriever,
        provider: Any,  # LLMProvider instance
        top_k: int = 4,
        similarity_threshold: float = 0.3,
    ) -> None:
        self.retriever = retriever
        self.provider = provider
        self.top_k = top_k
        self.similarity_threshold = similarity_threshold

    def query(self, user_query: str) -> Dict[str, Any]:
        """Run a full RAG query: retrieve context and generate answer.

        Data Flow:
        User Query -> Retriever (Embedding + Vector Search) -> Context Generation -> LLM Provider -> Structured Output

        Args:
            user_query: The user's natural language question.

        Returns:
            Dict matching the Phase 2.3 contract:
            {
                "query": user_query,
                "sources": [doc_id, ...],
                "context": concatenated context text,
                "answer": LLM-generated answer,
                "metadata": {pipeline metadata}
            }
        """
        # Step 1: Retrieve relevant context
        retrieval_result = self.retriever.retrieve(user_query)

        # Step 2: Build the prompt with context
        context = retrieval_result["context"]
        sources = retrieval_result["sources"]

        prompt = self._build_prompt(user_query, context)

        # Step 3: Generate answer via LLM provider
        try:
            llm_response = self.provider.chat(
                messages=[{"role": "user", "content": prompt}],
            )
            answer = llm_response.get("choices", [{}])[0].get(
                "message", {}
            ).get("content", "")
        except Exception:
            answer = self._fallback_answer(user_query, context)

        # Step 4: Return structured output
        return {
            "query": user_query,
            "sources": sources,
            "context": context,
            "answer": answer,
            "metadata": {
                "retrieval_results_count": retrieval_result["metadata"][
                    "results_count"
                ],
                "similarity_threshold": self.similarity_threshold,
                "provider": self.provider.name if hasattr(self.provider, "name") else "unknown",
            },
        }

    def _build_prompt(self, query: str, context: str) -> str:
        """Build the prompt for the LLM provider.

        Args:
            user_query: The original user query.
            context: Retrieved context text.

        Returns:
            A formatted prompt string.
        """
        return (
            "Context:\n"
            f"{context}\n\n"
            f"Question: {query}\n\n"
            "Answer the question based on the context provided above. If the context doesn't contain the answer, say \"I don't have enough information to answer this question.\""
        )

    def _fallback_answer(self, query: str, context: str) -> str:
        """Fallback answer when LLM provider chat fails."""
        if context.strip():
            return (
                f"Based on the available context: {context[:200]}...\n\n"
                f"Direct answer to '{query}': I don't have enough information to provide a complete answer, but the context above may be relevant."
            )
        return f"I don't have enough information to answer the question: '{query}'"


def rag_query(
    user_query: str,
    vector_store: VectorStore,
    embedding_pipeline: EmbeddingPipeline,
    provider: Any,
    top_k: int = 4,
    similarity_threshold: float = 0.3,
) -> Dict[str, Any]:
    """Convenience function to run a RAG query.

    Args:
        user_query: The user's question.
        vector_store: Initialized VectorStore instance.
        embedding_pipeline: Initialized EmbeddingPipeline instance.
        provider: LLMProvider instance (e.g. MockRiskAssessmentProvider).
        top_k: Number of retrieval results.
        similarity_threshold: Minimum similarity threshold.

    Returns:
        Dict matching the Phase 2.3 contract.
    """
    retriever = Retriever(
        vector_store=vector_store,
        embedding_pipeline=embedding_pipeline,
        top_k=top_k,
        similarity_threshold=similarity_threshold,
    )

    rag = RAGPipeline(
        retriever=retriever,
        provider=provider,
        top_k=top_k,
        similarity_threshold=similarity_threshold,
    )

    return rag.query(user_query)