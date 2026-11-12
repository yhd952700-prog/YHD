"""Live knowledge base (ingest + retrieval over the persisted vector store).

The vector store is the process-level singleton, so a restart keeps the
corpus: SQLite locally by default (``PersistentVectorStore``), Qdrant when
one is reachable and selected (see ``vector_store.select_vector_backend``).
The embedding provider is the configured one when available; when no external
embedding service is reachable the service falls back to deterministic local
hash vectors (``embedding.build_embedding_provider_with_local_fallback``) and
says so in :meth:`KnowledgeDocumentService.status` (``embedding_mode`` /
``semantic`` / ``vector_backend``) -- the fallback is lexical, not semantic,
and must never be presented as a semantic model. Set
``LIUHAO_KNOWLEDGE_LOCAL_FALLBACK=0`` to restore the old fail-closed
behaviour (no provider -> "未启用").
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
import unicodedata
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from .abstention import AbstentionConfig, resolve_abstention_config
from .chunker import chunk_document_structured, chunk_paged_document
from .embedding import (
    EmbeddingUnavailableError,
    build_embedding_provider_with_local_fallback,
)
from .vector_store import get_vector_store
from . import vector_store as vector_store_module

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


_HASH_LEN = 16
_MAX_ID_LEN = 128
#: 旧版命名空间前缀（CJK 被正则剥空后留下的 ``account---`` / ``auditroot---``）。
#: 迁移只认这两个可证实的旧 token，绝不猜别的账号前缀。
_LEGACY_OWNER_TOKENS = ("account", "auditroot")


def _normalize_identifier(raw: Any) -> str:
    """NFKC + 去首尾空白：全角/半角、组合字符与不同编码先归一，再进哈希。"""
    return unicodedata.normalize("NFKC", str(raw or "")).strip()


def _readable_token(raw: str, limit: int = 24) -> str:
    """保留可读 ASCII 前缀（便于排查），CJK 不再被当作边界字符吞掉。"""
    token = _ID_SAFE.sub("-", raw).strip("-._:")
    return token[:limit].strip("-._:")


def _stable_digest(raw: str) -> str:
    """稳定摘要：同一账号/文件名在任何进程、任何重启后都得到同一 token。"""
    return hashlib.sha256(_normalize_identifier(raw).encode("utf-8")).hexdigest()[
        :_HASH_LEN
    ]


def _fit_id(candidate: str, limit: int = _MAX_ID_LEN) -> str:
    """超长 id 用摘要收尾，绝不截掉末尾的 hash（截断会让不同文件重新撞车）。"""
    if len(candidate) <= limit:
        return candidate
    return f"{candidate[: limit - _HASH_LEN - 1]}-{_stable_digest(candidate)}"


def _safe_document_id(raw: Optional[str]) -> str:
    """文件名 -> 文档 id。

    纯 ASCII 安全名保持可读原样；含中文等字符时追加稳定 hash，保证
    ``文档.txt`` 与 ``报告.txt`` 不会再坍缩成同一个 ``---.txt``。
    """
    if not raw or not str(raw).strip():
        return uuid.uuid4().hex
    normalized = _normalize_identifier(raw)
    if _ID_SAFE.search(normalized) is None:
        return _fit_id(normalized)
    readable = _readable_token(normalized)
    digest = _stable_digest(normalized)
    base = f"{readable}-{digest}" if readable else f"doc-{digest}"
    return _fit_id(base)


def content_hash(text: str) -> str:
    """Unicode + whitespace-insensitive content hash.

    先做 NFKC 再折叠空白：全角 ``ＡＢＣ`` 与半角 ``ABC``、带/不带 BOM 的同文
    都会得到同一摘要，避免同一份材料从不同来源导入时被当成两篇文档（MEM-018）。
    """
    normalized = unicodedata.normalize("NFKC", text or "")
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _owner_token(owner: str) -> str:
    """账号名 -> 稳定、无路径分隔符的命名空间 token。

    纯 ASCII 安全名保持原样（与既有部署/测试兼容）；中文账号（``测试甲`` /
    ``测试乙``）不再共用 ``account``：token 由「可读 ASCII 前缀 + 归一化账号名
    的 sha256 前 16 位」组成，跨进程稳定。
    """
    normalized = _normalize_identifier(owner)
    if not normalized:
        return "account"
    if _ID_SAFE.search(normalized) is None:
        return _fit_id(normalized)
    readable = _readable_token(normalized, limit=20)
    digest = _stable_digest(normalized)
    return f"{readable}-{digest}" if readable else f"acct-{digest}"


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
    return _fit_id(prefix + base)


def scoped_content_hash(text: str, owner: Optional[str]) -> str:
    """账号内去重哈希：不同账号的同文不会互相命中（也不泄露对方的 doc id）。"""
    owner = str(owner or "").strip()
    if not owner:
        return content_hash(text)
    normalized = unicodedata.normalize("NFKC", text or "")
    normalized = re.sub(r"\s+", " ", normalized).strip()
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
            self._embedder = build_embedding_provider_with_local_fallback()
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

    def _ensure_embedding_compatible(self, store, embedder, dimension: int) -> None:
        """写入前校验库级模型/维度；不一致直接拒绝并给中文原因（不混写）。

        老库可能没有台账（此前没有记录模型），此时只要求"库内维度唯一且与
        本次向量一致"；一旦有台账，模型或维度任一不符都必须先重建索引。
        """
        meta = store.get_index_meta() or {}
        recorded_model = str(meta.get("embedding_model") or "").strip()
        recorded_dim = str(meta.get("embedding_dim") or "").strip()
        model = str(getattr(embedder, "model", "") or "")
        dims = store.dimension_distribution() or {}
        if len(dims) > 1:
            raise EmbeddingUnavailableError(
                "向量库维度不一致（"
                + "、".join(f"{dim} 维 {count} 条" for dim, count in sorted(dims.items()))
                + "），请先执行重建索引"
            )
        if recorded_model and model and recorded_model != model:
            raise EmbeddingUnavailableError(
                f"索引使用嵌入模型 {recorded_model}，当前配置为 {model}；请先执行重建索引"
            )
        if recorded_dim and recorded_dim.isdigit() and dimension and int(recorded_dim) != dimension:
            raise EmbeddingUnavailableError(
                f"索引记录维度 {recorded_dim}，当前模型输出 {dimension} 维；请先执行重建索引"
            )
        if dims and dimension:
            actual = next(iter(dims))
            if actual != dimension:
                raise EmbeddingUnavailableError(
                    f"库内向量为 {actual} 维，当前模型输出 {dimension} 维；请先执行重建索引"
                )

    def _record_index_meta(self, store, embedder, dimension: int) -> None:
        if not dimension:
            return
        try:
            store.set_index_meta(
                embedding_provider=str(
                    getattr(embedder, "name", type(embedder).__name__)
                ),
                embedding_model=str(getattr(embedder, "model", "") or ""),
                embedding_dim=int(dimension),
            )
        except Exception as exc:  # pragma: no cover - 台账失败不否决已成功写入
            logger.warning("knowledge: cannot record index metadata (%s)", exc)

    # -- read-only status --------------------------------------------------
    def status(self) -> Dict[str, Any]:
        """Human-readable availability; never raises.

        Honest-backend fields (the console's knowledge page reads this):

        * ``vector_backend`` -- ``"local_sqlite"`` or ``"qdrant"``: where the
          vectors actually live;
        * ``qdrant_reachable`` -- whether a Qdrant server answers at the
          configured address right now (cached 60 s);
        * ``embedding_mode`` -- ``"configured"`` for a real embedding
          provider, ``"local_hashing_fallback"`` when no external service is
          reachable and the deterministic local hash embedder is in use;
        * ``semantic`` -- False for the hashing fallback (lexical token
          overlap, not meaning) and for the dev-only mock; True for a real
          embedding model.
        """
        provider = None
        model = None
        local_fallback = False
        try:
            embedder = self._embedding()
            provider = getattr(embedder, "name", type(embedder).__name__)
            model = getattr(embedder, "model", None)
            local_fallback = bool(getattr(embedder, "local_fallback", False))
        except EmbeddingUnavailableError as exc:
            reason = str(exc)
        else:
            reason = None

        vectors = 0
        dims: Dict[int, int] = {}
        index_meta: Dict[str, str] = {}
        vector_backend = "local_sqlite"
        try:
            store = self._store()
            vectors = store.count()
            index_meta = store.get_index_meta() or {}
            dims = store.dimension_distribution() or {}
            vector_backend = str(getattr(store, "backend", "local_sqlite"))
        except Exception as exc:  # pragma: no cover - 存储首次打开失败
            logger.error("knowledge status: vector store unavailable (%s)", exc)
            reason = reason or f"向量库不可用：{type(exc).__name__}"

        qdrant_reachable = vector_store_module.probe_qdrant_cached()
        embedding_mode = (
            "local_hashing_fallback" if local_fallback else "configured"
        )
        semantic = (not local_fallback) and provider != "mock"

        # MEM-008：库级记录模型与维度；不一致时直接禁用并给中文原因，不让
        # 错配向量进入检索（历史台账在 knowledge_index_meta 表）。
        if reason is None and dims:
            if len(dims) > 1:
                detail = "、".join(f"{dim} 维 {count} 条" for dim, count in sorted(dims.items()))
                reason = f"向量库维度不一致（{detail}），请执行重建索引"
            else:
                actual_dim = next(iter(dims))
                recorded_dim = str(index_meta.get("embedding_dim") or "").strip()
                if recorded_dim and recorded_dim.isdigit() and int(recorded_dim) != actual_dim:
                    reason = (
                        f"索引记录维度 {recorded_dim}，库内实际 {actual_dim} 维；"
                        "请执行重建索引"
                    )
                elif not recorded_dim:
                    # 只记录一次“已观测维度”，不冒充当时的模型（老库可能换过模型）。
                    try:
                        store.set_index_meta(embedding_dim=actual_dim)
                        index_meta["embedding_dim"] = str(actual_dim)
                    except Exception as exc:  # pragma: no cover - 台账写失败不拖垮状态
                        logger.warning("knowledge status: cannot record dimension (%s)", exc)
        recorded_model = str(index_meta.get("embedding_model") or "").strip()
        if reason is None and recorded_model and model and recorded_model != str(model):
            reason = (
                f"索引使用嵌入模型 {recorded_model}，当前配置为 {model}；"
                "请执行重建索引"
            )

        if reason is None:
            if local_fallback and vector_backend == "local_sqlite":
                message = (
                    "知识库已启用（本地向量：哈希向量，非语义模型；"
                    "向量库为本机 SQLite，未使用 Qdrant）"
                )
            elif local_fallback:
                message = "知识库已启用（本地向量：哈希向量，非语义模型）"
            elif vector_backend == "qdrant":
                message = "知识库已启用（向量库：Qdrant）"
            else:
                message = "知识库已启用"
        elif reason.startswith("知识库未启用") or reason.startswith("向量库不可用"):
            # 未配置与向量库故障是两种状态：后者不能折叠成「未启用」。
            message = reason
        else:
            message = f"知识库不可用：{reason}"

        return {
            "enabled": reason is None,
            "provider": provider,
            "model": model,
            "vectors": vectors,
            "index_model": recorded_model or None,
            "index_dimension": (
                int(index_meta["embedding_dim"])
                if str(index_meta.get("embedding_dim") or "").isdigit()
                else None
            ),
            "dimensions": {str(dim): count for dim, count in sorted(dims.items())},
            "reason": reason,
            "message": message,
            # -- honest-backend fields (see docstring) ---------------------
            "vector_backend": vector_backend,
            "qdrant_reachable": qdrant_reachable,
            "embedding_mode": embedding_mode,
            "semantic": semantic,
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
        dimension = len(vectors[0]) if vectors else 0
        self._ensure_embedding_compatible(store, embedder, dimension)
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
        self._record_index_meta(store, embedder, dimension)
        # ``removed`` 是本次替换掉的旧分块数：>0 说明同名文档的上一版本被
        # 本次上传整体替换。把它显式回给调用方，用户至少能知道"旧版本不在了"
        # （MEM-015：不再静默覆盖）。
        previous_chunks = int(outcome.get("removed") or 0)
        return {
            "document_id": document_id,
            "chunks": len(chunks),
            "vectors": store.count(),
            "deduplicated": False,
            "matched_document_id": None,
            "overwritten": previous_chunks > 0,
            "previous_chunks": previous_chunks,
        }

    # -- maintenance --------------------------------------------------------
    def reindex(
        self,
        scope: Optional["KnowledgeScope"] = None,
        *,
        on_progress: Optional[Callable[[int, int], None]] = None,
    ) -> Dict[str, Any]:
        """用当前 embedding 模型重建可见文档的向量（逐文档原子替换）。

        错配模型/维度时这是唯一恢复路径：先按当前模型重算全部 chunk，再更新
        库级台账。单个文档失败不会污染其他文档（每个文档一次事务）。
        """
        embedder = self._embedding()
        store = self._store()
        owners = scope.owners if scope is not None else None
        include_unowned = scope.include_unowned if scope is not None else True
        documents = store.raw_documents(
            owners=owners, include_unowned=include_unowned
        )
        total = len(documents)
        rebuilt_docs = 0
        rebuilt_chunks = 0
        skipped = 0
        failures: List[Dict[str, Any]] = []
        dimension = 0
        for ordinal, document in enumerate(documents, start=1):
            document_id = document["document_id"]
            records = store.get_chunk_records(document_id)
            if not records:
                skipped += 1
                if on_progress is not None:
                    on_progress(ordinal, total)
                continue
            texts = []
            for _chunk_id, _vector, metadata in records:
                body = str(metadata.get("text", ""))
                header = str(metadata.get("context_header") or "").strip()
                texts.append(f"{header}\n{body}" if header else body)
            try:
                vectors = embedder.embed(texts)
                if len(vectors) != len(records):
                    raise RuntimeError(
                        f"重建索引时向量数不匹配（{len(vectors)}/{len(records)}）"
                    )
                dimension = len(vectors[0]) if vectors else dimension
                payload = []
                for (chunk_id, _old_vector, metadata), vector in zip(records, vectors):
                    index = int(metadata.get("chunk_index") or 0)
                    payload.append(
                        (
                            f"{document_id}#{index}",
                            vector,
                            {**metadata, "document_id": document_id, "chunk_index": index},
                        )
                    )
                removed = store.replace_prefix(f"{document_id}#", payload)
                rebuilt_docs += 1
                rebuilt_chunks += len(payload)
                logger.info(
                    "knowledge reindex: document=%s chunks=%d replaced=%d",
                    document_id,
                    len(payload),
                    removed,
                )
            except Exception as exc:  # noqa: BLE001 - 单文档失败不影响其他文档
                logger.error("knowledge reindex failed for %s: %s", document_id, exc)
                failures.append({"document_id": document_id, "error": str(exc)})
            if on_progress is not None:
                on_progress(ordinal, total)
        if rebuilt_docs:
            self._record_index_meta(store, embedder, dimension)
        return {
            "documents": rebuilt_docs,
            "chunks": rebuilt_chunks,
            "skipped": skipped,
            "failed": len(failures),
            "failures": failures,
            "dimension": dimension,
            "provider": str(getattr(embedder, "name", type(embedder).__name__)),
            "model": str(getattr(embedder, "model", "") or ""),
            "vectors": store.count(),
        }

    def migrate_legacy_document_ids(
        self,
        scope: Optional["KnowledgeScope"] = None,
        *,
        dry_run: bool = True,
    ) -> Dict[str, Any]:
        """把旧版 CJK 坍缩 id（``account---*`` / ``auditroot---*``）迁到新命名空间。

        可重入：改名成功后旧 id 消失，第二次执行只剩 ``unchanged``。
        可回滚：每个文档一次独立事务（``rename_document``），失败只记录不删除；
        ``dry_run=True`` 时只产出计划，不写库。
        """
        store = self._store()
        owners = scope.owners if scope is not None else None
        include_unowned = scope.include_unowned if scope is not None else True
        documents = store.raw_documents(
            owners=owners, include_unowned=include_unowned
        )
        before = len(documents)
        # 无归属的历史文档（账号体系上线前的单机数据）：迁移不改它们的归属，
        # 但必须在报告里可查，避免"看起来迁完了、其实有一批没归属"。
        unassigned_documents = []
        for document in documents:
            if str(document.get("owner") or ""):
                continue
            document_id = str(document["document_id"])
            display = (
                str(document.get("filename") or "").strip() or document_id
            )
            unassigned_documents.append(
                {
                    "document_id": document_id,
                    "chunks": int(document.get("chunks") or 0),
                    "display_name": display,
                    "filename": str(document.get("filename") or ""),
                    "reason": "missing_owner_metadata",
                    "detail": (
                        "缺少 owner_account_id，旧 id 也无法反推原始账号；"
                        "不会自动归给任何账号。根账号可在 "
                        "GET /v1/knowledge/maintenance/unassigned 查看原文并删除。"
                    ),
                }
            )
        planned: List[Dict[str, Any]] = []
        expected_ids: Dict[str, int] = {}
        for document in documents:
            old_id = str(document["document_id"])
            owner = str(document.get("owner") or "")
            if not owner:
                continue
            legacy_token = _ID_SAFE.sub("-", owner.strip()).strip("-")[:64] or "account"
            new_token = _owner_token(owner)
            new_prefix = f"{new_token}--"
            if not old_id.startswith(f"{legacy_token}--"):
                continue
            base = old_id[len(legacy_token) + 2:]
            display = (
                str(document.get("filename") or "").strip()
                or str(document.get("source") or "").strip()
                or str(document.get("title") or "").strip()
                or base
            )
            if new_token == legacy_token:
                # 纯 ASCII 账号命名空间没有变化：只有「旧版把 CJK 剥成 '-'」
                # 的文档需要迁移，新命名空间的文档必须原样跳过，避免把
                # 可读 id 反复改名。
                if base != _ID_SAFE.sub("-", display):
                    continue
            if new_token != legacy_token and old_id.startswith(new_prefix):
                # 只对「命名空间已经变化」的账号做幂等跳过；纯 ASCII 账号的
                # 前后前缀相同，不能拿它当"已迁移"的证据。
                continue
            new_id = scoped_document_id(owner, display)
            if new_id == old_id:
                # 旧版已把文件名信息丢在 id 里（如 ``---.txt``），无法还原原名；
                # 用旧 id 的稳定摘要生成 ``legacy-<hash>``，既完成迁移又不删除、
                # 不与新上传的同名文件再相撞。
                new_id = scoped_document_id(owner, f"legacy-{_stable_digest(old_id)}")
            if not new_id or new_id == old_id:
                continue
            planned.append(
                {
                    "old_id": old_id,
                    "new_id": new_id,
                    "owner": owner,
                    "chunks": int(document.get("chunks") or 0),
                    "display_name": display,
                }
            )
            expected_ids[new_id] = expected_ids.get(new_id, 0) + 1

        migrated = 0
        conflicts = 0
        failures: List[Dict[str, Any]] = []
        details: List[Dict[str, Any]] = []
        for item in planned:
            if expected_ids.get(item["new_id"], 0) > 1:
                conflicts += 1
                logger.error(
                    "knowledge migrate: conflict %s -> %s (multiple legacy docs "
                    "collapse to the same new id; left untouched)",
                    item["old_id"],
                    item["new_id"],
                )
                details.append({**item, "status": "conflict"})
                continue
            if dry_run:
                details.append({**item, "status": "planned"})
                continue
            try:
                result = store.rename_document(item["old_id"], item["new_id"])
                if not result.get("renamed"):
                    conflicts += 1
                    logger.warning(
                        "knowledge migrate: skip %s -> %s (%s)",
                        item["old_id"],
                        item["new_id"],
                        result.get("reason"),
                    )
                    details.append(
                        {**item, "status": "conflict", "reason": result.get("reason")}
                    )
                    continue
                migrated += 1
                logger.info(
                    "knowledge migrate: %s -> %s owner=%s chunks=%s",
                    item["old_id"],
                    item["new_id"],
                    item["owner"],
                    result.get("chunks"),
                )
                details.append(
                    {**item, "status": "migrated", "renamed_chunks": result.get("chunks")}
                )
            except Exception as exc:  # noqa: BLE001 - 单文档失败保持原样
                failures.append({"old_id": item["old_id"], "error": str(exc)})
                logger.error(
                    "knowledge migrate failed for %s: %s", item["old_id"], exc
                )
                details.append({**item, "status": "failed", "error": str(exc)})
        after = before
        if not dry_run:
            after = len(
                store.raw_documents(owners=owners, include_unowned=include_unowned)
            )
        # 逐篇去向账：每篇源文档都必须落进且只落进一个桶。
        # 知识库迁移只改 id、不删内容；``lost`` 必须为 0，count 守恒只作为
        # 第二道校验，不能单独拿来说"没丢"。
        unchanged = before - len(unassigned_documents) - len(planned)
        pending = len(planned) if dry_run else 0
        accounted = (
            unchanged
            + len(unassigned_documents)
            + migrated
            + conflicts
            + len(failures)
            + pending
        )
        lost = before - accounted
        kept_unresolved = len(unassigned_documents) + conflicts + len(failures)
        report = {
            "dry_run": bool(dry_run),
            "documents_before": before,
            "documents_after": after,
            "count_conserved": after == before,
            "lost_documents": lost,
            "rows_accounted": lost == 0,
            # 与记忆迁移同名口径：imported = 已在新库可寻址；merged 知识库没有
            # 同 id 合并语义，恒为 0；quarantined = 仍在库里但没有自动解决；
            # pending 只在 dry-run 出现（计划中，尚未执行）。
            "destinations": {
                "imported": migrated + unchanged,
                "merged": 0,
                "quarantined": kept_unresolved,
                "pending": pending,
                "lost": lost,
            },
            "unchanged": unchanged,
            "candidates": len(planned),
            "migrated": migrated,
            "conflicts": conflicts,
            "failed": len(failures),
            "failures": failures,
            "skip_reasons": {
                "missing_owner_metadata": len(unassigned_documents),
                "legacy_id_target_conflict": conflicts,
                "rename_failed": len(failures),
            },
            "kept_unresolved": kept_unresolved,
            # 归属无法判定的历史文档原样保留（单机老数据由根账号继续可见）。
            "unassigned_documents": len(unassigned_documents),
            "unassigned": unassigned_documents,
            "details": details,
        }
        logger.info(
            "knowledge migrate summary: dry_run=%s before=%s after=%s "
            "candidates=%s migrated=%s conflicts=%s failed=%s unassigned=%s "
            "lost=%s",
            dry_run,
            before,
            after,
            len(planned),
            migrated,
            conflicts,
            len(failures),
            len(unassigned_documents),
            lost,
        )
        return report

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
        # MEM-020：向量模型在、但索引不可用（维度/模型错配、向量库故障）时，
        # 过去的实现照样去检索，余弦算不出分就静默返回 no_relevant，用户看到
        # 「全绿但不可用」。这里先看 status，把降级状态原样放进响应；模型本身
        # 没配置（provider 为空）仍交给 _embedding() 抛 503，保持既有契约。
        status = self.status()
        if not status.get("enabled") and status.get("provider") is not None:
            reason = str(status.get("reason") or "知识库不可用")
            message = str(status.get("message") or f"知识库不可用：{reason}")
            logger.info("knowledge search skipped: %s", reason)
            return {
                "query": query,
                "top_k": top_k,
                "hits": [],
                "count": 0,
                "enabled": False,
                "reason": reason,
                "message": message,
                "retrieval": {
                    "mode": "unavailable",
                    "fts_available": False,
                    "status": "unavailable",
                    "reason": reason,
                    "message": message,
                    "enabled": False,
                    "top_score": None,
                    "runner_up_score": None,
                    "thresholds": {},
                    "abstained": False,
                    "hits": [],
                },
            }
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

    def unassigned_documents(
        self, *, limit: int = 50, offset: int = 0, preview_chars: int = 240
    ) -> Dict[str, Any]:
        """无归属历史文档的只读视图（路由层只允许主账号/老部署访问）。

        这些文档没有 ``owner_account_id``，旧 id 也无法反推账号，因此任何
        成员的 self 列表都看不到；但它们仍在同一向量库里、可以被检索与删除。
        这里给出可核对的身份、原因、首块原文预览与删除入口，避免"只有一个计数、
        用户永远不知道是哪一篇"。
        """
        store = self._store()
        # owners=() 只取无归属文档：不读取任何账号的私有文档，隔离语义不变。
        documents = [
            document
            for document in store.raw_documents(owners=(), include_unowned=True)
            if not str(document.get("owner") or "")
        ]
        total = len(documents)
        page = documents[offset:offset + limit + 1]
        has_more = len(page) > limit
        items: List[Dict[str, Any]] = []
        for document in page[:limit]:
            document_id = str(document["document_id"])
            display = (
                str(document.get("filename") or "").strip() or document_id
            )
            chunks = store.get_document_chunks(document_id)
            preview = ""
            if chunks:
                first = chunks[0]
                body = redact_for_output(first.get("text"))
                header = redact_for_output(first.get("context_header"))
                preview = f"[{header}]\n{body}" if header else body
            preview_truncated = len(preview) > preview_chars
            items.append(
                {
                    "document_id": document_id,
                    "owner": "",
                    "display_name": display,
                    "chunks": int(document.get("chunks") or 0),
                    "reason": "missing_owner_metadata",
                    "detail": (
                        "缺少 owner_account_id，旧 id 也无法反推原始账号；"
                        "不会自动归给任何账号，需要主账号人工确认后删除或重新入库。"
                    ),
                    "preview": preview[:preview_chars],
                    "preview_truncated": preview_truncated,
                    "reference": f"knowledge://documents/{document_id}#0",
                    "can_delete": True,
                }
            )
        return {
            "documents": items,
            "count": len(items),
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": has_more,
            "lost": 0,
            "note": (
                "这些是无归属的历史文档：不会出现在任何成员的列表中，"
                "主账号可在此查看首块原文与引用，并用 "
                "DELETE /v1/knowledge/documents/{document_id} 处置。"
            ),
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
