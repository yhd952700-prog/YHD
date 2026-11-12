"""ReAct (Reasoning + Acting) pattern implementation.

Implements the Thought-Action-Observation loop where the agent alternates
between reasoning about what to do next and taking actions through tools,
then integrating observations back into its reasoning.

This is the classic ReAct pattern from the paper "ReAct: Synergizing
Reasoning and Acting in Language Models" (Yao et al., 2022).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional
import time
import uuid


class ReActStepType(str, Enum):
    """Types of steps in a ReAct loop."""

    THOUGHT = "thought"
    """A reasoning step about what to do."""

    ACTION = "action"
    """A tool/action invocation."""

    OBSERVATION = "observation"
    """The result of an action."""

    FINAL_ANSWER = "final_answer"
    """The final answer / goal reached."""


class StopReason(str, Enum):
    """Reasons a ReAct loop stops."""

    GOAL_REACHED = "goal_reached"
    """The agent produced a final answer."""

    MAX_STEPS = "max_steps"
    """Maximum number of steps reached."""

    ERROR = "error"
    """An unrecoverable error occurred."""

    NO_PROGRESS = "no_progress"
    """The agent is looping without making progress."""

    USER_INTERRUPT = "user_interrupt"
    """The user interrupted the process."""


@dataclass
class ReActStep:
    """A single step in the ReAct loop."""

    step_id: str
    step_type: ReActStepType
    content: str
    action_name: Optional[str] = None
    action_input: Optional[Dict[str, Any]] = None
    observation: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_id": self.step_id,
            "step_type": self.step_type.value,
            "content": self.content,
            "action_name": self.action_name,
            "action_input": dict(self.action_input) if self.action_input else None,
            "observation": self.observation,
            "metadata": dict(self.metadata),
            "timestamp": self.timestamp,
        }


@dataclass
class ReActResult:
    """Result of a ReAct reasoning + acting process."""

    steps: List[ReActStep]
    final_answer: Optional[str]
    stop_reason: StopReason
    total_steps: int
    actions_taken: int
    duration_ms: float
    success: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "steps": [s.to_dict() for s in self.steps],
            "final_answer": self.final_answer,
            "stop_reason": self.stop_reason.value,
            "total_steps": self.total_steps,
            "actions_taken": self.actions_taken,
            "duration_ms": self.duration_ms,
            "success": self.success,
        }

    @property
    def trace(self) -> str:
        """Generate a human-readable trace of the ReAct loop."""
        lines = []
        for i, step in enumerate(self.steps, 1):
            prefix = step.step_type.value.upper()
            lines.append(f"[{i}] {prefix}: {step.content}")
            if step.action_name:
                lines.append(f"    Action: {step.action_name}")
            if step.action_input:
                lines.append(f"    Input: {step.action_input}")
            if step.observation:
                lines.append(f"    Observation: {step.observation}")
        return "\n".join(lines)


class ReActAgent:
    """ReAct (Reasoning + Acting) agent.

    Implements the Thought -> Action -> Observation loop.
    The agent reasons about what to do, selects a tool/action,
    executes it, observes the result, and continues until it
    reaches the goal or hits a stop condition.

    The agent uses callback functions for reasoning (thought
    generation, tool selection) and action execution, making it
    compatible with any backend (LLM, rule-based, etc.).

    Usage::

        agent = ReActAgent(max_steps=10)

        def thinker(question, history, available_tools):
            # Decide what to do next
            return ReActDecision(
                thought="I need to calculate this",
                action="calculator",
                action_input={"expression": "2+2"},
            )

        def actor(tool_name, tool_input):
            # Execute the tool
            return ToolResult(output="4", success=True)

        result = agent.run(
            question="What is 2+2?",
            think_fn=thinker,
            act_fn=actor,
            available_tools=["calculator", "search"],
        )
    """

    def __init__(
        self,
        max_steps: int = 10,
        max_no_progress_steps: int = 3,
        action_timeout: float = 30.0,
    ):
        """Initialize the ReAct agent.

        Args:
            max_steps: Maximum total steps before stopping.
            max_no_progress_steps: Stop if no progress for this many steps.
            action_timeout: Timeout in seconds for each action.
        """
        self.max_steps = max_steps
        self.max_no_progress_steps = max_no_progress_steps
        self.action_timeout = action_timeout
        self._steps: List[ReActStep] = []

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(
        self,
        question: str,
        think_fn: Callable[..., "ReActDecision"],
        act_fn: Callable[..., "ToolResult"],
        available_tools: Optional[List[str]] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> ReActResult:
        """Run the ReAct loop.

        Args:
            question: The question/task to solve.
            think_fn: Function that decides the next action.
                Signature: think_fn(question, history, available_tools, context)
                           -> ReActDecision
            act_fn: Function that executes an action.
                Signature: act_fn(tool_name, tool_input) -> ToolResult
            available_tools: List of available tool names.
            context: Additional context passed to think_fn.

        Returns:
            ReActResult with the full trace and final answer.
        """
        start_time = time.time()
        self._steps.clear()
        tools = list(available_tools or [])
        ctx = dict(context or {})

        action_count = 0
        no_progress_count = 0
        last_action_key = ""
        final_answer: Optional[str] = None
        stop_reason: Optional[StopReason] = None
        success = False

        for step_num in range(1, self.max_steps + 1):
            # --- Thought phase ---
            try:
                decision = think_fn(
                    question=question,
                    history=list(self._steps),
                    available_tools=tools,
                    context=ctx,
                )
            except Exception as e:
                stop_reason = StopReason.ERROR
                self._add_step(ReActStepType.THOUGHT, f"Error during thinking: {e}")
                break

            # Record the thought
            self._add_step(
                ReActStepType.THOUGHT,
                content=decision.thought,
                metadata={"step_num": step_num},
            )

            # Check if we have a final answer
            if decision.is_final:
                final_answer = decision.final_answer
                self._add_step(
                    ReActStepType.FINAL_ANSWER,
                    content=decision.final_answer or "",
                )
                stop_reason = StopReason.GOAL_REACHED
                success = True
                break

            # --- Action phase ---
            if not decision.action_name:
                # Agent didn't pick an action — count as no progress
                no_progress_count += 1
                if no_progress_count >= self.max_no_progress_steps:
                    stop_reason = StopReason.NO_PROGRESS
                    break
                continue

            # Detect repeated actions (no progress)
            action_key = f"{decision.action_name}:{_stable_hash(decision.action_input)}"
            if action_key == last_action_key:
                no_progress_count += 1
            else:
                no_progress_count = 0
            last_action_key = action_key

            if no_progress_count >= self.max_no_progress_steps:
                stop_reason = StopReason.NO_PROGRESS
                break

            # Record the action
            action_step = self._add_step(
                ReActStepType.ACTION,
                content=f"Executing {decision.action_name}",
                action_name=decision.action_name,
                action_input=decision.action_input,
            )
            action_count += 1

            # Execute the action
            try:
                result = act_fn(
                    tool_name=decision.action_name,
                    tool_input=decision.action_input or {},
                )
            except Exception as e:
                result = ToolResult(output="", success=False, error=str(e))

            # --- Observation phase ---
            observation_text = result.output if result.success else f"Error: {result.error}"
            action_step.observation = observation_text

            self._add_step(
                ReActStepType.OBSERVATION,
                content=observation_text[:500],  # Truncate long observations
                metadata={
                    "action": decision.action_name,
                    "success": result.success,
                },
            )

            if not result.success and result.fatal:
                stop_reason = StopReason.ERROR
                break

            # Check if we've reached the max steps
            if step_num >= self.max_steps:
                stop_reason = StopReason.MAX_STEPS
                break

        if stop_reason is None:
            stop_reason = StopReason.MAX_STEPS

        duration = (time.time() - start_time) * 1000

        return ReActResult(
            steps=list(self._steps),
            final_answer=final_answer,
            stop_reason=stop_reason,
            total_steps=len(self._steps),
            actions_taken=action_count,
            duration_ms=round(duration, 2),
            success=success,
        )

    # ------------------------------------------------------------------
    # Trace utilities
    # ------------------------------------------------------------------

    @property
    def steps(self) -> List[ReActStep]:
        """All steps from the last run."""
        return list(self._steps)

    def thoughts(self) -> List[ReActStep]:
        """Get all thought steps."""
        return [s for s in self._steps if s.step_type == ReActStepType.THOUGHT]

    def actions(self) -> List[ReActStep]:
        """Get all action steps."""
        return [s for s in self._steps if s.step_type == ReActStepType.ACTION]

    def observations(self) -> List[ReActStep]:
        """Get all observation steps."""
        return [s for s in self._steps if s.step_type == ReActStepType.OBSERVATION]

    def action_history(self) -> List[Dict[str, Any]]:
        """Get a compact action history (action + observation pairs)."""
        history = []
        for step in self._steps:
            if step.step_type == ReActStepType.ACTION:
                history.append({
                    "action": step.action_name,
                    "input": step.action_input,
                    "observation": step.observation,
                })
        return history

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _add_step(
        self,
        step_type: ReActStepType,
        content: str,
        action_name: Optional[str] = None,
        action_input: Optional[Dict[str, Any]] = None,
        observation: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ReActStep:
        """Create and append a ReAct step."""
        step = ReActStep(
            step_id=f"react_{uuid.uuid4().hex[:8]}",
            step_type=step_type,
            content=content,
            action_name=action_name,
            action_input=dict(action_input) if action_input else None,
            observation=observation,
            metadata=metadata or {},
        )
        self._steps.append(step)
        return step


@dataclass
class ReActDecision:
    """The decision made by the thinker in a ReAct step.

    Attributes:
        thought: The reasoning behind the decision.
        action_name: Name of the tool/action to take (None if final).
        action_input: Parameters for the action.
        is_final: Whether this is the final answer.
        final_answer: The final answer text (if is_final is True).
    """

    thought: str
    action_name: Optional[str] = None
    action_input: Optional[Dict[str, Any]] = None
    is_final: bool = False
    final_answer: Optional[str] = None

    @classmethod
    def final(cls, thought: str, answer: str) -> "ReActDecision":
        """Create a final answer decision."""
        return cls(thought=thought, is_final=True, final_answer=answer)

    @classmethod
    def action(
        cls,
        thought: str,
        tool: str,
        tool_input: Optional[Dict[str, Any]] = None,
    ) -> "ReActDecision":
        """Create an action decision."""
        return cls(
            thought=thought,
            action_name=tool,
            action_input=tool_input or {},
        )


@dataclass
class ToolResult:
    """Result of executing a tool/action in the ReAct loop."""

    output: str
    success: bool = True
    error: Optional[str] = None
    fatal: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def ok(cls, output: str, **kwargs: Any) -> "ToolResult":
        """Create a successful result."""
        return cls(output=output, success=True, metadata=kwargs)

    @classmethod
    def fail(cls, error: str, fatal: bool = False) -> "ToolResult":
        """Create a failed result."""
        return cls(output="", success=False, error=error, fatal=fatal)


def _stable_hash(obj: Any) -> str:
    """Create a stable hash string for an object (for action comparison)."""
    if obj is None:
        return "none"
    if isinstance(obj, dict):
        items = sorted((str(k), _stable_hash(v)) for k, v in obj.items())
        return "{" + ",".join(f"{k}:{v}" for k, v in items) + "}"
    if isinstance(obj, (list, tuple)):
        return "[" + ",".join(_stable_hash(x) for x in obj) + "]"
    return str(obj)
