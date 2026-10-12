"""Experiment Tracker for MLOps.

Provides experiment tracking with:
- Experiment creation and management
- Hyperparameter logging
- Metric tracking over time
- Artifact management
- Experiment comparison
- Run nesting
- Tagging and filtering
- Experiment reproduction
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union
from uuid import uuid4


class RunStatus(str, Enum):
    """Experiment run status."""

    CREATED = "created"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    KILLED = "killed"
    CRASHED = "crashed"


class ArtifactType(str, Enum):
    """Types of experiment artifacts."""

    MODEL = "model"
    DATASET = "dataset"
    CONFIG = "config"
    CHECKPOINT = "checkpoint"
    LOG = "log"
    IMAGE = "image"
    PLOT = "plot"
    METRICS = "metrics"
    OTHER = "other"


@dataclass
class Artifact:
    """An experiment artifact.

    Attributes:
        artifact_id: Unique artifact identifier
        name: Artifact name
        artifact_type: Type of artifact
        path: Path to the artifact
        size_bytes: Size in bytes
        metadata: Additional metadata
        created_at: Creation timestamp
    """

    artifact_id: str = field(default_factory=lambda: str(uuid4()))
    name: str = ""
    artifact_type: ArtifactType = ArtifactType.OTHER
    path: str = ""
    size_bytes: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class MetricLog:
    """A single metric log entry.

    Attributes:
        metric_name: Name of the metric
        value: Metric value
        step: Training/evaluation step
        epoch: Epoch number
        timestamp: Log timestamp
    """

    metric_name: str
    value: float
    step: int = 0
    epoch: int = 0
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class ExperimentRun:
    """A single experiment run.

    Attributes:
        run_id: Unique run identifier
        experiment_id: Parent experiment ID
        name: Run name
        status: Run status
        hyperparameters: Hyperparameters for this run
        metrics: Metric history keyed by metric name
        artifacts: List of artifacts produced
        tags: Tags for categorization
        parent_run_id: Parent run ID (for nested runs)
        child_run_ids: Child run IDs
        start_time: Start timestamp
        end_time: End timestamp
        duration_seconds: Total duration
        notes: Free-form notes
        metadata: Additional metadata
    """

    run_id: str = field(default_factory=lambda: str(uuid4()))
    experiment_id: str = ""
    name: str = ""
    status: RunStatus = RunStatus.CREATED
    hyperparameters: Dict[str, Any] = field(default_factory=dict)
    metrics: Dict[str, List[MetricLog]] = field(default_factory=dict)
    artifacts: List[Artifact] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    parent_run_id: Optional[str] = None
    child_run_ids: List[str] = field(default_factory=list)
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    duration_seconds: float = 0.0
    notes: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_running(self) -> bool:
        """Whether the run is currently running."""
        return self.status == RunStatus.RUNNING

    @property
    def is_finished(self) -> bool:
        """Whether the run has finished (completed/failed/killed)."""
        return self.status in (
            RunStatus.COMPLETED,
            RunStatus.FAILED,
            RunStatus.KILLED,
            RunStatus.CRASHED,
        )

    def get_latest_metric(self, metric_name: str) -> Optional[MetricLog]:
        """Get the latest value of a metric.

        Args:
            metric_name: Metric name

        Returns:
            Latest metric log, or None if not found
        """
        logs = self.metrics.get(metric_name, [])
        if not logs:
            return None
        return logs[-1]

    def get_metric_values(self, metric_name: str) -> List[float]:
        """Get all values of a metric.

        Args:
            metric_name: Metric name

        Returns:
            List of metric values
        """
        return [log.value for log in self.metrics.get(metric_name, [])]


@dataclass
class Experiment:
    """An experiment containing multiple runs.

    Attributes:
        experiment_id: Unique experiment identifier
        name: Experiment name
        description: Experiment description
        run_ids: List of run IDs in this experiment
        tags: Tags for categorization
        created_at: Creation timestamp
        created_by: Creator identifier
        metadata: Additional metadata
    """

    experiment_id: str = field(default_factory=lambda: str(uuid4()))
    name: str = ""
    description: str = ""
    run_ids: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    created_by: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


class ExperimentTracker:
    """Tracker for experiments and runs.

    Manages experiment lifecycle, hyperparameter logging, metric tracking,
    artifact management, and experiment comparison.

    Usage:
        tracker = ExperimentTracker()
        with tracker.start_run("my-experiment") as run:
            run.log_params({"learning_rate": 0.001})
            run.log_metric("loss", 0.5, step=1)
    """

    def __init__(
        self,
        tracking_dir: Optional[str] = None,
    ) -> None:
        """Initialize the experiment tracker.

        Args:
            tracking_dir: Directory to store tracking data
        """
        self._experiments: Dict[str, Experiment] = {}
        self._runs: Dict[str, ExperimentRun] = {}
        self._active_run_id: Optional[str] = None
        self._tracking_dir = tracking_dir
        self._tag_index: Dict[str, List[str]] = {}

        if tracking_dir:
            os.makedirs(tracking_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # Experiment management
    # ------------------------------------------------------------------

    def create_experiment(
        self,
        name: str,
        *,
        description: str = "",
        tags: Optional[List[str]] = None,
        created_by: str = "",
    ) -> Experiment:
        """Create a new experiment.

        Args:
            name: Experiment name
            description: Experiment description
            tags: Tags for categorization
            created_by: Creator identifier

        Returns:
            Created experiment
        """
        experiment = Experiment(
            name=name,
            description=description,
            tags=tags or [],
            created_by=created_by,
        )
        self._experiments[experiment.experiment_id] = experiment

        for tag in experiment.tags:
            if tag not in self._tag_index:
                self._tag_index[tag] = []
            self._tag_index[tag].append(experiment.experiment_id)

        return experiment

    def get_experiment(self, experiment_id: str) -> Optional[Experiment]:
        """Get an experiment by ID.

        Args:
            experiment_id: Experiment identifier

        Returns:
            Experiment if found, None otherwise
        """
        return self._experiments.get(experiment_id)

    def get_experiment_by_name(self, name: str) -> Optional[Experiment]:
        """Get an experiment by name.

        Args:
            name: Experiment name

        Returns:
            Experiment if found, None otherwise
        """
        for exp in self._experiments.values():
            if exp.name == name:
                return exp
        return None

    def list_experiments(
        self,
        *,
        tags: Optional[List[str]] = None,
    ) -> List[Experiment]:
        """List experiments, optionally filtered by tags.

        Args:
            tags: Filter by tags (all must match)

        Returns:
            List of matching experiments
        """
        experiments = list(self._experiments.values())

        if tags:
            for tag in tags:
                exp_ids = set(self._tag_index.get(tag, []))
                experiments = [e for e in experiments if e.experiment_id in exp_ids]

        return experiments

    def delete_experiment(self, experiment_id: str) -> bool:
        """Delete an experiment and all its runs.

        Args:
            experiment_id: Experiment identifier

        Returns:
            True if deleted, False if not found
        """
        experiment = self._experiments.pop(experiment_id, None)
        if not experiment:
            return False

        # Delete all runs
        for run_id in experiment.run_ids:
            self._runs.pop(run_id, None)

        # Remove from tag index
        for tag in experiment.tags:
            if tag in self._tag_index:
                self._tag_index[tag] = [
                    eid for eid in self._tag_index[tag]
                    if eid != experiment_id
                ]

        return True

    # ------------------------------------------------------------------
    # Run management
    # ------------------------------------------------------------------

    def start_run(
        self,
        experiment_name: str,
        *,
        run_name: Optional[str] = None,
        tags: Optional[List[str]] = None,
        parent_run_id: Optional[str] = None,
    ) -> ExperimentRun:
        """Start a new run.

        Args:
            experiment_name: Name of the experiment (created if not exists)
            run_name: Name of the run
            tags: Tags for the run
            parent_run_id: Parent run ID (for nesting)

        Returns:
            The started run
        """
        # Get or create experiment
        experiment = self.get_experiment_by_name(experiment_name)
        if not experiment:
            experiment = self.create_experiment(experiment_name)

        # Create run
        run = ExperimentRun(
            experiment_id=experiment.experiment_id,
            name=run_name or f"run-{int(time.time())}",
            status=RunStatus.RUNNING,
            tags=tags or [],
            parent_run_id=parent_run_id,
            start_time=datetime.now(timezone.utc).isoformat(),
        )

        self._runs[run.run_id] = run
        experiment.run_ids.append(run.run_id)
        self._active_run_id = run.run_id

        # Register as child of parent
        if parent_run_id and parent_run_id in self._runs:
            self._runs[parent_run_id].child_run_ids.append(run.run_id)

        return run

    def end_run(
        self,
        run_id: Optional[str] = None,
        *,
        status: RunStatus = RunStatus.COMPLETED,
    ) -> Optional[ExperimentRun]:
        """End a run.

        Args:
            run_id: Run identifier (None = current active run)
            status: Final status

        Returns:
            The ended run, or None if not found
        """
        run_id = run_id or self._active_run_id
        if not run_id:
            return None

        run = self._runs.get(run_id)
        if not run:
            return None

        run.status = status
        run.end_time = datetime.now(timezone.utc).isoformat()

        if run.start_time:
            start = datetime.fromisoformat(run.start_time.replace("Z", "+00:00"))
            end = datetime.fromisoformat(run.end_time.replace("Z", "+00:00"))
            run.duration_seconds = (end - start).total_seconds()

        if run_id == self._active_run_id:
            self._active_run_id = None

        return run

    def get_run(self, run_id: str) -> Optional[ExperimentRun]:
        """Get a run by ID.

        Args:
            run_id: Run identifier

        Returns:
            Run if found, None otherwise
        """
        return self._runs.get(run_id)

    def list_runs(
        self,
        *,
        experiment_id: Optional[str] = None,
        status: Optional[RunStatus] = None,
        tags: Optional[List[str]] = None,
    ) -> List[ExperimentRun]:
        """List runs with optional filters.

        Args:
            experiment_id: Filter by experiment
            status: Filter by status
            tags: Filter by tags

        Returns:
            List of matching runs
        """
        runs = list(self._runs.values())

        if experiment_id:
            runs = [r for r in runs if r.experiment_id == experiment_id]

        if status:
            runs = [r for r in runs if r.status == status]

        if tags:
            runs = [r for r in runs if all(t in r.tags for t in tags)]

        return runs

    # ------------------------------------------------------------------
    # Hyperparameter logging
    # ------------------------------------------------------------------

    def log_params(
        self,
        params: Dict[str, Any],
        *,
        run_id: Optional[str] = None,
    ) -> None:
        """Log hyperparameters for a run.

        Args:
            params: Dictionary of hyperparameters
            run_id: Run identifier (None = current active run)
        """
        run = self._get_active_run(run_id)
        if run:
            run.hyperparameters.update(params)

    def log_param(
        self,
        name: str,
        value: Any,
        *,
        run_id: Optional[str] = None,
    ) -> None:
        """Log a single hyperparameter.

        Args:
            name: Parameter name
            value: Parameter value
            run_id: Run identifier
        """
        self.log_params({name: value}, run_id=run_id)

    # ------------------------------------------------------------------
    # Metric logging
    # ------------------------------------------------------------------

    def log_metric(
        self,
        name: str,
        value: float,
        *,
        step: int = 0,
        epoch: int = 0,
        run_id: Optional[str] = None,
    ) -> None:
        """Log a metric value.

        Args:
            name: Metric name
            value: Metric value
            step: Training step
            epoch: Epoch number
            run_id: Run identifier
        """
        run = self._get_active_run(run_id)
        if not run:
            return

        log_entry = MetricLog(
            metric_name=name,
            value=value,
            step=step,
            epoch=epoch,
        )

        if name not in run.metrics:
            run.metrics[name] = []
        run.metrics[name].append(log_entry)

    def log_metrics(
        self,
        metrics: Dict[str, float],
        *,
        step: int = 0,
        epoch: int = 0,
        run_id: Optional[str] = None,
    ) -> None:
        """Log multiple metrics at once.

        Args:
            metrics: Dictionary of metric_name -> value
            step: Training step
            epoch: Epoch number
            run_id: Run identifier
        """
        for name, value in metrics.items():
            self.log_metric(name, value, step=step, epoch=epoch, run_id=run_id)

    # ------------------------------------------------------------------
    # Artifact management
    # ------------------------------------------------------------------

    def log_artifact(
        self,
        name: str,
        path: str,
        *,
        artifact_type: ArtifactType = ArtifactType.OTHER,
        metadata: Optional[Dict[str, Any]] = None,
        run_id: Optional[str] = None,
    ) -> Artifact:
        """Log an artifact.

        Args:
            name: Artifact name
            path: Path to the artifact
            artifact_type: Type of artifact
            metadata: Additional metadata
            run_id: Run identifier

        Returns:
            Created artifact
        """
        run = self._get_active_run(run_id)
        if not run:
            raise ValueError("No active run")

        size = 0
        try:
            if os.path.exists(path):
                size = os.path.getsize(path)
        except Exception:
            pass

        artifact = Artifact(
            name=name,
            artifact_type=artifact_type,
            path=path,
            size_bytes=size,
            metadata=metadata or {},
        )

        run.artifacts.append(artifact)
        return artifact

    # ------------------------------------------------------------------
    # Run nesting
    # ------------------------------------------------------------------

    def start_child_run(
        self,
        name: str,
        *,
        parent_run_id: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> ExperimentRun:
        """Start a child run (nested run).

        Args:
            name: Child run name
            parent_run_id: Parent run ID (None = current active run)
            tags: Tags for the child run

        Returns:
            Started child run
        """
        parent_id = parent_run_id or self._active_run_id
        if not parent_id:
            raise ValueError("No parent run available")

        parent_run = self._runs.get(parent_id)
        if not parent_run:
            raise ValueError(f"Parent run {parent_id} not found")

        experiment = self._experiments.get(parent_run.experiment_id)
        if not experiment:
            raise ValueError("Parent experiment not found")

        child_run = ExperimentRun(
            experiment_id=experiment.experiment_id,
            name=name,
            status=RunStatus.RUNNING,
            tags=tags or [],
            parent_run_id=parent_id,
            start_time=datetime.now(timezone.utc).isoformat(),
        )

        self._runs[child_run.run_id] = child_run
        parent_run.child_run_ids.append(child_run.run_id)
        experiment.run_ids.append(child_run.run_id)
        self._active_run_id = child_run.run_id

        return child_run

    def get_run_hierarchy(self, run_id: str) -> Dict[str, Any]:
        """Get the full hierarchy of a run (parents and children).

        Args:
            run_id: Run identifier

        Returns:
            Hierarchy information
        """
        run = self._runs.get(run_id)
        if not run:
            return {}

        # Get parent chain
        parent_chain: List[str] = []
        current = run.parent_run_id
        while current:
            parent_chain.append(current)
            parent = self._runs.get(current)
            if parent:
                current = parent.parent_run_id
            else:
                break

        # Get children recursively
        def get_children(run_id: str) -> List[Dict[str, Any]]:
            r = self._runs.get(run_id)
            if not r:
                return []
            children = []
            for child_id in r.child_run_ids:
                children.append(
                    {
                        "run_id": child_id,
                        "name": self._runs[child_id].name if child_id in self._runs else "",
                        "children": get_children(child_id),
                    }
                )
            return children

        return {
            "run_id": run_id,
            "name": run.name,
            "parent_chain": list(reversed(parent_chain)),
            "children": get_children(run_id),
        }

    # ------------------------------------------------------------------
    # Experiment comparison
    # ------------------------------------------------------------------

    def compare_runs(
        self,
        run_ids: List[str],
        *,
        metrics: Optional[List[str]] = None,
        params: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Compare multiple runs side by side.

        Args:
            run_ids: List of run IDs to compare
            metrics: Specific metrics to compare (None = all)
            params: Specific params to compare (None = all)

        Returns:
            Comparison data
        """
        runs = [self._runs[rid] for rid in run_ids if rid in self._runs]

        if not runs:
            return {"runs": {}}

        # Collect all metric names
        all_metric_names: Set[str] = set()
        all_param_names: Set[str] = set()

        for run in runs:
            all_metric_names.update(run.metrics.keys())
            all_param_names.update(run.hyperparameters.keys())

        if metrics:
            metric_names = [m for m in metrics if m in all_metric_names]
        else:
            metric_names = sorted(all_metric_names)

        if params:
            param_names = [p for p in params if p in all_param_names]
        else:
            param_names = sorted(all_param_names)

        comparison: Dict[str, Any] = {"runs": {}}

        for run in runs:
            run_data: Dict[str, Any] = {
                "name": run.name,
                "status": run.status.value,
                "duration_seconds": run.duration_seconds,
                "hyperparameters": {},
                "metrics": {},
            }

            for param_name in param_names:
                run_data["hyperparameters"][param_name] = run.hyperparameters.get(
                    param_name, None
                )

            for metric_name in metric_names:
                latest = run.get_latest_metric(metric_name)
                run_data["metrics"][metric_name] = {
                    "latest": latest.value if latest else None,
                    "min": min(run.get_metric_values(metric_name))
                    if metric_name in run.metrics
                    else None,
                    "max": max(run.get_metric_values(metric_name))
                    if metric_name in run.metrics
                    else None,
                    "count": len(run.metrics.get(metric_name, [])),
                }

            comparison["runs"][run.run_id] = run_data

        # Find best run for each metric
        comparison["best_runs"] = {}
        for metric_name in metric_names:
            best_run = None
            best_value = None
            for run in runs:
                latest = run.get_latest_metric(metric_name)
                if latest and (best_value is None or latest.value > best_value):
                    best_value = latest.value
                    best_run = run.run_id
            comparison["best_runs"][metric_name] = {
                "run_id": best_run,
                "value": best_value,
            }

        return comparison

    # ------------------------------------------------------------------
    # Experiment reproduction
    # ------------------------------------------------------------------

    def get_reproduction_config(self, run_id: str) -> Dict[str, Any]:
        """Get the configuration needed to reproduce a run.

        Args:
            run_id: Run identifier

        Returns:
            Reproduction configuration
        """
        run = self._runs.get(run_id)
        if not run:
            return {}

        return {
            "run_id": run_id,
            "experiment_id": run.experiment_id,
            "hyperparameters": dict(run.hyperparameters),
            "artifact_paths": [a.path for a in run.artifacts],
            "tag": run.tags,
            "metadata": dict(run.metadata),
            "python_version": "",
            "dependencies": [],
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_active_run(self, run_id: Optional[str] = None) -> Optional[ExperimentRun]:
        """Get the active run (specified or current)."""
        rid = run_id or self._active_run_id
        if not rid:
            return None
        return self._runs.get(rid)

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def get_stats(self) -> Dict[str, Any]:
        """Get tracker statistics.

        Returns:
            Dictionary of statistics
        """
        status_counts: Dict[str, int] = {}
        for run in self._runs.values():
            key = run.status.value
            status_counts[key] = status_counts.get(key, 0) + 1

        return {
            "experiments": len(self._experiments),
            "total_runs": len(self._runs),
            "runs_by_status": status_counts,
            "active_run": self._active_run_id,
            "tags": len(self._tag_index),
        }

    def __repr__(self) -> str:
        return (
            f"ExperimentTracker(experiments={len(self._experiments)}, "
            f"runs={len(self._runs)})"
        )
