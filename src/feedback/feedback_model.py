"""Feedback model for the Phase 4 feedback and continuous learning pipeline."""

from dataclasses import dataclass, field
from typing import Optional
from uuid import uuid4


@dataclass
class Feedback:
    """Feedback record collected from task and workflow outputs."""
    feedback_id: str = field(default_factory=lambda: str(uuid4()))
    task_id: Optional[str] = None
    workflow_id: Optional[str] = None
    agent_id: Optional[str] = None
    input_context: Optional[str] = None
    ai_output: Optional[str] = None
    human_label: Optional[str] = None
    score: Optional[float] = None
    created_at: Optional[str] = None
