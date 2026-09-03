"""
AI Employee Module for LiuHao AI OS

Provides multi-agent coordination framework for complex task execution.
Supports agent pools, task distribution, result aggregation, and KPI reporting.
"""

from typing import Dict, List, Any, Optional, Callable
from dataclasses import dataclass, field
from enum import Enum
import uuid
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from .providers import BaseProvider, get_provider


class AgentStatus(Enum):
    """Agent execution status."""
    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class TaskStatus(Enum):
    """Task execution status."""
    PENDING = "pending"
    ASSIGNED = "assigned"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class Agent:
    """Represents an AI agent in the employee pool."""
    id: str
    agent_type: str
    name: str
    provider: BaseProvider
    system_prompt: str = ""
    status: AgentStatus = AgentStatus.IDLE
    current_task: Optional[str] = None
    completed_tasks: int = 0
    failed_tasks: int = 0
    total_latency_ms: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def execute(self, task: str, **kwargs) -> Dict[str, Any]:
        """Execute a task using this agent's provider."""
        self.status = AgentStatus.RUNNING
        self.current_task = task
        start_time = time.time()

        try:
            # Build prompt with system prompt
            full_prompt = f"{self.system_prompt}\n\nTask: {task}" if self.system_prompt else task

            # Generate response
            response = self.provider.generate(full_prompt, **kwargs)

            latency_ms = (time.time() - start_time) * 1000
            self.total_latency_ms += latency_ms
            self.completed_tasks += 1
            self.status = AgentStatus.COMPLETED
            self.current_task = None

            return {
                "agent_id": self.id,
                "agent_type": self.agent_type,
                "task": task,
                "result": response,
                "status": "completed",
                "latency_ms": latency_ms,
            }

        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000
            self.total_latency_ms += latency_ms
            self.failed_tasks += 1
            self.status = AgentStatus.FAILED
            self.current_task = None

            return {
                "agent_id": self.id,
                "agent_type": self.agent_type,
                "task": task,
                "result": str(e),
                "status": "failed",
                "latency_ms": latency_ms,
                "error": str(e),
            }


@dataclass
class Task:
    """Represents a task to be executed by agents."""
    id: str
    description: str
    task_type: str = "general"
    priority: int = 0
    depends_on: List[str] = field(default_factory=list)
    status: TaskStatus = TaskStatus.PENDING
    assigned_agent: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None


class Employee:
    """
    AI Employee - Coordinates multiple agents for complex task execution.

    Features:
    - Agent pool management with different agent types
    - Task distribution with dependency resolution
    - Result aggregation
    - KPI reporting
    """

    def __init__(
        self,
        name: str,
        provider: Optional[BaseProvider] = None,
        agent_count: int = 3,
        agent_types: Optional[List[str]] = None,
        system_prompts: Optional[Dict[str, str]] = None,
    ):
        """
        Initialize an Employee.

        Args:
            name: Employee name
            provider: AI provider (defaults to global provider)
            agent_count: Number of agents to create
            agent_types: List of agent types (defaults to ["general"] * agent_count)
            system_prompts: Custom system prompts per agent type
        """
        self.name = name
        self.provider = provider or get_provider()
        self.agent_count = agent_count
        self.agent_types = agent_types or ["general"] * agent_count
        self.system_prompts = system_prompts or {}

        # Validate agent_types length
        if len(self.agent_types) < agent_count:
            # Repeat the last type
            self.agent_types = self.agent_types + [self.agent_types[-1]] * (agent_count - len(self.agent_types))

        # Create agents
        self.agents: Dict[str, Agent] = {}
        self._create_agents()

        # Task management
        self.tasks: Dict[str, Task] = {}
        self.task_queue: List[str] = []
        self.completed_tasks: List[str] = []
        self.failed_tasks: List[str] = []

        # Metrics
        self.total_tasks_submitted = 0
        self.total_tasks_completed = 0
        self.total_tasks_failed = 0

    def _create_agents(self):
        """Create agent pool."""
        for i in range(self.agent_count):
            agent_type = self.agent_types[i]
            agent_id = f"agent_{i}"
            system_prompt = self.system_prompts.get(agent_type, "")

            agent = Agent(
                id=agent_id,
                agent_type=agent_type,
                name=f"{self.name}-{agent_type}-{i}",
                provider=self.provider,
                system_prompt=system_prompt,
            )
            self.agents[agent_id] = agent

    def add_task(self, description: str, task_type: str = "general",
                 priority: int = 0, depends_on: Optional[List[str]] = None) -> str:
        """Add a task to the queue."""
        task_id = f"task_{len(self.tasks)}"
        task = Task(
            id=task_id,
            description=description,
            task_type=task_type,
            priority=priority,
            depends_on=depends_on or [],
        )
        self.tasks[task_id] = task
        self.task_queue.append(task_id)
        self.total_tasks_submitted += 1
        return task_id

    def add_tasks(self, tasks: List[Dict[str, Any]]) -> List[str]:
        """Add multiple tasks at once."""
        task_ids = []
        for task_data in tasks:
            task_id = self.add_task(
                description=task_data.get("description", ""),
                task_type=task_data.get("type", "general"),
                priority=task_data.get("priority", 0),
                depends_on=task_data.get("depends_on"),
            )
            task_ids.append(task_id)
        return task_ids

    def distribute_tasks(self, tasks: Optional[List[str]] = None) -> Dict[str, str]:
        """
        Distribute tasks to available agents.

        Args:
            tasks: Specific task IDs to distribute (defaults to all pending)

        Returns:
            Mapping of agent_id -> task_id
        """
        if tasks is None:
            tasks = [t for t in self.task_queue if self.tasks[t].status == TaskStatus.PENDING]

        distribution = {}
        available_agents = [a for a in self.agents.values() if a.status == AgentStatus.IDLE]

        for i, task_id in enumerate(tasks):
            if i >= len(available_agents):
                break  # No more available agents

            agent = available_agents[i]
            task = self.tasks[task_id]

            # Check dependencies
            if not self._dependencies_met(task):
                continue

            task.status = TaskStatus.ASSIGNED
            task.assigned_agent = agent.id
            agent.current_task = task_id
            distribution[agent.id] = task_id

        return distribution

    def _dependencies_met(self, task: Task) -> bool:
        """Check if all task dependencies are completed."""
        for dep_id in task.depends_on:
            if dep_id not in self.tasks:
                return False
            if self.tasks[dep_id].status != TaskStatus.COMPLETED:
                return False
        return True

    def execute_tasks(self, tasks: Optional[List[str]] = None, max_workers: int = None) -> Dict[str, Any]:
        """
        Execute distributed tasks in parallel.

        Args:
            tasks: Task IDs to execute (defaults to all assigned)
            max_workers: Maximum parallel workers

        Returns:
            Aggregated results
        """
        if tasks is None:
            tasks = [t for t in self.task_queue if self.tasks[t].status == TaskStatus.ASSIGNED]

        max_workers = max_workers or len(self.agents)
        results = {}

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_task = {}

            for task_id in tasks:
                task = self.tasks[task_id]
                if task.status != TaskStatus.ASSIGNED:
                    continue

                agent = self.agents.get(task.assigned_agent)
                if not agent:
                    continue

                task.status = TaskStatus.RUNNING
                task.started_at = time.time()

                future = executor.submit(agent.execute, task.description)
                future_to_task[future] = (task_id, agent.id)

            for future in as_completed(future_to_task):
                task_id, agent_id = future_to_task[future]
                try:
                    result = future.result()
                    task = self.tasks[task_id]
                    task.result = result
                    task.status = TaskStatus.COMPLETED if result["status"] == "completed" else TaskStatus.FAILED
                    task.completed_at = time.time()

                    if task.status == TaskStatus.COMPLETED:
                        self.completed_tasks.append(task_id)
                        self.total_tasks_completed += 1
                    else:
                        self.failed_tasks.append(task_id)
                        self.total_tasks_failed += 1

                    results[task_id] = result

                except Exception as e:
                    task = self.tasks[task_id]
                    task.status = TaskStatus.FAILED
                    task.completed_at = time.time()
                    self.failed_tasks.append(task_id)
                    self.total_tasks_failed += 1
                    results[task_id] = {
                        "agent_id": agent_id,
                        "task": task.description,
                        "result": str(e),
                        "status": "failed",
                        "error": str(e),
                    }

        return results

    def aggregate_results(self, results: Dict[str, Any]) -> Dict[str, Any]:
        """
        Aggregate results from multiple agent executions.

        Args:
            results: Results from execute_tasks()

        Returns:
            Aggregated summary
        """
        completed = [r for r in results.values() if r.get("status") == "completed"]
        failed = [r for r in results.values() if r.get("status") == "failed"]

        all_results = []
        for r in completed:
            all_results.append(r.get("result", ""))

        return {
            "employee": self.name,
            "total_agents": len(self.agents),
            "total_tasks": len(results),
            "completed": len(completed),
            "failed": len(failed),
            "success_rate": len(completed) / len(results) * 100 if results else 0,
            "results": all_results,
            "errors": [r.get("error") for r in failed if r.get("error")],
            "avg_latency_ms": sum(r.get("latency_ms", 0) for r in results.values()) / len(results) if results else 0,
        }

    def generate_kpi_report(self) -> Dict[str, Any]:
        """Generate KPI report for this employee."""
        total = self.total_tasks_completed + self.total_tasks_failed
        success_rate = (self.total_tasks_completed / total * 100) if total > 0 else 0

        # Agent-level stats
        agent_stats = {}
        for agent_id, agent in self.agents.items():
            agent_total = agent.completed_tasks + agent.failed_tasks
            agent_stats[agent_id] = {
                "agent_type": agent.agent_type,
                "completed": agent.completed_tasks,
                "failed": agent.failed_tasks,
                "success_rate": (agent.completed_tasks / agent_total * 100) if agent_total > 0 else 0,
                "avg_latency_ms": (agent.total_latency_ms / agent_total) if agent_total > 0 else 0,
            }

        return {
            "employee_name": self.name,
            "agent_count": self.agent_count,
            "agent_types": self.agent_types,
            "total_tasks_submitted": self.total_tasks_submitted,
            "completed_tasks": self.total_tasks_completed,
            "failed_tasks": self.total_tasks_failed,
            "success_rate": success_rate,
            "agent_stats": agent_stats,
            "pending_tasks": len([t for t in self.tasks.values() if t.status == TaskStatus.PENDING]),
            "running_tasks": len([t for t in self.tasks.values() if t.status == TaskStatus.RUNNING]),
        }

    def get_agent_status(self) -> Dict[str, Any]:
        """Get status of all agents."""
        return {
            agent_id: {
                "agent_type": agent.agent_type,
                "status": agent.status.value,
                "current_task": agent.current_task,
                "completed": agent.completed_tasks,
                "failed": agent.failed_tasks,
            }
            for agent_id, agent in self.agents.items()
        }

    def run_workflow(self, tasks: List[Dict[str, Any]], max_workers: int = None) -> Dict[str, Any]:
        """
        Run a complete workflow: add tasks, distribute, execute, aggregate.

        Args:
            tasks: List of task dicts with description, type, priority, depends_on
            max_workers: Maximum parallel workers

        Returns:
            Complete workflow results
        """
        # Add all tasks
        task_ids = self.add_tasks(tasks)

        # Distribute and execute in dependency order
        all_results = {}
        remaining_tasks = set(task_ids)

        while remaining_tasks:
            # Find tasks with met dependencies
            ready_tasks = [
                t for t in remaining_tasks
                if self._dependencies_met(self.tasks[t])
            ]

            if not ready_tasks:
                # Check for circular dependency or all failed
                break

            # Distribute ready tasks
            distribution = self.distribute_tasks(ready_tasks)

            # Execute
            executed_tasks = list(distribution.values())
            results = self.execute_tasks(executed_tasks, max_workers)
            all_results.update(results)

            # Remove completed from remaining
            remaining_tasks -= set(executed_tasks)

        # Aggregate
        aggregated = self.aggregate_results(all_results)
        aggregated["kpi"] = self.generate_kpi_report()
        aggregated["task_details"] = all_results

        return aggregated


class AgentPool:
    """Manages a pool of employees for large-scale coordination."""

    def __init__(self, name: str):
        self.name = name
        self.employees: Dict[str, Employee] = {}

    def add_employee(self, employee: Employee) -> None:
        """Add an employee to the pool."""
        self.employees[employee.name] = employee

    def remove_employee(self, name: str) -> bool:
        """Remove an employee from the pool."""
        if name in self.employees:
            del self.employees[name]
            return True
        return False

    def get_employee(self, name: str) -> Optional[Employee]:
        """Get an employee by name."""
        return self.employees.get(name)

    def distribute_workload(self, tasks: List[Dict[str, Any]]) -> Dict[str, List[str]]:
        """Distribute tasks across employees based on capacity."""
        # Simple round-robin for now
        employee_names = list(self.employees.keys())
        if not employee_names:
            return {}

        distribution = {name: [] for name in employee_names}
        for i, task in enumerate(tasks):
            emp_name = employee_names[i % len(employee_names)]
            distribution[emp_name].append(task)

        return distribution

    def execute_parallel(self, tasks: List[Dict[str, Any]], max_workers: int = None) -> Dict[str, Any]:
        """Execute tasks across all employees in parallel."""
        distribution = self.distribute_workload(tasks)
        all_results = {}

        with ThreadPoolExecutor(max_workers=max_workers or len(self.employees)) as executor:
            future_to_emp = {}

            for emp_name, emp_tasks in distribution.items():
                employee = self.employees[emp_name]
                future = executor.submit(employee.run_workflow, emp_tasks)
                future_to_emp[future] = emp_name

            for future in as_completed(future_to_emp):
                emp_name = future_to_emp[future]
                try:
                    result = future.result()
                    all_results[emp_name] = result
                except Exception as e:
                    all_results[emp_name] = {"error": str(e)}

        return all_results

    def aggregate_pool_kpi(self) -> Dict[str, Any]:
        """Aggregate KPIs across all employees."""
        total_submitted = 0
        total_completed = 0
        total_failed = 0

        employee_kpis = {}
        for name, emp in self.employees.items():
            kpi = emp.generate_kpi_report()
            employee_kpis[name] = kpi
            total_submitted += kpi["total_tasks_submitted"]
            total_completed += kpi["completed_tasks"]
            total_failed += kpi["failed_tasks"]

        return {
            "pool_name": self.name,
            "employee_count": len(self.employees),
            "total_tasks_submitted": total_submitted,
            "total_completed": total_completed,
            "total_failed": total_failed,
            "overall_success_rate": (total_completed / (total_completed + total_failed) * 100)
                if (total_completed + total_failed) > 0 else 0,
            "employee_kpis": employee_kpis,
        }
