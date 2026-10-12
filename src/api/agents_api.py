"""Agent management API endpoints for LiuHao AI OS.

RESTful API endpoints for agent management:
- GET /agents - List agents with pagination, filtering, sorting
- POST /agents - Create a new agent
- GET /agents/{id} - Get agent details
- PUT /agents/{id} - Update agent
- DELETE /agents/{id} - Delete agent
- POST /agents/{id}/execute - Execute agent with a task
- GET /agents/{id}/status - Get agent runtime status
- POST /agents/{id}/stop - Stop a running agent
- GET /agents/{id}/metrics - Get agent metrics
- GET /agents/{id}/logs - Get agent logs
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field


router = APIRouter(prefix="/agents", tags=["agents"])


# =====================================================================
# Enums & Models
# =====================================================================


class AgentStatus(str, Enum):
    """Agent runtime status."""
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    ERROR = "error"


class AgentRole(str, Enum):
    """Agent roles."""
    MANAGER = "manager"
    RESEARCHER = "researcher"
    ANALYST = "analyst"
    CRITIC = "critic"
    CREATOR = "creator"
    EXECUTOR = "executor"
    QA = "qa"
    PLANNER = "planner"
    GENERAL = "general"


class AgentCreate(BaseModel):
    """Request body for creating an agent."""
    name: str = Field(..., min_length=1, max_length=100, description="Agent display name")
    role: AgentRole = Field(..., description="Agent role specialization")
    description: Optional[str] = Field(default="", max_length=500, description="Agent description")
    config: Optional[Dict[str, Any]] = Field(default=None, description="Agent configuration")


class AgentUpdate(BaseModel):
    """Request body for updating an agent."""
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    description: Optional[str] = Field(default=None, max_length=500)
    config: Optional[Dict[str, Any]] = None
    status: Optional[AgentStatus] = None


class AgentResponse(BaseModel):
    """Response model for agent data."""
    id: str
    name: str
    role: str
    status: str
    description: str
    created_at: str
    last_run: Optional[str] = None
    total_tasks: int = 0
    config: Dict[str, Any] = Field(default_factory=dict)


class AgentListResponse(BaseModel):
    """Paginated agent list response."""
    total: int
    limit: int
    offset: int
    items: List[AgentResponse]


class ExecuteRequest(BaseModel):
    """Request body for executing an agent."""
    task: str = Field(..., description="Task description for the agent")
    parameters: Optional[Dict[str, Any]] = Field(default=None, description="Additional parameters")


class ExecuteResponse(BaseModel):
    """Response from agent execution."""
    agent_id: str
    status: str
    task: str
    started_at: str
    execution_id: str


class AgentMetricsResponse(BaseModel):
    """Agent performance metrics response."""
    agent_id: str
    total_tasks: int
    status: str
    avg_response_time_ms: float
    success_rate: float
    tokens_used: int


class AgentLogsResponse(BaseModel):
    """Agent logs response."""
    agent_id: str
    level: str
    limit: int
    entries: List[Dict[str, Any]]


# =====================================================================
# In-memory store (scaffold)
# =====================================================================


_agents: Dict[str, Dict[str, Any]] = {}


def _get_agent_or_404(agent_id: str) -> Dict[str, Any]:
    """Get agent by ID or raise 404.

    Args:
        agent_id: Agent identifier.

    Returns:
        Agent data dict.

    Raises:
        HTTPException: 404 if agent not found.
    """
    agent = _agents.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent not found: {agent_id}")
    return agent


# =====================================================================
# API Endpoints
# =====================================================================


@router.get("", response_model=AgentListResponse)
async def list_agents(
    role: Optional[AgentRole] = Query(None, description="Filter by agent role"),
    status: Optional[AgentStatus] = Query(None, description="Filter by agent status"),
    limit: int = Query(20, ge=1, le=100, description="Result limit"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    sort_by: str = Query("created_at", description="Sort field"),
    sort_order: str = Query("desc", regex="^(asc|desc)$", description="Sort order"),
) -> Dict[str, Any]:
    """List all agents with pagination, filtering, and sorting.

    Args:
        role: Filter by agent role.
        status: Filter by agent status.
        limit: Maximum number of results.
        offset: Pagination offset.
        sort_by: Field to sort by.
        sort_order: Sort direction.

    Returns:
        Paginated list of agents.
    """
    agents = list(_agents.values())
    if role:
        agents = [a for a in agents if a["role"] == role.value]
    if status:
        agents = [a for a in agents if a["status"] == status.value]

    # Sort
    reverse = sort_order == "desc"
    agents.sort(key=lambda a: a.get(sort_by, ""), reverse=reverse)

    total = len(agents)
    items = agents[offset : offset + limit]

    return {"total": total, "limit": limit, "offset": offset, "items": items}


@router.post("", response_model=AgentResponse, status_code=201)
async def create_agent(body: AgentCreate) -> Dict[str, Any]:
    """Create a new agent.

    Args:
        body: Agent creation request body.

    Returns:
        Created agent details.
    """
    agent_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    agent = {
        "id": agent_id,
        "name": body.name,
        "role": body.role.value,
        "status": AgentStatus.IDLE.value,
        "description": body.description or "",
        "created_at": now,
        "last_run": None,
        "total_tasks": 0,
        "config": body.config or {},
    }
    _agents[agent_id] = agent
    return agent


@router.get("/{agent_id}", response_model=AgentResponse)
async def get_agent(agent_id: str) -> Dict[str, Any]:
    """Get details for a specific agent.

    Args:
        agent_id: Agent identifier.

    Returns:
        Agent details.

    Raises:
        HTTPException: 404 if agent not found.
    """
    return _get_agent_or_404(agent_id)


@router.put("/{agent_id}", response_model=AgentResponse)
async def update_agent(agent_id: str, body: AgentUpdate) -> Dict[str, Any]:
    """Update an existing agent.

    Args:
        agent_id: Agent identifier.
        body: Update request body.

    Returns:
        Updated agent details.

    Raises:
        HTTPException: 404 if agent not found.
    """
    agent = _get_agent_or_404(agent_id)
    update_data = body.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        if value is not None:
            if key == "status":
                agent[key] = value.value
            else:
                agent[key] = value
    return agent


@router.delete("/{agent_id}", status_code=204)
async def delete_agent(agent_id: str) -> None:
    """Delete an agent.

    Args:
        agent_id: Agent identifier.

    Raises:
        HTTPException: 404 if agent not found.
    """
    if agent_id not in _agents:
        raise HTTPException(status_code=404, detail=f"Agent not found: {agent_id}")
    del _agents[agent_id]


@router.post("/{agent_id}/execute", response_model=ExecuteResponse, status_code=202)
async def execute_agent(agent_id: str, body: ExecuteRequest) -> Dict[str, Any]:
    """Execute an agent with a task.

    Args:
        agent_id: Agent identifier.
        body: Execution request body.

    Returns:
        Execution status and details.

    Raises:
        HTTPException: 404 if agent not found.
    """
    agent = _get_agent_or_404(agent_id)
    now = datetime.utcnow().isoformat()
    agent["status"] = AgentStatus.RUNNING.value
    agent["last_run"] = now
    agent["total_tasks"] = agent.get("total_tasks", 0) + 1
    execution_id = str(uuid.uuid4())
    return {
        "agent_id": agent_id,
        "status": AgentStatus.RUNNING.value,
        "task": body.task,
        "started_at": now,
        "execution_id": execution_id,
    }


@router.get("/{agent_id}/status")
async def get_agent_status(agent_id: str) -> Dict[str, Any]:
    """Get runtime status of an agent.

    Args:
        agent_id: Agent identifier.

    Returns:
        Agent status details.

    Raises:
        HTTPException: 404 if agent not found.
    """
    agent = _get_agent_or_404(agent_id)
    return {
        "agent_id": agent_id,
        "status": agent["status"],
        "total_tasks": agent.get("total_tasks", 0),
        "last_run": agent.get("last_run"),
        "role": agent["role"],
    }


@router.post("/{agent_id}/stop")
async def stop_agent(agent_id: str) -> Dict[str, Any]:
    """Stop a running agent.

    Args:
        agent_id: Agent identifier.

    Returns:
        Stop confirmation.

    Raises:
        HTTPException: 404 if agent not found.
    """
    agent = _get_agent_or_404(agent_id)
    agent["status"] = AgentStatus.STOPPED.value
    return {
        "agent_id": agent_id,
        "status": AgentStatus.STOPPED.value,
        "stopped_at": datetime.utcnow().isoformat(),
    }


@router.get("/{agent_id}/metrics", response_model=AgentMetricsResponse)
async def get_agent_metrics(agent_id: str) -> Dict[str, Any]:
    """Get performance metrics for an agent.

    Args:
        agent_id: Agent identifier.

    Returns:
        Agent metrics data.

    Raises:
        HTTPException: 404 if agent not found.
    """
    agent = _get_agent_or_404(agent_id)
    return {
        "agent_id": agent_id,
        "total_tasks": agent.get("total_tasks", 0),
        "status": agent["status"],
        "avg_response_time_ms": 450.0,
        "success_rate": 0.97,
        "tokens_used": 12500,
    }


@router.get("/{agent_id}/logs", response_model=AgentLogsResponse)
async def get_agent_logs(
    agent_id: str,
    limit: int = Query(50, ge=1, le=500, description="Max log entries"),
    level: str = Query("info", regex="^(debug|info|warning|error)$", description="Log level"),
) -> Dict[str, Any]:
    """Get logs for an agent.

    Args:
        agent_id: Agent identifier.
        limit: Maximum log entries.
        level: Minimum log level.

    Returns:
        Agent log entries.

    Raises:
        HTTPException: 404 if agent not found.
    """
    _get_agent_or_404(agent_id)
    return {
        "agent_id": agent_id,
        "level": level,
        "limit": limit,
        "entries": [
            {
                "timestamp": datetime.utcnow().isoformat(),
                "level": level.upper(),
                "message": f"Agent {agent_id} log entry {i}",
            }
            for i in range(min(limit, 5))
        ],
    }
