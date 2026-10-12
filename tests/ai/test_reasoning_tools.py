"""Tests for the reasoning and tools modules.

Covers:
- Chain-of-Thought reasoning
- Reflection engine
- ReAct agent
- Tool base framework and registry
- Built-in tools (calculator, search, memory, datetime, file, code_runner)
- Tool executor
"""
from __future__ import annotations

import pytest
import tempfile
import os
from pathlib import Path

from src.ai.reasoning import (
    ChainOfThought,
    ChainOfThoughtResult,
    ReasoningStep,
    ReasoningStrategy,
    StepType,
    Contradiction,
    ReflectionEngine,
    ReflectionResult,
    ReflectionEntry,
    ReflectionPhase,
    SelfCritique,
    CritiqueScore,
    CritiqueDimension,
    IterationDelta,
    ReActAgent,
    ReActResult,
    ReActStep,
    ReActStepType,
    ReActDecision,
    ToolResult as ReactToolResult,
    StopReason,
)

from src.ai.tools import (
    BaseTool,
    ToolRegistry,
    ToolResult,
    ParameterSchema,
    ToolCategory,
    ToolStatus,
    ResultStatus,
    CalculatorTool,
    SearchTool,
    CodeRunnerTool,
    MemoryTool,
    DateTimeTool,
    FileTool,
    ToolExecutor,
    ToolCandidate,
    ToolCallStats,
    ToolExecutorStats,
    BatchResult,
)


# ======================================================================
# Chain-of-Thought Tests
# ======================================================================

class TestChainOfThought:
    """Test the Chain-of-Thought reasoning engine."""

    def test_create_premise(self):
        """Test adding a premise to the reasoning chain."""
        cot = ChainOfThought(strategy=ReasoningStrategy.ANALYTICAL)
        step = cot.add_premise("All humans are mortal")

        assert step.step_type == StepType.PREMISE
        assert step.content == "All humans are mortal"
        assert step.confidence == 1.0
        assert step.premises == []
        assert cot.step_count == 1

    def test_add_inference(self):
        """Test adding an inference based on premises."""
        cot = ChainOfThought()
        p1 = cot.add_premise("All humans are mortal")
        p2 = cot.add_premise("Socrates is human")

        inference = cot.add_inference(
            "Socrates is mortal",
            premises=[p1.step_id, p2.step_id],
            justification="syllogism",
            confidence=0.95,
        )

        assert inference.step_type == StepType.INFERENCE
        assert inference.content == "Socrates is mortal"
        assert inference.confidence == 0.95
        assert len(inference.premises) == 2
        assert cot.step_count == 3

    def test_inference_with_unknown_premise_raises(self):
        """Test that inference with unknown premise ID raises ValueError."""
        cot = ChainOfThought()
        cot.add_premise("A premise")

        with pytest.raises(ValueError, match="Unknown premise step id"):
            cot.add_inference("Conclusion", premises=["nonexistent_id"])

    def test_sub_conclusion(self):
        """Test adding a sub-conclusion."""
        cot = ChainOfThought()
        p = cot.add_premise("The sky is blue")

        sub = cot.add_sub_conclusion(
            "Blue skies suggest clear weather",
            premises=[p.step_id],
            confidence=0.8,
        )

        assert sub.step_type == StepType.SUB_CONCLUSION
        assert sub.confidence == 0.8
        assert len(sub.premises) == 1

    def test_correction_step(self):
        """Test adding a correction to a previous step."""
        cot = ChainOfThought()
        p = cot.add_premise("2 + 2 = 5", confidence=0.5)

        correction = cot.add_correction(
            target_step_id=p.step_id,
            corrected_content="2 + 2 = 4",
            reason="arithmetic error",
            confidence=0.99,
        )

        assert correction.step_type == StepType.CORRECTION
        assert correction.metadata["corrects"] == p.step_id
        assert correction.metadata["reason"] == "arithmetic error"
        assert correction.metadata["original"] == "2 + 2 = 5"

    def test_correction_unknown_step_raises(self):
        """Test correction with unknown step ID raises ValueError."""
        cot = ChainOfThought()
        with pytest.raises(ValueError, match="Unknown step id"):
            cot.add_correction("fake_id", "corrected", "reason")

    def test_finalize_with_conclusion(self):
        """Test finalizing the chain with a final conclusion."""
        cot = ChainOfThought()
        p1 = cot.add_premise("All birds have wings")
        p2 = cot.add_premise("Penguins are birds")

        result = cot.finalize(
            conclusion="Penguins have wings",
            justification="deduction from premises",
        )

        assert isinstance(result, ChainOfThoughtResult)
        assert result.final_conclusion is not None
        assert result.final_conclusion.content == "Penguins have wings"
        assert result.final_conclusion.step_type == StepType.FINAL_CONCLUSION
        assert result.strategy == ReasoningStrategy.ANALYTICAL
        assert result.duration_ms >= 0
        assert len(result.steps) >= 2

    def test_contradiction_detection(self):
        """Test heuristic contradiction detection."""
        cot = ChainOfThought()
        cot.add_premise("All birds can fly")
        cot.add_premise("All birds cannot fly")

        contradictions = cot.detect_contradictions()
        assert len(contradictions) > 0
        assert all(isinstance(c, Contradiction) for c in contradictions)
        assert all(0 < c.severity <= 1.0 for c in contradictions)

    def test_no_contradiction_in_consistent_chain(self):
        """Test that consistent premises don't trigger false positives."""
        cot = ChainOfThought()
        cot.add_premise("The sun rises in the east")
        cot.add_premise("Water is wet")

        contradictions = cot.detect_contradictions()
        # No direct negation patterns between these
        assert len(contradictions) == 0

    def test_trace_generation(self):
        """Test human-readable trace generation."""
        cot = ChainOfThought()
        p1 = cot.add_premise("Premise A")
        cot.add_inference("Inference B", premises=[p1.step_id],
                          justification="modus ponens")

        trace = cot.trace()
        assert "[1] (premise)" in trace
        assert "[2] (inference)" in trace
        assert "modus ponens" in trace
        assert "based on steps" in trace

    def test_multiple_strategies(self):
        """Test using different reasoning strategies."""
        for strategy in ReasoningStrategy:
            cot = ChainOfThought(strategy=strategy)
            step = cot.add_premise("test")
            assert step.strategy == strategy

    def test_get_step_by_id(self):
        """Test retrieving a step by its ID."""
        cot = ChainOfThought()
        step = cot.add_premise("test premise")

        retrieved = cot.get_step(step.step_id)
        assert retrieved is not None
        assert retrieved.content == "test premise"

        assert cot.get_step("nonexistent") is None

    def test_steps_by_type(self):
        """Test filtering steps by type."""
        cot = ChainOfThought()
        p = cot.add_premise("P")
        i = cot.add_inference("I", premises=[p.step_id])

        premises = cot.steps_by_type(StepType.PREMISE)
        inferences = cot.steps_by_type(StepType.INFERENCE)

        assert len(premises) == 1
        assert len(inferences) == 1
        assert premises[0].step_id == p.step_id
        assert inferences[0].step_id == i.step_id


# ======================================================================
# Reflection Engine Tests
# ======================================================================

class TestReflectionEngine:
    """Test the reflection and self-improvement engine."""

    def test_create_critique(self):
        """Test creating a structured self-critique."""
        engine = ReflectionEngine()
        critique = engine.create_critique(
            overall_score=0.75,
            dimension_scores={
                CritiqueDimension.ACCURACY: 0.8,
                CritiqueDimension.CLARITY: 0.7,
            },
            summary="Decent output with room for improvement",
            strengths=["Well structured"],
            weaknesses=["Lacks depth"],
            suggestions=["Add more examples"],
        )

        assert isinstance(critique, SelfCritique)
        assert critique.overall_score == 0.75
        assert len(critique.scores) == 2
        assert critique.summary == "Decent output with room for improvement"
        assert len(critique.strengths) == 1
        assert len(critique.weaknesses) == 1
        assert len(critique.suggestions) == 1

    def test_critique_passed_threshold(self):
        """Test the passed property of SelfCritique."""
        high = SelfCritique(overall_score=0.85, scores=[], summary="",
                            strengths=[], weaknesses=[], suggestions=[])
        low = SelfCritique(overall_score=0.5, scores=[], summary="",
                           strengths=[], weaknesses=[], suggestions=[])

        assert high.passed is True
        assert low.passed is False

    def test_reflection_single_iteration(self):
        """Test reflection with a single iteration that meets target."""
        engine = ReflectionEngine(max_iterations=3, target_score=0.8)

        def generate(context, critique=None):
            return "Initial output"

        def evaluate(output, context):
            return SelfCritique(
                overall_score=0.9,
                scores=[CritiqueScore(CritiqueDimension.ACCURACY, 0.9, "Good")],
                summary="Great output",
                strengths=["Accurate"],
                weaknesses=[],
                suggestions=[],
            )

        result = engine.reflect(generate, evaluate, context="test")

        assert isinstance(result, ReflectionResult)
        assert result.iterations >= 1
        assert result.final_score == 0.9
        assert result.converged is True
        assert result.final_output == "Initial output"

    def test_reflection_improves_over_iterations(self):
        """Test that reflection tracks improvement across iterations."""
        engine = ReflectionEngine(max_iterations=5, convergence_threshold=0.01)

        call_count = {"n": 0}

        def generate(context, critique=None):
            call_count["n"] += 1
            return f"Output v{call_count['n']}"

        def evaluate(output, context):
            # Score increases with each call
            base = 0.5 + 0.1 * call_count["n"]
            score = min(0.95, base)
            return SelfCritique(
                overall_score=score,
                scores=[],
                summary=f"Score: {score}",
                strengths=["Getting better"],
                weaknesses=["Needs more work"],
                suggestions=["Improve"],
            )

        result = engine.reflect(generate, evaluate, context="test")

        assert result.iterations >= 2
        assert len(result.improvements) >= 1
        assert result.final_score > 0.5
        # First improvement should be positive
        assert result.improvements[0].overall_change > 0

    def test_reflection_max_iterations(self):
        """Test that reflection stops at max_iterations."""
        engine = ReflectionEngine(max_iterations=2, target_score=1.0,
                                   convergence_threshold=0.0)

        def generate(context, critique=None):
            return "stuck"

        def evaluate(output, context):
            return SelfCritique(
                overall_score=0.5,
                scores=[],
                summary="stuck at 0.5",
                strengths=[],
                weaknesses=[],
                suggestions=[],
            )

        result = engine.reflect(generate, evaluate, context="test")
        assert result.iterations == 2
        assert result.converged is False  # Didn't hit target, hit max iterations

    def test_reflection_with_initial_output(self):
        """Test reflection starting from an initial output."""
        engine = ReflectionEngine(max_iterations=3, target_score=0.8)
        call_count = {"n": 0}

        def generate(context, critique=None):
            call_count["n"] += 1
            return "Generated output"

        def evaluate(output, context):
            return SelfCritique(
                overall_score=0.9,
                scores=[],
                summary="",
                strengths=[],
                weaknesses=[],
                suggestions=[],
            )

        result = engine.reflect(
            generate, evaluate,
            context="test",
            initial_output="Pre-existing output",
        )

        # Should evaluate the initial output first
        assert result.final_output == "Pre-existing output"
        assert call_count["n"] == 0  # No generation needed

    def test_journal_tracking(self):
        """Test that the reflection journal records all phases."""
        engine = ReflectionEngine(max_iterations=2, target_score=0.9,
                                   convergence_threshold=0.0)

        def generate(context, critique=None):
            return "output"

        def evaluate(output, context):
            return SelfCritique(
                overall_score=0.7,
                scores=[],
                summary="",
                strengths=[],
                weaknesses=[],
                suggestions=["improve"],
            )

        engine.reflect(generate, evaluate, context="test")

        journal = engine.journal
        phases = [e.phase for e in journal]

        assert ReflectionPhase.ACTION in phases
        assert ReflectionPhase.OBSERVATION in phases
        assert ReflectionPhase.REFLECTION in phases
        assert ReflectionPhase.IMPROVEMENT in phases

    def test_score_history(self):
        """Test score history tracking across iterations."""
        engine = ReflectionEngine(max_iterations=3, convergence_threshold=0.0)
        scores = [0.5, 0.7, 0.85]
        idx = {"n": 0}

        def generate(context, critique=None):
            return f"out_{idx['n']}"

        def evaluate(output, context):
            score = scores[idx["n"]]
            idx["n"] += 1
            return SelfCritique(
                overall_score=score,
                scores=[],
                summary="",
                strengths=[],
                weaknesses=[],
                suggestions=[],
            )

        engine.reflect(generate, evaluate, context="test")
        history = engine.score_history()

        assert len(history) == 3
        assert history[0] == 0.5
        assert history[1] == 0.7
        assert history[2] == 0.85

    def test_critique_score_to_dict(self):
        """Test CritiqueScore serialization."""
        cs = CritiqueScore(
            dimension=CritiqueDimension.CLARITY,
            score=0.85,
            feedback="Clear and concise",
        )
        d = cs.to_dict()
        assert d["dimension"] == "clarity"
        assert d["score"] == 0.85
        assert d["feedback"] == "Clear and concise"


# ======================================================================
# ReAct Agent Tests
# ======================================================================

class TestReActAgent:
    """Test the ReAct (Reasoning + Acting) agent."""

    def test_react_final_answer(self):
        """Test ReAct agent reaching a final answer."""
        agent = ReActAgent(max_steps=5)

        def thinker(question, history, available_tools, context):
            return ReActDecision.final("I know the answer", "42")

        def actor(tool_name, tool_input):
            return ReactToolResult.ok("result")

        result = agent.run(
            question="What is the answer?",
            think_fn=thinker,
            act_fn=actor,
            available_tools=["calculator"],
        )

        assert isinstance(result, ReActResult)
        assert result.success is True
        assert result.stop_reason == StopReason.GOAL_REACHED
        assert result.final_answer == "42"

    def test_react_action_then_answer(self):
        """Test ReAct taking an action then giving final answer."""
        agent = ReActAgent(max_steps=5)
        step_count = {"n": 0}

        def thinker(question, history, available_tools, context):
            step_count["n"] += 1
            if step_count["n"] == 1:
                return ReActDecision.action(
                    "Need to calculate",
                    "calculator",
                    {"expression": "6 * 7"},
                )
            else:
                return ReActDecision.final("Based on calculation", "42")

        def actor(tool_name, tool_input):
            return ReactToolResult.ok("42")

        result = agent.run(
            question="What is 6*7?",
            think_fn=thinker,
            act_fn=actor,
            available_tools=["calculator"],
        )

        assert result.success is True
        assert result.actions_taken == 1
        assert result.stop_reason == StopReason.GOAL_REACHED

    def test_react_max_steps(self):
        """Test ReAct stopping at max steps."""
        agent = ReActAgent(max_steps=3)

        def thinker(question, history, available_tools, context):
            return ReActDecision.action(
                "Let me search",
                "search",
                {"query": "test"},
            )

        def actor(tool_name, tool_input):
            return ReactToolResult.ok("No results found")

        result = agent.run(
            question="Find something",
            think_fn=thinker,
            act_fn=actor,
            available_tools=["search"],
        )

        assert result.success is False
        assert result.stop_reason == StopReason.MAX_STEPS
        assert result.actions_taken == 3

    def test_react_no_progress_detection(self):
        """Test ReAct detecting no progress / looping."""
        agent = ReActAgent(max_steps=10, max_no_progress_steps=2)

        def thinker(question, history, available_tools, context):
            return ReActDecision.action(
                "Same action every time",
                "search",
                {"query": "same query"},
            )

        def actor(tool_name, tool_input):
            return ReactToolResult.ok("same result")

        result = agent.run(
            question="Stuck loop",
            think_fn=thinker,
            act_fn=actor,
            available_tools=["search"],
        )

        assert result.stop_reason == StopReason.NO_PROGRESS
        assert result.success is False

    def test_react_action_error(self):
        """Test ReAct handling action errors."""
        agent = ReActAgent(max_steps=3)
        step = {"n": 0}

        def thinker(question, history, available_tools, context):
            step["n"] += 1
            if step["n"] == 1:
                return ReActDecision.action("Try it", "bad_tool", {})
            return ReActDecision.final("Couldn't use tool", "default")

        def actor(tool_name, tool_input):
            return ReactToolResult.fail("Tool not found")

        result = agent.run(
            question="Test error",
            think_fn=thinker,
            act_fn=actor,
            available_tools=["bad_tool"],
        )

        assert result.stop_reason == StopReason.GOAL_REACHED
        observations = agent.observations()
        assert len(observations) >= 1

    def test_react_trace_generation(self):
        """Test ReAct trace generation."""
        agent = ReActAgent(max_steps=2)
        step = {"n": 0}

        def thinker(question, history, available_tools, context):
            step["n"] += 1
            if step["n"] == 1:
                return ReActDecision.action("Thinking", "calculator", {"expression": "1+1"})
            return ReActDecision.final("Done", "2")

        def actor(tool_name, tool_input):
            return ReactToolResult.ok("2")

        result = agent.run(
            question="1+1?",
            think_fn=thinker,
            act_fn=actor,
            available_tools=["calculator"],
        )

        trace = result.trace
        assert "THOUGHT:" in trace
        assert "ACTION:" in trace or "action" in trace.lower()
        assert "OBSERVATION:" in trace
        assert "FINAL_ANSWER:" in trace

    def test_react_fatal_error_stops(self):
        """Test that fatal errors stop the ReAct loop."""
        agent = ReActAgent(max_steps=5)
        step = {"n": 0}

        def thinker(question, history, available_tools, context):
            step["n"] += 1
            return ReActDecision.action("Try", "dangerous", {})

        def actor(tool_name, tool_input):
            return ReactToolResult.fail("Fatal explosion", fatal=True)

        result = agent.run(
            question="Fatal test",
            think_fn=thinker,
            act_fn=actor,
            available_tools=["dangerous"],
        )

        assert result.stop_reason == StopReason.ERROR
        assert result.actions_taken == 1

    def test_react_decision_helpers(self):
        """Test ReActDecision helper class methods."""
        final = ReActDecision.final("Got it", "answer42")
        assert final.is_final is True
        assert final.final_answer == "answer42"
        assert final.action_name is None

        action = ReActDecision.action("Let me calculate", "calc", {"x": 1})
        assert action.is_final is False
        assert action.action_name == "calc"
        assert action.action_input == {"x": 1}

    def test_react_action_history(self):
        """Test action history retrieval."""
        agent = ReActAgent(max_steps=3)
        step = {"n": 0}

        def thinker(question, history, available_tools, context):
            step["n"] += 1
            if step["n"] <= 2:
                return ReActDecision.action("Searching", "search", {"query": f"q{step['n']}"})
            return ReActDecision.final("Done", "result")

        def actor(tool_name, tool_input):
            return ReactToolResult.ok(f"result_{tool_input.get('query')}")

        agent.run(
            question="Multi-step",
            think_fn=thinker,
            act_fn=actor,
            available_tools=["search"],
        )

        history = agent.action_history()
        assert len(history) == 2
        assert history[0]["action"] == "search"
        assert history[1]["action"] == "search"


# ======================================================================
# Tool Base & Registry Tests
# ======================================================================

class TestParameterSchema:
    """Test parameter schema validation."""

    def test_required_parameter_missing(self):
        """Test that missing required parameters fail validation."""
        schema = ParameterSchema(name="query", type=str, required=True)
        valid, error = schema.validate(None)
        assert valid is False
        assert "required" in error.lower()

    def test_type_mismatch(self):
        """Test type mismatch detection."""
        schema = ParameterSchema(name="count", type=int, required=True)
        valid, error = schema.validate("not a number")
        assert valid is False
        assert "type" in error.lower()

    def test_choices_validation(self):
        """Test choices/enum validation."""
        schema = ParameterSchema(
            name="color", type=str, required=True,
            choices=["red", "green", "blue"],
        )
        assert schema.validate("red")[0] is True
        assert schema.validate("yellow")[0] is False

    def test_min_max_value(self):
        """Test numeric min/max bounds."""
        schema = ParameterSchema(name="age", type=int, min_value=0, max_value=150)
        assert schema.validate(25)[0] is True
        assert schema.validate(-1)[0] is False
        assert schema.validate(200)[0] is False

    def test_min_max_length(self):
        """Test string length bounds."""
        schema = ParameterSchema(name="name", type=str, min_length=2, max_length=50)
        assert schema.validate("Alice")[0] is True
        assert schema.validate("A")[0] is False
        assert schema.validate("x" * 100)[0] is False

    def test_optional_with_default(self):
        """Test optional parameter with default passes None."""
        schema = ParameterSchema(name="opt", type=str, required=False, default="hello")
        valid, _ = schema.validate(None)
        assert valid is True  # Optional with default is fine with None

    def test_bool_not_int(self):
        """Test that booleans are not accepted for int parameters."""
        schema = ParameterSchema(name="count", type=int, required=True)
        valid, _ = schema.validate(True)
        assert valid is False


class TestToolRegistry:
    """Test the tool registry."""

    def test_register_and_get(self):
        """Test registering and retrieving a tool."""
        registry = ToolRegistry()
        tool = CalculatorTool()
        name = registry.register(tool)

        assert name == "calculator"
        assert registry.has("calculator")
        retrieved = registry.get("calculator")
        assert retrieved is not None
        assert retrieved.name == "calculator"

    def test_register_duplicate_raises(self):
        """Test that duplicate registration raises ValueError."""
        registry = ToolRegistry()
        registry.register(CalculatorTool())

        with pytest.raises(ValueError, match="already registered"):
            registry.register(CalculatorTool())

    def test_unregister(self):
        """Test unregistering a tool."""
        registry = ToolRegistry()
        registry.register(CalculatorTool())
        assert registry.has("calculator")

        result = registry.unregister("calculator")
        assert result is True
        assert not registry.has("calculator")

    def test_unregister_nonexistent(self):
        """Test unregistering a tool that doesn't exist."""
        registry = ToolRegistry()
        assert registry.unregister("nonexistent") is False

    def test_by_category(self):
        """Test looking up tools by category."""
        registry = ToolRegistry()
        registry.register(CalculatorTool())
        registry.register(DateTimeTool())

        comp_tools = registry.by_category(ToolCategory.COMPUTATION)
        util_tools = registry.by_category(ToolCategory.UTILITY)

        assert len(comp_tools) == 1
        assert comp_tools[0].name == "calculator"
        assert len(util_tools) == 1
        assert util_tools[0].name == "datetime"

    def test_list_names(self):
        """Test listing all tool names."""
        registry = ToolRegistry()
        registry.register(CalculatorTool())
        registry.register(SearchTool())

        names = registry.list_names()
        assert "calculator" in names
        assert "search" in names
        assert len(names) == 2

    def test_stats(self):
        """Test registry statistics."""
        registry = ToolRegistry()
        registry.register(CalculatorTool())
        registry.register(SearchTool())

        stats = registry.stats()
        assert stats["total_tools"] == 2
        assert "categories" in stats
        assert stats["total_calls"] == 0

    def test_get_nonexistent_returns_none(self):
        """Test getting a nonexistent tool returns None."""
        registry = ToolRegistry()
        assert registry.get("nonexistent") is None


# ======================================================================
# Built-in Tools Tests
# ======================================================================

class TestCalculatorTool:
    """Test the calculator tool."""

    def test_basic_arithmetic(self):
        """Test basic arithmetic operations."""
        calc = CalculatorTool()
        result = calc.execute(expression="2 + 3")
        assert result.ok
        assert result.output["result"] == 5

    def test_complex_expression(self):
        """Test more complex expressions."""
        calc = CalculatorTool()
        result = calc.execute(expression="(2 + 3) * 4")
        assert result.ok
        assert result.output["result"] == 20

    def test_math_functions(self):
        """Test math functions like sqrt, sin, etc."""
        calc = CalculatorTool()
        result = calc.execute(expression="sqrt(16)")
        assert result.ok
        assert result.output["result"] == 4.0

    def test_constants(self):
        """Test math constants."""
        calc = CalculatorTool()
        result = calc.execute(expression="pi * 2")
        assert result.ok
        import math
        assert abs(result.output["result"] - math.pi * 2) < 0.001

    def test_division_by_zero(self):
        """Test division by zero error handling."""
        calc = CalculatorTool()
        result = calc.execute(expression="1 / 0")
        assert not result.ok
        assert "zero" in result.error.lower()

    def test_dangerous_expression_rejected(self):
        """Test that dangerous expressions are rejected."""
        calc = CalculatorTool()
        result = calc.execute(expression="__import__('os')")
        assert not result.ok
        assert "disallowed" in result.error.lower()

    def test_invalid_expression(self):
        """Test invalid syntax handling."""
        calc = CalculatorTool()
        result = calc.execute(expression="2 +* 3")
        assert not result.ok


class TestSearchTool:
    """Test the search tool."""

    def test_search_finds_results(self):
        """Test that search returns relevant results."""
        search = SearchTool()
        result = search.execute(query="python")
        assert result.ok
        assert result.output["total_results"] >= 1
        assert len(result.output["results"]) >= 1

    def test_search_no_results(self):
        """Test search with no matching results."""
        search = SearchTool()
        result = search.execute(query="xyzzy_nonexistent_term_12345")
        assert result.ok
        assert result.output["total_results"] == 0

    def test_search_max_results(self):
        """Test max_results parameter."""
        search = SearchTool()
        result = search.execute(query="a", max_results=2)
        assert result.ok
        assert len(result.output["results"]) <= 2

    def test_search_domain_filter(self):
        """Test domain filtering."""
        search = SearchTool()
        result = search.execute(query="python", domain="knowledge://")
        assert result.ok
        # All results should have knowledge:// URLs
        for r in result.output["results"]:
            assert "knowledge://" in r["url"]


class TestMemoryTool:
    """Test the memory tool."""

    def test_add_and_get(self):
        """Test adding and retrieving a memory item."""
        tool = MemoryTool()
        add_result = tool.execute(operation="add", content="Test memory item")
        assert add_result.ok
        item_id = add_result.output["item"]["id"]

        get_result = tool.execute(operation="get", item_id=item_id)
        assert get_result.ok
        assert get_result.output["found"] is True
        assert get_result.output["item"]["content"] == "Test memory item"

    def test_search_memory(self):
        """Test searching memory items."""
        tool = MemoryTool()
        tool.execute(operation="add", content="The quick brown fox")
        tool.execute(operation="add", content="The lazy dog")

        result = tool.execute(operation="search", query="fox")
        assert result.ok
        assert result.output["count"] >= 1

    def test_list_memory(self):
        """Test listing memory items."""
        tool = MemoryTool()
        tool.execute(operation="add", content="Item 1")
        tool.execute(operation="add", content="Item 2")

        result = tool.execute(operation="list", limit=10)
        assert result.ok
        assert result.output["count"] >= 2

    def test_remove_item(self):
        """Test removing a memory item."""
        tool = MemoryTool()
        add_result = tool.execute(operation="add", content="To be removed")
        item_id = add_result.output["item"]["id"]

        remove_result = tool.execute(operation="remove", item_id=item_id)
        assert remove_result.ok
        assert remove_result.output["removed"] is True

        get_result = tool.execute(operation="get", item_id=item_id)
        assert get_result.output["found"] is False

    def test_clear_memory(self):
        """Test clearing all memory."""
        tool = MemoryTool()
        tool.execute(operation="add", content="Item A")
        tool.execute(operation="add", content="Item B")

        clear_result = tool.execute(operation="clear")
        assert clear_result.ok

        list_result = tool.execute(operation="list")
        assert list_result.output["count"] == 0

    def test_invalid_operation(self):
        """Test invalid operation name."""
        tool = MemoryTool()
        result = tool.execute(operation="invalid_op")
        assert not result.ok


class TestDateTimeTool:
    """Test the datetime tool."""

    def test_now_operation(self):
        """Test getting current datetime."""
        tool = DateTimeTool()
        result = tool.execute(operation="now")
        assert result.ok
        assert "datetime" in result.output
        assert "date" in result.output
        assert "time" in result.output
        assert "weekday" in result.output

    def test_format_operation(self):
        """Test formatting a date."""
        tool = DateTimeTool()
        result = tool.execute(
            operation="format",
            date_str="2025-01-15",
            format_str="%d/%m/%Y",
        )
        assert result.ok
        assert result.output["formatted"] == "15/01/2025"

    def test_add_days(self):
        """Test adding days to a date."""
        tool = DateTimeTool()
        result = tool.execute(
            operation="add_days",
            date_str="2025-01-01",
            days=10,
        )
        assert result.ok
        assert result.output["result"] == "2025-01-11"

    def test_add_negative_days(self):
        """Test subtracting days from a date."""
        tool = DateTimeTool()
        result = tool.execute(
            operation="add_days",
            date_str="2025-01-10",
            days=-5,
        )
        assert result.ok
        assert result.output["result"] == "2025-01-05"

    def test_diff_operation(self):
        """Test date difference calculation."""
        tool = DateTimeTool()
        result = tool.execute(
            operation="diff",
            date_str="2025-01-01",
            date_str2="2025-01-10",
        )
        assert result.ok
        assert result.output["days_diff"] == 9

    def test_parse_operation(self):
        """Test parsing a date string."""
        tool = DateTimeTool()
        result = tool.execute(operation="parse", date_str="2025-06-15")
        assert result.ok
        assert result.output["year"] == 2025
        assert result.output["month"] == 6
        assert result.output["day"] == 15


class TestFileTool:
    """Test the file tool (with temp directory)."""

    def test_write_and_read(self, tmp_path):
        """Test writing and reading a file."""
        tool = FileTool(base_dir=str(tmp_path))

        write_result = tool.execute(
            operation="write",
            path="test.txt",
            content="Hello, world!",
        )
        assert write_result.ok
        assert write_result.output["success"] is True

        read_result = tool.execute(operation="read", path="test.txt")
        assert read_result.ok
        assert read_result.output["content"] == "Hello, world!"

    def test_list_directory(self, tmp_path):
        """Test listing directory contents."""
        tool = FileTool(base_dir=str(tmp_path))
        tool.execute(operation="write", path="a.txt", content="A")
        tool.execute(operation="write", path="b.txt", content="B")

        result = tool.execute(operation="list", path=".")
        assert result.ok
        assert result.output["count"] == 2
        names = [e["name"] for e in result.output["entries"]]
        assert "a.txt" in names
        assert "b.txt" in names

    def test_file_exists(self, tmp_path):
        """Test checking if a file exists."""
        tool = FileTool(base_dir=str(tmp_path))
        tool.execute(operation="write", path="exists.txt", content="yes")

        result = tool.execute(operation="exists", path="exists.txt")
        assert result.ok
        assert result.output["exists"] is True
        assert result.output["is_file"] is True

        result2 = tool.execute(operation="exists", path="missing.txt")
        assert result2.ok
        assert result2.output["exists"] is False

    def test_append_file(self, tmp_path):
        """Test appending to a file."""
        tool = FileTool(base_dir=str(tmp_path))
        tool.execute(operation="write", path="log.txt", content="Line 1\n")

        append_result = tool.execute(
            operation="append",
            path="log.txt",
            content="Line 2\n",
        )
        assert append_result.ok

        read_result = tool.execute(operation="read", path="log.txt")
        assert "Line 1" in read_result.output["content"]
        assert "Line 2" in read_result.output["content"]

    def test_delete_file(self, tmp_path):
        """Test deleting a file."""
        tool = FileTool(base_dir=str(tmp_path))
        tool.execute(operation="write", path="todelete.txt", content="bye")

        result = tool.execute(operation="delete", path="todelete.txt")
        assert result.ok
        assert result.output["deleted"] is True

        check = tool.execute(operation="exists", path="todelete.txt")
        assert check.output["exists"] is False

    def test_path_traversal_blocked(self, tmp_path):
        """Test that path traversal is blocked."""
        tool = FileTool(base_dir=str(tmp_path))

        result = tool.execute(operation="read", path="../etc/passwd")
        assert not result.ok
        assert "escapes" in result.error.lower() or "allowed" in result.error.lower()

    def test_read_nonexistent_file(self, tmp_path):
        """Test reading a file that doesn't exist."""
        tool = FileTool(base_dir=str(tmp_path))
        result = tool.execute(operation="read", path="nonexistent.txt")
        assert not result.ok


class TestCodeRunnerTool:
    """Test the code runner tool."""

    def test_simple_code_execution(self):
        """Test running simple Python code."""
        tool = CodeRunnerTool()
        result = tool.execute(code="result = 2 + 3")
        assert result.ok
        assert result.output["result"] == 5
        assert result.output["success"] is True

    def test_print_output_capture(self):
        """Test that print output is captured."""
        tool = CodeRunnerTool()
        result = tool.execute(code='print("Hello"); result = 1')
        assert result.ok
        assert "Hello" in result.output["output"]

    def test_dangerous_code_blocked(self):
        """Test that dangerous code patterns are blocked."""
        tool = CodeRunnerTool()
        result = tool.execute(code="import os")
        assert not result.ok
        assert "disallowed" in result.error.lower()

    def test_invalid_code(self):
        """Test running syntactically invalid code."""
        tool = CodeRunnerTool()
        result = tool.execute(code="def foo(:")
        assert not result.ok


# ======================================================================
# Tool Executor Tests
# ======================================================================

class TestToolExecutor:
    """Test the tool executor."""

    def test_execute_tool(self):
        """Test executing a tool through the executor."""
        registry = ToolRegistry()
        registry.register(CalculatorTool())
        executor = ToolExecutor(registry)

        result = executor.execute("calculator", expression="10 + 20")
        assert result.ok
        assert result.output["result"] == 30

    def test_execute_nonexistent_tool(self):
        """Test executing a tool that doesn't exist."""
        executor = ToolExecutor()
        result = executor.execute("nonexistent", x=1)
        assert not result.ok
        assert "not found" in result.error.lower()

    def test_select_tools(self):
        """Test tool selection based on task description."""
        registry = ToolRegistry()
        registry.register(CalculatorTool())
        registry.register(SearchTool())
        registry.register(DateTimeTool())
        executor = ToolExecutor(registry)

        candidates = executor.select_tools("calculate the area of a circle")
        assert len(candidates) > 0
        assert candidates[0].tool_name == "calculator"
        assert candidates[0].score > 0

    def test_select_best_tool(self):
        """Test selecting the single best tool."""
        registry = ToolRegistry()
        registry.register(CalculatorTool())
        registry.register(SearchTool())
        executor = ToolExecutor(registry)

        best = executor.select_best("what is 2 plus 2")
        assert best == "calculator"

    def test_batch_execute(self):
        """Test batch tool execution."""
        registry = ToolRegistry()
        registry.register(CalculatorTool())
        registry.register(DateTimeTool())
        executor = ToolExecutor(registry)

        batch = executor.batch_execute([
            ("calculator", {"expression": "1 + 1"}),
            ("datetime", {"operation": "now"}),
        ])

        assert isinstance(batch, BatchResult)
        assert batch.total_calls == 2
        assert batch.successful == 2
        assert batch.all_succeeded is True

    def test_batch_stop_on_error(self):
        """Test batch execution stopping on error."""
        registry = ToolRegistry()
        registry.register(CalculatorTool())
        executor = ToolExecutor(registry)

        batch = executor.batch_execute(
            [
                ("calculator", {"expression": "invalid !! "}),
                ("calculator", {"expression": "1 + 1"}),  # Should not run
            ],
            stop_on_error=True,
        )

        assert batch.total_calls == 1
        assert batch.failed == 1

    def test_statistics_tracking(self):
        """Test that execution statistics are tracked."""
        registry = ToolRegistry()
        registry.register(CalculatorTool())
        executor = ToolExecutor(registry)

        executor.execute("calculator", expression="1 + 1")
        executor.execute("calculator", expression="2 + 2")
        executor.execute("nonexistent")

        stats = executor.stats
        assert stats.total_calls == 3
        assert stats.successful_calls == 2
        assert stats.failed_calls == 1
        assert "calculator" in stats.tool_stats
        assert stats.tool_stats["calculator"]["calls"] == 2

    def test_reset_stats(self):
        """Test resetting statistics."""
        registry = ToolRegistry()
        registry.register(CalculatorTool())
        executor = ToolExecutor(registry)

        executor.execute("calculator", expression="1 + 1")
        assert executor.stats.total_calls == 1

        executor.reset_stats()
        assert executor.stats.total_calls == 0

    def test_format_result_text(self):
        """Test result formatting in text style."""
        registry = ToolRegistry()
        registry.register(CalculatorTool())
        executor = ToolExecutor(registry)

        result = executor.execute("calculator", expression="2 + 2")
        formatted = executor.format_result(result, style="text")
        assert "Status: success" in formatted
        assert "Output:" in formatted

    def test_format_result_brief(self):
        """Test brief result formatting."""
        registry = ToolRegistry()
        registry.register(CalculatorTool())
        executor = ToolExecutor(registry)

        result = executor.execute("calculator", expression="1 + 1")
        brief = executor.format_result(result, style="brief")
        assert "[OK]" in brief

    def test_available_tools_listing(self):
        """Test listing available tools."""
        registry = ToolRegistry()
        registry.register(CalculatorTool())
        registry.register(SearchTool())
        executor = ToolExecutor(registry)

        tools = executor.available_tools()
        assert "calculator" in tools
        assert "search" in tools

    def test_tool_info(self):
        """Test getting tool info."""
        registry = ToolRegistry()
        registry.register(CalculatorTool())
        executor = ToolExecutor(registry)

        info = executor.tool_info("calculator")
        assert info is not None
        assert info["name"] == "calculator"
        assert info["category"] == "computation"

        assert executor.tool_info("nonexistent") is None

    def test_register_tool(self):
        """Test registering a tool through the executor."""
        executor = ToolExecutor()
        name = executor.register_tool(CalculatorTool())
        assert name == "calculator"
        assert "calculator" in executor.available_tools()
