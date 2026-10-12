"""
Todo/Task Management Plugin for LiuHao AI OS

Provides task CRUD, priority management, due dates, category/tag support,
progress tracking, and task dependencies.

Capabilities:
- create_task: Create a new task
- update_task: Update an existing task
- delete_task: Delete a task
- get_task: Get a task by ID
- list_tasks: List tasks with filters
- set_priority: Set task priority
- set_due_date: Set task due date
- add_tag: Add a tag to a task
- mark_complete: Mark task as complete
- get_progress: Get task progress stats
- add_dependency: Add task dependency
"""

import uuid
from typing import Dict, Any, Optional, List, Set
from dataclasses import dataclass, field
from datetime import datetime, date

from ..plugin_base import (
    BasePlugin,
    PluginMetadata,
    PluginStatus,
    PluginExecutionError,
)


@dataclass
class Task:
    """A task item."""
    id: str
    title: str
    description: str = ""
    priority: int = 2  # 0=low, 1=medium, 2=high, 3=urgent
    status: str = "pending"  # pending, in_progress, completed, cancelled
    due_date: Optional[datetime] = None
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None
    tags: List[str] = field(default_factory=list)
    category: str = ""
    dependencies: List[str] = field(default_factory=list)  # task IDs
    estimated_minutes: int = 0
    spent_minutes: int = 0
    notes: str = ""
    assignee: str = ""


class TodoPlugin(BasePlugin):
    """Todo/Task management plugin.

    Supports task CRUD, priorities, due dates, tags/categories,
    progress tracking, and task dependencies.

    Example:
        plugin = TodoPlugin()
        plugin.initialize()
        plugin.activate()

        task = plugin.create_task(title="Write report", priority=2)
        plugin.mark_complete(task.id)
    """

    PRIORITY_LOW = 0
    PRIORITY_MEDIUM = 1
    PRIORITY_HIGH = 2
    PRIORITY_URGENT = 3

    PRIORITY_LABELS = {
        0: "Low",
        1: "Medium",
        2: "High",
        3: "Urgent",
    }

    def __init__(self) -> None:
        """Initialize the todo plugin."""
        metadata = PluginMetadata(
            name="todo",
            version="1.0.0",
            description="Task and todo management plugin",
            author="LiuHao AI OS Team",
            categories=["productivity", "tasks", "utility"],
            keywords=["todo", "task", "gtd", "productivity", "reminder"],
            requires_internet=False,
            dependencies=[],
        )
        super().__init__(metadata)

        self._tasks: Dict[str, Task] = {}

    # ==================== Lifecycle Methods ====================

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """Initialize the todo plugin."""
        try:
            self._set_status(PluginStatus.INITIALIZED)
            return True
        except Exception as e:
            self._set_error(f"Initialization failed: {e}")
            return False

    def activate(self) -> bool:
        """Activate the todo plugin."""
        try:
            self._set_status(PluginStatus.ACTIVE)
            return True
        except Exception as e:
            self._set_error(f"Activation failed: {e}")
            return False

    def deactivate(self) -> bool:
        """Deactivate the todo plugin."""
        try:
            self._set_status(PluginStatus.INACTIVE)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def shutdown(self) -> bool:
        """Shut down the todo plugin."""
        try:
            self._tasks.clear()
            self._set_status(PluginStatus.SHUTDOWN)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def get_capabilities(self) -> List[str]:
        """Return the list of capabilities."""
        return [
            "create_task",
            "update_task",
            "delete_task",
            "get_task",
            "list_tasks",
            "set_priority",
            "set_due_date",
            "add_tag",
            "remove_tag",
            "mark_complete",
            "mark_in_progress",
            "get_progress",
            "add_dependency",
            "remove_dependency",
            "get_overdue_tasks",
            "get_stats",
        ]

    # ==================== Task CRUD ====================

    def create_task(
        self,
        title: str,
        description: str = "",
        priority: int = 1,
        due_date: Optional[datetime] = None,
        tags: Optional[List[str]] = None,
        category: str = "",
        estimated_minutes: int = 0,
        assignee: str = "",
    ) -> Task:
        """Create a new task.

        Args:
            title: Task title.
            description: Task description.
            priority: Priority level (0-3).
            due_date: Due date/time.
            tags: List of tag strings.
            category: Category name.
            estimated_minutes: Estimated time in minutes.
            assignee: Person assigned.

        Returns:
            The created Task.
        """
        self._ensure_active()
        self._increment_call_count()

        task_id = str(uuid.uuid4())[:8]

        task = Task(
            id=task_id,
            title=title,
            description=description,
            priority=max(0, min(3, priority)),
            due_date=due_date,
            tags=tags or [],
            category=category,
            estimated_minutes=estimated_minutes,
            assignee=assignee,
        )

        self._tasks[task_id] = task
        return task

    def update_task(self, task_id: str, **kwargs: Any) -> Optional[Task]:
        """Update a task.

        Args:
            task_id: Task ID.
            **kwargs: Fields to update.

        Returns:
            Updated Task or None.
        """
        self._ensure_active()
        self._increment_call_count()

        task = self._tasks.get(task_id)
        if not task:
            return None

        for key, value in kwargs.items():
            if hasattr(task, key) and key != "id":
                setattr(task, key, value)

        task.updated_at = datetime.now()
        return task

    def delete_task(self, task_id: str) -> bool:
        """Delete a task.

        Args:
            task_id: Task ID.

        Returns:
            True if task was found and deleted.
        """
        self._ensure_active()
        self._increment_call_count()

        if task_id in self._tasks:
            # Remove this task as a dependency from other tasks
            for other in self._tasks.values():
                if task_id in other.dependencies:
                    other.dependencies.remove(task_id)

            del self._tasks[task_id]
            return True
        return False

    def get_task(self, task_id: str) -> Optional[Task]:
        """Get a task by ID."""
        self._ensure_active()
        return self._tasks.get(task_id)

    def list_tasks(
        self,
        status: Optional[str] = None,
        priority: Optional[int] = None,
        category: Optional[str] = None,
        tag: Optional[str] = None,
        assignee: Optional[str] = None,
        due_before: Optional[datetime] = None,
        due_after: Optional[datetime] = None,
        sort_by: str = "priority",
        sort_desc: bool = True,
    ) -> List[Task]:
        """List tasks with optional filters.

        Args:
            status: Filter by status.
            priority: Filter by priority.
            category: Filter by category.
            tag: Filter by tag.
            assignee: Filter by assignee.
            due_before: Filter tasks due before this time.
            due_after: Filter tasks due after this time.
            sort_by: Sort field (priority, due_date, created_at, title).
            sort_desc: Sort descending.

        Returns:
            List of matching Task objects.
        """
        self._ensure_active()
        self._increment_call_count()

        tasks = list(self._tasks.values())

        if status:
            tasks = [t for t in tasks if t.status == status]

        if priority is not None:
            tasks = [t for t in tasks if t.priority == priority]

        if category:
            tasks = [t for t in tasks if t.category == category]

        if tag:
            tasks = [t for t in tasks if tag in t.tags]

        if assignee:
            tasks = [t for t in tasks if t.assignee == assignee]

        if due_before:
            tasks = [t for t in tasks if t.due_date and t.due_date <= due_before]

        if due_after:
            tasks = [t for t in tasks if t.due_date and t.due_date >= due_after]

        # Sort
        sort_key_map = {
            "priority": lambda t: t.priority,
            "due_date": lambda t: t.due_date or datetime.max,
            "created_at": lambda t: t.created_at,
            "title": lambda t: t.title.lower(),
            "status": lambda t: t.status,
        }
        key_func = sort_key_map.get(sort_by, lambda t: t.priority)
        tasks.sort(key=key_func, reverse=sort_desc)

        return tasks

    # ==================== Task Operations ====================

    def set_priority(self, task_id: str, priority: int) -> bool:
        """Set task priority.

        Args:
            task_id: Task ID.
            priority: New priority level (0-3).

        Returns:
            True if task was found and updated.
        """
        task = self._tasks.get(task_id)
        if not task:
            return False
        task.priority = max(0, min(3, priority))
        task.updated_at = datetime.now()
        return True

    def set_due_date(self, task_id: str, due_date: Optional[datetime]) -> bool:
        """Set task due date.

        Args:
            task_id: Task ID.
            due_date: New due date (None to remove).

        Returns:
            True if task was found and updated.
        """
        task = self._tasks.get(task_id)
        if not task:
            return False
        task.due_date = due_date
        task.updated_at = datetime.now()
        return True

    def add_tag(self, task_id: str, tag: str) -> bool:
        """Add a tag to a task.

        Args:
            task_id: Task ID.
            tag: Tag string.

        Returns:
            True if tag was added.
        """
        task = self._tasks.get(task_id)
        if not task:
            return False
        if tag not in task.tags:
            task.tags.append(tag)
            task.updated_at = datetime.now()
        return True

    def remove_tag(self, task_id: str, tag: str) -> bool:
        """Remove a tag from a task."""
        task = self._tasks.get(task_id)
        if not task or tag not in task.tags:
            return False
        task.tags.remove(tag)
        task.updated_at = datetime.now()
        return True

    def mark_complete(self, task_id: str) -> bool:
        """Mark a task as completed.

        Args:
            task_id: Task ID.

        Returns:
            True if task was found and updated.
        """
        task = self._tasks.get(task_id)
        if not task:
            return False
        task.status = "completed"
        task.completed_at = datetime.now()
        task.updated_at = datetime.now()
        return True

    def mark_in_progress(self, task_id: str) -> bool:
        """Mark a task as in progress."""
        task = self._tasks.get(task_id)
        if not task:
            return False
        task.status = "in_progress"
        task.updated_at = datetime.now()
        return True

    # ==================== Dependencies ====================

    def add_dependency(self, task_id: str, depends_on_id: str) -> bool:
        """Add a dependency: task depends on another task.

        Args:
            task_id: The task that depends on another.
            depends_on_id: The task it depends on.

        Returns:
            True if dependency was added.
        """
        task = self._tasks.get(task_id)
        dep_task = self._tasks.get(depends_on_id)

        if not task or not dep_task:
            return False

        if task_id == depends_on_id:
            return False  # Can't depend on itself

        if depends_on_id not in task.dependencies:
            task.dependencies.append(depends_on_id)
            task.updated_at = datetime.now()

        return True

    def remove_dependency(self, task_id: str, depends_on_id: str) -> bool:
        """Remove a task dependency."""
        task = self._tasks.get(task_id)
        if not task or depends_on_id not in task.dependencies:
            return False
        task.dependencies.remove(depends_on_id)
        task.updated_at = datetime.now()
        return True

    def get_dependency_chain(self, task_id: str) -> List[str]:
        """Get the full dependency chain for a task (recursive).

        Returns list of task IDs in dependency order.
        """
        if task_id not in self._tasks:
            return []

        visited: Set[str] = set()
        chain: List[str] = []

        def dfs(tid: str) -> None:
            if tid in visited:
                return
            visited.add(tid)
            task = self._tasks.get(tid)
            if task:
                for dep in task.dependencies:
                    dfs(dep)
                chain.append(tid)

        dfs(task_id)
        return chain

    def can_start(self, task_id: str) -> bool:
        """Check if a task can start (all dependencies completed)."""
        task = self._tasks.get(task_id)
        if not task:
            return False

        for dep_id in task.dependencies:
            dep = self._tasks.get(dep_id)
            if not dep or dep.status != "completed":
                return False
        return True

    # ==================== Progress & Stats ====================

    def get_progress(self, category: Optional[str] = None) -> Dict[str, Any]:
        """Get task progress statistics.

        Args:
            category: Optional category filter.

        Returns:
            Dict with total, completed, pending, percentage, etc.
        """
        self._ensure_active()

        tasks = list(self._tasks.values())
        if category:
            tasks = [t for t in tasks if t.category == category]

        total = len(tasks)
        completed = sum(1 for t in tasks if t.status == "completed")
        in_progress = sum(1 for t in tasks if t.status == "in_progress")
        pending = sum(1 for t in tasks if t.status == "pending")
        cancelled = sum(1 for t in tasks if t.status == "cancelled")

        completion_rate = completed / total if total > 0 else 0.0

        # Priority breakdown
        priority_counts = {0: 0, 1: 0, 2: 0, 3: 0}
        for t in tasks:
            priority_counts[t.priority] = priority_counts.get(t.priority, 0) + 1

        # Time tracking
        total_estimated = sum(t.estimated_minutes for t in tasks)
        total_spent = sum(t.spent_minutes for t in tasks)

        return {
            "total": total,
            "completed": completed,
            "in_progress": in_progress,
            "pending": pending,
            "cancelled": cancelled,
            "completion_rate": round(completion_rate, 3),
            "priority_breakdown": {
                self.PRIORITY_LABELS.get(k, str(k)): v
                for k, v in priority_counts.items()
            },
            "total_estimated_minutes": total_estimated,
            "total_spent_minutes": total_spent,
            "remaining_estimate": max(0, total_estimated - total_spent),
        }

    def get_overdue_tasks(self) -> List[Task]:
        """Get tasks that are past their due date and not completed."""
        self._ensure_active()
        now = datetime.now()
        return [
            t for t in self._tasks.values()
            if t.due_date and t.due_date < now
            and t.status not in ("completed", "cancelled")
        ]

    def get_stats(self) -> Dict[str, Any]:
        """Get overall todo statistics."""
        progress = self.get_progress()
        return {
            **progress,
            "total_tags": len(self._get_all_tags()),
            "total_categories": len(self._get_all_categories()),
        }

    def _get_all_tags(self) -> Set[str]:
        """Get all unique tags across tasks."""
        tags: Set[str] = set()
        for task in self._tasks.values():
            tags.update(task.tags)
        return tags

    def _get_all_categories(self) -> Set[str]:
        """Get all unique categories."""
        return {t.category for t in self._tasks.values() if t.category}
