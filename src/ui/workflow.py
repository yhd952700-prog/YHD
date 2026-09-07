"""Task workflow console for the product console."""

from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional
from enum import Enum
from datetime import datetime


class WorkflowStatus(Enum):
    """Workflow execution status."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class TaskWorkflowConsole:
    """Console for monitoring and managing task workflows."""
    
    workflows: Dict[str, Any] = field(default_factory=dict)
    completed_count: int = 0
    failed_count: int = 0
    running_count: int = 0
    total_count: int = 0
    
    def add_workflow(self, workflow_id: str, status: Any = WorkflowStatus.PENDING) -> None:
        """Add a workflow to monitoring."""
        if isinstance(status, str):
            status_value = status
        else:
            status_value = status.value
        self.workflows[workflow_id] = {"status": status_value, "created": datetime.now().isoformat()}
        self.total_count += 1
    
    def update_workflow(self, workflow_id: str, status: WorkflowStatus) -> None:
        """Update workflow status."""
        if workflow_id in self.workflows:
            self.workflows[workflow_id]["status"] = status.value
            # Update counters (simple version)
    
    def get_workflow(self, workflow_id: str) -> Optional[Dict[str, Any]]:
        """Get workflow status by ID."""
        return self.workflows.get(workflow_id)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert workflow console to dictionary for UI rendering."""
        return {
            "workflows": self.workflows,
            "completed_count": self.completed_count,
            "failed_count": self.failed_count,
            "running_count": self.running_count,
            "total_count": self.total_count,
        }