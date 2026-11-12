"""Qdrant-backed vector store for the knowledge base.

Used only when a Qdrant server is actually reachable (see
``vector_store.select_vector_backend``). Everything here speaks the Qdrant
REST API with the standard library only -- no ``qdrant-client`` dependency,
so the adapter can be tested against a fake HTTP server and the behaviour
does not depend on a client library's version.

What is stored where
--------------------
* one collection (default ``jarvis_knowledge``) holds one point per chunk:
  the vector plus the full chunk metadata as payload, plus three internal
  payload fields: ``_chunk_id``, ``_document_id`` and ``_tokens`` (the
  tokenized chunk text, used for the lexical side of hybrid search);
* a second collection (``<collection>__meta``) holds a single point whose
  payload is the index metadata (embedding provider / model / dimension).

Point ids are deterministic UUIDv5 values derived from the chunk id, so a
re-ingest of the same chunk overwrites the same point and a restart sees the
same corpus.

Honesty notes
-------------
* Vector scores come from Qdrant itself (cosine distance over the stored
  vectors) -- they are real similarity scores, not computed locally and not
  placeholders.
* The lexical side has no FTS5 here: it scrolls the corpus and scores
  token overlap with IDF weights computed from the corpus itself. That is a
  real computation over real data, and it is reported as ``mode="hybrid"``
  with ``fts_available=False`` -- never as BM25.
* The corpus is desktop-scale (tens of thousands of chunks at most), so
  scroll-based admin operations (list / rename / delete-by-document) are
  acceptable. If the corpus ever grows past that, these need server-side
  filters instead of client-side scans.

Not verified against a real Qdrant server in this environment (none is
reachable here and there is no Docker): the adapter is covered by tests
against a fake Qdrant REST server (``tests/knowledge/test_qdrant_store.py``).
Before pointing a production install at a real Qdrant, run that suite
against the real server once.
"""

from __future__ import annotations

import json
import logging
import math
import os
import threading
import urllib.error
import urllib.request
import uuid
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .abstention import (
    NO_RELEVANT,
    STATUS_OK,
    AbstentionConfig,
    assess_confidence,
)
from .vector_store import (
    VectorStore,
    _chunk_index_of,
    _document_id_of,
    _metadata_owner,
    _owner_visible,
    cosine_similarity,
    query_tokens,
    tokenize_for_index,
    weighted_reciprocal_rank_fusion,
)

logger = logging.getLogger("liuhao.knowledge.qdrant_store")

QDRANT_HOST_ENV = "QDRANT_HOST"
QDRANT_PORT_ENV = "QDRANT_PORT"
QDRANT_API_KEY_ENV = "QDRANT_API_KEY"
QDRANT_COLLECTION_ENV = "LIUHAO_QDRANT_COLLECTION"
QDRANT_TIMEOUT_ENV = "LIUHAO_QDRANT_TIMEOUT"
DEFAULT_COLLECTION = "jarvis_knowledge"
DEFAULT_HOST = "localhost"
DEFAULT_PORT = 6333

#: Fixed point id for the single index-metadata point in the meta collection.
_META_POINT_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "jarvis-kb:index-meta"))
#: Namespace for deterministic chunk point ids.
_CHUNK_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "jarvis-kb:chunk")


class QdrantError(RuntimeError):
    """Qdrant is configured/selected but the server cannot serve the request."""


class QdrantUnavailableError(QdrantError):
    """The Qdrant server is not reachable at all."""


def qdrant_address() -> Tuple[str, int, Optional[str]]:
    """``(host, port, api_key)`` from the environment (``.env.example``)."""
    host = (os.environ.get(QDRANT_HOST_ENV) or DEFAULT_HOST).strip() or DEFAULT_HOST
    try:
        port = int((os.environ.get(QDRANT_PORT_ENV) or "").strip() or DEFAULT_PORT)
    except ValueError:
        port = DEFAULT_PORT
    api_key = (os.environ.get(QDRANT_API_KEY_ENV) or "").strip() or None
    return host, port, api_key


def qdrant_base_url(host: Optional[str] = None, port: Optional[int] = None) -> str:
    """Base URL of the configured Qdrant server (no trailing slash)."""
    env_host, env_port, _ = qdrant_address()
    return f"http://{host or env_host}:{port or env_port}"


def _probe_timeout() -> float:
    try:
        return max(0.2, min(float(os.environ.get("LIUHAO_QDRANT_PROBE_TIMEOUT", "1") or 1), 5.0))
    except ValueError:
        return 1.0


def probe_qdrant(
    host: Optional[str] = None,
    port: Optional[int] = None,
    timeout: Optional[float] = None,
) -> bool:
    """True when a Qdrant server answers ``GET /`` (never raises)."""
    url = f"{qdrant_base_url(host, port)}/"
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout or _probe_timeout()) as response:
            return 200 <= response.status < 300
    except Exception:  # noqa: BLE001 - unreachable is a normal "no" answer
        return False


def _chunk_point_id(chunk_id: str) -> str:
    return str(uuid.uuid5(_CHUNK_NAMESPACE, chunk_id))


def _payload_for(chunk_id: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(metadata or {})
    payload["_chunk_id"] = chunk_id
    payload["_document_id"] = _document_id_of(chunk_id, metadata)
    payload["_tokens"] = tokenize_for_index(
        f"{str(metadata.get('context_header') or '').strip()}\n{str(metadata.get('text', ''))}"
        if str(metadata.get("context_header") or "").strip()
        else str(metadata.get("text", ""))
    )
    return payload


def _metadata_from_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: value for key, value in (payload or {}).items() if not key.startswith("_")
    }


class QdrantVectorStore(VectorStore):
    """Vector store whose source of truth is a Qdrant collection.

    The in-memory dict inherited from :class:`VectorStore` is **not** used as
    a cache: every read goes to Qdrant, so two processes (or a restart) see
    the same corpus. The base class only supplies the interface and the
    ``threading.RLock``.
    """

    backend = "qdrant"

    def __init__(
        self,
        collection: Optional[str] = None,
        host: Optional[str] = None,
        port: Optional[int] = None,
        api_key: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> None:
        super().__init__()
        env_host, env_port, env_key = qdrant_address()
        self.collection = (
            (collection or os.environ.get(QDRANT_COLLECTION_ENV) or DEFAULT_COLLECTION)
            .strip()
            or DEFAULT_COLLECTION
        )
        self.meta_collection = f"{self.collection}__meta"
        self.base_url = f"http://{host or env_host}:{port or env_port}"
        self.api_key = api_key if api_key is not None else env_key
        try:
            self.timeout = float(timeout if timeout is not None else os.environ.get(QDRANT_TIMEOUT_ENV, "") or 10)
        except ValueError:
            self.timeout = 10.0
        self._known_dimensions: Dict[str, int] = {}
        self._lock = threading.RLock()

    # -- low-level HTTP ------------------------------------------------------
    def _request(
        self, method: str, path: str, payload: Optional[Dict[str, Any]] = None
    ) -> Any:
        url = f"{self.base_url}{path}"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["api-key"] = self.api_key
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8")
                return json.loads(body) if body.strip() else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500] if exc.fp else ""
            if exc.code == 404:
                raise QdrantUnavailableError(
                    f"Qdrant {method} {path} returned 404: {detail or 'not found'}"
                ) from exc
            raise QdrantError(
                f"Qdrant {method} {path} returned HTTP {exc.code}: {detail}"
            ) from exc
        except (urllib.error.URLError, OSError) as exc:
            raise QdrantUnavailableError(
                f"Qdrant at {self.base_url} is unreachable ({exc})"
            ) from exc

    def _collection_exists(self, name: str) -> bool:
        try:
            self._request("GET", f"/collections/{name}")
            return True
        except QdrantUnavailableError:
            return False

    def _ensure_collection(self, name: str, dimension: int) -> None:
        known = self._known_dimensions.get(name)
        if known == dimension:
            return
        if known is not None and known != dimension:
            raise QdrantError(
                f"Qdrant 集合 {name} 已按 {known} 维创建，当前向量为 {dimension} 维；"
                "请先删除集合或重建索引"
            )
        if not self._collection_exists(name):
            try:
                self._request(
                    "PUT",
                    f"/collections/{name}",
                    {"vectors": {"size": dimension, "distance": "Cosine"}},
                )
            except QdrantUnavailableError:
                # 并发创建时另一个进程可能刚建好：再查一次，存在即算成功。
                if not self._collection_exists(name):
                    raise
        self._known_dimensions[name] = dimension

    def _ensure_meta_collection(self) -> None:
        # 元数据点本身不带向量：用 1 维占位向量建集合（Qdrant 集合必须声明向量配置）。
        self._ensure_collection(self.meta_collection, 1)

    # -- point helpers ---------------------------------------------------------
    def _upsert_points(
        self, collection: str, points: List[Dict[str, Any]], dimension: int
    ) -> None:
        if not points:
            return
        self._ensure_collection(collection, dimension)
        for start in range(0, len(points), 100):
            self._request(
                "PUT",
                f"/collections/{collection}/points?wait=true",
                {"points": points[start:start + 100]},
            )

    def _scroll_all(
        self,
        collection: str,
        *,
        with_vector: bool = False,
        limit: int = 250,
    ) -> List[Dict[str, Any]]:
        """Every point in the collection (paginated; desktop-scale corpora)."""
        if not self._collection_exists(collection):
            return []
        points: List[Dict[str, Any]] = []
        offset: Any = None
        while True:
            body: Dict[str, Any] = {
                "limit": limit,
                "with_payload": True,
                "with_vector": with_vector,
            }
            if offset is not None:
                body["offset"] = offset
            result = self._request("POST", f"/collections/{collection}/points/scroll", body)
            page = result.get("result", {}) if isinstance(result, dict) else {}
            batch = page.get("points", []) if isinstance(page, dict) else []
            points.extend(batch)
            offset = page.get("next_page_offset") if isinstance(page, dict) else None
            if not offset or not batch:
                break
        return points

    def _query_points(
        self,
        query_vector: List[float],
        limit: int,
        score_threshold: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        if not self._collection_exists(self.collection):
            return []
        body: Dict[str, Any] = {
            "query": list(query_vector),
            "limit": limit,
            "with_payload": True,
        }
        if score_threshold is not None:
            body["score_threshold"] = score_threshold
        result = self._request("POST", f"/collections/{self.collection}/points/query", body)
        payload = result.get("result", {}) if isinstance(result, dict) else {}
        if isinstance(payload, list):
            return payload
        return payload.get("points", []) if isinstance(payload, dict) else []

    def _delete_points(self, ids: List[str]) -> None:
        if not ids:
            return
        for start in range(0, len(ids), 100):
            self._request(
                "POST",
                f"/collections/{self.collection}/points/delete?wait=true",
                {"points": ids[start:start + 100]},
            )

    def _chunks_of(self, document_id: str) -> List[Dict[str, Any]]:
        """All points of one document, as ``(chunk_id, vector, metadata)`` dicts."""
        prefix = f"{document_id}#"
        records = []
        for point in self._scroll_all(self.collection, with_vector=True):
            payload = point.get("payload") or {}
            chunk_id = str(payload.get("_chunk_id") or "")
            if not chunk_id.startswith(prefix):
                continue
            records.append(
                {
                    "chunk_id": chunk_id,
                    "vector": list(point.get("vector") or []),
                    "metadata": _metadata_from_payload(payload),
                }
            )
        records.sort(key=lambda item: _chunk_index_of(item["chunk_id"], item["metadata"]))
        return records

    # -- VectorStore interface -------------------------------------------------
    def insert(
        self, doc_id: str, embedding: List[float], metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        record_metadata = metadata if metadata is not None else {}
        with self._lock:
            self._upsert_points(
                self.collection,
                [
                    {
                        "id": _chunk_point_id(doc_id),
                        "vector": list(embedding),
                        "payload": _payload_for(doc_id, record_metadata),
                    }
                ],
                len(embedding),
            )

    def search(
        self,
        query_embedding: List[float],
        top_k: int = 4,
        threshold: Optional[float] = None,
        *,
        owners: Optional[Iterable[str]] = None,
        include_unowned: bool = True,
    ) -> List[Tuple[str, float, Dict[str, Any]]]:
        with self._lock:
            points = self._query_points(query_embedding, max(top_k, 1), threshold)
        results = []
        for point in points:
            payload = point.get("payload") or {}
            metadata = _metadata_from_payload(payload)
            if not _owner_visible(metadata, owners, include_unowned):
                continue
            chunk_id = str(payload.get("_chunk_id") or "")
            if not chunk_id:
                continue
            results.append((chunk_id, float(point.get("score") or 0.0), metadata))
        results.sort(key=lambda item: item[1], reverse=True)
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
        """Vector (Qdrant cosine) + lexical (IDF token overlap) fused with RRF.

        Scores in the returned hits are the **fused RRF scores** (same as the
        SQLite store); the gate's ``top_score`` / ``runner_up_score`` are real
        Qdrant cosine similarities. ``fts_available`` is False: there is no
        FTS5/BM25 here, the lexical side is IDF-weighted token overlap over
        the scrolled corpus.
        """
        candidate_k = max(40, top_k * 4)
        with self._lock:
            vector_results = self.search(
                query_embedding,
                top_k=candidate_k,
                threshold=threshold,
                owners=owners,
                include_unowned=include_unowned,
            )
            vector_ids = [doc_id for doc_id, _, _ in vector_results]
            all_points = self._scroll_all(self.collection, with_vector=False)
            visible: Dict[str, Dict[str, Any]] = {}
            for point in all_points:
                payload = point.get("payload") or {}
                chunk_id = str(payload.get("_chunk_id") or "")
                metadata = _metadata_from_payload(payload)
                if chunk_id and _owner_visible(metadata, owners, include_unowned):
                    visible[chunk_id] = metadata
            empty_corpus = not visible
            lexical = self._lexical_ranked(query_text, visible)
            lexical_ids = [chunk_id for chunk_id, _ in lexical]
            similarities: Dict[str, float] = {
                doc_id: score for doc_id, score, _ in vector_results
            }
            # Chunks the vector query missed still need a real cosine for the
            # gate: Qdrant already scored them out of the top-k, so fetch their
            # vectors and compute locally (same cosine as the SQLite store).
            missing = [cid for cid in lexical_ids if cid not in similarities]
            if missing:
                for point in self._scroll_all(self.collection, with_vector=True):
                    payload = point.get("payload") or {}
                    chunk_id = str(payload.get("_chunk_id") or "")
                    if chunk_id in missing and chunk_id in visible:
                        similarities[chunk_id] = cosine_similarity(
                            query_embedding, list(point.get("vector") or [])
                        )
            assessment = assess_confidence(
                similarities,
                len(lexical_ids),
                config=config,
                empty_corpus=empty_corpus,
                zero_score_allowed=threshold is not None and threshold <= 0.0,
            )
            if lexical_ids:
                fused = weighted_reciprocal_rank_fusion(vector_ids, lexical, rrf_k=rrf_k)
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
                metadata = visible.get(chunk_id)
                if metadata is None:
                    continue
                ranked.append((chunk_id, fused_scores.get(chunk_id, 0.0), metadata))
                if len(ranked) >= top_k:
                    break
            gated = abstain and not assessment["confident"]
            results = [] if gated else ranked
            no_relevant = gated or not results
            info = {
                "mode": mode,
                "fts_available": False,
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
                "lexical_weighting": "idf_token_overlap" if lexical_ids else None,
                "rrf_k": rrf_k,
            }
        return results, info

    @staticmethod
    def _lexical_ranked(
        query_text: str,
        visible: Dict[str, Dict[str, Any]],
        limit: int = 40,
    ) -> List[Tuple[str, float]]:
        """IDF-weighted token-overlap ranking over the visible corpus.

        ``weight`` is normalized so the best hit is 1.0 (same contract as the
        SQLite store's BM25 weights). Tokens that appear in almost every chunk
        contribute ~0; a token unique to one chunk contributes the most.
        """
        tokens = query_tokens(query_text)
        if not tokens or not visible:
            return []
        doc_tokens: Dict[str, set] = {}
        doc_freq: Dict[str, int] = {}
        for chunk_id, metadata in visible.items():
            chunk_tokens = set(_tokens_for_metadata(metadata).split())
            doc_tokens[chunk_id] = chunk_tokens
            for token in chunk_tokens:
                doc_freq[token] = doc_freq.get(token, 0) + 1
        total = len(visible)
        scored: List[Tuple[str, float]] = []
        for chunk_id, chunk_tokens in doc_tokens.items():
            score = 0.0
            for token in tokens:
                if token in chunk_tokens:
                    score += math.log((total + 1) / (doc_freq.get(token, 0) + 1)) + 1.0
            if score > 0:
                scored.append((chunk_id, score))
        scored.sort(key=lambda item: (-item[1], item[0]))
        scored = scored[:limit]
        if not scored:
            return []
        best = scored[0][1]
        return [(chunk_id, score / best) for chunk_id, score in scored]

    def delete(self, doc_id: str) -> bool:
        with self._lock:
            points = self._scroll_all(self.collection, with_vector=False)
            found = any(
                str((point.get("payload") or {}).get("_chunk_id") or "") == doc_id
                for point in points
            )
            if not found:
                return False
            self._delete_points([_chunk_point_id(doc_id)])
            return True

    def count(self) -> int:
        with self._lock:
            if not self._collection_exists(self.collection):
                return 0
            result = self._request(
                "POST",
                f"/collections/{self.collection}/points/count",
                {"exact": True},
            )
            payload = result.get("result", {}) if isinstance(result, dict) else {}
            return int(payload.get("count", 0)) if isinstance(payload, dict) else 0

    def get(self, doc_id: str) -> Optional[Tuple[List[float], Dict[str, Any]]]:
        with self._lock:
            try:
                result = self._request(
                    "GET",
                    f"/collections/{self.collection}/points/{_chunk_point_id(doc_id)}",
                )
            except QdrantUnavailableError:
                return None
            payload = result.get("result", {}) if isinstance(result, dict) else {}
            if not payload:
                return None
            metadata = _metadata_from_payload(payload.get("payload") or {})
            return list(payload.get("vector") or []), metadata

    # -- index metadata ------------------------------------------------------
    def get_index_meta(self) -> Dict[str, str]:
        with self._lock:
            self._ensure_meta_collection()
            try:
                result = self._request(
                    "GET", f"/collections/{self.meta_collection}/points/{_META_POINT_ID}"
                )
            except QdrantUnavailableError:
                return {}
            payload = result.get("result", {}) if isinstance(result, dict) else {}
            stored = (payload.get("payload") or {}).get("meta") if payload else None
            return dict(stored) if isinstance(stored, dict) else {}

    def set_index_meta(self, **values: Any) -> None:
        if not values:
            return
        with self._lock:
            meta = self.get_index_meta()
            meta.update({str(key): str(value) for key, value in values.items()})
            self._upsert_points(
                self.meta_collection,
                [
                    {
                        "id": _META_POINT_ID,
                        "vector": [0.0],
                        "payload": {"meta": meta},
                    }
                ],
                1,
            )

    def dimension_distribution(self) -> Dict[int, int]:
        with self._lock:
            meta = self.get_index_meta()
            recorded = str(meta.get("embedding_dim") or "").strip()
            if recorded.isdigit():
                return {int(recorded): self.count()}
            counts: Dict[int, int] = {}
            for point in self._scroll_all(self.collection, with_vector=True):
                dimension = len(point.get("vector") or [])
                counts[dimension] = counts.get(dimension, 0) + 1
            return counts

    # -- document administration ---------------------------------------------
    def raw_documents(
        self,
        *,
        owners: Optional[Iterable[str]] = None,
        include_unowned: bool = True,
    ) -> List[Dict[str, Any]]:
        with self._lock:
            documents: Dict[str, Dict[str, Any]] = {}
            for point in self._scroll_all(self.collection, with_vector=False):
                payload = point.get("payload") or {}
                metadata = _metadata_from_payload(payload)
                if not _owner_visible(metadata, owners, include_unowned):
                    continue
                chunk_id = str(payload.get("_chunk_id") or "")
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
        with self._lock:
            return [
                (item["chunk_id"], list(item["vector"]), dict(item["metadata"]))
                for item in self._chunks_of(document_id)
            ]

    def list_documents(
        self,
        limit: int = 50,
        offset: int = 0,
        *,
        owners: Optional[Iterable[str]] = None,
        include_unowned: bool = True,
    ) -> Tuple[List[Dict[str, Any]], int]:
        limit = max(1, int(limit))
        offset = max(0, int(offset))
        with self._lock:
            documents: Dict[str, Dict[str, Any]] = {}
            for point in self._scroll_all(self.collection, with_vector=False):
                payload = point.get("payload") or {}
                metadata = _metadata_from_payload(payload)
                if not _owner_visible(metadata, owners, include_unowned):
                    continue
                chunk_id = str(payload.get("_chunk_id") or "")
                document_id = _document_id_of(chunk_id, metadata)
                entry = documents.get(document_id)
                if entry is None:
                    entry = {
                        "document_id": document_id,
                        "chunks": 0,
                        "source": metadata.get("source") or metadata.get("filename"),
                        "title": metadata.get("title"),
                        "file_type": metadata.get("file_type"),
                        "page_count": metadata.get("page_count"),
                        # ``_content_hash`` is stripped from chunk metadata by
                        # ``_metadata_from_payload``; read it off the payload.
                        "content_hash": payload.get("_content_hash"),
                        "ingested_at": None,
                    }
                    documents[document_id] = entry
                entry["chunks"] += 1
            ordered = sorted(documents.values(), key=lambda item: item["document_id"])
            return ordered[offset:offset + limit], len(ordered)

    def get_document_chunks(self, document_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            chunks = []
            for item in self._chunks_of(document_id):
                metadata = item["metadata"]
                pages = metadata.get("pages") or (
                    [metadata["page"]]
                    if isinstance(metadata.get("page"), int)
                    else []
                )
                if len(pages) > 1:
                    page_label = f"第 {pages[0]}-{pages[-1]} 页"
                elif isinstance(metadata.get("page"), int):
                    page_label = f"第 {metadata['page']} 页"
                else:
                    page_label = None
                chunks.append(
                    {
                        "chunk_id": item["chunk_id"],
                        "chunk_index": _chunk_index_of(item["chunk_id"], metadata),
                        "text": str(metadata.get("text", "")),
                        "heading_path": list(metadata.get("heading_path") or []),
                        "context_header": str(metadata.get("context_header") or ""),
                        "owner_account_id": _metadata_owner(metadata),
                        "page": metadata.get("page"),
                        "pages": pages,
                        "page_label": page_label,
                    }
                )
            return chunks

    def delete_document(self, document_id: str) -> Dict[str, Any]:
        prefix = f"{document_id}#"
        with self._lock:
            chunks = self._chunks_of(document_id)
            if not chunks:
                return {"deleted": False, "chunks_removed": 0}
            self._delete_points([_chunk_point_id(item["chunk_id"]) for item in chunks])
            return {"deleted": True, "chunks_removed": len(chunks)}

    def rename_document(self, old_id: str, new_id: str) -> Dict[str, Any]:
        if not old_id or not new_id or old_id == new_id:
            return {"renamed": False, "reason": "same_id", "chunks": 0}
        prefix_old = f"{old_id}#"
        prefix_new = f"{new_id}#"
        with self._lock:
            old_chunks = self._chunks_of(old_id)
            if not old_chunks:
                return {"renamed": False, "reason": "not_found", "chunks": 0}
            if self._chunks_of(new_id):
                return {"renamed": False, "reason": "target_exists", "chunks": 0}
            payload: List[Tuple[str, List[float], Dict[str, Any]]] = []
            for item in old_chunks:
                metadata = dict(item["metadata"])
                index = _chunk_index_of(item["chunk_id"], metadata)
                new_meta = dict(metadata)
                new_meta["document_id"] = new_id
                new_meta["chunk_index"] = index
                payload.append((f"{prefix_new}{index}", item["vector"], new_meta))
            removed = self.replace_prefix(prefix_old, payload)
            return {"renamed": True, "reason": None, "chunks": len(payload)}

    def replace_prefix(
        self,
        prefix: str,
        items: List[Tuple[str, List[float], Dict[str, Any]]],
    ) -> int:
        """Replace all chunks under ``prefix`` (upsert-then-prune).

        New points are written **before** stale ones are deleted, and only
        ids absent from the new set are deleted: a re-ingest reuses the same
        chunk ids, so deleting first (or deleting the whole old set after the
        upsert) would remove the just-written revision.
        """
        if not prefix:
            raise ValueError("replace_prefix requires a non-empty prefix")
        with self._lock:
            points = self._scroll_all(self.collection, with_vector=False)
            old_ids = [
                _chunk_point_id(str((point.get("payload") or {}).get("_chunk_id") or ""))
                for point in points
                if str((point.get("payload") or {}).get("_chunk_id") or "").startswith(prefix)
            ]
            new_ids = set()
            if items:
                dimension = len(items[0][1])
                new_points = []
                for doc_id, embedding, metadata in items:
                    new_ids.add(_chunk_point_id(doc_id))
                    new_points.append(
                        {
                            "id": _chunk_point_id(doc_id),
                            "vector": list(embedding),
                            "payload": _payload_for(doc_id, metadata),
                        }
                    )
                self._upsert_points(self.collection, new_points, dimension)
            self._delete_points([pid for pid in old_ids if pid not in new_ids])
            return len(old_ids)

    def upsert_document(
        self,
        prefix: str,
        items: List[Tuple[str, List[float], Dict[str, Any]]],
        content_hash: str,
        document_id: str,
    ) -> Dict[str, Any]:
        if not prefix:
            raise ValueError("upsert_document requires a non-empty prefix")
        with self._lock:
            for point in self._scroll_all(self.collection, with_vector=False):
                payload = point.get("payload") or {}
                if str(payload.get("_content_hash") or "") != content_hash:
                    continue
                owner_doc = str(payload.get("_document_id") or "")
                if owner_doc and owner_doc != document_id:
                    return {
                        "deduplicated": True,
                        "matched_document_id": owner_doc,
                        "removed": 0,
                    }
            # The hash travels in the payload so a later import of the same
            # text can find it (and so ``list_documents`` can report it).
            hashed_items = [
                (doc_id, embedding, {**metadata, "_content_hash": content_hash})
                for doc_id, embedding, metadata in items
            ]
            removed = self.replace_prefix(prefix, hashed_items)
            return {
                "deduplicated": False,
                "matched_document_id": None,
                "removed": removed,
            }

    def close(self) -> None:
        return None


def _tokens_for_metadata(metadata: Dict[str, Any]) -> str:
    body = str(metadata.get("text", ""))
    header = str(metadata.get("context_header") or "").strip()
    return tokenize_for_index(f"{header}\n{body}" if header else body)
