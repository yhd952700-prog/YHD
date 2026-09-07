"""Workflow automation engine for Phase 3.

Exports the core components: WorkflowEngine, EventBus, StateMachine,
WorkflowTask, WorkflowRun, WorkflowInstance, and template functions.
"""

from .workflow import WorkflowEngine  # noqa: F401
from .event_bus import EventBus  # noqa: F401
from .state_machine import StateMachine  # noqa: F401
from .models import WorkflowTask, WorkflowRun, WorkflowInstance  # noqa: F401
from .templates import get_workflow, SALES_ONBOARDING_WORKFLOW, PROJECT_KICKOFF_WORKFLOW, ERROR_HANDLING_WORKFLOW  # noqa: F401
