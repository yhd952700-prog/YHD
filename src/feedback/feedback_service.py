"""Feedback service for the Phase 4 feedback and continuous learning pipeline."""

from typing import List, Optional
from uuid import uuid4

from .feedback_model import Feedback


class FeedbackService:
    """Service for collecting and managing feedback records."""
    
    def __init__(self):
        self._feedback_store: dict[str, Feedback] = {}
    
    def submit_feedback(self, task_id: Optional[str] = None, 
                       workflow_id: Optional[str] = None,
                       agent_id: Optional[str] = None,
                       input_context: Optional[str] = None,
                       ai_output: Optional[str] = None,
                       human_label: Optional[str] = None,
                       score: Optional[float] = None) -> Feedback:
        """Submit a new feedback record."""
        feedback = Feedback(
            feedback_id=str(uuid4()),
            task_id=task_id,
            workflow_id=workflow_id,
            agent_id=agent_id,
            input_context=input_context,
            ai_output=ai_output,
            human_label=human_label,
            score=score,
            created_at=self._now_iso()
        )
        self._feedback_store[feedback.feedback_id] = feedback
        return feedback
    
    def get_feedback(self, feedback_id: str) -> Optional[Feedback]:
        """Retrieve a feedback record by ID."""
        return self._feedback_store.get(feedback_id)
    
    def update_feedback_score(self, feedback_id: str, score: float) -> bool:
        """Update the score of an existing feedback record."""
        feedback = self._feedback_store.get(feedback_id)
        if feedback:
            feedback.score = score
            return True
        return False
    
    def _now_iso(self) -> str:
        """Return current ISO format timestamp."""
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat()