"""Reasoning modules for LiuHao AI OS.

Provides multi-strategy reasoning capabilities:

- **ChainOfThought** — Step-by-step reasoning with trace recording,
  self-correction (contradiction detection), and confidence scoring.
  Supports analytical, creative, and critical reasoning strategies.

- **ReflectionEngine** — Iterative self-improvement via the
  action -> observation -> reflection -> improvement cycle.
  Includes self-critique, improvement tracking, and a reflection journal.

- **ReActAgent** — Reasoning + Acting loop (Thought-Action-Observation)
  that integrates tool use into the reasoning process.
"""
from __future__ import annotations

from .chain_of_thought import (
    ChainOfThought,
    ChainOfThoughtResult,
    ReasoningStep,
    ReasoningStrategy,
    StepType,
    Contradiction,
)
from .reflection import (
    ReflectionEngine,
    ReflectionResult,
    ReflectionEntry,
    ReflectionPhase,
    SelfCritique,
    CritiqueScore,
    CritiqueDimension,
    IterationDelta,
)
from .react import (
    ReActAgent,
    ReActResult,
    ReActStep,
    ReActStepType,
    ReActDecision,
    ToolResult,
    StopReason,
)
from .deductive import (
    Reasoner,
)
from .deductive import (
    ReasoningStep as DeductiveReasoningStep,
)

__all__ = [
    # Chain of Thought
    "ChainOfThought",
    "ChainOfThoughtResult",
    "ReasoningStep",
    "ReasoningStrategy",
    "StepType",
    "Contradiction",
    # Reflection
    "ReflectionEngine",
    "ReflectionResult",
    "ReflectionEntry",
    "ReflectionPhase",
    "SelfCritique",
    "CritiqueScore",
    "CritiqueDimension",
    "IterationDelta",
    # ReAct
    "ReActAgent",
    "ReActResult",
    "ReActStep",
    "ReActStepType",
    "ReActDecision",
    "ToolResult",
    "StopReason",
    # Deductive reasoning (deterministic forward-chaining engine)
    "Reasoner",
    "DeductiveReasoningStep",
]
