"""Meta-Memory — memory about memory.

Meta-memory is "knowing about knowing":
  - What do I know? (and how confident am I?)
  - What don't I know? (knowledge gaps)
  - How good is my memory? (metacognitive accuracy)
  - When should I look something up vs. try to recall?

This is the executive/monitoring layer of memory — it doesn't store
facts, it stores *about* facts.

Why meta-memory matters:
  1. **Honesty** — the system knows what it doesn't know, so it doesn't
     bluff or hallucinate answers
  2. **Efficiency** — knows when to search memory vs. search externally
  3. **Learning prioritisation** — focuses learning on knowledge gaps
  4. **Confidence calibration** — knows how sure it is about each fact
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
import time
import uuid


class MemoryConfidence(str, Enum):
    """Confidence levels for memory entries."""
    CERTAIN = "certain"       # 95%+ — proven, verified
    HIGH = "high"             # 80-95% — very likely true
    MODERATE = "moderate"     # 50-80% — probably true but unconfirmed
    LOW = "low"               # 20-50% — plausible but uncertain
    SPECULATION = "speculation"  # <20% — guess, hypothesis
    UNKNOWN = "unknown"       # No confidence estimate


_CONFIDENCE_SCORES = {
    MemoryConfidence.CERTAIN: 0.975,
    MemoryConfidence.HIGH: 0.875,
    MemoryConfidence.MODERATE: 0.65,
    MemoryConfidence.LOW: 0.35,
    MemoryConfidence.SPECULATION: 0.1,
    MemoryConfidence.UNKNOWN: 0.25,  # Unknown = low confidence, should verify
}


@dataclass
class KnowledgeGap:
    """A gap in the system's knowledge.

    A knowledge gap is something the system has identified that it
    doesn't know (or doesn't know well enough). Tracking these is
    important for:
      - Honesty: admitting what you don't know
      - Learning priority: what to learn next
      - Query routing: when to use external search
    """
    id: str
    topic: str
    description: str = ""
    gap_type: str = "knowledge"  # "knowledge", "skill", "context", "memory"
    importance: float = 0.5      # How important it is to fill this gap
    urgency: float = 0.3         # How soon it's needed
    first_identified: float = field(default_factory=time.time)
    last_encountered: float = field(default_factory=time.time)
    encounter_count: int = 1
    related_topics: List[str] = field(default_factory=list)
    status: str = "open"         # "open", "investigating", "filled", "wont_fix"

    @property
    def priority_score(self) -> float:
        """Combined importance * urgency score."""
        return self.importance * self.urgency * (1 + 0.1 * self.encounter_count)


@dataclass
class MemoryBelief:
    """A tracked belief with confidence and source tracking."""
    id: str
    claim: str                      # What is believed
    confidence: MemoryConfidence = MemoryConfidence.MODERATE
    confidence_score: float = 0.65  # Numeric confidence (0-1)
    sources: List[str] = field(default_factory=list)  # Where this came from
    evidence_count: int = 1         # How many pieces of evidence support this
    last_verified: Optional[float] = None
    contradictions: List[str] = field(default_factory=list)
    related_beliefs: List[str] = field(default_factory=list)


class MetaMemory:
    """Meta-memory — monitoring and control of memory systems.

    This is the "executive" for memory — it:
      - Tracks confidence in beliefs
      - Identifies knowledge gaps
      - Makes decisions about memory strategies
      - Evaluates memory accuracy (metacognition)

    Usage::

        meta = MetaMemory()

        # Track a belief
        meta.add_belief("Python is a programming language",
                        confidence=MemoryConfidence.CERTAIN,
                        sources=["education", "documentation"])

        # Record a knowledge gap
        meta.add_gap("quantum computing",
                     description="Don't know enough about quantum algorithms",
                     importance=0.4)

        # Check confidence in a topic
        conf = meta.confidence_in_topic("python")
        print(conf)  # MemoryConfidence.CERTAIN

        # Get priority gaps to learn
        top_gaps = meta.top_gaps(limit=5)
    """

    def __init__(self):
        self._beliefs: Dict[str, MemoryBelief] = {}
        self._gaps: Dict[str, KnowledgeGap] = {}

        # Indexes
        self._topic_beliefs: Dict[str, List[str]] = {}  # topic keyword -> [belief_ids]
        self._topic_gaps: Dict[str, List[str]] = {}     # topic keyword -> [gap_ids]

        # Metacognitive stats
        self._total_recalls: int = 0
        self._correct_recalls: int = 0
        self._total_judgments: int = 0
        self._accurate_judgments: int = 0

    # ------------------------------------------------------------------
    # Belief tracking
    # ------------------------------------------------------------------

    def add_belief(
        self,
        claim: str,
        confidence: MemoryConfidence = MemoryConfidence.MODERATE,
        sources: Optional[List[str]] = None,
        related_topics: Optional[List[str]] = None,
    ) -> MemoryBelief:
        """Add or update a belief."""
        # Check for existing similar belief
        existing = self._find_belief(claim)
        if existing is not None:
            # Update: strengthen with additional evidence
            existing.evidence_count += 1
            if sources:
                for s in sources:
                    if s not in existing.sources:
                        existing.sources.append(s)
            # Increase confidence with more evidence (diminishing returns)
            existing.confidence_score = min(
                0.99,
                existing.confidence_score + 0.1 / existing.evidence_count,
            )
            existing.confidence = self._score_to_confidence(existing.confidence_score)
            return existing

        belief_id = f"bel_{uuid.uuid4().hex[:10]}"
        belief = MemoryBelief(
            id=belief_id,
            claim=claim,
            confidence=confidence,
            confidence_score=_CONFIDENCE_SCORES.get(confidence, 0.5),
            sources=sources or [],
            last_verified=time.time(),
        )

        self._beliefs[belief_id] = belief
        self._index_belief(belief, related_topics or [])

        return belief

    def update_belief_confidence(
        self,
        belief_id: str,
        new_confidence: MemoryConfidence,
        reason: str = "",
    ) -> bool:
        """Update a belief's confidence level."""
        belief = self._beliefs.get(belief_id)
        if belief is None:
            return False

        belief.confidence = new_confidence
        belief.confidence_score = _CONFIDENCE_SCORES.get(new_confidence, 0.5)
        if reason:
            belief.contradictions.append(f"update: {reason}")
        return True

    def get_beliefs_about(self, topic: str, limit: int = 10) -> List[MemoryBelief]:
        """Find beliefs related to a topic."""
        topic_lower = topic.lower()
        candidate_ids: set = set()

        # Check topic index
        for keyword, ids in self._topic_beliefs.items():
            if keyword in topic_lower or topic_lower in keyword:
                candidate_ids.update(ids)

        # Also search claim text
        for belief in self._beliefs.values():
            if topic_lower in belief.claim.lower():
                candidate_ids.add(belief.id)

        # Get and sort by confidence
        results = []
        for bid in candidate_ids:
            belief = self._beliefs.get(bid)
            if belief:
                results.append(belief)

        results.sort(key=lambda b: b.confidence_score, reverse=True)
        return results[:limit]

    def confidence_in_topic(self, topic: str) -> MemoryConfidence:
        """Estimate overall confidence in a topic area.

        Aggregates across all beliefs about the topic.
        """
        beliefs = self.get_beliefs_about(topic, limit=20)
        if not beliefs:
            return MemoryConfidence.UNKNOWN

        # Weighted average of confidence scores
        total_weight = 0
        weighted_sum = 0
        for belief in beliefs:
            weight = belief.evidence_count
            weighted_sum += belief.confidence_score * weight
            total_weight += weight

        if total_weight == 0:
            return MemoryConfidence.UNKNOWN

        avg_score = weighted_sum / total_weight
        return self._score_to_confidence(avg_score)

    # ------------------------------------------------------------------
    # Knowledge gaps
    # ------------------------------------------------------------------

    def add_gap(
        self,
        topic: str,
        description: str = "",
        gap_type: str = "knowledge",
        importance: float = 0.5,
        urgency: float = 0.3,
        related_topics: Optional[List[str]] = None,
    ) -> KnowledgeGap:
        """Record a knowledge gap.

        If the gap already exists, update its counters instead.
        """
        existing = self._find_gap(topic)
        if existing is not None:
            existing.encounter_count += 1
            existing.last_encountered = time.time()
            existing.importance = max(existing.importance, importance)
            existing.urgency = max(existing.urgency, urgency)
            if description and not existing.description:
                existing.description = description
            return existing

        gap_id = f"gap_{uuid.uuid4().hex[:10]}"
        gap = KnowledgeGap(
            id=gap_id,
            topic=topic,
            description=description,
            gap_type=gap_type,
            importance=importance,
            urgency=urgency,
            related_topics=related_topics or [],
        )

        self._gaps[gap_id] = gap
        self._index_gap(gap)
        return gap

    def close_gap(self, gap_id: str, method: str = "learned") -> bool:
        """Mark a knowledge gap as filled."""
        gap = self._gaps.get(gap_id)
        if gap is None:
            return False
        gap.status = "filled"
        return True

    def top_gaps(
        self,
        limit: int = 10,
        status: str = "open",
    ) -> List[KnowledgeGap]:
        """Get the highest-priority knowledge gaps."""
        gaps = [g for g in self._gaps.values() if g.status == status]
        gaps.sort(key=lambda g: g.priority_score, reverse=True)
        return gaps[:limit]

    def get_gaps_by_type(self, gap_type: str) -> List[KnowledgeGap]:
        """Get all gaps of a certain type."""
        return [g for g in self._gaps.values() if g.gap_type == gap_type]

    def knows_enough_about(self, topic: str, threshold: float = 0.6) -> bool:
        """Quick check: do we know enough about this topic?

        Returns True if confidence is above threshold, meaning we
        probably don't need to look it up.
        """
        confidence = self.confidence_in_topic(topic)
        score = _CONFIDENCE_SCORES.get(confidence, 0.5)
        return score >= threshold

    def should_look_up(self, topic: str) -> bool:
        """Should we look up this topic externally?

        Returns True if confidence is low, meaning we should search
        for external information rather than relying on memory.
        """
        return not self.knows_enough_about(topic, threshold=0.5)

    # ------------------------------------------------------------------
    # Metacognition
    # ------------------------------------------------------------------

    def record_recall_attempt(self, correct: bool) -> None:
        """Record whether a recall attempt was correct.

        Used to calibrate metacognitive accuracy — how good is the
        system at knowing whether it knows something?
        """
        self._total_recalls += 1
        if correct:
            self._correct_recalls += 1

    def record_judgment(self, accurate: bool) -> None:
        """Record whether a confidence judgment was accurate.

        If the system said "I'm sure" and was right (or "I'm not sure"
        and was wrong), that's accurate metacognition.
        """
        self._total_judgments += 1
        if accurate:
            self._accurate_judgments += 1

    @property
    def recall_accuracy(self) -> float:
        """Overall recall accuracy (0-1)."""
        if self._total_recalls == 0:
            return 0.0
        return self._correct_recalls / self._total_recalls

    @property
    def metacognitive_accuracy(self) -> float:
        """How accurate are confidence judgments? (0-1)"""
        if self._total_judgments == 0:
            return 0.5  # Default: 50/50
        return self._accurate_judgments / self._total_judgments

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def stats(self) -> Dict[str, Any]:
        """Get meta-memory statistics."""
        confidence_dist: Dict[str, int] = {}
        for belief in self._beliefs.values():
            c = belief.confidence.value
            confidence_dist[c] = confidence_dist.get(c, 0) + 1

        gap_statuses: Dict[str, int] = {}
        for gap in self._gaps.values():
            gap_statuses[gap.status] = gap_statuses.get(gap.status, 0) + 1

        return {
            "belief_count": len(self._beliefs),
            "gap_count": len(self._gaps),
            "confidence_distribution": confidence_dist,
            "gap_statuses": gap_statuses,
            "recall_accuracy": round(self.recall_accuracy, 3),
            "metacognitive_accuracy": round(self.metacognitive_accuracy, 3),
            "total_recalls": self._total_recalls,
            "total_judgments": self._total_judgments,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _score_to_confidence(score: float) -> MemoryConfidence:
        """Convert a numeric confidence score to enum level."""
        if score >= 0.95:
            return MemoryConfidence.CERTAIN
        elif score >= 0.80:
            return MemoryConfidence.HIGH
        elif score >= 0.50:
            return MemoryConfidence.MODERATE
        elif score >= 0.20:
            return MemoryConfidence.LOW
        elif score >= 0.05:
            return MemoryConfidence.SPECULATION
        else:
            return MemoryConfidence.UNKNOWN

    def _find_belief(self, claim: str) -> Optional[MemoryBelief]:
        """Find an existing belief by exact claim text."""
        claim_lower = claim.lower().strip()
        for belief in self._beliefs.values():
            if belief.claim.lower().strip() == claim_lower:
                return belief
        return None

    def _find_gap(self, topic: str) -> Optional[KnowledgeGap]:
        """Find an existing gap by topic."""
        topic_lower = topic.lower().strip()
        for gap in self._gaps.values():
            if gap.topic.lower().strip() == topic_lower:
                return gap
        return None

    def _index_belief(self, belief: MemoryBelief, topics: List[str]) -> None:
        """Add belief to topic indexes."""
        keywords = self._extract_keywords(belief.claim)
        for kw in keywords:
            if kw not in self._topic_beliefs:
                self._topic_beliefs[kw] = []
            if belief.id not in self._topic_beliefs[kw]:
                self._topic_beliefs[kw].append(belief.id)

        for topic in topics:
            tl = topic.lower()
            if tl not in self._topic_beliefs:
                self._topic_beliefs[tl] = []
            if belief.id not in self._topic_beliefs[tl]:
                self._topic_beliefs[tl].append(belief.id)

    def _index_gap(self, gap: KnowledgeGap) -> None:
        """Add gap to topic indexes."""
        keywords = self._extract_keywords(gap.topic)
        for kw in keywords:
            if kw not in self._topic_gaps:
                self._topic_gaps[kw] = []
            if gap.id not in self._topic_gaps[kw]:
                self._topic_gaps[kw].append(gap.id)

    @staticmethod
    def _extract_keywords(text: str) -> List[str]:
        """Extract keywords from text for indexing."""
        import re
        words = re.findall(r"[a-zA-Z0-9_\u4e00-\u9fff]+", text.lower())
        return [w for w in words if len(w) >= 3]
