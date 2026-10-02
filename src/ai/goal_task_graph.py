"""
Goal → Task Graph Module for LiuHao AI OS

Provides goal decomposition, task chain generation, and execution workflow management.
Supports dependency resolution, circular dependency detection, and parallel execution.
"""

from typing import Dict, List, Any, Optional, Set, Callable
from dataclasses import dataclass, field
from enum import Enum
import uuid
import time
import json
import os
from collections import defaultdict, deque

from .providers import BaseProvider, get_provider
try:
    from ..observability.metrics import (
        track_goal_decomposition,
        track_task_execution,
    )
except Exception:  # pragma: no cover - metrics backend optional at import time
    # When the metrics backend (prometheus_client) is not installed the planner
    # must still import and run; metrics simply degrade to no-ops. In the normal
    # (venv) environment the import succeeds and behaviour is unchanged.
    def track_goal_decomposition(*_args, **_kwargs):
        return None

    def track_task_execution(*_args, **_kwargs):
        return None
from ..observability.tracing import create_span, end_span, AISpanAttributes
from ..knowledge.memory import create_memory_manager, MemoryTier
from .observability import observe
# The kernel layer is audited via @kernel_action (Policy Controlled + Audited +
# Observable). Importing it here follows the existing src/ai -> src/kernels
# dependency pattern (src/ai/agent_runtime.py, src/ai/liuhao.py already do so).
from src.kernels._crosscutting import kernel_action


class GoalStatus(Enum):
    """Goal execution status."""
    PENDING = "pending"
    DECOMPOSING = "decomposing"
    DECOMPOSED = "decomposed"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"


class TaskStatus(Enum):
    """Task execution status."""
    PENDING = "pending"
    READY = "ready"           # Dependencies met, waiting for execution
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class GoalDefinition:
    """Represents a high-level goal to be decomposed into tasks."""
    id: str
    description: str
    priority: str = "medium"  # low, medium, high, critical
    status: GoalStatus = GoalStatus.PENDING
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def __post_init__(self):
        if not self.id:
            self.id = f"goal_{uuid.uuid4().hex[:8]}"


@dataclass
class TaskNode:
    """Represents a task in the goal-task graph."""
    id: str
    description: str
    task_type: str = "general"
    depends_on: List[str] = field(default_factory=list)  # Task IDs this task depends on
    status: TaskStatus = TaskStatus.PENDING
    assigned_agent: Optional[str] = None
    result: Optional[Any] = None
    error: Optional[str] = None
    priority: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None

    def __post_init__(self):
        if not self.id:
            self.id = f"task_{uuid.uuid4().hex[:8]}"


class GoalTaskGraph:
    """
    Goal → Task Graph - Decomposes goals into executable task graphs.

    Features:
    - AI-powered goal decomposition
    - Dependency graph construction
    - Topological sorting for execution order
    - Circular dependency detection
    - Parallel execution support
    - Progress tracking
    - Observability integration (metrics, tracing, memory)
    """

    def __init__(
        self,
        provider: Optional[BaseProvider] = None,
        goal: Optional[GoalDefinition] = None,
        max_decomposition_depth: int = 3,
        max_tasks_per_goal: int = 20,
        enable_observability: bool = True,
    ):
        """
        Initialize GoalTaskGraph.

        Args:
            provider: AI provider for decomposition
            goal: Initial goal (can be set later)
            max_decomposition_depth: Maximum recursion depth for decomposition
            max_tasks_per_goal: Maximum tasks to generate per goal
            enable_observability: Enable metrics/tracing/memory integration
        """
        self.provider = provider or get_provider()
        self.goal = goal
        self.max_decomposition_depth = max_decomposition_depth
        self.max_tasks_per_goal = max_tasks_per_goal
        self.enable_observability = enable_observability

        # Task graph storage
        self.tasks: Dict[str, TaskNode] = {}
        self.goal_tasks: Dict[str, List[str]] = defaultdict(list)  # goal_id -> task_ids
        self.task_dependencies: Dict[str, Set[str]] = defaultdict(set)  # task_id -> dependent task_ids

        # Execution state
        self.execution_order: List[str] = []
        self.completed_tasks: Set[str] = set()
        self.failed_tasks: Set[str] = set()

        # Observability
        if self.enable_observability:
            self._memory = create_memory_manager(user_id="goal_task_graph", use_mem0=False)
        else:
            self._memory = None

        # Decomposition prompts
        self.decomposition_prompt_template = """You are an expert task planner. Decompose the following goal into specific, actionable tasks.

Goal: {goal_description}
Priority: {priority}

Generate a JSON array of tasks. Each task should have:
- "id": unique identifier (e.g., "task_1")
- "description": clear, actionable task description
- "type": task type (research, implementation, testing, documentation, review, etc.)
- "depends_on": list of task IDs this task depends on (empty if no dependencies)
- "priority": integer priority (0-10, higher = more important)

Constraints:
- Maximum {max_tasks} tasks
- Tasks should be logically ordered with clear dependencies
- Each task should be independently executable once dependencies are met
- Avoid circular dependencies

Example output format:
[
    {{"id": "task_1", "description": "Research best practices", "type": "research", "depends_on": [], "priority": 8}},
    {{"id": "task_2", "description": "Design architecture", "type": "design", "depends_on": ["task_1"], "priority": 9}},
    {{"id": "task_3", "description": "Implement core module", "type": "implementation", "depends_on": ["task_2"], "priority": 10}}
]"""

    def set_goal(self, goal: GoalDefinition):
        """Set the goal to decompose."""
        self.goal = goal
        self.goal.status = GoalStatus.PENDING

    @observe("goal_task_graph.decompose_goal")
    def decompose_goal(self, goal: Optional[GoalDefinition] = None) -> List[TaskNode]:
        """
        Decompose a goal into a list of tasks using AI.

        Args:
            goal: Goal to decompose (uses self.goal if not provided)

        Returns:
            List of TaskNode objects
        """
        if goal:
            self.set_goal(goal)

        if not self.goal:
            raise ValueError("No goal set for decomposition")

        self.goal.status = GoalStatus.DECOMPOSING

        # Build prompt
        prompt = self.decomposition_prompt_template.format(
            goal_description=self.goal.description,
            priority=self.goal.priority,
            max_tasks=self.max_tasks_per_goal,
        )

        # Use tracing for decomposition
        span = create_span(
            "goal.decompose",
            attributes={
                AISpanAttributes.GOAL_ID: self.goal.id,
                "goal.description": self.goal.description[:200],
                "goal.priority": self.goal.priority,
            }
        )

        start_time = time.time()
        try:
            # Call AI provider with retry
            response = self.provider.generate_with_retry(prompt, temperature=0.3)

            # Parse response
            tasks = self._parse_decomposition_response(response)

            # Create TaskNodes
            task_nodes = []
            for task_data in tasks:
                task = TaskNode(
                    id=task_data.get("id", f"task_{len(task_nodes)}"),
                    description=task_data.get("description", ""),
                    task_type=task_data.get("type", "general"),
                    depends_on=task_data.get("depends_on", []),
                    priority=task_data.get("priority", 0),
                )
                task_nodes.append(task)

            # Store tasks
            self._store_tasks(self.goal.id, task_nodes)

            self.goal.status = GoalStatus.DECOMPOSED
            self.goal.updated_at = time.time()

            # Track metrics
            duration = time.time() - start_time
            track_goal_decomposition("success", duration, len(task_nodes))

            # Persist to memory
            if self.enable_observability and self._memory:
                self._memory.remember(
                    content=f"Goal decomposed: {self.goal.description}",
                    tier=MemoryTier.SEMANTIC,
                    importance=0.9,
                    tags=["goal", "decomposition"],
                    metadata={"goal_id": self.goal.id, "task_count": len(task_nodes)}
                )

            if span:
                end_span(span)

            return task_nodes

        except Exception as e:
            self.goal.status = GoalStatus.FAILED
            self.goal.updated_at = time.time()

            # Track metrics
            duration = time.time() - start_time
            track_goal_decomposition("failed", duration, 0)

            if span:
                end_span(span, error=e)

            raise RuntimeError(f"Goal decomposition failed: {e}")

    def _parse_decomposition_response(self, response: str) -> List[Dict[str, Any]]:
        """Parse AI response into task list."""
        import json
        import re

        # Try to extract JSON from response
        # Look for JSON array in the response
        json_match = re.search(r'\[.*\]', response, re.DOTALL)
        if json_match:
            json_str = json_match.group()
            try:
                return json.loads(json_str)
            except json.JSONDecodeError:
                pass

        # Fallback: try to parse as JSON directly
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            pass

        # Last resort: create fallback tasks
        return self._fallback_decomposition()

    def _fallback_decomposition(self) -> List[Dict[str, Any]]:
        """Create fallback tasks when AI parsing fails."""
        return [
            {"id": "task_1", "description": f"Analyze goal: {self.goal.description}", "type": "analysis", "depends_on": [], "priority": 10},
            {"id": "task_2", "description": "Plan implementation approach", "type": "planning", "depends_on": ["task_1"], "priority": 9},
            {"id": "task_3", "description": "Execute core work", "type": "implementation", "depends_on": ["task_2"], "priority": 8},
            {"id": "task_4", "description": "Verify results", "type": "testing", "depends_on": ["task_3"], "priority": 7},
        ]

    def _store_tasks(self, goal_id: str, tasks: List[TaskNode]):
        """Store tasks in the graph."""
        task_ids = []
        for task in tasks:
            self.tasks[task.id] = task
            task_ids.append(task.id)
            # Build reverse dependency map
            for dep in task.depends_on:
                self.task_dependencies[dep].add(task.id)

        self.goal_tasks[goal_id] = task_ids

    def get_tasks_for_goal(self, goal_id: str) -> List[TaskNode]:
        """Get all tasks for a specific goal."""
        task_ids = self.goal_tasks.get(goal_id, [])
        return [self.tasks[tid] for tid in task_ids if tid in self.tasks]

    def get_execution_order(self, tasks: Optional[List[TaskNode]] = None) -> List[TaskNode]:
        """
        Get topological execution order for tasks.

        Args:
            tasks: Specific tasks to order (defaults to all tasks)

        Returns:
            Tasks in execution order (dependencies first)
        """
        if tasks is None:
            tasks = list(self.tasks.values())

        # Build adjacency list and in-degree count
        task_ids = [t.id for t in tasks]
        adj = defaultdict(list)
        in_degree = {tid: 0 for tid in task_ids}

        for task in tasks:
            for dep in task.depends_on:
                if dep in in_degree:
                    adj[dep].append(task.id)
                    in_degree[task.id] += 1

        # Topological sort (Kahn's algorithm)
        queue = deque([tid for tid in task_ids if in_degree[tid] == 0])
        order = []

        while queue:
            current = queue.popleft()
            order.append(current)

            for neighbor in adj[current]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        # Check for cycles
        if len(order) != len(task_ids):
            # Cycle detected - return best effort order
            remaining = set(task_ids) - set(order)
            order.extend(remaining)

        # Convert back to TaskNode objects
        return [self.tasks[tid] for tid in order if tid in self.tasks]

    def has_cycle(self, tasks: Optional[List[TaskNode]] = None) -> bool:
        """
        Detect if there are circular dependencies in the task graph.

        Args:
            tasks: Specific tasks to check (defaults to all tasks)

        Returns:
            True if cycle detected, False otherwise
        """
        if tasks is None:
            tasks = list(self.tasks.values())

        task_ids = [t.id for t in tasks]
        adj = defaultdict(list)

        for task in tasks:
            for dep in task.depends_on:
                if dep in task_ids:
                    adj[dep].append(task.id)

        # DFS cycle detection
        visited = set()
        rec_stack = set()

        def dfs(node: str) -> bool:
            visited.add(node)
            rec_stack.add(node)

            for neighbor in adj[node]:
                if neighbor not in visited:
                    if dfs(neighbor):
                        return True
                elif neighbor in rec_stack:
                    return True

            rec_stack.remove(node)
            return False

        for task_id in task_ids:
            if task_id not in visited:
                if dfs(task_id):
                    return True

        return False

    def get_ready_tasks(self, tasks: Optional[List[TaskNode]] = None) -> List[TaskNode]:
        """Get tasks that are ready to execute (dependencies met)."""
        if tasks is None:
            tasks = list(self.tasks.values())

        ready = []
        for task in tasks:
            if task.status != TaskStatus.PENDING:
                continue

            # Check if all dependencies are completed
            deps_met = all(
                dep_id in self.completed_tasks
                for dep_id in task.depends_on
            )

            if deps_met:
                task.status = TaskStatus.READY
                ready.append(task)

        return ready

    @observe("goal_task_graph.execute_task")
    def execute_task(self, task_id: str, agent_executor: Callable) -> Dict[str, Any]:
        """
        Execute a single task via the audited ``@kernel_action`` path.

        The real execution work (status transitions + the agent callback) is
        performed by :meth:`_execute_task_audited`, which is decorated with
        ``@kernel_action("ai.execute_planner_task")`` so the planner's
        task-execution loop is now covered by the audit (previously it called
        ``agent_executor`` directly and was orphaned from the audited path).
        """
        return self._execute_task_audited(task_id, agent_executor)

    @kernel_action("ai.execute_planner_task")
    def _execute_task_audited(self, task_id: str, agent_executor: Callable) -> Dict[str, Any]:
        """
        Audited core of a single task's execution.

        Decorated with ``@kernel_action`` so every task run records an honest
        audit event (``outcome`` / ``policy_decision``) via the same
        cross-cutting path used by the kernel layer. The policy posture stays
        record-only (``enforce=False``) -- this is honest auditing, not blocking.
        """
        if task_id not in self.tasks:
            return {"task_id": task_id, "status": "failed", "error": "Task not found"}

        task = self.tasks[task_id]
        task.status = TaskStatus.RUNNING
        task.started_at = time.time()

        # Use tracing for task execution
        span = create_span(
            "task.execute",
            attributes={
                AISpanAttributes.TASK_ID: task_id,
                AISpanAttributes.AGENT_TASK: task.description[:200],
            }
        )

        try:
            # Execute via agent executor -- now inside the audited wrapper.
            result = agent_executor(task.assigned_agent or "default", task.description)

            task.result = result
            task.status = TaskStatus.COMPLETED
            task.completed_at = time.time()
            self.completed_tasks.add(task_id)

            # Track metrics
            latency_ms = (task.completed_at - task.started_at) * 1000
            track_task_execution(task.task_type, "completed", latency_ms / 1000)

            # Persist to memory
            if self.enable_observability and self._memory:
                self._memory.remember(
                    content=f"Task executed: {task.description}",
                    tier=MemoryTier.EPISODIC,
                    importance=0.7,
                    tags=["task", task.task_type, "completed"],
                    metadata={"task_id": task_id, "result": str(result)[:500]}
                )

            if span:
                end_span(span)

            return {
                "task_id": task_id,
                "status": "completed",
                "result": result,
                "latency_ms": latency_ms,
            }

        except Exception as e:
            task.error = str(e)
            task.status = TaskStatus.FAILED
            task.completed_at = time.time()
            self.failed_tasks.add(task_id)

            # Track metrics
            latency_ms = (task.completed_at - task.started_at) * 1000
            track_task_execution(task.task_type, "failed", latency_ms / 1000)

            # Persist to memory
            if self.enable_observability and self._memory:
                self._memory.remember(
                    content=f"Task failed: {task.description}",
                    tier=MemoryTier.EPISODIC,
                    importance=0.8,
                    tags=["task", task.task_type, "failed"],
                    metadata={"task_id": task_id, "error": str(e)}
                )

            if span:
                end_span(span, error=e)

            return {
                "task_id": task_id,
                "status": "failed",
                "error": str(e),
                "latency_ms": latency_ms,
            }

    @observe("goal_task_graph.execute_graph")
    def execute_graph(
        self,
        goal_id: str,
        agent_executor: callable,
        max_parallel: int = 3,
    ) -> Dict[str, Any]:
        """
        Execute the full task graph for a goal.

        Args:
            goal_id: Goal ID to execute
            agent_executor: Function(agent_id, task_description) -> result
            max_parallel: Maximum parallel executions

        Returns:
            Execution results
        """
        import concurrent.futures

        tasks = self.get_tasks_for_goal(goal_id)
        if not tasks:
            return {"goal_id": goal_id, "status": "no_tasks", "results": {}}

        self.goal.status = GoalStatus.EXECUTING
        self.completed_tasks.clear()
        self.failed_tasks.clear()

        results = {}
        remaining = set(t.id for t in tasks)

        # Assign agents to tasks (simple round-robin)
        agent_ids = [f"agent_{i}" for i in range(max_parallel)]
        for i, task in enumerate(tasks):
            task.assigned_agent = agent_ids[i % len(agent_ids)]

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_parallel) as executor:
            while remaining:
                # Get ready tasks
                ready = [t for t in tasks if t.id in remaining and self._is_ready(t.id)]

                if not ready:
                    # No ready tasks but remaining - check for deadlock
                    if self.has_cycle([t for t in tasks if t.id in remaining]):
                        # Mark remaining as failed due to cycle
                        for tid in remaining:
                            task = self.tasks[tid]
                            task.status = TaskStatus.FAILED
                            task.error = "Circular dependency detected"
                            self.failed_tasks.add(tid)
                            results[tid] = {"task_id": tid, "status": "failed", "error": "Circular dependency"}
                        break
                    # Wait a bit and retry
                    time.sleep(0.1)
                    continue

                # Submit ready tasks
                future_to_task = {}
                for task in ready[:max_parallel]:
                    remaining.remove(task.id)
                    future = executor.submit(self.execute_task, task.id, agent_executor)
                    future_to_task[future] = task.id

                # Collect results
                for future in concurrent.futures.as_completed(future_to_task):
                    tid = future_to_task[future]
                    try:
                        result = future.result()
                        results[tid] = result
                    except Exception as e:
                        results[tid] = {"task_id": tid, "status": "failed", "error": str(e)}

        # Determine overall status
        completed_count = sum(1 for r in results.values() if r.get("status") == "completed")
        failed_count = sum(1 for r in results.values() if r.get("status") == "failed")

        if failed_count == 0:
            self.goal.status = GoalStatus.COMPLETED
            overall_status = "completed"
        elif completed_count == 0:
            self.goal.status = GoalStatus.FAILED
            overall_status = "failed"
        else:
            self.goal.status = GoalStatus.COMPLETED  # Partial success
            overall_status = "partial"

        self.goal.updated_at = time.time()

        return {
            "goal_id": goal_id,
            "goal_description": self.goal.description,
            "status": overall_status,
            "total_tasks": len(tasks),
            "completed": completed_count,
            "failed": failed_count,
            "results": results,
            "execution_order": [t.id for t in self.get_execution_order(tasks)],
        }

    def _is_ready(self, task_id: str) -> bool:
        """Check if task is ready to execute."""
        task = self.tasks.get(task_id)
        if not task or task.status != TaskStatus.PENDING:
            return False
        return all(dep_id in self.completed_tasks for dep_id in task.depends_on)

    def get_graph_summary(self, goal_id: str) -> Dict[str, Any]:
        """Get summary of the task graph for a goal."""
        tasks = self.get_tasks_for_goal(goal_id)

        if not tasks:
            return {"goal_id": goal_id, "task_count": 0}

        type_counts = defaultdict(int)
        for task in tasks:
            type_counts[task.task_type] += 1

        return {
            "goal_id": goal_id,
            "goal_description": self.goal.description if self.goal else "unknown",
            "goal_status": self.goal.status.value if self.goal else "unknown",
            "task_count": len(tasks),
            "task_types": dict(type_counts),
            "has_cycle": self.has_cycle(tasks),
            "execution_order": [t.id for t in self.get_execution_order(tasks)],
            "completed": len([t for t in tasks if t.id in self.completed_tasks]),
            "failed": len([t for t in tasks if t.id in self.failed_tasks]),
            "pending": len([t for t in tasks if t.status == TaskStatus.PENDING]),
        }

    # ------------------------------------------------------------------ #
    # Durable persistence (restart recovery) -- stdlib json only, no deps.
    # ------------------------------------------------------------------ #
    @staticmethod
    def _default_state_path() -> str:
        """Default location for the persisted planner state.

        Under ``LIUHAO_WORKSPACE_ROOT`` when set (REDIR-able so isolation
        verifiers never touch the real workspace), else
        ``<repo>/.planner_state.json``.
        """
        root = os.environ.get("LIUHAO_WORKSPACE_ROOT")
        if root:
            return os.path.join(root, "planner_state.json")
        repo_root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        return os.path.join(repo_root, ".planner_state.json")

    def _serialize_goal(self, goal: Optional[GoalDefinition]) -> Optional[Dict[str, Any]]:
        if goal is None:
            return None
        return {
            "id": goal.id,
            "description": goal.description,
            "priority": goal.priority,
            "status": goal.status.value,
            "metadata": goal.metadata,
            "created_at": goal.created_at,
            "updated_at": goal.updated_at,
        }

    def _serialize_task(self, task: TaskNode) -> Dict[str, Any]:
        # result may be an arbitrary object; keep it JSON-safe or fall back to a
        # stringified form so the dump never raises during restart recovery.
        try:
            json.dumps(task.result)
            result = task.result
        except (TypeError, ValueError):
            result = {"__unserializable__": str(task.result)}
        return {
            "id": task.id,
            "description": task.description,
            "task_type": task.task_type,
            "depends_on": list(task.depends_on),
            "status": task.status.value,
            "assigned_agent": task.assigned_agent,
            "result": result,
            "error": task.error,
            "priority": task.priority,
            "metadata": task.metadata,
            "created_at": task.created_at,
            "started_at": task.started_at,
            "completed_at": task.completed_at,
        }

    def save_state(self, path: Optional[str] = None) -> str:
        """Persist goals + tasks to a JSON file.

        Returns the path written. Uses only the stdlib ``json`` module so no
        third-party dependency is introduced. Call this after (or during)
        execution so a process restart can recover the graph via
        :meth:`load_state`.
        """
        path = path or self._default_state_path()
        data = {
            "version": 1,
            "goals": self._serialize_goal(self.goal),
            "tasks": [self._serialize_task(t) for t in self.tasks.values()],
        }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        return path

    @classmethod
    def load_state(
        cls, path: Optional[str] = None, **init_kwargs: Any
    ) -> "GoalTaskGraph":
        """Load a previously saved planner state into a fresh instance.

        The restored instance carries the same goal id, task ids, statuses,
        dependency edges and completion sets, enabling recovery across a
        process restart. ``**init_kwargs`` are forwarded to the constructor
        (e.g. ``enable_observability=False``).
        """
        resolved = path or cls._default_state_path()
        if not os.path.exists(resolved):
            raise FileNotFoundError(f"planner state not found: {resolved}")
        with open(resolved, "r", encoding="utf-8") as fh:
            data = json.load(fh)

        graph = cls(**init_kwargs)
        goal_data = data.get("goals")
        if goal_data:
            graph.goal = GoalDefinition(
                id=goal_data["id"],
                description=goal_data.get("description", ""),
                priority=goal_data.get("priority", "medium"),
                status=GoalStatus(goal_data.get("status", "pending")),
                metadata=goal_data.get("metadata", {}) or {},
                created_at=goal_data.get("created_at", time.time()),
                updated_at=goal_data.get("updated_at", time.time()),
            )
        for td in data.get("tasks", []):
            task = TaskNode(
                id=td["id"],
                description=td.get("description", ""),
                task_type=td.get("task_type", "general"),
                depends_on=list(td.get("depends_on", []) or []),
                status=TaskStatus(td.get("status", "pending")),
                assigned_agent=td.get("assigned_agent"),
                result=td.get("result"),
                error=td.get("error"),
                priority=td.get("priority", 0),
                metadata=td.get("metadata", {}) or {},
                created_at=td.get("created_at", time.time()),
                started_at=td.get("started_at"),
                completed_at=td.get("completed_at"),
            )
            graph.tasks[task.id] = task
            if graph.goal is not None:
                graph.goal_tasks[graph.goal.id].append(task.id)
                for dep in task.depends_on:
                    graph.task_dependencies[dep].add(task.id)
            if task.status == TaskStatus.COMPLETED:
                graph.completed_tasks.add(task.id)
            elif task.status == TaskStatus.FAILED:
                graph.failed_tasks.add(task.id)
        return graph


# Convenience function for quick goal decomposition
def decompose_goal(
    description: str,
    priority: str = "medium",
    provider: Optional[BaseProvider] = None,
) -> List[TaskNode]:
    """
    Quick function to decompose a goal into tasks.

    Args:
        description: Goal description
        priority: Goal priority
        provider: AI provider (optional)

    Returns:
        List of TaskNode objects
    """
    graph = GoalTaskGraph(provider=provider)
    goal = GoalDefinition(
        id=f"goal_{uuid.uuid4().hex[:8]}",
        description=description,
        priority=priority,
    )
    return graph.decompose_goal(goal)
