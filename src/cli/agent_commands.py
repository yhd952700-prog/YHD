"""Agent management CLI commands for LiuHao AI OS.

Provides command-line interface for managing AI agents including:
- agent list, agent create, agent get, agent delete
- agent run, agent status, agent stop
- agent metrics, agent logs
"""

from __future__ import annotations

import argparse
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class AgentStatus(str, Enum):
    """Agent runtime status."""
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    ERROR = "error"


@dataclass
class AgentRecord:
    """An agent record stored in the CLI registry."""
    agent_id: str
    name: str
    role: str
    status: AgentStatus = AgentStatus.IDLE
    description: str = ""
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    last_run: Optional[str] = None
    total_tasks: int = 0
    config: Dict[str, Any] = field(default_factory=dict)


class AgentManager:
    """In-memory agent manager for CLI operations.

    In a production deployment this would delegate to the agent runtime
    service. For the CLI scaffold we maintain an in-memory registry so
    commands are independently testable.
    """

    def __init__(self) -> None:
        self._agents: Dict[str, AgentRecord] = {}

    def list_agents(
        self,
        role: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """List agents with optional filtering.

        Args:
            role: Filter by agent role.
            status: Filter by agent status.
            limit: Maximum number of results.
            offset: Pagination offset.

        Returns:
            Dict with total count and agent list.
        """
        agents = list(self._agents.values())
        if role:
            agents = [a for a in agents if a.role == role]
        if status:
            agents = [a for a in agents if a.status.value == status]
        total = len(agents)
        agents = agents[offset : offset + limit]
        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "items": [
                {
                    "id": a.agent_id,
                    "name": a.name,
                    "role": a.role,
                    "status": a.status.value,
                    "description": a.description,
                    "created_at": a.created_at,
                    "last_run": a.last_run,
                    "total_tasks": a.total_tasks,
                }
                for a in agents
            ],
        }

    def create_agent(
        self,
        name: str,
        role: str,
        description: str = "",
        config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create a new agent.

        Args:
            name: Agent display name.
            role: Agent role (e.g. researcher, analyst).
            description: Optional description.
            config: Optional agent configuration.

        Returns:
            Dict with created agent details.
        """
        agent_id = str(uuid.uuid4())
        agent = AgentRecord(
            agent_id=agent_id,
            name=name,
            role=role,
            description=description,
            config=config or {},
        )
        self._agents[agent_id] = agent
        return {
            "id": agent.agent_id,
            "name": agent.name,
            "role": agent.role,
            "status": agent.status.value,
            "description": agent.description,
            "created_at": agent.created_at,
            "config": agent.config,
        }

    def get_agent(self, agent_id: str) -> Dict[str, Any]:
        """Get details for a specific agent.

        Args:
            agent_id: The agent identifier.

        Returns:
            Dict with agent details.

        Raises:
            ValueError: If agent is not found.
        """
        agent = self._agents.get(agent_id)
        if not agent:
            raise ValueError(f"Agent not found: {agent_id}")
        return {
            "id": agent.agent_id,
            "name": agent.name,
            "role": agent.role,
            "status": agent.status.value,
            "description": agent.description,
            "created_at": agent.created_at,
            "last_run": agent.last_run,
            "total_tasks": agent.total_tasks,
            "config": agent.config,
        }

    def delete_agent(self, agent_id: str) -> Dict[str, Any]:
        """Delete an agent.

        Args:
            agent_id: The agent identifier.

        Returns:
            Dict confirming deletion.

        Raises:
            ValueError: If agent is not found.
        """
        if agent_id not in self._agents:
            raise ValueError(f"Agent not found: {agent_id}")
        del self._agents[agent_id]
        return {"status": "deleted", "agent_id": agent_id}

    def run_agent(self, agent_id: str, task: str = "") -> Dict[str, Any]:
        """Start / run an agent with an optional task.

        Args:
            agent_id: The agent identifier.
            task: Optional task description.

        Returns:
            Dict with execution status.

        Raises:
            ValueError: If agent is not found.
        """
        agent = self._agents.get(agent_id)
        if not agent:
            raise ValueError(f"Agent not found: {agent_id}")
        agent.status = AgentStatus.RUNNING
        agent.last_run = datetime.utcnow().isoformat()
        agent.total_tasks += 1
        return {
            "agent_id": agent_id,
            "status": agent.status.value,
            "task": task,
            "started_at": agent.last_run,
        }

    def agent_status(self, agent_id: str) -> Dict[str, Any]:
        """Get runtime status of an agent.

        Args:
            agent_id: The agent identifier.

        Returns:
            Dict with status details.

        Raises:
            ValueError: If agent is not found.
        """
        agent = self._agents.get(agent_id)
        if not agent:
            raise ValueError(f"Agent not found: {agent_id}")
        return {
            "agent_id": agent_id,
            "status": agent.status.value,
            "total_tasks": agent.total_tasks,
            "last_run": agent.last_run,
        }

    def stop_agent(self, agent_id: str) -> Dict[str, Any]:
        """Stop a running agent.

        Args:
            agent_id: The agent identifier.

        Returns:
            Dict confirming stop.

        Raises:
            ValueError: If agent is not found.
        """
        agent = self._agents.get(agent_id)
        if not agent:
            raise ValueError(f"Agent not found: {agent_id}")
        agent.status = AgentStatus.STOPPED
        return {
            "agent_id": agent_id,
            "status": agent.status.value,
            "stopped_at": datetime.utcnow().isoformat(),
        }

    def agent_metrics(self, agent_id: str) -> Dict[str, Any]:
        """Get performance metrics for an agent.

        Args:
            agent_id: The agent identifier.

        Returns:
            Dict with metrics data.

        Raises:
            ValueError: If agent is not found.
        """
        agent = self._agents.get(agent_id)
        if not agent:
            raise ValueError(f"Agent not found: {agent_id}")
        return {
            "agent_id": agent_id,
            "total_tasks": agent.total_tasks,
            "status": agent.status.value,
            "avg_response_time_ms": 450,  # placeholder
            "success_rate": 0.97,  # placeholder
            "tokens_used": 12500,  # placeholder
        }

    def agent_logs(
        self, agent_id: str, limit: int = 50, level: str = "info"
    ) -> Dict[str, Any]:
        """Get logs for an agent.

        Args:
            agent_id: The agent identifier.
            limit: Maximum number of log entries.
            level: Minimum log level.

        Returns:
            Dict with log entries.

        Raises:
            ValueError: If agent is not found.
        """
        if agent_id not in self._agents:
            raise ValueError(f"Agent not found: {agent_id}")
        return {
            "agent_id": agent_id,
            "level": level,
            "limit": limit,
            "entries": [
                {
                    "timestamp": datetime.utcnow().isoformat(),
                    "level": level.upper(),
                    "message": f"Agent {agent_id} log entry",
                }
            ],
        }


# =====================================================================
# CLI Parser
# =====================================================================


def register_agent_commands(
    subparsers: argparse._SubParsersAction,
) -> None:
    """Register agent subcommands under the given argparse subparser.

    Args:
        subparsers: The parent subparsers action to attach to.
    """
    p_agent = subparsers.add_parser("agent", help="AI agent management")
    agent_sub = p_agent.add_subparsers(dest="agent_command", required=True)

    # agent list
    p_list = agent_sub.add_parser("list", help="List all agents")
    p_list.add_argument("--role", help="Filter by agent role")
    p_list.add_argument("--status", help="Filter by agent status")
    p_list.add_argument("--limit", type=int, default=20, help="Result limit")
    p_list.add_argument("--offset", type=int, default=0, help="Pagination offset")

    # agent create
    p_create = agent_sub.add_parser("create", help="Create a new agent")
    p_create.add_argument("--name", required=True, help="Agent display name")
    p_create.add_argument(
        "--role",
        required=True,
        choices=["manager", "researcher", "analyst", "critic", "creator", "executor", "qa", "planner", "general"],
        help="Agent role",
    )
    p_create.add_argument("--description", default="", help="Agent description")
    p_create.add_argument("--config", help="JSON string with agent config")

    # agent get
    p_get = agent_sub.add_parser("get", help="Get agent details")
    p_get.add_argument("agent_id", help="Agent identifier")

    # agent delete
    p_delete = agent_sub.add_parser("delete", help="Delete an agent")
    p_delete.add_argument("agent_id", help="Agent identifier")
    p_delete.add_argument("--force", action="store_true", help="Force delete")

    # agent run
    p_run = agent_sub.add_parser("run", help="Run / start an agent")
    p_run.add_argument("agent_id", help="Agent identifier")
    p_run.add_argument("--task", default="", help="Task description")

    # agent status
    p_status = agent_sub.add_parser("status", help="Get agent runtime status")
    p_status.add_argument("agent_id", help="Agent identifier")

    # agent stop
    p_stop = agent_sub.add_parser("stop", help="Stop a running agent")
    p_stop.add_argument("agent_id", help="Agent identifier")

    # agent metrics
    p_metrics = agent_sub.add_parser("metrics", help="Get agent performance metrics")
    p_metrics.add_argument("agent_id", help="Agent identifier")

    # agent logs
    p_logs = agent_sub.add_parser("logs", help="Get agent logs")
    p_logs.add_argument("agent_id", help="Agent identifier")
    p_logs.add_argument("--limit", type=int, default=50, help="Max log entries")
    p_logs.add_argument(
        "--level",
        default="info",
        choices=["debug", "info", "warning", "error"],
        help="Minimum log level",
    )


# =====================================================================
# Command Handler
# =====================================================================


def handle_agent_command(args: argparse.Namespace, manager: AgentManager) -> int:
    """Dispatch an agent CLI command to the appropriate handler.

    Args:
        args: Parsed argparse namespace.
        manager: AgentManager instance.

    Returns:
        Exit code (0 for success, 1 for error).
    """
    try:
        if args.agent_command == "list":
            result = manager.list_agents(
                role=args.role,
                status=args.status,
                limit=args.limit,
                offset=args.offset,
            )
        elif args.agent_command == "create":
            config = json.loads(args.config) if args.config else None
            result = manager.create_agent(
                name=args.name,
                role=args.role,
                description=args.description,
                config=config,
            )
        elif args.agent_command == "get":
            result = manager.get_agent(args.agent_id)
        elif args.agent_command == "delete":
            result = manager.delete_agent(args.agent_id)
        elif args.agent_command == "run":
            result = manager.run_agent(args.agent_id, task=args.task)
        elif args.agent_command == "status":
            result = manager.agent_status(args.agent_id)
        elif args.agent_command == "stop":
            result = manager.stop_agent(args.agent_id)
        elif args.agent_command == "metrics":
            result = manager.agent_metrics(args.agent_id)
        elif args.agent_command == "logs":
            result = manager.agent_logs(
                args.agent_id, limit=args.limit, level=args.level
            )
        else:
            print(f"Unknown agent command: {args.agent_command}")
            return 1

        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}, indent=2, ensure_ascii=False))
        return 1
