"""Feedback repository for the Phase 4 feedback and continuous learning pipeline."""

from typing import List
from uuid import UUID

from .feedback_model import Feedback


class FeedbackRepository:
    """Repository for persency and querying of feedback records."""

    def __init__(self):
        self._store: dict[UUID, Feedback] = {}

    def save(self, feedback: Feedback) -> None:
        """Save a feedback record."""
        self._store[UUID(feedback.feedback_id)] = feedback

    def find_by_task_id(self, task_id: str) -> List[Feedback]:
        """Find feedback records by task ID."""
        return [
            f for f in self._store.values()
            if f.task_id == task_id
        ]

    def find_by_workflow_id(self, workflow_id: str) -> List[Feedback]:
        """Find feedback records by workflow ID."""
        return [
            f for f in self._store.values()
            if f.workflow_id == workflow_id
        ]

    def find_by_agent_id(self, agent_id: str) -> List[Feedback]:
        """Find feedback records by agent ID."""
        return [
            f for f in self._store.values()
            if f.agent_id == agent_id
        ]

    def all(self) -> List[Feedback]:
        """Return all feedback records."""
        return list(self._store.values())
