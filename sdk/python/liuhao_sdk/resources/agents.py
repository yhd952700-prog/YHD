"""
Agent Resource SDK.

Provides programmatic access to agent management, execution,
and metrics on the LiuHao AI OS platform.
"""

from typing import Optional, Dict, Any, List, AsyncIterator
from enum import Enum
from dataclasses import dataclass, field


class AgentStatus(str, Enum):
    """Current lifecycle status of an agent."""
    ACTIVE = "active"
    INACTIVE = "inactive"
    DEPLOYING = "deploying"
    FAILED = "failed"
    DELETED = "deleted"


class AgentRunStatus(str, Enum):
    """Status of an agent task execution."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


@dataclass
class Agent:
    """Represents an agent on the LiuHao platform.

    Attributes:
        id: Unique agent identifier.
        name: Human-readable agent name.
        description: Brief description of the agent's purpose.
        model: The underlying AI model used by the agent.
        status: Current lifecycle status.
        capabilities: List of capability identifiers.
        system_prompt: System prompt template.
        tools: List of configured tool names.
        created_at: ISO 8601 creation timestamp.
        updated_at: ISO 8601 last-update timestamp.
        metadata: Arbitrary key-value metadata.
    """
    id: str
    name: str
    description: str = ""
    model: str = "default"
    status: AgentStatus = AgentStatus.INACTIVE
    capabilities: List[str] = field(default_factory=list)
    system_prompt: str = ""
    tools: List[str] = field(default_factory=list)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentRun:
    """Represents an agent task execution run.

    Attributes:
        id: Unique run identifier.
        agent_id: The agent that executed this run.
        status: Current run status.
        input: The input/query that triggered the run.
        output: The generated output (if completed).
        error: Error message if the run failed.
        started_at: ISO 8601 start timestamp.
        completed_at: ISO 8601 completion timestamp.
        duration_ms: Total execution duration in milliseconds.
        tokens_used: Total tokens consumed.
        metadata: Additional run metadata.
    """
    id: str
    agent_id: str
    status: AgentRunStatus = AgentRunStatus.PENDING
    input: str = ""
    output: Optional[str] = None
    error: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    duration_ms: Optional[int] = None
    tokens_used: Optional[int] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentMetrics:
    """Usage and performance metrics for an agent.

    Attributes:
        agent_id: The agent these metrics belong to.
        total_runs: Total number of executions.
        successful_runs: Number of successful executions.
        failed_runs: Number of failed executions.
        avg_duration_ms: Average execution duration.
        total_tokens: Total tokens consumed.
        period_start: Start of the metrics period.
        period_end: End of the metrics period.
    """
    agent_id: str
    total_runs: int = 0
    successful_runs: int = 0
    failed_runs: int = 0
    avg_duration_ms: float = 0.0
    total_tokens: int = 0
    period_start: Optional[str] = None
    period_end: Optional[str] = None


class AgentsResource:
    """Agent management and execution resource.

    Provides CRUD operations for agents, execution control,
    and metrics retrieval.

    Args:
        client: The parent LiuHaoClient instance used for HTTP requests.
    """

    def __init__(self, client: Any) -> None:
        """Initialize the AgentsResource.

        Args:
            client: The parent SDK client with request capabilities.
        """
        self._client = client
        self._base_path = "/agents"

    # ------------------------------------------------------------------
    # Agent management (CRUD)
    # ------------------------------------------------------------------

    def list(
        self,
        limit: int = 20,
        offset: int = 0,
        status: Optional[AgentStatus] = None,
    ) -> Dict[str, Any]:
        """List agents with pagination.

        Args:
            limit: Maximum number of agents to return.
            offset: Pagination offset.
            status: Optional filter by agent status.

        Returns:
            Dictionary with ``items`` (list of agents) and
            ``total`` (total count).
        """
        params: Dict[str, Any] = {"limit": limit, "offset": offset}
        if status is not None:
            params["status"] = status.value
        return self._client.get(f"{self._base_path}", params=params)

    def get(self, agent_id: str) -> Dict[str, Any]:
        """Retrieve a single agent by ID.

        Args:
            agent_id: Unique identifier of the agent.

        Returns:
            Agent data dictionary.

        Raises:
            NotFoundError: If no agent with the given ID exists.
        """
        return self._client.get(f"{self._base_path}/{agent_id}")

    def create(
        self,
        name: str,
        description: str = "",
        model: str = "default",
        system_prompt: str = "",
        tools: Optional[List[str]] = None,
        capabilities: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create a new agent.

        Args:
            name: Human-readable agent name.
            description: Brief description of the agent's purpose.
            model: The AI model identifier to use.
            system_prompt: System prompt to configure the agent's behavior.
            tools: List of tool names the agent may use.
            capabilities: List of capability identifiers.
            metadata: Arbitrary key-value metadata.

        Returns:
            Created agent data dictionary.

        Raises:
            ValidationError: If required fields are missing or invalid.
        """
        payload: Dict[str, Any] = {
            "name": name,
            "description": description,
            "model": model,
            "system_prompt": system_prompt,
        }
        if tools is not None:
            payload["tools"] = tools
        if capabilities is not None:
            payload["capabilities"] = capabilities
        if metadata is not None:
            payload["metadata"] = metadata
        return self._client.post(f"{self._base_path}", json=payload)

    def update(
        self,
        agent_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        model: Optional[str] = None,
        system_prompt: Optional[str] = None,
        tools: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Update an existing agent.

        Only provided fields will be updated.

        Args:
            agent_id: Unique identifier of the agent to update.
            name: New agent name.
            description: New description.
            model: New model identifier.
            system_prompt: New system prompt.
            tools: New list of tool names.
            metadata: New metadata (replaces existing).

        Returns:
            Updated agent data dictionary.
        """
        payload: Dict[str, Any] = {}
        if name is not None:
            payload["name"] = name
        if description is not None:
            payload["description"] = description
        if model is not None:
            payload["model"] = model
        if system_prompt is not None:
            payload["system_prompt"] = system_prompt
        if tools is not None:
            payload["tools"] = tools
        if metadata is not None:
            payload["metadata"] = metadata
        return self._client.patch(f"{self._base_path}/{agent_id}", json=payload)

    def delete(self, agent_id: str) -> Dict[str, Any]:
        """Delete an agent.

        Args:
            agent_id: Unique identifier of the agent to delete.

        Returns:
            Deletion confirmation dictionary.
        """
        return self._client.delete(f"{self._base_path}/{agent_id}")

    # ------------------------------------------------------------------
    # Agent execution
    # ------------------------------------------------------------------

    def run(
        self,
        agent_id: str,
        input_text: str,
        context: Optional[Dict[str, Any]] = None,
        stream: bool = False,
    ) -> Dict[str, Any]:
        """Execute a task on the agent.

        Args:
            agent_id: Identifier of the agent to run.
            input_text: The input/query for the agent to process.
            context: Optional additional context data.
            stream: Whether to stream the response (placeholder).

        Returns:
            Run result dictionary containing run ID and status.
        """
        payload: Dict[str, Any] = {
            "input": input_text,
            "stream": stream,
        }
        if context is not None:
            payload["context"] = context
        return self._client.post(f"{self._base_path}/{agent_id}/runs", json=payload)

    def get_run_status(self, agent_id: str, run_id: str) -> Dict[str, Any]:
        """Get the status of an agent run.

        Args:
            agent_id: Identifier of the agent.
            run_id: Identifier of the run.

        Returns:
            Run status dictionary.
        """
        return self._client.get(f"{self._base_path}/{agent_id}/runs/{run_id}")

    def cancel_run(self, agent_id: str, run_id: str) -> Dict[str, Any]:
        """Cancel a running agent task.

        Args:
            agent_id: Identifier of the agent.
            run_id: Identifier of the run to cancel.

        Returns:
            Cancellation confirmation dictionary.
        """
        return self._client.post(
            f"{self._base_path}/{agent_id}/runs/{run_id}/cancel"
        )

    def list_runs(
        self,
        agent_id: str,
        limit: int = 20,
        offset: int = 0,
        status: Optional[AgentRunStatus] = None,
    ) -> Dict[str, Any]:
        """List execution runs for an agent.

        Args:
            agent_id: Identifier of the agent.
            limit: Maximum number of runs to return.
            offset: Pagination offset.
            status: Optional filter by run status.

        Returns:
            Dictionary with ``items`` and ``total``.
        """
        params: Dict[str, Any] = {"limit": limit, "offset": offset}
        if status is not None:
            params["status"] = status.value
        return self._client.get(
            f"{self._base_path}/{agent_id}/runs", params=params
        )

    # ------------------------------------------------------------------
    # Agent metrics
    # ------------------------------------------------------------------

    def get_metrics(
        self,
        agent_id: str,
        period_start: Optional[str] = None,
        period_end: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get usage and performance metrics for an agent.

        Args:
            agent_id: Identifier of the agent.
            period_start: ISO 8601 start of the metrics window.
            period_end: ISO 8601 end of the metrics window.

        Returns:
            Metrics data dictionary.
        """
        params: Dict[str, Any] = {}
        if period_start is not None:
            params["period_start"] = period_start
        if period_end is not None:
            params["period_end"] = period_end
        return self._client.get(
            f"{self._base_path}/{agent_id}/metrics", params=params
        )

    # ------------------------------------------------------------------
    # Async support pattern (stubs)
    # ------------------------------------------------------------------

    async def a_list(
        self,
        limit: int = 20,
        offset: int = 0,
        status: Optional[AgentStatus] = None,
    ) -> Dict[str, Any]:
        """Async version of :meth:`list`."""
        return self.list(limit=limit, offset=offset, status=status)

    async def a_get(self, agent_id: str) -> Dict[str, Any]:
        """Async version of :meth:`get`."""
        return self.get(agent_id)

    async def a_create(
        self,
        name: str,
        description: str = "",
        model: str = "default",
        system_prompt: str = "",
        tools: Optional[List[str]] = None,
        capabilities: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Async version of :meth:`create`."""
        return self.create(
            name=name,
            description=description,
            model=model,
            system_prompt=system_prompt,
            tools=tools,
            capabilities=capabilities,
            metadata=metadata,
        )

    async def a_update(
        self,
        agent_id: str,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Async version of :meth:`update`."""
        return self.update(agent_id, **kwargs)

    async def a_delete(self, agent_id: str) -> Dict[str, Any]:
        """Async version of :meth:`delete`."""
        return self.delete(agent_id)

    async def a_run(
        self,
        agent_id: str,
        input_text: str,
        context: Optional[Dict[str, Any]] = None,
        stream: bool = False,
    ) -> Dict[str, Any]:
        """Async version of :meth:`run`."""
        return self.run(agent_id, input_text, context=context, stream=stream)

    async def a_get_run_status(self, agent_id: str, run_id: str) -> Dict[str, Any]:
        """Async version of :meth:`get_run_status`."""
        return self.get_run_status(agent_id, run_id)

    async def a_cancel_run(self, agent_id: str, run_id: str) -> Dict[str, Any]:
        """Async version of :meth:`cancel_run`."""
        return self.cancel_run(agent_id, run_id)

    async def a_get_metrics(
        self,
        agent_id: str,
        period_start: Optional[str] = None,
        period_end: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Async version of :meth:`get_metrics`."""
        return self.get_metrics(agent_id, period_start, period_end)
