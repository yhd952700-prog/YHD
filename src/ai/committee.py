"""Committee & Consensus — multi-agent decision-making patterns.

Implements:
  1. Committee — multiple agents review/vote on the same question
  2. ConflictResolver — resolves disagreements between agents
  3. ResultAggregator — combines multiple results into one

These patterns are essential for:
  - Reducing individual agent bias
  - Improving answer quality through diversity
  - Handling ambiguous problems with multiple valid approaches
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple
import time
import uuid

from .employee import Agent
from .agent_factory import AgentRole, AgentFactory


class VotingMethod(str, Enum):
    """Voting methods for committee decisions."""
    MAJORITY = "majority"           # Simple majority wins
    UNANIMITY = "unanimity"         # All must agree
    WEIGHTED = "weighted"           # Votes weighted by agent expertise
    RANKED = "ranked"               # Ranked-choice voting
    CONSENSUS = "consensus"         # Build consensus through discussion


class ConflictStrategy(str, Enum):
    """Strategies for resolving conflicts between agents."""
    VOTE = "vote"                   # Put to a vote
    HIERARCHY = "hierarchy"         # Senior agent decides
    MEDIATOR = "mediator"           # Third-party agent mediates
    COMPROMISE = "compromise"       # Find middle ground
    EVIDENCE = "evidence"           # Go with best-evidenced position
    RANDOM = "random"               # Break tie randomly (last resort)


@dataclass
class Vote:
    """A single vote from a committee member."""
    voter_id: str
    voter_role: str
    choice: Any                     # What they voted for
    weight: float = 1.0
    confidence: float = 0.5
    reasoning: str = ""
    timestamp: float = field(default_factory=time.time)


@dataclass
class CommitteeResult:
    """Result of a committee decision."""
    question: str
    method: VotingMethod
    winner: Any
    votes: List[Vote]
    vote_counts: Dict[str, int]
    consensus_level: float          # 0-1, how much agreement
    resolved: bool
    tie: bool
    duration_ms: float
    reasoning: str = ""


class AgentCommittee:
    """A committee of agents that votes on questions.

    Committee pattern: multiple agents independently evaluate the same
    question, then a voting method aggregates their answers.

    This improves quality by:
      - Reducing individual bias
      - Catching errors any single agent might miss
      - Leveraging diverse perspectives

    Usage::

        committee = AgentCommittee(
            agents=[agent1, agent2, agent3],
            method=VotingMethod.MAJORITY,
        )

        result = committee.decide("What is 2+2?")
        print(result.winner)  # Most popular answer
    """

    def __init__(
        self,
        agents: List[Agent],
        method: VotingMethod = VotingMethod.MAJORITY,
        weights: Optional[Dict[str, float]] = None,
    ):
        self.agents = agents
        self.method = method
        self.weights = weights or {}  # agent_id -> weight
        self._vote_history: List[CommitteeResult] = []

    def decide(
        self,
        question: str,
        method: Optional[VotingMethod] = None,
        context: Optional[str] = None,
        timeout_ms: float = 30000,
    ) -> CommitteeResult:
        """Have the committee decide on a question.

        Each agent independently answers, then votes are tallied
        according to the voting method.
        """
        start_time = time.time()
        vote_method = method or self.method
        votes: List[Vote] = []

        # Collect votes from each agent
        for agent in self.agents:
            try:
                answer, confidence, reasoning = self._get_agent_vote(
                    agent, question, context
                )
                weight = self.weights.get(agent.id, 1.0)
                votes.append(Vote(
                    voter_id=agent.id,
                    voter_role=agent.agent_type,
                    choice=answer,
                    weight=weight,
                    confidence=confidence,
                    reasoning=reasoning,
                ))
            except Exception:
                # Skip agents that fail
                continue

        # Tally votes
        result = self._tally_votes(question, votes, vote_method)
        result.duration_ms = (time.time() - start_time) * 1000

        self._vote_history.append(result)
        return result

    def _get_agent_vote(
        self,
        agent: Agent,
        question: str,
        context: Optional[str],
    ) -> Tuple[Any, float, str]:
        """Get an agent's answer/confidence/reasoning.

        For real LLM agents, this calls the provider.
        For mock agents in tests, it uses the mock response.
        """
        # Build the prompt
        prompt = question
        if context:
            prompt = f"Context: {context}\n\nQuestion: {question}"

        # Try to execute via the agent
        try:
            result = agent.execute(prompt)
            answer = result.get("result", result.get("answer", "unknown"))
            confidence = result.get("confidence", 0.5)
            reasoning = result.get("reasoning", "")
            return answer, float(confidence), str(reasoning)
        except Exception:
            # Fallback: return a simple response
            return "error", 0.1, "Agent failed to respond"

    def _tally_votes(
        self,
        question: str,
        votes: List[Vote],
        method: VotingMethod,
    ) -> CommitteeResult:
        """Tally votes according to the voting method."""
        if not votes:
            return CommitteeResult(
                question=question,
                method=method,
                winner=None,
                votes=[],
                vote_counts={},
                consensus_level=0.0,
                resolved=False,
                tie=False,
                duration_ms=0,
                reasoning="No votes collected",
            )

        # Count raw votes
        raw_counts: Dict[str, float] = {}
        for vote in votes:
            key = str(vote.choice)
            if method == VotingMethod.WEIGHTED:
                raw_counts[key] = raw_counts.get(key, 0) + vote.weight
            else:
                raw_counts[key] = raw_counts.get(key, 0) + 1

        # Sort by vote count descending
        sorted_choices = sorted(raw_counts.items(), key=lambda x: x[1], reverse=True)
        top_count = sorted_choices[0][1]
        winner = sorted_choices[0][0]

        # Check for tie
        tied = [c for c, v in sorted_choices if v == top_count]
        is_tie = len(tied) > 1

        # Calculate consensus level
        total_votes = sum(raw_counts.values())
        majority_ratio = top_count / total_votes if total_votes > 0 else 0

        # Check resolution based on method
        resolved = True
        if method == VotingMethod.UNANIMITY:
            resolved = len(sorted_choices) == 1  # All voted the same
        elif method == VotingMethod.MAJORITY:
            resolved = majority_ratio > 0.5
        elif method == VotingMethod.WEIGHTED:
            resolved = majority_ratio > 0.5
        # For RANKED and CONSENSUS, always mark resolved (simplified)

        # Vote counts (for display)
        vote_counts = {k: int(v) for k, v in raw_counts.items()}

        return CommitteeResult(
            question=question,
            method=method,
            winner=winner,
            votes=votes,
            vote_counts=vote_counts,
            consensus_level=majority_ratio,
            resolved=resolved,
            tie=is_tie,
            duration_ms=0,
            reasoning=f"Winning choice '{winner}' with {top_count:.1f} votes "
                      f"({majority_ratio*100:.0f}%)",
        )

    @property
    def member_count(self) -> int:
        return len(self.agents)

    @property
    def decision_count(self) -> int:
        return len(self._vote_history)


# =====================================================================
# Conflict Resolver
# =====================================================================

@dataclass
class Conflict:
    """A conflict between two or more agents."""
    id: str
    topic: str
    description: str
    parties: List[str]              # Agent IDs in conflict
    positions: Dict[str, Any]       # agent_id -> their position
    severity: float = 0.5           # 0-1, how severe
    created_at: float = field(default_factory=time.time)
    resolved: bool = False
    resolution: str = ""


class ConflictResolver:
    """Resolves conflicts between agents using various strategies.

    When agents disagree, this module provides structured ways to
    resolve the conflict rather than letting it fester.

    Strategies:
      - Vote: Put it to a vote among a larger group
      - Hierarchy: A senior/manager agent decides
      - Mediator: A neutral agent facilitates discussion
      - Evidence: Go with the best-evidenced position
      - Compromise: Find a middle ground
    """

    def __init__(
        self,
        default_strategy: ConflictStrategy = ConflictStrategy.VOTE,
        mediator: Optional[Agent] = None,
    ):
        self.default_strategy = default_strategy
        self.mediator = mediator
        self._conflicts: Dict[str, Conflict] = {}
        self._resolutions: List[Dict[str, Any]] = []

    def create_conflict(
        self,
        topic: str,
        positions: Dict[str, Any],
        description: str = "",
        severity: float = 0.5,
    ) -> Conflict:
        """Create a new conflict to be resolved."""
        conflict = Conflict(
            id=f"conflict_{uuid.uuid4().hex[:10]}",
            topic=topic,
            description=description,
            parties=list(positions.keys()),
            positions=positions,
            severity=severity,
        )
        self._conflicts[conflict.id] = conflict
        return conflict

    def resolve(
        self,
        conflict_id: str,
        strategy: Optional[ConflictStrategy] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Resolve a conflict using the specified strategy."""
        conflict = self._conflicts.get(conflict_id)
        if conflict is None:
            return {"status": "error", "error": "Conflict not found"}

        strategy = strategy or self.default_strategy

        resolution = self._apply_strategy(conflict, strategy, **kwargs)

        if resolution.get("resolved"):
            conflict.resolved = True
            conflict.resolution = resolution.get("reason", "")

        resolution["conflict_id"] = conflict_id
        resolution["strategy"] = strategy.value
        self._resolutions.append(resolution)

        return resolution

    def _apply_strategy(
        self,
        conflict: Conflict,
        strategy: ConflictStrategy,
        **kwargs,
    ) -> Dict[str, Any]:
        """Apply a specific resolution strategy."""

        if strategy == ConflictStrategy.VOTE:
            return self._resolve_by_vote(conflict, **kwargs)

        elif strategy == ConflictStrategy.HIERARCHY:
            return self._resolve_by_hierarchy(conflict, **kwargs)

        elif strategy == ConflictStrategy.MEDIATOR:
            return self._resolve_by_mediator(conflict, **kwargs)

        elif strategy == ConflictStrategy.EVIDENCE:
            return self._resolve_by_evidence(conflict, **kwargs)

        elif strategy == ConflictStrategy.COMPROMISE:
            return self._resolve_by_compromise(conflict, **kwargs)

        elif strategy == ConflictStrategy.RANDOM:
            return self._resolve_by_random(conflict)

        else:
            return {"resolved": False, "reason": f"Unknown strategy: {strategy}"}

    def _resolve_by_vote(self, conflict: Conflict, committee=None) -> Dict[str, Any]:
        """Resolve by committee vote."""
        if committee is None:
            return {"resolved": False, "reason": "No committee provided for vote"}

        result = committee.decide(
            question=f"Resolve conflict on: {conflict.topic}",
            context=conflict.description,
        )

        return {
            "resolved": result.resolved,
            "winner": result.winner,
            "reason": f"Committee decided: {result.winner}",
            "vote_details": result.vote_counts,
        }

    def _resolve_by_hierarchy(self, conflict: Conflict, authority: str = "",
                              authority_position: Any = None) -> Dict[str, Any]:
        """Resolve by authority figure."""
        if authority_position is not None:
            return {
                "resolved": True,
                "winner": authority_position,
                "authority": authority,
                "reason": f"Resolved by authority: {authority}",
            }
        return {"resolved": False, "reason": "No authority position provided"}

    def _resolve_by_mediator(self, conflict: Conflict, **kwargs) -> Dict[str, Any]:
        """Resolve via mediator agent."""
        if self.mediator is None:
            return {"resolved": False, "reason": "No mediator available"}

        # In a real system, the mediator would facilitate discussion
        # For now, return a structured mediation result
        return {
            "resolved": True,
            "winner": "compromise",
            "mediator": self.mediator.id,
            "reason": "Mediator facilitated compromise between parties",
        }

    def _resolve_by_evidence(self, conflict: Conflict,
                             evidence_scores: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
        """Resolve by which position has better evidence."""
        if evidence_scores is None:
            # Default: equal evidence → not resolved
            return {"resolved": False, "reason": "No evidence scores provided"}

        best_party = max(evidence_scores, key=evidence_scores.get)
        best_score = evidence_scores[best_party]

        return {
            "resolved": True,
            "winner": conflict.positions.get(best_party),
            "winning_party": best_party,
            "evidence_score": best_score,
            "reason": f"Best evidence from {best_party} (score: {best_score})",
        }

    def _resolve_by_compromise(self, conflict: Conflict) -> Dict[str, Any]:
        """Find a compromise between positions.

        For numeric positions, find the midpoint.
        For non-numeric, suggest combining positions.
        """
        positions = list(conflict.positions.values())

        # Try numeric compromise
        try:
            numeric_positions = [float(p) for p in positions]
            compromise_value = sum(numeric_positions) / len(numeric_positions)
            return {
                "resolved": True,
                "winner": compromise_value,
                "reason": f"Compromise value: {compromise_value} (average of positions)",
            }
        except (ValueError, TypeError):
            pass

        # Non-numeric: suggest combining
        combined = " + ".join(str(p) for p in positions)
        return {
            "resolved": True,
            "winner": combined,
            "reason": f"Combined compromise: {combined}",
        }

    def _resolve_by_random(self, conflict: Conflict) -> Dict[str, Any]:
        """Random tiebreaker — last resort."""
        import random
        parties = list(conflict.positions.keys())
        winner = random.choice(parties)
        return {
            "resolved": True,
            "winner": conflict.positions[winner],
            "winning_party": winner,
            "reason": f"Random tiebreaker selected: {winner}",
        }

    @property
    def unresolved_count(self) -> int:
        return sum(1 for c in self._conflicts.values() if not c.resolved)

    @property
    def total_conflicts(self) -> int:
        return len(self._conflicts)


# =====================================================================
# Result Aggregator
# =====================================================================

class ResultAggregator:
    """Aggregates results from multiple agents into a single result.

    Different aggregation strategies for different result types:
      - Text: summarization, concatenation, voting on key points
      - Numeric: average, median, weighted average
      - Lists: union, intersection, ranked merge
      - Structured: field-by-field merge with conflict detection
    """

    @staticmethod
    def aggregate_text(
        results: List[str],
        method: str = "concatenate",
        separator: str = "\n\n",
    ) -> str:
        """Aggregate text results.

        Methods:
          - concatenate: Join all results with separator
          - longest: Pick the longest result
          - shortest: Pick the shortest result
          - first: Just use the first result
        """
        if not results:
            return ""

        if method == "concatenate":
            return separator.join(results)
        elif method == "longest":
            return max(results, key=len)
        elif method == "shortest":
            return min(results, key=len)
        elif method == "first":
            return results[0]
        else:
            return separator.join(results)

    @staticmethod
    def aggregate_numeric(
        values: List[float],
        method: str = "average",
        weights: Optional[List[float]] = None,
    ) -> float:
        """Aggregate numeric results.

        Methods:
          - average: Arithmetic mean
          - weighted: Weighted average
          - median: Median value
          - max: Maximum
          - min: Minimum
        """
        if not values:
            return 0.0

        if method == "average":
            return sum(values) / len(values)

        elif method == "weighted":
            if weights is None or len(weights) != len(values):
                return sum(values) / len(values)
            total_weight = sum(weights)
            if total_weight == 0:
                return sum(values) / len(values)
            return sum(v * w for v, w in zip(values, weights)) / total_weight

        elif method == "median":
            sorted_vals = sorted(values)
            n = len(sorted_vals)
            mid = n // 2
            if n % 2 == 0:
                return (sorted_vals[mid - 1] + sorted_vals[mid]) / 2
            else:
                return sorted_vals[mid]

        elif method == "max":
            return max(values)

        elif method == "min":
            return min(values)

        else:
            return sum(values) / len(values)

    @staticmethod
    def aggregate_lists(
        lists: List[List[Any]],
        method: str = "union",
    ) -> List[Any]:
        """Aggregate list results.

        Methods:
          - union: All unique items from all lists
          - intersection: Items present in all lists
          - frequency: Items sorted by how many lists they appear in
        """
        if not lists:
            return []

        if method == "union":
            seen = set()
            result = []
            for lst in lists:
                for item in lst:
                    key = str(item)
                    if key not in seen:
                        seen.add(key)
                        result.append(item)
            return result

        elif method == "intersection":
            if not lists:
                return []
            result = set(str(x) for x in lists[0])
            for lst in lists[1:]:
                result &= set(str(x) for x in lst)
            # Return items from first list that match
            return [item for item in lists[0] if str(item) in result]

        elif method == "frequency":
            freq: Dict[str, int] = {}
            items_map: Dict[str, Any] = {}
            for lst in lists:
                for item in lst:
                    key = str(item)
                    freq[key] = freq.get(key, 0) + 1
                    items_map[key] = item
            sorted_items = sorted(freq.items(), key=lambda x: x[1], reverse=True)
            return [items_map[key] for key, _ in sorted_items]

        else:
            # Default: union
            seen = set()
            result = []
            for lst in lists:
                for item in lst:
                    if str(item) not in seen:
                        seen.add(str(item))
                        result.append(item)
            return result

    @staticmethod
    def aggregate_dicts(
        dicts: List[Dict[str, Any]],
        conflict_resolution: str = "first_wins",
    ) -> Dict[str, Any]:
        """Aggregate dictionary results field-by-field.

        Conflict resolution:
          - first_wins: First dict's value wins for conflicts
          - last_wins: Last dict's value wins for conflicts
          - merge_lists: If values are lists, merge them
          - combine: Combine conflicting values into a list
        """
        if not dicts:
            return {}

        result: Dict[str, Any] = {}
        all_keys = set()
        for d in dicts:
            all_keys.update(d.keys())

        for key in all_keys:
            values = [d[key] for d in dicts if key in d]

            if len(values) == 1:
                result[key] = values[0]
            elif conflict_resolution == "first_wins":
                result[key] = values[0]
            elif conflict_resolution == "last_wins":
                result[key] = values[-1]
            elif conflict_resolution == "merge_lists":
                # If all values are lists, merge them
                if all(isinstance(v, list) for v in values):
                    merged = []
                    for v in values:
                        merged.extend(v)
                    result[key] = merged
                else:
                    result[key] = values[0]
            elif conflict_resolution == "combine":
                result[key] = values
            else:
                result[key] = values[0]

        return result

    @classmethod
    def smart_aggregate(cls, results: List[Any]) -> Any:
        """Automatically choose the best aggregation method based on result types."""
        if not results:
            return None

        # All numeric?
        if all(isinstance(r, (int, float)) for r in results):
            return cls.aggregate_numeric([float(r) for r in results], method="average")

        # All strings?
        if all(isinstance(r, str) for r in results):
            return cls.aggregate_text(results, method="concatenate")

        # All lists?
        if all(isinstance(r, list) for r in results):
            return cls.aggregate_lists(results, method="frequency")

        # All dicts?
        if all(isinstance(r, dict) for r in results):
            return cls.aggregate_dicts(results, conflict_resolution="merge_lists")

        # Mixed: return all results
        return results
