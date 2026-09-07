"""Workflow automation engine for Phase 3.

Exports the core components: WorkflowEngine, EventBus, StateMachine, 
WorkflowTask, WorkflowRun, WorkflowInstance, and template functions.
"""

from .workflow import WorkflowEngine
from .event_bus import EventBus
from .state_machine import StateMachine
from .models import WorkflowTask, WorkflowRun, WorkflowInstance
from .templates import get_workflow, SALES_ONBOARDING_WORKFLOW, PROJECT_KICKOFF_WORKFLOW, ERROR_HANDLING_WORKFLOW