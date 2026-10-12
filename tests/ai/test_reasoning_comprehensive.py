"""Comprehensive reasoning tests for LiuHao AI OS.

Covers:
- Chain of Thought: multi-step reasoning, confidence scoring,
  contradiction detection, different strategies, trace formatting
- Reflection: reflection cycles, self-critique dimensions,
  improvement tracking, convergence detection, iteration limits
- ReAct: thought-action-observation loops, stop conditions,
  tool selection, progress detection, error handling
- Integration: CoT -> Reflection -> ReAct pipeline
"""
from __future__ import annotations

import pytest
import time

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
    ToolResult,
    StopReason,
)


# ======================================================================
# Chain of Thought Tests (18 tests)
# ======================================================================

class TestChainOfThoughtCore:
    """Core Chain-of-Thought reasoning tests."""

    def test_create_premise_default_confidence(self):
        """Test premise creation with default confidence (1.0)."""
        cot = ChainOfThought()
        step = cot.add_premise("All humans are mortal")
        assert step.step_type == StepType.PREMISE
        assert step.content == "All humans are mortal"
        assert step.confidence == 1.0
        assert step.premises == []
        assert cot.step_count == 1

    def test_create_premise_custom_confidence(self):
        """Test premise creation with custom confidence."""
        cot = ChainOfThought()
        step = cot.add_premise("It might rain", confidence=0.6, source="weather_app")
        assert step.confidence == 0.6
        assert step.metadata["source"] == "weather_app"

    def test_add_inference_with_premises(self):
        """Test adding an inference based on multiple premises."""
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

    def test_add_sub_conclusion(self):
        """Test adding a sub-conclusion."""
        cot = ChainOfThought()
        p1 = cot.add_premise("A = B")
        p2 = cot.add_premise("B = C")
        sub = cot.add_sub_conclusion(
            "A = C",
            premises=[p1.step_id, p2.step_id],
            justification="transitive property",
            confidence=0.9,
        )
        assert sub.step_type == StepType.SUB_CONCLUSION
        assert sub.content == "A = C"
        assert sub.confidence == 0.9

    def test_add_correction(self):
        """Test adding a correction to a previous step."""
        cot = ChainOfThought()
        step = cot.add_premise("The earth is flat")
        correction = cot.add_correction(
            target_step_id=step.step_id,
            corrected_content="The earth is spherical",
            reason="scientific consensus",
            confidence=0.99,
        )
        assert correction.step_type == StepType.CORRECTION
        assert correction.content == "The earth is spherical"
        # Correction stores target in metadata with 'corrects' key
        assert correction.metadata.get("corrects") == step.step_id
        assert "original" in correction.metadata

    def test_finalize_produces_result(self):
        """Test that finalize produces a ChainOfThoughtResult."""
        cot = ChainOfThought(strategy=ReasoningStrategy.ANALYTICAL)
        p1 = cot.add_premise("2 + 2 = 4")
        result = cot.finalize("The answer is 4")
        assert isinstance(result, ChainOfThoughtResult)
        assert result.final_conclusion is not None
        assert result.final_conclusion.step_type == StepType.FINAL_CONCLUSION
        assert result.strategy == ReasoningStrategy.ANALYTICAL
        assert result.duration_ms >= 0
        assert len(result.steps) == 2  # premise + final conclusion

    def test_finalize_overall_confidence(self):
        """Test overall confidence aggregation."""
        cot = ChainOfThought()
        cot.add_premise("Fact A", confidence=0.9)
        cot.add_premise("Fact B", confidence=0.8)
        result = cot.finalize("Conclusion")
        assert 0.0 <= result.overall_confidence <= 1.0

    def test_step_count_accumulates(self):
        """Test step count accumulates correctly."""
        cot = ChainOfThought()
        assert cot.step_count == 0
        cot.add_premise("P1")
        assert cot.step_count == 1
        cot.add_premise("P2")
        assert cot.step_count == 2

    def test_step_has_unique_id(self):
        """Test each step gets a unique ID."""
        cot = ChainOfThought()
        ids = set()
        for i in range(10):
            step = cot.add_premise(f"Premise {i}")
            ids.add(step.step_id)
        assert len(ids) == 10

    def test_step_to_dict_serialization(self):
        """Test ReasoningStep to_dict serialization."""
        cot = ChainOfThought(strategy=ReasoningStrategy.CRITICAL)
        step = cot.add_premise("Test premise")
        d = step.to_dict()
        assert d["step_id"] == step.step_id
        assert d["step_type"] == "premise"
        assert d["content"] == "Test premise"
        assert d["confidence"] == 1.0
        assert d["strategy"] == "critical"
        assert "created_at" in d
        assert "metadata" in d

    def test_result_to_dict_serialization(self):
        """Test ChainOfThoughtResult to_dict serialization."""
        cot = ChainOfThought()
        cot.add_premise("P1")
        result = cot.finalize("Done")
        d = result.to_dict()
        assert "steps" in d
        assert "final_conclusion" in d
        assert "overall_confidence" in d
        assert "contradictions" in d
        assert "strategy" in d
        assert "duration_ms" in d
        assert isinstance(d["steps"], list)

    def test_contradiction_detection(self):
        """Test contradiction between steps."""
        cot = ChainOfThought()
        s1 = cot.add_premise("The sky is blue")
        s2 = cot.add_premise("The sky is green")
        # Manually register a contradiction
        contradiction = Contradiction(
            step_a_id=s1.step_id,
            step_b_id=s2.step_id,
            description="Sky color disagreement",
            severity=0.9,
        )
        assert contradiction.severity == 0.9
        assert contradiction.step_a_id == s1.step_id
        assert contradiction.step_b_id == s2.step_id
        assert "disagreement" in contradiction.description

    def test_contradiction_to_dict(self):
        """Test Contradiction to_dict serialization."""
        c = Contradiction(
            step_a_id="a1",
            step_b_id="b2",
            description="test",
            severity=0.5,
        )
        d = c.to_dict()
        assert d["step_a_id"] == "a1"
        assert d["step_b_id"] == "b2"
        assert d["description"] == "test"
        assert d["severity"] == 0.5

    def test_invalid_premise_raises(self):
        """Test that referencing non-existent premise IDs raises an error."""
        cot = ChainOfThought()
        with pytest.raises((ValueError, KeyError)):
            cot.add_inference(
                "Invalid inference",
                premises=["nonexistent_id"],
            )

    def test_analytical_strategy(self):
        """Test analytical reasoning strategy."""
        cot = ChainOfThought(strategy=ReasoningStrategy.ANALYTICAL)
        assert cot.strategy == ReasoningStrategy.ANALYTICAL
        step = cot.add_premise("Test")
        assert step.strategy == ReasoningStrategy.ANALYTICAL

    def test_creative_strategy(self):
        """Test creative reasoning strategy."""
        cot = ChainOfThought(strategy=ReasoningStrategy.CREATIVE)
        assert cot.strategy == ReasoningStrategy.CREATIVE
        step = cot.add_premise("Test")
        assert step.strategy == ReasoningStrategy.CREATIVE

    def test_critical_strategy(self):
        """Test critical reasoning strategy."""
        cot = ChainOfThought(strategy=ReasoningStrategy.CRITICAL)
        assert cot.strategy == ReasoningStrategy.CRITICAL
        step = cot.add_premise("Test")
        assert step.strategy == ReasoningStrategy.CRITICAL

    def test_inference_with_strategy_override(self):
        """Test inference with strategy override."""
        cot = ChainOfThought(strategy=ReasoningStrategy.ANALYTICAL)
        p = cot.add_premise("Premise")
        inf = cot.add_inference(
            "Creative inference",
            premises=[p.step_id],
            strategy=ReasoningStrategy.CREATIVE,
        )
        assert inf.strategy == ReasoningStrategy.CREATIVE


# ======================================================================
# Reflection Tests (18 tests)
# ======================================================================

class TestReflectionEngine:
    """Reflection engine tests."""

    def _make_critique(self, score: float, dimensions=None) -> SelfCritique:
        """Helper to create a SelfCritique."""
        dims = dimensions or [
            CritiqueScore(
                dimension=CritiqueDimension.ACCURACY,
                score=score,
                feedback="accuracy feedback",
            ),
            CritiqueScore(
                dimension=CritiqueDimension.CLARITY,
                score=min(score + 0.1, 1.0),
                feedback="clarity feedback",
            ),
        ]
        return SelfCritique(
            overall_score=score,
            scores=dims,
            summary="test critique",
            strengths=["good start"],
            weaknesses=["needs work"],
            suggestions=["improve X"],
        )

    def test_self_critique_passed_threshold(self):
        """Test SelfCritique.passed property (>= 0.7 passes)."""
        good = self._make_critique(0.8)
        bad = self._make_critique(0.5)
        assert good.passed is True
        assert bad.passed is False

    def test_self_critique_boundary(self):
        """Test SelfCritique.passed at boundary (0.7 exactly)."""
        boundary = self._make_critique(0.7)
        assert boundary.passed is True

    def test_critique_score_to_dict(self):
        """Test CritiqueScore to_dict serialization."""
        cs = CritiqueScore(
            dimension=CritiqueDimension.COMPLETENESS,
            score=0.75,
            feedback="missing details",
        )
        d = cs.to_dict()
        assert d["dimension"] == "completeness"
        assert d["score"] == 0.75
        assert d["feedback"] == "missing details"

    def test_self_critique_to_dict(self):
        """Test SelfCritique to_dict serialization."""
        critique = self._make_critique(0.6)
        d = critique.to_dict()
        assert d["overall_score"] == 0.6
        assert isinstance(d["scores"], list)
        assert len(d["scores"]) == 2
        assert d["summary"] == "test critique"
        assert isinstance(d["strengths"], list)
        assert isinstance(d["weaknesses"], list)
        assert isinstance(d["suggestions"], list)

    def test_reflection_entry_creation(self):
        """Test ReflectionEntry creation and fields."""
        entry = ReflectionEntry(
            entry_id="ref_001",
            iteration=1,
            phase=ReflectionPhase.ACTION,
            content="initial output",
        )
        assert entry.entry_id == "ref_001"
        assert entry.iteration == 1
        assert entry.phase == ReflectionPhase.ACTION
        assert entry.content == "initial output"
        assert entry.critique is None
        assert isinstance(entry.metadata, dict)

    def test_reflection_entry_with_critique(self):
        """Test ReflectionEntry with critique attached."""
        critique = self._make_critique(0.7)
        entry = ReflectionEntry(
            entry_id="ref_002",
            iteration=2,
            phase=ReflectionPhase.OBSERVATION,
            content="evaluated",
            critique=critique,
        )
        assert entry.critique is not None
        assert entry.critique.overall_score == 0.7

    def test_reflection_entry_to_dict(self):
        """Test ReflectionEntry to_dict serialization."""
        entry = ReflectionEntry(
            entry_id="ref_003",
            iteration=1,
            phase=ReflectionPhase.REFLECTION,
            content="reflecting",
            metadata={"key": "value"},
        )
        d = entry.to_dict()
        assert d["entry_id"] == "ref_003"
        assert d["iteration"] == 1
        assert d["phase"] == "reflection"
        assert d["content"] == "reflecting"
        assert d["metadata"]["key"] == "value"

    def test_iteration_delta(self):
        """Test IterationDelta tracking."""
        delta = IterationDelta(
            from_iteration=1,
            to_iteration=2,
            overall_change=0.15,
            dimension_changes={"accuracy": 0.2, "clarity": 0.1},
            description="improved accuracy",
        )
        assert delta.from_iteration == 1
        assert delta.to_iteration == 2
        assert delta.overall_change == 0.15
        assert delta.overall_change > 0  # improvement

    def test_iteration_delta_to_dict(self):
        """Test IterationDelta to_dict serialization."""
        delta = IterationDelta(
            from_iteration=1,
            to_iteration=2,
            overall_change=0.1,
            dimension_changes={"accuracy": 0.1},
            description="improved",
        )
        d = delta.to_dict()
        assert d["from_iteration"] == 1
        assert d["to_iteration"] == 2
        assert d["overall_change"] == 0.1
        assert "dimension_changes" in d

    def test_reflect_single_iteration(self):
        """Test reflection runs for at least one iteration."""
        engine = ReflectionEngine(max_iterations=3, target_score=0.9)

        def generate(context, critique=None):
            return f"output v{critique.overall_score if critique else 'init'}"

        def evaluate(output, context):
            return self._make_critique(0.5)  # always 0.5

        result = engine.reflect(
            generate_fn=generate,
            evaluate_fn=evaluate,
            context="test problem",
        )
        assert isinstance(result, ReflectionResult)
        assert result.iterations >= 1
        assert result.final_output is not None

    def test_reflect_converges_on_target(self):
        """Test reflection stops when target score is reached."""
        engine = ReflectionEngine(max_iterations=10, target_score=0.8)
        call_count = {"n": 0}

        def generate(context, critique=None):
            call_count["n"] += 1
            return f"output-{call_count['n']}"

        def evaluate(output, context):
            # Score gets better each time
            n = call_count["n"]
            return self._make_critique(min(0.5 + n * 0.2, 1.0))

        result = engine.reflect(
            generate_fn=generate,
            evaluate_fn=evaluate,
            context="test",
        )
        assert result.converged is True
        assert result.final_score >= 0.8
        assert result.iterations < 10  # stopped early

    def test_reflect_respects_max_iterations(self):
        """Test reflection respects max_iterations limit."""
        # Score increases slightly each iteration but never reaches target
        engine = ReflectionEngine(
            max_iterations=3,
            target_score=0.99,
            convergence_threshold=0.001,
        )
        call_count = {"n": 0}

        def generate(context, critique=None):
            call_count["n"] += 1
            return f"output-{call_count['n']}"

        def evaluate(output, context):
            # Score increases by 0.01 per call - never reaches 0.99 target
            # but does change enough to avoid convergence
            base = 0.3 + (call_count["n"] - 1) * 0.01
            return self._make_critique(base)

        result = engine.reflect(
            generate_fn=generate,
            evaluate_fn=evaluate,
            context="test",
        )
        assert result.iterations == 3
        assert result.converged is False

    def test_reflect_result_to_dict(self):
        """Test ReflectionResult to_dict serialization."""
        engine = ReflectionEngine(max_iterations=2, target_score=0.9)

        def generate(context, critique=None):
            return "out"

        def evaluate(output, context):
            return self._make_critique(0.6)

        result = engine.reflect(
            generate_fn=generate,
            evaluate_fn=evaluate,
            context="test",
        )
        d = result.to_dict()
        assert "iterations" in d
        assert "final_output" in d
        assert "final_score" in d
        assert "improvements" in d
        assert "journal" in d
        assert "converged" in d
        assert "total_time_ms" in d

    def test_reflect_initial_output(self):
        """Test reflection with initial output provided."""
        engine = ReflectionEngine(max_iterations=3, target_score=0.9)
        call_count = {"n": 0}

        def generate(context, critique=None):
            call_count["n"] += 1
            return f"gen-{call_count['n']}"

        def evaluate(output, context):
            return self._make_critique(0.7)

        result = engine.reflect(
            generate_fn=generate,
            evaluate_fn=evaluate,
            context="test",
            initial_output="pre-made output",
        )
        assert result.iterations >= 1

    def test_reflection_journal_entries(self):
        """Test that reflection journal has entries for each phase."""
        engine = ReflectionEngine(max_iterations=1, target_score=0.9)

        def generate(context, critique=None):
            return "output"

        def evaluate(output, context):
            return self._make_critique(0.6)

        result = engine.reflect(
            generate_fn=generate,
            evaluate_fn=evaluate,
            context="test",
        )
        # Each iteration has at least ACTION, OBSERVATION, REFLECTION phases
        assert len(result.journal) >= 3
        phases = [e.phase for e in result.journal]
        assert ReflectionPhase.ACTION in phases
        assert ReflectionPhase.OBSERVATION in phases

    def test_critique_dimensions_all_present(self):
        """Test all critique dimensions are available."""
        dimensions = list(CritiqueDimension)
        assert len(dimensions) >= 5
        assert CritiqueDimension.ACCURACY in dimensions
        assert CritiqueDimension.COMPLETENESS in dimensions
        assert CritiqueDimension.CLARITY in dimensions
        assert CritiqueDimension.RELEVANCE in dimensions
        assert CritiqueDimension.COHERENCE in dimensions

    def test_reflection_phases_all_present(self):
        """Test all reflection phases are available."""
        phases = list(ReflectionPhase)
        assert len(phases) == 4
        assert ReflectionPhase.ACTION in phases
        assert ReflectionPhase.OBSERVATION in phases
        assert ReflectionPhase.REFLECTION in phases
        assert ReflectionPhase.IMPROVEMENT in phases

    def test_improvement_tracking(self):
        """Test that improvements are tracked between iterations."""
        engine = ReflectionEngine(max_iterations=4, target_score=0.95)
        scores = [0.4, 0.6, 0.75, 0.85]
        idx = {"n": 0}

        def generate(context, critique=None):
            return f"v{idx['n']}"

        def evaluate(output, context):
            score = scores[idx["n"]]
            idx["n"] += 1
            return self._make_critique(score)

        result = engine.reflect(
            generate_fn=generate,
            evaluate_fn=evaluate,
            context="test",
        )
        # Should have improvement deltas
        assert isinstance(result.improvements, list)
        # At least some improvements should be positive
        if len(result.improvements) > 0:
            assert result.improvements[0].overall_change > 0


# ======================================================================
# ReAct Tests (14 tests)
# ======================================================================

class TestReActAgent:
    """ReAct agent tests."""

    def test_react_step_to_dict(self):
        """Test ReActStep to_dict serialization."""
        step = ReActStep(
            step_id="s1",
            step_type=ReActStepType.THOUGHT,
            content="I need to calculate",
            action_name="calculator",
            action_input={"expr": "2+2"},
            observation="4",
            metadata={"key": "val"},
        )
        d = step.to_dict()
        assert d["step_id"] == "s1"
        assert d["step_type"] == "thought"
        assert d["content"] == "I need to calculate"
        assert d["action_name"] == "calculator"
        assert d["action_input"] == {"expr": "2+2"}
        assert d["observation"] == "4"
        assert d["metadata"] == {"key": "val"}

    def test_react_result_trace(self):
        """Test ReActResult trace property produces readable output."""
        steps = [
            ReActStep(
                step_id="s1",
                step_type=ReActStepType.THOUGHT,
                content="Let me calculate",
            ),
            ReActStep(
                step_id="s2",
                step_type=ReActStepType.ACTION,
                content="Executing calc",
                action_name="calculator",
                action_input={"expr": "2+2"},
            ),
            ReActStep(
                step_id="s3",
                step_type=ReActStepType.OBSERVATION,
                content="Result observed",
                observation="4",
            ),
        ]
        result = ReActResult(
            steps=steps,
            final_answer="4",
            stop_reason=StopReason.GOAL_REACHED,
            total_steps=3,
            actions_taken=1,
            duration_ms=100.0,
            success=True,
        )
        trace = result.trace
        assert "THOUGHT" in trace
        assert "ACTION" in trace
        assert "OBSERVATION" in trace
        assert "calculator" in trace

    def test_react_result_to_dict(self):
        """Test ReActResult to_dict serialization."""
        result = ReActResult(
            steps=[],
            final_answer="done",
            stop_reason=StopReason.GOAL_REACHED,
            total_steps=1,
            actions_taken=0,
            duration_ms=50.0,
            success=True,
        )
        d = result.to_dict()
        assert d["final_answer"] == "done"
        assert d["stop_reason"] == "goal_reached"
        assert d["total_steps"] == 1
        assert d["actions_taken"] == 0
        assert d["success"] is True

    def test_react_goal_reached(self):
        """Test ReAct agent stops when goal is reached."""
        agent = ReActAgent(max_steps=10)

        def think_fn(question, history, available_tools, context):
            return ReActDecision(
                thought="I know the answer",
                is_final=True,
                final_answer="The answer is 42",
            )

        def act_fn(tool_name, tool_input):
            return ToolResult(output="", success=True)

        result = agent.run(
            question="What is the answer?",
            think_fn=think_fn,
            act_fn=act_fn,
            available_tools=["calc"],
        )
        assert result.success is True
        assert result.stop_reason == StopReason.GOAL_REACHED
        assert result.final_answer == "The answer is 42"
        assert result.actions_taken == 0

    def test_react_action_execution(self):
        """Test ReAct agent executes actions."""
        agent = ReActAgent(max_steps=5)
        step = {"n": 0}

        def think_fn(question, history, available_tools, context):
            step["n"] += 1
            if step["n"] == 1:
                return ReActDecision(
                    thought="Let me calculate",
                    action_name="calculator",
                    action_input={"expr": "2+2"},
                )
            else:
                return ReActDecision(
                    thought="Got the result",
                    is_final=True,
                    final_answer="4",
                )

        def act_fn(tool_name, tool_input):
            assert tool_name == "calculator"
            return ToolResult(output="4", success=True)

        result = agent.run(
            question="2+2?",
            think_fn=think_fn,
            act_fn=act_fn,
            available_tools=["calculator"],
        )
        assert result.success is True
        assert result.actions_taken == 1
        assert result.final_answer == "4"

    def test_react_max_steps_stop(self):
        """Test ReAct agent stops at max_steps."""
        agent = ReActAgent(max_steps=5)
        step_counter = {"n": 0}

        def think_fn(question, history, available_tools, context):
            step_counter["n"] += 1
            # Different action each time to avoid no-progress stop
            return ReActDecision(
                thought=f"step {step_counter['n']}",
                action_name=f"tool_{step_counter['n']}",
                action_input={"step": step_counter["n"]},
            )

        def act_fn(tool_name, tool_input):
            return ToolResult(output=f"result for {tool_name}", success=True)

        result = agent.run(
            question="Never ending",
            think_fn=think_fn,
            act_fn=act_fn,
            available_tools=[f"tool_{i}" for i in range(1, 10)],
        )
        assert result.success is False
        assert result.stop_reason == StopReason.MAX_STEPS
        # total_steps counts thought+action+observation per cycle
        assert result.actions_taken == 5
        assert result.total_steps == 15  # 5 cycles * 3 step types

    def test_react_no_progress_stop(self):
        """Test ReAct agent stops when no progress is made."""
        agent = ReActAgent(max_steps=10, max_no_progress_steps=2)

        def think_fn(question, history, available_tools, context):
            # Same action every time
            return ReActDecision(
                thought="looping",
                action_name="same_tool",
                action_input={"same": True},
            )

        def act_fn(tool_name, tool_input):
            return ToolResult(output="same", success=True)

        result = agent.run(
            question="Loop test",
            think_fn=think_fn,
            act_fn=act_fn,
            available_tools=["same_tool"],
        )
        assert result.stop_reason == StopReason.NO_PROGRESS
        assert result.success is False

    def test_react_error_in_think(self):
        """Test ReAct agent handles errors in think function."""
        agent = ReActAgent(max_steps=5)

        def think_fn(question, history, available_tools, context):
            raise RuntimeError("thinking failed")

        def act_fn(tool_name, tool_input):
            return ToolResult(output="", success=True)

        result = agent.run(
            question="Error test",
            think_fn=think_fn,
            act_fn=act_fn,
            available_tools=[],
        )
        assert result.stop_reason == StopReason.ERROR
        assert result.success is False

    def test_react_error_in_action(self):
        """Test ReAct agent handles errors in action function."""
        agent = ReActAgent(max_steps=5)
        call_count = {"n": 0}

        def think_fn(question, history, available_tools, context):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return ReActDecision(
                    thought="Let me try",
                    action_name="failing_tool",
                    action_input={},
                )
            return ReActDecision(
                thought="Recovered with answer",
                is_final=True,
                final_answer="fallback answer",
            )

        def act_fn(tool_name, tool_input):
            raise ValueError("tool failed")

        result = agent.run(
            question="Failing action",
            think_fn=think_fn,
            act_fn=act_fn,
            available_tools=["failing_tool"],
        )
        # Should handle the error and continue or stop gracefully
        assert isinstance(result, ReActResult)

    def test_react_decision_defaults(self):
        """Test ReActDecision default values."""
        d = ReActDecision(thought="thinking")
        assert d.thought == "thinking"
        assert d.action_name is None
        assert d.action_input is None
        assert d.is_final is False
        assert d.final_answer is None

    def test_react_decision_final(self):
        """Test ReActDecision with final answer."""
        d = ReActDecision(
            thought="I know it",
            is_final=True,
            final_answer="42",
        )
        assert d.is_final is True
        assert d.final_answer == "42"

    def test_stop_reason_all_present(self):
        """Test all stop reasons are available."""
        reasons = list(StopReason)
        assert len(reasons) >= 4
        assert StopReason.GOAL_REACHED in reasons
        assert StopReason.MAX_STEPS in reasons
        assert StopReason.ERROR in reasons
        assert StopReason.NO_PROGRESS in reasons

    def test_react_step_types_all_present(self):
        """Test all ReAct step types are available."""
        types = list(ReActStepType)
        assert len(types) == 4
        assert ReActStepType.THOUGHT in types
        assert ReActStepType.ACTION in types
        assert ReActStepType.OBSERVATION in types
        assert ReActStepType.FINAL_ANSWER in types

    def test_tool_result_success(self):
        """Test ToolResult creation for success."""
        r = ToolResult(output="hello", success=True)
        assert r.output == "hello"
        assert r.success is True

    def test_tool_result_failure(self):
        """Test ToolResult creation for failure."""
        r = ToolResult(output="error msg", success=False, error="something wrong")
        assert r.success is False
        assert r.error == "something wrong"


# ======================================================================
# Integration Tests (5 tests)
# ======================================================================

class TestReasoningIntegration:
    """Integration tests combining CoT, Reflection, and ReAct."""

    def test_cot_feeds_reflection(self):
        """Test CoT result can be fed into reflection engine."""
        # Build a CoT chain
        cot = ChainOfThought(strategy=ReasoningStrategy.ANALYTICAL)
        p1 = cot.add_premise("X is 10")
        p2 = cot.add_premise("Y is 20")
        inf = cot.add_inference(
            "X + Y = 30",
            premises=[p1.step_id, p2.step_id],
            confidence=0.9,
        )
        cot_result = cot.finalize("The sum is 30")

        # Reflect on the result
        engine = ReflectionEngine(max_iterations=3, target_score=0.9)

        def generate(context, critique=None):
            base = context["conclusion"]
            if critique:
                return f"{base} (improved)"
            return base

        def evaluate(output, context):
            score = 0.7 if "improved" not in output else 0.9
            return SelfCritique(
                overall_score=score,
                scores=[
                    CritiqueScore(CritiqueDimension.ACCURACY, score, "ok"),
                    CritiqueScore(CritiqueDimension.CLARITY, score, "ok"),
                ],
                summary="review",
                strengths=["logical"],
                weaknesses=["could be better"],
                suggestions=["improve"],
            )

        ref_result = engine.reflect(
            generate_fn=generate,
            evaluate_fn=evaluate,
            context={"conclusion": cot_result.final_conclusion.content},
        )
        assert isinstance(ref_result, ReflectionResult)
        assert ref_result.iterations >= 1
        assert ref_result.final_score >= 0.7

    def test_cot_trace_contains_all_steps(self):
        """Test CoT result trace includes all reasoning steps."""
        cot = ChainOfThought()
        cot.add_premise("P1")
        cot.add_premise("P2")
        result = cot.finalize("C")
        assert len(result.steps) == 3
        step_types = [s.step_type for s in result.steps]
        assert StepType.PREMISE in step_types
        assert StepType.FINAL_CONCLUSION in step_types

    def test_react_uses_cot_style_thinking(self):
        """Test ReAct agent can incorporate CoT-style reasoning in thoughts."""
        agent = ReActAgent(max_steps=5)
        step_count = {"n": 0}

        def think_fn(question, history, available_tools, context):
            step_count["n"] += 1
            if step_count["n"] == 1:
                # First thought: break down the problem (CoT style)
                return ReActDecision(
                    thought="Step 1: Identify the operation needed",
                    action_name="calculator",
                    action_input={"expr": "15*4"},
                )
            else:
                return ReActDecision(
                    thought="Step 2: The calculation confirms the answer",
                    is_final=True,
                    final_answer="60",
                )

        def act_fn(tool_name, tool_input):
            return ToolResult(output="60", success=True)

        result = agent.run(
            question="What is 15 times 4?",
            think_fn=think_fn,
            act_fn=act_fn,
            available_tools=["calculator"],
        )
        assert result.success is True
        assert result.actions_taken == 1
        assert len(result.steps) >= 3  # thought + action + obs + final

    def test_full_pipeline_cot_reflection_react(self):
        """Test the full CoT -> Reflection -> ReAct pipeline."""
        # Phase 1: CoT reasoning
        cot = ChainOfThought(strategy=ReasoningStrategy.ANALYTICAL)
        cot.add_premise("Problem: Find area of circle with radius 5")
        cot.add_premise("Formula: area = pi * r^2")
        cot_result = cot.finalize("Need to calculate pi * 25")

        # Phase 2: Reflect on the reasoning
        engine = ReflectionEngine(max_iterations=2, target_score=0.8)

        def gen_plan(context, critique=None):
            base = context["plan"]
            if critique and not critique.passed:
                return f"{base} [refined]"
            return base

        def eval_plan(output, context):
            score = 0.65 if "refined" not in output else 0.85
            return SelfCritique(
                overall_score=score,
                scores=[
                    CritiqueScore(CritiqueDimension.ACCURACY, score, "ok"),
                ],
                summary="plan review",
                strengths=["clear formula"],
                weaknesses=["needs computation"],
                suggestions=["use calculator"],
            )

        ref_result = engine.reflect(
            generate_fn=gen_plan,
            evaluate_fn=eval_plan,
            context={"plan": cot_result.final_conclusion.content},
        )

        # Phase 3: ReAct to execute the plan
        agent = ReActAgent(max_steps=5)
        s = {"n": 0}

        def thinker(question, history, available_tools, context):
            s["n"] += 1
            if s["n"] == 1:
                return ReActDecision(
                    thought=ref_result.final_output,
                    action_name="calculator",
                    action_input={"expr": "3.14159 * 25"},
                )
            return ReActDecision(
                thought="Calculation complete",
                is_final=True,
                final_answer="78.54",
            )

        def actor(tool_name, tool_input):
            return ToolResult(output="78.53975", success=True)

        react_result = agent.run(
            question="Area of circle r=5",
            think_fn=thinker,
            act_fn=actor,
            available_tools=["calculator"],
        )

        # Verify end-to-end
        assert isinstance(cot_result, ChainOfThoughtResult)
        assert isinstance(ref_result, ReflectionResult)
        assert isinstance(react_result, ReActResult)
        assert react_result.success is True
        assert react_result.final_answer == "78.54"

    def test_reflection_improves_react_plan(self):
        """Test that reflection improves a ReAct plan quality."""
        initial_score = 0.5
        improved_score = 0.8

        engine = ReflectionEngine(max_iterations=3, target_score=0.75)
        n = {"i": 0}

        def generate(context, critique=None):
            n["i"] += 1
            plans = [
                "vague plan",
                "better plan with steps",
                "detailed plan with tools and timeline",
            ]
            return plans[min(n["i"] - 1, len(plans) - 1)]

        def evaluate(output, context):
            scores = {
                "vague plan": 0.5,
                "better plan with steps": 0.7,
                "detailed plan with tools and timeline": 0.9,
            }
            score = scores.get(output, 0.5)
            return SelfCritique(
                overall_score=score,
                scores=[
                    CritiqueScore(CritiqueDimension.COMPLETENESS, score, "level"),
                    CritiqueScore(CritiqueDimension.CLARITY, min(score + 0.1, 1.0), "ok"),
                ],
                summary=f"Score: {score}",
                strengths=["has direction"],
                weaknesses=["needs detail"],
                suggestions=["add more steps"],
            )

        result = engine.reflect(
            generate_fn=generate,
            evaluate_fn=evaluate,
            context="plan a solution",
        )
        assert result.final_score > initial_score
        assert result.converged is True
