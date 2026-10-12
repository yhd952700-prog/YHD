"""Memory API endpoints for LiuHao AI OS.

RESTful API endpoints for memory management:
- CRUD operations on memory entries
- Search endpoint
- Batch operations
- Categories management
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field


router = APIRouter(prefix="/memory", tags=["memory"])


# =====================================================================
# Models
# =====================================================================


class MemoryType(str):
    """Memory type string constants."""
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"
    WORKING = "working"
    META = "meta"


class MemoryCreate(BaseModel):
    """Request body for creating a memory entry."""
    content: str = Field(..., min_length=1, description="Memory content")
    memory_type: str = Field(default="episodic", description="Memory type")
    category: str = Field(default="general", description="Memory category")
    tags: Optional[List[str]] = Field(default=None, description="Tag list")
    importance: float = Field(default=0.5, ge=0.0, le=1.0, description="Importance score")
    metadata: Optional[Dict[str, Any]] = None


class MemoryUpdate(BaseModel):
    """Request body for updating a memory entry."""
    content: Optional[str] = Field(default=None, min_length=1)
    category: Optional[str] = None
    tags: Optional[List[str]] = None
    importance: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    metadata: Optional[Dict[str, Any]] = None


class MemoryResponse(BaseModel):
    """Memory entry response model."""
    id: str
    content: str
    memory_type: str
    category: str
    tags: List[str]
    importance: float
    created_at: str
    last_accessed: Optional[str] = None
    access_count: int
    metadata: Dict[str, Any]


class MemoryListResponse(BaseModel):
    """Paginated memory list response."""
    total: int
    limit: int
    offset: int
    items: List[MemoryResponse]


class MemorySearchResponse(BaseModel):
    """Memory search response."""
    query: str
    total: int
    limit: int
    items: List[MemoryResponse]


class BatchMemoryCreate(BaseModel):
    """Batch memory creation request."""
    items: List[MemoryCreate] = Field(..., min_length=1, max_length=100)


class BatchMemoryResponse(BaseModel):
    """Batch operation response."""
    created: int
    failed: int
    errors: List[str]
    items: List[MemoryResponse]


class MemoryStatsResponse(BaseModel):
    """Memory system statistics response."""
    total_memories: int
    by_type: Dict[str, int]
    by_category: Dict[str, int]
    total_accesses: int
    avg_importance: float


class CategoryListResponse(BaseModel):
    """Memory categories list response."""
    categories: List[str]
    total: int


# =====================================================================
# In-memory store (scaffold)
# =====================================================================


_memories: Dict[str, Dict[str, Any]] = {}


def _get_memory_or_404(memory_id: str) -> Dict[str, Any]:
    """Get memory by ID or raise 404.

    Args:
        memory_id: Memory identifier.

    Returns:
        Memory data dict.

    Raises:
        HTTPException: 404 if not found.
    """
    mem = _memories.get(memory_id)
    if not mem:
        raise HTTPException(status_code=404, detail=f"Memory not found: {memory_id}")
    return mem


# =====================================================================
# API Endpoints
# =====================================================================


@router.get("", response_model=MemoryListResponse)
async def list_memories(
    memory_type: Optional[str] = Query(None, description="Filter by memory type"),
    category: Optional[str] = Query(None, description="Filter by category"),
    tag: Optional[str] = Query(None, description="Filter by tag"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    sort_by: str = Query("created_at"),
    sort_order: str = Query("desc", regex="^(asc|desc)$"),
) -> Dict[str, Any]:
    """List memory entries with pagination and filtering.

    Args:
        memory_type: Filter by memory type.
        category: Filter by category.
        tag: Filter by tag.
        limit: Result limit.
        offset: Pagination offset.
        sort_by: Sort field.
        sort_order: Sort direction.

    Returns:
        Paginated memory list.
    """
    items = list(_memories.values())
    if memory_type:
        items = [m for m in items if m["memory_type"] == memory_type]
    if category:
        items = [m for m in items if m["category"] == category]
    if tag:
        items = [m for m in items if tag in m.get("tags", [])]

    reverse = sort_order == "desc"
    items.sort(key=lambda m: m.get(sort_by, ""), reverse=reverse)

    total = len(items)
    items = items[offset : offset + limit]
    return {"total": total, "limit": limit, "offset": offset, "items": items}


@router.post("", response_model=MemoryResponse, status_code=201)
async def create_memory(body: MemoryCreate) -> Dict[str, Any]:
    """Create a new memory entry.

    Args:
        body: Memory creation request.

    Returns:
        Created memory entry.
    """
    mem_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    memory = {
        "id": mem_id,
        "content": body.content,
        "memory_type": body.memory_type,
        "category": body.category,
        "tags": body.tags or [],
        "importance": body.importance,
        "created_at": now,
        "last_accessed": None,
        "access_count": 0,
        "metadata": body.metadata or {},
    }
    _memories[mem_id] = memory
    return memory


@router.get("/search", response_model=MemorySearchResponse)
async def search_memories(
    query: str = Query(..., min_length=1, description="Search query"),
    memory_type: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    limit: int = Query(10, ge=1, le=100),
) -> Dict[str, Any]:
    """Search memory entries by keyword.

    Args:
        query: Search query string.
        memory_type: Optional type filter.
        category: Optional category filter.
        limit: Maximum results.

    Returns:
        Search results.
    """
    query_lower = query.lower()
    results = []
    for mem in _memories.values():
        if memory_type and mem["memory_type"] != memory_type:
            continue
        if category and mem["category"] != category:
            continue
        if query_lower in mem["content"].lower() or any(
            query_lower in t.lower() for t in mem.get("tags", [])
        ):
            results.append(mem)
    results.sort(key=lambda m: m["importance"], reverse=True)
    results = results[:limit]
    return {"query": query, "total": len(results), "limit": limit, "items": results}


@router.post("/batch", response_model=BatchMemoryResponse, status_code=201)
async def batch_create_memories(body: BatchMemoryCreate) -> Dict[str, Any]:
    """Batch create memory entries.

    Args:
        body: Batch creation request.

    Returns:
        Batch operation result.
    """
    created = []
    errors = []
    for item in body.items:
        try:
            mem_id = str(uuid.uuid4())
            now = datetime.utcnow().isoformat()
            memory = {
                "id": mem_id,
                "content": item.content,
                "memory_type": item.memory_type,
                "category": item.category,
                "tags": item.tags or [],
                "importance": item.importance,
                "created_at": now,
                "last_accessed": None,
                "access_count": 0,
                "metadata": item.metadata or {},
            }
            _memories[mem_id] = memory
            created.append(memory)
        except Exception as exc:
            errors.append(str(exc))
    return {
        "created": len(created),
        "failed": len(errors),
        "errors": errors,
        "items": created,
    }


@router.get("/stats", response_model=MemoryStatsResponse)
async def get_memory_stats() -> Dict[str, Any]:
    """Get memory system statistics.

    Returns:
        Memory statistics.
    """
    by_type: Dict[str, int] = {}
    by_category: Dict[str, int] = {}
    total_accesses = 0
    for mem in _memories.values():
        by_type[mem["memory_type"]] = by_type.get(mem["memory_type"], 0) + 1
        by_category[mem["category"]] = by_category.get(mem["category"], 0) + 1
        total_accesses += mem.get("access_count", 0)
    return {
        "total_memories": len(_memories),
        "by_type": by_type,
        "by_category": by_category,
        "total_accesses": total_accesses,
        "avg_importance": (
            sum(m["importance"] for m in _memories.values()) / len(_memories)
            if _memories
            else 0.0
        ),
    }


@router.get("/categories", response_model=CategoryListResponse)
async def list_categories() -> Dict[str, Any]:
    """List all memory categories.

    Returns:
        List of categories.
    """
    categories = sorted(set(m["category"] for m in _memories.values()))
    return {"categories": categories, "total": len(categories)}


# NOTE: the ``/{memory_id}`` catch-all routes MUST stay below every fixed-path
# route (``/search``, ``/batch``, ``/stats``, ``/categories``). FastAPI matches
# routes in declaration order; declaring ``/{memory_id}`` first makes it swallow
# the fixed paths (``/memory/search`` -> memory_id="search" -> 404).


@router.get("/{memory_id}", response_model=MemoryResponse)
async def get_memory(memory_id: str) -> Dict[str, Any]:
    """Get a memory entry by ID.

    Args:
        memory_id: Memory identifier.

    Returns:
        Memory entry details.

    Raises:
        HTTPException: 404 if not found.
    """
    mem = _get_memory_or_404(memory_id)
    mem["access_count"] += 1
    mem["last_accessed"] = datetime.utcnow().isoformat()
    return mem


@router.put("/{memory_id}", response_model=MemoryResponse)
async def update_memory(memory_id: str, body: MemoryUpdate) -> Dict[str, Any]:
    """Update a memory entry.

    Args:
        memory_id: Memory identifier.
        body: Update request.

    Returns:
        Updated memory entry.

    Raises:
        HTTPException: 404 if not found.
    """
    mem = _get_memory_or_404(memory_id)
    update_data = body.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        if value is not None:
            mem[key] = value
    return mem


@router.delete("/{memory_id}", status_code=204)
async def delete_memory(memory_id: str) -> None:
    """Delete a memory entry.

    Args:
        memory_id: Memory identifier.

    Raises:
        HTTPException: 404 if not found.
    """
    if memory_id not in _memories:
        raise HTTPException(status_code=404, detail=f"Memory not found: {memory_id}")
    del _memories[memory_id]
