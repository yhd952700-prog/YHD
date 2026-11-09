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

The lexical list is fused with its **BM25 strength**, not a flat rank weight
(``weight/(rrf_k+rank)``, ``weight`` normalized so the best hit is 1.0). Plain
RRF throws away BM25 magnitude, and that cost section precision on a real
semantic model: one exact match (BM25 ``-22.6``) lost to two medium matches
from sibling sections (BM25 ``-5.9``) because they occupied more ranks, so
the document was found while the correct section was not in the top 5
(``section_hit@5 = 0.0``). Measured 2026-11-06; the trade-off is recorded in
``evaluation``: the hashing baseline keeps recall/abstention but loses ~0.017
MRR because BM25 slightly prefers a distractor document, while the real model
gained recall, MRR and section precision.

Tried and ineffective for that defect (kept here so it is not re-attempted):

* raising ``candidate_k`` at the evaluation's ``top_k=10`` -- all 36 chunks
  already fit the 40-candidate window, so it changed nothing there. But at the
  gateway's ``top_k=4`` the old floor of 20 dropped the correct chunk from the
  vector list, leaving it lexical-only and behind a distractor; the floor is
  now 40 and the E2E citation points at the correct section first;
* shrinking chunks -- the failing section is a single short paragraph;
* dropping ``context_header`` or adding ``search_query:`` / ``search_document:``
  prefixes -- the correct chunk stayed at vector rank 30+ in all four variants.


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
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from .abstention import (
    NO_RELEVANT,
    STATUS_OK,
    AbstentionConfig,
    assess_confidence,
)
from ..common.data_paths import resolve_store_path
from ..common.input_guard import ensure_json_serializable

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


def cosine_similarity(left: List[float], right: List[float]) -> float:
    """Cosine similarity in [-1, 1]; 0.0 when either vector is all zeros.

    维度不一致（换过 embedding 模型/维度）返回 0.0 而不是 ``zip`` 静默截断：
    截断后的点积仍然"看起来正常"，会把错配向量混进检索结果。调用方通过
    ``dimension_mismatch_count`` / 状态接口暴露该情况，要求重建索引。
    """
    if len(left) != len(right):
        _record_dimension_mismatch(len(left), len(right))
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    norm_left = sum(a * a for a in left) ** 0.5
    norm_right = sum(b * b for b in right) ** 0.5
    if norm_left <= 0 or norm_right <= 0:
        return 0.0
    return dot / (norm_left * norm_right)


_DIM_MISMATCH_LOCK = threading.Lock()
_DIM_MISMATCH_PAIRS: set = set()


def _record_dimension_mismatch(left_dim: int, right_dim: int) -> None:
    """同一个维度组合只告警一次，避免每次检索刷屏。"""
    pair = (int(left_dim), int(right_dim))
    with _DIM_MISMATCH_LOCK:
        if pair in _DIM_MISMATCH_PAIRS:
            return
        _DIM_MISMATCH_PAIRS.add(pair)
    logger.error(
        "knowledge vector dimension mismatch: query=%d stored=%d; "
        "该向量已跳过（不做 zip 静默截断），请执行重建索引",
        left_dim,
        right_dim,
    )


def _document_id_of(chunk_id: str, metadata: Dict[str, Any]) -> str:
    """Document id for a chunk row; legacy rows only have the ``doc#n`` id."""
    value = metadata.get("document_id")
    if isinstance(value, str) and value:
        return value
    return chunk_id.split("#", 1)[0]


def _chunk_index_of(chunk_id: str, metadata: Dict[str, Any]) -> int:
    value = metadata.get("chunk_index")
    if isinstance(value, int):
        return value
    suffix = chunk_id.rsplit("#", 1)[-1]
    return int(suffix) if suffix.isdigit() else 0


def reciprocal_rank_fusion(
    ranked_lists: Iterable[Iterable[str]], rrf_k: int = 60
) -> List[Tuple[str, float]]:
    """Fuse ranked id lists; ``1/(k + rank)`` makes small score scales moot."""
    scores: Dict[str, float] = {}
    for ranked in ranked_lists:
        for rank, doc_id in enumerate(ranked, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (rrf_k + rank)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))


def weighted_reciprocal_rank_fusion(
    vector_ranked: Iterable[str],
    lexical_ranked: Iterable[Tuple[str, float]],
    rrf_k: int = 60,
) -> List[Tuple[str, float]]:
    """RRF with the lexical list scaled by its normalized BM25 strength.

    The vector list contributes ``1/(k + rank)`` exactly as plain RRF. The
    lexical list contributes ``weight/(k + rank)`` where ``weight`` is in
    ``(0, 1]`` (1.0 for the best BM25 hit). A single strong keyword hit can
    therefore outrank several weak sibling hits, which plain RRF cannot express
    because it only sees positions.
    """
    scores: Dict[str, float] = {}
    for rank, chunk_id in enumerate(vector_ranked, start=1):
        scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (rrf_k + rank)
    for rank, (chunk_id, weight) in enumerate(lexical_ranked, start=1):
        scores[chunk_id] = scores.get(chunk_id, 0.0) + weight / (rrf_k + rank)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))


def _metadata_owner(metadata: Optional[Dict[str, Any]]) -> str:
    """Chunk 归属账号；没有该字段的历史数据视为"无归属"（老部署公共库）。"""
    return str((metadata or {}).get("owner_account_id") or "").strip()


def _owner_visible(
    metadata: Optional[Dict[str, Any]],
    owners: Optional[Iterable[str]],
    include_unowned: bool,
) -> bool:
    """``owners=None`` 表示不限制（主账号聚合读）；空归属单独开关。"""
    owner = _metadata_owner(metadata)
    if not owner:
        return bool(include_unowned)
    if owners is None:
        return True
    return owner in owners


class VectorStore:
    """In-memory vector store prototype.

    Stores embeddings (float lists) associated with document IDs and metadata.
    All operations are in-memory only; production would use pgvector, Pinecone,
    Qdrant, etc. but this prototype is designed for compatibility with those.
    """

    def __init__(self) -> None:
        # In-memory storage: doc_id -> (embedding, metadata)
        self._store: Dict[str, Tuple[List[float], Dict[str, Any]]] = {}
        # content_hash -> document_id (content-level dedup across file names)
        self._content_hashes: Dict[str, str] = {}
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
        *,
        owners: Optional[Iterable[str]] = None,
        include_unowned: bool = True,
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
            if not _owner_visible(metadata, owners, include_unowned):
                continue
            similarity = cosine_similarity(query_embedding, stored_embedding)
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
        abstain: bool = True,
        config: Optional[AbstentionConfig] = None,
        *,
        owners: Optional[Iterable[str]] = None,
        include_unowned: bool = True,
    ) -> Tuple[List[Tuple[str, float, Dict[str, Any]]], Dict[str, Any]]:
        """Vector-only fallback for stores without an FTS index.

        Returns ``(hits, retrieval)``. ``retrieval`` carries the finalized
        contract fields (``status`` / ``reason`` / ``top_score`` /
        ``runner_up_score`` / ``thresholds``); ``hits`` is empty whenever the
        status is ``no_relevant``.
        """
        results = self.search(
            query_embedding,
            top_k=top_k,
            threshold=threshold,
            owners=owners,
            include_unowned=include_unowned,
        )
        with self._lock:
            empty_corpus = not any(
                _owner_visible(meta, owners, include_unowned)
                for _, (_, meta) in self._store.items()
            )
        assessment = assess_confidence(
            {doc_id: score for doc_id, score, _ in results},
            0,
            config=config,
            empty_corpus=empty_corpus,
            zero_score_allowed=threshold is not None and threshold <= 0.0,
        )
        gated = abstain and not assessment["confident"]
        if gated:
            results = []
        no_relevant = gated or not results
        info = {
            "mode": "vector",
            "fts_available": False,
            "status": NO_RELEVANT if no_relevant else STATUS_OK,
            "reason": assessment["reason"] if no_relevant else None,
            "top_score": assessment["top_score"],
            "runner_up_score": assessment["runner_up_score"],
            "thresholds": assessment["thresholds"],
            "abstained": gated,
            "confidence": assessment,
            "notes": "this store has no FTS5 side index",
            "candidate_k": top_k,
            "vector_candidates": assessment["candidate_count"],
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

    # -- index metadata / maintenance（基类内存实现，持久化子类覆盖） --------
    def get_index_meta(self) -> Dict[str, str]:
        return {}

    def set_index_meta(self, **values: Any) -> None:
        return None

    def dimension_distribution(self) -> Dict[int, int]:
        with self._lock:
            counts: Dict[int, int] = {}
            for embedding, _ in self._store.values():
                dimension = len(embedding)
                counts[dimension] = counts.get(dimension, 0) + 1
            return counts

    def raw_documents(
        self,
        *,
        owners: Optional[Iterable[str]] = None,
        include_unowned: bool = True,
    ) -> List[Dict[str, Any]]:
        """按文档聚合的原始记录（迁移/重建索引用，含 owner 与展示名）。"""
        with self._lock:
            snapshot = list(self._store.items())
        documents: Dict[str, Dict[str, Any]] = {}
        for chunk_id, (_, metadata) in snapshot:
            if not _owner_visible(metadata, owners, include_unowned):
                continue
            document_id = _document_id_of(chunk_id, metadata)
            entry = documents.get(document_id)
            if entry is None:
                entry = {
                    "document_id": document_id,
                    "owner": _metadata_owner(metadata),
                    "filename": metadata.get("filename")
                    or metadata.get("source")
                    or metadata.get("title"),
                    "chunks": 0,
                }
                documents[document_id] = entry
            entry["chunks"] += 1
        return list(documents.values())

    def get_chunk_records(self, document_id: str) -> List[Tuple[str, List[float], Dict[str, Any]]]:
        """完整 chunk 记录（含向量与全部元数据），供重建索引复用。"""
        prefix = f"{document_id}#"
        with self._lock:
            rows = [
                (chunk_id, list(embedding), dict(metadata))
                for chunk_id, (embedding, metadata) in self._store.items()
                if chunk_id.startswith(prefix)
            ]
        rows.sort(key=lambda item: _chunk_index_of(item[0], item[2]))
        return rows

    def rename_document(self, old_id: str, new_id: str) -> Dict[str, Any]:
        """单文档改名（原子）；基类内存实现只改读路径。"""
        if not old_id or not new_id or old_id == new_id:
            return {"renamed": False, "reason": "same_id", "chunks": 0}
        prefix_old = f"{old_id}#"
        with self._lock:
            rows = [
                (chunk_id, value)
                for chunk_id, value in self._store.items()
                if chunk_id.startswith(prefix_old)
            ]
            if not rows:
                return {"renamed": False, "reason": "not_found", "chunks": 0}
            for key, _ in rows:
                self._store.pop(key, None)
            for old_chunk, (embedding, metadata) in rows:
                index = _chunk_index_of(old_chunk, metadata)
                new_chunk = f"{new_id}#{index}"
                new_meta = dict(metadata)
                new_meta["document_id"] = new_id
                new_meta["chunk_index"] = index
                self._store[new_chunk] = (embedding, new_meta)
            return {"renamed": True, "reason": None, "chunks": len(rows)}

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

    def upsert_document(
        self,
        prefix: str,
        items: List[Tuple[str, List[float], Dict[str, Any]]],
        content_hash: str,
        document_id: str,
    ) -> Dict[str, Any]:
        """Content-addressed insert: identical text owned by another doc is skipped."""
        with self._lock:
            existing = self._content_hashes.get(content_hash)
            if existing is not None and existing != document_id:
                return {
                    "deduplicated": True,
                    "matched_document_id": existing,
                    "removed": 0,
                }
            removed = self.replace_prefix(prefix, items)
            for hash_value, owner in list(self._content_hashes.items()):
                if owner == document_id:
                    del self._content_hashes[hash_value]
            self._content_hashes[content_hash] = document_id
            return {
                "deduplicated": False,
                "matched_document_id": None,
                "removed": removed,
            }

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

    # -- document administration (list / read all chunks / delete) ---------
    def list_documents(
        self,
        limit: int = 50,
        offset: int = 0,
        *,
        owners: Optional[Iterable[str]] = None,
        include_unowned: bool = True,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """Document summaries aggregated from chunks (in-memory store)."""
        limit = max(1, int(limit))
        offset = max(0, int(offset))
        with self._lock:
            snapshot = tuple(self._store.items())
            owned_hashes = {
                owner: value for value, owner in self._content_hashes.items()
            }
        documents: Dict[str, Dict[str, Any]] = {}
        for chunk_id, (_, metadata) in snapshot:
            meta = metadata or {}
            if not _owner_visible(meta, owners, include_unowned):
                continue
            document_id = _document_id_of(chunk_id, meta)
            entry = documents.get(document_id)
            if entry is None:
                entry = {
                    "document_id": document_id,
                    "chunks": 0,
                    "source": meta.get("source") or meta.get("filename"),
                    "title": meta.get("title"),
                    "file_type": meta.get("file_type"),
                    "page_count": meta.get("page_count"),
                    "content_hash": owned_hashes.get(document_id),
                    "ingested_at": None,
                }
                documents[document_id] = entry
            entry["chunks"] += 1
        ordered = sorted(documents.values(), key=lambda item: item["document_id"])
        return ordered[offset:offset + limit], len(ordered)

    def get_document_chunks(self, document_id: str) -> List[Dict[str, Any]]:
        """Every chunk of one document, ordered by ``chunk_index``."""
        prefix = f"{document_id}#"
        with self._lock:
            entries = [
                (chunk_id, metadata or {})
                for chunk_id, (_, metadata) in self._store.items()
                if chunk_id.startswith(prefix)
            ]
        entries.sort(key=lambda item: _chunk_index_of(item[0], item[1]))
        return [
            {
                "chunk_id": chunk_id,
                "chunk_index": _chunk_index_of(chunk_id, meta),
                "text": str(meta.get("text", "")),
                "heading_path": list(meta.get("heading_path") or []),
                "context_header": str(meta.get("context_header") or ""),
                "owner_account_id": _metadata_owner(meta),
                "page": meta.get("page"),
                "pages": (
                    meta.get("pages")
                    or ([meta["page"]] if isinstance(meta.get("page"), int) else [])
                ),
                "page_label": (
                    (
                        f"第 {meta['pages'][0]}-{meta['pages'][-1]} 页"
                        if len(meta.get("pages") or []) > 1
                        else f"第 {meta['page']} 页"
                    )
                    if isinstance(meta.get("page"), int)
                    else None
                ),
            }
            for chunk_id, meta in entries
        ]

    def delete_document(self, document_id: str) -> Dict[str, Any]:
        """Delete one document and every index entry that points at it."""
        prefix = f"{document_id}#"
        with self._lock:
            keys = [key for key in self._store if key.startswith(prefix)]
            for key in keys:
                del self._store[key]
            had_hash = False
            for value, owner in list(self._content_hashes.items()):
                if owner == document_id:
                    del self._content_hashes[value]
                    had_hash = True
            return {
                "deleted": bool(keys) or had_hash,
                "chunks_removed": len(keys),
            }


def default_db_path() -> Path:
    """SQLite file for knowledge vectors.

    ``LIUHAO_KNOWLEDGE_DB`` > ``LIUHAO_DATA_DIR/knowledge_vectors.sqlite3`` >
    ``<repo>/data/knowledge_vectors.sqlite3`` (see ``src.common.data_paths``).
    """
    return Path(
        resolve_store_path(
            "knowledge_vectors.sqlite3",
            Path(__file__).resolve().parents[2] / "data" / "knowledge_vectors.sqlite3",
            env_var=KNOWLEDGE_DB_ENV,
        )
    )


def _like_prefix_pattern(prefix: str) -> str:
    """把 ``prefix`` 变成"只匹配它自己开头"的 LIKE 模式。

    document id 里允许出现 ``_``（``inspect_a_zone.md``）和 ``%``，而 SQL 的
    ``LIKE`` 把这两个字符当通配符：``'inspect_a_zone.md#%'`` 会匹配到
    ``inspectxa_zone.md#0``。删除时这是**静默数据损坏**——被误删的那份文档
    向量还在，只有词法行消失，检索质量下降却没有任何报错。所以这里逐个转义
    ``\\`` / ``%`` / ``_``，并在调用处显式声明 ``ESCAPE '\\'``。
    """
    escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"{escaped}%"


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
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS knowledge_document_hashes (
                content_hash TEXT PRIMARY KEY,
                document_id TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS knowledge_index_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL DEFAULT '',
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
        # chunk id 形如 ``<document_id>#<index>``，而 document id 里可以有 ``_``
        # 和 ``%``；它们同时是 LIKE 的通配符。这里必须转义并显式 ESCAPE，
        # 让语义真的是"精确前缀"，否则删 ``inspect_a_zone.md`` 会连
        # ``inspectxa_zone.md`` 的词法行一起删掉（见 _like_prefix_pattern）。
        self._conn.execute(
            "DELETE FROM knowledge_fts WHERE chunk_id LIKE ? ESCAPE '\\'",
            (_like_prefix_pattern(prefix),),
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
                if not isinstance(metadata, dict):
                    raise ValueError("metadata is not an object")
                # 旧版本可能超过网关的嵌套上限后仍被落库：这类行在检索结果
                # 序列化时会抛 ``RecursionError``（对外表现为 400），
                # 让整个 search/query 长期失败。加载阶段就跳过它。
                ensure_json_serializable(metadata, "metadata")
            except (TypeError, ValueError, RecursionError):
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
                self._write_chunks_locked(prefix, payload)
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
            return self._swap_memory_locked(prefix, payload)

    def _write_chunks_locked(
        self,
        prefix: str,
        payload: List[Tuple[str, List[float], Dict[str, Any]]],
    ) -> None:
        """Delete+reinsert chunks under ``prefix``; caller owns lock/transaction."""
        self._conn.execute(
            "DELETE FROM knowledge_vectors WHERE substr(doc_id, 1, ?) = ?",
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

    def _swap_memory_locked(
        self,
        prefix: str,
        payload: List[Tuple[str, List[float], Dict[str, Any]]],
    ) -> int:
        """Replace in-memory chunks after a successful commit; caller owns lock."""
        removed = [key for key in self._store if key.startswith(prefix)]
        for key in removed:
            del self._store[key]
        for doc_id, embedding, metadata in payload:
            self._store[doc_id] = (embedding, metadata)
        return len(removed)

    def upsert_document(
        self,
        prefix: str,
        items: List[Tuple[str, List[float], Dict[str, Any]]],
        content_hash: str,
        document_id: str,
    ) -> Dict[str, Any]:
        """Content-level dedup + atomic replace, in one lock and one transaction.

        If the same normalized text is already owned by a different
        ``document_id`` nothing is written and the caller gets
        ``matched_document_id`` back. Old hash rows for this document are
        released on every successful re-import, so editing a document frees its
        previous content.
        """
        if not prefix:
            raise ValueError("upsert_document requires a non-empty prefix")
        payload = list(items)
        with self._lock:
            try:
                row = self._conn.execute(
                    "SELECT document_id FROM knowledge_document_hashes "
                    "WHERE content_hash = ?",
                    (content_hash,),
                ).fetchone()
                if row is not None and row["document_id"] != document_id:
                    return {
                        "deduplicated": True,
                        "matched_document_id": row["document_id"],
                        "removed": 0,
                    }
                self._conn.execute(
                    "DELETE FROM knowledge_document_hashes WHERE document_id = ?",
                    (document_id,),
                )
                self._conn.execute(
                    "INSERT OR REPLACE INTO knowledge_document_hashes "
                    "(content_hash, document_id, created_at) "
                    "VALUES (?, ?, datetime('now'))",
                    (content_hash, document_id),
                )
                self._write_chunks_locked(prefix, payload)
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
            removed = self._swap_memory_locked(prefix, payload)
            return {
                "deduplicated": False,
                "matched_document_id": None,
                "removed": removed,
            }

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

    # -- index metadata ------------------------------------------------------
    def get_index_meta(self) -> Dict[str, str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT key, value FROM knowledge_index_meta"
            ).fetchall()
        return {str(row["key"]): str(row["value"]) for row in rows}

    def set_index_meta(self, **values: Any) -> None:
        if not values:
            return
        with self._lock:
            for key, value in values.items():
                self._conn.execute(
                    "INSERT INTO knowledge_index_meta (key, value, updated_at) "
                    "VALUES (?, ?, datetime('now')) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                    "updated_at = datetime('now')",
                    (str(key), str(value)),
                )
            self._conn.commit()

    def rename_document(self, old_id: str, new_id: str) -> Dict[str, Any]:
        """单文档改名：chunks / FTS / hash 行同一事务，失败整体回滚。

        迁移必须可重入：改名成功后旧 id 不再存在，重复执行是 no-op；若目标
        id 已存在则拒绝（不覆盖另一个文档），由调用方记入 conflict 日志。
        """
        if not old_id or not new_id or old_id == new_id:
            return {"renamed": False, "reason": "same_id", "chunks": 0}
        prefix_old = f"{old_id}#"
        prefix_new = f"{new_id}#"
        with self._lock:
            rows = self._conn.execute(
                "SELECT doc_id, embedding, metadata FROM knowledge_vectors "
                "WHERE substr(doc_id, 1, ?) = ?",
                (len(prefix_old), prefix_old),
            ).fetchall()
            if not rows:
                return {"renamed": False, "reason": "not_found", "chunks": 0}
            conflict = self._conn.execute(
                "SELECT 1 FROM knowledge_vectors "
                "WHERE substr(doc_id, 1, ?) = ? LIMIT 1",
                (len(prefix_new), prefix_new),
            ).fetchone()
            if conflict is not None:
                return {"renamed": False, "reason": "target_exists", "chunks": 0}
            payload: List[Tuple[str, List[float], Dict[str, Any]]] = []
            for row in rows:
                metadata = json.loads(row["metadata"] or "{}")
                if not isinstance(metadata, dict):
                    metadata = {}
                index = _chunk_index_of(row["doc_id"], metadata)
                new_meta = dict(metadata)
                new_meta["document_id"] = new_id
                new_meta["chunk_index"] = index
                payload.append(
                    (
                        f"{prefix_new}{index}",
                        json.loads(row["embedding"]),
                        new_meta,
                    )
                )
            try:
                self._conn.execute(
                    "DELETE FROM knowledge_vectors WHERE substr(doc_id, 1, ?) = ?",
                    (len(prefix_old), prefix_old),
                )
                self._fts_delete_prefix(prefix_old)
                self._conn.execute(
                    "UPDATE knowledge_document_hashes SET document_id = ? "
                    "WHERE document_id = ?",
                    (new_id, old_id),
                )
                for chunk_id, embedding, metadata in payload:
                    self._conn.execute(
                        "INSERT INTO knowledge_vectors "
                        "(doc_id, embedding, metadata, updated_at) "
                        "VALUES (?, ?, ?, datetime('now')) "
                        "ON CONFLICT(doc_id) DO UPDATE SET "
                        "embedding = excluded.embedding, "
                        "metadata = excluded.metadata, "
                        "updated_at = datetime('now')",
                        (
                            chunk_id,
                            json.dumps(list(embedding)),
                            json.dumps(metadata, ensure_ascii=False, default=str),
                        ),
                    )
                    self._fts_upsert(chunk_id, metadata)
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
            self._swap_memory_locked(prefix_old, [])
            for chunk_id, embedding, metadata in payload:
                self._store[chunk_id] = (embedding, metadata)
            return {"renamed": True, "reason": None, "chunks": len(payload)}


    def list_documents(
        self,
        limit: int = 50,
        offset: int = 0,
        *,
        owners: Optional[Iterable[str]] = None,
        include_unowned: bool = True,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """Document summaries with ingest time and content hash from SQLite."""
        limit = max(1, int(limit))
        offset = max(0, int(offset))
        with self._lock:
            rows = self._conn.execute(
                "SELECT doc_id, metadata, updated_at FROM knowledge_vectors"
            ).fetchall()
            hashes = {
                row["document_id"]: row["content_hash"]
                for row in self._conn.execute(
                    "SELECT document_id, content_hash FROM knowledge_document_hashes"
                ).fetchall()
            }
        documents: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            try:
                meta = json.loads(row["metadata"] or "{}")
            except (TypeError, ValueError):
                meta = {}
            if not isinstance(meta, dict):
                meta = {}
            if not _owner_visible(meta, owners, include_unowned):
                continue
            document_id = _document_id_of(row["doc_id"], meta)
            entry = documents.get(document_id)
            if entry is None:
                entry = {
                    "document_id": document_id,
                    "chunks": 0,
                    "source": meta.get("source") or meta.get("filename"),
                    "title": meta.get("title"),
                    "file_type": meta.get("file_type"),
                    "page_count": meta.get("page_count"),
                    "content_hash": hashes.get(document_id),
                    "ingested_at": row["updated_at"],
                }
                documents[document_id] = entry
            entry["chunks"] += 1
            previous = entry["ingested_at"]
            if row["updated_at"] and (previous is None or row["updated_at"] < previous):
                entry["ingested_at"] = row["updated_at"]
        ordered = sorted(
            documents.values(),
            key=lambda item: (item["ingested_at"] or "", item["document_id"]),
            reverse=True,
        )
        return ordered[offset:offset + limit], len(ordered)

    def delete_document(self, document_id: str) -> Dict[str, Any]:
        """Delete chunks, FTS rows and the content hash in one transaction.

        The in-memory dict is only mutated after the commit succeeds, so a
        failure midway leaves both the database and the read path unchanged.
        """
        prefix = f"{document_id}#"
        with self._lock:
            existed = bool(
                self._conn.execute(
                    "SELECT 1 FROM knowledge_vectors "
                    "WHERE substr(doc_id, 1, ?) = ? LIMIT 1",
                    (len(prefix), prefix),
                ).fetchone()
            ) or bool(
                self._conn.execute(
                    "SELECT 1 FROM knowledge_document_hashes "
                    "WHERE document_id = ? LIMIT 1",
                    (document_id,),
                ).fetchone()
            )
            if not existed:
                return {"deleted": False, "chunks_removed": 0}
            try:
                cursor = self._conn.execute(
                    "DELETE FROM knowledge_vectors "
                    "WHERE substr(doc_id, 1, ?) = ?",
                    (len(prefix), prefix),
                )
                removed = int(cursor.rowcount or 0)
                self._fts_delete_prefix(prefix)
                self._conn.execute(
                    "DELETE FROM knowledge_document_hashes WHERE document_id = ?",
                    (document_id,),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
            for key in [key for key in self._store if key.startswith(prefix)]:
                del self._store[key]
            for value, owner in list(self._content_hashes.items()):
                if owner == document_id:
                    del self._content_hashes[value]
            return {"deleted": True, "chunks_removed": removed}

    def _lexical_rare_token_match(self, chunk_id: str, query_text: str) -> bool:
        """Whether the best lexical chunk contains a *rare* query token.

        "Rare" means the token exists in at most two indexed chunks. This is the
        signal a common-word match cannot fake: ``知识库`` appears everywhere,
        while ``申根``/``签证`` pin exactly one document. Used only to rescue the
        relevance gate, never to re-rank.
        """
        if not self._fts_available or not chunk_id:
            return False
        stored = self._store.get(chunk_id)
        if stored is None:
            return False
        query = set(query_tokens(query_text))
        if not query:
            return False
        chunk_tokens = set(_tokens_for_metadata(stored[1]).split())
        for token in [item for item in query if item in chunk_tokens][:12]:
            expression = f'"{token.replace(chr(34), chr(34) * 2)}"'
            try:
                rows = self._conn.execute(
                    "SELECT 1 FROM knowledge_fts WHERE tokens MATCH ? LIMIT 3",
                    (expression,),
                ).fetchall()
            except sqlite3.OperationalError:
                continue
            if len(rows) < 3:
                return True
        return False

    def _fts_search_scored(
        self,
        query_text: str,
        limit: int,
        visible: Optional[Callable[[str], bool]] = None,
    ) -> List[Tuple[str, float]]:
        """BM25-ordered ``(chunk_id, weight)`` pairs (empty when no FTS).

        ``visible`` filters candidates **before** normalization and truncation.
        Scoped readers must not be truncated by other accounts' documents: with
        the SQL ``LIMIT`` applied to the global index, 40 better-matching chunks
        from another account could hide the caller's own exact hit entirely.
        When a filter is present the scan window therefore covers the whole
        corpus (BM25 rows are unique chunk ids, so the corpus size is a hard
        upper bound), then the visible list is ranked and cut to ``limit``.

        SQLite's ``bm25()`` is negative (more negative = better), so weights are
        normalized by the best visible score: 1.0 for the top hit, a fraction
        for weaker matches. If ``bm25()`` ever returns non-negative values, fall
        back to flat 1.0 weights (plain RRF behaviour) instead of dividing by ~0.
        """
        if not self._fts_available:
            return []
        tokens = query_tokens(query_text)
        if not tokens:
            return []
        expression = " OR ".join(f'"{token}"' for token in tokens)
        scan = max(limit, len(self._store)) if visible is not None else limit
        try:
            rows = self._conn.execute(
                """
                SELECT chunk_id, bm25(knowledge_fts) AS score FROM knowledge_fts
                WHERE tokens MATCH ?
                ORDER BY bm25(knowledge_fts)
                LIMIT ?
                """,
                (expression, scan),
            ).fetchall()
        except sqlite3.OperationalError as exc:
            logger.warning("FTS query failed, falling back to vector-only: %s", exc)
            return []
        if visible is not None:
            rows = [row for row in rows if visible(row["chunk_id"])]
        rows = rows[:limit]
        if not rows:
            return []
        best = min(float(row["score"]) for row in rows)
        if best >= 0.0:
            return [(row["chunk_id"], 1.0) for row in rows]
        return [(row["chunk_id"], float(row["score"]) / best) for row in rows]

    def hybrid_search(
        self,
        query_text: str,
        query_embedding: List[float],
        top_k: int = 4,
        threshold: Optional[float] = None,
        rrf_k: int = 60,
        abstain: bool = True,
        config: Optional[AbstentionConfig] = None,
        *,
        owners: Optional[Iterable[str]] = None,
        include_unowned: bool = True,
    ) -> Tuple[List[Tuple[str, float, Dict[str, Any]]], Dict[str, Any]]:
        """Vector + BM25(FTS5) candidates fused with RRF, then gated.

        The lexical list is fused with its normalized BM25 strength (see
        ``weighted_reciprocal_rank_fusion``); this keeps the exact keyword hit
        from being outvoted by several medium sibling hits. Both stages are
        separate knobs: ``candidate_k`` decides what enters the fusion, the
        weighted RRF decides the final top-``top_k``.

        Returns ``(hits, retrieval)``. ``retrieval`` exposes the finalized
        contract fields ``status`` (``"ok"`` / ``"no_relevant"``), ``reason``
        (``"below_threshold"`` / ``"ambiguous"`` / ``"empty_corpus"``, or
        ``None``), ``top_score``, ``runner_up_score`` and ``thresholds``.
        ``hits`` is empty whenever the status is ``no_relevant``; the gate runs
        before truncation so an irrelevant list is never padded out to
        ``top_k``.
        """
        # Both stages need a *fair* rank: with a 20-candidate window and
        # ``top_k=4`` (the gateway's default) the correct-but-semantically-
        # misranked chunk (vector rank 30) was absent from the vector list, so
        # its fused score was lexical-only and a distractor document took the
        # first citation slot. 40 keeps such a lexical winner in both lists.
        candidate_k = max(40, top_k * 4)
        with self._lock:
            empty_corpus = not any(
                _owner_visible(meta, owners, include_unowned)
                for _, (_, meta) in self._store.items()
            )
            vector_results = self.search(
                query_embedding,
                top_k=candidate_k,
                threshold=threshold,
                owners=owners,
                include_unowned=include_unowned,
            )
            vector_ids = [doc_id for doc_id, _, _ in vector_results]

            def _visible(chunk_id: str) -> bool:
                stored = self._store.get(chunk_id)
                return stored is not None and _owner_visible(
                    stored[1], owners, include_unowned
                )

            lexical = self._fts_search_scored(
                query_text, candidate_k, visible=_visible
            )
            lexical_ids = [chunk_id for chunk_id, _ in lexical]
            lexical_rare = bool(lexical) and self._lexical_rare_token_match(
                lexical[0][0], query_text
            )

            similarities: Dict[str, float] = {}
            for chunk_id in set(vector_ids) | set(lexical_ids):
                stored = self._store.get(chunk_id)
                if stored is not None:
                    similarities[chunk_id] = cosine_similarity(
                        query_embedding, stored[0]
                    )
            assessment = assess_confidence(
                similarities,
                len(lexical_ids),
                config=config,
                empty_corpus=empty_corpus,
                zero_score_allowed=threshold is not None and threshold <= 0.0,
                lexical_rare_match=lexical_rare,
            )

            if lexical_ids:
                fused = weighted_reciprocal_rank_fusion(
                    vector_ids, lexical, rrf_k=rrf_k
                )
                # Equal fused scores are decided inside the fusion by chunk-id
                # string order. On a measured tie (both 1/61 ≈ 0.016393) the
                # BM25 winner -- the chunk that actually carried the answer --
                # lost to an unrelated same-domain document that merely held
                # the cosine top (0.7015 vs 0.6234). Exact query-term evidence
                # is the scarcer signal, so ties prefer the better lexical
                # rank, then cosine, then the chunk id for determinism.
                lexical_rank = {
                    chunk_id: rank for rank, (chunk_id, _) in enumerate(lexical)
                }
                fused = sorted(
                    fused,
                    key=lambda item: (
                        -item[1],
                        lexical_rank.get(item[0], len(lexical_ids)),
                        -similarities.get(item[0], 0.0),
                        item[0],
                    ),
                )
                ordered_ids = [doc_id for doc_id, _ in fused]
                fused_scores = dict(fused)
                mode = "hybrid"
            else:
                ordered_ids = list(vector_ids)
                fused_scores = {
                    doc_id: score for doc_id, score, _ in vector_results
                }
                mode = "vector"

            ranked: List[Tuple[str, float, Dict[str, Any]]] = []
            for chunk_id in ordered_ids:
                stored = self._store.get(chunk_id)
                if stored is None:
                    continue
                ranked.append(
                    (chunk_id, fused_scores.get(chunk_id, 0.0), stored[1])
                )
                if len(ranked) >= top_k:
                    break

            gated = abstain and not assessment["confident"]
            results = [] if gated else ranked
            no_relevant = gated or not results
            info = {
                "mode": mode,
                "fts_available": self._fts_available,
                "status": NO_RELEVANT if no_relevant else STATUS_OK,
                "reason": assessment["reason"] if no_relevant else None,
                "top_score": assessment["top_score"],
                "runner_up_score": assessment["runner_up_score"],
                "thresholds": assessment["thresholds"],
                "abstained": gated,
                "confidence": assessment,
                "candidate_k": candidate_k,
                "vector_candidates": len(vector_ids),
                "lexical_candidates": len(lexical_ids),
                "lexical_weighting": "bm25" if lexical_ids else None,
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
