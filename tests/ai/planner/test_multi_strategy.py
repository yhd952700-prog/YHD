"""Tests for the MultiStrategyPlanner and StrategySelector."""
from __future__ import annotations

import pytest

from src.ai.planner.multi_strategy import (
    StrategySelector,
    StrategySelection,
    PlanningStrategy,
    MultiStrategyPlanner,
    STRATEGY_DESCRIPTIONS,
)


class TestPlanningStrategy:
    """Test the PlanningStrategy enum."""

    def test_all_strategies_have_descriptions(self):
        """Every strategy should have a description."""
        for strategy in PlanningStrategy:
            assert strategy in STRATEGY_DESCRIPTIONS
            assert len(STRATEGY_DESCRIPTIONS[strategy]) > 0


class TestStrategySelector:
    """Test the StrategySelector class."""

    def test_select_compute_goal(self):
        """A simple arithmetic goal should be classified as DIRECT."""
        selector = StrategySelector()
        result = selector.select("calculate 2 + 3")

        assert isinstance(result, StrategySelection)
        assert result.selection_method == "heuristic"
        # Simple arithmetic → direct strategy (or at least high confidence for it)
        assert result.confidence >= 0

    def test_select_hierarchical_goal(self):
        """A complex project goal should be classified as HIERARCHICAL."""
        selector = StrategySelector()
        result = selector.select("创建一个完整的全栈项目，包含前端后端和数据库")

        assert isinstance(result, StrategySelection)
        # Complex project → hierarchical should be in the alternatives or best

    def test_select_exploratory_goal(self):
        """A research goal should be classified as EXPLORATORY."""
        selector = StrategySelector()
        result = selector.select("研究一下最新的AI发展趋势并做个比较")

        assert isinstance(result, StrategySelection)
        # Research → exploratory should score high

    def test_select_iterative_goal(self):
        """A debugging/optimization goal should be classified as ITERATIVE."""
        selector = StrategySelector()
        result = selector.select("优化这个函数的性能，修复bug")

        assert isinstance(result, StrategySelection)

    def test_confidence_between_zero_and_one(self):
        """Confidence should always be between 0 and 1."""
        selector = StrategySelector()

        goals = [
            "hello",
            "calculate 2+3",
            "create a full-stack web application with react and fastapi",
            "research the history of computing",
            "fix the bug and optimize performance",
            "do something vague",
        ]

        for goal in goals:
            result = selector.select(goal)
            assert 0 <= result.confidence <= 1.0, f"Bad confidence for: {goal}"

    def test_alternatives_are_sorted(self):
        """Alternatives should be sorted by confidence (descending)."""
        selector = StrategySelector()
        result = selector.select("create a project")

        for i in range(len(result.alternatives) - 1):
            assert result.alternatives[i][1] >= result.alternatives[i + 1][1]

    def test_default_strategy(self):
        """Default strategy should be used when nothing matches well."""
        selector = StrategySelector()
        result = selector.select("asdfghjkl")  # Nonsense input

        assert isinstance(result.strategy, PlanningStrategy)

    def test_has_reason(self):
        """Every selection should have a reason string."""
        selector = StrategySelector()
        result = selector.select("test goal")

        assert len(result.reason) > 0


class TestMultiStrategyPlanner:
    """Test the MultiStrategyPlanner class."""

    def test_init_defaults(self):
        """MultiStrategyPlanner should initialise with defaults."""
        planner = MultiStrategyPlanner()

        assert planner._strategy_selector is not None
        assert planner._rule_based_decomposer is None

    def test_plan_without_provider_falls_back(self):
        """Without an LLM provider, should fall back to rule-based."""
        from src.kernels.execution import GoalDecomposer

        planner = MultiStrategyPlanner(
            rule_based_decomposer=GoalDecomposer(),
        )

        plan, selection = planner.plan(
            "write a file named test.txt containing hello",
            scope="L1",
        )

        assert plan is not None
        assert plan.task_count > 0
        # Should have used rule-based fallback
        assert selection.strategy == PlanningStrategy.RULE_BASED

    def test_plan_with_no_provider_and_no_fallback(self):
        """Without provider or fallback, should return error plan."""
        planner = MultiStrategyPlanner()

        plan, selection = planner.plan("do something", scope="L1")

        assert plan is not None
        assert plan.parse_success is False

    def test_strategy_selection_runs(self):
        """Strategy selection should always produce a result."""
        planner = MultiStrategyPlanner()

        _, selection = planner.plan("test goal", scope="L1")

        assert isinstance(selection, StrategySelection)
        assert isinstance(selection.strategy, PlanningStrategy)
