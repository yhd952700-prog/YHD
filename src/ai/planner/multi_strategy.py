"""Multi-strategy planning — pick the right planning approach for the goal.

Different goals need different planning strategies:
  - "Calculate 2+3" → direct (single step)
  - "Write a report about X" → linear (research → outline → write → review)
  - "Set up a full-stack project" → hierarchical (big goal → subgoals → tasks)
  - "Find the best restaurant" → exploratory (search → compare → decide)

The strategy selector first classifies the goal, then applies the right
planning strategy. This is more efficient and produces better plans than
always using the same heavyweight planner.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from .parser import ParsedPlan, ParsedTask


class PlanningStrategy(str, Enum):
    """Available planning strategies."""
    DIRECT = "direct"           # Single task, no decomposition needed
    LINEAR = "linear"           # Sequential steps
    PARALLEL = "parallel"       # Multiple independent tasks
    HIERARCHICAL = "hierarchical"  # Big goal → subgoals → tasks
    EXPLORATORY = "exploratory"    # Research first, then plan
    ITERATIVE = "iterative"     # Try → check → adjust → repeat
    RULE_BASED = "rule_based"   # Fallback: use the regex-based decomposer


STRATEGY_DESCRIPTIONS = {
    PlanningStrategy.DIRECT: "直接执行 — 简单、明确的单步任务",
    PlanningStrategy.LINEAR: "线性规划 — 有明确先后顺序的多步任务",
    PlanningStrategy.PARALLEL: "并行规划 — 可同时执行的独立任务",
    PlanningStrategy.HIERARCHICAL: "层次规划 — 复杂大目标，需要先分解为子目标",
    PlanningStrategy.EXPLORATORY: "探索式规划 — 目标不明确，需要先调研再规划",
    PlanningStrategy.ITERATIVE: "迭代规划 — 需要多轮试错和调整的任务",
    PlanningStrategy.RULE_BASED: "规则匹配 — 回退到正则表达式匹配",
}


@dataclass
class StrategySelection:
    """Result of strategy selection."""
    strategy: PlanningStrategy
    confidence: float = 0.0
    reason: str = ""
    alternatives: List[Tuple[PlanningStrategy, float]] = field(default_factory=list)
    selection_method: str = "heuristic"  # heuristic / llm / cached


class StrategySelector:
    """Select the best planning strategy for a given goal.

    Uses a two-stage approach:
      1. **Fast heuristic** — quick keyword-based classification (sub-ms)
      2. **LLM refinement** — if heuristic confidence is low, ask the LLM

    This way, simple goals get planned instantly, and only tricky goals
    pay the LLM latency cost.
    """

    # Keyword patterns for each strategy
    _DIRECT_PATTERNS = [
        r"^计算\s+", r"^算\s+", r"^求值",
        r"^\d+\s*[+\-*/%]\s*\d+",
        r"^写\s*(?:一个)?\s*文件",
        r"^创建\s*(?:一个)?\s*文件",
        r"^(?:what's|what is|计算|告诉我)\s+",
    ]

    _LINEAR_PATTERNS = [
        r"然后", r"接着", r"之后", r"下一步",
        r"先.*再.*", r"首先.*然后.*",
        r"step by step", r"步骤",
    ]

    _PARALLEL_PATTERNS = [
        r"同时", r"分别", r"各自",
        r"both.*and", r"同时做",
    ]

    _HIERARCHICAL_PATTERNS = [
        r"项目", r"系统", r"平台", r"应用",
        r"完整的", r"全面的", r"端到端",
        r"full.*project", r"complete.*system",
    ]

    _EXPLORATORY_PATTERNS = [
        r"研究", r"调研", r"分析", r"了解",
        r"比较", r"评估", r"寻找", r"搜索",
        r"research", r"investigate", r"explore", r"find out",
    ]

    _ITERATIVE_PATTERNS = [
        r"优化", r"改进", r"迭代", r"调整",
        r"调试", r"修复", r"完善",
        r"optimize", r"improve", r"debug", r"refine",
    ]

    def __init__(
        self,
        use_llm_refinement: bool = True,
        llm_threshold: float = 0.6,
        default_strategy: PlanningStrategy = PlanningStrategy.RULE_BASED,
    ):
        self.use_llm_refinement = use_llm_refinement
        self.llm_threshold = llm_threshold
        self.default_strategy = default_strategy

    def select(self, goal_text: str, context: Optional[Dict[str, Any]] = None) -> StrategySelection:
        """Select the best planning strategy for a goal.

        Args:
            goal_text: The natural language goal
            context: Optional context (goal length, complexity hints, etc.)

        Returns:
            StrategySelection with the chosen strategy and confidence
        """
        # Stage 1: Fast heuristic classification
        heuristic_result = self._heuristic_classify(goal_text)

        # If heuristic is confident enough, use it
        if heuristic_result.confidence >= self.llm_threshold:
            return heuristic_result

        # Stage 2: If LLM refinement is enabled and heuristic is unsure,
        # fall back to the default strategy (we keep LLM call optional
        # because the caller might not have an LLM provider ready).
        # The LLMPlanner class will handle LLM-based refinement.

        return heuristic_result

    def _heuristic_classify(self, goal_text: str) -> StrategySelection:
        """Quick keyword-based strategy classification.

        Returns a StrategySelection with heuristic confidence scores.
        """
        scores: Dict[PlanningStrategy, float] = {}
        goal_lower = goal_text.lower()
        goal_length = len(goal_text)

        # Score each strategy based on pattern matches
        scores[PlanningStrategy.DIRECT] = self._score_patterns(
            goal_lower, self._DIRECT_PATTERNS
        )

        scores[PlanningStrategy.LINEAR] = self._score_patterns(
            goal_lower, self._LINEAR_PATTERNS
        )

        scores[PlanningStrategy.PARALLEL] = self._score_patterns(
            goal_lower, self._PARALLEL_PATTERNS
        )

        scores[PlanningStrategy.HIERARCHICAL] = self._score_patterns(
            goal_lower, self._HIERARCHICAL_PATTERNS
        )

        scores[PlanningStrategy.EXPLORATORY] = self._score_patterns(
            goal_lower, self._EXPLORATORY_PATTERNS
        )

        scores[PlanningStrategy.ITERATIVE] = self._score_patterns(
            goal_lower, self._ITERATIVE_PATTERNS
        )

        # Length-based adjustments
        if goal_length < 30:
            scores[PlanningStrategy.DIRECT] += 0.2
        elif goal_length < 100:
            scores[PlanningStrategy.LINEAR] += 0.1
        elif goal_length > 200:
            scores[PlanningStrategy.HIERARCHICAL] += 0.2
            scores[PlanningStrategy.EXPLORATORY] += 0.1

        # Find the best strategy
        sorted_strategies = sorted(
            scores.items(), key=lambda x: x[1], reverse=True
        )

        best_strategy, best_score = sorted_strategies[0]
        alternatives = [(s, sc) for s, sc in sorted_strategies[1:4] if sc > 0]

        # Normalise confidence (0-1 scale)
        confidence = min(best_score, 1.0)

        return StrategySelection(
            strategy=best_strategy,
            confidence=confidence,
            reason=f"Heuristic classification: best match is {best_strategy.value} "
                   f"with score {best_score:.2f}",
            alternatives=alternatives,
            selection_method="heuristic",
        )

    def _score_patterns(self, text: str, patterns: List[str]) -> float:
        """Score a piece of text against a list of regex patterns.

        Returns a score between 0 and ~1.0 (multiple matches can exceed 1.0,
        but the caller should clamp).
        """
        score = 0.0
        for pattern in patterns:
            if re.search(pattern, text, re.IGNORECASE):
                score += 0.3
        return min(score, 1.0)


class MultiStrategyPlanner:
    """Planner that selects and applies the best strategy for each goal.

    This is the high-level entry point that wraps the LLMPlanner with
    strategy selection and fallbacks.

    Architecture::

        goal → StrategySelector → best strategy
                                     ↓
                        ┌────────┴────────┐
                        ▼                 ▼
                  DIRECT/LINEAR/    HIERARCHICAL/
                  PARALLEL etc.     EXPLORATORY
                  (simple prompt)   (complex prompt)
                        │                 │
                        └───────┬─────────┘
                                ▼
                          LLMPlanner
                                │
                                ▼
                     PlanValidator + PlanOptimizer
                                │
                                ▼
                           Final Plan
    """

    def __init__(
        self,
        llm_planner=None,  # LLMPlanner — imported lazily to avoid circular imports
        validator=None,    # PlanValidator
        optimizer=None,    # PlanOptimizer
        strategy_selector: Optional[StrategySelector] = None,
        rule_based_decomposer=None,  # Fallback: the regex GoalDecomposer
    ):
        self._llm_planner = llm_planner
        self._validator = validator
        self._optimizer = optimizer
        self._strategy_selector = strategy_selector or StrategySelector()
        self._rule_based_decomposer = rule_based_decomposer

    @property
    def llm_planner(self):
        """Lazy import to avoid circular dependencies."""
        if self._llm_planner is None:
            from .llm_planner import LLMPlanner
            self._llm_planner = LLMPlanner()
        return self._llm_planner

    def plan(
        self,
        goal_text: str,
        scope: str = "L1",
        capabilities: Optional[List[Dict[str, Any]]] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> Tuple[ParsedPlan, StrategySelection]:
        """Plan a goal using the best strategy.

        Args:
            goal_text: The natural language goal
            scope: Authorisation scope
            capabilities: Available capabilities
            context: Additional context

        Returns:
            Tuple of (ParsedPlan, StrategySelection)
        """
        # Step 1: Select strategy
        strategy_selection = self._strategy_selector.select(goal_text, context)
        strategy = strategy_selection.strategy

        # Step 2: Try LLM-based planning (if we have a provider)
        plan = None
        try:
            if hasattr(self.llm_planner, "has_provider") and self.llm_planner.has_provider():
                plan = self.llm_planner.plan(
                    goal_text=goal_text,
                    scope=scope,
                    capabilities=capabilities or [],
                    strategy=strategy,
                    context=context,
                )
        except Exception:
            # LLM planning failed — fall through to rule-based
            pass

        # Step 3: Fallback to rule-based if LLM failed or unavailable
        if plan is None and self._rule_based_decomposer is not None:
            plan = self._fallback_to_rule_based(goal_text, scope)
            strategy_selection.strategy = PlanningStrategy.RULE_BASED
            strategy_selection.selection_method = "fallback"

        # Step 4: Validate
        if plan is not None and self._validator is not None:
            validation = self._validator.validate(plan)
            # Store validation result on plan (as metadata)
            plan.parse_warnings.append(
                f"Validation: {'pass' if validation.is_valid else 'fail'} "
                f"({len(validation.errors)} errors, {len(validation.warnings)} warnings)"
            )

        # Step 5: Optimise
        if plan is not None and self._optimizer is not None:
            opt_result = self._optimizer.optimize(plan)
            plan.parse_warnings.append(
                f"Optimised: {len(opt_result.optimisations_applied)} optimisations applied"
            )

        if plan is None:
            # Last resort: empty plan
            plan = ParsedPlan(
                parse_success=False,
                parse_errors=["No planner available"],
            )

        return plan, strategy_selection

    def _fallback_to_rule_based(self, goal_text: str, scope: str) -> ParsedPlan:
        """Convert rule-based decomposer output into a ParsedPlan."""
        from src.kernels.execution import Goal, GoalDecomposer

        decomposer = self._rule_based_decomposer or GoalDecomposer()
        goal = Goal(id="fb_goal", natural_language=goal_text, scope=scope)
        tasks = decomposer.decompose(goal)

        parsed_tasks = []
        for i, task in enumerate(tasks):
            parsed = ParsedTask(
                id=task.id if hasattr(task, "id") else f"rule_task_{i}",
                name=task.name if hasattr(task, "name") else f"Task {i + 1}",
                description=getattr(task, "description", ""),
                capability_id=getattr(task, "capability_id", ""),
                inputs=getattr(task, "inputs", {}),
                depends_on=list(getattr(task, "depends_on", [])),
                is_valid=True,
            )
            parsed_tasks.append(parsed)

        return ParsedPlan(
            tasks=parsed_tasks,
            plan_summary=f"Rule-based plan (fallback) — {len(parsed_tasks)} tasks",
            parse_success=True,
        )
