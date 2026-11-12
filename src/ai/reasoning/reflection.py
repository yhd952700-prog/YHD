"""Reflection and self-improvement module.

Implements the reflection cycle: action -> observation -> reflection -> improved action.
Provides self-critique, iterative refinement, and improvement tracking.

Reflection is what turns a single-pass reasoner into an iterative improver —
it evaluates its own output, identifies flaws, and tries again, tracking
progress across iterations.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional
import time
import uuid


class ReflectionPhase(str, Enum):
    """Phases of the reflection cycle."""

    ACTION = "action"
    """Initial action / output generation."""

    OBSERVATION = "observation"
    """Observing / measuring the output."""

    REFLECTION = "reflection"
    """Critiquing and analyzing the output."""

    IMPROVEMENT = "improvement"
    """Generating an improved version."""


class CritiqueDimension(str, Enum):
    """Dimensions along which output can be critiqued."""

    ACCURACY = "accuracy"
    """How factually correct is the output."""

    COMPLETENESS = "completeness"
    """Does the output cover everything needed."""

    CLARITY = "clarity"
    """How clear and understandable is the output."""

    RELEVANCE = "relevance"
    """How relevant is the output to the goal."""

    COHERENCE = "coherence"
    """How logically consistent is the output."""

    ORIGINALITY = "originality"
    """How creative/novel is the output."""


@dataclass
class CritiqueScore:
    """A score for a specific critique dimension."""

    dimension: CritiqueDimension
    score: float  # 0.0 - 1.0
    feedback: str  # Specific feedback for this dimension

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dimension": self.dimension.value,
            "score": self.score,
            "feedback": self.feedback,
        }


@dataclass
class SelfCritique:
    """Result of a self-critique evaluation."""

    overall_score: float
    scores: List[CritiqueScore]
    summary: str
    strengths: List[str]
    weaknesses: List[str]
    suggestions: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "overall_score": self.overall_score,
            "scores": [s.to_dict() for s in self.scores],
            "summary": self.summary,
            "strengths": list(self.strengths),
            "weaknesses": list(self.weaknesses),
            "suggestions": list(self.suggestions),
        }

    @property
    def passed(self) -> bool:
        """Whether the output passes the quality bar."""
        return self.overall_score >= 0.7


@dataclass
class IterationDelta:
    """Tracks improvement between two iterations."""

    from_iteration: int
    to_iteration: int
    overall_change: float  # Positive = improved
    dimension_changes: Dict[str, float]
    description: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "from_iteration": self.from_iteration,
            "to_iteration": self.to_iteration,
            "overall_change": self.overall_change,
            "dimension_changes": dict(self.dimension_changes),
            "description": self.description,
        }


@dataclass
class ReflectionEntry:
    """A single entry in the reflection journal."""

    entry_id: str
    iteration: int
    phase: ReflectionPhase
    content: str
    critique: Optional[SelfCritique] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "iteration": self.iteration,
            "phase": self.phase.value,
            "content": self.content,
            "critique": self.critique.to_dict() if self.critique else None,
            "metadata": dict(self.metadata),
            "timestamp": self.timestamp,
        }


@dataclass
class ReflectionResult:
    """Result of a full reflection process."""

    iterations: int
    final_output: Any
    final_score: float
    improvements: List[IterationDelta]
    journal: List[ReflectionEntry]
    converged: bool
    total_time_ms: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "iterations": self.iterations,
            "final_output": self.final_output,
            "final_score": self.final_score,
            "improvements": [i.to_dict() for i in self.improvements],
            "journal": [j.to_dict() for j in self.journal],
            "converged": self.converged,
            "total_time_ms": self.total_time_ms,
        }


class ReflectionEngine:
    """Reflection and self-improvement engine.

    Implements the action-observation-reflection-improvement cycle.
    Takes a generator function that produces output and an evaluator
    function that critiques it, then iteratively improves until
    convergence or max iterations.

    Usage::

        engine = ReflectionEngine(max_iterations=5)

        def generate(context, critique=None):
            # produce output based on context and optional previous critique
            return "some output"

        def evaluate(output, context):
            # critique the output
            return SelfCritique(...)

        result = engine.reflect(
            generate_fn=generate,
            evaluate_fn=evaluate,
            context="problem description",
        )
    """

    def __init__(
        self,
        max_iterations: int = 5,
        convergence_threshold: float = 0.05,
        target_score: float = 0.8,
    ):
        """Initialize the reflection engine.

        Args:
            max_iterations: Maximum number of refinement iterations.
            convergence_threshold: Stop if improvement is below this.
            target_score: Stop if score reaches or exceeds this.
        """
        self.max_iterations = max_iterations
        self.convergence_threshold = convergence_threshold
        self.target_score = target_score
        self._journal: List[ReflectionEntry] = []

    # ------------------------------------------------------------------
    # Core reflection cycle
    # ------------------------------------------------------------------

    def reflect(
        self,
        generate_fn: Callable[..., Any],
        evaluate_fn: Callable[..., SelfCritique],
        context: Any = None,
        initial_output: Any = None,
    ) -> ReflectionResult:
        """Run the full reflection cycle.

        Args:
            generate_fn: Function that produces output.
                Signature: generate_fn(context, critique=None) -> output
            evaluate_fn: Function that critiques output.
                Signature: evaluate_fn(output, context) -> SelfCritique
            context: Context passed to both generate and evaluate.
            initial_output: Optional initial output (skips first generation).

        Returns:
            ReflectionResult with full iteration history.
        """
        start_time = time.time()
        self._journal.clear()
        improvements: List[IterationDelta] = []
        previous_score: Optional[float] = None
        previous_critique: Optional[SelfCritique] = None
        current_output = initial_output
        last_critique: Optional[SelfCritique] = None
        converged = False

        for iteration in range(1, self.max_iterations + 1):
            # --- Phase 1: Action (generate output) ---
            if current_output is None or iteration > 1:
                current_output = generate_fn(
                    context,
                    critique=last_critique,
                )
                self._journal.append(ReflectionEntry(
                    entry_id=f"ref_{uuid.uuid4().hex[:8]}",
                    iteration=iteration,
                    phase=ReflectionPhase.ACTION,
                    content=str(current_output),
                    metadata={"iteration": iteration},
                ))

            # --- Phase 2: Observation (evaluate output) ---
            critique = evaluate_fn(current_output, context)
            self._journal.append(ReflectionEntry(
                entry_id=f"ref_{uuid.uuid4().hex[:8]}",
                iteration=iteration,
                phase=ReflectionPhase.OBSERVATION,
                content=f"Score: {critique.overall_score:.2f}",
                critique=critique,
                metadata={"score": critique.overall_score},
            ))

            # Update last critique before any break checks
            last_critique = critique

            # --- Phase 3: Reflection (analyze improvement) ---
            reflection_text = self._build_reflection(critique, previous_critique)
            self._journal.append(ReflectionEntry(
                entry_id=f"ref_{uuid.uuid4().hex[:8]}",
                iteration=iteration,
                phase=ReflectionPhase.REFLECTION,
                content=reflection_text,
                metadata={
                    "score": critique.overall_score,
                    "previous_score": previous_score,
                },
            ))

            # Track improvement delta
            if previous_score is not None:
                delta = critique.overall_score - previous_score
                dim_changes = self._compute_dimension_changes(
                    critique, previous_critique,
                )
                improvements.append(IterationDelta(
                    from_iteration=iteration - 1,
                    to_iteration=iteration,
                    overall_change=delta,
                    dimension_changes=dim_changes,
                    description=(
                        f"Score changed by {delta:+.3f} "
                        f"({previous_score:.3f} -> {critique.overall_score:.3f})"
                    ),
                ))

                # Check convergence
                if abs(delta) < self.convergence_threshold:
                    converged = True
                    break

            # Check if we've hit the target
            if critique.overall_score >= self.target_score:
                converged = True
                break

            # --- Phase 4: Prepare for improvement ---
            previous_critique = critique
            previous_score = critique.overall_score

            self._journal.append(ReflectionEntry(
                entry_id=f"ref_{uuid.uuid4().hex[:8]}",
                iteration=iteration,
                phase=ReflectionPhase.IMPROVEMENT,
                content=f"Improving based on {len(critique.suggestions)} suggestions",
                metadata={"suggestions": critique.suggestions},
            ))

        total_time = (time.time() - start_time) * 1000

        return ReflectionResult(
            iterations=len([e for e in self._journal
                            if e.phase == ReflectionPhase.ACTION]),
            final_output=current_output,
            final_score=last_critique.overall_score if last_critique else 0.0,
            improvements=improvements,
            journal=list(self._journal),
            converged=converged,
            total_time_ms=round(total_time, 2),
        )

    # ------------------------------------------------------------------
    # Self-critique helpers
    # ------------------------------------------------------------------

    def create_critique(
        self,
        overall_score: float,
        dimension_scores: Optional[Dict[CritiqueDimension, float]] = None,
        summary: str = "",
        strengths: Optional[List[str]] = None,
        weaknesses: Optional[List[str]] = None,
        suggestions: Optional[List[str]] = None,
    ) -> SelfCritique:
        """Create a structured SelfCritique from individual scores.

        Args:
            overall_score: Overall quality score (0-1).
            dimension_scores: Per-dimension scores.
            summary: Summary of the critique.
            strengths: List of strengths.
            weaknesses: List of weaknesses.
            suggestions: List of improvement suggestions.

        Returns:
            A SelfCritique object.
        """
        scores: List[CritiqueScore] = []
        if dimension_scores:
            for dim, score in dimension_scores.items():
                feedback = self._default_feedback(dim, score)
                scores.append(CritiqueScore(
                    dimension=dim,
                    score=score,
                    feedback=feedback,
                ))

        return SelfCritique(
            overall_score=overall_score,
            scores=scores,
            summary=summary,
            strengths=list(strengths or []),
            weaknesses=list(weaknesses or []),
            suggestions=list(suggestions or []),
        )

    # ------------------------------------------------------------------
    # Journal / history
    # ------------------------------------------------------------------

    @property
    def journal(self) -> List[ReflectionEntry]:
        """Get the reflection journal from the last run."""
        return list(self._journal)

    def journal_by_phase(self, phase: ReflectionPhase) -> List[ReflectionEntry]:
        """Get journal entries filtered by phase."""
        return [e for e in self._journal if e.phase == phase]

    def journal_by_iteration(self, iteration: int) -> List[ReflectionEntry]:
        """Get journal entries for a specific iteration."""
        return [e for e in self._journal if e.iteration == iteration]

    def score_history(self) -> List[float]:
        """Get the list of overall scores across iterations."""
        return [
            e.critique.overall_score
            for e in self._journal
            if e.phase == ReflectionPhase.OBSERVATION and e.critique is not None
        ]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_reflection(
        self,
        current: SelfCritique,
        previous: Optional[SelfCritique],
    ) -> str:
        """Build a reflection summary text."""
        parts = [f"Overall score: {current.overall_score:.3f}"]

        if current.strengths:
            parts.append(f"Strengths ({len(current.strengths)}):")
            for s in current.strengths:
                parts.append(f"  + {s}")

        if current.weaknesses:
            parts.append(f"Weaknesses ({len(current.weaknesses)}):")
            for w in current.weaknesses:
                parts.append(f"  - {w}")

        if previous is not None:
            delta = current.overall_score - previous.overall_score
            direction = "improved" if delta > 0 else "declined" if delta < 0 else "unchanged"
            parts.append(f"Compared to previous iteration: {direction} "
                         f"by {abs(delta):.3f}")

        return "\n".join(parts)

    def _compute_dimension_changes(
        self,
        current: SelfCritique,
        previous: Optional[SelfCritique],
    ) -> Dict[str, float]:
        """Compute per-dimension score changes."""
        changes: Dict[str, float] = {}
        if previous is None:
            return changes

        prev_map = {s.dimension.value: s.score for s in previous.scores}
        for s in current.scores:
            prev = prev_map.get(s.dimension.value, 0.0)
            changes[s.dimension.value] = s.score - prev

        return changes

    def _default_feedback(self, dimension: CritiqueDimension, score: float) -> str:
        """Generate default feedback text for a dimension score."""
        if score >= 0.9:
            quality = "excellent"
        elif score >= 0.75:
            quality = "good"
        elif score >= 0.6:
            quality = "adequate"
        elif score >= 0.4:
            quality = "below average"
        else:
            quality = "poor"

        return f"{dimension.value.capitalize()} is {quality} ({score:.2f})"
