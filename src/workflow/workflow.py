"""Workflow execution primitives and workflow engine for the automation demo."""

from dataclasses import dataclass
from typing import Any, Dict, Optional
from uuid import uuid4

from src.tasks.models import Task, TaskResult


@dataclass
class WorkflowTask:
    """Task description used by the workflow engine."""
    title: str
    description: str = ""
    worker: str = "worker"


@dataclass
class WorkflowStep:
    """Workflow step placeholder for tests and future execution order."""
    name: str
    kind: str = "task"


class WorkflowEngine:
    """Small additive PLACEHOLDER engine for the automation demo.

    It performs NO real execution, writes NO audit row and runs NO security
    check. Whatever it returns must say so explicitly: a control that did not
    run must never be reported as having run.
    """

    def __init__(self, event_bus: Optional[object] = None):
        self.event_bus = event_bus or object()

    def subscribe(self, event_type: str, handler) -> None:
        pass

    def execute_task(self, task: Task, worker: str = "worker") -> TaskResult:
        """PLACEHOLDER: does not execute the task, and says so honestly.

        Historically this returned ``TaskResult(success=True)`` and stamped
        ``audit_written=True`` / ``security_status="passed"`` while executing
        nothing -- a false claim that a control had run. It now reports the
        truth: no execution, no audit write, no security check.
        """
        task.metadata.setdefault("workflow_id", str(uuid4()))
        task.metadata["worker"] = worker
        # Explicit, honest state: this is a stub and no control has run.
        task.metadata["stub"] = True
        task.metadata["executed"] = False
        task.metadata["audit_written"] = False
        task.metadata["security_status"] = "not_checked"

        return TaskResult(
            success=False,
            output={"task_id": str(task.id), "worker": worker, "executed": False},
            metadata=task.metadata,
        )

    def execute_workflow(self, workflow: Dict[str, Any], task: Task) -> Dict[str, Any]:
        """PLACEHOLDER: run an in-memory workflow template without executing it.

        Delegates to the stub :meth:`execute_task`; no task runs and no
        audit/security control fires. The returned metadata says so explicitly
        instead of claiming a passed security check.
        """
        steps = workflow.get("steps", [])
        worker = steps[2].get("name", "worker") if len(steps) >= 3 else "worker"
        self.execute_task(task, worker=worker)

        return {
            "task_status": task.status,
            "metadata": {
                "stub": True,
                "executed": False,
                "audit_written": False,
                "security_status": "not_checked",
            },
        }
