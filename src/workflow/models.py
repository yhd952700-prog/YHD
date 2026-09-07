"""Models and data structures for workflow management."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from uuid import uuid4


@dataclass
class WorkflowTask:
    """Task description used by the workflow engine."""
    title: str
    description: str = ""
    worker: str = "worker"
    id: str = field(default_factory=lambda: str(uuid4()))
    status: str = "pending"
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkflowRun:
    """A single execution of a workflow."""
    id: str = field(default_factory=lambda: str(uuid4()))
    workflow_name: str = ""
    steps_completed: int = 0
    total_steps: int = 0
    status: str = "pending"
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkflowInstance:
    """A running workflow instance."""
    id: str = field(default_factory=lambda: str(uuid4()))
    name: str = ""
    current_step: int = 0
    total_steps: int = 0
    status: str = "pending"
    task: Optional[WorkflowTask] = None
    metadata: Dict[str, Any] = field(default_factory=dict)