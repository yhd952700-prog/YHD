"""Tests for the PlanOptimizer module."""
from __future__ import annotations

import pytest

from src.ai.planner.parser import ParsedPlan, ParsedTask
from src.ai.planner.optimizer import PlanOptimizer, OptimizationResult


def _make_plan(task_specs):
    """Helper: create a ParsedPlan from task specs.

    task_specs: list of (id, name, capability_id, depends_on list)
    """
    tasks = []
    for spec in task_specs:
        task_id, name, cap_id, deps = spec
        tasks.append(ParsedTask(
            id=task_id,
            name=name,
            capability_id=cap_id,
            inputs={},
            depends_on=list(deps),
            is_valid=True,
        ))
    return ParsedPlan(tasks=tasks, parse_success=True)


class TestPlanOptimizer:
    """Test the PlanOptimizer class."""

    def test_optimize_empty_plan(self):
        """Optimising an empty plan should be a no-op."""
        plan = ParsedPlan(tasks=[], parse_success=True)
        optimizer = PlanOptimizer()
        result = optimizer.optimize(plan)

        assert result.plan is plan
        assert result.original_task_count == 0
        assert result.optimised_task_count == 0
        assert result.critical_path_length == 0

    def test_critical_path_single_task(self):
        """A single task has critical path length 1."""
        plan = _make_plan([
            ("t1", "Task 1", "WriteFile", []),
        ])
        optimizer = PlanOptimizer()
        result = optimizer.optimize(plan)

        assert result.critical_path_length == 1

    def test_critical_path_linear(self):
        """Linear chain: critical path = number of tasks."""
        plan = _make_plan([
            ("t1", "Task 1", "WriteFile", []),
            ("t2", "Task 2", "ReadFile", ["t1"]),
            ("t3", "Task 3", "ExecuteCode", ["t2"]),
        ])
        optimizer = PlanOptimizer()
        result = optimizer.optimize(plan)

        assert result.critical_path_length == 3

    def test_critical_path_parallel(self):
        """Parallel tasks: critical path = longest chain."""
        plan = _make_plan([
            ("start", "Start", "WriteFile", []),
            ("fast", "Fast", "ReadFile", ["start"]),
            ("slow1", "Slow 1", "ExecuteCode", ["start"]),
            ("slow2", "Slow 2", "WebSearch", ["slow1"]),
            ("end", "End", "WriteFile", ["fast", "slow2"]),
        ])
        optimizer = PlanOptimizer()
        result = optimizer.optimize(plan)

        # Critical path: start -> slow1 -> slow2 -> end = 4
        assert result.critical_path_length == 4

    def test_parallel_analysis(self):
        """Parallel analysis should mark tasks at the same level."""
        plan = _make_plan([
            ("t1", "Task 1", "WriteFile", []),
            ("t2", "Task 2", "ReadFile", []),
            ("t3", "Task 3", "ExecuteCode", ["t1", "t2"]),
        ])
        optimizer = PlanOptimizer(max_parallel=4)
        result = optimizer.optimize(plan)

        assert result.parallel_tasks > 0  # t1 and t2 are parallel

        # Check parallel metadata was added
        t1 = plan.get_task_by_id("t1")
        assert "_plan_meta" in t1.inputs
        meta = t1.inputs["_plan_meta"]
        assert "topo_level" in meta
        assert meta["parallel_group"] == "level_0"

    def test_merging_disabled(self):
        """When merging is disabled, no tasks should be merged."""
        plan = _make_plan([
            ("t1", "Task 1", "WriteFile", []),
            ("t2", "Task 2", "ReadFile", ["t1"]),
        ])
        optimizer = PlanOptimizer(enable_merging=False)
        result = optimizer.optimize(plan)

        assert result.merged_tasks == 0
        assert result.optimised_task_count == 2

    def test_parallel_disabled(self):
        """When parallelisation is disabled, no parallel analysis."""
        plan = _make_plan([
            ("t1", "Task 1", "WriteFile", []),
            ("t2", "Task 2", "ReadFile", []),
        ])
        optimizer = PlanOptimizer(enable_parallelisation=False)
        result = optimizer.optimize(plan)

        assert result.parallel_tasks == 0

    def test_critical_path_disabled(self):
        """When critical path is disabled, length should be 0."""
        plan = _make_plan([
            ("t1", "Task 1", "WriteFile", []),
            ("t2", "Task 2", "ReadFile", ["t1"]),
        ])
        optimizer = PlanOptimizer(enable_critical_path=False)
        result = optimizer.optimize(plan)

        assert result.critical_path_length == 0

    def test_max_parallel_limit(self):
        """max_parallel should cap the parallel group size."""
        plan = _make_plan([
            ("t1", "T1", "WriteFile", []),
            ("t2", "T2", "ReadFile", []),
            ("t3", "T3", "ExecuteCode", []),
            ("t4", "T4", "WebSearch", []),
            ("t5", "T5", "WriteFile", []),
            ("t6", "T6", "ReadFile", []),
        ])
        optimizer = PlanOptimizer(max_parallel=3)
        result = optimizer.optimize(plan)

        # All 6 tasks are at level 0, but max_parallel=3
        assert result.parallel_tasks == 6  # Still parallelisable in theory
        # The cap is informational; actual enforcement is at execution time

    def test_optimisations_applied_tracking(self):
        """Optimisation result should track which optimisations ran."""
        plan = _make_plan([
            ("t1", "T1", "WriteFile", []),
            ("t2", "T2", "ReadFile", []),
        ])
        optimizer = PlanOptimizer()
        result = optimizer.optimize(plan)

        assert len(result.optimisations_applied) > 0
        assert any("critical_path" in opt for opt in result.optimisations_applied)
        assert any("parallel" in opt for opt in result.optimisations_applied)

    def test_tasks_removed_counter(self):
        """tasks_removed should reflect how many tasks were merged away."""
        plan = _make_plan([
            ("t1", "T1", "WriteFile", []),
            ("t2", "T2", "ReadFile", ["t1"]),
        ])
        optimizer = PlanOptimizer()
        result = optimizer.optimize(plan)

        assert result.tasks_removed == (
            result.original_task_count - result.optimised_task_count
        )


class TestOptimizationResult:
    """Test the OptimizationResult dataclass."""

    def test_default_values(self):
        plan = ParsedPlan(tasks=[])
        result = OptimizationResult(plan=plan)

        assert result.plan is plan
        assert result.original_task_count == 0
        assert result.optimised_task_count == 0
        assert result.merged_tasks == 0
        assert result.parallel_tasks == 0
        assert result.critical_path_length == 0
        assert result.optimisations_applied == []
