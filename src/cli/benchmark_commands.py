"""Benchmark CLI commands for LiuHao AI OS.

Provides command-line interface for benchmarking operations:
- benchmark run, benchmark list
- benchmark compare, benchmark report
- benchmark baseline
"""

from __future__ import annotations

import argparse
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class BenchmarkStatus(str, Enum):
    """Benchmark run status."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class BenchmarkType(str, Enum):
    """Types of benchmarks."""
    PERFORMANCE = "performance"
    QUALITY = "quality"
    COST = "cost"
    INTEGRATION = "integration"
    STRESS = "stress"


@dataclass
class BenchmarkRun:
    """A single benchmark run."""
    run_id: str
    name: str
    benchmark_type: BenchmarkType
    status: BenchmarkStatus = BenchmarkStatus.PENDING
    duration_ms: float = 0.0
    score: float = 0.0
    metrics: Dict[str, Any] = field(default_factory=dict)
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    baseline: bool = False
    description: str = ""


class BenchmarkManager:
    """In-memory benchmark manager for CLI operations.

    Provides benchmark execution, listing, comparison, and reporting.
    """

    def __init__(self) -> None:
        self._runs: Dict[str, BenchmarkRun] = {}
        self._baselines: Dict[str, BenchmarkRun] = {}
        self._seed_sample_data()

    def _seed_sample_data(self) -> None:
        """Seed with sample benchmark data."""
        runs = [
            ("run-001", "API Performance Test", BenchmarkType.PERFORMANCE, 1500.5, 92.5, True),
            ("run-002", "Agent Quality Test", BenchmarkType.QUALITY, 30000.0, 87.3, False),
            ("run-003", "Memory Throughput", BenchmarkType.PERFORMANCE, 800.2, 95.1, False),
            ("run-004", "Cost Efficiency", BenchmarkType.COST, 500.0, 78.5, False),
            ("run-005", "Integration Suite", BenchmarkType.INTEGRATION, 45000.0, 90.0, False),
        ]
        now = datetime.utcnow()
        for i, (rid, name, btype, dur, score, baseline) in enumerate(runs):
            started = now.replace(hour=i * 2)
            run = BenchmarkRun(
                run_id=rid,
                name=name,
                benchmark_type=btype,
                status=BenchmarkStatus.COMPLETED,
                duration_ms=dur,
                score=score,
                started_at=started.isoformat(),
                completed_at=started.isoformat(),
                baseline=baseline,
                metrics={
                    "requests_per_second": 1000 + i * 100,
                    "error_rate": 0.01 * i,
                    "p95_latency_ms": 50 + i * 10,
                },
            )
            self._runs[rid] = run
            if baseline:
                self._baselines[btype.value] = run

    def run_benchmark(
        self,
        name: str,
        benchmark_type: str = "performance",
        duration: int = 60,
        concurrency: int = 1,
        description: str = "",
    ) -> Dict[str, Any]:
        """Run a benchmark.

        Args:
            name: Benchmark name.
            benchmark_type: Type of benchmark.
            duration: Duration in seconds.
            concurrency: Concurrency level.
            description: Optional description.

        Returns:
            Dict with benchmark run details.
        """
        run_id = f"run-{uuid.uuid4().hex[:8]}"
        run = BenchmarkRun(
            run_id=run_id,
            name=name,
            benchmark_type=BenchmarkType(benchmark_type),
            status=BenchmarkStatus.RUNNING,
            started_at=datetime.utcnow().isoformat(),
            description=description,
        )
        self._runs[run_id] = run

        # Simulate completion
        run.status = BenchmarkStatus.COMPLETED
        run.duration_ms = duration * 1000.0
        run.score = 85.0 + hash(name) % 150 / 10
        run.completed_at = datetime.utcnow().isoformat()
        run.metrics = {
            "duration_seconds": duration,
            "concurrency": concurrency,
            "requests_per_second": 500 + hash(name) % 500,
            "error_rate": 0.005,
            "p50_latency_ms": 25.0,
            "p95_latency_ms": 80.0,
            "p99_latency_ms": 150.0,
        }

        return {
            "run_id": run.run_id,
            "name": run.name,
            "type": run.benchmark_type.value,
            "status": run.status.value,
            "score": run.score,
            "duration_ms": run.duration_ms,
            "started_at": run.started_at,
            "completed_at": run.completed_at,
            "metrics": run.metrics,
        }

    def list_benchmarks(
        self,
        benchmark_type: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """List benchmark runs.

        Args:
            benchmark_type: Filter by type.
            status: Filter by status.
            limit: Result limit.
            offset: Pagination offset.

        Returns:
            Dict with paginated benchmark list.
        """
        runs = list(self._runs.values())
        if benchmark_type:
            runs = [r for r in runs if r.benchmark_type.value == benchmark_type]
        if status:
            runs = [r for r in runs if r.status.value == status]
        runs.sort(key=lambda r: r.started_at or "", reverse=True)
        total = len(runs)
        runs = runs[offset : offset + limit]
        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "items": [
                {
                    "run_id": r.run_id,
                    "name": r.name,
                    "type": r.benchmark_type.value,
                    "status": r.status.value,
                    "score": r.score,
                    "duration_ms": r.duration_ms,
                    "started_at": r.started_at,
                    "baseline": r.baseline,
                }
                for r in runs
            ],
        }

    def compare_benchmarks(self, run_ids: List[str]) -> Dict[str, Any]:
        """Compare multiple benchmark runs.

        Args:
            run_ids: List of run identifiers to compare.

        Returns:
            Dict with comparison data.

        Raises:
            ValueError: If any run not found.
        """
        runs = []
        for rid in run_ids:
            run = self._runs.get(rid)
            if not run:
                raise ValueError(f"Benchmark run not found: {rid}")
            runs.append(run)

        comparison = {
            "run_ids": run_ids,
            "score_comparison": {r.run_id: r.score for r in runs},
            "duration_comparison_ms": {r.run_id: r.duration_ms for r in runs},
            "metrics": {r.run_id: r.metrics for r in runs},
            "best_score": max(r.score for r in runs),
            "fastest_ms": min(r.duration_ms for r in runs),
            "winner": max(runs, key=lambda r: r.score).run_id,
        }
        return comparison

    def generate_report(
        self,
        run_id: Optional[str] = None,
        benchmark_type: Optional[str] = None,
        format: str = "json",
    ) -> Dict[str, Any]:
        """Generate a benchmark report.

        Args:
            run_id: Optional specific run to report on.
            benchmark_type: Optional type filter.
            format: Report format (json, text).

        Returns:
            Dict with report data.
        """
        if run_id:
            run = self._runs.get(run_id)
            if not run:
                raise ValueError(f"Benchmark run not found: {run_id}")
            return {
                "format": format,
                "generated_at": datetime.utcnow().isoformat(),
                "run": {
                    "run_id": run.run_id,
                    "name": run.name,
                    "type": run.benchmark_type.value,
                    "status": run.status.value,
                    "score": run.score,
                    "duration_ms": run.duration_ms,
                    "metrics": run.metrics,
                },
                "summary": f"Benchmark {run.name} scored {run.score:.2f}",
            }

        runs = list(self._runs.values())
        if benchmark_type:
            runs = [r for r in runs if r.benchmark_type.value == benchmark_type]
        return {
            "format": format,
            "generated_at": datetime.utcnow().isoformat(),
            "total_runs": len(runs),
            "avg_score": sum(r.score for r in runs) / len(runs) if runs else 0,
            "by_type": {
                bt.value: {
                    "count": sum(1 for r in runs if r.benchmark_type == bt),
                    "avg_score": (
                        sum(r.score for r in runs if r.benchmark_type == bt)
                        / sum(1 for r in runs if r.benchmark_type == bt)
                        if any(r.benchmark_type == bt for r in runs)
                        else 0
                    ),
                }
                for bt in BenchmarkType
            },
        }

    def get_baseline(self, benchmark_type: Optional[str] = None) -> Dict[str, Any]:
        """Get baseline benchmark data.

        Args:
            benchmark_type: Optional type filter.

        Returns:
            Dict with baseline data.
        """
        if benchmark_type:
            baseline = self._baselines.get(benchmark_type)
            if not baseline:
                return {"baseline": None, "type": benchmark_type}
            return {
                "type": benchmark_type,
                "baseline": {
                    "run_id": baseline.run_id,
                    "name": baseline.name,
                    "score": baseline.score,
                    "duration_ms": baseline.duration_ms,
                    "metrics": baseline.metrics,
                },
            }
        return {
            "baselines": {
                btype: {
                    "run_id": b.run_id,
                    "name": b.name,
                    "score": b.score,
                    "duration_ms": b.duration_ms,
                }
                for btype, b in self._baselines.items()
            },
        }

    def set_baseline(self, run_id: str) -> Dict[str, Any]:
        """Set a benchmark run as the baseline for its type.

        Args:
            run_id: Run identifier.

        Returns:
            Dict confirming baseline set.

        Raises:
            ValueError: If run not found.
        """
        run = self._runs.get(run_id)
        if not run:
            raise ValueError(f"Benchmark run not found: {run_id}")
        # Unset existing baseline for this type
        old_baseline = self._baselines.get(run.benchmark_type.value)
        if old_baseline:
            old_baseline.baseline = False
        run.baseline = True
        self._baselines[run.benchmark_type.value] = run
        return {
            "status": "baseline_set",
            "run_id": run_id,
            "type": run.benchmark_type.value,
            "score": run.score,
        }


# =====================================================================
# CLI Parser
# =====================================================================


def register_benchmark_commands(
    subparsers: argparse._SubParsersAction,
) -> None:
    """Register benchmark subcommands under the given argparse subparser.

    Args:
        subparsers: The parent subparsers action to attach to.
    """
    p_bench = subparsers.add_parser("benchmark", help="Benchmark management")
    bench_sub = p_bench.add_subparsers(dest="benchmark_command", required=True)

    # benchmark run
    p_run = bench_sub.add_parser("run", help="Run a benchmark")
    p_run.add_argument("--name", required=True, help="Benchmark name")
    p_run.add_argument(
        "--type",
        default="performance",
        choices=["performance", "quality", "cost", "integration", "stress"],
        help="Benchmark type",
    )
    p_run.add_argument("--duration", type=int, default=60, help="Duration in seconds")
    p_run.add_argument("--concurrency", type=int, default=1, help="Concurrency level")
    p_run.add_argument("--description", default="", help="Benchmark description")

    # benchmark list
    p_list = bench_sub.add_parser("list", help="List benchmark runs")
    p_list.add_argument("--type", help="Filter by benchmark type")
    p_list.add_argument("--status", help="Filter by status")
    p_list.add_argument("--limit", type=int, default=20, help="Result limit")
    p_list.add_argument("--offset", type=int, default=0, help="Pagination offset")

    # benchmark compare
    p_compare = bench_sub.add_parser("compare", help="Compare benchmark runs")
    p_compare.add_argument(
        "--run",
        action="append",
        dest="run_ids",
        required=True,
        help="Run ID to compare (repeatable)",
    )

    # benchmark report
    p_report = bench_sub.add_parser("report", help="Generate benchmark report")
    p_report.add_argument("--run-id", help="Specific run to report")
    p_report.add_argument("--type", help="Filter by benchmark type")
    p_report.add_argument(
        "--format", default="json", choices=["json", "text"], help="Report format"
    )

    # benchmark baseline
    p_baseline = bench_sub.add_parser("baseline", help="Manage baselines")
    baseline_sub = p_baseline.add_subparsers(dest="baseline_command", required=True)

    p_base_get = baseline_sub.add_parser("get", help="Get baseline")
    p_base_get.add_argument("--type", help="Filter by benchmark type")

    p_base_set = baseline_sub.add_parser("set", help="Set a run as baseline")
    p_base_set.add_argument("run_id", help="Run identifier")


# =====================================================================
# Command Handler
# =====================================================================


def handle_benchmark_command(
    args: argparse.Namespace, manager: BenchmarkManager
) -> int:
    """Dispatch a benchmark CLI command to the appropriate handler.

    Args:
        args: Parsed argparse namespace.
        manager: BenchmarkManager instance.

    Returns:
        Exit code (0 for success, 1 for error).
    """
    try:
        if args.benchmark_command == "run":
            result = manager.run_benchmark(
                name=args.name,
                benchmark_type=args.type,
                duration=args.duration,
                concurrency=args.concurrency,
                description=args.description,
            )
        elif args.benchmark_command == "list":
            result = manager.list_benchmarks(
                benchmark_type=getattr(args, "type", None),
                status=args.status,
                limit=args.limit,
                offset=args.offset,
            )
        elif args.benchmark_command == "compare":
            result = manager.compare_benchmarks(args.run_ids)
        elif args.benchmark_command == "report":
            result = manager.generate_report(
                run_id=args.run_id,
                benchmark_type=getattr(args, "type", None),
                format=args.format,
            )
        elif args.benchmark_command == "baseline":
            if args.baseline_command == "get":
                result = manager.get_baseline(
                    benchmark_type=getattr(args, "type", None)
                )
            elif args.baseline_command == "set":
                result = manager.set_baseline(args.run_id)
            else:
                print(f"Unknown baseline command: {args.baseline_command}")
                return 1
        else:
            print(f"Unknown benchmark command: {args.benchmark_command}")
            return 1

        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}, indent=2, ensure_ascii=False))
        return 1
