"""Solution template CLI commands for LiuHao AI OS.

Provides command-line interface for solution template management:
- solution list, solution info, solution deploy
- solution status, solution configure
- solution metrics
"""

from __future__ import annotations

import argparse
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class SolutionStatus(str, Enum):
    """Solution deployment status."""
    AVAILABLE = "available"
    DEPLOYING = "deploying"
    DEPLOYED = "deployed"
    FAILED = "failed"
    STOPPED = "stopped"
    UPDATING = "updating"


@dataclass
class SolutionTemplate:
    """A solution template definition."""
    solution_id: str
    name: str
    description: str
    category: str
    version: str
    author: str = ""
    tags: List[str] = field(default_factory=list)
    required_agents: List[str] = field(default_factory=list)
    required_plugins: List[str] = field(default_factory=list)
    config_schema: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())


@dataclass
class SolutionDeployment:
    """A deployed solution instance."""
    deployment_id: str
    solution_id: str
    name: str
    status: SolutionStatus = SolutionStatus.DEPLOYED
    config: Dict[str, Any] = field(default_factory=dict)
    deployed_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    last_updated: Optional[str] = None
    metrics: Dict[str, Any] = field(default_factory=dict)


class SolutionManager:
    """In-memory solution manager for CLI operations.

    Provides solution template listing, deployment, and management.
    """

    def __init__(self) -> None:
        self._templates: Dict[str, SolutionTemplate] = {}
        self._deployments: Dict[str, SolutionDeployment] = {}
        self._seed_sample_data()

    def _seed_sample_data(self) -> None:
        """Seed with sample solution data."""
        templates = [
            (
                "sol-customer-support",
                "Customer Support Agent",
                "Automated customer support with AI agents",
                "customer_service",
                "1.2.0",
                "liuhao",
                ["support", "customer", "chat"],
                ["executor", "analyst"],
                ["plugin-slack"],
            ),
            (
                "sol-code-review",
                "Code Review Assistant",
                "Automated code review and quality analysis",
                "development",
                "2.0.0",
                "liuhao",
                ["code-review", "devops", "quality"],
                ["critic", "analyst"],
                ["plugin-github"],
            ),
            (
                "sol-research-assistant",
                "Research Assistant",
                "AI-powered research and literature review",
                "research",
                "1.0.0",
                "liuhao",
                ["research", "analysis", "writing"],
                ["researcher", "analyst", "writer"],
                [],
            ),
            (
                "sol-data-pipeline",
                "Data Pipeline Manager",
                "Automated ETL and data processing pipelines",
                "data",
                "0.9.0",
                "community",
                ["data", "etl", "pipeline"],
                ["executor", "analyst"],
                [],
            ),
        ]
        for sid, name, desc, cat, ver, author, tags, agents, plugins in templates:
            self._templates[sid] = SolutionTemplate(
                solution_id=sid,
                name=name,
                description=desc,
                category=cat,
                version=ver,
                author=author,
                tags=tags,
                required_agents=agents,
                required_plugins=plugins,
                config_schema={"api_key": {"type": "string", "required": True}},
            )

        # One deployed solution
        self._deployments["dep-001"] = SolutionDeployment(
            deployment_id="dep-001",
            solution_id="sol-customer-support",
            name="Customer Support Production",
            status=SolutionStatus.DEPLOYED,
            config={"api_key": "********", "tier": "enterprise"},
            metrics={"tickets_handled": 1250, "satisfaction_score": 0.88},
        )

    def list_solutions(
        self,
        category: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """List available solution templates.

        Args:
            category: Filter by category.
            search: Search keyword.
            limit: Result limit.
            offset: Pagination offset.

        Returns:
            Dict with paginated solution list.
        """
        solutions = list(self._templates.values())
        if category:
            solutions = [s for s in solutions if s.category == category]
        if search:
            search_lower = search.lower()
            solutions = [
                s for s in solutions
                if search_lower in s.name.lower()
                or search_lower in s.description.lower()
                or any(search_lower in t.lower() for t in s.tags)
            ]
        solutions.sort(key=lambda s: s.name.lower())
        total = len(solutions)
        solutions = solutions[offset : offset + limit]
        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "items": [
                {
                    "id": s.solution_id,
                    "name": s.name,
                    "description": s.description,
                    "category": s.category,
                    "version": s.version,
                    "author": s.author,
                    "tags": s.tags,
                }
                for s in solutions
            ],
        }

    def get_solution_info(self, solution_id: str) -> Dict[str, Any]:
        """Get detailed info about a solution template.

        Args:
            solution_id: Solution identifier.

        Returns:
            Dict with solution details.

        Raises:
            ValueError: If solution not found.
        """
        solution = self._templates.get(solution_id)
        if not solution:
            raise ValueError(f"Solution not found: {solution_id}")
        return {
            "id": solution.solution_id,
            "name": solution.name,
            "description": solution.description,
            "category": solution.category,
            "version": solution.version,
            "author": solution.author,
            "tags": solution.tags,
            "required_agents": solution.required_agents,
            "required_plugins": solution.required_plugins,
            "config_schema": solution.config_schema,
            "created_at": solution.created_at,
        }

    def deploy_solution(
        self,
        solution_id: str,
        name: str,
        config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Deploy a solution template.

        Args:
            solution_id: Solution template ID.
            name: Deployment name.
            config: Optional configuration.

        Returns:
            Dict with deployment details.

        Raises:
            ValueError: If solution not found.
        """
        if solution_id not in self._templates:
            raise ValueError(f"Solution not found: {solution_id}")
        deployment_id = f"dep-{uuid.uuid4().hex[:8]}"
        deployment = SolutionDeployment(
            deployment_id=deployment_id,
            solution_id=solution_id,
            name=name,
            status=SolutionStatus.DEPLOYING,
            config=config or {},
        )
        self._deployments[deployment_id] = deployment
        # Simulate deployment completion
        deployment.status = SolutionStatus.DEPLOYED
        return {
            "deployment_id": deployment.deployment_id,
            "solution_id": deployment.solution_id,
            "name": deployment.name,
            "status": deployment.status.value,
            "config": deployment.config,
            "deployed_at": deployment.deployed_at,
        }

    def get_deployment_status(self, deployment_id: str) -> Dict[str, Any]:
        """Get the status of a deployed solution.

        Args:
            deployment_id: Deployment identifier.

        Returns:
            Dict with deployment status.

        Raises:
            ValueError: If deployment not found.
        """
        deployment = self._deployments.get(deployment_id)
        if not deployment:
            raise ValueError(f"Deployment not found: {deployment_id}")
        return {
            "deployment_id": deployment.deployment_id,
            "solution_id": deployment.solution_id,
            "name": deployment.name,
            "status": deployment.status.value,
            "deployed_at": deployment.deployed_at,
            "last_updated": deployment.last_updated,
            "uptime_seconds": 86400,  # placeholder
        }

    def configure_solution(
        self,
        deployment_id: str,
        config: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Update configuration of a deployed solution.

        Args:
            deployment_id: Deployment identifier.
            config: New configuration values.

        Returns:
            Dict with updated configuration.

        Raises:
            ValueError: If deployment not found.
        """
        deployment = self._deployments.get(deployment_id)
        if not deployment:
            raise ValueError(f"Deployment not found: {deployment_id}")
        deployment.config.update(config)
        deployment.last_updated = datetime.utcnow().isoformat()
        deployment.status = SolutionStatus.UPDATING
        return {
            "deployment_id": deployment_id,
            "status": "updating",
            "config": deployment.config,
            "updated_at": deployment.last_updated,
        }

    def get_solution_metrics(self, deployment_id: str) -> Dict[str, Any]:
        """Get metrics for a deployed solution.

        Args:
            deployment_id: Deployment identifier.

        Returns:
            Dict with metrics data.

        Raises:
            ValueError: If deployment not found.
        """
        deployment = self._deployments.get(deployment_id)
        if not deployment:
            raise ValueError(f"Deployment not found: {deployment_id}")
        return {
            "deployment_id": deployment_id,
            "solution_id": deployment.solution_id,
            "period": "last_30_days",
            "metrics": {
                "tasks_completed": deployment.metrics.get("tasks_handled", 0),
                "success_rate": 0.95,
                "avg_response_time_ms": 1200,
                "user_satisfaction": deployment.metrics.get("satisfaction_score", 0.85),
                "cost_estimate": 150.0,
            },
        }


# =====================================================================
# CLI Parser
# =====================================================================


def register_solution_commands(
    subparsers: argparse._SubParsersAction,
) -> None:
    """Register solution subcommands under the given argparse subparser.

    Args:
        subparsers: The parent subparsers action to attach to.
    """
    p_sol = subparsers.add_parser("solution", help="Solution template management")
    sol_sub = p_sol.add_subparsers(dest="solution_command", required=True)

    # solution list
    p_list = sol_sub.add_parser("list", help="List available solutions")
    p_list.add_argument("--category", help="Filter by category")
    p_list.add_argument("--search", help="Search keyword")
    p_list.add_argument("--limit", type=int, default=20, help="Result limit")
    p_list.add_argument("--offset", type=int, default=0, help="Pagination offset")

    # solution info
    p_info = sol_sub.add_parser("info", help="Get solution details")
    p_info.add_argument("solution_id", help="Solution identifier")

    # solution deploy
    p_deploy = sol_sub.add_parser("deploy", help="Deploy a solution")
    p_deploy.add_argument("solution_id", help="Solution template ID")
    p_deploy.add_argument("--name", required=True, help="Deployment name")
    p_deploy.add_argument("--config", help="JSON string with configuration")

    # solution status
    p_status = sol_sub.add_parser("status", help="Get deployment status")
    p_status.add_argument("deployment_id", help="Deployment identifier")

    # solution configure
    p_config = sol_sub.add_parser("configure", help="Configure a deployed solution")
    p_config.add_argument("deployment_id", help="Deployment identifier")
    p_config.add_argument("--config", required=True, help="JSON string with config updates")

    # solution metrics
    p_metrics = sol_sub.add_parser("metrics", help="Get solution metrics")
    p_metrics.add_argument("deployment_id", help="Deployment identifier")


# =====================================================================
# Command Handler
# =====================================================================


def handle_solution_command(
    args: argparse.Namespace, manager: SolutionManager
) -> int:
    """Dispatch a solution CLI command to the appropriate handler.

    Args:
        args: Parsed argparse namespace.
        manager: SolutionManager instance.

    Returns:
        Exit code (0 for success, 1 for error).
    """
    try:
        if args.solution_command == "list":
            result = manager.list_solutions(
                category=args.category,
                search=args.search,
                limit=args.limit,
                offset=args.offset,
            )
        elif args.solution_command == "info":
            result = manager.get_solution_info(args.solution_id)
        elif args.solution_command == "deploy":
            config = json.loads(args.config) if args.config else None
            result = manager.deploy_solution(
                args.solution_id, name=args.name, config=config
            )
        elif args.solution_command == "status":
            result = manager.get_deployment_status(args.deployment_id)
        elif args.solution_command == "configure":
            config = json.loads(args.config)
            result = manager.configure_solution(args.deployment_id, config)
        elif args.solution_command == "metrics":
            result = manager.get_solution_metrics(args.deployment_id)
        else:
            print(f"Unknown solution command: {args.solution_command}")
            return 1

        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except (ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": str(exc)}, indent=2, ensure_ascii=False))
        return 1
