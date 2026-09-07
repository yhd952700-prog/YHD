"""Models and data structures for task and task result."""
from dataclasses import field

from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from uuid import uuid4


@dataclass
class Task:
    """Task model for workflow execution."""
    id: str = field(default_factory=lambda: str(uuid4()))
    title: str = ""
    description: str = ""
    status: str = "pending"
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = ""


@dataclass
class TaskResult:
    """Result of task execution."""
    success: bool
    output: Dict[str, Any]
    metadata: Dict[str, Any] = field(default_factory=dict)
from enum import Enum

class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
