"""LLM-powered Planner — the main entry point for intelligent goal decomposition.

This replaces the regex-based GoalDecomposer with a true LLM planning
system that understands natural language semantically.

Architecture (simplified)::

    goal ──► LLMPlanner.plan()
               │
               ├─► PlanMemory.lookup()  — check cache first
               │    ├─ exact match → return cached plan
               │    └─ no match → continue
               │
               ├─► prompts.build_decomposition_prompt()  — build prompt
               │
               ├─► provider.chat()  — call LLM
               │
               ├─► PlanParser.parse()  — parse JSON output
               │
               ├─► PlanValidator.validate()  — check against capabilities
               │
               ├─► PlanOptimizer.optimize()  — parallelise, merge, etc.
               │
               └─► PlanMemory.store()  — cache for next time
               │
               └──► ParsedPlan (structured tasks)

The LLMPlanner is designed to **fail gracefully**:
  - If no LLM provider is configured → returns None (caller falls back)
  - If LLM call fails → returns None
  - If output can't be parsed → plan.parse_success = False
  - If validation fails → plan has errors but is still returned
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from .parser import ParsedPlan, ParsedTask, PlanParser
from .validator import PlanValidator, ValidationResult
from .optimizer import PlanOptimizer, OptimizationResult
from .plan_memory import PlanMemory, PlanRecord
from .prompts import (
    build_decomposition_prompt,
    build_replanning_prompt,
    build_strategy_selection_prompt,
)
from .multi_strategy import PlanningStrategy


class PlanResultStatus(str, Enum):
    """Status of a planning attempt."""
    SUCCESS = "success"           # Plan generated and validated
    CACHE_HIT = "cache_hit"       # Returned from plan memory
    VALIDATION_FAILED = "validation_failed"  # Generated but didn't pass validation
    PARSE_FAILED = "parse_failed"  # LLM output couldn't be parsed
    PROVIDER_ERROR = "provider_error"  # LLM call failed
    NO_PROVIDER = "no_provider"    # No LLM provider configured
    FALLBACK_RULES = "fallback_rules"  # Used rule-based decomposer


@dataclass
class PlanResult:
    """Result of a planning operation."""
    status: PlanResultStatus
    plan: Optional[ParsedPlan] = None
    strategy: PlanningStrategy = PlanningStrategy.RULE_BASED
    duration_ms: float = 0.0
    validation: Optional[ValidationResult] = None
    optimization: Optional[OptimizationResult] = None
    cache_hit: bool = False
    error_message: str = ""
    llm_prompt_tokens: int = 0
    llm_completion_tokens: int = 0

    @property
    def is_success(self) -> bool:
        return self.status in (
            PlanResultStatus.SUCCESS,
            PlanResultStatus.CACHE_HIT,
            PlanResultStatus.FALLBACK_RULES,
        )

    @property
    def task_count(self) -> int:
        return self.plan.task_count if self.plan else 0


class LLMPlanner:
    """LLM-powered goal decomposition and planning.

    This is the main entry point for the planning engine. It wraps the
    parser, validator, optimizer, and memory into a single interface.

    Usage::

        planner = LLMPlanner(provider=my_llm_provider)
        result = planner.plan("Create a Python project with tests", scope="L2")
        if result.is_success:
            for task in result.plan.tasks:
                print(task.name, task.capability_id)
    """

    def __init__(
        self,
        provider=None,  # LLM provider (BaseProvider or LLMProvider)
        model: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: int = 2000,
        capabilities: Optional[List[Dict[str, Any]]] = None,
        validator: Optional[PlanValidator] = None,
        optimizer: Optional[PlanOptimizer] = None,
        memory: Optional[PlanMemory] = None,
        parser: Optional[PlanParser] = None,
        use_memory: bool = True,
        validate_output: bool = True,
        optimize_output: bool = True,
        enable_retry: bool = True,
        max_retries: int = 2,
    ):
        """
        Args:
            provider: LLM provider instance (optional — if None, planner
                     will return NO_PROVIDER status)
            model: Model name to use (defaults to provider's model)
            temperature: Sampling temperature (lower = more deterministic)
            max_tokens: Maximum output tokens
            capabilities: List of available capabilities
            validator: PlanValidator instance (auto-created if None)
            optimizer: PlanOptimizer instance (auto-created if None)
            memory: PlanMemory instance (auto-created if None and use_memory)
            parser: PlanParser instance (auto-created if None)
            use_memory: Whether to cache plans in memory
            validate_output: Whether to validate plans against capabilities
            optimize_output: Whether to optimise plans
            enable_retry: Whether to retry on parse failure
            max_retries: Maximum number of retries on parse failure
        """
        self._provider = provider
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._capabilities = capabilities or []

        self.parser = parser or PlanParser(strict=False)
        self.validator = validator or PlanValidator(capabilities=capabilities)
        self.optimizer = optimizer or PlanOptimizer()
        self.memory = memory or (PlanMemory() if use_memory else None)

        self.use_memory = use_memory
        self.validate_output = validate_output
        self.optimize_output = optimize_output
        self.enable_retry = enable_retry
        self.max_retries = max_retries

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def has_provider(self) -> bool:
        """Check if an LLM provider is configured."""
        return self._provider is not None

    def set_provider(self, provider) -> None:
        """Set or update the LLM provider."""
        self._provider = provider

    def set_capabilities(self, capabilities: List[Dict[str, Any]]) -> None:
        """Update the capability registry."""
        self._capabilities = capabilities
        self.validator.set_capabilities(capabilities)

    def plan(
        self,
        goal_text: str,
        scope: str = "L1",
        capabilities: Optional[List[Dict[str, Any]]] = None,
        strategy: PlanningStrategy = PlanningStrategy.LINEAR,
        context: Optional[Dict[str, Any]] = None,
    ) -> PlanResult:
        """Generate a plan for a goal.

        Args:
            goal_text: Natural language goal
            scope: Authorisation scope
            capabilities: Capabilities to use (overrides instance default)
            strategy: Planning strategy hint
            context: Additional context (memory, history, etc.)

        Returns:
            PlanResult with the plan (or error details)
        """
        start_time = time.time()
        caps = capabilities or self._capabilities

        # Step 1: Check memory cache
        if self.use_memory and self.memory is not None:
            cached = self.memory.lookup(goal_text, scope=scope)
            if cached is not None:
                plan = self._record_to_plan(cached)
                duration = (time.time() - start_time) * 1000
                return PlanResult(
                    status=PlanResultStatus.CACHE_HIT,
                    plan=plan,
                    strategy=strategy,
                    duration_ms=duration,
                    cache_hit=True,
                )

        # Step 2: Check we have a provider
        if self._provider is None:
            duration = (time.time() - start_time) * 1000
            return PlanResult(
                status=PlanResultStatus.NO_PROVIDER,
                duration_ms=duration,
                error_message="No LLM provider configured",
            )

        # Step 3: Build prompt
        messages = build_decomposition_prompt(
            goal_text=goal_text,
            scope=scope,
            capabilities=caps,
            context=context,
        )

        # Step 4: Call LLM (with retries for parse failures)
        raw_output = ""
        parse_errors: List[str] = []
        plan = None
        prompt_tokens = 0
        completion_tokens = 0

        for attempt in range(self.max_retries + 1):
            try:
                response = self._call_llm(messages)
                raw_output = response.get("content", "")
                prompt_tokens = response.get("prompt_tokens", 0)
                completion_tokens = response.get("completion_tokens", 0)
            except Exception as e:
                duration = (time.time() - start_time) * 1000
                return PlanResult(
                    status=PlanResultStatus.PROVIDER_ERROR,
                    duration_ms=duration,
                    error_message=f"LLM call failed: {e}",
                )

            # Step 5: Parse output
            plan = self.parser.parse(raw_output)
            if plan.parse_success:
                break

            parse_errors.extend(plan.parse_errors)

            # If retry is enabled and we have attempts left, try again
            # with a hint about what went wrong
            if self.enable_retry and attempt < self.max_retries:
                retry_hint = (
                    f"Previous attempt failed with parse errors: "
                    f"{'; '.join(plan.parse_errors[:3])}. "
                    f"Please output ONLY valid JSON with no markdown formatting."
                )
                messages.append({"role": "assistant", "content": raw_output})
                messages.append({"role": "user", "content": retry_hint})
                continue

            break  # No retries left or parsing succeeded

        # Step 6: If parse still failed, return failure
        if plan is None or not plan.parse_success:
            duration = (time.time() - start_time) * 1000
            return PlanResult(
                status=PlanResultStatus.PARSE_FAILED,
                plan=plan,
                strategy=strategy,
                duration_ms=duration,
                error_message="; ".join(parse_errors) if parse_errors else "Parse failed",
                llm_prompt_tokens=prompt_tokens,
                llm_completion_tokens=completion_tokens,
            )

        # Step 7: Validate
        validation = None
        if self.validate_output:
            validation = self.validator.validate(plan)
            # Even if validation has errors, we still return the plan
            # — the caller decides what to do with invalid tasks

        # Step 8: Optimise
        optimization = None
        if self.optimize_output:
            optimization = self.optimizer.optimize(plan)

        # Step 9: Store in memory
        if self.use_memory and self.memory is not None and plan.parse_success:
            self.memory.store(
                goal_text=goal_text,
                plan_data=self._plan_to_record_data(plan),
                strategy=strategy.value,
                scope=scope,
            )

        # Step 10: Return result
        duration = (time.time() - start_time) * 1000
        status = (
            PlanResultStatus.SUCCESS
            if plan.parse_success
            else PlanResultStatus.PARSE_FAILED
        )

        return PlanResult(
            status=status,
            plan=plan,
            strategy=strategy,
            duration_ms=duration,
            validation=validation,
            optimization=optimization,
            cache_hit=False,
            llm_prompt_tokens=prompt_tokens,
            llm_completion_tokens=completion_tokens,
        )

    def replan(
        self,
        goal_text: str,
        original_plan: ParsedPlan,
        failed_tasks: List[Dict[str, Any]],
        completed_tasks: List[Dict[str, Any]],
        error_summary: str,
        scope: str = "L1",
    ) -> PlanResult:
        """Generate a revised plan after execution failure.

        Args:
            goal_text: The original goal
            original_plan: The plan that was executed
            failed_tasks: Tasks that failed
            completed_tasks: Tasks that succeeded
            error_summary: Summary of what went wrong
            scope: Authorisation scope

        Returns:
            PlanResult with the revised plan
        """
        start_time = time.time()

        if self._provider is None:
            return PlanResult(
                status=PlanResultStatus.NO_PROVIDER,
                duration_ms=0.0,
                error_message="No LLM provider configured",
            )

        # Build replan prompt
        messages = build_replanning_prompt(
            goal_text=goal_text,
            original_plan={"tasks": [t.to_dict() for t in original_plan.tasks]},
            failed_tasks=failed_tasks,
            completed_tasks=completed_tasks,
            error_summary=error_summary,
        )

        # Call LLM
        try:
            response = self._call_llm(messages)
            raw_output = response.get("content", "")
        except Exception as e:
            duration = (time.time() - start_time) * 1000
            return PlanResult(
                status=PlanResultStatus.PROVIDER_ERROR,
                duration_ms=duration,
                error_message=f"LLM call failed: {e}",
            )

        # Parse
        plan = self.parser.parse(raw_output)

        # Validate
        validation = None
        if self.validate_output:
            validation = self.validator.validate(plan)

        # Optimise
        optimization = None
        if self.optimize_output:
            optimization = self.optimizer.optimize(plan)

        duration = (time.time() - start_time) * 1000
        return PlanResult(
            status=PlanResultStatus.SUCCESS if plan.parse_success else PlanResultStatus.PARSE_FAILED,
            plan=plan,
            strategy=PlanningStrategy.ITERATIVE,
            duration_ms=duration,
            validation=validation,
            optimization=optimization,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _call_llm(self, messages: List[Dict[str, str]]) -> Dict[str, Any]:
        """Call the LLM provider and return content + token info.

        Handles different provider interfaces gracefully.
        """
        provider = self._provider

        # Try the chat() method (from llm_base.py LLMProvider)
        if hasattr(provider, "chat"):
            response = provider.chat(
                messages=messages,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )
            # Extract content from response
            content = ""
            if isinstance(response, dict):
                content = response.get("content", "") or response.get("text", "")
                if not content and "choices" in response:
                    choices = response["choices"]
                    if choices and isinstance(choices, list):
                        msg = choices[0].get("message", {})
                        content = msg.get("content", "")
                prompt_tokens = response.get("prompt_tokens", 0)
                completion_tokens = response.get("completion_tokens", 0)
            else:
                content = str(response)
                prompt_tokens = 0
                completion_tokens = 0

            return {
                "content": content,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
            }

        # Fallback: try generate() method
        if hasattr(provider, "generate"):
            # Concatenate messages into a single prompt
            prompt = "\n".join(
                f"[{m.get('role', 'user')}]: {m.get('content', '')}"
                for m in messages
            )
            result = provider.generate(prompt)
            if isinstance(result, dict):
                return {
                    "content": result.get("content", "") or result.get("text", str(result)),
                    "prompt_tokens": result.get("prompt_tokens", 0),
                    "completion_tokens": result.get("completion_tokens", 0),
                }
            return {"content": str(result), "prompt_tokens": 0, "completion_tokens": 0}

        raise ValueError(f"Provider {type(provider).__name__} has no chat() or generate() method")

    def _record_to_plan(self, record: PlanRecord) -> ParsedPlan:
        """Reconstruct a ParsedPlan from a PlanRecord."""
        data = record.plan_data
        tasks_data = data.get("tasks", [])
        tasks = []
        for td in tasks_data:
            tasks.append(ParsedTask(
                id=td.get("id", ""),
                name=td.get("name", ""),
                description=td.get("description", ""),
                capability_id=td.get("capability_id", ""),
                inputs=td.get("inputs", {}),
                depends_on=td.get("depends_on", []),
                verification_criteria=td.get("verification_criteria", ""),
                is_valid=True,
            ))

        return ParsedPlan(
            tasks=tasks,
            clarification_needed=data.get("clarification_needed", False),
            clarification_reason=data.get("clarification_reason", ""),
            plan_summary=data.get("plan_summary", ""),
            estimated_complexity=data.get("estimated_complexity", "medium"),
            parallelizable=data.get("parallelizable", True),
            parse_success=True,
        )

    def _plan_to_record_data(self, plan: ParsedPlan) -> Dict[str, Any]:
        """Convert a ParsedPlan to serialisable dict for storage."""
        return {
            "tasks": [t.to_dict() for t in plan.tasks],
            "clarification_needed": plan.clarification_needed,
            "clarification_reason": plan.clarification_reason,
            "plan_summary": plan.plan_summary,
            "estimated_complexity": plan.estimated_complexity,
            "parallelizable": plan.parallelizable,
        }
