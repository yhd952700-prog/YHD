"""Tests for the LLMPlanner class."""
from __future__ import annotations

import json
import pytest

from src.ai.planner.llm_planner import LLMPlanner, PlanResult, PlanResultStatus
from src.ai.planner.parser import ParsedPlan, ParsedTask


class MockLLMProvider:
    """Mock LLM provider for testing."""

    def __init__(self, responses=None):
        self.responses = responses or []
        self.call_count = 0
        self.last_messages = None

    def chat(self, messages, temperature=1.0, max_tokens=None, **kwargs):
        self.call_count += 1
        self.last_messages = messages

        if self.call_count <= len(self.responses):
            response_text = self.responses[self.call_count - 1]
        else:
            # Default: return a simple valid plan
            response_text = json.dumps({
                "tasks": [
                    {
                        "id": "task_1",
                        "name": "Default task",
                        "description": "Mock response",
                        "capability_id": "WriteFile",
                        "inputs": {"path": "output.txt", "content": "hello"},
                        "depends_on": [],
                        "verification_criteria": "File exists",
                    }
                ],
                "clarification_needed": False,
                "plan_summary": "Mock plan",
                "estimated_complexity": "low",
                "parallelizable": True,
            })

        return {
            "content": response_text,
            "prompt_tokens": 100,
            "completion_tokens": 50,
        }


TEST_CAPABILITIES = [
    {
        "id": "WriteFile",
        "name": "Write File",
        "description": "Write to a file",
        "allowed_scopes": ["L1", "L2", "L3"],
        "inputs_schema": {
            "path": {"type": "string", "required": True},
            "content": {"type": "string", "required": True},
        },
    },
    {
        "id": "ReadFile",
        "name": "Read File",
        "description": "Read a file",
        "allowed_scopes": ["L1", "L2", "L3"],
        "inputs_schema": {
            "path": {"type": "string", "required": True},
        },
    },
]


class TestLLMPlannerInit:
    """Test LLMPlanner initialisation."""

    def test_init_without_provider(self):
        """Planner should initialise without a provider."""
        planner = LLMPlanner()
        assert planner.has_provider() is False

    def test_init_with_provider(self):
        """Planner should initialise with a provider."""
        provider = MockLLMProvider()
        planner = LLMPlanner(provider=provider)
        assert planner.has_provider() is True

    def test_set_provider(self):
        """set_provider should update the provider."""
        planner = LLMPlanner()
        assert planner.has_provider() is False

        planner.set_provider(MockLLMProvider())
        assert planner.has_provider() is True

    def test_set_capabilities(self):
        """set_capabilities should update the validator."""
        planner = LLMPlanner()
        planner.set_capabilities(TEST_CAPABILITIES)
        assert len(planner._capabilities) == 2


class TestLLMPlannerPlan:
    """Test the plan() method."""

    def test_plan_no_provider_returns_no_provider_status(self):
        """Without a provider, plan() should return NO_PROVIDER."""
        planner = LLMPlanner()
        result = planner.plan("test goal", scope="L1")

        assert result.status == PlanResultStatus.NO_PROVIDER
        assert result.is_success is False
        assert result.plan is None
        assert "No LLM provider" in result.error_message
        assert result.duration_ms >= 0

    def test_plan_with_provider_returns_success(self):
        """With a provider, plan() should return a valid plan."""
        provider = MockLLMProvider()
        planner = LLMPlanner(provider=provider, capabilities=TEST_CAPABILITIES)

        result = planner.plan("write a file", scope="L1")

        assert result.status == PlanResultStatus.SUCCESS
        assert result.is_success is True
        assert result.plan is not None
        assert result.plan.parse_success is True
        assert result.task_count >= 1
        assert result.llm_prompt_tokens > 0
        assert result.llm_completion_tokens > 0
        assert result.duration_ms >= 0

    def test_plan_uses_memory_cache(self):
        """Second call with same goal should hit cache."""
        provider = MockLLMProvider()
        planner = LLMPlanner(
            provider=provider,
            capabilities=TEST_CAPABILITIES,
            use_memory=True,
        )

        # First call — should call LLM
        result1 = planner.plan("write a file called cache.txt with content hello", scope="L1")
        assert result1.status == PlanResultStatus.SUCCESS
        assert result1.cache_hit is False
        assert provider.call_count == 1

        # Second call — should hit cache
        result2 = planner.plan("write a file called cache.txt with content hello", scope="L1")
        assert result2.status == PlanResultStatus.CACHE_HIT
        assert result2.cache_hit is True
        assert provider.call_count == 1  # No additional LLM call

    def test_plan_memory_disabled(self):
        """When use_memory=False, no caching should happen."""
        provider = MockLLMProvider()
        planner = LLMPlanner(
            provider=provider,
            capabilities=TEST_CAPABILITIES,
            use_memory=False,
        )

        result1 = planner.plan("test goal", scope="L1")
        result2 = planner.plan("test goal", scope="L1")

        assert provider.call_count == 2  # Both calls hit LLM
        assert result1.cache_hit is False
        assert result2.cache_hit is False

    def test_plan_validation_runs(self):
        """Validation should run when validate_output is True."""
        provider = MockLLMProvider()
        planner = LLMPlanner(
            provider=provider,
            capabilities=TEST_CAPABILITIES,
            validate_output=True,
        )

        result = planner.plan("test goal", scope="L1")

        assert result.validation is not None
        assert isinstance(result.validation.is_valid, bool)

    def test_plan_optimization_runs(self):
        """Optimization should run when optimize_output is True."""
        provider = MockLLMProvider()
        planner = LLMPlanner(
            provider=provider,
            capabilities=TEST_CAPABILITIES,
            optimize_output=True,
        )

        result = planner.plan("test goal", scope="L1")

        assert result.optimization is not None
        assert result.optimization.plan is result.plan

    def test_plan_parse_failure_retry(self):
        """On parse failure, planner should retry if enabled."""
        # First response is bad JSON; after retries use default (valid JSON)
        provider = MockLLMProvider(responses=[
            "this is not json at all",  # Bad response (1st call)
        ])
        # With max_retries=1: 1 original + 1 retry = 2 calls
        # Second call uses the default valid JSON
        planner = LLMPlanner(
            provider=provider,
            capabilities=TEST_CAPABILITIES,
            enable_retry=True,
            max_retries=1,
        )

        result = planner.plan("test goal", scope="L1")

        # Should have retried (2 calls total)
        assert provider.call_count == 2
        assert result.status == PlanResultStatus.SUCCESS

    def test_plan_parse_failure_no_retry(self):
        """When retry is disabled, parse failure should return error."""
        provider = MockLLMProvider(responses=["not valid json"])
        planner = LLMPlanner(
            provider=provider,
            enable_retry=False,
        )

        result = planner.plan("test goal", scope="L1")

        assert provider.call_count == 1
        assert result.status == PlanResultStatus.PARSE_FAILED
        assert result.is_success is False

    def test_plan_provider_error(self):
        """Provider exceptions should be caught and reported."""
        class ErrorProvider:
            def chat(self, messages, **kwargs):
                raise RuntimeError("Connection failed")

        planner = LLMPlanner(provider=ErrorProvider())
        result = planner.plan("test goal", scope="L1")

        assert result.status == PlanResultStatus.PROVIDER_ERROR
        assert "Connection failed" in result.error_message


class TestLLMPlannerReplan:
    """Test the replan() method."""

    def test_replan_with_provider(self):
        """replan() should call the LLM with failure context."""
        provider = MockLLMProvider()
        planner = LLMPlanner(
            provider=provider,
            capabilities=TEST_CAPABILITIES,
        )

        original_plan = ParsedPlan(
            tasks=[
                ParsedTask(id="t1", name="Task 1", capability_id="WriteFile", inputs={}),
            ],
            parse_success=True,
        )

        result = planner.replan(
            goal_text="write a file",
            original_plan=original_plan,
            failed_tasks=[{"id": "t1", "name": "Task 1", "status": "failed", "error": "Permission denied"}],
            completed_tasks=[],
            error_summary="Permission denied when writing file",
            scope="L1",
        )

        assert result.is_success is True
        assert provider.call_count == 1

    def test_replan_no_provider(self):
        """replan() without provider should return NO_PROVIDER."""
        planner = LLMPlanner()
        result = planner.replan(
            goal_text="test",
            original_plan=ParsedPlan(tasks=[], parse_success=True),
            failed_tasks=[],
            completed_tasks=[],
            error_summary="test error",
        )

        assert result.status == PlanResultStatus.NO_PROVIDER


class TestPlanResult:
    """Test the PlanResult dataclass."""

    def test_is_success_for_success_status(self):
        result = PlanResult(status=PlanResultStatus.SUCCESS)
        assert result.is_success is True

    def test_is_success_for_cache_hit(self):
        result = PlanResult(status=PlanResultStatus.CACHE_HIT)
        assert result.is_success is True

    def test_is_success_for_fallback(self):
        result = PlanResult(status=PlanResultStatus.FALLBACK_RULES)
        assert result.is_success is True

    def test_is_failure_for_parse_failed(self):
        result = PlanResult(status=PlanResultStatus.PARSE_FAILED)
        assert result.is_success is False

    def test_is_failure_for_provider_error(self):
        result = PlanResult(status=PlanResultStatus.PROVIDER_ERROR)
        assert result.is_success is False

    def test_is_failure_for_no_provider(self):
        result = PlanResult(status=PlanResultStatus.NO_PROVIDER)
        assert result.is_success is False

    def test_task_count_with_plan(self):
        plan = ParsedPlan(tasks=[
            ParsedTask(id="t1", name="T1", capability_id="X"),
            ParsedTask(id="t2", name="T2", capability_id="Y"),
        ])
        result = PlanResult(status=PlanResultStatus.SUCCESS, plan=plan)
        assert result.task_count == 2

    def test_task_count_without_plan(self):
        result = PlanResult(status=PlanResultStatus.NO_PROVIDER)
        assert result.task_count == 0
