"""Live knowledge base (ingest + retrieval over the persisted vector store).

The service never fabricates an answer: if no embedding provider is configured,
``build_embedding_provider`` raises and every caller reports "未启用". The vector
store is the process-level SQLite singleton, so a restart keeps the corpus.
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from .abstention import AbstentionConfig, resolve_abstention_config
from .chunker import chunk_document_structured, chunk_paged_document
from .embedding import EmbeddingUnavailableError, build_embedding_provider
from .vector_store import get_vector_store

logger = logging.getLogger("liuhao.knowledge.documents")

_ID_SAFE = re.compile(r"[^A-Za-z0-9_.:-]+")


class IngestCancelled(Exception):
    """Raised at a chunk boundary when a cancellable ingest is aborted.

    Nothing has been written to the store at that point: vectors are computed
    before the swap, so cancellation is **all-or-nothing** (全回滚).
    """

    def __init__(self, processed: int, total: int) -> None:
        super().__init__(
            f"入库已取消（已处理 {processed}/{total} 个分块，未写入任何分块）"
        )
        self.processed = processed
        self.total = total


def _safe_document_id(raw: Optional[str]) -> str:
    if not raw or not raw.strip():
        return uuid.uuid4().hex
    return _ID_SAFE.sub("-", raw.strip())[:128]


def content_hash(text: str) -> str:
    """Whitespace-insensitive content hash (same text, different line endings)."""
    normalized = re.sub(r"\s+", " ", text or "").strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _owner_token(owner: str) -> str:
    """账号名 -> 可安全进入 chunk/doc id 的短 token（稳定、无路径分隔符）。"""
    token = _ID_SAFE.sub("-", str(owner or "").strip()).strip("-")[:64]
    return token or "account"


def scoped_document_id(owner: Optional[str], raw: Optional[str]) -> str:
    """把文档 id 收进账号命名空间，避免两个账号同名文件互相覆盖。

    幂等：已带自己前缀的 id 原样返回，重复调用不会层层叠加前缀。
    """
    base = _safe_document_id(raw)
    owner = str(owner or "").strip()
    if not owner:
        return base
    prefix = f"{_owner_token(owner)}--"
    if base.startswith(prefix):
        return base
    return _safe_document_id(prefix + base)


def scoped_content_hash(text: str, owner: Optional[str]) -> str:
    """账号内去重哈希：不同账号的同文不会互相命中（也不泄露对方的 doc id）。"""
    owner = str(owner or "").strip()
    if not owner:
        return content_hash(text)
    normalized = re.sub(r"\s+", " ", text or "").strip()
    return hashlib.sha256(f"{owner}\x00{normalized}".encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class KnowledgeScope:
    """一次知识库读写的账号范围。

    - ``owners=None``：不限制（内部/评测用；HTTP 面不会这样传）；
    - ``owners=(principal,)``：只读自己（主账号与成员一致）；
    - ``include_unowned``：是否包含无归属的历史数据（单机老部署）；
    - ``owns_legacy``：能否管理无归属历史数据（仅主账号/老部署）；
    - ``unowned_writes``：新写入是否沿用无归属 id（仅单机老部署；
      账号树里的主账号仍写自己的前缀）。
    """

    principal: str
    owners: Optional[Tuple[str, ...]] = None
    include_unowned: bool = True
    owns_legacy: bool = True
    unowned_writes: bool = False

    @classmethod
    def all(cls) -> "KnowledgeScope":
        return cls(
            principal="", owners=None, include_unowned=True, unowned_writes=True
        )

    def covers(self, owner: Optional[str]) -> bool:
        owner = str(owner or "").strip()
        if not owner:
            return self.include_unowned
        return self.owners is None or owner in self.owners

    def may_write(self, owner: Optional[str]) -> bool:
        """写操作只作用于自己的文档；无归属老数据只允许主账号/老部署管理。"""
        owner = str(owner or "").strip()
        if not owner:
            return self.owns_legacy
        return owner == self.principal

    def may_delete(self, owner: Optional[str]) -> bool:
        """删除归属：只删自己的；无归属历史只允许主账号/老部署。

        知识库是**账号自有资产**（``docs/account-hierarchy.md`` 的日常能力表），
        主账号也没有聚合读/删成员知识库的权限；删除与 ``may_write`` 同口径，
        这样"删除只影响自己的"对主账号与成员一致成立。
        """
        owner = str(owner or "").strip()
        if not owner:
            return self.owns_legacy
        return owner == self.principal

    def store_kwargs(self) -> Dict[str, Any]:
        return {"owners": self.owners, "include_unowned": self.include_unowned}

    @property
    def write_owner(self) -> str:
        """写入归属：单机老部署沿用无归属历史（id 不变），账号树写自己。"""
        if self.unowned_writes:
            return ""
        return self.principal


def _page_label(metadata: Dict[str, Any]) -> Optional[str]:
    """Human-readable page label ("第 3 页" / "第 2-3 页"), if the chunk has one."""
    pages = metadata.get("pages")
    if isinstance(pages, (list, tuple)) and pages:
        first, last = pages[0], pages[-1]
        if isinstance(first, int) and isinstance(last, int) and first > 0:
            return f"第 {first} 页" if first == last else f"第 {first}-{last} 页"
    page = metadata.get("page")
    if isinstance(page, int) and page > 0:
        return f"第 {page} 页"
    return None


def _hit_text(metadata: Dict[str, Any]) -> str:
    """Body plus provenance breadcrumb; legacy rows (no header/page) still work."""
    body = str(metadata.get("text", ""))
    header = str(metadata.get("context_header") or "").strip()
    page = _page_label(metadata)
    prefix = " / ".join(part for part in (page, header) if part)
    if not prefix:
        return body
    return f"[{prefix}]\n{body}" if body else f"[{prefix}]"


#: 脱敏本身失败时对外返回的固定屏蔽文案：宁可看不到内容，也不漏原文。
REDACTED_CONTENT = "[内容已屏蔽：脱敏失败，未输出原文]"

#: 这些元数据字段会原样出现在检索结果 / 分块 / 文档列表里，必须脱敏；
#: ``document_id`` / ``chunk_index`` / ``score`` 等标识字段保留，否则引用会错位。
_REDACT_METADATA_KEYS = ("text", "source", "filename", "title", "context_header")


def redact_for_output(text: Any) -> str:
    """知识库对外展示的最后一层脱敏（密钥 / 路径 / 邮箱 / PII）。

    写入与检索仍用原文（否则关键词与语义都会失真），只在返回给客户端 /
    提示词 / 界面之前脱敏。任何异常都 fail-closed：不返回原文。
    """
    if not text:
        return ""
    try:
        from src.common.redaction import redact_log_text
        from .pii import redact_text as redact_pii

        return redact_log_text(redact_pii(str(text)))
    except Exception as exc:  # noqa: BLE001 - 脱敏失败必须屏蔽，不能退回原文
        logger.error("knowledge redaction failed; refusing raw text: %s", exc)
        return REDACTED_CONTENT


def _redact_metadata(metadata: Dict[str, Any]) -> Dict[str, Any]:
    """Copy metadata with the human-visible string fields redacted."""
    safe = dict(metadata or {})
    for key in _REDACT_METADATA_KEYS:
        value = safe.get(key)
        if isinstance(value, str) and value:
            safe[key] = redact_for_output(value)
    heading_path = safe.get("heading_path")
    if isinstance(heading_path, (list, tuple)):
        safe["heading_path"] = [
            redact_for_output(item) for item in heading_path if isinstance(item, str)
        ]
    return safe


def _embed_pages(
    embedder,
    chunks,
    pages: List[Tuple[int, str]],
    on_progress: Optional[Callable[[int, int], None]],
    on_page_progress: Callable[[int, int], None],
    cancel_event: Optional[threading.Event],
) -> List[List[float]]:
    """Embed paged chunks one source page at a time.

    Progress is reported in **pages**, not a fabricated percentage: blank pages
    still advance the counter because they were really processed. Cancellation
    is checked at each page boundary, and since no vector has been written to
    the store yet, cancelling here is an all-or-nothing rollback.
    """
    vectors: List[List[float]] = []
    offset = 0
    processed_chunks = 0
    total_pages = len(pages)
    for ordinal, (page_number, _page_text) in enumerate(pages, start=1):
        if cancel_event is not None and cancel_event.is_set():
            raise IngestCancelled(processed_chunks, len(chunks))
        group = []
        while offset < len(chunks) and chunks[offset].page == page_number:
            group.append(chunks[offset])
            offset += 1
        if group:
            vectors.extend(embedder.embed([chunk.embed_text for chunk in group]))
            processed_chunks += len(group)
            if on_progress is not None:
                on_progress(processed_chunks, len(chunks))
        on_page_progress(ordinal, total_pages)
    # Defensive: a paged chunk with no page tag would otherwise be dropped.
    while offset < len(chunks):
        if cancel_event is not None and cancel_event.is_set():
            raise IngestCancelled(processed_chunks, len(chunks))
        vectors.append(embedder.embed([chunks[offset].embed_text])[0])
        processed_chunks += 1
        offset += 1
        if on_progress is not None:
            on_progress(processed_chunks, len(chunks))
    return vectors


class KnowledgeDocumentService:
    """Ingest text into and search over the persisted knowledge index."""

    def __init__(self, vector_store=None, embedder=None) -> None:
        self._vector_store = vector_store
        self._embedder = embedder
        self._unavailable_reason: Optional[str] = None

    # -- component wiring -------------------------------------------------
    def _embedding(self):
        if self._embedder is not None:
            return self._embedder
        try:
            self._embedder = build_embedding_provider()
        except EmbeddingUnavailableError as exc:
            self._unavailable_reason = str(exc)
            raise
        self._unavailable_reason = None
        return self._embedder

    def _store(self):
        if self._vector_store is not None:
            return self._vector_store
        self._vector_store = get_vector_store()
        return self._vector_store

    # -- read-only status --------------------------------------------------
    def status(self) -> Dict[str, Any]:
        """Human-readable availability; never raises."""
        provider = None
        model = None
        try:
            embedder = self._embedding()
            provider = getattr(embedder, "name", type(embedder).__name__)
            model = getattr(embedder, "model", None)
        except EmbeddingUnavailableError as exc:
            reason = str(exc)
        else:
            reason = None

        vectors = 0
        try:
            vectors = self._store().count()
        except Exception as exc:  # pragma: no cover - 存储首次打开失败
            logger.error("knowledge status: vector store unavailable (%s)", exc)
            reason = reason or f"向量库不可用：{type(exc).__name__}"

        if reason is None:
            message = "知识库已启用"
        elif reason.startswith("知识库未启用"):
            message = reason
        else:
            message = f"知识库未启用：{reason}"

        return {
            "enabled": reason is None,
            "provider": provider,
            "model": model,
            "vectors": vectors,
            "reason": reason,
            "message": message,
        }

    # -- write -------------------------------------------------------------
    def ingest(
        self,
        text: str,
        doc_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        *,
        pages: Optional[List[Tuple[int, str]]] = None,
        on_progress: Optional[Callable[[int, int], None]] = None,
        on_page_progress: Optional[Callable[[int, int], None]] = None,
        cancel_event: Optional[threading.Event] = None,
        owner_account_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Ingest already-extracted text (and, for PDFs, per-page text).

        ``text`` is the **extracted** body for every format -- the content hash
        used for dedup is computed on it, so renaming ``manual.pdf`` to
        ``manual-copy.pdf`` still hits the duplicate path. ``pages`` is the
        paged form ``[(1, page_text), ...]``: when present each chunk is tagged
        with its 1-based page number in metadata.

        ``owner_account_id`` 是调用者账号：文档 id / 去重哈希 / chunk 元数据
        全部收进该账号的命名空间，写入只落在自己范围。
        """
        embedder = self._embedding()
        store = self._store()
        owner = str(owner_account_id or "").strip()
        document_id = scoped_document_id(owner, doc_id)
        if pages:
            chunks = chunk_paged_document(list(pages))
        else:
            chunks = chunk_document_structured(text)

        static_metadata = dict(metadata or {})
        # The heading breadcrumb is embedded together with the body so a chunk
        # that starts with "如下所示" still carries its section topic.
        if pages and on_page_progress is not None:
            vectors = _embed_pages(
                embedder,
                chunks,
                list(pages),
                on_progress,
                on_page_progress,
                cancel_event,
            )
        elif on_progress is None and cancel_event is None:
            vectors = embedder.embed([chunk.embed_text for chunk in chunks])
        else:
            # Cancellable path: one embedding per chunk so a cancel request is
            # honoured at the next chunk boundary, and progress is observable.
            vectors = []
            for index, chunk in enumerate(chunks):
                if cancel_event is not None and cancel_event.is_set():
                    raise IngestCancelled(index, len(chunks))
                vectors.append(embedder.embed([chunk.embed_text])[0])
                if on_progress is not None:
                    on_progress(index + 1, len(chunks))

        # Replace the *whole* document revision atomically. Embeddings are
        # computed before touching the store, so an embedding failure leaves the
        # previous revision intact. Swapping (delete old ids + insert new ones)
        # in one call also drops tail chunks from a longer prior version -- the
        # old implementation only overwrote ``doc#0..n`` and stale tail chunks
        # kept leaking into retrieval.
        items = [
            (
                f"{document_id}#{index}",
                vector,
                {
                    **static_metadata,
                    "owner_account_id": owner,
                    "document_id": document_id,
                    "chunk_index": index,
                    "text": chunk.text,
                    "heading_path": list(chunk.heading_path),
                    "context_header": chunk.context_header,
                    **(
                        {
                            "page": chunk.page,
                            "page_label": (
                                f"第 {chunk.pages[0]}-{chunk.pages[-1]} 页"
                                if len(chunk.pages) > 1
                                else f"第 {chunk.page} 页"
                            ),
                            "pages": list(chunk.pages),
                        }
                        if chunk.page is not None
                        else {}
                    ),
                },
            )
            for index, (chunk, vector) in enumerate(zip(chunks, vectors))
        ]
        outcome = store.upsert_document(
            f"{document_id}#",
            items,
            scoped_content_hash(text, owner),
            document_id,
        )
        if outcome.get("deduplicated"):
            matched = outcome.get("matched_document_id")
            logger.info(
                "knowledge ingest: document=%s skipped (duplicate of %s)",
                document_id,
                matched,
            )
            return {
                "document_id": document_id,
                "chunks": 0,
                "vectors": store.count(),
                "deduplicated": True,
                "matched_document_id": matched,
            }

        logger.info(
            "knowledge ingest: document=%s chunks=%d provider=%s",
            document_id,
            len(chunks),
            getattr(embedder, "name", "unknown"),
        )
        return {
            "document_id": document_id,
            "chunks": len(chunks),
            "vectors": store.count(),
            "deduplicated": False,
            "matched_document_id": None,
        }

    # -- read ---------------------------------------------------------------
    def search(
        self,
        query: str,
        top_k: int = 4,
        threshold: Optional[float] = None,
        abstain: bool = True,
        abstention_config: Optional[AbstentionConfig] = None,
        scope: Optional[KnowledgeScope] = None,
    ) -> Dict[str, Any]:
        """Retrieve chunks, with the relevance gate on by default.

        ``retrieval`` always carries the finalized contract fields
        (``status`` / ``reason`` / ``top_score`` / ``runner_up_score`` /
        ``thresholds``) and mirrors the returned ``hits`` so callers that read
        ``retrieval.hits`` see exactly the same list. ``abstain=False`` is an
        escape hatch for diagnostics; it can return low-score chunks, so
        production callers must leave the default.

        ``abstention_config`` pins the gate thresholds for this call only
        (evaluation/system checks use it). Production leaves it ``None``: the
        gate is then chosen by the **actual** embedder identity, never by
        another model's numbers. An embedder with no evaluation evidence gets
        the lenient gate (never abstain on a non-empty corpus) and
        ``retrieval.abstention_calibration`` says ``thresholds_from=
        "uncalibrated"`` so the caller can surface that honestly.
        """
        embedder = self._embedding()
        store = self._store()
        pinned = abstention_config
        gate, calibration = resolve_abstention_config(
            getattr(embedder, "name", None),
            getattr(embedder, "model", None),
            pinned=pinned,
        )
        query_vector = embedder.embed([query])[0]
        store_filter = scope.store_kwargs() if scope is not None else {}
        if hasattr(store, "hybrid_search"):
            results, retrieval = store.hybrid_search(
                query, query_vector, top_k=top_k, threshold=threshold,
                abstain=abstain, config=gate, **store_filter,
            )
        else:  # pragma: no cover - third-party stores without the hybrid API
            results = store.search(
                query_vector, top_k=top_k, threshold=threshold, **store_filter
            )
            retrieval = {
                "mode": "vector",
                "fts_available": False,
                "status": "ok" if results else "no_relevant",
                "reason": None if results else "below_threshold",
                "top_score": None,
                "runner_up_score": None,
                "thresholds": gate.as_dict(),
                "abstained": False,
            }
        retrieval["abstention_calibration"] = calibration
        hits: List[Dict[str, Any]] = [
            {
                "doc_id": doc_id,
                "score": round(score, 6),
                "text": redact_for_output(_hit_text(meta or {})),
                # 展示/引用用的元数据同样脱敏；document_id / chunk_index 保留，
                # 否则前端与引用编号会指向错误片段。
                "metadata": _redact_metadata(meta or {}),
                # Explicit provenance fields so a client never has to dig into
                # metadata to show "来自第 N 页" / "第 2-3 页".
                "page": (meta or {}).get("page"),
                "pages": (meta or {}).get("pages"),
                "location": _page_label(meta or {}),
            }
            for doc_id, score, meta in results
        ]
        retrieval["hits"] = hits
        logger.info(
            "knowledge search: top_k=%d hits=%d provider=%s status=%s reason=%s",
            top_k,
            len(hits),
            getattr(embedder, "name", "unknown"),
            retrieval.get("status"),
            retrieval.get("reason"),
        )
        return {
            "query": query,
            "top_k": top_k,
            "hits": hits,
            "count": len(hits),
            "retrieval": retrieval,
        }

    # -- document administration (no embedder needed) -----------------------
    def list_documents(
        self,
        limit: int = 50,
        offset: int = 0,
        scope: Optional[KnowledgeScope] = None,
    ) -> Dict[str, Any]:
        store = self._store()
        store_filter = scope.store_kwargs() if scope is not None else {}
        documents, total = store.list_documents(
            limit=limit, offset=offset, **store_filter
        )
        for document in documents:
            for key in ("source", "title"):
                value = document.get(key)
                if isinstance(value, str) and value:
                    document[key] = redact_for_output(value)
        return {
            "documents": documents,
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    def document_owner(self, document_id: str) -> Optional[str]:
        """文档归属账号；``None`` 表示文档不存在，``""`` 表示无归属历史数据。"""
        chunks = self._store().get_document_chunks(document_id)
        if not chunks:
            return None
        return str(chunks[0].get("owner_account_id") or "").strip()

    def document_chunks(
        self, document_id: str, scope: Optional[KnowledgeScope] = None
    ) -> Dict[str, Any]:
        chunks = self._store().get_document_chunks(document_id)
        if scope is not None:
            chunks = [
                chunk
                for chunk in chunks
                if scope.covers(chunk.get("owner_account_id"))
            ]
        chunks = [
            {
                **chunk,
                "text": redact_for_output(chunk.get("text")),
                "context_header": redact_for_output(chunk.get("context_header")),
                "heading_path": [
                    redact_for_output(item)
                    for item in (chunk.get("heading_path") or [])
                    if isinstance(item, str)
                ],
            }
            for chunk in chunks
        ]
        return {
            "document_id": document_id,
            "chunks": chunks,
            "count": len(chunks),
        }

    def delete_document(
        self, document_id: str, scope: Optional[KnowledgeScope] = None
    ) -> Dict[str, Any]:
        owner = self.document_owner(document_id)
        if scope is not None and (
            owner is None or not scope.covers(owner) or not scope.may_delete(owner)
        ):
            # 不存在 / 不可见 / 无权删，对外一律同一种"没删到"（路由映射为中性 404）。
            return {
                "document_id": document_id,
                "deleted": False,
                "chunks_removed": 0,
                "not_found": True,
            }
        outcome = self._store().delete_document(document_id)
        logger.info(
            "knowledge delete: document=%s deleted=%s chunks_removed=%s",
            document_id,
            outcome.get("deleted"),
            outcome.get("chunks_removed"),
        )
        return {
            "document_id": document_id,
            "deleted": bool(outcome.get("deleted")),
            "chunks_removed": int(outcome.get("chunks_removed") or 0),
        }


_SERVICE: Optional[KnowledgeDocumentService] = None


def get_knowledge_service() -> KnowledgeDocumentService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = KnowledgeDocumentService()
    return _SERVICE


def reset_knowledge_service() -> None:
    """Test helper: forget the cached embedder/store wiring."""
    global _SERVICE
    _SERVICE = None
