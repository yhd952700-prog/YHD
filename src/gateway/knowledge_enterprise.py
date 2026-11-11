"""企业知识库 / 长期记忆 HTTP 接口。

挂载约定
========

守门线在 ``src/gateway/main.py`` 现有 knowledge 路由之后加两行即可：

.. code-block:: python

    from .knowledge_enterprise import router as knowledge_enterprise_router
    app.include_router(knowledge_enterprise_router)

路由自带走令牌鉴权（``require_viewer``），不需要再套 ``_require_human``。

权限口径
========

* 账号主体只来自经核验的令牌，请求体里的 ``owner`` / ``account`` 字段一律忽略；
* 越权与不存在统一 404，不泄露「存在但不属于你」；
* 文档读按分类可见性（private/team/enterprise）判定，写/删只允许上传者本人；
* 记忆与知识库分库，记忆接口没有任何文档写入口。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from src.ai.knowledge import memory as memory_service
from src.ai.knowledge import service
from src.ai.knowledge.service import KnowledgeError, Viewer
from src.ai.knowledge.store import knowledge_db_path, knowledge_files_dir, memory_db_path
from src.ai.knowledge.text import TextExtractionError

router = APIRouter(prefix="/v1/knowledge/enterprise", tags=["knowledge-enterprise"])

#: 单文件上限。单机自用场景下 25MB 足够放制度/产品/表格，也不会把请求拖死。
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
_READ_CHUNK = 1024 * 1024


def require_viewer(request: Request) -> Viewer:
    """从经核验的令牌推导账号视角；测试可通过依赖覆盖注入。"""
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
    context = require_account_principal(payload=payload)
    return Viewer(
        principal=context.principal,
        subtree=tuple(context.subtree or (context.principal,)),
        is_root=bool(context.is_root),
    )


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, TextExtractionError):
        return HTTPException(
            status_code=exc.status,
            detail={"code": exc.code, "message": str(exc)},
        )
    if isinstance(exc, KnowledgeError):
        return HTTPException(
            status_code=exc.status,
            detail={"code": exc.code, "message": str(exc)},
        )
    return HTTPException(status_code=500, detail={"code": "internal_error", "message": "服务内部错误"})


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except (KnowledgeError, TextExtractionError) as exc:
        raise _http_error(exc) from exc


# ---------------------------------------------------------------------------
# 请求模型
# ---------------------------------------------------------------------------


class CategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    parent_id: str = ""
    visibility: str = "team"
    description: str = ""


class CategoryUpdate(BaseModel):
    name: Optional[str] = None
    parent_id: Optional[str] = None
    visibility: Optional[str] = None
    description: Optional[str] = None


class TextIngest(BaseModel):
    text: str
    title: str = ""
    category_id: str = ""
    doc_key: str = ""
    visibility: str = ""
    project_id: str = ""
    source_ref: str = ""
    description: str = ""
    expires_at: Optional[str] = None
    expiry_notice_days: int = 30
    tags: List[str] = Field(default_factory=list)
    note: str = ""


class DocumentUpdate(BaseModel):
    title: Optional[str] = None
    category_id: Optional[str] = None
    visibility: Optional[str] = None
    expires_at: Optional[str] = None
    clear_expiry: bool = False
    expiry_notice_days: Optional[int] = None
    tags: Optional[List[str]] = None
    description: Optional[str] = None
    project_id: Optional[str] = None
    source_ref: Optional[str] = None


class SearchRequest(BaseModel):
    query: str
    category_id: str = ""
    include_subcategories: bool = True
    project_id: str = ""
    top_k: int = 8
    include_expired: bool = False
    min_score: float = 0.0


class MemoryCreate(BaseModel):
    content: str
    title: str = ""
    project_id: str = ""
    employee_id: str = ""
    scope: str = "personal"
    source: str = ""
    source_ref: str = ""
    tags: List[str] = Field(default_factory=list)
    confidence: Optional[float] = None
    expires_at: Optional[str] = None


class MemoryUpdate(BaseModel):
    content: Optional[str] = None
    title: Optional[str] = None
    project_id: Optional[str] = None
    employee_id: Optional[str] = None
    scope: Optional[str] = None
    source: Optional[str] = None
    source_ref: Optional[str] = None
    tags: Optional[List[str]] = None
    confidence: Optional[float] = None
    expires_at: Optional[str] = None
    clear_expiry: bool = False


def _split_tags(raw: str) -> List[str]:
    return [item.strip() for item in raw.replace("，", ",").replace(";", ",").split(",") if item.strip()]


# ---------------------------------------------------------------------------
# 状态与分类
# ---------------------------------------------------------------------------


@router.get("/status")
def knowledge_status(viewer: Viewer = Depends(require_viewer)) -> Dict[str, Any]:
    data = _call(service.status_counts, viewer)
    data["memory_store"] = memory_db_path()
    data["knowledge_store"] = knowledge_db_path()
    data["memory_scopes"] = list(memory_service.MEMORY_SCOPES)
    return data


@router.get("/categories")
def get_categories(viewer: Viewer = Depends(require_viewer)) -> Dict[str, Any]:
    tree = _call(service.list_categories, viewer)
    return {"categories": tree}


@router.post("/categories", status_code=201)
def add_category(
    payload: CategoryCreate, viewer: Viewer = Depends(require_viewer)
) -> Dict[str, Any]:
    return _call(
        service.create_category,
        viewer,
        name=payload.name,
        parent_id=payload.parent_id,
        visibility=payload.visibility,
        description=payload.description,
    )


@router.patch("/categories/{category_id}")
def edit_category(
    category_id: str,
    payload: CategoryUpdate,
    viewer: Viewer = Depends(require_viewer),
) -> Dict[str, Any]:
    return _call(
        service.update_category,
        viewer,
        category_id,
        name=payload.name,
        parent_id=payload.parent_id,
        visibility=payload.visibility,
        description=payload.description,
    )


@router.delete("/categories/{category_id}")
def remove_category(
    category_id: str, viewer: Viewer = Depends(require_viewer)
) -> Dict[str, Any]:
    return _call(service.delete_category, viewer, category_id)


# ---------------------------------------------------------------------------
# 文档
# ---------------------------------------------------------------------------


@router.post("/documents/upload", status_code=201)
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    title: str = Form(""),
    category_id: str = Form(""),
    doc_key: str = Form(""),
    visibility: str = Form(""),
    project_id: str = Form(""),
    source_ref: str = Form(""),
    description: str = Form(""),
    expires_at: str = Form(""),
    expiry_notice_days: int = Form(30),
    tags: str = Form(""),
    note: str = Form(""),
    allow_low_quality: bool = Form(False),
    viewer: Viewer = Depends(require_viewer),
) -> Dict[str, Any]:
    filename = (file.filename or "").strip()
    if not filename:
        raise HTTPException(
            status_code=422, detail={"code": "invalid_request", "message": "请选择要上传的文件"}
        )
    incoming = knowledge_files_dir() / "_incoming"
    incoming.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix="upload_", dir=incoming)
    temp_path = Path(temp_name)
    written = 0
    try:
        with os.fdopen(handle, "wb") as sink:
            while True:
                block = await file.read(_READ_CHUNK)
                if not block:
                    break
                written += len(block)
                if written > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail={
                            "code": "file_too_large",
                            "message": f"文件超过 {MAX_UPLOAD_BYTES // (1024 * 1024)}MB 上限",
                        },
                    )
                sink.write(block)
        await file.close()
        result = _call(
            service.ingest_file,
            viewer,
            source=temp_path,
            filename=filename,
            title=title,
            category_id=category_id,
            doc_key=doc_key,
            visibility=visibility,
            project_id=project_id,
            source_ref=source_ref,
            description=description,
            expires_at=expires_at or None,
            expiry_notice_days=expiry_notice_days,
            tags=_split_tags(tags) if tags.strip() else [],
            note=note,
            allow_low_quality=allow_low_quality,
        )
        return result
    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:  # pragma: no cover - 清理失败不影响业务结果
                pass


@router.post("/documents/text", status_code=201)
def ingest_text(
    payload: TextIngest, viewer: Viewer = Depends(require_viewer)
) -> Dict[str, Any]:
    return _call(
        service.ingest_text,
        viewer,
        text=payload.text,
        title=payload.title,
        category_id=payload.category_id,
        doc_key=payload.doc_key,
        visibility=payload.visibility,
        project_id=payload.project_id,
        source_ref=payload.source_ref,
        description=payload.description,
        expires_at=payload.expires_at,
        expiry_notice_days=payload.expiry_notice_days,
        tags=payload.tags,
        note=payload.note,
    )


@router.get("/documents")
def get_documents(
    category_id: str = Query(""),
    include_subcategories: bool = Query(True),
    project_id: str = Query(""),
    include_expired: bool = Query(False),
    q: str = Query(""),
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
    viewer: Viewer = Depends(require_viewer),
) -> Dict[str, Any]:
    return _call(
        service.list_documents,
        viewer,
        category_id=category_id,
        include_subcategories=include_subcategories,
        project_id=project_id,
        include_expired=include_expired,
        q=q,
        limit=limit,
        offset=offset,
    )


@router.get("/documents/{document_id}")
def get_document(
    document_id: str,
    include_text: bool = Query(True),
    viewer: Viewer = Depends(require_viewer),
) -> Dict[str, Any]:
    return _call(service.get_document, viewer, document_id, include_text=include_text)


@router.patch("/documents/{document_id}")
def patch_document(
    document_id: str,
    payload: DocumentUpdate,
    viewer: Viewer = Depends(require_viewer),
) -> Dict[str, Any]:
    return _call(
        service.update_document,
        viewer,
        document_id,
        title=payload.title,
        category_id=payload.category_id,
        visibility=payload.visibility,
        expires_at=payload.expires_at,
        clear_expiry=payload.clear_expiry,
        expiry_notice_days=payload.expiry_notice_days,
        tags=payload.tags,
        description=payload.description,
        project_id=payload.project_id,
        source_ref=payload.source_ref,
    )


@router.delete("/documents/{document_id}")
def remove_document(
    document_id: str, viewer: Viewer = Depends(require_viewer)
) -> Dict[str, Any]:
    return _call(service.delete_document, viewer, document_id)


@router.get("/documents/{document_id}/versions/{version}")
def get_document_version(
    document_id: str,
    version: int,
    viewer: Viewer = Depends(require_viewer),
) -> Dict[str, Any]:
    return _call(service.get_version, viewer, document_id, version)


@router.get("/documents/{document_id}/diff")
def diff_document_versions(
    document_id: str,
    from_version: int = Query(..., ge=1),
    to_version: int = Query(..., ge=1),
    viewer: Viewer = Depends(require_viewer),
) -> Dict[str, Any]:
    return _call(service.diff_versions, viewer, document_id, from_version, to_version)


@router.get("/documents/{document_id}/file")
def download_document(
    document_id: str,
    version: Optional[int] = Query(None, ge=1),
    viewer: Viewer = Depends(require_viewer),
):
    path, filename, mime = _call(service.document_file_info, viewer, document_id, version)
    return FileResponse(path=str(path), filename=filename, media_type=mime)


@router.post("/search")
def search_documents(
    payload: SearchRequest, viewer: Viewer = Depends(require_viewer)
) -> Dict[str, Any]:
    return _call(
        service.search,
        viewer,
        payload.query,
        category_id=payload.category_id,
        include_subcategories=payload.include_subcategories,
        project_id=payload.project_id,
        top_k=payload.top_k,
        include_expired=payload.include_expired,
        min_score=payload.min_score,
    )


@router.get("/expiring")
def get_expiring(
    days: int = Query(30, ge=0, le=3650),
    limit: int = Query(100, ge=1, le=200),
    viewer: Viewer = Depends(require_viewer),
) -> Dict[str, Any]:
    return _call(service.expiring_documents, viewer, days=days, limit=limit)


# ---------------------------------------------------------------------------
# 长期记忆（独立库）
# ---------------------------------------------------------------------------


@router.get("/memory")
def get_memories(
    project_id: str = Query(""),
    employee_id: str = Query(""),
    scope: str = Query(""),
    q: str = Query(""),
    include_expired: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    viewer: Viewer = Depends(require_viewer),
) -> Dict[str, Any]:
    return _call(
        memory_service.list_memories,
        viewer,
        project_id=project_id,
        employee_id=employee_id,
        scope=scope,
        q=q,
        include_expired=include_expired,
        limit=limit,
        offset=offset,
    )


@router.post("/memory", status_code=201)
def add_memory(
    payload: MemoryCreate, viewer: Viewer = Depends(require_viewer)
) -> Dict[str, Any]:
    return _call(
        memory_service.create_memory,
        viewer,
        content=payload.content,
        title=payload.title,
        project_id=payload.project_id,
        employee_id=payload.employee_id,
        scope=payload.scope,
        source=payload.source,
        source_ref=payload.source_ref,
        tags=payload.tags,
        confidence=payload.confidence,
        expires_at=payload.expires_at,
    )


@router.get("/memory/views")
def get_memory_views(viewer: Viewer = Depends(require_viewer)) -> Dict[str, Any]:
    return _call(memory_service.memory_views, viewer)


@router.get("/memory/{memory_id}")
def get_memory(memory_id: str, viewer: Viewer = Depends(require_viewer)) -> Dict[str, Any]:
    return _call(memory_service.get_memory, viewer, memory_id)


@router.patch("/memory/{memory_id}")
def patch_memory(
    memory_id: str,
    payload: MemoryUpdate,
    viewer: Viewer = Depends(require_viewer),
) -> Dict[str, Any]:
    return _call(
        memory_service.update_memory,
        viewer,
        memory_id,
        content=payload.content,
        title=payload.title,
        project_id=payload.project_id,
        employee_id=payload.employee_id,
        scope=payload.scope,
        source=payload.source,
        source_ref=payload.source_ref,
        tags=payload.tags,
        confidence=payload.confidence,
        expires_at=payload.expires_at,
        clear_expiry=payload.clear_expiry,
    )


@router.delete("/memory/{memory_id}")
def remove_memory(
    memory_id: str, viewer: Viewer = Depends(require_viewer)
) -> Dict[str, Any]:
    return _call(memory_service.delete_memory, viewer, memory_id)


__all__ = ["router", "require_viewer", "MAX_UPLOAD_BYTES"]
