"""Benchmark Runner for the evaluation framework.

Manages benchmark datasets, test suites, batch evaluation,
result storage, baseline management, and report generation.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from uuid import uuid4

from .metrics import MetricAggregator, MetricCategory, MetricResult


class BenchmarkStatus(str, Enum):
    """Benchmark run status."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class EvaluationStrategy(str, Enum):
    """Benchmark evaluation strategies."""

    FULL = "full"
    RANDOM = "random"
    STRATIFIED = "stratified"
    INCREMENTAL = "incremental"


@dataclass
class BenchmarkDataset:
    """A benchmark dataset.

    Attributes:
        dataset_id: Unique dataset identifier
        name: Dataset name
        description: Dataset description
        task_type: Type of task (e.g., "text_generation", "code")
        samples: List of test samples
        categories: Category labels for stratification
        source: Dataset source
        version: Dataset version
        created_at: Creation timestamp
        metadata: Additional metadata
    """

    dataset_id: str = field(default_factory=lambda: str(uuid4()))
    name: str = ""
    description: str = ""
    task_type: str = ""
    samples: List[Dict[str, Any]] = field(default_factory=list)
    categories: List[str] = field(default_factory=list)
    source: str = ""
    version: str = "1.0.0"
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.samples)

    def get_sample(self, index: int) -> Dict[str, Any]:
        """Get a sample by index.

        Args:
            index: Sample index

        Returns:
            Sample dictionary
        """
        return self.samples[index]

    def by_category(self, category: str) -> List[Dict[str, Any]]:
        """Get samples by category.

        Args:
            category: Category name

        Returns:
            List of samples in the category
        """
        return [s for s in self.samples if s.get("category") == category]

    def categories_distribution(self) -> Dict[str, int]:
        """Get the distribution of samples by category.

        Returns:
            Dictionary of category -> count
        """
        from collections import Counter

        return dict(Counter(s.get("category", "uncategorized") for s in self.samples))


@dataclass
class TestSuite:
    """A test suite defining which benchmarks to run.

    Attributes:
        suite_id: Unique suite identifier
        name: Suite name
        description: Suite description
        datasets: List of dataset IDs to include
        metrics: List of metric names to compute
        model_ids: List of model IDs to evaluate
        strategy: Evaluation strategy
        sample_size: Number of samples (for random/stratified)
        config: Additional configuration
        created_at: Creation timestamp
    """

    suite_id: str = field(default_factory=lambda: str(uuid4()))
    name: str = ""
    description: str = ""
    datasets: List[str] = field(default_factory=list)
    metrics: List[str] = field(default_factory=list)
    model_ids: List[str] = field(default_factory=list)
    strategy: EvaluationStrategy = EvaluationStrategy.FULL
    sample_size: Optional[int] = None
    config: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class BenchmarkResult:
    """Result of a benchmark run.

    Attributes:
        result_id: Unique result identifier
        run_id: Run identifier
        dataset_id: Dataset evaluated
        model_id: Model evaluated
        metric_results: Dictionary of metric_name -> MetricResult
        total_samples: Total samples evaluated
        completed_samples: Successfully completed samples
        failed_samples: Failed samples
        duration_seconds: Total duration
        started_at: Start timestamp
        completed_at: Completion timestamp
        status: Run status
        error: Error message if failed
        metadata: Additional metadata
    """

    result_id: str = field(default_factory=lambda: str(uuid4()))
    run_id: str = ""
    dataset_id: str = ""
    model_id: str = ""
    metric_results: Dict[str, MetricResult] = field(default_factory=dict)
    total_samples: int = 0
    completed_samples: int = 0
    failed_samples: int = 0
    duration_seconds: float = 0.0
    started_at: str = ""
    completed_at: str = ""
    status: BenchmarkStatus = BenchmarkStatus.PENDING
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary.

        Returns:
            Dictionary representation
        """
        return {
            "result_id": self.result_id,
            "run_id": self.run_id,
            "dataset_id": self.dataset_id,
            "model_id": self.model_id,
            "metric_results": {
                k: {
                    "value": v.value,
                    "confidence": v.confidence,
                    "sample_size": v.sample_size,
                }
                for k, v in self.metric_results.items()
            },
            "total_samples": self.total_samples,
            "completed_samples": self.completed_samples,
            "failed_samples": self.failed_samples,
            "duration_seconds": self.duration_seconds,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "status": self.status.value,
            "error": self.error,
        }


@dataclass
class Baseline:
    """A benchmark baseline for comparison.

    Attributes:
        baseline_id: Unique baseline identifier
        name: Baseline name
        description: Baseline description
        model_id: Model ID for the baseline
        dataset_id: Dataset ID
        metric_values: Dictionary of metric values
        created_at: Creation timestamp
        is_active: Whether this is the active baseline
    """

    baseline_id: str = field(default_factory=lambda: str(uuid4()))
    name: str = ""
    description: str = ""
    model_id: str = ""
    dataset_id: str = ""
    metric_values: Dict[str, float] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    is_active: bool = False


class BenchmarkRunner:
    """Runner for benchmark evaluations.

    Manages benchmark datasets, runs evaluations, stores results,
    and generates comparison reports.

    Usage:
        runner = BenchmarkRunner()
        runner.add_dataset(dataset)
        results = runner.run_suite(test_suite, eval_fn)
    """

    def __init__(
        self,
        results_dir: Optional[str] = None,
    ) -> None:
        """Initialize the benchmark runner.

        Args:
            results_dir: Directory to store results
        """
        self._datasets: Dict[str, BenchmarkDataset] = {}
        self._suites: Dict[str, TestSuite] = {}
        self._results: Dict[str, BenchmarkResult] = {}
        self._baselines: Dict[str, Baseline] = {}
        self._runs: Dict[str, Dict[str, Any]] = {}
        self._results_dir = results_dir
        self._progress_callbacks: List[Callable[[str, float], None]] = []

        if results_dir:
            os.makedirs(results_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # Dataset management
    # ------------------------------------------------------------------

    def add_dataset(self, dataset: BenchmarkDataset) -> None:
        """Register a benchmark dataset.

        Args:
            dataset: Dataset to register
        """
        self._datasets[dataset.dataset_id] = dataset

    def remove_dataset(self, dataset_id: str) -> None:
        """Remove a dataset.

        Args:
            dataset_id: Dataset identifier
        """
        self._datasets.pop(dataset_id, None)

    def get_dataset(self, dataset_id: str) -> Optional[BenchmarkDataset]:
        """Get a dataset by ID.

        Args:
            dataset_id: Dataset identifier

        Returns:
            Dataset if found, None otherwise
        """
        return self._datasets.get(dataset_id)

    def list_datasets(self) -> List[BenchmarkDataset]:
        """List all registered datasets.

        Returns:
            List of datasets
        """
        return list(self._datasets.values())

    # ------------------------------------------------------------------
    # Test suite management
    # ------------------------------------------------------------------

    def add_suite(self, suite: TestSuite) -> None:
        """Register a test suite.

        Args:
            suite: Test suite to register
        """
        self._suites[suite.suite_id] = suite

    def get_suite(self, suite_id: str) -> Optional[TestSuite]:
        """Get a test suite by ID.

        Args:
            suite_id: Suite identifier

        Returns:
            Test suite if found, None otherwise
        """
        return self._suites.get(suite_id)

    # ------------------------------------------------------------------
    # Baseline management
    # ------------------------------------------------------------------

    def set_baseline(self, baseline: Baseline) -> None:
        """Set a benchmark baseline.

        Args:
            baseline: Baseline to set
        """
        self._baselines[baseline.baseline_id] = baseline

    def get_active_baseline(
        self, model_id: str, dataset_id: str
    ) -> Optional[Baseline]:
        """Get the active baseline for a model+dataset combination.

        Args:
            model_id: Model identifier
            dataset_id: Dataset identifier

        Returns:
            Active baseline if found, None otherwise
        """
        for baseline in self._baselines.values():
            if (
                baseline.is_active
                and baseline.model_id == model_id
                and baseline.dataset_id == dataset_id
            ):
                return baseline
        return None

    def compare_to_baseline(
        self, result: BenchmarkResult
    ) -> Dict[str, Dict[str, float]]:
        """Compare a result to the active baseline.

        Args:
            result: Benchmark result to compare

        Returns:
            Dictionary of metric -> comparison data
        """
        baseline = self.get_active_baseline(result.model_id, result.dataset_id)
        if not baseline:
            return {}

        comparison: Dict[str, Dict[str, float]] = {}
        for metric_name, metric_result in result.metric_results.items():
            baseline_val = baseline.metric_values.get(metric_name, 0.0)
            diff = metric_result.value - baseline_val
            relative = (
                diff / baseline_val if baseline_val != 0 else 0.0
            )

            comparison[metric_name] = {
                "baseline": baseline_val,
                "current": metric_result.value,
                "absolute_diff": diff,
                "relative_diff": relative,
                "improved": diff > 0,
            }

        return comparison

    # ------------------------------------------------------------------
    # Sample selection
    # ------------------------------------------------------------------

    def _select_samples(
        self,
        dataset: BenchmarkDataset,
        strategy: EvaluationStrategy,
        sample_size: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Select samples from a dataset based on strategy.

        Args:
            dataset: Benchmark dataset
            strategy: Evaluation strategy
            sample_size: Number of samples to select

        Returns:
            List of selected samples
        """
        import random

        samples = list(dataset.samples)

        if strategy == EvaluationStrategy.FULL:
            return samples

        if strategy == EvaluationStrategy.RANDOM:
            k = sample_size or min(100, len(samples))
            return random.sample(samples, min(k, len(samples)))

        if strategy == EvaluationStrategy.STRATIFIED:
            k = sample_size or min(100, len(samples))
            categories = dataset.categories_distribution()
            if not categories:
                return random.sample(samples, min(k, len(samples)))

            selected: List[Dict[str, Any]] = []
            per_category = max(1, k // len(categories))
            for category in categories:
                cat_samples = dataset.by_category(category)
                if cat_samples:
                    selected.extend(
                        random.sample(
                            cat_samples, min(per_category, len(cat_samples))
                        )
                    )
            return selected[:k]

        if strategy == EvaluationStrategy.INCREMENTAL:
            k = sample_size or min(100, len(samples))
            return samples[:k]

        return samples

    # ------------------------------------------------------------------
    # Benchmark execution
    # ------------------------------------------------------------------

    def run_benchmark(
        self,
        dataset: BenchmarkDataset,
        model_id: str,
        eval_fn: Callable[[Dict[str, Any]], Any],
        metric_fns: Dict[str, Callable[[Any, Any], float]],
        *,
        strategy: EvaluationStrategy = EvaluationStrategy.FULL,
        sample_size: Optional[int] = None,
        run_id: Optional[str] = None,
    ) -> BenchmarkResult:
        """Run a benchmark evaluation.

        Args:
            dataset: Dataset to evaluate on
            model_id: Model being evaluated
            eval_fn: Function that takes a sample and returns model output
            metric_fns: Dictionary of metric_name -> function(output, reference) -> float
            strategy: Evaluation strategy
            sample_size: Number of samples to evaluate
            run_id: Run identifier

        Returns:
            Benchmark result
        """
        run_id = run_id or str(uuid4())
        result = BenchmarkResult(
            run_id=run_id,
            dataset_id=dataset.dataset_id,
            model_id=model_id,
            total_samples=len(dataset),
            started_at=datetime.now(timezone.utc).isoformat(),
            status=BenchmarkStatus.RUNNING,
        )

        start_time = time.time()

        try:
            selected_samples = self._select_samples(dataset, strategy, sample_size)
            result.total_samples = len(selected_samples)

            aggregators: Dict[str, MetricAggregator] = {
                name: MetricAggregator() for name in metric_fns
            }

            completed = 0
            failed = 0

            for i, sample in enumerate(selected_samples):
                try:
                    output = eval_fn(sample)
                    reference = sample.get("reference", sample.get("expected", ""))

                    for metric_name, metric_fn in metric_fns.items():
                        try:
                            metric_value = metric_fn(output, reference)
                            aggregators[metric_name].add_result(
                                metric_name, metric_value
                            )
                        except Exception:
                            pass

                    completed += 1
                except Exception:
                    failed += 1

                # Progress update
                if len(selected_samples) > 0:
                    progress = (i + 1) / len(selected_samples)
                    self._report_progress(run_id, progress)

            # Compute final metrics
            for metric_name, aggregator in aggregators.items():
                mean_val = aggregator.mean(metric_name)
                std_val = aggregator.std_dev(metric_name)
                result.metric_results[metric_name] = MetricResult(
                    metric_name=metric_name,
                    category=MetricCategory.TEXT_GENERATION,
                    value=mean_val,
                    confidence=max(0.0, 1.0 - std_val),
                    sample_size=completed,
                    details=aggregator.summary(metric_name),
                )

            result.completed_samples = completed
            result.failed_samples = failed
            result.status = BenchmarkStatus.COMPLETED

        except Exception as e:
            result.status = BenchmarkStatus.FAILED
            result.error = str(e)

        result.duration_seconds = time.time() - start_time
        result.completed_at = datetime.now(timezone.utc).isoformat()

        # Store result
        self._results[result.result_id] = result

        # Save to disk if configured
        if self._results_dir:
            self._save_result(result)

        return result

    def run_suite(
        self,
        suite: TestSuite,
        eval_fn: Callable[[Dict[str, Any]], Any],
        metric_fns: Dict[str, Callable[[Any, Any], float]],
    ) -> Dict[str, BenchmarkResult]:
        """Run a full test suite.

        Args:
            suite: Test suite to run
            eval_fn: Evaluation function
            metric_fns: Metric functions

        Returns:
            Dictionary of (dataset_id, model_id) -> result
        """
        run_id = str(uuid4())
        self._runs[run_id] = {
            "suite_id": suite.suite_id,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "status": "running",
        }

        results: Dict[str, BenchmarkResult] = {}

        for dataset_id in suite.datasets:
            dataset = self._datasets.get(dataset_id)
            if not dataset:
                continue

            for model_id in suite.model_ids:
                key = f"{dataset_id}:{model_id}"
                result = self.run_benchmark(
                    dataset=dataset,
                    model_id=model_id,
                    eval_fn=eval_fn,
                    metric_fns=metric_fns,
                    strategy=suite.strategy,
                    sample_size=suite.sample_size,
                    run_id=run_id,
                )
                results[key] = result

        self._runs[run_id]["status"] = "completed"
        self._runs[run_id]["completed_at"] = datetime.now(timezone.utc).isoformat()
        self._runs[run_id]["results_count"] = len(results)

        return results

    # ------------------------------------------------------------------
    # Progress tracking
    # ------------------------------------------------------------------

    def add_progress_callback(
        self, callback: Callable[[str, float], None]
    ) -> None:
        """Add a progress callback.

        Args:
            callback: Function(run_id, progress) called on progress updates
        """
        self._progress_callbacks.append(callback)

    def _report_progress(self, run_id: str, progress: float) -> None:
        """Report progress to all callbacks.

        Args:
            run_id: Run identifier
            progress: Progress value (0-1)
        """
        for callback in self._progress_callbacks:
            try:
                callback(run_id, progress)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Report generation
    # ------------------------------------------------------------------

    def generate_report(
        self,
        result: BenchmarkResult,
        *,
        include_baseline: bool = True,
        format: str = "dict",
    ) -> Any:
        """Generate a benchmark report.

        Args:
            result: Benchmark result
            include_baseline: Whether to include baseline comparison
            format: Output format ("dict", "json", "markdown")

        Returns:
            Report in the specified format
        """
        report_data: Dict[str, Any] = {
            "result_id": result.result_id,
            "run_id": result.run_id,
            "model_id": result.model_id,
            "dataset_id": result.dataset_id,
            "status": result.status.value,
            "samples": {
                "total": result.total_samples,
                "completed": result.completed_samples,
                "failed": result.failed_samples,
            },
            "duration_seconds": result.duration_seconds,
            "metrics": {
                name: {
                    "value": mr.value,
                    "confidence": mr.confidence,
                    "sample_size": mr.sample_size,
                }
                for name, mr in result.metric_results.items()
            },
        }

        if include_baseline:
            baseline_comparison = self.compare_to_baseline(result)
            if baseline_comparison:
                report_data["baseline_comparison"] = baseline_comparison

        if format == "json":
            return json.dumps(report_data, indent=2)
        elif format == "markdown":
            return self._generate_markdown_report(report_data)
        else:
            return report_data

    def _generate_markdown_report(self, data: Dict[str, Any]) -> str:
        """Generate a markdown-formatted report.

        Args:
            data: Report data dictionary

        Returns:
            Markdown string
        """
        lines = [
            f"# Benchmark Report",
            "",
            f"- **Model**: {data['model_id']}",
            f"- **Dataset**: {data['dataset_id']}",
            f"- **Status**: {data['status']}",
            f"- **Samples**: {data['samples']['completed']}/{data['samples']['total']}",
            f"- **Duration**: {data['duration_seconds']:.2f}s",
            "",
            "## Metrics",
            "",
            "| Metric | Value | Confidence |",
            "|--------|-------|------------|",
        ]

        for name, metric in data["metrics"].items():
            lines.append(
                f"| {name} | {metric['value']:.4f} | {metric['confidence']:.2f} |"
            )

        if "baseline_comparison" in data:
            lines.extend(["", "## Baseline Comparison", ""])
            lines.append("| Metric | Baseline | Current | Change |")
            lines.append("|--------|----------|---------|--------|")
            for name, comp in data["baseline_comparison"].items():
                arrow = "+" if comp["improved"] else ""
                lines.append(
                    f"| {name} | {comp['baseline']:.4f} | {comp['current']:.4f} | "
                    f"{arrow}{comp['absolute_diff']:.4f} ({comp['relative_diff']:.1%}) |"
                )

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Result persistence
    # ------------------------------------------------------------------

    def _save_result(self, result: BenchmarkResult) -> None:
        """Save a result to disk.

        Args:
            result: Result to save
        """
        if not self._results_dir:
            return

        filepath = os.path.join(
            self._results_dir, f"{result.result_id}.json"
        )
        try:
            with open(filepath, "w") as f:
                json.dump(result.to_dict(), f, indent=2)
        except Exception:
            pass

    def get_result(self, result_id: str) -> Optional[BenchmarkResult]:
        """Get a result by ID.

        Args:
            result_id: Result identifier

        Returns:
            Result if found, None otherwise
        """
        return self._results.get(result_id)

    def list_results(
        self,
        *,
        model_id: Optional[str] = None,
        dataset_id: Optional[str] = None,
    ) -> List[BenchmarkResult]:
        """List results, optionally filtered.

        Args:
            model_id: Filter by model ID
            dataset_id: Filter by dataset ID

        Returns:
            List of matching results
        """
        results = list(self._results.values())
        if model_id:
            results = [r for r in results if r.model_id == model_id]
        if dataset_id:
            results = [r for r in results if r.dataset_id == dataset_id]
        return results

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def get_stats(self) -> Dict[str, Any]:
        """Get runner statistics.

        Returns:
            Dictionary of statistics
        """
        return {
            "datasets": len(self._datasets),
            "suites": len(self._suites),
            "results": len(self._results),
            "baselines": len(self._baselines),
            "runs": len(self._runs),
        }

    def __repr__(self) -> str:
        return (
            f"BenchmarkRunner(datasets={len(self._datasets)}, "
            f"results={len(self._results)})"
        )
