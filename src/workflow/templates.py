"""Workflow templates for common automation patterns."""

from typing import Any, Dict, List, Optional


# Standard workflow templates

SALES_ONBOARDING_WORKFLOW = {
    "steps": [
        {"name": "setup_resources", "kind": "task"},
        {"name": "configure_permissions", "kind": "task"},
        {"name": "train_system", "kind": "task"},
        {"name": " go_live", "kind": "task"},
    ],
    "description": "Sales team onboarding workflow",
}

PROJECT_KICKOFF_WORKFLOW = {
    "steps": [
        {"name": "define_scope", "kind": "task"},
        {"name": "assign_team", "kind": "task"},
        {"name": "setup_repo", "kind": "task"},
        {"name": "initial_sync", "kind": "task"},
    ],
    "description": "Project kickoff workflow",
}

ERROR_HANDLING_WORKFLOW = {
    "steps": [
        {"name": "log_error", "kind": "task"},
        {"name": "notify_team", "kind": "task"},
        {"name": "root_cause_analysis", "kind": "task"},
        {"name": "implement_fix", "kind": "task"},
        {"name": "verify_fix", "kind": "task"},
    ],
    "description": "Error handling and remediation workflow",
}


def get_workflow(name: str = "sales_onboarding") -> Dict[str, Any]:
    """Get a workflow template by name."""
    templates = {
        "sales_onboarding": SALES_ONBOARDING_WORKFLOW,
        "project_kickoff": PROJECT_KICKOFF_WORKFLOW,
        "error_handling": ERROR_HANDLING_WORKFLOW,
    }
    return templates.get(name, SALES_ONBOARDING_WORKFLOW)