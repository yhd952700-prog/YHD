"""In-memory prototype vector store for the embedding pipeline.

Intentionally compatible with future pgvector adoption. Uses the
Phase 2.1 registry-based provider interface and never hardwires
OpenAI or another external provider into the embedding path.

Supports insert, search, and delete operations on vector embeddings.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


class VectorStore:
    """In-memory vector store prototype.

    Stores embeddings (float lists) associated with document IDs and metadata.
    All operations are in-memory only; production would use pgvector, Pinecone,
    Qdrant, etc. but this prototype is designed for compatibility with those.
    """

    def __init__(self) -> None:
        # In-memory storage: doc_id -> (embedding, metadata)
        self._store: Dict[str, Tuple[List[float], Dict[str, Any]]] = {}

    def insert(self, doc_id: str, embedding: List[float], metadata: Optional[Dict[str, Any]] = None) -> None:
        """Insert an embedding vector associated with a document ID.

        Args:
            doc_id: Unique document identifier.
            embedding: Float vector (typically 1536-dimensional for OpenAI).
            metadata: Optional metadata dict (source, title, tags, etc.).
        """
        self._store[doc_id] = (embedding, metadata if metadata is not None else {})

    def search(
        self,
        query_embedding: List[float],
        top_k: int = 4,
        threshold: Optional[float] = None,
    ) -> List[Tuple[str, float, Dict[str, Any]]]:
        """Search for the most similar documents to a query embedding.

        Uses cosine similarity for comparison.

        Args:
            query_embedding: The query vector.
            top_k: Return at most this many results.
            threshold: Minimum similarity score to include results.

        Returns:
            List of (doc_id, similarity_score, metadata) tuples, sorted by
            similarity descending. Similarity is in [-1, 1] where 1 is identical.
        """
        if not self._store:
            return []

        results: List[Tuple[str, float, Dict[str, Any]]] = []

        for doc_id, (stored_embedding, metadata) in self._store.items():
            # Compute cosine similarity
            dot = sum(a * b for a, b in zip(query_embedding, stored_embedding))
            norm_a = sum(a * a for a in query_embedding) ** 0.5
            norm_b = sum(b * b for b in stored_embedding) ** 0.5
            if norm_a > 0 and norm_b > 0:
                similarity = dot / (norm_a * norm_b)
            else:
                similarity = 0.0

            if threshold is None or similarity >= threshold:
                results.append((doc_id, similarity, metadata))

        # Sort by similarity descending
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]

    def delete(self, doc_id: str) -> bool:
        """Delete a document by ID.

        Args:
            doc_id: The document identifier to delete.

        Returns:
            True if the document was found and deleted, False otherwise.
        """
        if doc_id in self._store:
            del self._store[doc_id]
            return True
        return False

    def count(self) -> int:
        """Return the number of stored embeddings."""
        return len(self._store)

    def get(self, doc_id: str) -> Optional[Tuple[List[float], Dict[str, Any]]]:
        """Get embedding and metadata for a document ID.

        Args:
            doc_id: The document identifier.

        Returns:
            Tuple of (embedding, metadata) or None if not found.
        """
        if doc_id in self._store:
            return self._store[doc_id]
        return None