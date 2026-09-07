"""Tests for Phase 4 feedback and continuous learning pipeline."""

import pytest
from uuid import UUID

from src.feedback.feedback_model import Feedback
from src.feedback.feedback_service import FeedbackService
from src.feedback.feedback_repository import FeedbackRepository
from src.datasets.dataset_model import DatasetSample
from src.datasets.dataset_service import DatasetService
from src.datasets.dataset_builder import DatasetBuilder
from src.mlops.experiment import Experiment, TrainingJob
from src.mlops.trainer import Trainer
from src.mlops.evaluator import Evaluator
from src.mlops.model_registry import ModelRegistry, RegisteredModel


def test_feedback_model_creation():
    """Test Feedback dataclass can be created."""
    f = Feedback(
        feedback_id="12345678-1234-5678-1234-567812345678",
        task_id="task-1",
        human_label="positive",
        score=0.95,
    )
    assert f.feedback_id is not None
    assert f.task_id == "task-1"
    assert f.human_label == "positive"
    assert f.score == 0.95


def test_feedback_service():
    """Test FeedbackService submit and retrieve."""
    service = FeedbackService()
    feedback = service.submit_feedback(
        task_id="task-1",
        human_label="positive",
        score=0.9,
    )
    assert feedback.task_id == "task-1"
    assert feedback.human_label == "positive"
    assert feedback.score == 0.9
    assert feedback.feedback_id is not None


def test_feedback_repository():
    """Test FeedbackRepository save and find."""
    repo = FeedbackRepository()
    feedback = Feedback(
        feedback_id="12345678-1234-5678-1234-567812345678",
        task_id="task-1",
        human_label="positive",
    )
    repo.save(feedback)
    
    found = repo.find_by_task_id("task-1")
    assert len(found) == 1
    assert found[0].human_label == "positive"


def test_dataset_model():
    """Test DatasetSample creation."""
    sample = DatasetSample(
        input="test input",
        context="test context",
        output="test output",
        label="positive",
        quality_score=0.9,
    )
    assert sample.input == "test input"
    assert sample.label == "positive"
    assert sample.quality_score == 0.9


def test_dataset_service():
    """Test DatasetService add and all_samples."""
    service = DatasetService()
    sample = DatasetSample(input="test", label="pos", quality_score=0.9)
    service.add_sample(sample)
    samples = service.all_samples()
    assert len(samples) == 1
    assert samples[0].input == "test"


def test_dataset_builder():
    """Test DatasetBuilder build_from_feedback."""
    builder = DatasetBuilder()
    from src.feedback.feedback_model import Feedback
    feedback = Feedback(
        feedback_id="f-1",
        task_id="t-1",
        input_context="input",
        ai_output="output",
        human_label="positive",
        score=0.9,
    )
    samples = builder.build_from_feedback([feedback])
    assert len(samples) == 1
    assert samples[0].label == "positive"
    assert samples[0].quality_score == 0.9


def test_experiment():
    """Test Experiment creation."""
    exp = Experiment(name="test_exp", description="Test experiment")
    assert exp.name == "test_exp"
    assert exp.version == "v1"
    assert exp.experiment_id is not None


def test_training_job():
    """Test TrainingJob run."""
    exp = Experiment(name="test_exp")
    job = TrainingJob(exp)
    result = job.run()
    assert result["status"] == "completed"
    assert "model_version" in result


def test_trainer():
    """Test Trainer train."""
    exp = Experiment(name="test_exp")
    trainer = Trainer()
    result = trainer.train(exp)
    assert result["training_status"] == "completed"
    assert "metrics" in result


def test_evaluator():
    """Test Evaluator evaluate."""
    exp = Experiment(name="test_exp")
    evaluator = Evaluator()
    metrics = evaluator.evaluate(exp)
    assert "accuracy" in metrics
    assert "task_success_rate" in metrics
    assert "human_score" in metrics
    assert "execution_quality" in metrics


def test_model_registry():
    """Test ModelRegistry register and get."""
    registry = ModelRegistry()
    model = RegisteredModel(version="v1", metrics={"accuracy": 0.9})
    registry.register("v1", {"accuracy": 0.9})
    
    found = registry.get("v1")
    assert found is not None
    assert found.version == "v1"
    assert found.metrics["accuracy"] == 0.9


def test_model_registry_list_versions():
    """Test ModelRegistry list_versions."""
    registry = ModelRegistry()
    registry.register("v1", {"accuracy": 0.9})
    registry.register("v2", {"accuracy": 0.85})
    
    versions = registry.list_versions()
    assert "v1" in versions
    assert "v2" in versions