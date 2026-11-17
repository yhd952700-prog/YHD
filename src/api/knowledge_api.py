"""Knowledge base API endpoints for LiuHao AI OS.

.. deprecated::
    本模块是**未挂进网关主路由**的旧壳（见 ``docs/knowledge-memory-mainline.md``），
    只留给旧测试做回归；不要再往里面加功能。主线是
    ``src/api/routes/knowledge.py``（网关挂载的 ``/v1/knowledge``，真实 RAG 管线），
    前端实际走 ``POST /v1/knowledge/search``（``apps/console/console/src/lib/knowledge.ts``）。

    语义搜索空壳已删除（2026-11-14，A2a 决定）：本模块以前的
    ``POST /{kb_id}/search`` 没有向量模型、没有索引，永远返回
    ``search_unavailable`` 空结果，却长得像个能用的搜索接口。删掉它，
    不要留着空壳假装能用；需要搜索请走主线 ``/v1/knowledge/search``。

RESTful API endpoints for knowledge base management:
- KB management (create, list, get, update, delete)
- Document upload
- Collection management
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, UploadFile, File, Form
from pydantic import BaseModel, Field


router = APIRouter(prefix="/knowledge", tags=["knowledge"])


# =====================================================================
# Models
# =====================================================================


class KBCreate(BaseModel):
    """Request body for creating a knowledge base."""
    name: str = Field(..., min_length=1, max_length=100, description="KB name")
    description: str = Field(default="", max_length=500, description="KB description")
    category: str = Field(default="general", description="KB category")
    embedding_model: str = Field(default="default", description="Embedding model")


class KBUpdate(BaseModel):
    """Request body for updating a knowledge base."""
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    description: Optional[str] = Field(default=None, max_length=500)
    category: Optional[str] = None


class KBResponse(BaseModel):
    """Knowledge base response model."""
    id: str
    name: str
    description: str
    category: str
    embedding_model: str
    document_count: int
    created_at: str
    updated_at: str


class KBListResponse(BaseModel):
    """Paginated KB list response."""
    total: int
    limit: int
    offset: int
    items: List[KBResponse]


class DocumentResponse(BaseModel):
    """Document response model."""
    id: str
    kb_id: str
    title: str
    source: str
    chunk_count: int
    size_bytes: int
    created_at: str


class DocumentListResponse(BaseModel):
    """Paginated document list response."""
    total: int
    limit: int
    offset: int
    items: List[DocumentResponse]


class CollectionCreate(BaseModel):
    """Collection creation request."""
    name: str = Field(..., min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)


class CollectionResponse(BaseModel):
    """Collection response model."""
    id: str
    name: str
    description: str
    kb_ids: List[str]
    created_at: str


# =====================================================================
# In-memory stores (scaffold)
# =====================================================================
# NOTE: these stores are in-memory only and are NOT persisted; contents
# are lost on restart and there is no vector index. Documents recorded
# here are metadata only — see upload_document / semantic_search.

_CHUNK_SIZE_CHARS = 1000


_knowledge_bases: Dict[str, Dict[str, Any]] = {}
_documents: Dict[str, Dict[str, Any]] = {}
_collections: Dict[str, Dict[str, Any]] = {}


def _get_kb_or_404(kb_id: str) -> Dict[str, Any]:
    """Get KB by ID or raise 404."""
    kb = _knowledge_bases.get(kb_id)
    if not kb:
        raise HTTPException(status_code=404, detail=f"Knowledge base not found: {kb_id}")
    return kb


# =====================================================================
# KB Management Endpoints
# =====================================================================


@router.get("", response_model=KBListResponse)
async def list_knowledge_bases(
    category: Optional[str] = Query(None, description="Filter by category"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> Dict[str, Any]:
    """List all knowledge bases with pagination.

    Args:
        category: Filter by category.
        limit: Result limit.
        offset: Pagination offset.

    Returns:
        Paginated KB list.
    """
    kbs = list(_knowledge_bases.values())
    if category:
        kbs = [k for k in kbs if k["category"] == category]
    total = len(kbs)
    items = kbs[offset: offset + limit]
    return {"total": total, "limit": limit, "offset": offset, "items": items}


@router.post("", response_model=KBResponse, status_code=201)
async def create_knowledge_base(body: KBCreate) -> Dict[str, Any]:
    """Create a new knowledge base.

    Args:
        body: KB creation request.

    Returns:
        Created KB details.
    """
    kb_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    kb = {
        "id": kb_id,
        "name": body.name,
        "description": body.description,
        "category": body.category,
        "embedding_model": body.embedding_model,
        "document_count": 0,
        "created_at": now,
        "updated_at": now,
    }
    _knowledge_bases[kb_id] = kb
    return kb


@router.get("/{kb_id}", response_model=KBResponse)
async def get_knowledge_base(kb_id: str) -> Dict[str, Any]:
    """Get details of a knowledge base.

    Args:
        kb_id: Knowledge base identifier.

    Returns:
        KB details.

    Raises:
        HTTPException: 404 if not found.
    """
    return _get_kb_or_404(kb_id)


@router.put("/{kb_id}", response_model=KBResponse)
async def update_knowledge_base(kb_id: str, body: KBUpdate) -> Dict[str, Any]:
    """Update a knowledge base.

    Args:
        kb_id: Knowledge base identifier.
        body: Update request.

    Returns:
        Updated KB details.

    Raises:
        HTTPException: 404 if not found.
    """
    kb = _get_kb_or_404(kb_id)
    update_data = body.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        if value is not None:
            kb[key] = value
    kb["updated_at"] = datetime.utcnow().isoformat()
    return kb


@router.delete("/{kb_id}", status_code=204)
async def delete_knowledge_base(kb_id: str) -> None:
    """Delete a knowledge base.

    Args:
        kb_id: Knowledge base identifier.

    Raises:
        HTTPException: 404 if not found.
    """
    if kb_id not in _knowledge_bases:
        raise HTTPException(status_code=404, detail=f"Knowledge base not found: {kb_id}")
    del _knowledge_bases[kb_id]


# =====================================================================
# Document Endpoints
# =====================================================================


@router.get("/{kb_id}/documents", response_model=DocumentListResponse)
async def list_documents(
    kb_id: str,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> Dict[str, Any]:
    """List documents in a knowledge base.

    Args:
        kb_id: Knowledge base identifier.
        limit: Result limit.
        offset: Pagination offset.

    Returns:
        Paginated document list.

    Raises:
        HTTPException: 404 if KB not found.
    """
    _get_kb_or_404(kb_id)
    docs = [d for d in _documents.values() if d["kb_id"] == kb_id]
    total = len(docs)
    items = docs[offset: offset + limit]
    return {"total": total, "limit": limit, "offset": offset, "items": items}


@router.post("/{kb_id}/documents/upload", response_model=DocumentResponse, status_code=201)
async def upload_document(
    kb_id: str,
    file: UploadFile = File(..., description="Document file to upload"),
    title: str = Form(..., description="Document title"),
) -> Dict[str, Any]:
    """Upload a document to a knowledge base.

    The uploaded bytes are actually read: ``size_bytes`` is their real
    length and ``chunk_count`` is the number of ``_CHUNK_SIZE_CHARS``
    text chunks the content splits into. The document content itself
    is NOT persisted or indexed — there is no chunking/indexing
    pipeline or vector store wired in this module, so the record kept
    here is metadata only.

    Args:
        kb_id: Knowledge base identifier.
        file: Uploaded file.
        title: Document title.

    Returns:
        Uploaded document details.

    Raises:
        HTTPException: 404 if KB not found.
    """
    kb = _get_kb_or_404(kb_id)
    content = await file.read()
    text = content.decode("utf-8", errors="replace")
    chunk_count = (
        (len(text) + _CHUNK_SIZE_CHARS - 1) // _CHUNK_SIZE_CHARS
        if text
        else 0
    )
    doc_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    doc = {
        "id": doc_id,
        "kb_id": kb_id,
        "title": title,
        "source": file.filename or "unknown",
        "chunk_count": chunk_count,
        "size_bytes": len(content),
        "created_at": now,
    }
    _documents[doc_id] = doc
    kb["document_count"] += 1
    kb["updated_at"] = now
    return doc


@router.delete("/{kb_id}/documents/{doc_id}", status_code=204)
async def delete_document(kb_id: str, doc_id: str) -> None:
    """Delete a document from a knowledge base.

    Args:
        kb_id: Knowledge base identifier.
        doc_id: Document identifier.

    Raises:
        HTTPException: 404 if KB or document not found.
    """
    kb = _get_kb_or_404(kb_id)
    if doc_id not in _documents or _documents[doc_id]["kb_id"] != kb_id:
        raise HTTPException(status_code=404, detail=f"Document not found: {doc_id}")
    del _documents[doc_id]
    kb["document_count"] = max(0, kb["document_count"] - 1)
    kb["updated_at"] = datetime.utcnow().isoformat()


# =====================================================================
# Semantic Search Endpoint — 已删除（见模块头 docstring 与
# docs/knowledge-memory-mainline.md）。以前这里是个空壳：没有向量模型、
# 没有索引，永远返回 search_unavailable 空结果，却长得像能用的搜索。
# 前端实际走主线 POST /v1/knowledge/search（真实 RAG 管线），删掉空壳
# 不影响前端；不要把空壳加回来。
# =====================================================================


# =====================================================================
# Collection Management Endpoints
# =====================================================================


@router.get("/collections/list")
async def list_collections(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> Dict[str, Any]:
    """List all collections.

    Args:
        limit: Result limit.
        offset: Pagination offset.

    Returns:
        Paginated collection list.
    """
    items = list(_collections.values())
    total = len(items)
    items = items[offset: offset + limit]
    return {"total": total, "limit": limit, "offset": offset, "items": items}


@router.post("/collections", response_model=CollectionResponse, status_code=201)
async def create_collection(body: CollectionCreate) -> Dict[str, Any]:
    """Create a new collection of knowledge bases.

    Args:
        body: Collection creation request.

    Returns:
        Created collection details.
    """
    col_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    collection = {
        "id": col_id,
        "name": body.name,
        "description": body.description,
        "kb_ids": [],
        "created_at": now,
    }
    _collections[col_id] = collection
    return collection


@router.post("/collections/{collection_id}/kbs/{kb_id}")
async def add_kb_to_collection(collection_id: str, kb_id: str) -> Dict[str, Any]:
    """Add a knowledge base to a collection.

    Args:
        collection_id: Collection identifier.
        kb_id: Knowledge base identifier.

    Returns:
        Operation status.

    Raises:
        HTTPException: 404 if collection or KB not found.
    """
    if collection_id not in _collections:
        raise HTTPException(status_code=404, detail=f"Collection not found: {collection_id}")
    _get_kb_or_404(kb_id)
    if kb_id not in _collections[collection_id]["kb_ids"]:
        _collections[collection_id]["kb_ids"].append(kb_id)
    return {"status": "added", "collection_id": collection_id, "kb_id": kb_id}


@router.delete("/collections/{collection_id}/kbs/{kb_id}", status_code=204)
async def remove_kb_from_collection(collection_id: str, kb_id: str) -> None:
    """Remove a knowledge base from a collection.

    Args:
        collection_id: Collection identifier.
        kb_id: Knowledge base identifier.

    Raises:
        HTTPException: 404 if collection not found.
    """
    if collection_id not in _collections:
        raise HTTPException(status_code=404, detail=f"Collection not found: {collection_id}")
    if kb_id in _collections[collection_id]["kb_ids"]:
        _collections[collection_id]["kb_ids"].remove(kb_id)
