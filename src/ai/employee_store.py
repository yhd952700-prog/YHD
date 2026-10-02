"""
Durable EmployeeStore for LiuHao AI-OS.

Persists ``Employee`` + its agents + tasks to a single JSON file using ONLY the
stdlib ``json`` module (no third-party dependency is introduced). This is the
first real persistence layer for employees/agents/tasks -- previously ALL of
that state lived in in-memory Python dicts and was lost on every process
restart (flagged in the P1 readiness doc as the single biggest gap).

Design mirrors the existing planner persistence in
``src/ai/goal_task_graph.py`` (``save_state`` / ``load_state``):

  * REDIR-able store path via ``LIUHAO_WORKSPACE_ROOT`` so isolation verifiers
    never touch the real workspace.
  * An unserializable ``result`` falls back to
    ``{"__unserializable__": str(result)}`` so the dump never raises mid
    restart-recovery.
  * Enums (``AgentStatus`` / ``TaskStatus``) are stored by their ``.value``
    string and reconstructed from it.

The store holds a dict of employees keyed by name in one file, so
``list_employees`` / ``remove`` operate over the same JSON document.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional

from .employee import Agent, AgentStatus, Employee, Task, TaskStatus
from .providers import BaseProvider, get_provider


class EmployeeStore:
    """JSON-file backed persistence for ``Employee`` instances.

    Usage::

        store = EmployeeStore()                       # path auto-resolved
        emp = store.create_employee("liuhao-default") # seeds OR loads
        emp.add_task("...")
        store.save(emp)                               # persist
        emp2 = store.load("liuhao-default")            # recover after restart
        store.remove("liuhao-default")
    """

    VERSION = 1

    def __init__(self, path: Optional[str] = None):
        self.path = path or self._default_store_path()

    # ------------------------------------------------------------------ #
    # Path resolution (REDIR-able, mirrors the planner's helper)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _default_store_path() -> str:
        """Default location of the employee store.

        ``LIUHAO_WORKSPACE_ROOT/liuhao_employees.json`` when that env var is
        set (so isolation verifiers never write to the real workspace), else
        ``<repo_root>/.liuhao_employees.json``.
        """
        root = os.environ.get("LIUHAO_WORKSPACE_ROOT")
        if root:
            return os.path.join(root, "liuhao_employees.json")
        repo_root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        return os.path.join(repo_root, ".liuhao_employees.json")

    # ------------------------------------------------------------------ #
    # Low-level file IO
    # ------------------------------------------------------------------ #
    def _load_store(self) -> Dict[str, Any]:
        if not os.path.exists(self.path):
            return {"version": self.VERSION, "employees": {}}
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError):
            # A corrupt/partial file must not crash recovery; start fresh.
            return {"version": self.VERSION, "employees": {}}
        data.setdefault("version", self.VERSION)
        data.setdefault("employees", {})
        return data

    def _write_store(self, data: Dict[str, Any]) -> None:
        directory = os.path.dirname(os.path.abspath(self.path))
        if directory:
            os.makedirs(directory, exist_ok=True)
        tmp = f"{self.path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        os.replace(tmp, self.path)

    # ------------------------------------------------------------------ #
    # Serialization helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _safe_json(value: Any) -> Any:
        """Return ``value`` if it is JSON-safe, else a stringified fallback."""
        try:
            json.dumps(value)
            return value
        except (TypeError, ValueError):
            return {"__unserializable__": str(value)}

    def _serialize_agent(self, agent: Agent) -> Dict[str, Any]:
        return {
            "id": agent.id,
            "agent_type": agent.agent_type,
            "name": agent.name,
            "system_prompt": agent.system_prompt,
            "status": agent.status.value,
            "completed_tasks": agent.completed_tasks,
            "failed_tasks": agent.failed_tasks,
            "total_latency_ms": agent.total_latency_ms,
            "metadata": agent.metadata,
            "current_task": agent.current_task,
        }

    def _serialize_task(self, task: Task) -> Dict[str, Any]:
        return {
            "id": task.id,
            "description": task.description,
            "task_type": task.task_type,
            "depends_on": list(task.depends_on),
            "status": task.status.value,
            "assigned_agent": task.assigned_agent,
            "result": self._safe_json(task.result),
            "error": getattr(task, "error", None),
            "priority": task.priority,
            "metadata": getattr(task, "metadata", {}) or {},
            "created_at": task.created_at,
            "started_at": task.started_at,
            "completed_at": task.completed_at,
        }

    def _serialize_employee(self, employee: Employee) -> Dict[str, Any]:
        return {
            "name": employee.name,
            "agent_count": employee.agent_count,
            "agent_types": list(employee.agent_types),
            "system_prompts": employee.system_prompts,
            "enable_observability": employee.enable_observability,
            "agents": [self._serialize_agent(a) for a in employee.agents.values()],
            "tasks": [self._serialize_task(t) for t in employee.tasks.values()],
            "task_queue": list(employee.task_queue),
            "completed_tasks": list(employee.completed_tasks),
            "failed_tasks": list(employee.failed_tasks),
            "total_tasks_submitted": employee.total_tasks_submitted,
            "total_tasks_completed": employee.total_tasks_completed,
            "total_tasks_failed": employee.total_tasks_failed,
            "total_goals_submitted": employee.total_goals_submitted,
            "total_goals_completed": employee.total_goals_completed,
            "total_goals_failed": employee.total_goals_failed,
            "goals": list(employee.goals),
        }

    def _deserialize_employee(
        self, data: Dict[str, Any], provider: Optional[BaseProvider] = None
    ) -> Employee:
        provider = provider or get_provider()
        emp = Employee(
            name=data["name"],
            provider=provider,
            agent_count=data.get("agent_count", len(data.get("agents", []))),
            agent_types=list(data.get("agent_types", [])) or None,
            system_prompts=data.get("system_prompts") or None,
            enable_observability=bool(data.get("enable_observability", True)),
        )

        # Reconstruct agents (overwrite the freshly-built pool).
        emp.agents = {}
        for ad in data.get("agents", []):
            agent = Agent(
                id=ad["id"],
                agent_type=ad["agent_type"],
                name=ad["name"],
                provider=provider,
                system_prompt=ad.get("system_prompt", ""),
                status=AgentStatus(ad["status"]),
                current_task=ad.get("current_task"),
                completed_tasks=int(ad.get("completed_tasks", 0)),
                failed_tasks=int(ad.get("failed_tasks", 0)),
                total_latency_ms=float(ad.get("total_latency_ms", 0.0)),
                metadata=ad.get("metadata", {}) or {},
            )
            emp.agents[agent.id] = agent

        # Reconstruct tasks.
        emp.tasks = {}
        for td in data.get("tasks", []):
            task = Task(
                id=td["id"],
                description=td.get("description", ""),
                task_type=td.get("task_type", "general"),
                priority=int(td.get("priority", 0)),
                depends_on=list(td.get("depends_on", []) or []),
                status=TaskStatus(td["status"]),
                assigned_agent=td.get("assigned_agent"),
                result=td.get("result"),
                created_at=td.get("created_at", time.time()),
                started_at=td.get("started_at"),
                completed_at=td.get("completed_at"),
            )
            # ``error`` / ``metadata`` exist on the planner's TaskNode but not on
            # the Employee Task dataclass; restore them as ad-hoc attributes so
            # the serialized shape round-trips without altering the dataclass.
            task.error = td.get("error")
            task.metadata = td.get("metadata", {}) or {}
            emp.tasks[task.id] = task

        # Reconstruct queue + completion lists + aggregate counters.
        emp.task_queue = list(data.get("task_queue", []))
        emp.completed_tasks = list(data.get("completed_tasks", []))
        emp.failed_tasks = list(data.get("failed_tasks", []))
        emp.total_tasks_submitted = int(data.get("total_tasks_submitted", 0))
        emp.total_tasks_completed = int(data.get("total_tasks_completed", 0))
        emp.total_tasks_failed = int(data.get("total_tasks_failed", 0))
        emp.total_goals_submitted = int(data.get("total_goals_submitted", 0))
        emp.total_goals_completed = int(data.get("total_goals_completed", 0))
        emp.total_goals_failed = int(data.get("total_goals_failed", 0))
        emp.goals = list(data.get("goals", []))
        return emp

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def create_employee(
        self,
        name: str,
        agent_count: int = 3,
        agent_types: Optional[List[str]] = None,
        system_prompts: Optional[Dict[str, str]] = None,
        enable_observability: bool = True,
    ) -> Employee:
        """Get (or seed) an employee by ``name``.

        If an employee with ``name`` already exists in the store, it is LOADED
        (reconstructing agents + tasks + aggregate counters). Otherwise a fresh
        ``Employee`` is constructed with the supplied config and SAVED.
        """
        store = self._load_store()
        existing = store.get("employees", {}).get(name)
        if existing is not None:
            return self._deserialize_employee(existing)
        emp = Employee(
            name=name,
            agent_count=agent_count,
            agent_types=agent_types,
            system_prompts=system_prompts,
            enable_observability=enable_observability,
        )
        self.save(emp)
        return emp

    def save(self, employee: Employee) -> str:
        """Serialize ``employee`` (agents, tasks, queue, counters) to JSON.

        Returns the path written.
        """
        store = self._load_store()
        store.setdefault("employees", {})[employee.name] = self._serialize_employee(employee)
        self._write_store(store)
        return self.path

    def load(self, name: str) -> Optional[Employee]:
        """Reconstruct an employee from the store, or ``None`` if absent."""
        store = self._load_store()
        data = store.get("employees", {}).get(name)
        if data is None:
            return None
        return self._deserialize_employee(data)

    def list_employees(self) -> List[str]:
        """Names of all employees present in the store."""
        return list(self._load_store().get("employees", {}).keys())

    def remove(self, name: str) -> bool:
        """Remove an employee from the store. Returns True if it existed."""
        store = self._load_store()
        if name in store.get("employees", {}):
            del store["employees"][name]
            self._write_store(store)
            return True
        return False
