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
the process-level vector store (local SQLite by default, Qdrant when one is
reachable and selected) and a **real** embedding provider. When no external
embedding service is reachable the service falls back to deterministic local
hash vectors and says so in ``GET /v1/knowledge/status`` (``embedding_mode`` /
``semantic`` / ``vector_backend``) -- the fallback is lexical, not semantic,
and its scores are real token-overlap cosines, never placeholders. Set
``LIUHAO_KNOWLEDGE_LOCAL_FALLBACK=0`` to restore the old behaviour: no
provider -> the endpoints answer 503 with "未启用". They never return
fabricated vectors or a mock-written answer.

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
import os
import sqlite3
import tempfile
import weakref
from pathlib import Path
from typing import Any, Dict, List, Optional

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
from pydantic import BaseModel, Field, field_validator

from src.common.input_guard import (
    ensure_json_serializable,
    normalize_required_name,
    normalize_required_text,
)

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

    @field_validator("text")
    @classmethod
    def _check_text(cls, value: str) -> str:
        return normalize_required_text(value, "文档内容", max_chars=200_000)

    @field_validator("doc_id")
    @classmethod
    def _check_doc_id(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        return normalize_required_name(value, "文档 id", max_chars=128)

    @field_validator("metadata")
    @classmethod
    def _check_metadata(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        # 深嵌套 metadata 落库后会让检索/序列化长期 400，必须在写入前拒绝。
        if value:
            ensure_json_serializable(value, "metadata")
        return value


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=8000)
    top_k: int = Field(default=4, ge=1, le=20)
    threshold: Optional[float] = Field(default=None, ge=-1.0, le=1.0)

    @field_validator("query")
    @classmethod
    def _check_query(cls, value: str) -> str:
        return normalize_required_text(value, "检索问题", max_chars=8000)


def _unavailable(exc: Exception) -> HTTPException:
    from src.gateway.display import UserReadableHTTPException

    message = str(exc)
    if not message.startswith("知识库未启用"):
        message = f"知识库未启用：{message}"
    # 这条 503 是"照着做就能恢复"的运维文案（启动 Ollama / 配置 provider），
    # 显式标记为可透传；网关仍会做一次路径/堆栈/SQL 安全闸。
    return UserReadableHTTPException(status_code=503, detail=message)


def _not_found() -> HTTPException:
    """越权/不存在同形：中性 404，绝不暴露"存在但不属于你"。"""
    from src.gateway.account_context import deny
    from src.kernels.identity import _accounts

    return deny(
        404, _accounts.CODE_SUBTREE_DENIED, _accounts.NEUTRAL_NOT_FOUND_MESSAGE
    )


def _scope_from_account_context(context: Any) -> KnowledgeScope:
    """账号树位置 -> 知识库读写范围（每个账号只看/只删自己的）。

    知识库是账号**自有资产**（``docs/account-hierarchy.md`` §7.4：日常能力，
    所有账号限自有范围），主账号与成员一样**不聚合**下级文档：列表、检索、
    分块内容都只返回自己的。这样成员资料不会被混进主账号的回答，也让
    "读取不串"与删除边界同口径。主账号额外保留对无归属历史文档的可见/可
    管理能力，兼容账号体系上线前的单机数据。
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
        "enabled": result.get("enabled", True),
        "reason": result.get("reason"),
        "message": result.get("message"),
        "retrieval": result.get("retrieval"),
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

#: 上传处理并发上限。同步上传会占住一个工作线程做解析 + 嵌入；异步上传在写
#: 临时文件阶段也占一个槽位。2 个槽位与入库线程池 ``max_workers=2`` 对齐，
#: 超出的请求在真正读盘/解析前排队，而不是 N 份 20MB 上传同时展开。
_UPLOAD_CONCURRENCY = 2
_upload_limiters: "weakref.WeakKeyDictionary[Any, asyncio.Semaphore]" = (
    weakref.WeakKeyDictionary()
)

_TEMP_UPLOAD_PREFIX = "liuhao-kb-upload-"


def _upload_limiter() -> asyncio.Semaphore:
    """每个事件循环一个信号量：测试或多 loop 场景不会把等待者绑错 loop。"""
    loop = asyncio.get_running_loop()
    limiter = _upload_limiters.get(loop)
    if limiter is None:
        limiter = asyncio.Semaphore(_UPLOAD_CONCURRENCY)
        _upload_limiters[loop] = limiter
    return limiter


def _cleanup_temp_upload(path: Optional[Path]) -> None:
    """删除落盘的上传临时文件；失败只记日志，不覆盖原始错误。"""
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.warning("knowledge upload temp file could not be removed: %s", path)


async def _stream_upload_to_temp(file: UploadFile, limit: int) -> Path:
    """分块把上传写成临时文件：整个文件从不进入进程堆。

    超限、客户端断开或磁盘写失败时自己清理，绝不留下半截文件。调用方在
    用完路径后必须调用 :func:`_cleanup_temp_upload`。
    """
    suffix = Path(file.filename or "").suffix.lower()
    fd, name = tempfile.mkstemp(prefix=_TEMP_UPLOAD_PREFIX, suffix=suffix)
    os.close(fd)
    path = Path(name)
    total = 0
    try:
        with path.open("wb") as sink:
            while True:
                chunk = await file.read(_READ_CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > limit:
                    raise FileTooLarge(
                        f"文件过大：已读取超过上限 "
                        f"{limit // (1024 * 1024)}MB；请压缩或拆分后再上传"
                    )
                sink.write(chunk)
        return path
    except BaseException:
        _cleanup_temp_upload(path)
        raise


class UnassignedKnowledgeDocument(BaseModel):
    """一篇无归属历史文档（``owner_account_id`` 为空，无法反推账号）。"""

    document_id: str
    owner: str = Field(description="恒为空串：不属于任何账号")
    display_name: str
    chunks: int
    reason: str = Field(examples=["missing_owner_metadata"])
    detail: str
    preview: str = Field(description="首块原文预览（已脱敏、已截断）")
    preview_truncated: bool
    reference: str = Field(examples=["knowledge://documents/legacy.txt#0"])
    can_delete: bool


class UnassignedKnowledgeResponse(BaseModel):
    """``GET /v1/knowledge/maintenance/unassigned``（主账号/老部署专属）。"""

    documents: List[UnassignedKnowledgeDocument]
    count: int
    total: int
    limit: int
    offset: int
    has_more: bool
    lost: int = Field(description="迁移丢失数；只读视图自身恒为 0")
    note: str


class KnowledgeNeutralNotFound(BaseModel):
    """越权与"不存在"同形的 404 实体（不暴露文档是否存在）。"""

    code: str
    message: str
    detail: str


_KNOWLEDGE_NOT_FOUND_RESPONSE: Dict[int, Dict[str, Any]] = {
    404: {
        "model": KnowledgeNeutralNotFound,
        "description": (
            "非主账号一律中性 404：与「这篇文档不存在」逐字一致，"
            "不泄露无归属文档的存在。"
        ),
    }
}


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


@documents_admin.get(
    "/maintenance/unassigned",
    response_model=UnassignedKnowledgeResponse,
    responses=_KNOWLEDGE_NOT_FOUND_RESPONSE,
    summary="无归属历史文档只读视图（仅主账号）",
)
def knowledge_unassigned_documents(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    scope: KnowledgeScope = Depends(_knowledge_scope),
) -> Dict[str, Any]:
    """无归属历史文档的只读视图（主账号/老部署专用；成员中性 404）。

    只返回 ``owner_account_id`` 为空的文档，不读取任何账号的私有文档；
    成员访问与"不存在"同形，隔离边界不放宽。
    """
    if not scope.owns_legacy:
        raise _not_found()
    try:
        return get_knowledge_service().unassigned_documents(
            limit=limit, offset=offset
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

    Authorisation is the account scope, not an admin claim: every account (the
    root included) may delete only its own document, and anything outside that
    range is a neutral 404 -- byte-identical to "this document does not exist",
    so the endpoint cannot be used to probe another account's library.
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

    The body is streamed to a temporary file first, so peak memory stays flat
    regardless of file size; the temp file is removed on success and on every
    failure path.
    """
    filename = (file.filename or "").strip()
    if not filename:
        raise HTTPException(status_code=400, detail="缺少文件名")
    _reject_unsafe_upload_names(filename, doc_id)

    async with _upload_limiter():
        try:
            temp_path = await _stream_upload_to_temp(file, _MAX_UPLOAD_BYTES)
            try:
                # 解析按路径读盘；提取与嵌入都是同步阻塞操作，放到工作线程，
                # 避免大文件上传期间把事件循环堵死。
                extracted = await asyncio.to_thread(
                    extract_document,
                    temp_path,
                    filename,
                    allow_low_quality=allow_low_quality,
                )
            finally:
                # 提取完成（或失败）后正文已在内存里，临时文件不再需要。
                _cleanup_temp_upload(temp_path)
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

    @field_validator("text")
    @classmethod
    def _check_text(cls, value: str) -> str:
        return normalize_required_text(value, "文档内容", max_chars=200_000)

    @field_validator("doc_id")
    @classmethod
    def _check_doc_id(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        return normalize_required_name(value, "文档 id", max_chars=128)

    @field_validator("metadata")
    @classmethod
    def _check_metadata(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        if value:
            ensure_json_serializable(value, "metadata")
        return value


#: 上传文件名 / 显式文档 id 的上限。文件名会同时写进 ``source`` 元数据，
#: 不设上限时 3004 字的文件名会原样留在检索结果里。
_MAX_FILENAME_CHARS = 255


def _reject_unsafe_upload_names(filename: str, doc_id: Optional[str]) -> None:
    from src.common.input_guard import reject_unsafe_text

    if len(filename) > _MAX_FILENAME_CHARS:
        raise HTTPException(
            status_code=422,
            detail=f"文件名过长，最多允许 {_MAX_FILENAME_CHARS} 个字符（当前 {len(filename)} 个）。",
        )
    try:
        reject_unsafe_text(filename, "文件名")
        if doc_id:
            reject_unsafe_text(doc_id, "文档 id")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if doc_id and len(doc_id) > 128:
        raise HTTPException(
            status_code=422,
            detail=f"文档 id 过长，最多允许 128 个字符（当前 {len(doc_id)} 个）。",
        )


@documents_admin.post("/documents/upload/jobs", status_code=202)
async def knowledge_submit_upload_job(
    file: UploadFile = File(...),
    doc_id: Optional[str] = Form(default=None),
    allow_low_quality: bool = Form(default=False),
    scope: KnowledgeScope = Depends(_knowledge_scope),
) -> Dict[str, Any]:
    """Queue a **file** ingest job and return immediately with a job id.

    This is the async twin of ``POST /documents/upload``: the body is streamed
    to a temporary file here, then extraction and embedding happen in the
    worker **from that path** (no full bytes copy held by the job). A 300-page
    PDF therefore answers 202 instantly and reports real page progress via
    ``GET /ingest/jobs/{job_id}`` (``total_pages`` / ``extracted_pages`` /
    ``embedded_pages`` / ``progress_percent``). Cancel with
    ``POST /ingest/jobs/{job_id}/cancel``: nothing is written until the whole
    document is embedded, so a cancel is a full rollback (全回滚). The worker
    removes the temp file on success, failure and cancellation alike.

    Extraction failures (scanned PDF, encrypted/corrupt file, garbled text layer)
    do not fail the *request* -- they are reported on the job as
    ``status=failed`` with ``error_code`` and the extractor's message. For a
    garbled text layer the job keeps the measured ``quality`` and the caller
    can resubmit with ``allow_low_quality=true`` after inspecting it.
    """
    filename = (file.filename or "").strip()
    if not filename:
        raise HTTPException(status_code=400, detail="缺少文件名")
    _reject_unsafe_upload_names(filename, doc_id)

    status = get_knowledge_service().status()
    if not status.get("enabled"):
        # reason 已自带「知识库未启用」或「向量库不可用」前缀，显式标记透传
        # （仍过网关的路径/堆栈/SQL 安全闸）。
        from src.gateway.display import UserReadableHTTPException

        raise UserReadableHTTPException(
            status_code=503, detail=f"{status.get('reason') or '知识库不可用'}"
        )

    try:
        async with _upload_limiter():
            temp_path = await _stream_upload_to_temp(file, _MAX_UPLOAD_BYTES)
            try:
                # 传路径而不是 bytes：任务存活期间不再驻留一份完整文件副本。
                return get_ingest_job_manager().submit_file(
                    temp_path,
                    filename,
                    doc_id=doc_id,
                    allow_low_quality=allow_low_quality,
                    owner_account_id=scope.write_owner,
                )
            except BaseException:
                # 入队失败（队列满 / 执行器已关闭 / 请求被取消）由这里清理；
                # 入队成功后临时文件所有权交给 job，由 worker 的 finally 删除。
                _cleanup_temp_upload(temp_path)
                raise
    except ExtractionError as exc:
        raise HTTPException(status_code=exc.http_status, detail=str(exc))
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
        # reason 已自带「知识库未启用」或「向量库不可用」前缀，显式标记透传
        # （仍过网关的路径/堆栈/SQL 安全闸）。
        from src.gateway.display import UserReadableHTTPException

        raise UserReadableHTTPException(
            status_code=503, detail=f"{status.get('reason') or '知识库不可用'}"
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


@documents_admin.post("/maintenance/reindex")
def knowledge_reindex(
    scope: KnowledgeScope = Depends(_knowledge_scope),
) -> Dict[str, Any]:
    """按当前 embedding 模型重建可见文档向量（换模型/维度漂移后的恢复入口）。"""
    try:
        return get_knowledge_service().reindex(scope=scope)
    except EmbeddingUnavailableError as exc:
        raise _unavailable(exc)
    except (OSError, sqlite3.Error) as exc:
        raise _storage_error(exc)


@documents_admin.post("/maintenance/migrate-document-ids")
def knowledge_migrate_document_ids(
    dry_run: bool = Query(default=True),
    scope: KnowledgeScope = Depends(_knowledge_scope),
) -> Dict[str, Any]:
    """迁移旧版 CJK 坍缩 id；默认 dry-run，返回完整计划与条数守恒结论。"""
    try:
        report = get_knowledge_service().migrate_legacy_document_ids(
            scope=scope, dry_run=dry_run
        )
    except (OSError, sqlite3.Error) as exc:
        raise _storage_error(exc)
    _audit_document_migrated(scope, report)
    return report


def _audit_document_migrated(scope: KnowledgeScope, report: Dict[str, Any]) -> None:
    """迁移必须留审计：谁、迁了多少、是否守恒、失败数。"""
    try:
        from src.kernels.audit import AuditEventType, AuditScope, log_event

        log_event(
            AuditEventType.STATE_CHANGE,
            principal_id=scope.principal,
            scope=AuditScope.L1,
            outcome="allow",
            details={
                "action": "knowledge.migrate_document_ids",
                "dry_run": report.get("dry_run"),
                "documents_before": report.get("documents_before"),
                "documents_after": report.get("documents_after"),
                "count_conserved": report.get("count_conserved"),
                "lost_documents": report.get("lost_documents"),
                "rows_accounted": report.get("rows_accounted"),
                "unassigned_documents": report.get("unassigned_documents"),
                "migrated": report.get("migrated"),
                "conflicts": report.get("conflicts"),
                "failed": report.get("failed"),
            },
        )
    except Exception:  # noqa: BLE001 - 审计失败不否决迁移本身
        pass


# Mounted last on purpose: the routers above were built before these routes
# existed, so a second include is the append-only way to publish them.
router.include_router(documents_admin)
legacy_router.include_router(documents_admin, deprecated=True)
