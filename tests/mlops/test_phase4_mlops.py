"""Tests for Phase 4 MLOps pipeline."""

from src.mlops.experiment import Experiment, TrainingJob
from src.mlops.trainer import Trainer
from src.mlops.evaluator import Evaluator
from src.mlops.model_registry import ModelRegistry, RegisteredModel


def test_experiment_creation():
    """Test Experiment can be created."""
    exp = Experiment(name="test", description="test desc")
    assert exp.name == "test"
    assert exp.version == "v1"


def test_training_job_runs():
    """Test TrainingJob returns completion marker."""
    exp = Experiment(name="test")
    job = TrainingJob(exp)
    result = job.run()
    assert result["status"] == "completed"


def test_trainer_train():
    """Test Trainer returns training result."""
    exp = Experiment(name="test")
    trainer = Trainer()
    result = trainer.train(exp)
    assert result["training_status"] == "completed"


def test_evaluator_metrics():
    """Test Evaluator returns deterministic metrics."""
    exp = Experiment(name="test")
    evaluator = Evaluator()
    metrics = evaluator.evaluate(exp)
    assert metrics["accuracy"] == 0.85
    assert metrics["task_success_rate"] == 0.78
    assert metrics["human_score"] == 0.82
    assert metrics["execution_quality"] == 0.80


def test_model_registry_operations():
    """Test ModelRegistry register, get, and list operations."""
    registry = ModelRegistry()
    
    # Register versions
    registry.register("v1", {"accuracy": 0.9})
    registry.register("v2", {"accuracy": 0.85})
    
    # Get by version
    found_v1 = registry.get("v1")
    assert found_v1 is not None
    assert found_v1.version == "v1"
    assert found_v1.metrics["accuracy"] == 0.9
    
    found_v2 = registry.get("v2")
    assert found_v2 is not None
    assert found_v2.version == "v2"
    assert found_v2.metrics["accuracy"] == 0.85
    
    # List versions
    versions = registry.list_versions()
    assert "v1" in versions
    assert "v2" in versions
    assert len(versions) == 2