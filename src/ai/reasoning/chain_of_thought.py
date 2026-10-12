"""Chain-of-Thought (CoT) reasoning module.

Implements step-by-step reasoning generation with trace recording,
self-correction, confidence scoring, and multiple reasoning strategies.

The module provides a structured approach to deliberative thinking,
where each reasoning step builds on previous ones and can be
inspected, scored, and corrected.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional
import time
import uuid


class ReasoningStrategy(str, Enum):
    """Supported reasoning strategies."""

    ANALYTICAL = "analytical"
    """Systematic, logical breakdown of problems into parts."""

    CREATIVE = "creative"
    """Divergent thinking, exploring multiple possibilities."""

    CRITICAL = "critical"
    """Evaluative, questioning assumptions and identifying flaws."""


class StepType(str, Enum):
    """Types of reasoning steps."""

    PREMISE = "premise"
    """A starting assumption or known fact."""

    INFERENCE = "inference"
    """A conclusion drawn from previous steps."""

    SUB_CONCLUSION = "sub_conclusion"
    """An intermediate conclusion."""

    CORRECTION = "correction"
    """A correction to a previous step."""

    FINAL_CONCLUSION = "final_conclusion"
    """The final answer or decision."""


@dataclass
class ReasoningStep:
    """A single step in a chain-of-thought reasoning process.

    Attributes:
        step_id: Unique identifier for this step.
        step_type: The type of reasoning step.
        content: The textual content of this step.
        premises: IDs of steps this step is based on.
        confidence: Confidence score (0.0 - 1.0) for this step.
        justification: Why this step follows from its premises.
        strategy: Which reasoning strategy produced this step.
        metadata: Additional key-value metadata.
        created_at: Timestamp when the step was created.
    """

    step_id: str
    step_type: StepType
    content: str
    premises: List[str] = field(default_factory=list)
    confidence: float = 0.5
    justification: str = ""
    strategy: ReasoningStrategy = ReasoningStrategy.ANALYTICAL
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the step to a dictionary."""
        return {
            "step_id": self.step_id,
            "step_type": self.step_type.value,
            "content": self.content,
            "premises": list(self.premises),
            "confidence": self.confidence,
            "justification": self.justification,
            "strategy": self.strategy.value,
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
        }


@dataclass
class Contradiction:
    """Represents a detected contradiction between two steps."""

    step_a_id: str
    step_b_id: str
    description: str
    severity: float  # 0.0 - 1.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_a_id": self.step_a_id,
            "step_b_id": self.step_b_id,
            "description": self.description,
            "severity": self.severity,
        }


@dataclass
class ChainOfThoughtResult:
    """Result of a chain-of-thought reasoning process.

    Attributes:
        steps: All reasoning steps in order.
        final_conclusion: The final conclusion step, if reached.
        overall_confidence: Aggregated confidence across the chain.
        contradictions: Detected contradictions between steps.
        strategy: The primary reasoning strategy used.
        duration_ms: Total time spent reasoning.
    """

    steps: List[ReasoningStep]
    final_conclusion: Optional[ReasoningStep]
    overall_confidence: float
    contradictions: List[Contradiction]
    strategy: ReasoningStrategy
    duration_ms: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "steps": [s.to_dict() for s in self.steps],
            "final_conclusion": (
                self.final_conclusion.to_dict() if self.final_conclusion else None
            ),
            "overall_confidence": self.overall_confidence,
            "contradictions": [c.to_dict() for c in self.contradictions],
            "strategy": self.strategy.value,
            "duration_ms": self.duration_ms,
        }


class ChainOfThought:
    """Chain-of-Thought reasoning engine.

    Builds a step-by-step reasoning chain with trace recording,
    self-correction via contradiction detection, and per-step
    confidence scoring. Supports multiple reasoning strategies.

    Usage::

        cot = ChainOfThought(strategy=ReasoningStrategy.ANALYTICAL)
        cot.add_premise("All humans are mortal")
        cot.add_premise("Socrates is human")
        cot.add_inference(
            "Socrates is mortal",
            premises=[cot.steps[0].step_id, cot.steps[1].step_id],
            justification="syllogism",
            confidence=0.95,
        )
        result = cot.finalize("Socrates is mortal")
    """

    def __init__(
        self,
        strategy: ReasoningStrategy = ReasoningStrategy.ANALYTICAL,
        min_confidence: float = 0.0,
    ):
        """Initialize the CoT engine.

        Args:
            strategy: The default reasoning strategy.
            min_confidence: Minimum confidence to consider a step valid.
        """
        self.strategy = strategy
        self.min_confidence = min_confidence
        self._steps: List[ReasoningStep] = []
        self._step_index: Dict[str, ReasoningStep] = {}
        self._start_time: float = time.time()

    # ------------------------------------------------------------------
    # Step creation
    # ------------------------------------------------------------------

    def add_premise(
        self,
        content: str,
        confidence: float = 1.0,
        source: str = "given",
    ) -> ReasoningStep:
        """Add a premise (starting fact/assumption) to the chain.

        Args:
            content: The premise text.
            confidence: How confident we are in this premise (0-1).
            source: Where this premise came from.

        Returns:
            The created ReasoningStep.
        """
        step = self._create_step(
            step_type=StepType.PREMISE,
            content=content,
            premises=[],
            confidence=confidence,
            justification=f"premise from {source}",
            metadata={"source": source},
        )
        return step

    def add_inference(
        self,
        content: str,
        premises: List[str],
        justification: str = "",
        confidence: float = 0.7,
        strategy: Optional[ReasoningStrategy] = None,
    ) -> ReasoningStep:
        """Add an inference step based on previous steps.

        Args:
            content: The inferred conclusion.
            premises: IDs of premise steps this inference is based on.
            justification: Why this inference follows.
            confidence: Confidence in this inference (0-1).
            strategy: Override the default strategy for this step.

        Returns:
            The created ReasoningStep.
        """
        self._validate_premises(premises)
        step = self._create_step(
            step_type=StepType.INFERENCE,
            content=content,
            premises=premises,
            confidence=confidence,
            justification=justification,
            strategy=strategy or self.strategy,
        )
        return step

    def add_sub_conclusion(
        self,
        content: str,
        premises: List[str],
        justification: str = "",
        confidence: float = 0.8,
    ) -> ReasoningStep:
        """Add a sub-conclusion (intermediate result) to the chain.

        Args:
            content: The sub-conclusion text.
            premises: IDs of supporting steps.
            justification: Why this sub-conclusion holds.
            confidence: Confidence score (0-1).

        Returns:
            The created ReasoningStep.
        """
        self._validate_premises(premises)
        step = self._create_step(
            step_type=StepType.SUB_CONCLUSION,
            content=content,
            premises=premises,
            confidence=confidence,
            justification=justification,
        )
        return step

    def add_correction(
        self,
        target_step_id: str,
        corrected_content: str,
        reason: str,
        confidence: float = 0.8,
    ) -> ReasoningStep:
        """Add a correction to a previous step.

        Self-correction is a key feature: when the reasoner detects
        a flaw in its own reasoning, it can issue a correction step
        rather than silently backtracking.

        Args:
            target_step_id: The step being corrected.
            corrected_content: The corrected version.
            reason: Why the correction is needed.
            confidence: Confidence in the correction (0-1).

        Returns:
            The correction ReasoningStep.
        """
        if target_step_id not in self._step_index:
            raise ValueError(f"Unknown step id: {target_step_id}")

        step = self._create_step(
            step_type=StepType.CORRECTION,
            content=corrected_content,
            premises=[target_step_id],
            confidence=confidence,
            justification=f"Correction to step {target_step_id}: {reason}",
            metadata={
                "corrects": target_step_id,
                "reason": reason,
                "original": self._step_index[target_step_id].content,
            },
        )
        return step

    # ------------------------------------------------------------------
    # Finalization
    # ------------------------------------------------------------------

    def finalize(
        self,
        conclusion: str,
        premises: Optional[List[str]] = None,
        justification: str = "",
        confidence: Optional[float] = None,
    ) -> ChainOfThoughtResult:
        """Finalize the reasoning chain with a final conclusion.

        Args:
            conclusion: The final conclusion text.
            premises: IDs of supporting steps (defaults to all steps).
            justification: Why the conclusion follows.
            confidence: Override overall confidence (auto-computed if None).

        Returns:
            A ChainOfThoughtResult summarizing the full reasoning chain.
        """
        if premises is None:
            premises = [s.step_id for s in self._steps
                        if s.step_type != StepType.CORRECTION]

        final_step = self._create_step(
            step_type=StepType.FINAL_CONCLUSION,
            content=conclusion,
            premises=premises,
            confidence=confidence if confidence is not None else self._auto_confidence(),
            justification=justification,
        )

        contradictions = self.detect_contradictions()
        overall_conf = confidence if confidence is not None else self._auto_confidence()

        # Reduce confidence if contradictions exist
        if contradictions:
            max_severity = max(c.severity for c in contradictions)
            overall_conf = max(0.0, overall_conf * (1.0 - max_severity * 0.5))

        duration = (time.time() - self._start_time) * 1000

        return ChainOfThoughtResult(
            steps=list(self._steps),
            final_conclusion=final_step,
            overall_confidence=round(overall_conf, 4),
            contradictions=contradictions,
            strategy=self.strategy,
            duration_ms=round(duration, 2),
        )

    # ------------------------------------------------------------------
    # Self-correction: contradiction detection
    # ------------------------------------------------------------------

    def detect_contradictions(self) -> List[Contradiction]:
        """Detect contradictions between steps in the chain.

        Uses heuristic-based detection:
        - Direct negation patterns (e.g., "X is true" vs "X is false")
        - Conflicting quantitative claims
        - Mutually exclusive categories

        Returns:
            List of detected Contradiction objects.
        """
        contradictions: List[Contradiction] = []
        contents = [(s.step_id, s.content.lower()) for s in self._steps
                     if s.step_type in (StepType.PREMISE, StepType.INFERENCE,
                                        StepType.SUB_CONCLUSION)]

        for i, (id_a, content_a) in enumerate(contents):
            for id_b, content_b in contents[i + 1:]:
                severity = self._contradiction_severity(content_a, content_b)
                if severity > 0:
                    contradictions.append(Contradiction(
                        step_a_id=id_a,
                        step_b_id=id_b,
                        description="Potential contradiction detected",
                        severity=severity,
                    ))

        return contradictions

    def _contradiction_severity(self, text_a: str, text_b: str) -> float:
        """Heuristic contradiction severity score between two texts.

        Returns a value between 0.0 (no contradiction) and 1.0 (strong
        contradiction).
        """
        severity = 0.0

        # Direct negation patterns
        negation_pairs = [
            ("is true", "is false"),
            ("are true", "are false"),
            ("is correct", "is incorrect"),
            ("is right", "is wrong"),
            ("is possible", "is impossible"),
            ("can", "cannot"),
            ("will", "will not"),
            ("does", "does not"),
            ("has", "does not have"),
        ]

        for pos, neg in negation_pairs:
            # Check if one text asserts and the other denies the same thing
            # This is a simplified heuristic
            if pos in text_a and neg in text_b:
                # Check for shared content around the negation
                words_a = set(text_a.replace(pos, "").split())
                words_b = set(text_b.replace(neg, "").split())
                overlap = words_a & words_b
                if len(overlap) >= 2:
                    severity = max(severity, 0.8)
            if neg in text_a and pos in text_b:
                words_a = set(text_a.replace(neg, "").split())
                words_b = set(text_b.replace(pos, "").split())
                overlap = words_a & words_b
                if len(overlap) >= 2:
                    severity = max(severity, 0.8)

        # Mutually exclusive quantitative claims
        # "all X" vs "no X" vs "some X"
        quantifier_pairs = [
            ("all", "no"),
            ("every", "none"),
            ("always", "never"),
            ("all", "some not"),
            ("every", "not every"),
        ]

        for q1, q2 in quantifier_pairs:
            if q1 in text_a and q2 in text_b:
                severity = max(severity, 0.5)
            if q2 in text_a and q1 in text_b:
                severity = max(severity, 0.5)

        return severity

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    @property
    def steps(self) -> List[ReasoningStep]:
        """Get all steps in order."""
        return list(self._steps)

    @property
    def step_count(self) -> int:
        """Number of steps in the chain."""
        return len(self._steps)

    def get_step(self, step_id: str) -> Optional[ReasoningStep]:
        """Get a step by its ID."""
        return self._step_index.get(step_id)

    def steps_by_type(self, step_type: StepType) -> List[ReasoningStep]:
        """Get all steps of a given type."""
        return [s for s in self._steps if s.step_type == step_type]

    def trace(self) -> str:
        """Generate a human-readable reasoning trace.

        Returns:
            A formatted string showing the full reasoning chain.
        """
        lines = []
        for i, step in enumerate(self._steps, 1):
            prefix = f"[{i}] ({step.step_type.value})"
            conf = f"[conf={step.confidence:.2f}]"
            lines.append(f"{prefix} {conf} {step.content}")
            if step.premises:
                premise_nums = []
                for pid in step.premises:
                    for j, s in enumerate(self._steps, 1):
                        if s.step_id == pid:
                            premise_nums.append(str(j))
                            break
                lines.append(f"     ↳ based on steps: {', '.join(premise_nums)}")
            if step.justification:
                lines.append(f"     ↳ reason: {step.justification}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _create_step(
        self,
        step_type: StepType,
        content: str,
        premises: List[str],
        confidence: float,
        justification: str,
        strategy: Optional[ReasoningStrategy] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ReasoningStep:
        """Create and register a new reasoning step."""
        step_id = f"cot_{uuid.uuid4().hex[:8]}"
        step = ReasoningStep(
            step_id=step_id,
            step_type=step_type,
            content=content,
            premises=list(premises),
            confidence=max(0.0, min(1.0, confidence)),
            justification=justification,
            strategy=strategy or self.strategy,
            metadata=metadata or {},
        )
        self._steps.append(step)
        self._step_index[step_id] = step
        return step

    def _validate_premises(self, premise_ids: List[str]) -> None:
        """Validate that all premise IDs exist in the chain."""
        for pid in premise_ids:
            if pid not in self._step_index:
                raise ValueError(f"Unknown premise step id: {pid}")

    def _auto_confidence(self) -> float:
        """Compute automatic overall confidence from step confidences.

        Uses a weighted product: multiply confidences along the chain,
        weighted by step type importance.
        """
        if not self._steps:
            return 0.0

        type_weights = {
            StepType.PREMISE: 1.0,
            StepType.INFERENCE: 1.0,
            StepType.SUB_CONCLUSION: 1.0,
            StepType.CORRECTION: 0.5,  # corrections carry less weight
            StepType.FINAL_CONCLUSION: 1.0,
        }

        total_weight = 0.0
        weighted_sum = 0.0

        for step in self._steps:
            weight = type_weights.get(step.step_type, 1.0)
            weighted_sum += step.confidence * weight
            total_weight += weight

        if total_weight == 0:
            return 0.0

        return weighted_sum / total_weight
