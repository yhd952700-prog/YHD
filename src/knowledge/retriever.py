"""Retriever for the RAG pipeline.

Responsible for:
- Query embedding generation via the Phase 2.1 provider registry
- Vector similarity search in the VectorStore
- Result ranking and context selection
"""

from __future__ import annotations

from typing import Any, Dict

from .embedding import EmbeddingPipeline
from .vector_store import VectorStore


class Retriever:
    """Retrieves relevant context for a user query using embedding + vector search.

    Data Flow:
    User Query -> Embedding -> Vector Store Search -> Context Selection -> LLM Provider
    """

    def __init__(
        self,
        vector_store: VectorStore,
        embedding_pipeline: EmbeddingPipeline,
        top_k: int = 4,
        similarity_threshold: float = 0.3,
    ) -> None:
        self.vector_store = vector_store
        self.embedding_pipeline = embedding_pipeline
        self.top_k = top_k
        self.similarity_threshold = similarity_threshold

    def retrieve(self, query: str) -> Dict[str, Any]:
        """Retrieve relevant context chunks for a query.

        Args:
            query: The user query string.

        Returns:
            Dict with keys:
                - "sources": list of doc IDs that were retrieved
                - "context": concatenated context text from retrieved sources
                - "metadata": additional metadata about the retrieval operation
        """
        # Step 1: Generate embedding for the query
        query_embedding = self.embedding_pipeline.embed(query)

        # Step 2: Search the vector store
        search_results = self.vector_store.search(
            query_embedding,
            top_k=self.top_k,
            threshold=self.similarity_threshold,
        )

        # Step 3: Extract sources and build context
        sources = []
        context_parts = []
        metadata = {
            "query": query,
            "top_k": self.top_k,
            "threshold": self.similarity_threshold,
            "results_count": len(search_results),
        }

        for doc_id, similarity, metadata in search_results:
            sources.append(doc_id)
            # In a real implementation, we'd fetch the original text from
            # a document store. Here we use the metadata as context.
            if metadata:
                context_parts.append(str(metadata))

        # Join context parts with a separator
        context = "\n\n".join(context_parts) if context_parts else ""

        return {
            "sources": sources,
            "context": context,
            "metadata": metadata,
        }

    def retrieve_with_scores(
        self, query: str
    ) -> Dict[str, Any]:
        """Retrieve with detailed similarity scores included.

        Returns additional score information for each source.
        """
        query_embedding = self.embedding_pipeline.embed(query)
        search_results = self.vector_store.search(
            query_embedding,
            top_k=self.top_k,
            threshold=self.similarity_threshold,
        )

        sources_with_scores = []
        context_parts = []

        for doc_id, similarity, metadata in search_results:
            sources_with_scores.append(
                {"doc_id": doc_id, "similarity": round(similarity, 4)}
            )
            if metadata:
                context_parts.append(str(metadata))

        context = "\n\n".join(context_parts) if context_parts else ""

        return {
            "sources": sources_with_scores,
            "context": context,
            "metadata": {
                "query": query,
                "results_count": len(search_results),
                "threshold": self.similarity_threshold,
            },
        }
