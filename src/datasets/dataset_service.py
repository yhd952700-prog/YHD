"""Dataset service for the Phase 4 feedback and continuous learning pipeline."""

from typing import List, Optional
from uuid import UUID

from .dataset_model import DatasetSample


class DatasetService:
    """Service for managing dataset samples derived from feedback."""
    
    def __init__(self):
        self._samples: dict[UUID, DatasetSample] = {}
    
    def add_sample(self, sample: DatasetSample) -> None:
        """Add a dataset sample."""
        self._samples[UUID(sample.sample_id)] = sample
    
    def from_feedback(self, feedback: ...) -> DatasetSample:
        """Convert a feedback object into a training sample."""
        sample = DatasetSample(
            input=feedback.input_context,
            context=feedback.ai_output,
            output=feedback.ai_output,
            label=feedback.human_label,
            quality_score=feedback.score,
            created_at=feedback.created_at,
        )
        self.add_sample(sample)
        return sample
    
    def all_samples(self) -> List[DatasetSample]:
        """Return all dataset samples."""
        return list(self._samples.values())