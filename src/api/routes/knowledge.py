"""Knowledge base HTTP surface (live).

Canonical prefix is ``/v1/knowledge`` to match the rest of the gateway;
``/knowledge`` is kept as a deprecated alias so existing clients (and the
single-port reserved-path list) keep working. Both are behind the human-principal
gate, and every handler resolves the caller's **account scope** from the verified
token: every account reads/writes/deletes only its own documents. The root
account is **not** an exception (knowledge is a per-account asset); it only
keeps access to unowned legacy documents from pre-account installations.
Cross-account access is a neutral 404 (indistinguishable from "not found").

Retrieval and ingest go through :class:`KnowledgeDocumentService`, which uses
the process-level SQLite vector store and a **real** embedding provider. When no
provider is configured the endpoints answer 503 with "未启用" -- they never
return fabricated vectors or a mock-written answer.

``POST /v1/knowledge/search`` returns the finalized relevance-gate contract
(owned by :mod:`src.knowledge.abstention`; do not rename):

* ``retrieval.status`` -- exactly ``"ok"`` or ``"no_relevant"``;
* ``retrieval.reason`` -- ``null`` when ok, otherwise ``"below_threshold"`` /
  ``"ambiguous"`` / ``"empty_corpus"`` (``empty_corpus`` means the library has
  zero documents and must never be merged with "no match");
* ``retrieval.top_score`` / ``retrieval.runner_up_score`` -- highest and second
  cosine score, ``null`` when absent;
* ``retrieval.thresholds`` -- effective ``min_top_score`` / ``min_score_gap``;
* ``retrieval.hits`` -- mirrors the top-level ``hits`` and **must be empty**
  when the status is ``no_relevant``; low-score padding is forbidden.

``POST /v1/knowledge/documents/upload`` accepts **files**, not just JSON text:
``.pdf`` / ``.docx`` / ``.html`` / ``.htm`` / ``.txt`` / ``.md``. Extraction is
owned by :mod:`src.knowledge.extractors`; a scanned PDF is a 422
(``该 PDF 无文本层，需要 OCR，暂不支持``), never an empty success. PDF chunks
carry ``page`` metadata and search hits expose ``page`` / ``pages`` /
``location``; a sentence that crosses a page boundary stays in one chunk whose
``pages`` is ``[2, 3]``.

``POST /v1/knowledge/documents/upload/jobs`` is the async twin (202 + job id):
extraction and embedding run in a worker, progress is counted in real pages
(``total_pages`` / ``extracted_pages`` / ``embedded_pages``), and cancelling is
a full rollback -- nothing is written until the whole document is embedded.

A text layer that is mostly replacement/block glyphs is refused with
``该文件可能是扫描件或字体嵌入异常，提取质量差`` (422,
``error_code="low_quality_extraction"``); resubmitting with
``allow_low_quality=true`` is the explicit "store it anyway" decision.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from typing import Any, Dict, Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from pydantic import BaseModel, Field

from src.knowledge.documents import (
    KnowledgeScope,
    get_knowledge_service,
)
from src.knowledge.embedding import EmbeddingUnavailableError
from src.knowledge.extractors import (
    DEFAULT_LIMITS,
    ExtractionError,
    FileTooLarge,
    extract_document,
)
from src.knowledge.ingest_jobs import IngestQueueFull, get_ingest_job_manager

core = APIRouter(tags=["knowledge"])

logger = logging.getLogger(__name__)


def _audit_document_deleted(scope: KnowledgeScope, document_id: str, chunks: int) -> None:
    """删除文档写入哈希链；审计故障不改变"已经删掉"的事实。"""
    try:
        from src.kernels.identity import get_identity_manager

        get_identity_manager().record_event(
            action="knowledge.document.deleted",
            actor=scope.principal or "",
            target=document_id,
            outcome="allowed",
            details={
                "document_id": document_id,
                "chunks_removed": int(chunks),
            },
        )
    except Exception:  # noqa: BLE001 - 审计不可用不能反向让删除失败
        logger.exception("could not audit knowledge delete for %s", document_id)


class IngestRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=200_000)
    doc_id: Optional[str] = Field(default=None, max_length=128)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=8000)
    top_k: int = Field(default=4, ge=1, le=20)
    threshold: Optional[float] = Field(default=None, ge=-1.0, le=1.0)


def _unavailable(exc: Exception) -> HTTPException:
    message = str(exc)
    if not message.startswith("知识库未启用"):
        message = f"知识库未启用：{message}"
    return HTTPException(status_code=503, detail=message)


def _not_found() -> HTTPException:
    """越权/不存在同形：中性 404，绝不暴露"存在但不属于你"。"""
    from src.gateway.account_context import deny
    from src.kernels.identity import _accounts

    return deny(
        404, _accounts.CODE_SUBTREE_DENIED, _accounts.NEUTRAL_NOT_FOUND_MESSAGE
    )


def _scope_from_account_context(context: Any) -> KnowledgeScope:
    """账号树位置 -> 知识库读写范围（每个账号只看/只删自己的）。

    知识库是账号自有资产：主账号与成员都**不聚合**子账号文档，避免
    "主账号检索时把成员资料混进回答、删除时误伤成员数据"（E2E 中已复现
    主账号聚合检索导致回答引用错文档）。主账号额外保留对无归属历史文档
    的可见/可管理能力，兼容账号体系上线前的单机数据。
    """
    if context.standalone:
        # 老部署/单机令牌：历史无归属文档仍归自己，新写入沿用无归属 id。
        return KnowledgeScope(
            principal=context.principal,
            owners=(context.principal,),
            include_unowned=True,
            owns_legacy=True,
            unowned_writes=True,
        )
    if context.is_member:
        return KnowledgeScope(
            principal=context.principal,
            owners=(context.principal,),
            include_unowned=False,
            owns_legacy=False,
        )
    return KnowledgeScope(
        principal=context.principal,
        owners=(context.principal,),
        include_unowned=True,
        owns_legacy=True,
    )


def _knowledge_scope(request: Request) -> KnowledgeScope:
    """Per-request knowledge scope derived from the *verified* token only."""
    from src.gateway.account_context import require_account_principal
    from src.gateway.policy import (
        STANDARD_TOKEN_HEADER,
        TOKEN_HEADER,
        require_bearer_payload,
    )

    payload = require_bearer_payload(
        x_liuhao_token=request.headers.get(TOKEN_HEADER),
        authorization=request.headers.get(STANDARD_TOKEN_HEADER),
    )
    return _scope_from_account_context(require_account_principal(payload=payload))


@core.get("/status")
def knowledge_status() -> Dict[str, Any]:
    """Always 200: lets the console show "未启用" instead of hiding the feature."""
    return get_knowledge_service().status()


@core.post("/documents")
def knowledge_ingest(
    payload: IngestRequest,
    scope: KnowledgeScope = Depends(_knowledge_scope),
) -> Dict[str, Any]:
    try:
        return get_knowledge_service().ingest(
            payload.text,
            doc_id=payload.doc_id,
            metadata=payload.metadata,
            owner_account_id=scope.write_owner,
        )
    except EmbeddingUnavailableError as exc:
        raise _unavailable(exc)
    except (OSError, sqlite3.Error) as exc:
        raise HTTPException(status_code=503, detail=f"向量库不可用：{exc}")


@core.post("/search")
def knowledge_search(
    payload: SearchRequest,
    scope: KnowledgeScope = Depends(_knowledge_scope),
) -> Dict[str, Any]:
    try:
        return get_knowledge_service().search(
            payload.query,
            top_k=payload.top_k,
            threshold=payload.threshold,
            scope=scope,
        )
    except EmbeddingUnavailableError as exc:
        raise _unavailable(exc)
    except (OSError, sqlite3.Error) as exc:
        raise HTTPException(status_code=503, detail=f"向量库不可用：{exc}")


@core.post("/query", deprecated=True)
def knowledge_query(
    payload: SearchRequest,
    scope: KnowledgeScope = Depends(_knowledge_scope),
) -> Dict[str, Any]:
    """Retrieval-only RAG contract. Generation stays on ``/v1/chat``.

    The old handler answered with a mock-written ``answer``; returning that from
    a production endpoint is exactly the "pretend it works" failure this pass
    removes. ``answer`` is therefore empty and the hits are real.
    """
    try:
        result = get_knowledge_service().search(
            payload.query,
            top_k=payload.top_k,
            threshold=payload.threshold,
            scope=scope,
        )
    except EmbeddingUnavailableError as exc:
        raise _unavailable(exc)
    except (OSError, sqlite3.Error) as exc:
        raise HTTPException(status_code=503, detail=f"向量库不可用：{exc}")

    hits = result["hits"]
    return {
        "query": payload.query,
        "sources": [
            {
                "doc_id": hit["doc_id"],
                "score": hit["score"],
                "content": hit["text"],
                "metadata": hit["metadata"],
            }
            for hit in hits
        ],
        "context": "\n\n".join(hit["text"] for hit in hits if hit["text"]),
        "answer": "",
        "metadata": {
            "provider": None,
            "retrieval": "vector_similarity",
            "hits": len(hits),
            "note": "仅检索；生成回答请调用 /v1/chat",
        },
    }


router = APIRouter(prefix="/v1/knowledge", tags=["knowledge"])
router.include_router(core)

#: Deprecated compatibility alias. Kept because the console's reserved-prefix
#: list and existing clients still reference ``/knowledge``.
legacy_router = APIRouter(prefix="/knowledge", tags=["knowledge"])
legacy_router.include_router(core, deprecated=True)


# ==================== Document administration (append-only) ====================
#
# Everything below was appended for the knowledge-center UI. The handlers above
# are intentionally untouched: another workstream is editing the gateway, and a
# merge conflict inside an existing handler would be a regression neither side
# asked for.

_MAX_UPLOAD_BYTES = DEFAULT_LIMITS.max_file_bytes
_READ_CHUNK_BYTES = 1024 * 1024

#: New routes live on their own router because ``core`` was already included
#: into ``router`` / ``legacy_router`` above: routes appended to ``core`` after
#: that point would never be mounted. These are included at the end of the file.
documents_admin = APIRouter(tags=["knowledge"])


def _storage_error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=503, detail=f"向量库不可用：{exc}")


@documents_admin.get("/documents")
def knowledge_list_documents(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    scope: KnowledgeScope = Depends(_knowledge_scope),
) -> Dict[str, Any]:
    """Paged document ledger (chunk count, ingest time, source, content hash)."""
    try:
        return get_knowledge_service().list_documents(
            limit=limit, offset=offset, scope=scope
        )
    except (OSError, sqlite3.Error) as exc:
        raise _storage_error(exc)


@documents_admin.get("/documents/{document_id}/chunks")
def knowledge_document_chunks(
    document_id: str,
    scope: KnowledgeScope = Depends(_knowledge_scope),
) -> Dict[str, Any]:
    """Every chunk of one document (ordered), not just the top-k search hits."""
    try:
        result = get_knowledge_service().document_chunks(document_id, scope=scope)
    except (OSError, sqlite3.Error) as exc:
        raise _storage_error(exc)
    if result["count"] == 0:
        raise _not_found()
    return result


@documents_admin.delete("/documents/{document_id}")
def knowledge_delete_document(
    document_id: str,
    scope: KnowledgeScope = Depends(_knowledge_scope),
) -> Dict[str, Any]:
    """Delete a document: chunks, vectors, FTS rows and content hash at once.

    Authorisation is the account scope, not an admin claim: a member may delete
    only their own document, the root account may delete documents in its
    subtree, and anything outside that range is a neutral 404 -- byte-identical
    to "this document does not exist", so the endpoint cannot be used to probe
    another account's library.
    """
    try:
        result = get_knowledge_service().delete_document(
            document_id, scope=scope
        )
    except (OSError, sqlite3.Error) as exc:
        raise _storage_error(exc)
    if not result["deleted"]:
        raise _not_found()
    _audit_document_deleted(
        scope, document_id, int(result.get("chunks_removed") or 0)
    )
    return result


async def _read_upload_limited(file: UploadFile, limit: int) -> bytes:
    """Read in chunks and refuse past ``limit`` instead of buffering to OOM."""
    buffer = bytearray()
    while True:
        chunk = await file.read(_READ_CHUNK_BYTES)
        if not chunk:
            break
        buffer.extend(chunk)
        if len(buffer) > limit:
            raise FileTooLarge(
                f"文件过大：已读取超过上限 "
                f"{limit // (1024 * 1024)}MB；请压缩或拆分后再上传"
            )
    return bytes(buffer)


@documents_admin.post("/documents/upload")
async def knowledge_upload_document(
    file: UploadFile = File(...),
    doc_id: Optional[str] = Form(default=None),
    allow_low_quality: bool = Form(default=False),
    scope: KnowledgeScope = Depends(_knowledge_scope),
) -> Dict[str, Any]:
    """Multipart upload for PDF / DOCX / HTML / plain text (synchronous).

    Every format is reduced to extracted text **before** it reaches the index,
    so the content hash (and therefore dedup) is computed on what the library
    actually says, not on the file name or bytes. PDF chunks carry ``page``
    metadata and search hits expose ``page`` / ``location``.

    Errors are the extractor's own readable messages:
      * 413 -- file size / PDF page count / extracted length over the cap;
      * 415 -- unsupported suffix;
      * 422 -- scanned PDF (no text layer), encrypted PDF, corrupt file, empty
        document, or a text layer full of broken glyphs. A scanned PDF never
        returns an empty string as "success", and a garbled text layer is never
        stored silently: the caller must retry with ``allow_low_quality=true``.

    Large files belong on ``POST /documents/upload/jobs``: this handler blocks
    until extraction + embedding finish.
    """
    filename = (file.filename or "").strip()
    if not filename:
        raise HTTPException(status_code=400, detail="缺少文件名")

    try:
        data = await _read_upload_limited(file, _MAX_UPLOAD_BYTES)
        # 提取与嵌入都是同步 CPU/IO 阻塞操作；放到工作线程，避免大文件上传
        # 期间把事件循环堵死（健康检查、对话等请求会一起排队）。
        extracted = await asyncio.to_thread(
            extract_document,
            data,
            filename,
            allow_low_quality=allow_low_quality,
        )
    except ExtractionError as exc:
        raise HTTPException(status_code=exc.http_status, detail=str(exc))

    metadata: Dict[str, Any] = {
        "source": filename,
        "filename": filename,
        "file_type": extracted.file_type,
        **extracted.metadata,
    }
    if extracted.title:
        metadata["title"] = extracted.title

    try:
        result = await asyncio.to_thread(
            get_knowledge_service().ingest,
            extracted.text,
            doc_id=doc_id or filename,
            metadata=metadata,
            pages=extracted.pages or None,
            owner_account_id=scope.write_owner,
        )
    except EmbeddingUnavailableError as exc:
        raise _unavailable(exc)
    except (OSError, sqlite3.Error) as exc:
        raise _storage_error(exc)

    result["source"] = filename
    result["extraction"] = extracted.summary()
    return result


class IngestJobRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=200_000)
    doc_id: Optional[str] = Field(default=None, max_length=128)
    metadata: Dict[str, Any] = Field(default_factory=dict)


@documents_admin.post("/documents/upload/jobs", status_code=202)
async def knowledge_submit_upload_job(
    file: UploadFile = File(...),
    doc_id: Optional[str] = Form(default=None),
    allow_low_quality: bool = Form(default=False),
    scope: KnowledgeScope = Depends(_knowledge_scope),
) -> Dict[str, Any]:
    """Queue a **file** ingest job and return immediately with a job id.

    This is the async twin of ``POST /documents/upload``: the bytes are read
    here, then extraction and embedding happen in the worker. A 300-page PDF
    therefore answers 202 instantly and reports real page progress via
    ``GET /ingest/jobs/{job_id}`` (``total_pages`` / ``extracted_pages`` /
    ``embedded_pages`` / ``progress_percent``). Cancel with
    ``POST /ingest/jobs/{job_id}/cancel``: nothing is written until the whole
    document is embedded, so a cancel is a full rollback (全回滚).

    Extraction failures (scanned PDF, encrypted/corrupt file, garbled text layer)
    do not fail the *request* -- they are reported on the job as
    ``status=failed`` with ``error_code`` and the extractor's message. For a
    garbled text layer the job keeps the measured ``quality`` and the caller
    can resubmit with ``allow_low_quality=true`` after inspecting it.
    """
    filename = (file.filename or "").strip()
    if not filename:
        raise HTTPException(status_code=400, detail="缺少文件名")

    status = get_knowledge_service().status()
    if not status.get("enabled"):
        raise HTTPException(
            status_code=503, detail=f"知识库未启用：{status.get('reason')}"
        )

    try:
        data = await _read_upload_limited(file, _MAX_UPLOAD_BYTES)
    except ExtractionError as exc:
        raise HTTPException(status_code=exc.http_status, detail=str(exc))

    try:
        return get_ingest_job_manager().submit_file(
            data,
            filename,
            doc_id=doc_id,
            allow_low_quality=allow_low_quality,
            owner_account_id=scope.write_owner,
        )
    except IngestQueueFull as exc:
        raise HTTPException(
            status_code=429,
            detail={
                "code": exc.code,
                "message": str(exc),
                "pending": exc.pending,
                "limit": exc.limit,
            },
        ) from exc


@documents_admin.post("/ingest/jobs", status_code=202)
def knowledge_submit_ingest_job(
    payload: IngestJobRequest,
    scope: KnowledgeScope = Depends(_knowledge_scope),
) -> Dict[str, Any]:
    """Queue a raw-text ingest job; cancellation lands at chunk boundaries."""
    status = get_knowledge_service().status()
    if not status.get("enabled"):
        raise HTTPException(
            status_code=503, detail=f"知识库未启用：{status.get('reason')}"
        )
    try:
        return get_ingest_job_manager().submit(
            payload.text,
            doc_id=payload.doc_id,
            metadata=payload.metadata,
            owner_account_id=scope.write_owner,
        )
    except IngestQueueFull as exc:
        raise HTTPException(
            status_code=429,
            detail={
                "code": exc.code,
                "message": str(exc),
                "pending": exc.pending,
                "limit": exc.limit,
            },
        ) from exc


@documents_admin.get("/ingest/jobs/{job_id}")
def knowledge_get_ingest_job(
    job_id: str,
    scope: KnowledgeScope = Depends(_knowledge_scope),
) -> Dict[str, Any]:
    job = get_ingest_job_manager().get(job_id, scope=scope)
    if job is None:
        raise _not_found()
    return job


@documents_admin.post("/ingest/jobs/{job_id}/cancel")
def knowledge_cancel_ingest_job(
    job_id: str,
    scope: KnowledgeScope = Depends(_knowledge_scope),
) -> Dict[str, Any]:
    """Request cancellation; it lands at the next chunk boundary."""
    job = get_ingest_job_manager().cancel(job_id, scope=scope)
    if job is None:
        raise _not_found()
    return job


# Mounted last on purpose: the routers above were built before these routes
# existed, so a second include is the append-only way to publish them.
router.include_router(documents_admin)
legacy_router.include_router(documents_admin, deprecated=True)
