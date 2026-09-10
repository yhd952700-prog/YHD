"""Feedback API routes for the Phase 4 feedback and continuous learning pipeline."""

from typing import Dict, List, Optional
from fastapi import APIRouter, HTTPException

from .feedback_model import Feedback
from .feedback_service import FeedbackService
from .feedback_repository import FeedbackRepository

router = APIRouter(prefix="/feedback", tags=["feedback"])

feedback_service = FeedbackService()
feedback_repository = FeedbackRepository()


@router.post("/", response_model=Dict[str, str])
async def submit_feedback(
    task_id: Optional[str] = None,
    workflow_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    input_context: Optional[str] = None,
    ai_output: Optional[str] = None,
    human_label: Optional[str] = None,
    score: Optional[float] = None,
) -> Dict[str, str]:
    """Submit a new feedback record."""
    feedback = feedback_service.submit_feedback(
        task_id=task_id,
        workflow_id=workflow_id,
        agent_id=agent_id,
        input_context=input_context,
        ai_output=ai_output,
        human_label=human_label,
        score=score,
    )
    feedback_repository.save(feedback)
    return {"feedback_id": feedback.feedback_id}


@router.get("/{feedback_id}", response_model=Feedback)
async def get_feedback(feedback_id: str) -> Feedback:
    """Retrieve a feedback record by ID."""
    feedback = feedback_repository.get_feedback(feedback_id)
    if feedback is None:
        raise HTTPException(status_code=404, detail="Feedback not found")
    return feedback


@router.get("/", response_model=List[Feedback])
async def list_feedback(
    agent_id: Optional[str] = None,
    task_id: Optional[str] = None,
    workflow_id: Optional[str] = None,
) -> List[Feedback]:
    """List feedback records with optional filtering."""
    all_feedback = feedback_repository.all()

    filtered = []
    for f in all_feedback:
        if agent_id and f.agent_id != agent_id:
            continue
        if task_id and f.task_id != task_id:
            continue
        if workflow_id and f.workflow_id != workflow_id:
            continue
        filtered.append(f)

    return filtered


@router.put("/{feedback_id}/score", response_model=Dict[str, str])
async def update_feedback_score(feedback_id: str, score: float) -> Dict[str, str]:
    """Update the score of a feedback record."""
    success = feedback_repository.update_feedback_score(feedback_id, score)
    if not success:
        raise HTTPException(status_code=404, detail="Feedback not found")
    return {"status": "updated"}
