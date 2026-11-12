"""LEGACY (not mounted by the gateway): Task management API endpoints for LiuHao AI OS.

RESTful API endpoints for task management:
- Task CRUD operations
- Task execution
- Task results
- Task status streaming (SSE endpoint)
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field


router = APIRouter(prefix="/tasks", tags=["tasks"])


# =====================================================================
# Enums & Models
# =====================================================================


class TaskStatus(str, Enum):
    """Task status values."""
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskPriority(str, Enum):
    """Task priority levels."""
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class TaskCreate(BaseModel):
    """Request body for creating a task."""
    title: str = Field(..., min_length=1, max_length=200, description="Task title")
    description: str = Field(default="", max_length=2000, description="Task description")
    priority: TaskPriority = Field(default=TaskPriority.NORMAL, description="Task priority")
    assigned_agent: Optional[str] = Field(default=None, description="Assigned agent ID")
    parameters: Optional[Dict[str, Any]] = Field(default=None, description="Task parameters")
    scheduled_at: Optional[str] = Field(default=None, description="Scheduled execution time")
    # 截止时间（ISO 8601 字符串，如 "2026-12-31T23:59:59"）。B组第二轮补：
    # 此前 Task 只有 priority/progress，没有截止时间字段。
    due_date: Optional[str] = Field(default=None, description="Due date (ISO 8601, optional)")


class TaskUpdate(BaseModel):
    """Request body for updating a task."""
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=2000)
    priority: Optional[TaskPriority] = None
    status: Optional[TaskStatus] = None
    assigned_agent: Optional[str] = None
    due_date: Optional[str] = Field(default=None, description="Due date (ISO 8601, optional)")


class TaskResponse(BaseModel):
    """Task response model."""
    id: str
    title: str
    description: str
    status: str
    priority: str
    assigned_agent: Optional[str]
    parameters: Dict[str, Any]
    created_at: str
    updated_at: str
    started_at: Optional[str]
    completed_at: Optional[str]
    progress_pct: float
    # 截止时间：创建时未给则为 None。progress_pct 目前恒为 0.0 —— 执行器
    # 未接线（execute 端点返回 501），没有真实进度可报，不编假进度。
    due_date: Optional[str] = None


class TaskListResponse(BaseModel):
    """Paginated task list response."""
    total: int
    limit: int
    offset: int
    items: List[TaskResponse]


class TaskExecuteRequest(BaseModel):
    """Task execution request."""
    agent_id: Optional[str] = Field(default=None, description="Agent to execute with")
    parameters: Optional[Dict[str, Any]] = None


class TaskExecuteResponse(BaseModel):
    """Task execution response."""
    task_id: str
    execution_id: str
    status: str
    started_at: str


class TaskResultResponse(BaseModel):
    """Task result response."""
    task_id: str
    status: str
    result: Dict[str, Any]
    completed_at: str
    duration_ms: Optional[float] = None
    error: Optional[str] = None
    note: Optional[str] = None


# =====================================================================
# In-memory store (scaffold)
# =====================================================================


_tasks: Dict[str, Dict[str, Any]] = {}


def _get_task_or_404(task_id: str) -> Dict[str, Any]:
    """Get task by ID or raise 404."""
    task = _tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    return task


def _duration_ms(started_at: Optional[str], completed_at: Optional[str]) -> Optional[float]:
    """Real duration: milliseconds between start and completion.

    Returns None when either timestamp is missing or unparseable
    instead of a fabricated constant.
    """
    if not started_at or not completed_at:
        return None
    try:
        started = datetime.fromisoformat(started_at)
        completed = datetime.fromisoformat(completed_at)
    except ValueError:
        return None
    return max(0.0, round((completed - started).total_seconds() * 1000.0, 2))


# =====================================================================
# Task CRUD Endpoints
# =====================================================================


@router.get("", response_model=TaskListResponse)
async def list_tasks(
    status: Optional[TaskStatus] = Query(None, description="Filter by status"),
    priority: Optional[TaskPriority] = Query(None, description="Filter by priority"),
    assigned_agent: Optional[str] = Query(None, description="Filter by assigned agent"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    sort_by: str = Query("created_at"),
    sort_order: str = Query("desc", regex="^(asc|desc)$"),
) -> Dict[str, Any]:
    """List tasks with pagination and filtering.

    Args:
        status: Filter by task status.
        priority: Filter by priority.
        assigned_agent: Filter by assigned agent.
        limit: Result limit.
        offset: Pagination offset.
        sort_by: Sort field.
        sort_order: Sort direction.

    Returns:
        Paginated task list.
    """
    tasks = list(_tasks.values())
    if status:
        tasks = [t for t in tasks if t["status"] == status.value]
    if priority:
        tasks = [t for t in tasks if t["priority"] == priority.value]
    if assigned_agent:
        tasks = [t for t in tasks if t.get("assigned_agent") == assigned_agent]

    reverse = sort_order == "desc"
    tasks.sort(key=lambda t: t.get(sort_by, ""), reverse=reverse)

    total = len(tasks)
    items = tasks[offset: offset + limit]
    return {"total": total, "limit": limit, "offset": offset, "items": items}


@router.post("", response_model=TaskResponse, status_code=201)
async def create_task(body: TaskCreate) -> Dict[str, Any]:
    """Create a new task.

    Args:
        body: Task creation request.

    Returns:
        Created task details.
    """
    task_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    task = {
        "id": task_id,
        "title": body.title,
        "description": body.description,
        "status": TaskStatus.PENDING.value,
        "priority": body.priority.value,
        "assigned_agent": body.assigned_agent,
        "parameters": body.parameters or {},
        "created_at": now,
        "updated_at": now,
        "started_at": None,
        "completed_at": None,
        "progress_pct": 0.0,
        "due_date": body.due_date,
    }
    _tasks[task_id] = task
    return task


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(task_id: str) -> Dict[str, Any]:
    """Get task details by ID.

    Args:
        task_id: Task identifier.

    Returns:
        Task details.

    Raises:
        HTTPException: 404 if not found.
    """
    return _get_task_or_404(task_id)


@router.put("/{task_id}", response_model=TaskResponse)
async def update_task(task_id: str, body: TaskUpdate) -> Dict[str, Any]:
    """Update a task.

    Args:
        task_id: Task identifier.
        body: Update request.

    Returns:
        Updated task details.

    Raises:
        HTTPException: 404 if not found.
    """
    task = _get_task_or_404(task_id)
    update_data = body.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        if value is not None:
            if isinstance(value, Enum):
                task[key] = value.value
            else:
                task[key] = value
    task["updated_at"] = datetime.utcnow().isoformat()
    return task


@router.delete("/{task_id}", status_code=204)
async def delete_task(task_id: str) -> None:
    """Delete a task.

    Args:
        task_id: Task identifier.

    Raises:
        HTTPException: 404 if not found.
    """
    if task_id not in _tasks:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    del _tasks[task_id]


# =====================================================================
# Task Execution Endpoints
# =====================================================================


@router.post("/{task_id}/execute", response_model=TaskExecuteResponse, status_code=202)
async def execute_task(task_id: str, body: TaskExecuteRequest) -> Dict[str, Any]:
    """Execute a task.

    NOT IMPLEMENTED: no task executor is wired to this endpoint, so
    executing would only pretend to run. Returns HTTP 501 and leaves
    the task untouched (still PENDING) instead of marking it RUNNING
    with a fabricated progress value.

    Args:
        task_id: Task identifier.
        body: Execution request.

    Returns:
        Never returns normally while no executor is wired.

    Raises:
        HTTPException: 404 if not found, 501 if no executor is wired.
    """
    _get_task_or_404(task_id)
    raise HTTPException(
        status_code=501,
        detail=(
            "Task execution is not implemented: no executor is wired "
            "to this endpoint, so the task was not started."
        ),
    )


@router.get("/{task_id}/result", response_model=TaskResultResponse)
async def get_task_result(task_id: str) -> Dict[str, Any]:
    """Get the result of a completed task.

    duration_ms is computed from the task record's started_at /
    completed_at timestamps; it is null (with a note) when the
    record has no usable timestamps.

    Args:
        task_id: Task identifier.

    Returns:
        Task result data.

    Raises:
        HTTPException: 404 if not found.
    """
    task = _get_task_or_404(task_id)
    if task["status"] != TaskStatus.COMPLETED.value:
        return {
            "task_id": task_id,
            "status": task["status"],
            "result": {},
            "completed_at": task.get("completed_at") or "",
            "duration_ms": None,
            "error": None if task["status"] != TaskStatus.FAILED.value else "Task failed",
            "note": "Duration is not available: the task has not completed.",
        }
    duration_ms = _duration_ms(task.get("started_at"), task.get("completed_at"))
    return {
        "task_id": task_id,
        "status": task["status"],
        "result": {"output": "Task completed successfully", "details": task.get("parameters", {})},
        "completed_at": task.get("completed_at") or "",
        "duration_ms": duration_ms,
        "error": None,
        "note": (
            None
            if duration_ms is not None
            else "Duration is not available: the task record has no usable started_at/completed_at timestamps."
        ),
    }


@router.post("/{task_id}/cancel")
async def cancel_task(task_id: str) -> Dict[str, Any]:
    """Cancel a running task.

    Args:
        task_id: Task identifier.

    Returns:
        Cancellation status.

    Raises:
        HTTPException: 404 if not found.
    """
    task = _get_task_or_404(task_id)
    if task["status"] in (TaskStatus.RUNNING.value, TaskStatus.QUEUED.value):
        task["status"] = TaskStatus.CANCELLED.value
        task["updated_at"] = datetime.utcnow().isoformat()
    return {
        "task_id": task_id,
        "status": task["status"],
        "cancelled_at": datetime.utcnow().isoformat(),
    }


# =====================================================================
# Task Status Streaming (SSE endpoint description)
# =====================================================================


@router.get("/{task_id}/stream")
async def stream_task_status(task_id: str) -> Dict[str, Any]:
    """Stream task status updates (SSE endpoint info).

    This endpoint describes the SSE streaming endpoint that would provide
    real-time task status updates. In a full implementation, this would
    return a Server-Sent Events stream.

    Args:
        task_id: Task identifier.

    Returns:
        Current task status (placeholder for SSE stream).

    Raises:
        HTTPException: 404 if not found.
    """
    task = _get_task_or_404(task_id)
    return {
        "task_id": task_id,
        "status": task["status"],
        "progress_pct": task.get("progress_pct", 0),
        "stream_endpoint": f"/tasks/{task_id}/stream/sse",
        "events": ["status_change", "progress_update", "completion"],
    }


@router.get("/{task_id}/status")
async def get_task_status(task_id: str) -> Dict[str, Any]:
    """Get current status of a task.

    Args:
        task_id: Task identifier.

    Returns:
        Task status summary.

    Raises:
        HTTPException: 404 if not found.
    """
    task = _get_task_or_404(task_id)
    return {
        "task_id": task_id,
        "status": task["status"],
        "priority": task["priority"],
        "progress_pct": task.get("progress_pct", 0),
        "due_date": task.get("due_date"),
        "assigned_agent": task.get("assigned_agent"),
        "updated_at": task["updated_at"],
    }
