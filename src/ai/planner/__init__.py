"""LLM-Powered Planning Engine for LiuHao AI OS.

This module replaces the regex-based GoalDecomposer with a true LLM-powered
planning system that can:

- Understand natural language goals semantically (not just keyword matching)
- Decompose complex goals into hierarchical task structures
- Validate plan feasibility against capability registry
- Optimise task ordering and parallelism
- Select from multiple planning strategies
- Learn from historical plans (plan memory)

Architecture:
    LLMPlanner (main entry point)
    ├── PlanPrompts (prompt templates)
    ├── PlanParser (parse LLM output into structured tasks)
    ├── PlanValidator (validate plan feasibility)
    ├── PlanOptimizer (optimise task ordering / parallelism)
    ├── MultiStrategyPlanner (select best planning strategy)
    └── PlanMemory (store and reuse historical plans)
"""
from __future__ import annotations

from .llm_planner import LLMPlanner, PlanResult, PlanningStrategy
from .parser import PlanParser, ParsedPlan, ParsedTask
from .validator import PlanValidator, ValidationResult, ValidationIssue
from .optimizer import PlanOptimizer, OptimizationResult
from .multi_strategy import MultiStrategyPlanner, StrategySelector
from .plan_memory import PlanMemory, PlanRecord

__all__ = [
    "LLMPlanner",
    "PlanResult",
    "PlanningStrategy",
    "PlanParser",
    "ParsedPlan",
    "ParsedTask",
    "PlanValidator",
    "ValidationResult",
    "ValidationIssue",
    "PlanOptimizer",
    "OptimizationResult",
    "MultiStrategyPlanner",
    "StrategySelector",
    "PlanMemory",
    "PlanRecord",
]
