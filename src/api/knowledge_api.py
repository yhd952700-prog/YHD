"""Knowledge base API endpoints for LiuHao AI OS.

RESTful API endpoints for knowledge base management:
- KB management (create, list, get, update, delete)
- Document upload
- Semantic search
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


class SearchResponse(BaseModel):
    """Semantic search response."""
    query: str
    kb_id: str
    total: int
    limit: int
    results: List[Dict[str, Any]]


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
    items = kbs[offset : offset + limit]
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
    items = docs[offset : offset + limit]
    return {"total": total, "limit": limit, "offset": offset, "items": items}


@router.post("/{kb_id}/documents/upload", response_model=DocumentResponse, status_code=201)
async def upload_document(
    kb_id: str,
    file: UploadFile = File(..., description="Document file to upload"),
    title: str = Form(..., description="Document title"),
) -> Dict[str, Any]:
    """Upload a document to a knowledge base.

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
    doc_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    doc = {
        "id": doc_id,
        "kb_id": kb_id,
        "title": title,
        "source": file.filename or "unknown",
        "chunk_count": 5,  # placeholder
        "size_bytes": file.size or 0,
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
# Semantic Search Endpoint
# =====================================================================


@router.post("/{kb_id}/search", response_model=SearchResponse)
async def semantic_search(
    kb_id: str,
    query: str = Query(..., min_length=1, description="Search query"),
    limit: int = Query(10, ge=1, le=50, description="Max results"),
    threshold: float = Query(0.7, ge=0.0, le=1.0, description="Similarity threshold"),
) -> Dict[str, Any]:
    """Perform semantic search on a knowledge base.

    Args:
        kb_id: Knowledge base identifier.
        query: Search query.
        limit: Maximum results.
        threshold: Similarity threshold.

    Returns:
        Search results with relevance scores.

    Raises:
        HTTPException: 404 if KB not found.
    """
    _get_kb_or_404(kb_id)
    docs = [d for d in _documents.values() if d["kb_id"] == kb_id]
    results = []
    for i, doc in enumerate(docs[:limit]):
        score = 0.95 - (i * 0.05)  # placeholder scores
        if score >= threshold:
            results.append({
                "document_id": doc["id"],
                "title": doc["title"],
                "content": f"Relevant chunk from {doc['title']}",
                "similarity_score": round(score, 4),
                "chunk_index": 0,
            })
    return {
        "query": query,
        "kb_id": kb_id,
        "total": len(results),
        "limit": limit,
        "results": results,
    }


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
    items = items[offset : offset + limit]
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
