"""Vector stores for the knowledge base.

``VectorStore`` is the original in-memory prototype (kept for tests and as the
fallback type). ``PersistentVectorStore`` adds SQLite-backed durability with the
same interface, and ``get_vector_store()`` exposes a **process-level singleton**
so a request never opens a fresh, empty index.

Hybrid retrieval (no new dependencies)
--------------------------------------
``PersistentVectorStore`` maintains an SQLite **FTS5** side index next to the
vector table and fuses both rankings with Reciprocal Rank Fusion (RRF, k=60).
Keyword-heavy queries (part numbers, ticket ids, product names) are where pure
embeddings are weakest, so the lexical path is a real recall safety net.

Chinese tokenization is intentionally dependency-free: each CJK run is split
into overlapping **bigrams** (``知识库`` -> ``知识 识库``) and ASCII words are
kept verbatim. Known limits:

* single-character CJK queries usually miss (the index stores bigrams, not
  unigrams), unless the document itself contains a one-character run;
* no synonyms / morphological analysis -- ``服务器`` will not match ``主机``;
* the index is larger than a word-segmented one (roughly N bigrams per run);
* FTS5 is compiled into CPython's bundled SQLite on all supported platforms, but
  if it is missing the store degrades to vector-only and reports that in
  ``retrieval.fts_available`` instead of failing.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger("liuhao.knowledge.vector_store")

KNOWLEDGE_DB_ENV = "LIUHAO_KNOWLEDGE_DB"

_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")
_ASCII_WORD_RE = re.compile(r"[a-z0-9_]+")


def tokenize_for_index(text: str) -> str:
    """Cheap, dependency-free tokenization: ASCII words + CJK bigrams."""
    lowered = (text or "").lower()
    tokens: List[str] = list(_ASCII_WORD_RE.findall(lowered))
    for run in _CJK_RUN_RE.findall(lowered):
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[index:index + 2] for index in range(len(run) - 1))
    return " ".join(tokens)


def query_tokens(text: str, limit: int = 32) -> List[str]:
    """Deduplicated query terms, capped so a long query cannot blow up FTS."""
    tokens: List[str] = []
    for token in tokenize_for_index(text).split():
        if token not in tokens:
            tokens.append(token)
        if len(tokens) >= limit:
            break
    return tokens


def _tokens_for_metadata(metadata: Dict[str, Any]) -> str:
    body = str(metadata.get("text", ""))
    header = str(metadata.get("context_header") or "").strip()
    return tokenize_for_index(f"{header}\n{body}" if header else body)


def reciprocal_rank_fusion(
    ranked_lists: Iterable[Iterable[str]], rrf_k: int = 60
) -> List[Tuple[str, float]]:
    """Fuse ranked id lists; ``1/(k + rank)`` makes small score scales moot."""
    scores: Dict[str, float] = {}
    for ranked in ranked_lists:
        for rank, doc_id in enumerate(ranked, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (rrf_k + rank)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))


class VectorStore:
    """In-memory vector store prototype.

    Stores embeddings (float lists) associated with document IDs and metadata.
    All operations are in-memory only; production would use pgvector, Pinecone,
    Qdrant, etc. but this prototype is designed for compatibility with those.
    """

    def __init__(self) -> None:
        # In-memory storage: doc_id -> (embedding, metadata)
        self._store: Dict[str, Tuple[List[float], Dict[str, Any]]] = {}
        # Protects both reads and writes: ``search`` iterates the dict while
        # ``insert`` may be mutating it (concurrent ingest + chat retrieval).
        self._lock = threading.RLock()

    def insert(self, doc_id: str, embedding: List[float], metadata: Optional[Dict[str, Any]] = None) -> None:
        """Insert an embedding vector associated with a document ID.

        Args:
            doc_id: Unique document identifier.
            embedding: Float vector (typically 1536-dimensional for OpenAI).
            metadata: Optional metadata dict (source, title, tags, etc.).
        """
        with self._lock:
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

        # Take an immutable snapshot under the lock, then score outside it: a
        # concurrent ``insert`` must not be able to resize the dict while we
        # iterate (``RuntimeError: dictionary changed size during iteration``).
        with self._lock:
            snapshot = tuple(self._store.items())

        results: List[Tuple[str, float, Dict[str, Any]]] = []

        for doc_id, (stored_embedding, metadata) in snapshot:
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

    def hybrid_search(
        self,
        query_text: str,
        query_embedding: List[float],
        top_k: int = 4,
        threshold: Optional[float] = None,
        rrf_k: int = 60,
    ) -> Tuple[List[Tuple[str, float, Dict[str, Any]]], Dict[str, Any]]:
        """Vector-only fallback for stores without an FTS index."""
        results = self.search(query_embedding, top_k=top_k, threshold=threshold)
        info = {
            "mode": "vector",
            "fts_available": False,
            "reason": "this store has no FTS5 side index",
            "candidate_k": top_k,
            "vector_candidates": len(results),
            "lexical_candidates": 0,
            "rrf_k": rrf_k,
        }
        return results, info

    def delete(self, doc_id: str) -> bool:
        """Delete a document by ID.

        Args:
            doc_id: The document identifier to delete.

        Returns:
            True if the document was found and deleted, False otherwise.
        """
        with self._lock:
            if doc_id in self._store:
                del self._store[doc_id]
                return True
            return False

    def replace_prefix(
        self,
        prefix: str,
        items: List[Tuple[str, List[float], Dict[str, Any]]],
    ) -> int:
        """Atomically replace every chunk whose id starts with ``prefix``.

        Re-importing a document must not leave the tail chunks of a longer
        previous version behind (they stay retrievable and poison answers). The
        whole swap is one operation: on failure nothing changes.

        Returns the number of prior chunk ids removed.
        """
        if not prefix:
            raise ValueError("replace_prefix requires a non-empty prefix")
        with self._lock:
            removed = [key for key in self._store if key.startswith(prefix)]
            for key in removed:
                del self._store[key]
            for doc_id, embedding, metadata in items:
                self._store[doc_id] = (embedding, metadata)
            return len(removed)

    def count(self) -> int:
        """Return the number of stored embeddings."""
        with self._lock:
            return len(self._store)

    def get(self, doc_id: str) -> Optional[Tuple[List[float], Dict[str, Any]]]:
        """Get embedding and metadata for a document ID.

        Args:
            doc_id: The document identifier.

        Returns:
            Tuple of (embedding, metadata) or None if not found.
        """
        with self._lock:
            return self._store.get(doc_id)


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
        self._fts_available = self._create_fts_table()
        self._load()

    def _create_fts_table(self) -> bool:
        """Create the FTS5 side index, degrading to vector-only if unavailable."""
        try:
            self._conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts
                USING fts5(
                    chunk_id UNINDEXED,
                    document_id UNINDEXED,
                    tokens
                )
                """
            )
            self._conn.commit()
            return True
        except sqlite3.OperationalError as exc:
            logger.warning(
                "FTS5 unavailable at %s (%s); hybrid search degrades to vector-only",
                self.db_path,
                exc,
            )
            return False

    def _fts_delete_prefix(self, prefix: str) -> None:
        if not self._fts_available:
            return
        # chunk ids only contain [A-Za-z0-9_.:-] + '#', so LIKE is unambiguous.
        self._conn.execute(
            "DELETE FROM knowledge_fts WHERE chunk_id LIKE ?", (f"{prefix}%",)
        )

    def _fts_upsert(self, doc_id: str, metadata: Dict[str, Any]) -> None:
        if not self._fts_available:
            return
        self._conn.execute(
            "DELETE FROM knowledge_fts WHERE chunk_id = ?", (doc_id,)
        )
        self._conn.execute(
            "INSERT INTO knowledge_fts (chunk_id, document_id, tokens) VALUES (?, ?, ?)",
            (
                doc_id,
                str(metadata.get("document_id", "")),
                _tokens_for_metadata(metadata),
            ),
        )

    def _rebuild_fts(self) -> None:
        """Index every row loaded from disk (old corpora need no rebuild script)."""
        if not self._fts_available:
            return
        with self._lock:
            self._conn.execute("DELETE FROM knowledge_fts")
            for chunk_id, (_, metadata) in self._store.items():
                self._fts_upsert(chunk_id, metadata)
            self._conn.commit()

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
        self._rebuild_fts()

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
            self._fts_upsert(doc_id, record_metadata)
            self._conn.commit()
            self._store[doc_id] = (embedding, record_metadata)

    def replace_prefix(
        self,
        prefix: str,
        items: List[Tuple[str, List[float], Dict[str, Any]]],
    ) -> int:
        """Atomically replace all chunks under ``prefix`` (DB + memory).

        The SQL delete/upserts run in a single transaction and the in-memory
        dict is only swapped *after* the commit succeeds, so a failure midway
        leaves both the database and the read path on the previous version.
        """
        if not prefix:
            raise ValueError("replace_prefix requires a non-empty prefix")
        payload = list(items)
        with self._lock:
            try:
                self._conn.execute(
                    "DELETE FROM knowledge_vectors "
                    "WHERE substr(doc_id, 1, ?) = ?",
                    (len(prefix), prefix),
                )
                self._fts_delete_prefix(prefix)
                for doc_id, embedding, metadata in payload:
                    self._fts_upsert(doc_id, metadata)
                    self._conn.execute(
                        """
                        INSERT INTO knowledge_vectors
                            (doc_id, embedding, metadata, updated_at)
                        VALUES (?, ?, ?, datetime('now'))
                        ON CONFLICT(doc_id) DO UPDATE SET
                            embedding = excluded.embedding,
                            metadata = excluded.metadata,
                            updated_at = datetime('now')
                        """,
                        (
                            doc_id,
                            json.dumps(list(embedding)),
                            json.dumps(metadata, ensure_ascii=False, default=str),
                        ),
                    )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

            removed = [key for key in self._store if key.startswith(prefix)]
            for key in removed:
                del self._store[key]
            for doc_id, embedding, metadata in payload:
                self._store[doc_id] = (embedding, metadata)
            return len(removed)

    def delete(self, doc_id: str) -> bool:
        with self._lock:
            removed = super().delete(doc_id)
            if removed:
                self._conn.execute(
                    "DELETE FROM knowledge_vectors WHERE doc_id = ?", (doc_id,)
                )
                if self._fts_available:
                    self._conn.execute(
                        "DELETE FROM knowledge_fts WHERE chunk_id = ?", (doc_id,)
                    )
                self._conn.commit()
            return removed

    def _fts_search_ids(self, query_text: str, limit: int) -> List[str]:
        """BM25-ordered chunk ids for ``query_text`` (empty when no FTS)."""
        if not self._fts_available:
            return []
        tokens = query_tokens(query_text)
        if not tokens:
            return []
        expression = " OR ".join(f'"{token}"' for token in tokens)
        try:
            rows = self._conn.execute(
                """
                SELECT chunk_id FROM knowledge_fts
                WHERE tokens MATCH ?
                ORDER BY bm25(knowledge_fts)
                LIMIT ?
                """,
                (expression, limit),
            ).fetchall()
        except sqlite3.OperationalError as exc:
            logger.warning("FTS query failed, falling back to vector-only: %s", exc)
            return []
        return [row["chunk_id"] for row in rows]

    def hybrid_search(
        self,
        query_text: str,
        query_embedding: List[float],
        top_k: int = 4,
        threshold: Optional[float] = None,
        rrf_k: int = 60,
    ) -> Tuple[List[Tuple[str, float, Dict[str, Any]]], Dict[str, Any]]:
        """Vector + BM25(FTS5) candidates fused with RRF."""
        candidate_k = max(20, top_k * 4)
        with self._lock:
            vector_results = self.search(
                query_embedding, top_k=candidate_k, threshold=threshold
            )
            vector_ids = [doc_id for doc_id, _, _ in vector_results]
            lexical_ids = self._fts_search_ids(query_text, candidate_k)

            if lexical_ids:
                fused = reciprocal_rank_fusion(
                    [vector_ids, lexical_ids], rrf_k=rrf_k
                )
                results: List[Tuple[str, float, Dict[str, Any]]] = []
                for doc_id, score in fused[:top_k]:
                    stored = self._store.get(doc_id)
                    if stored is not None:
                        results.append((doc_id, score, stored[1]))
                mode = "hybrid"
            else:
                results = vector_results[:top_k]
                mode = "vector"

            info = {
                "mode": mode,
                "fts_available": self._fts_available,
                "candidate_k": candidate_k,
                "vector_candidates": len(vector_ids),
                "lexical_candidates": len(lexical_ids),
                "rrf_k": rrf_k,
            }
        return results, info

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
