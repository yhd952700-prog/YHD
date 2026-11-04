"""Vector stores for the knowledge base.

``VectorStore`` is the original in-memory prototype (kept for tests and as the
fallback type). ``PersistentVectorStore`` adds SQLite-backed durability with the
same interface, and ``get_vector_store()`` exposes a **process-level singleton**
so a request never opens a fresh, empty index.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("liuhao.knowledge.vector_store")

KNOWLEDGE_DB_ENV = "LIUHAO_KNOWLEDGE_DB"


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


def default_db_path() -> Path:
    """SQLite file for knowledge vectors (persisted under ``data/`` by default)."""
    configured = (os.environ.get(KNOWLEDGE_DB_ENV) or "").strip()
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[2] / "data" / "knowledge_vectors.sqlite3"


class PersistentVectorStore(VectorStore):
    """SQLite-backed vector store: same interface, survives restarts.

    The in-memory dict stays the read path (search is O(n) cosine either way);
    every write is upserted to SQLite first so a crash cannot leave the process
    serving vectors that were never persisted.
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        super().__init__()
        self.db_path = Path(db_path) if db_path else default_db_path()
        self._lock = threading.RLock()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS knowledge_vectors (
                doc_id TEXT PRIMARY KEY,
                embedding TEXT NOT NULL,
                metadata TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        self._conn.commit()
        self._load()

    def _load(self) -> None:
        rows = self._conn.execute(
            "SELECT doc_id, embedding, metadata FROM knowledge_vectors"
        ).fetchall()
        for row in rows:
            try:
                embedding = json.loads(row["embedding"])
                metadata = json.loads(row["metadata"] or "{}")
                if not isinstance(embedding, list):
                    raise ValueError("embedding is not a list")
            except (TypeError, ValueError):
                logger.error(
                    "knowledge vector %r is corrupt; skipped on load", row["doc_id"]
                )
                continue
            self._store[row["doc_id"]] = (embedding, metadata)

    def insert(
        self, doc_id: str, embedding: List[float], metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        record_metadata = metadata if metadata is not None else {}
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO knowledge_vectors (doc_id, embedding, metadata, updated_at)
                VALUES (?, ?, ?, datetime('now'))
                ON CONFLICT(doc_id) DO UPDATE SET
                    embedding = excluded.embedding,
                    metadata = excluded.metadata,
                    updated_at = datetime('now')
                """,
                (
                    doc_id,
                    json.dumps(list(embedding)),
                    json.dumps(record_metadata, ensure_ascii=False, default=str),
                ),
            )
            self._conn.commit()
            self._store[doc_id] = (embedding, record_metadata)

    def delete(self, doc_id: str) -> bool:
        with self._lock:
            removed = super().delete(doc_id)
            if removed:
                self._conn.execute(
                    "DELETE FROM knowledge_vectors WHERE doc_id = ?", (doc_id,)
                )
                self._conn.commit()
            return removed

    def close(self) -> None:
        with self._lock:
            self._conn.close()


_STORE: Optional[VectorStore] = None
_STORE_LOCK = threading.Lock()


def get_vector_store(db_path: Optional[str] = None) -> VectorStore:
    """Process-level singleton; raises (fail-closed) if the DB cannot open."""
    global _STORE
    if _STORE is not None:
        return _STORE
    with _STORE_LOCK:
        if _STORE is None:
            try:
                _STORE = PersistentVectorStore(db_path)
            except (sqlite3.Error, OSError) as exc:
                logger.error(
                    "knowledge vector store unavailable at %s: %s",
                    db_path or default_db_path(),
                    exc,
                )
                raise
    return _STORE


def reset_vector_store() -> None:
    """Test helper: drop the singleton (does not delete the file)."""
    global _STORE
    with _STORE_LOCK:
        _STORE = None
