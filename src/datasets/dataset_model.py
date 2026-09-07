"""Dataset model for the Phase 4 feedback and continuous learning pipeline."""

from dataclasses import dataclass, field
from typing import Optional
from uuid import uuid4


@dataclass
class DatasetSample:
    """A single training sample derived from feedback."""
    sample_id: str = field(default_factory=lambda: str(uuid4()))
    input: Optional[str] = None
    context: Optional[str] = None
    output: Optional[str] = None
    label: Optional[str] = None
    quality_score: Optional[float] = None
    created_at: Optional[str] = None