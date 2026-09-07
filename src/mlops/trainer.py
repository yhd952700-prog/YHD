"""MLOps trainer module for the Phase 4 feedback and continuous learning pipeline."""

from typing import Any, Dict
from .experiment import Experiment, TrainingJob


class Trainer:
    """Trainer that executes a TrainingJob and returns results."""
    
    def train(self, experiment: Experiment) -> Dict[str, Any]:
        """Run the training job for the given experiment."""
        job = TrainingJob(experiment)
        result = job.run()
        return {
            "experiment_id": experiment.experiment_id,
            "name": experiment.name,
            "training_status": job.status,
            "model_version": result["model_version"],
            "metrics": result["metrics"],
        }