"""Dataset builder for the Phase 4 feedback and continuous learning pipeline."""

from typing import List, Optional

from .dataset_model import DatasetSample
from .dataset_service import DatasetService


class DatasetBuilder:
    """Builder for creating datasets from feedback records."""

    def __init__(self, dataset_service: Optional[DatasetService] = None):
        self._dataset_service = dataset_service or DatasetService()

    def build_from_feedback(self, feedback_samples: List[...]) -> List[DatasetSample]:
        """Build dataset samples from a list of feedback records."""
        samples: List[DatasetSample] = []
        for feedback in feedback_samples:
            sample = DatasetSample(
                input=feedback.input_context,
                context=feedback.ai_output,
                output=feedback.ai_output,
                label=feedback.human_label,
                quality_score=feedback.score,
                created_at=feedback.created_at,
            )
            self._dataset_service.add_sample(sample)
            samples.append(sample)
        return samples

    def build_all_from_repository(self, repository) -> List[DatasetSample]:
        """Build dataset samples from all feedback in a repository."""
        all_feedback = repository.all()
        return self.build_from_feedback(all_feedback)
